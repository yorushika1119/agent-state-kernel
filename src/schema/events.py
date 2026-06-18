"""Event type definitions for the Agent State Kernel.

All state changes flow through events. The State Reducer aggregates
events into derived state. No component writes state directly.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from src.utils.time import utc_now


class EventType(StrEnum):
    """Cognitive event types from the architecture document."""
    SESSION_CREATED = "SessionCreated"
    USER_MESSAGE_RECEIVED = "UserMessageReceived"
    INTENT_UPDATED = "IntentUpdated"
    PLAN_PROPOSED = "PlanProposed"
    PLAN_ACCEPTED = "PlanAccepted"
    STEP_STARTED = "StepStarted"
    TOOL_STARTED = "ToolStarted"
    TOOL_COMPLETED = "ToolCompleted"
    TOOL_FAILED = "ToolFailed"
    TOOL_RETRIED = "ToolRetried"
    EVIDENCE_CANDIDATE_FOUND = "EvidenceCandidateFound"
    EVIDENCE_ACCEPTED = "EvidenceAccepted"
    BELIEF_PROPOSED = "BeliefProposed"
    BELIEF_UPDATED = "BeliefUpdated"
    CONFLICT_DETECTED = "ConflictDetected"
    VERIFICATION_WARNING_RAISED = "VerificationWarningRaised"
    COMMITMENT_CREATED = "CommitmentCreated"
    COMMITMENT_UPDATED = "CommitmentUpdated"
    USER_CONFIRMATION_REQUIRED = "UserConfirmationRequired"
    PROGRESS_SYNTHESIZED = "ProgressSynthesized"
    EXTERNAL_SYNC_REQUESTED = "ExternalSyncRequested"
    TASK_COMPLETED = "TaskCompleted"
    TASK_FAILED = "TaskFailed"
    RUN_INTERRUPTED = "RunInterrupted"
    SESSION_CANCELLED = "SessionCancelled"
    # §6.2 Thinker 协议扩展类型
    REPLAN_REQUEST = "ReplanRequest"
    RISK_ASSESSMENT = "RiskAssessment"
    REASONING_SUMMARY = "ReasoningSummary"
    RAW_RESULT_AVAILABLE = "RawResultAvailable"
    ACTION_BLOCKED = "ActionBlocked"
    VERIFICATION_RESULT = "VerificationResult"
    COMPLETION_CHECK = "CompletionCheck"


class Visibility(StrEnum):
    """层级可见性（§5.11）：谁可以看到这个事件/状态。"""
    PRIVATE = "private"           # 只允许 KMS 和审计组件
    SHARED = "shared"             # Thinker 和 KMS
    TALKER_VISIBLE = "talker_visible"  # Talker 可读取
    USER_VISIBLE = "user_visible"      # 可直接对用户表达
    EXTERNAL_SYNC = "external_sync"    # 可同步给 Multica 等外部系统


class Actor(StrEnum):
    TALKER = "talker"
    THINKER = "thinker"
    KERNEL_MANAGER = "kernel_manager"
    EXECUTOR = "executor"
    VERIFIER = "verifier"


class RuntimeRef(BaseModel):
    """Reference to a resource in the host Agent Runtime."""
    message_id: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_result_ref: Optional[str] = None
    checkpoint_ref: Optional[str] = None
    process_session_id: Optional[str] = None


class CognitiveEvent(BaseModel):
    """A single cognitive event in the event log."""
    event_id: str = ""
    kernel_session_id: str
    runtime_session_id: str = ""
    run_id: str = ""
    event_type: EventType
    actor: Actor
    source_component: str = ""  # e.g. "thinker", "executor", "verifier"
    payload: Dict[str, Any] = Field(default_factory=dict)
    runtime_refs: RuntimeRef = Field(default_factory=RuntimeRef)
    visibility: Visibility = Visibility.SHARED
    intent_version: int = 0
    state_version: int = 0
    created_at: datetime = Field(default_factory=utc_now)


class EventSubmission(BaseModel):
    """External component submits this to propose an event.
    
    KMS (or the policy layer in MVP) validates and transforms
    this into a CognitiveEvent before appending to the log.
    """
    session_id: str
    component: str  # "talker", "thinker"
    request_type: str  # EventType value or KMS request type
    payload: Dict[str, Any] = Field(default_factory=dict)
    intent_version: int = 0
    run_id: str = ""
    runtime_refs: Optional[Dict[str, str]] = None
