"""External sync view stage for KMS."""

from __future__ import annotations

from typing import Optional

from src.kms.pipeline_stages.summarize import summarize
from src.kms.state.aliases import commitments_from_todos, intent_from_task_brief
from src.schema.state import SyncView


async def sync(store, session_id: str, *, api_key: str = "") -> Optional[SyncView]:
    """生成外部同步视图。"""

    progress = await summarize(store, session_id, api_key=api_key)
    session = await store.get_session(session_id)
    intent = intent_from_task_brief(await store.get_task_brief(session_id))
    commitments = commitments_from_todos(await store.get_todo_obligations(session_id))
    executions = await store.get_executions(session_id)
    if not progress or not session:
        return None

    pending_confirmations = [
        commitment.statement
        for commitment in commitments
        if commitment.requires_confirmation and commitment.status.value == "pending"
    ]
    failed_tools = [
        action.tool or action.action_id
        for action in executions
        if action.status == "failed"
    ]

    blocking_reason = None
    if intent and intent.cancelled:
        blocking_reason = "session_cancelled"
    elif pending_confirmations:
        blocking_reason = "awaiting_user_confirmation"
    elif progress.needs_user_input:
        blocking_reason = "awaiting_user_input"
    elif progress.status == "blocked":
        blocking_reason = "task_blocked"
    elif failed_tools:
        blocking_reason = f"tool_failed:{failed_tools[0]}"
    elif session.last_interrupted_run_id:
        blocking_reason = "interrupted_by_new_request"

    return SyncView(
        external_task_id=session.external_task_id,
        status=progress.status,
        stage=progress.stage,
        summary=progress.summary,
        needs_user_input=progress.needs_user_input,
        blocking_reason=blocking_reason,
        pending_confirmations=pending_confirmations,
        final_facts=progress.safe_facts[:5] if progress.status == "completed" else [],
    )
