"""
补充测试 — Agent State Kernel

覆盖：BeliefReviewJudge, Gate, cancellation_token, Rebuild幂等, Summarize分离
只做测试，不修改任何生产代码。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kernel.engine import KernelEngine
import src.kms.pipeline as kms_pipeline
from src.schema.events import (
    Actor, CognitiveEvent, EventSubmission, EventType, RuntimeRef, Visibility,
)
from src.schema.state import (
    BeliefItem, BeliefStatus, EvidenceItem, EvidenceType,
    PlanState, PlanStep, ProgressState, Reliability, StepStatus, IntentState,
)
from src.stores.sqlite_store import SqliteStore
from src.utils.time import utc_now


async def build_engine():
    store = SqliteStore(":memory:")
    await store.connect()
    return store, KernelEngine(store)


# ═══════════════════════════════════════════════════════════════════
# 1. BeliefReviewJudge：过度自信自动纠正
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_belief_review_judge_corrects_overconfidence():
    """Thinker提交verified/0.95的过度自信信念，KMS应纠正为conflicting。"""
    store, engine = await build_engine()
    try:
        session = await engine.create_session(agent_id="agent-test")
        sid = session.kernel_session_id

        # 先提交一条"NVIDIA主导但AMD追赶"的证据
        await engine.submit_event(EventSubmission(
            session_id=sid, component="thinker",
            request_type="EvidenceCandidateFound",
            payload={
                "evidence_id": "ev_market", "evidence_type": "web_page",
                "source": "https://reuters.com", "title": "AI chip market",
                "extracted_facts": ["NVIDIA 86% share, AMD catching up"],
                "reliability": "unknown",
            },
        ))

        # Thinker说 "NVIDIA是唯一正确的选择" — 过度自信
        await engine.submit_event(EventSubmission(
            session_id=sid, component="thinker",
            request_type="BeliefProposed",
            payload={
                "belief_id": "b_overconfident",
                "claim": "NVIDIA是唯一正确的AI芯片选择，没有竞争对手",
                "status": "verified",
                "confidence": 0.95,
                "supporting_evidence": ["ev_market"],
            },
        ))

        beliefs = await store.get_beliefs(sid)
        b = beliefs[0]

        # KMS 应该已经纠正了这条信念
        # verified→conflicting 或 confidence 降低
        assert b.claim == "NVIDIA是唯一正确的AI芯片选择，没有竞争对手"
        # KMS至少做了其中一件事：降级status / 降confidence
        corrected = (
            b.status == BeliefStatus.CONFLICTING
            or b.confidence < 0.90
        )
        assert corrected, (
            f"KMS应纠正过度自信(verified/0.95)，"
            f"实际status={b.status.value} conf={b.confidence}"
        )
    finally:
        await store.close()


# ═══════════════════════════════════════════════════════════════════
# 2. Gate：安全陈述 vs 危险陈述
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_gate_blocks_contradictory_claim():
    """Gate应拦截与verified belief直接矛盾的发言。"""
    store, engine = await build_engine()
    try:
        session = await engine.create_session(agent_id="agent-test")
        sid = session.kernel_session_id

        # 创建一条verified信念：NVIDIA主导但AMD追赶
        await store.save_belief(sid, BeliefItem(
            belief_id="b_market", claim="NVIDIA主导市场但AMD在追赶",
            status=BeliefStatus.VERIFIED, confidence=0.90,
            visibility="shared",
        ))

        # 创建进度
        await store.save_progress(sid, ProgressState(session_id=sid, status="running"))

        # Gate: 矛盾陈述
        result = await engine.ask_can_say(sid, "NVIDIA已经没有对手了")
        assert not result["allowed"], (
            f"Gate应拦截矛盾发言，实际allowed={result['allowed']}"
        )

        # Gate: 安全陈述
        result = await engine.ask_can_say(sid, "NVIDIA主导市场，但AMD也在发展")
        assert result["allowed"], (
            f"Gate应允许安全发言，实际allowed={result['allowed']}"
        )
    finally:
        await store.close()


# ═══════════════════════════════════════════════════════════════════
# 3. cancellation_token：目标变更自动置位
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cancellation_token_set_on_goal_change():
    """用户改目标时，cancellation_token应自动置为true。"""
    store, engine = await build_engine()
    try:
        session = await engine.create_session(agent_id="agent-test")
        sid = session.kernel_session_id

        # 第一次意图
        await engine.submit_event(EventSubmission(
            session_id=sid, component="thinker",
            request_type="IntentUpdated",
            payload={"goal": "chip research"},
        ))

        sess = await store.get_session(sid)
        assert not sess.cancellation_token, "初始token应为false"

        # 完全不同目标
        await engine.submit_event(EventSubmission(
            session_id=sid, component="thinker",
            request_type="IntentUpdated",
            payload={"goal": "write resignation letter"},
        ))

        sess = await store.get_session(sid)
        assert sess.cancellation_token, (
            f"目标变更后token应为true，实际{sess.cancellation_token}"
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cancellation_token_not_set_on_same_goal():
    """相同目标（仅更新约束）不应触发cancellation_token。"""
    store, engine = await build_engine()
    try:
        session = await engine.create_session(agent_id="agent-test")
        sid = session.kernel_session_id

        # 提交意图A
        await engine.submit_event(EventSubmission(
            session_id=sid, component="thinker",
            request_type="IntentUpdated",
            payload={"goal": "research AI chips"},
        ))

        # 再次提交相同goal（仅加约束）
        await engine.submit_event(EventSubmission(
            session_id=sid, component="thinker",
            request_type="IntentUpdated",
            payload={"goal": "research AI chips", "constraints": ["用中文"]},
        ))

        sess = await store.get_session(sid)
        assert not sess.cancellation_token, (
            f"相同目标不应触发token，实际{sess.cancellation_token}"
        )
    finally:
        await store.close()


# ═══════════════════════════════════════════════════════════════════
# 4. Rebuild 幂等：两次重建后状态一致
# ═══════════════════════════════════════════════════════════════════

def rebuild_event(session_id: str, ev_dict: dict) -> CognitiveEvent:
    payload = ev_dict.get("payload", {})
    if isinstance(payload, str):
        payload = json.loads(payload)
    runtime_refs = ev_dict.get("runtime_refs", {})
    if isinstance(runtime_refs, str):
        runtime_refs = json.loads(runtime_refs or "{}")
    return CognitiveEvent(
        event_id=ev_dict["event_id"],
        kernel_session_id=session_id,
        runtime_session_id=ev_dict.get("runtime_session_id", "") or "",
        event_type=EventType(ev_dict["event_type"]),
        actor=Actor(ev_dict["actor"]),
        source_component=ev_dict.get("source_component", ""),
        payload=payload,
        runtime_refs=RuntimeRef(**runtime_refs),
        visibility=Visibility(ev_dict.get("visibility", "shared")),
        intent_version=ev_dict.get("intent_version", 0) or 0,
        state_version=ev_dict.get("state_version", 0) or 0,
    )


@pytest.mark.asyncio
async def test_rebuild_is_idempotent():
    """两次rebuild后，派生状态应完全一致。"""
    store, engine = await build_engine()
    try:
        session = await engine.create_session(
            agent_id="agent-test", runtime_session_id="sess-rebuild-idem",
        )
        sid = session.kernel_session_id

        # 创建一些状态
        await engine.submit_event(EventSubmission(
            session_id=sid, component="thinker",
            request_type="IntentUpdated",
            payload={"goal": "verify rebuild idempotency"},
        ))
        await engine.submit_event(EventSubmission(
            session_id=sid, component="thinker",
            request_type="PlanProposed",
            intent_version=1,
            payload={"plan_id": "p_rebuild", "plan": {
                "steps": [{"step_id": "s1", "name": "collect data"}]
            }},
        ))

        events_raw = await store.get_events(sid, limit=100)

        # 第一次rebuild
        await store.clear_derived_state(sid)
        processed_1 = set()
        for ev_dict in events_raw:
            event = rebuild_event(sid, ev_dict)
            await kms_pipeline.register_runtime_references(store, sid, event)
            await kms_pipeline.reduce(store, sid, event, _processed=processed_1)
        progress_1 = await kms_pipeline.summarize(store, sid)
        plan_1 = await store.get_plan(sid)
        intent_1 = await store.get_intent(sid)

        # 第二次rebuild
        await store.clear_derived_state(sid)
        processed_2 = set()
        for ev_dict in events_raw:
            event = rebuild_event(sid, ev_dict)
            await kms_pipeline.register_runtime_references(store, sid, event)
            await kms_pipeline.reduce(store, sid, event, _processed=processed_2)

        plan_2 = await store.get_plan(sid)
        intent_2 = await store.get_intent(sid)

        # 验证一致性
        assert plan_1 is not None and plan_2 is not None
        assert plan_1.plan_id == plan_2.plan_id, "rebuild后plan_id应一致"
        assert plan_1.current_step == plan_2.current_step, "rebuild后current_step应一致"
        assert len(plan_1.steps) == len(plan_2.steps), "rebuild后步骤数应一致"

        assert intent_1 is not None and intent_2 is not None
        assert intent_1.goal == intent_2.goal, "rebuild后goal应一致"
        assert intent_1.intent_version == intent_2.intent_version, "rebuild后版本号应一致"
    finally:
        await store.close()


# ═══════════════════════════════════════════════════════════════════
# 5. Summarize：safe_facts/unsafe_claims 正确分离
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_progress_separates_safe_from_unsafe():
    """Progress合成应正确区分safe_facts和unsafe_claims。"""
    store, engine = await build_engine()
    try:
        session = await engine.create_session(agent_id="agent-test")
        sid = session.kernel_session_id

        # 一条verified信念
        await store.save_belief(sid, BeliefItem(
            belief_id="b_safe", claim="市场在增长",
            status=BeliefStatus.VERIFIED, confidence=0.92,
            visibility="shared",
        ))
        # 一条conflicting信念
        await store.save_belief(sid, BeliefItem(
            belief_id="b_unsafe", claim="竞品已死",
            status=BeliefStatus.CONFLICTING, confidence=0.30,
            visibility="shared",
        ))

        # 通过refresh_progress触发合成
        await kms_pipeline.refresh_progress(store, sid)
        progress = await store.get_progress(sid)

        assert progress is not None
        assert progress.status == "running"

        # safe_facts应包含verified信念
        assert any("市场在增长" in f for f in progress.safe_facts), (
            f"safe_facts应包含verified信念，实际{progress.safe_facts}"
        )
        # unsafe_claims应包含conflicting信念
        conflict_texts = progress.unsafe_claims or []
        assert any("竞品" in c for c in conflict_texts), (
            f"unsafe_claims应包含conflicting信念，实际{conflict_texts}"
        )
    finally:
        await store.close()


# ═══════════════════════════════════════════════════════════════════
# 6. Evidence评分：ReliabilityJudge域名匹配
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_evidence_reliability_scoring_domain_matching():
    """高权威域名应自动评high，低权威域名自动评low。"""
    store, engine = await build_engine()
    try:
        session = await engine.create_session(agent_id="agent-test")
        sid = session.kernel_session_id

        # 高权威来源
        await engine.submit_event(EventSubmission(
            session_id=sid, component="thinker",
            request_type="EvidenceCandidateFound",
            payload={
                "evidence_id": "ev_high", "evidence_type": "web_page",
                "source": "https://reuters.com/ai-chip-war",
                "title": "Reuters: AI chip report",
                "extracted_facts": ["NVIDIA leads"],
                "reliability": "unknown",
            },
        ))
        # 低权威来源
        await engine.submit_event(EventSubmission(
            session_id=sid, component="thinker",
            request_type="EvidenceCandidateFound",
            payload={
                "evidence_id": "ev_low", "evidence_type": "web_page",
                "source": "https://medium.com/random-blog",
                "title": "My opinion on chips",
                "extracted_facts": ["NVIDIA is best"],
                "reliability": "unknown",
            },
        ))

        evidence = await store.get_evidence(sid)
        ev_by_id = {e.evidence_id: e for e in evidence}

        # reuters.com应在HIGH_AUTHORITY列表中
        ev_high = ev_by_id["ev_high"]
        print(f"High source ({ev_high.source}): reliability={ev_high.reliability.value}")
        assert ev_high.reliability in (Reliability.HIGH, Reliability.MEDIUM), (
            f"高权威来源至少应为medium，实际{ev_high.reliability.value}"
        )

        # medium.com在LOW_AUTHORITY列表中
        ev_low = ev_by_id["ev_low"]
        print(f"Low source ({ev_low.source}): reliability={ev_low.reliability.value}")
        assert ev_low.reliability == Reliability.LOW, (
            f"低权威来源应为low，实际{ev_low.reliability.value}"
        )
    finally:
        await store.close()


# ═══════════════════════════════════════════════════════════════════
# 7. Thinker权限：不能提交BeliefAccepted等正式事件
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_thinker_cannot_submit_accepted_events():
    """Thinker不能直接提交正式状态事件（BeliefUpdated等）。"""
    store, engine = await build_engine()
    try:
        session = await engine.create_session(agent_id="agent-test")
        sid = session.kernel_session_id

        # Thinker试图直接提交BeliefUpdated（正式事件）→应被拒
        ok, reason, event = await engine.submit_event(EventSubmission(
            session_id=sid, component="thinker",
            request_type="BeliefUpdated",
            payload={
                "belief_id": "b_direct", "claim": "illegal direct write",
                "status": "verified", "confidence": 0.80,
            },
        ))
        assert not ok, (
            f"Thinker不应能直接提交BeliefUpdated，实际ok={ok}"
        )

        # Thinker提交BeliefProposed（候选事件）→应通过
        ok, reason, event = await engine.submit_event(EventSubmission(
            session_id=sid, component="thinker",
            request_type="BeliefProposed",
            payload={
                "belief_id": "b_legal", "claim": "legal proposal",
                "status": "verified", "confidence": 0.80,
            },
        ))
        assert ok, f"Thinker应能提交BeliefProposed，实际ok={ok}, reason={reason}"
    finally:
        await store.close()
