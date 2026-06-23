"""Build KMS dispatch responses that do not require Thinker execution."""

from __future__ import annotations

from typing import Any, Optional

from src.kms.dispatch.decision import DispatchDecision, kernel_response_decision


NO_RESUME_TASK_RESPONSE = "当前没有可继续的已挂起任务。"


class DispatchResponseCoordinator:
    """Centralizes KMS direct response recording and decision wrapping."""

    def __init__(self, *, store, route_clarifications, direct_replies):
        self.store = store
        self.route_clarifications = route_clarifications
        self.direct_replies = direct_replies

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

    async def clarification(
        self,
        *,
        session: Any,
        user_text: str,
        user_session_id: str,
        route: Any,
        runtime_refs: Optional[dict] = None,
    ) -> DispatchDecision:
        response = self.route_clarifications.build_response(route)
        await self.route_clarifications.record_exchange(
            user_text=user_text,
            response_text=response,
            user_session_id=user_session_id,
            kernel_session_id=session.kernel_session_id if session else "",
            route=route,
            runtime_refs=runtime_refs,
        )
        return kernel_response_decision(
            kernel_session_id=session.kernel_session_id if session else "",
            intent_version=await self.task_brief_version_for_session(session),
            run_id=session.active_run_id if session else "",
            session_status=session.status.value if session else "unknown",
            reason="task_route_needs_clarification",
            task_action="ask_clarification",
            task_id=session.active_task_id if session else "",
            kernel_response=response,
            user_session_id=user_session_id,
            route_decision=route.routing_decision,
        )

    async def kernel_direct_reply(
        self,
        *,
        session: Any,
        user_text: str,
        user_session_id: str,
        route: Any,
        reason: str,
        kind: str,
        target_task_id: str = "",
        runtime_refs: Optional[dict] = None,
    ) -> DispatchDecision:
        response = await self.direct_replies.build_and_record(
            session=session,
            user_text=user_text,
            user_session_id=user_session_id,
            route=route,
            kind=kind,
            target_task_id=target_task_id,
            runtime_refs=runtime_refs,
        )
        return kernel_response_decision(
            kernel_session_id=session.kernel_session_id,
            intent_version=await self.task_brief_version_for_session(session),
            run_id=session.active_run_id or "",
            session_status=session.status.value,
            reason=reason or "kernel_direct_status_reply",
            task_action="respond_from_kernel",
            task_id=target_task_id,
            kernel_response=response,
            user_session_id=user_session_id,
            route_decision=route.routing_decision,
        )

    async def no_resume_task(
        self,
        *,
        session: Any,
        user_text: str,
        user_session_id: str,
        route: Any,
        task_brief_version: int,
        runtime_refs: Optional[dict] = None,
    ) -> DispatchDecision:
        await self.direct_replies.record_static_reply(
            session=session,
            user_text=user_text,
            response_text=NO_RESUME_TASK_RESPONSE,
            user_session_id=user_session_id,
            route=route,
            task_id=session.active_task_id or "",
            runtime_refs=runtime_refs,
            metadata={"reason": "no_paused_task_to_resume"},
        )
        return kernel_response_decision(
            kernel_session_id=session.kernel_session_id,
            intent_version=task_brief_version,
            run_id=session.active_run_id or "",
            session_status=session.status.value,
            reason="no_paused_task_to_resume",
            task_action="respond_from_kernel",
            task_id=session.active_task_id or "",
            kernel_response=NO_RESUME_TASK_RESPONSE,
            user_session_id=user_session_id,
            route_decision=route.routing_decision,
        )
