"""Classify stage for KMS cognitive events."""

from __future__ import annotations

from dataclasses import dataclass

from src.schema.events import CognitiveEvent, EventType


@dataclass
class ClassifyResult:
    """Classify 阶段的输出——将事件路由到正确的状态类别。"""

    category: str


def classify(event: CognitiveEvent) -> ClassifyResult:
    """确定此事件属于哪个认知状态类别。"""
    et = event.event_type
    classification_map = {
        EventType.INTENT_UPDATED: "intent",
        EventType.PLAN_PROPOSED: "plan",
        EventType.PLAN_ACCEPTED: "plan",
        EventType.EVIDENCE_CANDIDATE_FOUND: "evidence",
        EventType.EVIDENCE_ACCEPTED: "evidence",
        EventType.BELIEF_PROPOSED: "belief",
        EventType.BELIEF_UPDATED: "belief",
        EventType.TOOL_STARTED: "execution",
        EventType.TOOL_COMPLETED: "execution",
        EventType.TOOL_FAILED: "execution",
        EventType.TOOL_RETRIED: "execution",
        EventType.STEP_STARTED: "plan",
        EventType.TASK_COMPLETED: "plan",
        EventType.TASK_FAILED: "plan",
        EventType.CONFLICT_DETECTED: "belief",
        EventType.VERIFICATION_WARNING_RAISED: "belief",
        EventType.COMMITMENT_CREATED: "commitment",
        EventType.COMMITMENT_UPDATED: "commitment",
        EventType.SESSION_CREATED: "intent",
        EventType.SESSION_CANCELLED: "intent",
        EventType.REPLAN_REQUEST: "plan",
        EventType.RISK_ASSESSMENT: "belief",
        EventType.REASONING_SUMMARY: "execution",
        EventType.RAW_RESULT_AVAILABLE: "execution",
        EventType.ACTION_BLOCKED: "execution",
        EventType.VERIFICATION_RESULT: "belief",
        EventType.COMPLETION_CHECK: "progress",
    }
    return ClassifyResult(category=classification_map.get(et, "execution"))
