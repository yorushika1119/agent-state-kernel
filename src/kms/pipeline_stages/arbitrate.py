"""Arbitrate stage for KMS cognitive events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from src.schema.events import Actor, CognitiveEvent, EventType
from src.schema.state import BeliefItem, EvidenceItem


CANDIDATE_TO_FINAL_EVENT = {
    EventType.PLAN_PROPOSED: EventType.PLAN_ACCEPTED,
    EventType.BELIEF_PROPOSED: EventType.BELIEF_UPDATED,
    EventType.EVIDENCE_CANDIDATE_FOUND: EventType.EVIDENCE_ACCEPTED,
}


@dataclass
class ArbitrateResult:
    """Arbitrate 阶段的输出——评判器裁决。"""

    modifications: Dict[str, Any] = field(default_factory=dict)
    side_effects: List[CognitiveEvent] = field(default_factory=list)
    judge_results: List[Dict[str, Any]] = field(default_factory=list)
    rejected: bool = False


async def arbitrate(
    event: CognitiveEvent,
    existing_evidence: List[EvidenceItem],
    existing_beliefs: List[BeliefItem],
    kms_url: str = "",
) -> ArbitrateResult:
    """对事件运行评判器。"""
    if kms_url:
        from src.kms.transport.remote import RemoteKMSClient

        client = RemoteKMSClient(kms_url)
        results = await client.evaluate(event, existing_evidence, existing_beliefs)
        mods = client.get_modifications(results)
        return ArbitrateResult(
            modifications=mods,
            side_effects=client.get_side_effects(results),
            judge_results=results,
            rejected=client.has_rejections(results),
        )

    from src.kms.decisioning.belief import BeliefReviewJudge
    from src.kms.decisioning.judges import (
        ConflictJudge,
        DedupJudge,
        KMSPipeline,
        ReliabilityJudge,
    )
    from src.kms.decisioning.model import ContentReliabilityJudge, SemanticConflictJudge

    pipeline = KMSPipeline()
    if event.event_type == EventType.BELIEF_UPDATED:
        pipeline.judges.append(BeliefReviewJudge())
    results = await pipeline.evaluate(event, existing_evidence, existing_beliefs)
    mods = pipeline.get_modifications(results)
    side_effects = pipeline.get_side_effects(results)
    judge_output = [
        {
            "judge_name": result.judge_name,
            "verdict": result.verdict,
            "reason": result.reason,
        }
        for result in results
    ]
    return ArbitrateResult(
        modifications=mods,
        side_effects=side_effects,
        judge_results=judge_output,
        rejected=any(result.verdict == "reject" for result in results),
    )


def is_candidate_event(event_type: EventType) -> bool:
    """只有 candidate/proposal 事件会被 KMS 提升为正式事件。"""
    return event_type in CANDIDATE_TO_FINAL_EVENT


def _accepted_event_type(event_type: EventType) -> EventType:
    return CANDIDATE_TO_FINAL_EVENT[event_type]


def should_arbitrate(event_type: EventType) -> bool:
    return event_type in (EventType.EVIDENCE_ACCEPTED, EventType.BELIEF_UPDATED)


def build_final_event(source_event: CognitiveEvent) -> CognitiveEvent:
    """把 Thinker 的 candidate/proposal 提升为 KMS 生成的正式事件。"""
    final_type = _accepted_event_type(source_event.event_type)
    payload = dict(source_event.payload)
    if final_type == EventType.PLAN_ACCEPTED and "intent_version" not in payload:
        payload["intent_version"] = source_event.intent_version

    return CognitiveEvent(
        event_id="",
        kernel_session_id=source_event.kernel_session_id,
        runtime_session_id=source_event.runtime_session_id,
        event_type=final_type,
        actor=Actor.KERNEL_MANAGER,
        source_component="kms",
        payload=payload,
        runtime_refs=source_event.runtime_refs,
        visibility=source_event.visibility,
        intent_version=source_event.intent_version,
    )
