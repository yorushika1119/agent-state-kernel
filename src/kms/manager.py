"""KMS runtime message dispatch manager."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from src.kms.conversation_ref_coordinator import ConversationRefCoordinator
from src.kms.dispatch_lifecycle_coordinator import DispatchLifecycleCoordinator
from src.kms.dispatch_context import build_kernel_dispatch_context
from src.kms.intent_classifier import classify_dispatch_intent_with_llm
from src.kms.kernel_direct_responder import KernelDirectResponder
from src.kms.task_coordinators import InterruptCoordinator, ResumeCoordinator
from src.kms.task_routing_coordinator import TaskRoutingCoordinator
from src.schema.state import TaskSnapshot, TaskStatus


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


class KmsManager:
    """Owns runtime-level user message dispatch and interrupt decisions."""

    def __init__(self, store, engine, *, enable_llm_router: bool | None = None):
        self.store = store
        self.engine = engine
        self.direct_responder = KernelDirectResponder(store, engine)
        self.interrupts = InterruptCoordinator(store)
        self.resumes = ResumeCoordinator(store)
        self.lifecycle = DispatchLifecycleCoordinator(store, engine)
        self.conversation_refs = ConversationRefCoordinator(store)
        self.enable_llm_router = (
            os.getenv("KMS_ENABLE_LLM_ROUTER") == "1"
            if enable_llm_router is None
            else enable_llm_router
        )
        self.task_router = TaskRoutingCoordinator(
            store,
            enable_llm=self.enable_llm_router,
        )

    async def _find_target_session(
        self,
        *,
        target_session_id: str,
        runtime_session_id: str,
    ):
        session = None
        if target_session_id:
            session = await self.store.get_session(target_session_id)
        elif runtime_session_id:
            sessions = await self.store.list_sessions_by_runtime_session(runtime_session_id, limit=1)
            session = sessions[0] if sessions else None
        return session

    async def _ensure_active_task_for_session(
        self,
        session,
        *,
        run_id: str,
    ) -> Optional[TaskSnapshot]:
        return await self.interrupts.ensure_active_task_for_session(session, run_id=run_id)

    async def _snapshot_current_task(
        self,
        session,
        *,
        interrupted_run_id: str = "",
    ) -> Optional[TaskSnapshot]:
        return await self.interrupts.snapshot_current_task(
            session,
            interrupted_run_id=interrupted_run_id,
        )

    def _build_resume_context(self, task: Optional[TaskSnapshot]) -> Dict[str, Any]:
        return self.resumes.build_resume_context(task)

    async def _restore_task_into_session(self, session_id: str, task: TaskSnapshot) -> None:
        await self.resumes.restore_task_into_session(session_id, task)

    async def _sync_global_task(
        self,
        task: Optional[TaskSnapshot],
        *,
        user_session_id: str = "",
        agent_id: str = "",
        task_brief_version: int = 0,
        active: bool = True,
    ) -> None:
        if task is None:
            return
        await self.store.upsert_global_task_from_snapshot(
            task,
            user_session_id=user_session_id,
            agent_id=agent_id,
            task_brief_version=task_brief_version,
        )
        if user_session_id:
            await self.store.link_task_to_user_session(
                user_session_id,
                task.task_id,
                active=active,
            )

    async def _build_kernel_direct_response(
        self,
        session_id: str,
        kind: str = "progress",
        *,
        target_task_id: str = "",
    ) -> str:
        return await self.direct_responder.build_response(
            session_id,
            kind,
            target_task_id=target_task_id,
        )

    def _build_route_clarification_response(self, route) -> str:
        question = route.clarification_question or "你指的是哪一个任务？"
        if not route.candidate_tasks:
            return question
        parts = []
        for index, task in enumerate(route.candidate_tasks, start=1):
            title = (
                task.get("title")
                or task.get("task_description")
                or task.get("task_id")
                or "未命名任务"
            )
            status = task.get("status") or "unknown"
            parts.append(f"{index}. {title}（{status}）")
        return question + "\n" + "\n".join(parts)

    async def dispatch_user_message(
        self,
        *,
        text: str,
        runtime_session_id: str = "",
        runtime_id: str = "",
        runtime_type: str = "cli-agent",
        agent_id: str = "",
        external_source: str = "",
        external_workspace_id: str = "",
        external_issue_id: str = "",
        external_task_id: str = "",
        target_session_id: str = "",
        user_session_id: str = "",
        mode: str = "auto",
        runtime_refs: Optional[dict] = None,
    ) -> DispatchDecision:
        routing = await self.task_router.route_message(
            text,
            user_session_id=user_session_id,
            runtime_session_id=runtime_session_id,
            runtime_id=runtime_id,
            runtime_type=runtime_type,
            agent_id=agent_id,
        )
        user_session = routing.user_session
        route = routing.route
        route_target_task = routing.route_target_task

        session = await self._find_target_session(
            target_session_id=target_session_id or routing.routed_session_id,
            runtime_session_id=runtime_session_id,
        )
        dispatch_context = await build_kernel_dispatch_context(self.store, session)
        intent = await classify_dispatch_intent_with_llm(
            text,
            mode=mode,
            session=session,
            context=dispatch_context,
        )
        wants_new_task = intent.intent == "new_task"
        explicit_new_task_requested = (
            wants_new_task
            and intent.source in {"explicit", "rule"}
            and intent.reason in {"explicit_new_task_mode", "explicit_new_task_marker"}
        )
        wants_resume = intent.intent == "resume_previous_task"
        wants_kernel_response = intent.intent == "kernel_answerable_query"
        wants_same_task_steer = intent.intent == "same_task_steer"
        route_clarification_applies = (
            route.needs_user_clarification
            and not wants_new_task
            and not wants_resume
        )

        if route_clarification_applies:
            clarification_response = self._build_route_clarification_response(route)
            await self.conversation_refs.record(
                text_summary=text,
                user_session_id=user_session.user_session_id,
                kernel_session_id=session.kernel_session_id if session else "",
                route_id=route.route_id,
                runtime_refs=runtime_refs,
                metadata={
                    "route_decision": route.routing_decision,
                    "task_action": "ask_clarification",
                },
            )
            await self.conversation_refs.record(
                text_summary=clarification_response,
                user_session_id=user_session.user_session_id,
                kernel_session_id=session.kernel_session_id if session else "",
                role="assistant",
                source="kernel_route_clarification",
                route_id=route.route_id,
                metadata={
                    "route_decision": route.routing_decision,
                    "task_action": "ask_clarification",
                },
            )
            return DispatchDecision(
                action="respond_from_kernel",
                kernel_session_id=session.kernel_session_id if session else "",
                intent_version=session.intent_version if session else 0,
                run_id=session.active_run_id if session else "",
                session_status=session.status.value if session else "unknown",
                reason="task_route_needs_clarification",
                task_action="ask_clarification",
                task_id=session.active_task_id if session else "",
                requires_thinker=False,
                kernel_response=clarification_response,
                user_session_id=user_session.user_session_id,
                route_decision=route.routing_decision,
            )

        if session and wants_kernel_response:
            response_task_id = (
                route.target_task_id
                if route.routing_decision == "select_existing" and route.target_task_id
                else session.active_task_id or ""
            )
            kernel_response = await self._build_kernel_direct_response(
                session.kernel_session_id,
                intent.kernel_answer_kind or "progress",
                target_task_id=response_task_id,
            )
            await self.conversation_refs.record(
                text_summary=text,
                user_session_id=user_session.user_session_id,
                kernel_session_id=session.kernel_session_id,
                task_id=response_task_id,
                run_id=session.active_run_id or "",
                route_id=route.route_id,
                runtime_refs=runtime_refs,
                metadata={
                    "route_decision": route.routing_decision,
                    "task_action": "respond_from_kernel",
                    "kernel_answer_kind": intent.kernel_answer_kind or "progress",
                },
            )
            await self.conversation_refs.record(
                text_summary=kernel_response,
                user_session_id=user_session.user_session_id,
                kernel_session_id=session.kernel_session_id,
                task_id=response_task_id,
                run_id=session.active_run_id or "",
                role="assistant",
                source="kernel_direct_response",
                route_id=route.route_id,
                metadata={
                    "route_decision": route.routing_decision,
                    "task_action": "respond_from_kernel",
                    "kernel_answer_kind": intent.kernel_answer_kind or "progress",
                },
            )
            return DispatchDecision(
                action="respond_from_kernel",
                kernel_session_id=session.kernel_session_id,
                intent_version=session.intent_version,
                run_id=session.active_run_id or "",
                session_status=session.status.value,
                reason=intent.reason or "kernel_direct_status_reply",
                task_action="respond_from_kernel",
                task_id=response_task_id,
                requires_thinker=False,
                kernel_response=kernel_response,
                user_session_id=user_session.user_session_id,
                route_decision=route.routing_decision,
            )

        created_session = False
        if session is None:
            created_session = True
            session = await self.engine.create_session(
                agent_id=agent_id,
                runtime_id=runtime_id,
                runtime_session_id=runtime_session_id,
                runtime_type=runtime_type,
                external_source=external_source,
                external_workspace_id=external_workspace_id,
                external_issue_id=external_issue_id,
                external_task_id=external_task_id,
            )

        run_id = self.lifecycle.new_run_id()
        previous_run_id = session.active_run_id or ""
        active_task = None
        event = None
        reason = "" if created_session else "reuse_existing_session"
        action = "start_new_task" if created_session else "interrupt_and_replan"
        task_action = "start_new_task" if created_session else "continue_active_task"
        active_task_id = session.active_task_id or ""
        last_paused_task_id = session.last_paused_task_id or ""
        should_submit_message = True
        resume_context: Dict[str, Any] = {}
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
            active_task = routed_task
            task_action = "continue_active_task"
            reason = reason or "route_selected_active_task"
            wants_same_task_steer = True
        elif (
            use_routed_task
            and routed_task is not None
            and session.active_task_id != routed_task.task_id
        ):
            if session.active_task_id:
                paused_task = await self._snapshot_current_task(
                    session,
                    interrupted_run_id=previous_run_id,
                )
                await self._sync_global_task(
                    paused_task,
                    user_session_id=user_session.user_session_id,
                    agent_id=agent_id,
                    task_brief_version=session.intent_version,
                    active=False,
                )
                last_paused_task_id = paused_task.task_id if paused_task else ""
            else:
                last_paused_task_id = ""
            await self._restore_task_into_session(session.kernel_session_id, routed_task)
            routed_task.status = TaskStatus.ACTIVE
            routed_task.last_run_id = run_id
            await self.store.save_task(routed_task)
            await self._sync_global_task(
                routed_task,
                user_session_id=user_session.user_session_id,
                agent_id=agent_id,
                task_brief_version=session.intent_version + 1,
            )
            active_task_id = routed_task.task_id
            action = "interrupt_and_replan" if previous_run_id else "start_new_task"
            task_action = "continue_routed_task"
            reason = "route_selected_existing_task"
            should_submit_message = False
            resume_context = self._build_resume_context(routed_task)
            active_task = routed_task
        elif session.active_task_id and wants_new_task:
            paused_task = await self._snapshot_current_task(session, interrupted_run_id=previous_run_id)
            await self._sync_global_task(
                paused_task,
                user_session_id=user_session.user_session_id,
                agent_id=agent_id,
                task_brief_version=session.intent_version,
                active=False,
            )
            last_paused_task_id = paused_task.task_id if paused_task else last_paused_task_id
            active_task_id = ""
            action = (
                "start_new_task"
                if explicit_new_task_requested or not previous_run_id
                else "interrupt_and_replan"
            )
            task_action = "start_new_task"
            reason = (
                "explicit_new_task_requested"
                if explicit_new_task_requested
                else "reuse_existing_session"
            )
        elif wants_resume:
            resume_task = await self.store.get_task(session.kernel_session_id, session.last_paused_task_id or "")
            if resume_task is None:
                resume_task = await self.store.get_latest_paused_task(session.kernel_session_id)
            if resume_task is None:
                response_text = "当前没有可继续的已挂起任务。"
                await self.conversation_refs.record(
                    text_summary=text,
                    user_session_id=user_session.user_session_id,
                    kernel_session_id=session.kernel_session_id,
                    task_id=session.active_task_id or "",
                    run_id=session.active_run_id or "",
                    route_id=route.route_id,
                    runtime_refs=runtime_refs,
                    metadata={
                        "route_decision": route.routing_decision,
                        "task_action": "respond_from_kernel",
                        "reason": "no_paused_task_to_resume",
                    },
                )
                await self.conversation_refs.record(
                    text_summary=response_text,
                    user_session_id=user_session.user_session_id,
                    kernel_session_id=session.kernel_session_id,
                    task_id=session.active_task_id or "",
                    run_id=session.active_run_id or "",
                    role="assistant",
                    source="kernel_direct_response",
                    route_id=route.route_id,
                    metadata={
                        "route_decision": route.routing_decision,
                        "task_action": "respond_from_kernel",
                        "reason": "no_paused_task_to_resume",
                    },
                )
                return DispatchDecision(
                    action="respond_from_kernel",
                    kernel_session_id=session.kernel_session_id,
                    intent_version=session.intent_version,
                    run_id=session.active_run_id or "",
                    session_status=session.status.value,
                    reason="no_paused_task_to_resume",
                    task_action="respond_from_kernel",
                    task_id=session.active_task_id or "",
                    requires_thinker=False,
                    kernel_response=response_text,
                    user_session_id=user_session.user_session_id,
                    route_decision=route.routing_decision,
                )
            if session.active_task_id and session.active_task_id != resume_task.task_id:
                paused_task = await self._snapshot_current_task(session, interrupted_run_id=previous_run_id)
                await self._sync_global_task(
                    paused_task,
                    user_session_id=user_session.user_session_id,
                    agent_id=agent_id,
                    task_brief_version=session.intent_version,
                    active=False,
                )
                last_paused_task_id = paused_task.task_id if paused_task else ""
            else:
                last_paused_task_id = ""
            await self._restore_task_into_session(session.kernel_session_id, resume_task)
            resume_task.status = TaskStatus.ACTIVE
            resume_task.last_run_id = run_id
            await self.store.save_task(resume_task)
            await self._sync_global_task(
                resume_task,
                user_session_id=user_session.user_session_id,
                agent_id=agent_id,
                task_brief_version=session.intent_version + 1,
            )
            active_task_id = resume_task.task_id
            action = "interrupt_and_replan" if previous_run_id else "start_new_task"
            task_action = "continue_paused_task"
            reason = "resume_previous_task"
            should_submit_message = False
            resume_context = self._build_resume_context(resume_task)
            active_task = resume_task
        elif previous_run_id and not wants_same_task_steer:
            paused_task = await self._snapshot_current_task(session, interrupted_run_id=previous_run_id)
            await self._sync_global_task(
                paused_task,
                user_session_id=user_session.user_session_id,
                agent_id=agent_id,
                task_brief_version=session.intent_version,
                active=False,
            )
            last_paused_task_id = paused_task.task_id if paused_task else last_paused_task_id
            active_task_id = ""
            task_action = "start_new_task"

        await self.lifecycle.activate_run(
            session,
            run_id=run_id,
            active_task_id=active_task_id,
            last_paused_task_id=last_paused_task_id,
            user_message=text,
        )

        if should_submit_message:
            event = await self.lifecycle.submit_user_message(session.kernel_session_id, text)

        refreshed = await self.store.get_session(session.kernel_session_id)

        if task_action == "continue_active_task":
            active_task = await self._ensure_active_task_for_session(refreshed, run_id=run_id)
            if active_task:
                intent = await self.store.get_intent(session.kernel_session_id)
                plan = await self.store.get_plan(session.kernel_session_id)
                progress = await self.store.get_progress(session.kernel_session_id)
                active_task.goal = intent.goal if intent else active_task.goal
                active_task.constraints = intent.constraints if intent else active_task.constraints
                active_task.plan_id = plan.plan_id if plan else active_task.plan_id
                active_task.current_step = plan.current_step if plan else active_task.current_step
                active_task.current_step_name = ""
                if plan and plan.current_step:
                    current = next((step for step in plan.steps if step.step_id == plan.current_step), None)
                    if current:
                        active_task.current_step_name = current.name
                    active_task.steps = [step.model_dump() for step in plan.steps]
                active_task.last_run_id = run_id
                if progress and progress.summary:
                    active_task.resume_summary = progress.summary
                await self.store.save_task(active_task)
                await self._sync_global_task(
                    active_task,
                    user_session_id=user_session.user_session_id,
                    agent_id=agent_id,
                    task_brief_version=refreshed.intent_version if refreshed else session.intent_version,
                )
        elif should_submit_message:
            active_task = await self.lifecycle.create_task_from_user_message(
                session.kernel_session_id,
                text=text,
                run_id=run_id,
                event=event,
                session_status=refreshed.status.value if refreshed else "running",
            )
            refreshed = await self.store.get_session(session.kernel_session_id)
            await self._sync_global_task(
                active_task,
                user_session_id=user_session.user_session_id,
                agent_id=agent_id,
                task_brief_version=refreshed.intent_version if refreshed else session.intent_version,
            )

        thinker_dispatch = None
        if active_task:
            thinker_dispatch = await self.lifecycle.create_thinker_dispatch(
                session_id=session.kernel_session_id,
                task_id=active_task.task_id,
                run_id=run_id,
                task_brief_version=refreshed.intent_version if refreshed else session.intent_version,
                dispatch_type=task_action or action,
                cancellation_token=False,
                payload={
                    "user_message": text,
                    "action": action,
                    "task_action": task_action,
                    "route_decision": route.routing_decision,
                    "resume_context": resume_context,
                },
            )
            await self.conversation_refs.record(
                text_summary=text,
                user_session_id=user_session.user_session_id,
                kernel_session_id=session.kernel_session_id,
                task_id=active_task.task_id,
                run_id=run_id,
                route_id=route.route_id,
                runtime_refs=runtime_refs,
                metadata={
                    "action": action,
                    "task_action": task_action,
                    "route_decision": route.routing_decision,
                    "thinker_dispatch_id": thinker_dispatch.dispatch_id
                    if thinker_dispatch
                    else "",
                },
            )

        return DispatchDecision(
            action=action,
            kernel_session_id=session.kernel_session_id,
            intent_version=event.intent_version if event else refreshed.intent_version,
            run_id=run_id,
            session_status=refreshed.status.value if refreshed else "running",
            reason=reason,
            task_action=task_action,
            task_id=active_task.task_id if active_task else (refreshed.active_task_id if refreshed else ""),
            resume_context=resume_context,
            user_session_id=user_session.user_session_id,
            route_decision=route.routing_decision,
            thinker_dispatch_id=thinker_dispatch.dispatch_id if thinker_dispatch else "",
        )
