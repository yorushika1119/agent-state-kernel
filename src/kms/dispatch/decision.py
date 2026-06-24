"""Dispatch decision model and builders."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class DispatchDecision:
    action: str
    kernel_session_id: str
    intent_version: int
    run_id: str
    session_status: str
    reason: str = ""
    task_action: str = ""
    task_id: str = ""
    requires_thinker: bool = True
    kernel_response: str = ""
    resume_context: Dict[str, Any] = field(default_factory=dict)
    user_session_id: str = ""
    route_decision: str = ""
    thinker_dispatch_id: str = ""
    debug_timings: list[dict[str, Any]] = field(default_factory=list)


def kernel_response_decision(
    *,
    kernel_session_id: str,
    intent_version: int,
    run_id: str = "",
    session_status: str = "unknown",
    reason: str,
    task_action: str,
    task_id: str = "",
    kernel_response: str,
    user_session_id: str,
    route_decision: str,
) -> DispatchDecision:
    return DispatchDecision(
        action="respond_from_kernel",
        kernel_session_id=kernel_session_id,
        intent_version=intent_version,
        run_id=run_id,
        session_status=session_status,
        reason=reason,
        task_action=task_action,
        task_id=task_id,
        requires_thinker=False,
        kernel_response=kernel_response,
        user_session_id=user_session_id,
        route_decision=route_decision,
    )


def thinker_run_decision(
    *,
    action: str,
    kernel_session_id: str,
    intent_version: int,
    run_id: str,
    session_status: str,
    reason: str,
    task_action: str,
    task_id: str,
    resume_context: Dict[str, Any],
    user_session_id: str,
    route_decision: str,
    thinker_dispatch_id: str,
) -> DispatchDecision:
    return DispatchDecision(
        action=action,
        kernel_session_id=kernel_session_id,
        intent_version=intent_version,
        run_id=run_id,
        session_status=session_status,
        reason=reason,
        task_action=task_action,
        task_id=task_id,
        resume_context=resume_context,
        user_session_id=user_session_id,
        route_decision=route_decision,
        thinker_dispatch_id=thinker_dispatch_id,
    )


def thinker_run_decision_from_execution(
    *,
    execution: Any,
    user_session_id: str,
    route_decision: str,
) -> DispatchDecision:
    refreshed = execution.refreshed or execution.session
    active_task = execution.active_task
    task_plan = execution.task_plan
    return thinker_run_decision(
        action=task_plan.action,
        kernel_session_id=execution.session.kernel_session_id,
        intent_version=execution.task_brief_version,
        run_id=execution.run_id,
        session_status=refreshed.status.value if refreshed else "running",
        reason=execution.reason,
        task_action=execution.task_action,
        task_id=(
            active_task.task_id
            if active_task
            else (refreshed.active_task_id if refreshed else "")
        ),
        resume_context=execution.resume_context,
        user_session_id=user_session_id,
        route_decision=route_decision,
        thinker_dispatch_id=(
            execution.thinker_dispatch.dispatch_id
            if execution.thinker_dispatch
            else ""
        ),
    )
