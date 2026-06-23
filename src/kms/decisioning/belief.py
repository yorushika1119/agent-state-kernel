"""BeliefReviewJudge — 审查信念与支撑证据的一致性。

属于 Arbitrate 阶段（第 5 阶段）。
只在 BELIEF_UPDATED 事件时运行。

核心职责：Thinker 提交信念声明→KMS 对比支撑证据→自动校准置信度。
防止 Thinker 过度自信或将无依据的声明标为 verified。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from src.kms.decisioning.judges import BaseJudge, JudgeResult
from src.kms.decisioning.model import ModelCall
from src.schema.events import CognitiveEvent, EventType
from src.schema.state import BeliefItem, EvidenceItem

logger = logging.getLogger(__name__)

# ── DeepSeek 审查系统提示词 ──
# 核心任务：判断信念声明是否与证据一致，给出修正建议

BELIEF_REVIEW_SYSTEM = """You review AI agent beliefs for consistency with their supporting evidence.

Given a belief claim and its supporting evidence facts, determine:
1. "consistent": true if the claim is supported by the evidence
2. "suggested_confidence": a number 0.0-1.0 for how confident we should be
3. "suggested_status": "verified" (>0.8), "likely" (0.5-0.8), "conflicting" (if evidence contradicts), "unverified" (<0.5)
4. "issues": list of problems (empty if none)

Respond with ONLY a JSON object, no markdown."""


def _rule_review_overconfident_claim(
    claim: str,
    supporting_facts: List[str],
    declared_confidence: float,
) -> JudgeResult | None:
    content = f"{claim} {' '.join(supporting_facts)}".lower()
    claim_lower = claim.lower()
    overclaims_no_competitor = any(
        marker in claim_lower
        for marker in ("唯一", "没有竞争对手", "没有对手", "only", "no competitor")
    )
    evidence_mentions_competition = any(
        marker in content
        for marker in ("amd", "竞争", "追赶", "catching up", "competitor")
    )
    if overclaims_no_competitor and evidence_mentions_competition:
        return JudgeResult(
            judge_name="belief_review_judge",
            verdict="modify",
            reason="支撑证据提到竞争者，不能声明没有竞争对手",
            modifications={
                "status": "conflicting",
                "confidence": min(declared_confidence, 0.6),
            },
        )
    return None


class BeliefReviewJudge(BaseJudge):
    """审查信念声明是否与其支撑证据一致。

    防止 Thinker：
    - 对未经证实的声明过度自信（声称 verified 但证据薄弱）
    - 做出逻辑跳跃（一个数据点 → 全局推广）
    - 忽略反证据

    评判器调用 DeepSeek 对比 claim 和 supporting_evidence，
    生成修正的置信度和状态。偏差 ≥15% 时触发修正。
    """

    def __init__(self, api_key: str = ""):
        self.model = ModelCall(api_key=api_key)

    @property
    def name(self) -> str:
        return "belief_review_judge"

    async def evaluate(
        self,
        event: CognitiveEvent,
        existing_evidence: List[EvidenceItem],
        existing_beliefs: List[BeliefItem],
    ) -> JudgeResult:
        # 只处理信念事件
        if event.event_type != EventType.BELIEF_UPDATED:
            return JudgeResult(judge_name=self.name, verdict="accept")

        claim = event.payload.get("claim", "")
        supporting_ids = event.payload.get("supporting_evidence", [])
        declared_confidence = event.payload.get("confidence", 0.5)
        declared_status = event.payload.get("status", "unverified")

        # 无声明或无支撑证据 → 强制降级
        if not claim or not supporting_ids:
            return JudgeResult(
                judge_name=self.name,
                verdict="modify",
                reason="信念缺少声明或支持证据",
                modifications={"confidence": 0.3, "status": "unverified"},
            )

        # 从证据库收集支撑事实
        evidence_map = {e.evidence_id: e for e in existing_evidence}
        supporting_facts = []
        for eid in supporting_ids:
            ev = evidence_map.get(eid)
            if ev:
                supporting_facts.extend(ev.extracted_facts)

        # 支撑证据在库中找不到 → 降级
        if not supporting_facts:
            return JudgeResult(
                judge_name=self.name,
                verdict="modify",
                reason="支持证据ID无法在证据库中找到",
                modifications={"confidence": min(declared_confidence, 0.4)},
            )

        rule_result = _rule_review_overconfident_claim(
            claim,
            supporting_facts,
            declared_confidence,
        )
        if rule_result is not None:
            return rule_result

        # ── 调用 DeepSeek 审查 ──
        facts_text = "\n".join(f"- {f}" for f in supporting_facts[:8])
        prompt = (
            f"Claim: {claim}\n\n"
            f"Supporting evidence facts:\n{facts_text}\n\n"
            f"Declared confidence: {declared_confidence}\n"
            f"Declared status: {declared_status}\n\n"
            f"Respond with JSON."
        )

        result = await self.model.ask_json(
            system=BELIEF_REVIEW_SYSTEM,
            user=prompt,
            max_tokens=200,
        )

        if result is None:
            return JudgeResult(judge_name=self.name, verdict="accept")

        suggested_conf = result.get("suggested_confidence", declared_confidence)
        suggested_status = result.get("suggested_status", declared_status)
        issues = result.get("issues", [])

        modifications = {}
        # 置信度偏差 >15% → 修正
        if abs(suggested_conf - declared_confidence) > 0.15:
            modifications["confidence"] = suggested_conf
        # 状态不一致 → 修正
        if suggested_status != declared_status:
            modifications["status"] = suggested_status

        if modifications:
            return JudgeResult(
                judge_name=self.name,
                verdict="modify",
                reason=(
                    f"置信度 {declared_confidence}→{suggested_conf}; "
                    + ("; ".join(issues) if issues else "一致性审查")
                ),
                modifications=modifications,
            )

        return JudgeResult(judge_name=self.name, verdict="accept")
