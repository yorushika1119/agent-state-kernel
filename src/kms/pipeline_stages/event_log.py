"""EventLog stage helpers for KMS cognitive events."""

from __future__ import annotations

import uuid

from src.schema.events import Actor, CognitiveEvent


async def assign_event_metadata(
    store,
    session_id: str,
    event: CognitiveEvent,
    *,
    force_next_intent_version: bool = False,
) -> CognitiveEvent:
    """为即将入日志的事件分配唯一 ID、状态版本和 intent 版本。"""
    latest = await store.get_latest_state_version(session_id)
    session = await store.get_session(session_id)
    task_brief = await store.get_task_brief(session_id)
    current_intent_version = (
        task_brief.task_brief_version
        if task_brief and task_brief.task_brief_version
        else session.intent_version if session else 0
    )

    if not event.event_id:
        event.event_id = f"evt_{uuid.uuid4().hex[:12]}"
    event.state_version = latest + 1
    if not event.runtime_session_id and session:
        event.runtime_session_id = session.runtime_session_id
    if not event.run_id and session and event.actor == Actor.THINKER:
        event.run_id = session.active_run_id or ""

    if force_next_intent_version:
        event.intent_version = current_intent_version + 1
    elif event.intent_version <= 0:
        event.intent_version = current_intent_version

    return event
