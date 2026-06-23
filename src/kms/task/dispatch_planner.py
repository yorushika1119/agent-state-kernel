"""Task switch planning for KMS dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from src.schema.state import TaskSnapshot


@dataclass
class TaskDispatchPlan:
    action: str
    task_action: str
    reason: str
    active_task_id: str
    last_paused_task_id: str
    should_submit_message: bool = True
    active_task: Optional[TaskSnapshot] = None
    resume_context: dict[str, Any] = field(default_factory=dict)
    no_resume_task: bool = False


class TaskDispatchPlanner:
    """Plans active/paused task transitions inside the KMS layer."""

    def __init__(self, store, task_switches):
        self.store = store
        self.task_switches = task_switches

    async def plan(
        self,
        *,
        session,
        created_session: bool,
        route,
        route_target_task,
        explicit_new_task_requested: bool,
        wants_new_task: bool,
        wants_resume: bool,
        wants_same_task_steer: bool,
        previous_run_id: str,
        run_id: str,
        current_task_brief_version: int,
        next_task_brief_version: int,
        user_session_id: str,
        agent_id: str,
    ) -> TaskDispatchPlan:
        reason = "" if created_session else "reuse_existing_session"
        action = "start_new_task" if created_session else "interrupt_and_replan"
        task_action = "start_new_task" if created_session else "continue_active_task"
        active_task_id = session.active_task_id or ""
        last_paused_task_id = session.last_paused_task_id or ""

        plan = TaskDispatchPlan(
            action=action,
            task_action=task_action,
            reason=reason,
            active_task_id=active_task_id,
            last_paused_task_id=last_paused_task_id,
        )

        use_routed_task = (
            route.routing_decision == "select_existing"
            and route_target_task is not None
            and not explicit_new_task_requested
            and not wants_new_task
            and not wants_resume
        )
        routed_task = None
        if use_routed_task:
            routed_task = await self.store.get_task(
                route_target_task.kernel_session_id,
                route_target_task.task_id,
            )

        if (
            use_routed_task
            and routed_task is not None
            and session.active_task_id == routed_task.task_id
        ):
            plan.active_task = routed_task
            plan.task_action = "continue_active_task"
            plan.reason = plan.reason or "route_selected_active_task"
            return plan

        if (
            use_routed_task
            and routed_task is not None
            and session.active_task_id != routed_task.task_id
        ):
            if session.active_task_id:
                plan.last_paused_task_id = await self.task_switches.pause_current_task(
                    session,
                    interrupted_run_id=previous_run_id,
                    user_session_id=user_session_id,
                    agent_id=agent_id,
                    task_brief_version=current_task_brief_version,
                )
            else:
                plan.last_paused_task_id = ""
            plan.active_task, plan.resume_context = await self.task_switches.activate_existing_task(
                session,
                routed_task,
                run_id=run_id,
                user_session_id=user_session_id,
                agent_id=agent_id,
                task_brief_version=next_task_brief_version,
            )
            plan.active_task_id = routed_task.task_id
            plan.action = "interrupt_and_replan" if previous_run_id else "start_new_task"
            plan.task_action = "continue_routed_task"
            plan.reason = "route_selected_existing_task"
            plan.should_submit_message = False
            return plan

        if session.active_task_id and wants_new_task:
            plan.last_paused_task_id = await self.task_switches.pause_current_task(
                session,
                interrupted_run_id=previous_run_id,
                user_session_id=user_session_id,
                agent_id=agent_id,
                task_brief_version=current_task_brief_version,
                fallback_last_paused_task_id=plan.last_paused_task_id,
            )
            plan.active_task_id = ""
            plan.action = (
                "start_new_task"
                if explicit_new_task_requested or not previous_run_id
                else "interrupt_and_replan"
            )
            plan.task_action = "start_new_task"
            plan.reason = (
                "explicit_new_task_requested"
                if explicit_new_task_requested
                else "reuse_existing_session"
            )
            return plan

        if wants_resume:
            resume_task = await self.task_switches.get_resume_task(session)
            if resume_task is None:
                plan.no_resume_task = True
                return plan
            if session.active_task_id and session.active_task_id != resume_task.task_id:
                plan.last_paused_task_id = await self.task_switches.pause_current_task(
                    session,
                    interrupted_run_id=previous_run_id,
                    user_session_id=user_session_id,
                    agent_id=agent_id,
                    task_brief_version=current_task_brief_version,
                )
            else:
                plan.last_paused_task_id = ""
            plan.active_task, plan.resume_context = await self.task_switches.activate_existing_task(
                session,
                resume_task,
                run_id=run_id,
                user_session_id=user_session_id,
                agent_id=agent_id,
                task_brief_version=next_task_brief_version,
            )
            plan.active_task_id = resume_task.task_id
            plan.action = "interrupt_and_replan" if previous_run_id else "start_new_task"
            plan.task_action = "continue_paused_task"
            plan.reason = "resume_previous_task"
            plan.should_submit_message = False
            return plan

        if previous_run_id and not wants_same_task_steer:
            plan.last_paused_task_id = await self.task_switches.pause_current_task(
                session,
                interrupted_run_id=previous_run_id,
                user_session_id=user_session_id,
                agent_id=agent_id,
                task_brief_version=current_task_brief_version,
                fallback_last_paused_task_id=plan.last_paused_task_id,
            )
            plan.active_task_id = ""
            plan.task_action = "start_new_task"

        return plan
