"""Task-local conversation reference coordinator."""

from __future__ import annotations

from typing import Any, Optional

from src.schema.state import TaskConversationRef


def message_ref_id_from_runtime_refs(runtime_refs: Optional[dict]) -> str:
    runtime_refs = runtime_refs or {}
    return (
        runtime_refs.get("message_id")
        or runtime_refs.get("message_ref_id")
        or runtime_refs.get("ref_id")
        or ""
    )


class ConversationRefCoordinator:
    """Records task-local conversation snippets and runtime message refs."""

    def __init__(self, store):
        self.store = store

    async def record(
        self,
        *,
        text_summary: str = "",
        user_session_id: str = "",
        kernel_session_id: str = "",
        task_id: str = "",
        run_id: str = "",
        role: str = "user",
        source: str = "runtime_message",
        message_ref_id: str = "",
        route_id: str = "",
        runtime_refs: Optional[dict] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[TaskConversationRef]:
        resolved_message_ref_id = message_ref_id or message_ref_id_from_runtime_refs(runtime_refs)
        if not text_summary.strip() and not resolved_message_ref_id:
            return None
        return await self.store.create_task_conversation_ref(
            user_session_id=user_session_id,
            kernel_session_id=kernel_session_id,
            task_id=task_id,
            run_id=run_id,
            role=role,
            source=source,
            message_ref_id=resolved_message_ref_id,
            text_summary=text_summary.strip(),
            route_id=route_id,
            metadata=metadata or {},
        )

    async def record_dispatch_completion(
        self,
        dispatch,
        *,
        role: str = "assistant",
        source: str = "thinker_dispatch_complete",
        text_summary: str = "",
        runtime_refs: Optional[dict] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[TaskConversationRef]:
        global_task = await self.store.get_global_task(dispatch.task_id)
        return await self.record(
            user_session_id=global_task.user_session_id if global_task else "",
            kernel_session_id=dispatch.kernel_session_id,
            task_id=dispatch.task_id,
            run_id=dispatch.run_id,
            role=role,
            source=source,
            text_summary=text_summary,
            runtime_refs=runtime_refs,
            metadata=metadata,
        )
