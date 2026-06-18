"""KMS 模型评判器 — 基于 DeepSeek LLM 的事件审查。

提供：
- ModelCall：统一的 LLM 调用抽象
- SemanticConflictJudge：检测语义矛盾（规则覆盖不了的情况）
- ContentReliabilityJudge：评估 medium 可靠性来源的内容质量

这些评判器需要 DeepSeek API key。不可用时静默跳过——
评判器流水线仍然只运行规则引擎。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from src.kms.judges import BaseJudge, JudgeResult
from src.schema.events import CognitiveEvent, EventType
from src.schema.state import BeliefItem, EvidenceItem

logger = logging.getLogger(__name__)

# ── DeepSeek 配置 ──
# API key 从环境变量读取，不在代码中硬编码
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_MODEL = "deepseek-chat"


# ===========================================================================
# ModelCall — 统一的 LLM 调用抽象
# ===========================================================================

@dataclass
class ModelCall:
    """单次 LLM 调用，带超时和 JSON 解析。

    使用 DeepSeek API（openai 兼容格式）。
    两个方法：
    - ask()：返回原始字符串
    - ask_json()：解析 JSON 响应

    失败时优雅降级——返回 None 而不是抛出异常。
    """

    api_key: str = DEEPSEEK_API_KEY
    model: str = DEFAULT_MODEL
    timeout: float = 15.0

    async def ask(self, system: str, user: str, max_tokens: int = 200) -> Optional[str]:
        """发送提示词并返回原始字符串响应。失败时返回 None。

        用于 Summarize 等不需要结构化 JSON 的场景。
        """
        if not self.api_key:
            return None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    DEEPSEEK_BASE,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "temperature": 0,
                        "max_tokens": max_tokens,
                    },
                )
                if resp.status_code != 200:
                    logger.warning("ModelCall HTTP %s: %s", resp.status_code, resp.text[:200])
                    return None
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("ModelCall failed: %s", e)
            return None

    async def ask_json(self, system: str, user: str, max_tokens: int = 200) -> Optional[Dict[str, Any]]:
        """发送提示词并将响应解析为 JSON。失败时返回 None。

        用于 Judge 和 Gate 等需要结构化输出的场景。
        处理截断的 JSON 和 markdown 包装。
        """
        raw = await self.ask(system, user, max_tokens)
        if not raw:
            return None

        # 尝试多种解析策略
        strategies = [
            lambda s: s,                           # 纯 JSON
            lambda s: s[s.find("{"):s.rfind("}")+1] if "{" in s else "",  # 提取第一对花括号
            lambda s: s.replace("```json", "").replace("```", "").strip(),  # 移除 markdown
        ]

        for strategy in strategies:
            try:
                cleaned = strategy(raw)
                if cleaned:
                    return json.loads(cleaned)
            except (json.JSONDecodeError, ValueError):
                continue

        logger.warning("ModelCall: could not parse JSON from response: %s", raw[:100])
        return None


# ===========================================================================
# SemanticConflictJudge — 语义冲突检测
# ===========================================================================

SEMANTIC_CONFLICT_SYSTEM = """You detect semantic conflicts between pieces of evidence.

Two pieces of evidence may appear to agree but actually contradict each other at a semantic level.
For example:
- A says "the project is on schedule"
- B says "the deadline has been pushed back by 6 months"

Rule-based conflict detection would miss this because there are no overlapping numbers.
You detect these semantic-level contradictions.

Respond with JSON:
{"conflict": true/false, "reason": "one sentence explanation"}

Only mark as conflict if there's a clear semantic contradiction. If they discuss different aspects or time periods, say false."""


class SemanticConflictJudge(BaseJudge):
    """使用 LLM 检测语义层面的冲突。

    规则 ConflictJudge 只能检测数值冲突（"39万 vs 42万"）。
    这个评判器检测规则覆盖不了的语义层面矛盾：
    - "进展顺利" vs "严重延期"
    - "市场增长" vs "销量暴跌"
    """

    def __init__(self, api_key: str = DEEPSEEK_API_KEY):
        self.model = ModelCall(api_key=api_key)

    @property
    def name(self) -> str:
        return "semantic_conflict_judge"

    async def evaluate(
        self,
        event: CognitiveEvent,
        existing_evidence: List[EvidenceItem],
        existing_beliefs: List[BeliefItem],
    ) -> JudgeResult:
        if event.event_type != EventType.EVIDENCE_ACCEPTED:
            return JudgeResult(judge_name=self.name, verdict="accept")

        if len(existing_evidence) < 1:
            return JudgeResult(judge_name=self.name, verdict="accept")

        new_text = " ".join(event.payload.get("extracted_facts", []))
        if not new_text:
            return JudgeResult(judge_name=self.name, verdict="accept")

        # 与最新的一条证据比较（避免 N² 次 LLM 调用）
        latest = existing_evidence[-1]
        old_text = " ".join(latest.extracted_facts or [])

        if not old_text:
            return JudgeResult(judge_name=self.name, verdict="accept")

        try:
            result = await self.model.ask_json(
                system=SEMANTIC_CONFLICT_SYSTEM,
                user=f"Evidence A: {old_text[:300]}\nEvidence B: {new_text[:300]}\nDo these conflict?",
                max_tokens=80,
            )
            if result and result.get("conflict"):
                from src.schema.events import Actor, CognitiveEvent, EventType
                se = CognitiveEvent(
                    event_id="",
                    kernel_session_id=event.kernel_session_id,
                    event_type=EventType.CONFLICT_DETECTED,
                    actor=Actor.KERNEL_MANAGER,
                    source_component="kms",
                    payload={
                        "conflict_description": result.get("reason", "语义冲突"),
                        "evidence_a": latest.evidence_id,
                        "evidence_b": event.payload.get("evidence_id", ""),
                    },
                )
                return JudgeResult(
                    judge_name=self.name,
                    verdict="accept",
                    reason=result.get("reason", ""),
                    side_effects=[se],
                )
        except Exception as e:
            logger.debug("SemanticConflictJudge failed: %s", e)

        return JudgeResult(judge_name=self.name, verdict="accept")


# ===========================================================================
# ContentReliabilityJudge — 内容质量评估
# ===========================================================================

CONTENT_RELIABILITY_SYSTEM = """You evaluate the factual quality of web content.

Given a title and extracted facts from a web page, assess if the content is credible and factual or if it's speculation/opinion/low-quality.

Respond with JSON:
{"credible": true/false, "reason": "one sentence", "suggested_reliability": "high"/"medium"/"low"}

Rules:
- Official announcements, verified data, scientific results → credible
- Personal opinions, speculation, marketing claims → not credible
- Be skeptical of bold claims without evidence"""


class ContentReliabilityJudge(BaseJudge):
    """评估来源的 factual 质量。

    当前只评估 medium 可靠性来源（blog、news 聚合器等）。
    跳过 high（已信任）和 low（已不信任）来源，避免浪费 API 调用。

    高风险判断会触发降级——如 SEO 农场伪装成新闻报道。
    """

    def __init__(self, api_key: str = DEEPSEEK_API_KEY):
        self.model = ModelCall(api_key=api_key)

    @property
    def name(self) -> str:
        return "content_reliability_judge"

    async def evaluate(
        self,
        event: CognitiveEvent,
        existing_evidence: List[EvidenceItem],
        existing_beliefs: List[BeliefItem],
    ) -> JudgeResult:
        if event.event_type != EventType.EVIDENCE_ACCEPTED:
            return JudgeResult(judge_name=self.name, verdict="accept")

        # 只评估 medium 可靠性来源——high 已信任，low 已不信任
        reliability = event.payload.get("reliability", "unknown")
        if reliability != "medium":
            return JudgeResult(judge_name=self.name, verdict="accept")

        title = event.payload.get("title", "")
        facts = " ".join(event.payload.get("extracted_facts", []))
        if not facts:
            return JudgeResult(judge_name=self.name, verdict="accept")

        try:
            result = await self.model.ask_json(
                system=CONTENT_RELIABILITY_SYSTEM,
                user=f"Title: {title[:100]}\nExtracted facts: {facts[:300]}\nIs this credible?",
                max_tokens=80,
            )
            if result and not result.get("credible", True):
                return JudgeResult(
                    judge_name=self.name,
                    verdict="modify",
                    reason=f"内容不可靠: {result.get('reason', '')}",
                    modifications={"reliability": "low"},
                )
        except Exception as e:
            logger.debug("ContentReliabilityJudge failed: %s", e)

        return JudgeResult(judge_name=self.name, verdict="accept")
