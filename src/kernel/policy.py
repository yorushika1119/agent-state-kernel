"""Policy layer — rule-based validation for MVP.

Replaces full KMS model with deterministic rules drawn from
the functional design doc. Model-based classification arrives
in a later phase.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from src.schema.events import EventSubmission, EventType, Visibility

logger = logging.getLogger(__name__)

# Rules extracted from Sections 5.5–5.12 of the functional design doc

# Talker can submit only these request types
TALKER_ALLOWED_REQUESTS = {
    "GET_TALKER_CONTEXT",
    "SUBMIT_USER_MESSAGE",
    "REGISTER_USER_INTENT_UPDATE",
    "REGISTER_COMMITMENT_PROPOSAL",
    "ASK_CAN_SAY",
    "ASK_CAN_DO",
}

# Thinker can submit these event types
THINKER_ALLOWED_EVENTS = {
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
}

# Talker can NOT directly submit these
TALKER_FORBIDDEN_EVENTS = {
    EventType.BELIEF_UPDATED,
    EventType.EVIDENCE_ACCEPTED,
    EventType.TASK_COMPLETED,
    EventType.PLAN_ACCEPTED,
}


def validate_submission(
    submission: EventSubmission, existing_intent_version: int = 0
) -> Tuple[bool, Optional[str], Optional[CognitiveEvent]]:
    """Validate an event/request submission against policy rules.

    Returns: (allowed, reason_if_denied, normalized_event_if_allowed)
    """
    from src.schema.events import CognitiveEvent, Actor, RuntimeRef

    component = submission.component  # "talker" or "thinker"
    request_type = submission.request_type

    # --- Talker enforcement ---
    if component == "talker":
        if request_type not in TALKER_ALLOWED_REQUESTS:
            return (
                False,
                f"Talker cannot submit '{request_type}'. Allowed: {TALKER_ALLOWED_REQUESTS}",
                None,
            )

        # Talker submitting a user message → IntentUpdated
        if request_type == "SUBMIT_USER_MESSAGE":
            event = CognitiveEvent(
                event_id="",
                kernel_session_id=submission.session_id,
                event_type=EventType.INTENT_UPDATED,
                actor=Actor.TALKER,
                source_component="talker",
                payload=submission.payload,
                visibility=Visibility.SHARED,
            )
            return True, None, event

        # Talker registering commitment
        if request_type == "REGISTER_COMMITMENT_PROPOSAL":
            event = CognitiveEvent(
                event_id="",
                kernel_session_id=submission.session_id,
                event_type=EventType.COMMITMENT_CREATED,
                actor=Actor.TALKER,
                source_component="talker",
                payload=submission.payload,
                visibility=Visibility.SHARED,
            )
            return True, None, event

        # ASK_CAN_SAY / ASK_CAN_DO — these don't write events,
        # they just query the Visibility Gate. Handled at API level.
        if request_type in ("ASK_CAN_SAY", "ASK_CAN_DO"):
            return True, None, None  # no event to write

        return True, None, None

    # --- Thinker enforcement ---
    if component == "thinker":
        # Try to parse as EventType
        try:
            event_type = EventType(request_type)
        except ValueError:
            return (
                False,
                f"Unknown thinkter event type: {request_type}",
                None,
            )

        if event_type not in THINKER_ALLOWED_EVENTS:
            return (
                False,
                f"Thinker cannot submit '{event_type}'. Use a proposal type instead.",
                None,
            )

        # EvidenceCandidateFound → auto-accept in MVP (no KMS model to verify)
        if event_type == EventType.EVIDENCE_CANDIDATE_FOUND:
            event_type = EventType.EVIDENCE_ACCEPTED

        # BeliefProposed → auto-accept in MVP
        if event_type == EventType.BELIEF_PROPOSED:
            event_type = EventType.BELIEF_UPDATED

        # PlanProposed → auto-accept in MVP
        if event_type == EventType.PLAN_PROPOSED:
            event_type = EventType.PLAN_ACCEPTED

        # Intent version check: Thinker cannot act on stale intent
        if (
            submission.intent_version > 0
            and existing_intent_version > 0
            and submission.intent_version < existing_intent_version
        ):
            return (
                False,
                f"Intent version mismatch: thinkter has v{submission.intent_version}, "
                f"current is v{existing_intent_version}. Repan required.",
                None,
            )

        runtime_refs = None
        if submission.runtime_refs:
            runtime_refs = RuntimeRef(**submission.runtime_refs)

        event = CognitiveEvent(
            event_id="",
            kernel_session_id=submission.session_id,
            event_type=event_type,
            actor=Actor.THINKER,
            source_component="thinker",
            payload=submission.payload,
            runtime_refs=runtime_refs or RuntimeRef(),
            visibility=Visibility.SHARED,
            intent_version=submission.intent_version,
        )
        return True, None, event

    return False, f"Unknown component: {component}", None
