"""Normalize stage for KMS event submissions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from src.kms.decisioning.model import DEEPSEEK_API_KEY
from src.schema.events import (
    Actor,
    CognitiveEvent,
    EventSubmission,
    EventType,
    RuntimeRef,
    Visibility,
)

logger = logging.getLogger(__name__)


TALKER_REQUEST_MAP = {
    "GET_TALKER_CONTEXT": None,
    "SUBMIT_USER_MESSAGE": EventType.INTENT_UPDATED,
    "REGISTER_USER_INTENT_UPDATE": EventType.INTENT_UPDATED,
    "REGISTER_COMMITMENT_PROPOSAL": EventType.COMMITMENT_CREATED,
    "ASK_CAN_SAY": None,
    "ASK_CAN_DO": None,
}


THINKER_EVENT_MAP = {
    "PlanProposed": EventType.PLAN_PROPOSED,
    "BeliefProposed": EventType.BELIEF_PROPOSED,
    "EvidenceCandidateFound": EventType.EVIDENCE_CANDIDATE_FOUND,
    "ToolStarted": EventType.TOOL_STARTED,
    "ToolCompleted": EventType.TOOL_COMPLETED,
    "ToolFailed": EventType.TOOL_FAILED,
    "ToolRetried": EventType.TOOL_RETRIED,
    "StepStarted": EventType.STEP_STARTED,
    "ConflictDetected": EventType.CONFLICT_DETECTED,
    "VerificationWarningRaised": EventType.VERIFICATION_WARNING_RAISED,
    "TaskCompleted": EventType.TASK_COMPLETED,
    "TaskFailed": EventType.TASK_FAILED,
    "IntentUpdated": EventType.INTENT_UPDATED,
    "SessionCancelled": EventType.SESSION_CANCELLED,
    "ReplanRequest": EventType.REPLAN_REQUEST,
    "RiskAssessment": EventType.RISK_ASSESSMENT,
    "ReasoningSummary": EventType.REASONING_SUMMARY,
    "RawResultAvailable": EventType.RAW_RESULT_AVAILABLE,
    "ActionBlocked": EventType.ACTION_BLOCKED,
    "VerificationResult": EventType.VERIFICATION_RESULT,
    "CompletionCheck": EventType.COMPLETION_CHECK,
}


@dataclass
class NormalizeResult:
    """Normalize 阶段的输出。"""

    accepted: bool
    event: Optional[CognitiveEvent] = None
    reason: Optional[str] = None
    is_read_only: bool = False


NORMALIZE_SYSTEM = """You are a cognitive event classifier for an AI agent Kernel. Given a natural language user message sent through the Talker, classify what kind of event it represents and extract structured payload.

Respond ONLY with a JSON object, no markdown, no explanation.

Event types and their payloads:
- "IntentUpdated": payload has "goal" (string, the user's stated objective) and optionally "constraints" (array of strings)
- "BeliefProposed": payload has "belief_id" (derive from content), "claim" (string), "status" ("verified"/"likely"/"unverified"), "confidence" (0.0-1.0)
- "PlanProposed": payload has "plan_id", "plan" with "steps" array
- "EvidenceCandidateFound": payload has "evidence_id", "evidence_type", "source", "title", "extracted_facts" (array of strings), "reliability"
- "ToolStarted": payload has "action_id", "tool", "input_summary"
- "TaskCompleted": payload has "step_id"
- "CommitmentCreated": payload has "commitment_id", "description"

Rules:
1. If user describes a task to do or a question to answer → IntentUpdated
2. If user states a fact or opinion → BeliefProposed
3. If user gives a URL or reference → EvidenceCandidateFound
4. If user asks to check/verify something → IntentUpdated with constraints
5. If message is just greeting/thanks/noise → IntentUpdated with empty goal
6. Always preserve the original meaning in Chinese if the input is Chinese

Examples:
Input: "帮我查一下最近AI公司融资情况"
Output: {"event_type":"IntentUpdated","payload":{"goal":"查询最近AI公司融资情况","constraints":[]}}

Input: "我觉得这个数据不对，上次说是50万，这次变42万了"
Output: {"event_type":"IntentUpdated","payload":{"goal":"验证数据一致性","constraints":["上次说是50万","这次变42万"]}}

Input: "你好"
Output: {"event_type":"IntentUpdated","payload":{"goal":"greeting"}}"""


async def normalize(submission: EventSubmission) -> NormalizeResult:
    """将原始提交转换为结构化 CognitiveEvent。"""
    component = submission.component
    request_type = submission.request_type

    if component == "talker" and request_type == "raw":
        raw_text = submission.payload.get("text", "")
        if not raw_text:
            return NormalizeResult(False, reason="Raw request missing 'text' in payload")
        return await _normalize_from_text(submission, raw_text)

    if component == "talker":
        event_type = TALKER_REQUEST_MAP.get(request_type)
        if event_type is None:
            return NormalizeResult(True, is_read_only=True)
        actor = Actor.TALKER
    elif component == "thinker":
        event_type = THINKER_EVENT_MAP.get(request_type)
        if event_type is None:
            try:
                event_type = EventType(request_type)
            except ValueError:
                return NormalizeResult(False, reason=f"Unknown event type: {request_type}")
        actor = Actor.THINKER
    else:
        return NormalizeResult(False, reason=f"Unknown component: {component}")

    runtime_refs = RuntimeRef()
    merged_runtime_refs = {}
    if isinstance(submission.payload.get("runtime_refs"), dict):
        merged_runtime_refs.update(submission.payload["runtime_refs"])
    if submission.runtime_refs:
        merged_runtime_refs.update(submission.runtime_refs)
    if merged_runtime_refs:
        runtime_refs = RuntimeRef(**merged_runtime_refs)

    event = CognitiveEvent(
        event_id="",
        kernel_session_id=submission.session_id,
        run_id=submission.run_id,
        event_type=event_type,
        actor=actor,
        source_component=component,
        payload=dict(submission.payload),
        runtime_refs=runtime_refs,
        visibility=Visibility.SHARED,
        intent_version=submission.intent_version,
    )
    return NormalizeResult(True, event=event)


async def _normalize_from_text(submission: EventSubmission, text: str) -> NormalizeResult:
    if not DEEPSEEK_API_KEY:
        return _build_event(submission, EventType.INTENT_UPDATED, {"goal": text})

    try:
        from src.kms.decisioning.model import ModelCall

        model = ModelCall()
        followup_indicators = ["比", "呢", "上次", "刚才", "之前", "那个", "这个", "咋样"]
        is_new_task = any(kw in text for kw in ["帮我", "查一下", "搜索", "找一下", "什么是", "多少钱"])
        is_followup = (len(text) < 15 or any(kw in text for kw in followup_indicators)) and not is_new_task

        if is_followup and submission.intent_version > 0:
            return _build_event(submission, EventType.INTENT_UPDATED, {"constraints": [text]})

        result = await model.ask_json(
            system=NORMALIZE_SYSTEM,
            user=f"Input: \"{text}\"\nOutput:",
            max_tokens=200,
        )
        if result is None:
            return _build_event(submission, EventType.INTENT_UPDATED, {"goal": text})

        event_type_str = result.get("event_type", "IntentUpdated")
        payload = result.get("payload", {"goal": text})
        if submission.intent_version > 0 and event_type_str == "IntentUpdated" and "goal" in payload:
            payload["constraints"] = payload.get("constraints", []) + [payload["goal"]]
            payload.pop("goal", None)

        try:
            event_type = EventType(event_type_str)
        except ValueError:
            event_type = EventType.INTENT_UPDATED
        return _build_event(submission, event_type, payload)
    except Exception as exc:
        logger.warning("Normalize DeepSeek call failed: %s", exc)
        return _build_event(submission, EventType.INTENT_UPDATED, {"goal": text})


def _build_event(submission: EventSubmission, event_type: EventType, payload: dict) -> NormalizeResult:
    actor = Actor.TALKER if submission.component == "talker" else Actor.THINKER
    event = CognitiveEvent(
        event_id="",
        kernel_session_id=submission.session_id,
        run_id=submission.run_id,
        event_type=event_type,
        actor=actor,
        source_component=submission.component,
        payload=payload,
        visibility=Visibility.SHARED,
        intent_version=submission.intent_version,
    )
    return NormalizeResult(True, event=event)
