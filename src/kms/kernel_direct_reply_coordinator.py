"""Kernel-direct reply generation and conversation-ref recording."""

from __future__ import annotations

from typing import Any, Optional


class KernelDirectReplyCoordinator:
    """Handles replies that KMS can answer without waking Thinker."""

    def __init__(self, direct_responder, conversation_refs):
        self.direct_responder = direct_responder
        self.conversation_refs = conversation_refs

    async def build_and_record(
        self,
        *,
        session: Any,
        user_text: str,
        user_session_id: str,
        route: Any,
        kind: str,
        target_task_id: str = "",
        runtime_refs: Optional[dict] = None,
    ) -> str:
        response_text = await self.direct_responder.build_response(
            session.kernel_session_id,
            kind,
            target_task_id=target_task_id,
        )
        await self.record_static_reply(
            session=session,
            user_text=user_text,
            response_text=response_text,
            user_session_id=user_session_id,
            route=route,
            task_id=target_task_id,
            runtime_refs=runtime_refs,
            metadata={"kernel_answer_kind": kind},
        )
        return response_text

    async def record_static_reply(
        self,
        *,
        session: Any,
        user_text: str,
        response_text: str,
        user_session_id: str,
        route: Any,
        task_id: str = "",
        runtime_refs: Optional[dict] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        merged_metadata = {
            "route_decision": route.routing_decision,
            "task_action": "respond_from_kernel",
        }
        merged_metadata.update(metadata or {})
        await self.conversation_refs.record(
            text_summary=user_text,
            user_session_id=user_session_id,
            kernel_session_id=session.kernel_session_id,
            task_id=task_id,
            run_id=session.active_run_id or "",
            route_id=route.route_id,
            runtime_refs=runtime_refs,
            metadata=merged_metadata,
        )
        await self.conversation_refs.record(
            text_summary=response_text,
            user_session_id=user_session_id,
            kernel_session_id=session.kernel_session_id,
            task_id=task_id,
            run_id=session.active_run_id or "",
            role="assistant",
            source="kernel_direct_response",
            route_id=route.route_id,
            metadata=merged_metadata,
        )
