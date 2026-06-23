"""KMS runtime message dispatch manager."""

from __future__ import annotations

import os
from typing import Optional

from src.kms.conversation_ref_coordinator import ConversationRefCoordinator
from src.kms.dispatch.decision import (
    DispatchDecision,
    thinker_run_decision,
)
from src.kms.dispatch.execution import DispatchExecutionCoordinator
from src.kms.dispatch.lifecycle import DispatchLifecycleCoordinator
from src.kms.dispatch.preparation import DispatchPreparationCoordinator
from src.kms.dispatch.response import DispatchResponseCoordinator
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
from src.kms.dispatch.thinker_dispatch import ThinkerDispatchCoordinator


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
        self.dispatch_responses = DispatchResponseCoordinator(
            store=store,
            route_clarifications=self.route_clarifications,
            direct_replies=self.direct_replies,
        )
        self.dispatch_execution = DispatchExecutionCoordinator(
            store=store,
            sessions=self.sessions,
            lifecycle=self.lifecycle,
            task_dispatch_planner=self.task_dispatch_planner,
            task_switches=self.task_switches,
            thinker_dispatches=self.thinker_dispatches,
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
        self.dispatch_preparation = DispatchPreparationCoordinator(
            self.task_router,
            self.sessions,
        )

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
        prepared = await self.dispatch_preparation.prepare(
            text=text,
            runtime_session_id=runtime_session_id,
            runtime_id=runtime_id,
            runtime_type=runtime_type,
            agent_id=agent_id,
            target_session_id=target_session_id,
            user_session_id=user_session_id,
            mode=mode,
        )
        user_session = prepared.user_session
        route = prepared.route
        route_target_task = prepared.route_target_task
        session = prepared.session
        intent = prepared.intent
        flags = prepared.flags

        if flags.route_clarification_applies:
            return await self.dispatch_responses.clarification(
                session=session,
                user_text=text,
                user_session_id=user_session.user_session_id,
                route=route,
                runtime_refs=runtime_refs,
            )

        if session and flags.wants_kernel_response:
            response_task_id = (
                route.target_task_id
                if route.routing_decision == "select_existing" and route.target_task_id
                else session.active_task_id or ""
            )
            return await self.dispatch_responses.kernel_direct_reply(
                session=session,
                user_text=text,
                user_session_id=user_session.user_session_id,
                route=route,
                reason=intent.reason or "kernel_direct_status_reply",
                kind=intent.kernel_answer_kind or "progress",
                target_task_id=response_task_id,
                runtime_refs=runtime_refs,
            )

        execution = await self.dispatch_execution.execute(
            text=text,
            session=session,
            route=route,
            route_target_task=route_target_task,
            flags=flags,
            user_session=user_session,
            agent_id=agent_id,
            runtime_id=runtime_id,
            runtime_session_id=runtime_session_id,
            runtime_type=runtime_type,
            external_source=external_source,
            external_workspace_id=external_workspace_id,
            external_issue_id=external_issue_id,
            external_task_id=external_task_id,
            runtime_refs=runtime_refs,
        )

        if execution.task_plan.no_resume_task:
            return await self.dispatch_responses.no_resume_task(
                session=execution.session,
                user_text=text,
                user_session_id=user_session.user_session_id,
                route=route,
                task_brief_version=execution.task_brief_version,
                runtime_refs=runtime_refs,
            )

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
            task_id=active_task.task_id if active_task else (refreshed.active_task_id if refreshed else ""),
            resume_context=execution.resume_context,
            user_session_id=user_session.user_session_id,
            route_decision=route.routing_decision,
            thinker_dispatch_id=(
                execution.thinker_dispatch.dispatch_id
                if execution.thinker_dispatch
                else ""
            ),
        )
