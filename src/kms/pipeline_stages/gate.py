"""Visibility gate stage for KMS talker output."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from src.kms.pipeline_stages.summarize import refresh_progress
from src.kms.state.aliases import beliefs_from_claims

logger = logging.getLogger(__name__)


@dataclass
class GateResult:
    """Visibility Gate 的输出。"""

    allowed: bool
    reason: Optional[str] = None
    safe_alternative: Optional[str] = None


COMPLETION_KEYWORDS = [
    "完成",
    "已发送",
    "已发布",
    "成功了",
    "搞定了",
    "done",
    "completed",
    "finished",
]


def _rule_contradicts_verified_belief(beliefs, proposed_message: str) -> Optional[str]:
    message = proposed_message.lower()
    no_competitor_claim = any(
        marker in message
        for marker in ("没有对手", "没有竞争对手", "唯一", "no competitor", "only choice")
    )
    if not no_competitor_claim:
        return None

    for belief in beliefs:
        if getattr(getattr(belief, "status", None), "value", "") != "verified":
            continue
        claim = (belief.claim or "").lower()
        mentions_competition = any(
            marker in claim
            for marker in ("amd", "竞争", "追赶", "对手", "competitor", "catching up")
        )
        if mentions_competition:
            return belief.claim
    return None


async def gate(
    store,
    session_id: str,
    proposed_message: str = "",
    *,
    api_key: str = "",
) -> GateResult:
    """确定 Talker 能否安全地说某句话。"""

    progress = await store.get_progress(session_id)
    if not progress:
        progress = await refresh_progress(store, session_id)

    if not proposed_message:
        return GateResult(True)

    if any(kw in proposed_message.lower() for kw in COMPLETION_KEYWORDS):
        if progress.status != "completed":
            return GateResult(
                False,
                reason="任务尚未完成，不能宣称已完成",
                safe_alternative=progress.summary,
            )

    for claim in progress.unsafe_claims:
        if claim in proposed_message:
            return GateResult(
                False,
                reason=f"包含未验证内容: {claim}",
                safe_alternative="，".join(progress.safe_facts)
                if progress.safe_facts
                else progress.summary,
            )

    beliefs = beliefs_from_claims(await store.get_claim_items(session_id))
    contradicted = _rule_contradicts_verified_belief(beliefs, proposed_message)
    if contradicted:
        return GateResult(
            False,
            reason=f"与信念矛盾: {contradicted[:80]}",
            safe_alternative=progress.summary,
        )

    if beliefs and api_key:
        try:
            from src.kms.decisioning.model import ModelCall

            model = ModelCall()
            belief_text = "\n".join(
                f"- [{belief.status.value}] {belief.claim}"
                for belief in beliefs[:5]
            )
            result = await model.ask_json(
                system=(
                    "You are a fact-checker guarding an AI agent's output. "
                    "Check if a proposed message contradicts any of the agent's "
                    "verified beliefs. Respond ONLY with JSON: "
                    '{"contradicts": true/false, "which_belief": "brief quote of '
                    'contradicted belief", "reason": "one sentence"}'
                ),
                user=(
                    f"Agent's current beliefs:\n{belief_text}\n\n"
                    f'Proposed message: "{proposed_message}"\n\n'
                    "Does this message contradict any belief?"
                ),
                max_tokens=100,
            )
            if result and result.get("contradicts"):
                return GateResult(
                    False,
                    reason=(
                        f"与信念矛盾: {result.get('which_belief', '')[:80]} — "
                        f"{result.get('reason', '')}"
                    ),
                    safe_alternative=progress.summary,
                )
        except Exception as exc:
            logger.debug("Gate semantic check failed: %s", exc)

    return GateResult(True)
