"""KMS runtime message dispatch manager."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from src.kms.conversation_ref_coordinator import ConversationRefCoordinator
from src.kms.dispatch_decision import (
    DispatchDecision,
    kernel_response_decision,
    thinker_run_decision,
)
from src.kms.dispatch_lifecycle_coordinator import DispatchLifecycleCoordinator
from src.kms.dispatch_context import build_kernel_dispatch_context
from src.kms.intent_classifier import classify_dispatch_intent_with_llm
from src.kms.kernel_direct_reply_coordinator import KernelDirectReplyCoordinator
from src.kms.kernel_direct_responder import KernelDirectResponder
from src.kms.kernel_session_coordinator import KernelSessionCoordinator
from src.kms.route_clarification_coordinator import RouteClarificationCoordinator
from src.kms.task_coordinators import (
    InterruptCoordinator,
    ResumeCoordinator,
    TaskSwitchCoordinator,
)
from src.kms.task_dispatch_planner import TaskDispatchPlanner
from src.kms.task_routing_coordinator import TaskRoutingCoordinator
from src.kms.thinker_dispatch_coordinator import ThinkerDispatchCoordinator


class KmsManager:
    """Owns runtime-level user message dispatch and interrupt decisions."""

    def __init__(self, store, engine, *, enable_llm_router: bool | None = None):
        self.store = store
        self.engine = engine
        self.sessions = KernelSessionCoordinator(store, engine)
        self.direct_responder = KernelDirectResponder(store, engine)
        self.interrupts = InterruptCoordinator(store)
        self.resumes = ResumeCoordinator(store)
        self.task_switches = TaskSwitchCoordinator(
            store,
            self.interrupts,
            self.resumes,
        )
        self.task_dispatch_planner = TaskDispatchPlanner(
            store,
            self.task_switches,
        )
        self.lifecycle = DispatchLifecycleCoordinator(store, engine)
        self.conversation_refs = ConversationRefCoordinator(store)
        self.thinker_dispatches = ThinkerDispatchCoordinator(
            self.lifecycle,
            self.conversation_refs,
        )
        self.route_clarifications = RouteClarificationCoordinator(store)
        self.direct_replies = KernelDirectReplyCoordinator(
            self.direct_responder,
            self.conversation_refs,
        )
        self.enable_llm_router = (
            os.getenv("KMS_ENABLE_LLM_ROUTER") == "1"
            if enable_llm_router is None
            else enable_llm_router
        )
        self.task_router = TaskRoutingCoordinator(
            store,
            enable_llm=self.enable_llm_router,
        )

    async def _task_brief_version_for_session(
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

        session = await self.sessions.find_target_session(
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
            clarification_response = self.route_clarifications.build_response(route)
            await self.route_clarifications.record_exchange(
                user_text=text,
                response_text=clarification_response,
                user_session_id=user_session.user_session_id,
                kernel_session_id=session.kernel_session_id if session else "",
                route=route,
                runtime_refs=runtime_refs,
            )
            return kernel_response_decision(
                kernel_session_id=session.kernel_session_id if session else "",
                intent_version=await self._task_brief_version_for_session(session),
                run_id=session.active_run_id if session else "",
                session_status=session.status.value if session else "unknown",
                reason="task_route_needs_clarification",
                task_action="ask_clarification",
                task_id=session.active_task_id if session else "",
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
            kernel_response = await self.direct_replies.build_and_record(
                session=session,
                user_text=text,
                user_session_id=user_session.user_session_id,
                route=route,
                kind=intent.kernel_answer_kind or "progress",
                target_task_id=response_task_id,
                runtime_refs=runtime_refs,
            )
            return kernel_response_decision(
                kernel_session_id=session.kernel_session_id,
                intent_version=await self._task_brief_version_for_session(session),
                run_id=session.active_run_id or "",
                session_status=session.status.value,
                reason=intent.reason or "kernel_direct_status_reply",
                task_action="respond_from_kernel",
                task_id=response_task_id,
                kernel_response=kernel_response,
                user_session_id=user_session.user_session_id,
                route_decision=route.routing_decision,
            )

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

        run_id = self.lifecycle.new_run_id()
        previous_run_id = session.active_run_id or ""
        event = None
        current_task_brief_version = await self._task_brief_version_for_session(session)
        next_task_brief_version = current_task_brief_version + 1

        task_plan = await self.task_dispatch_planner.plan(
            session=session,
            created_session=created_session,
            route=route,
            route_target_task=route_target_task,
            explicit_new_task_requested=explicit_new_task_requested,
            wants_new_task=wants_new_task,
            wants_resume=wants_resume,
            wants_same_task_steer=wants_same_task_steer,
            previous_run_id=previous_run_id,
            run_id=run_id,
            current_task_brief_version=current_task_brief_version,
            next_task_brief_version=next_task_brief_version,
            user_session_id=user_session.user_session_id,
            agent_id=agent_id,
        )
        if task_plan.no_resume_task:
            response_text = "当前没有可继续的已挂起任务。"
            await self.direct_replies.record_static_reply(
                session=session,
                user_text=text,
                response_text=response_text,
                user_session_id=user_session.user_session_id,
                route=route,
                task_id=session.active_task_id or "",
                runtime_refs=runtime_refs,
                metadata={
                    "reason": "no_paused_task_to_resume",
                },
            )
            return kernel_response_decision(
                kernel_session_id=session.kernel_session_id,
                intent_version=current_task_brief_version,
                run_id=session.active_run_id or "",
                session_status=session.status.value,
                reason="no_paused_task_to_resume",
                task_action="respond_from_kernel",
                task_id=session.active_task_id or "",
                kernel_response=response_text,
                user_session_id=user_session.user_session_id,
                route_decision=route.routing_decision,
            )

        active_task = task_plan.active_task
        action = task_plan.action
        task_action = task_plan.task_action
        active_task_id = task_plan.active_task_id
        last_paused_task_id = task_plan.last_paused_task_id
        should_submit_message = task_plan.should_submit_message
        resume_context: Dict[str, Any] = task_plan.resume_context
        reason = task_plan.reason

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
        refreshed_task_brief_version = await self._task_brief_version_for_session(
            refreshed or session,
        )

        if task_action == "continue_active_task":
            active_task = await self.task_switches.refresh_active_task_from_kernel_state(
                refreshed,
                run_id=run_id,
                user_session_id=user_session.user_session_id,
                agent_id=agent_id,
                task_brief_version=refreshed_task_brief_version,
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
            refreshed_task_brief_version = await self._task_brief_version_for_session(
                refreshed or session,
            )
            await self.task_switches.sync_global_task(
                active_task,
                user_session_id=user_session.user_session_id,
                agent_id=agent_id,
                task_brief_version=refreshed_task_brief_version,
            )

        thinker_dispatch = None
        if active_task:
            thinker_dispatch = await self.thinker_dispatches.create_for_user_message(
                session=session,
                task=active_task,
                run_id=run_id,
                task_brief_version=refreshed_task_brief_version,
                dispatch_type=task_action or action,
                user_text=text,
                action=action,
                task_action=task_action,
                route=route,
                user_session_id=user_session.user_session_id,
                resume_context=resume_context,
                runtime_refs=runtime_refs,
            )

        return thinker_run_decision(
            action=action,
            kernel_session_id=session.kernel_session_id,
            intent_version=refreshed_task_brief_version,
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
