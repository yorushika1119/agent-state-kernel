"""Route clarification response and conversation-ref recording."""

from __future__ import annotations

from typing import Any, Optional

from src.kms.conversation_ref_coordinator import ConversationRefCoordinator


class RouteClarificationCoordinator:
    """Handles task-router clarification replies for KMS dispatch."""

    def __init__(self, store):
        self.conversation_refs = ConversationRefCoordinator(store)

    def build_response(self, route: Any) -> str:
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

    async def record_exchange(
        self,
        *,
        user_text: str,
        response_text: str,
        user_session_id: str,
        kernel_session_id: str = "",
        route: Any,
        runtime_refs: Optional[dict] = None,
    ) -> None:
        metadata = {
            "route_decision": route.routing_decision,
            "task_action": "ask_clarification",
        }
        await self.conversation_refs.record(
            text_summary=user_text,
            user_session_id=user_session_id,
            kernel_session_id=kernel_session_id,
            route_id=route.route_id,
            runtime_refs=runtime_refs,
            metadata=metadata,
        )
        await self.conversation_refs.record(
            text_summary=response_text,
            user_session_id=user_session_id,
            kernel_session_id=kernel_session_id,
            role="assistant",
            source="kernel_route_clarification",
            route_id=route.route_id,
            metadata=metadata,
        )
