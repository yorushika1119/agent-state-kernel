"""Prepare routing, target session, and intent for KMS dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.kms.context.dispatch_context import build_kernel_dispatch_context
from src.kms.intent_classifier import classify_dispatch_intent_with_llm


@dataclass
class DispatchIntentFlags:
    wants_new_task: bool
    explicit_new_task_requested: bool
    wants_resume: bool
    wants_kernel_response: bool
    wants_same_task_steer: bool
    route_clarification_applies: bool


@dataclass
class DispatchPreparation:
    routing: Any
    user_session: Any
    route: Any
    route_target_task: Any
    session: Any
    dispatch_context: Any
    intent: Any
    flags: DispatchIntentFlags


class DispatchPreparationCoordinator:
    """Builds the read-only facts needed before KMS changes task state."""

    def __init__(self, task_router, sessions):
        self.task_router = task_router
        self.sessions = sessions

    async def prepare(
        self,
        *,
        text: str,
        runtime_session_id: str = "",
        runtime_id: str = "",
        runtime_type: str = "cli-agent",
        agent_id: str = "",
        target_session_id: str = "",
        user_session_id: str = "",
        mode: str = "auto",
    ) -> DispatchPreparation:
        routing = await self.task_router.route_message(
            text,
            user_session_id=user_session_id,
            runtime_session_id=runtime_session_id,
            runtime_id=runtime_id,
            runtime_type=runtime_type,
            agent_id=agent_id,
        )
        session = await self.sessions.find_target_session(
            target_session_id=target_session_id or routing.routed_session_id,
            runtime_session_id=runtime_session_id,
        )
        dispatch_context = await build_kernel_dispatch_context(
            self.sessions.store,
            session,
        )
        intent = await classify_dispatch_intent_with_llm(
            text,
            mode=mode,
            session=session,
            context=dispatch_context,
        )
        flags = self._build_flags(intent, routing.route)
        return DispatchPreparation(
            routing=routing,
            user_session=routing.user_session,
            route=routing.route,
            route_target_task=routing.route_target_task,
            session=session,
            dispatch_context=dispatch_context,
            intent=intent,
            flags=flags,
        )

    @staticmethod
    def _build_flags(intent: Any, route: Any) -> DispatchIntentFlags:
        wants_new_task = intent.intent == "new_task"
        explicit_new_task_requested = (
            wants_new_task
            and intent.source in {"explicit", "rule"}
            and intent.reason in {"explicit_new_task_mode", "explicit_new_task_marker"}
        )
        wants_resume = intent.intent == "resume_previous_task"
        wants_kernel_response = intent.intent == "kernel_answerable_query"
        wants_same_task_steer = intent.intent == "same_task_steer"
        return DispatchIntentFlags(
            wants_new_task=wants_new_task,
            explicit_new_task_requested=explicit_new_task_requested,
            wants_resume=wants_resume,
            wants_kernel_response=wants_kernel_response,
            wants_same_task_steer=wants_same_task_steer,
            route_clarification_applies=(
                route.needs_user_clarification
                and not wants_new_task
                and not wants_resume
            ),
        )
