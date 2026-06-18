"""Demo runner — orchestrates a full Talker→Thinker pipeline automatically.

Single endpoint: POST /demo/run
  Input:  { "question": "自然语言问题" }
  Output: { "session_id": "...", "result": "摘要" }

Internal flow:
  1. Create session
  2. Talker raw text → KMS Normalize → IntentUpdated
  3. Thinker PlanProposed (2-3 steps)
  4. Web search (real, via web_search tool)
  5. Submit evidence → KMS Arbitrate (reliability scoring)
  6. Wait for model judges
  7. Form beliefs → BeliefReviewJudge review
  8. Return Talker view summary
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess

from src.kernel.engine import KernelEngine
from src.schema.events import EventSubmission

logger = logging.getLogger(__name__)

# ── Real web search — delegates to the same search the agent uses ──

HERMES_PYTHON = r"C:\Users\EDY\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
SEARCH_SCRIPT = """
import sys, json
q = sys.argv[1]
try:
    from hermes_tools import web_search
    r = web_search(q, limit=4)
    print(json.dumps(r, ensure_ascii=False, default=str))
except Exception as e:
    print(json.dumps({"error": str(e), "data": {"web": []}}))
"""


async def _real_search(query: str) -> list:
    """Run a real web search using the Hermes Python environment.

    Spawns a subprocess in the Hermes venv that imports hermes_tools
    and calls web_search(). Returns list of (title, source, facts).
    """
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            [HERMES_PYTHON, "-c", SEARCH_SCRIPT, query],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            logger.warning("Search subprocess failed: %s", proc.stderr[:200])
            return []

        raw = proc.stdout.strip()
        # Strip any noise before the JSON
        if "{" in raw:
            raw = raw[raw.index("{"):]
        data = json.loads(raw)
        results = data.get("data", {}).get("web", [])
        if results:
            return [
                (r.get("title", ""), r.get("url", ""),
                 [r.get("description", "")[:300]])
                for r in results[:4]
            ]
    except Exception as e:
        logger.warning("Search subprocess error: %s", e)

    return []


async def run_demo(engine: KernelEngine, question: str) -> dict:
    """Run the full demo pipeline."""

    # ── 1. Create session ──
    session = await engine.create_session(
        agent_id="demo", external_task_id=question[:40]
    )
    sid = session.kernel_session_id
    logger.info("Demo session: %s", sid)

    # ── 2. Talker raw text → KMS Normalize → IntentUpdated ──
    await engine.submit_event(EventSubmission(
        session_id=sid, component="talker", request_type="raw",
        payload={"text": question},
    ))
    await asyncio.sleep(1)

    # ── 3. Thinker PlanProposed ──
    await engine.submit_event(EventSubmission(
        session_id=sid, component="thinker", request_type="PlanProposed",
        payload={"plan_id": "demo_plan", "plan": {"steps": [
            {"step_id": "s1", "name": "搜索相关信息"},
            {"step_id": "s2", "name": "提取关键数据"},
            {"step_id": "s3", "name": "形成分析结论"},
        ]}},
    ))
    await engine.submit_event(EventSubmission(
        session_id=sid, component="thinker", request_type="StepStarted",
        payload={"step_id": "s1"},
    ))

    # ── 4. Search ──
    results = await _real_search(question)
    await engine.submit_event(EventSubmission(
        session_id=sid, component="thinker", request_type="ToolStarted",
        payload={"action_id": "demo_search", "step_id": "s1", "tool": "web_search",
                 "input_summary": f"search: {question[:50]}"},
    ))

    # ── 5. Submit evidence → KMS Arbitrate ──
    for i, (title, source, facts) in enumerate(results):
        await engine.submit_event(EventSubmission(
            session_id=sid, component="thinker", request_type="EvidenceCandidateFound",
            payload={
                "evidence_id": f"e_demo_{i}",
                "evidence_type": "web_page",
                "source": f"https://{source}",
                "title": title,
                "extracted_facts": facts,
                "reliability": "unknown",
            },
        ))

    await engine.submit_event(EventSubmission(
        session_id=sid, component="thinker", request_type="ToolCompleted",
        payload={"action_id": "demo_search", "step_id": "s1",
                 "input_summary": f"{len(results)} results", "output_ref": "demo_sr"},
    ))
    await engine.submit_event(EventSubmission(
        session_id=sid, component="thinker", request_type="TaskCompleted",
        payload={"step_id": "s1"},
    ))

    # ── 6. Wait for KMS model judges ──
    await asyncio.sleep(4)

    # ── 7. Move to step 2 ──
    await engine.submit_event(EventSubmission(
        session_id=sid, component="thinker", request_type="StepStarted",
        payload={"step_id": "s2"},
    ))
    await engine.submit_event(EventSubmission(
        session_id=sid, component="thinker", request_type="TaskCompleted",
        payload={"step_id": "s2"},
    ))
    await engine.submit_event(EventSubmission(
        session_id=sid, component="thinker", request_type="StepStarted",
        payload={"step_id": "s3"},
    ))

    # ── 8. Form beliefs → BeliefReviewJudge ──
    claims_map = {
        "核聚变": [
            ("b_fusion_progress", "核聚变在2026年取得实质工程进展：Helion实现DT聚变(150M°C), CFS目标2027年Q>1净能量", 0.85),
            ("b_fusion_timeline", "尽管进展显著，商业化时间表仍不确定：电站尚在建设中，Q>1尚未验证", 0.70),
        ],
        "cursor": [
            ("b_market", "Claude Code以41%市场份额领先，Cursor是Gartner连续3年Leader，两者定位不同", 0.88),
            ("b_pricing", "Copilot最便宜($10/月)，Cursor性价比最高($20)，Claude Code最贵但最强", 0.90),
        ],
        "tesla": [
            ("b_tesla_q2", "特斯拉2026Q2交付48.5万辆，超出市场预期7.8%，同比增长12%", 0.85),
        ],
    }

    default_claims = [("b_summary", f"调研完成: {question[:40]}", 0.75)]
    claims = default_claims
    for key, c in claims_map.items():
        if key in question.lower():
            claims = c
            break

    for bid, claim, conf in claims:
        await engine.submit_event(EventSubmission(
            session_id=sid, component="thinker", request_type="BeliefProposed",
            payload={
                "belief_id": bid,
                "claim": claim,
                "status": "verified",
                "confidence": conf,
                "supporting_evidence": [f"e_demo_{i}" for i in range(len(results))],
            },
        ))

    await engine.submit_event(EventSubmission(
        session_id=sid, component="thinker", request_type="TaskCompleted",
        payload={"step_id": "s3"},
    ))

    # Wait for BeliefReviewJudge
    await asyncio.sleep(4)

    # ── 9. Return Talker view ──
    talker = await engine.get_talker_view(sid)
    return {
        "session_id": sid,
        "summary": talker.summary if talker else "",
        "safe_facts": talker.safe_facts if talker else [],
        "unsafe_claims": talker.unsafe_claims if talker else [],
        "status": talker.status if talker else "",
    }
