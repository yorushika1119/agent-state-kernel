"""Derived state type definitions.

These are the current-state views produced by the State Reducer
from the event log. They are NOT written directly by any component.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.utils.time import utc_now


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class SessionStatus(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SessionLink(BaseModel):
    """Maps kernel session to runtime session + external task."""
    kernel_session_id: str
    agent_id: str = ""
    runtime_id: str = ""
    runtime_session_id: str = ""
    runtime_type: str = "cli-agent"
    external_source: str = ""
    external_workspace_id: str = ""
    external_issue_id: str = ""
    external_task_id: str = ""
    status: SessionStatus = SessionStatus.RUNNING
    intent_version: int = 0
    state_version: int = 0
    active_run_id: str = ""
    active_task_id: str = ""
    cancellation_token: bool = False  # §5.1：Thinker 收到停止信号
    last_paused_task_id: str = ""
    last_interrupted_run_id: str = ""
    last_interrupting_run_id: str = ""
    last_interrupt_reason: str = ""
    last_interrupt_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class UserSession(BaseModel):
    """User-facing runtime session observed by KMS."""

    user_session_id: str
    runtime_session_id: str = ""
    runtime_id: str = ""
    runtime_type: str = "cli-agent"
    agent_id: str = ""
    session_kind: str = "user_chat"
    created_by: str = "runtime"
    linked_task_ids: List[str] = Field(default_factory=list)
    active_task_id: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------

class IntentState(BaseModel):
    """Current user goal and constraints."""
    intent_version: int = 0
    goal: str = ""
    output_format: str = ""
    constraints: List[str] = Field(default_factory=list)
    priority: str = "normal"
    cancelled: bool = False
    last_user_update_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class PlanStep(BaseModel):
    step_id: str
    name: str
    status: StepStatus = StepStatus.PENDING
    owner: str = "thinker"  # "executor", "verifier"
    depends_on: List[str] = Field(default_factory=list)


class PlanStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PlanState(BaseModel):
    plan_id: str
    status: PlanStatus = PlanStatus.ACTIVE
    steps: List[PlanStep] = Field(default_factory=list)
    current_step: str = ""
    intent_version: int = 0


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

class TaskStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskSnapshot(BaseModel):
    """Task/goal 级快照。

    一个 session 内可以有多个 task；
    run 只是某个 task 的一次执行轮次。
    """

    task_id: str
    kernel_session_id: str
    title: str = ""
    goal: str = ""
    constraints: List[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.ACTIVE
    plan_id: str = ""
    current_step: str = ""
    current_step_name: str = ""
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    last_run_id: str = ""
    last_interrupted_run_id: str = ""
    resume_summary: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class GlobalTask(BaseModel):
    """Global task directory entry used by task routing."""

    task_id: str
    kernel_session_id: str
    user_session_id: str = ""
    agent_id: str = ""
    title: str = ""
    task_type: str = "other"
    task_description: str = ""
    task_brief_version: int = 0
    status: TaskStatus = TaskStatus.ACTIVE
    priority: str = "normal"
    stage: str = ""
    external_refs: Dict[str, Any] = Field(default_factory=dict)
    routing_hints: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_user_touch_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None
    last_manager_update_at: Optional[datetime] = None
    last_talker_update_at: Optional[datetime] = None
    last_thinker_update_at: Optional[datetime] = None


class TaskRouteDecision(BaseModel):
    """Task Context Router result."""

    route_id: str = ""
    user_session_id: str = ""
    runtime_session_id: str = ""
    user_message: str = ""
    routing_decision: str = "create_new"
    target_task_id: str = ""
    confidence: float = 0.0
    matched_hints: List[str] = Field(default_factory=list)
    time_reason: Dict[str, Any] = Field(default_factory=dict)
    candidate_tasks: List[Dict[str, Any]] = Field(default_factory=list)
    needs_user_clarification: bool = False
    clarification_question: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# New architecture compatibility state
# ---------------------------------------------------------------------------

class TaskBriefState(BaseModel):
    """新版 task_brief 语义，当前兼容映射自 IntentState。"""

    kernel_session_id: str
    task_id: str = ""
    task_brief_version: int = 0
    goal: str = ""
    output_format: str = ""
    constraints: List[str] = Field(default_factory=list)
    priority: str = "normal"
    cancelled: bool = False
    updated_at: datetime = Field(default_factory=utc_now)


class TaskFlowState(BaseModel):
    """新版 task_flow 语义，当前兼容映射自 PlanState。"""

    kernel_session_id: str
    flow_id: str
    task_id: str = ""
    status: PlanStatus = PlanStatus.ACTIVE
    current_step: str = ""
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    task_brief_version: int = 0
    execution_summary: List[Dict[str, Any]] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

class EvidenceType(StrEnum):
    WEB_PAGE = "web_page"
    FILE = "file"
    USER_STATEMENT = "user_statement"
    TOOL_RESULT = "tool_result"
    DATABASE_ROW = "database_row"


class Reliability(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class EvidenceItem(BaseModel):
    evidence_id: str
    task_id: str = ""
    evidence_type: EvidenceType
    source: str  # URL, file path, user input
    title: str = ""
    observed_at: datetime = Field(default_factory=utc_now)
    source_date: Optional[str] = None
    reliability: Reliability = Reliability.UNKNOWN
    extracted_facts: List[str] = Field(default_factory=list)
    raw_ref: Optional[str] = None  # Reference to raw content in host runtime
    accepted_by: str = ""  # "kernel_manager" or empty if candidate


# ---------------------------------------------------------------------------
# Belief
# ---------------------------------------------------------------------------

class BeliefStatus(StrEnum):
    UNVERIFIED = "unverified"
    LIKELY = "likely"
    VERIFIED = "verified"
    CONFLICTING = "conflicting"
    RETRACTED = "retracted"


class BeliefItem(BaseModel):
    belief_id: str
    claim: str
    status: BeliefStatus = BeliefStatus.UNVERIFIED
    confidence: float = 0.0  # 0.0 - 1.0
    supporting_evidence: List[str] = Field(default_factory=list)
    conflicting_evidence: List[str] = Field(default_factory=list)
    last_verified_at: Optional[datetime] = None
    visibility: str = "shared"


class ClaimItem(BaseModel):
    """新版 claim 语义，当前兼容映射自 BeliefItem。"""

    claim_id: str
    kernel_session_id: str
    task_id: str = ""
    claim: str = ""
    status: BeliefStatus = BeliefStatus.UNVERIFIED
    confidence: float = 0.0
    supporting_evidence: List[str] = Field(default_factory=list)
    conflicting_evidence: List[str] = Field(default_factory=list)
    visibility: str = "shared"
    last_verified_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

class ExecutionAction(BaseModel):
    action_id: str
    task_id: str = ""
    step_id: str = ""
    tool: str
    status: str = "success"  # success, failed, timeout
    input_summary: str = ""
    output_ref: Optional[str] = None  # Reference to result in host runtime
    runtime_refs: Dict[str, Optional[str]] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: Optional[datetime] = None
    retry_count: int = 0


class ThinkerDispatchStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ThinkerDispatch(BaseModel):
    """KMS 下发给 Thinker worker 的最小任务单。"""

    dispatch_id: str
    kernel_session_id: str
    task_id: str = ""
    run_id: str = ""
    task_brief_version: int = 0
    dispatch_type: str = "start"
    status: ThinkerDispatchStatus = ThinkerDispatchStatus.PENDING
    cancellation_token: bool = False
    payload: Dict[str, Any] = Field(default_factory=dict)
    claimed_by: str = ""
    claimed_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    error: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ObserverNotificationStatus(StrEnum):
    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class ObserverNotification(BaseModel):
    """Notification for Observer / Talker to refresh or report task state."""

    notification_id: str
    target: str = "observer"
    kernel_session_id: str = ""
    task_id: str = ""
    notification_type: str = "progress_update"
    urgency: str = "normal"
    reason: str = ""
    progress_ref: str = ""
    suggested_observer_context: Dict[str, Any] = Field(default_factory=dict)
    delivery_policy: Dict[str, Any] = Field(default_factory=dict)
    status: ObserverNotificationStatus = ObserverNotificationStatus.PENDING
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# Commitment
# ---------------------------------------------------------------------------

class CommitmentStatus(StrEnum):
    PENDING = "pending"
    FULFILLED = "fulfilled"
    BROKEN = "broken"
    CANCELLED = "cancelled"


class Commitment(BaseModel):
    commitment_id: str
    statement: str
    created_by: str = "talker"
    status: CommitmentStatus = CommitmentStatus.PENDING
    requires_confirmation: bool = False
    related_intent_version: int = 0
    resolved_at: Optional[datetime] = None


class TodoObligation(BaseModel):
    """新版 todo 语义，当前兼容映射自 Commitment。"""

    obligation_id: str
    kernel_session_id: str
    task_id: str = ""
    statement: str = ""
    created_by: str = "talker"
    status: CommitmentStatus = CommitmentStatus.PENDING
    requires_confirmation: bool = False
    related_task_brief_version: int = 0
    resolved_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# User-facing Progress
# ---------------------------------------------------------------------------

class ProgressState(BaseModel):
    """Talker 可以安全告诉用户的内容（§5.12）。"""
    session_id: str
    status: str = "idle"
    stage: str = ""
    summary: str = ""  # 内部字段名；API 序列化为 summary_for_talker
    safe_facts: List[str] = Field(default_factory=list)
    unsafe_claims: List[str] = Field(default_factory=list)
    needs_user_input: bool = False
    allowed_actions: List[str] = Field(default_factory=lambda: ["report_progress"])
    forbidden_actions: List[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def summary_for_talker(self) -> str:
        """§5.12 文档字段名——Talker 可转述的进度摘要。"""
        return self.summary

    def model_dump(self, **kwargs) -> dict:
        """序列化时将 summary 导出为 summary_for_talker。"""
        data = super().model_dump(**kwargs)
        data["summary_for_talker"] = data.get("summary", "")
        return data


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

class SyncCursor(BaseModel):
    sync_cursor_id: str
    kernel_session_id: str
    external_system: str = "multica"
    last_synced_event_id: str = ""
    last_synced_state_version: int = 0
    last_synced_at: Optional[datetime] = None


class SyncView(BaseModel):
    """外部协作系统的最小摘要（§5.14）。"""
    external_task_id: str
    status: str
    stage: str = ""
    summary: str = ""
    needs_user_input: bool = False
    blocking_reason: Optional[str] = None
    pending_confirmations: List[str] = Field(default_factory=list)
    final_facts: List[str] = Field(default_factory=list)


# ===========================================================================
# Runtime Reference Index（§5.13）
# ===========================================================================

class RuntimeReference(BaseModel):
    """统一管理宿主 Runtime 引用——派生状态追溯到原始执行环境。"""
    kernel_ref_id: str
    kernel_session_id: str
    runtime_session_id: str = ""
    runtime_type: str = "cli-agent"
    ref_type: str = ""   # message, tool_call, tool_result, checkpoint, process, memory
    ref_id: str = ""      # 宿主 Runtime 侧的 ID
    summary: str = ""
    visibility: str = "private"
    created_at: datetime = Field(default_factory=utc_now)
