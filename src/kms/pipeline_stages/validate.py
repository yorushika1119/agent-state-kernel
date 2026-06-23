"""Validate stage for KMS cognitive events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.schema.events import Actor, CognitiveEvent, EventType


@dataclass
class ValidateResult:
    """Validate 阶段的输出。"""

    allowed: bool
    reason: Optional[str] = None


TALKER_FORBIDDEN = {
    EventType.BELIEF_UPDATED,
    EventType.EVIDENCE_ACCEPTED,
    EventType.PLAN_ACCEPTED,
    EventType.TASK_COMPLETED,
}


THINKER_ALLOWED = {
    EventType.PLAN_PROPOSED,
    EventType.BELIEF_PROPOSED,
    EventType.TOOL_STARTED,
    EventType.TOOL_COMPLETED,
    EventType.TOOL_FAILED,
    EventType.TOOL_RETRIED,
    EventType.EVIDENCE_CANDIDATE_FOUND,
    EventType.STEP_STARTED,
    EventType.CONFLICT_DETECTED,
    EventType.VERIFICATION_WARNING_RAISED,
    EventType.TASK_COMPLETED,
    EventType.TASK_FAILED,
    EventType.INTENT_UPDATED,
    EventType.SESSION_CANCELLED,
    EventType.REPLAN_REQUEST,
    EventType.RISK_ASSESSMENT,
    EventType.REASONING_SUMMARY,
    EventType.RAW_RESULT_AVAILABLE,
    EventType.ACTION_BLOCKED,
    EventType.VERIFICATION_RESULT,
    EventType.COMPLETION_CHECK,
}


def validate(
    event: CognitiveEvent,
    existing_intent_version: int = 0,
    *,
    session_status: str = "running",
    active_run_id: str = "",
) -> ValidateResult:
    """根据策略规则验证事件。"""
    et = event.event_type

    if event.actor == Actor.TALKER:
        if et in TALKER_FORBIDDEN:
            return ValidateResult(False, f"Talker cannot submit {et.value}. Use proposal types instead.")

    if event.actor == Actor.THINKER:
        if et not in THINKER_ALLOWED:
            return ValidateResult(False, f"Thinker cannot submit {et.value}.")
        if session_status in {"paused", "cancelled", "completed", "failed"}:
            return ValidateResult(
                False,
                f"Session is {session_status}; thinker writes are not accepted.",
            )
        if active_run_id and et not in {EventType.INTENT_UPDATED, EventType.SESSION_CANCELLED}:
            if not event.run_id:
                return ValidateResult(False, "Thinker must include run_id.")
            if event.run_id != active_run_id:
                return ValidateResult(
                    False,
                    f"Stale thinker run: submitted {event.run_id}, active is {active_run_id}",
                )

    if (
        event.intent_version > 0
        and existing_intent_version > 0
        and event.intent_version < existing_intent_version
    ):
        return ValidateResult(
            False,
            f"Intent version mismatch: submitted v{event.intent_version}, current v{existing_intent_version}",
        )

    if et in (EventType.BELIEF_PROPOSED, EventType.BELIEF_UPDATED):
        claim = event.payload.get("claim", "")
        confidence = event.payload.get("confidence", 0)
        if not claim or len(claim.strip()) < 2:
            return ValidateResult(False, "Belief claim must be at least 2 characters")
        if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
            return ValidateResult(False, f"Belief confidence must be 0-1, got {confidence}")

    return ValidateResult(True)
