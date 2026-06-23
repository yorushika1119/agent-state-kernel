"""Thinker dispatch creation plus task conversation-ref recording."""

from __future__ import annotations

from typing import Any, Optional


class ThinkerDispatchCoordinator:
    """Creates Thinker work items for a dispatched user message."""

    def __init__(self, lifecycle, conversation_refs):
        self.lifecycle = lifecycle
        self.conversation_refs = conversation_refs

    async def create_for_user_message(
        self,
        *,
        session: Any,
        task: Any,
        run_id: str,
        task_brief_version: int,
        dispatch_type: str,
        user_text: str,
        action: str,
        task_action: str,
        route: Any,
        user_session_id: str,
        resume_context: Optional[dict] = None,
        runtime_refs: Optional[dict] = None,
    ):
        dispatch = await self.lifecycle.create_thinker_dispatch(
            session_id=session.kernel_session_id,
            task_id=task.task_id,
            run_id=run_id,
            task_brief_version=task_brief_version,
            dispatch_type=dispatch_type,
            cancellation_token=False,
            payload={
                "user_message": user_text,
                "action": action,
                "task_action": task_action,
                "route_decision": route.routing_decision,
                "resume_context": resume_context or {},
            },
        )
        await self.conversation_refs.record(
            text_summary=user_text,
            user_session_id=user_session_id,
            kernel_session_id=session.kernel_session_id,
            task_id=task.task_id,
            run_id=run_id,
            route_id=route.route_id,
            runtime_refs=runtime_refs,
            metadata={
                "action": action,
                "task_action": task_action,
                "route_decision": route.routing_decision,
                "thinker_dispatch_id": dispatch.dispatch_id,
            },
        )
        return dispatch
