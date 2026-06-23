"""KMS runtime message dispatch manager."""

from __future__ import annotations

from typing import Optional

from src.kms.dispatch.decision import (
    DispatchDecision,
    thinker_run_decision_from_execution,
)
from src.kms.manager_components import build_kms_manager_components


class KmsManager:
    """Owns runtime-level user message dispatch and interrupt decisions."""

    def __init__(self, store, engine, *, enable_llm_router: bool | None = None):
        self.store = store
        self.engine = engine
        components = build_kms_manager_components(
            store,
            engine,
            enable_llm_router=enable_llm_router,
        )
        self.sessions = components.sessions
        self.direct_responder = components.direct_responder
        self.interrupts = components.interrupts
        self.resumes = components.resumes
        self.task_switches = components.task_switches
        self.task_dispatch_planner = components.task_dispatch_planner
        self.lifecycle = components.lifecycle
        self.conversation_refs = components.conversation_refs
        self.thinker_dispatches = components.thinker_dispatches
        self.route_clarifications = components.route_clarifications
        self.direct_replies = components.direct_replies
        self.dispatch_responses = components.dispatch_responses
        self.dispatch_execution = components.dispatch_execution
        self.enable_llm_router = components.enable_llm_router
        self.task_router = components.task_router
        self.dispatch_preparation = components.dispatch_preparation

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

        return thinker_run_decision_from_execution(
            execution=execution,
            user_session_id=user_session.user_session_id,
            route_decision=route.routing_decision,
        )
