"""Execute KMS dispatch decisions that require Thinker work."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class DispatchExecutionResult:
    session: Any
    refreshed: Any
    active_task: Any
    task_plan: Any
    run_id: str
    task_brief_version: int
    thinker_dispatch: Any = None
    kernel_response: str = ""
    reason: str = ""
    task_action: str = ""
    resume_context: dict[str, Any] = field(default_factory=dict)
    debug_timings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def requires_thinker(self) -> bool:
        return self.thinker_dispatch is not None


class DispatchExecutionCoordinator:
    """Creates/updates runs, tasks, and thinker dispatches inside KMS."""

    def __init__(
        self,
        *,
        store,
        sessions,
        lifecycle,
        task_dispatch_planner,
        task_switches,
        thinker_dispatches,
    ):
        self.store = store
        self.sessions = sessions
        self.lifecycle = lifecycle
        self.task_dispatch_planner = task_dispatch_planner
        self.task_switches = task_switches
        self.thinker_dispatches = thinker_dispatches

    async def task_brief_version_for_session(
        self,
        session: Any,
        *,
        increment: int = 0,
    ) -> int:
        if session is None:
            return increment
        task_brief = await self.store.get_task_brief(session.kernel_session_id)
        version = (
            task_brief.task_brief_version
            if task_brief and task_brief.task_brief_version
            else session.intent_version
        )
        return version + increment

    async def execute(
        self,
        *,
        text: str,
        session: Any,
        route: Any,
        route_target_task: Any,
        flags: Any,
        user_session: Any,
        agent_id: str = "",
        runtime_id: str = "",
        runtime_session_id: str = "",
        runtime_type: str = "cli-agent",
        external_source: str = "",
        external_workspace_id: str = "",
        external_issue_id: str = "",
        external_task_id: str = "",
        runtime_refs: Optional[dict] = None,
        debug_timings: bool = False,
    ) -> DispatchExecutionResult:
        total_started_at = time.perf_counter()
        timings: list[dict[str, Any]] = []

        def record_timing(step: str, started_at: float) -> None:
            if not debug_timings:
                return
            now = time.perf_counter()
            timings.append(
                {
                    "step": step,
                    "duration_s": round(now - started_at, 6),
                    "total_s": round(now - total_started_at, 6),
                }
            )

        started_at = time.perf_counter()
        session, created_session = await self.sessions.get_or_create_session(
            session,
            agent_id=agent_id,
            runtime_id=runtime_id,
            runtime_session_id=runtime_session_id,
            runtime_type=runtime_type,
            external_source=external_source,
            external_workspace_id=external_workspace_id,
            external_issue_id=external_issue_id,
            external_task_id=external_task_id,
        )
        record_timing("get_or_create_session", started_at)

        run_id = self.lifecycle.new_run_id()
        previous_run_id = session.active_run_id or ""
        started_at = time.perf_counter()
        current_version = await self.task_brief_version_for_session(session)
        record_timing("current_task_brief_version", started_at)
        started_at = time.perf_counter()
        task_plan = await self.task_dispatch_planner.plan(
            session=session,
            created_session=created_session,
            route=route,
            route_target_task=route_target_task,
            explicit_new_task_requested=flags.explicit_new_task_requested,
            wants_new_task=flags.wants_new_task,
            wants_resume=flags.wants_resume,
            wants_same_task_steer=flags.wants_same_task_steer,
            previous_run_id=previous_run_id,
            run_id=run_id,
            current_task_brief_version=current_version,
            next_task_brief_version=current_version + 1,
            user_session_id=user_session.user_session_id,
            agent_id=agent_id,
        )
        record_timing("task_dispatch_plan", started_at)

        if task_plan.no_resume_task:
            return DispatchExecutionResult(
                session=session,
                refreshed=session,
                active_task=None,
                task_plan=task_plan,
                run_id=session.active_run_id or "",
                task_brief_version=current_version,
                reason="no_paused_task_to_resume",
                task_action="respond_from_kernel",
                debug_timings=timings,
            )

        active_task = task_plan.active_task
        started_at = time.perf_counter()
        await self.lifecycle.activate_run(
            session,
            run_id=run_id,
            active_task_id=task_plan.active_task_id,
            last_paused_task_id=task_plan.last_paused_task_id,
            user_message=text,
        )
        record_timing("activate_run", started_at)

        event = None
        if task_plan.should_submit_message:
            message_payload = {"text": text}
            if task_plan.task_action == "continue_active_task":
                message_payload["constraints"] = [text]
            else:
                message_payload["goal"] = text
            started_at = time.perf_counter()
            event = await self.lifecycle.submit_user_message(
                session.kernel_session_id,
                text,
                payload=message_payload,
            )
            record_timing("submit_user_message", started_at)

        started_at = time.perf_counter()
        refreshed = await self.store.get_session(session.kernel_session_id)
        record_timing("refresh_session", started_at)
        started_at = time.perf_counter()
        refreshed_version = await self.task_brief_version_for_session(refreshed or session)
        record_timing("refreshed_task_brief_version", started_at)

        if task_plan.task_action == "continue_active_task":
            started_at = time.perf_counter()
            active_task = await self.task_switches.refresh_active_task_from_kernel_state(
                refreshed,
                run_id=run_id,
                user_session_id=user_session.user_session_id,
                agent_id=agent_id,
                task_brief_version=refreshed_version,
            )
            record_timing("refresh_active_task", started_at)
        elif task_plan.should_submit_message:
            started_at = time.perf_counter()
            active_task = await self.lifecycle.create_task_from_user_message(
                session.kernel_session_id,
                text=text,
                run_id=run_id,
                event=event,
                session_status=refreshed.status.value if refreshed else "running",
            )
            record_timing("create_task_from_user_message", started_at)
            started_at = time.perf_counter()
            refreshed = await self.store.get_session(session.kernel_session_id)
            record_timing("refresh_session_after_task", started_at)
            started_at = time.perf_counter()
            refreshed_version = await self.task_brief_version_for_session(refreshed or session)
            record_timing("refreshed_task_brief_version_after_task", started_at)
            started_at = time.perf_counter()
            await self.task_switches.sync_global_task(
                active_task,
                user_session_id=user_session.user_session_id,
                agent_id=agent_id,
                task_brief_version=refreshed_version,
            )
            record_timing("sync_global_task", started_at)

        thinker_dispatch = None
        if active_task:
            started_at = time.perf_counter()
            thinker_dispatch = await self.thinker_dispatches.create_for_user_message(
                session=session,
                task=active_task,
                run_id=run_id,
                task_brief_version=refreshed_version,
                dispatch_type=task_plan.task_action or task_plan.action,
                user_text=text,
                action=task_plan.action,
                task_action=task_plan.task_action,
                route=route,
                user_session_id=user_session.user_session_id,
                resume_context=task_plan.resume_context,
                runtime_refs=runtime_refs,
            )
            record_timing("create_thinker_dispatch", started_at)

        return DispatchExecutionResult(
            session=session,
            refreshed=refreshed,
            active_task=active_task,
            task_plan=task_plan,
            run_id=run_id,
            task_brief_version=refreshed_version,
            thinker_dispatch=thinker_dispatch,
            reason=task_plan.reason,
            task_action=task_plan.task_action,
            resume_context=task_plan.resume_context,
            debug_timings=timings,
        )
