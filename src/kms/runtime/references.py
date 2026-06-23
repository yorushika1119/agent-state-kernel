"""Runtime reference indexing helpers for KMS events."""

from __future__ import annotations

import uuid
from typing import Dict

from src.schema.events import CognitiveEvent
from src.schema.state import RuntimeReference


def _runtime_ref_summary(event: CognitiveEvent, ref_type: str, ref_id: str) -> str:
    """为 runtime reference 生成简短摘要。"""
    if ref_type == "message":
        return event.payload.get("goal") or event.payload.get("text") or event.event_type.value
    if ref_type == "tool_call":
        tool = event.payload.get("tool", "")
        return f"{tool or 'tool'} call {ref_id}"
    if ref_type == "tool_result":
        title = event.payload.get("title") or event.payload.get("output_summary") or event.payload.get("input_summary")
        return title or f"tool result {ref_id}"
    if ref_type == "checkpoint":
        return event.payload.get("reason", "") or f"checkpoint {ref_id}"
    if ref_type == "process":
        tool = event.payload.get("tool", "")
        return f"{tool or 'process'} {ref_id}"
    return f"{event.event_type.value} {ref_id}"


def _extract_runtime_ref_values(event: CognitiveEvent) -> Dict[str, str]:
    """从 event.runtime_refs 和 payload 中统一抽取 runtime 引用值。"""
    values: Dict[str, str] = {}

    if event.runtime_refs:
        for key, value in event.runtime_refs.model_dump().items():
            if value:
                values[key] = value

    payload_runtime_refs = event.payload.get("runtime_refs")
    if isinstance(payload_runtime_refs, dict):
        for key, value in payload_runtime_refs.items():
            if value and key not in values:
                values[key] = value

    for field_name, alias in (
        ("raw_ref", "tool_result_ref"),
        ("output_ref", "tool_result_ref"),
        ("ref", "tool_result_ref"),
    ):
        value = event.payload.get(field_name)
        if value and alias not in values:
            values[alias] = value

    return values


async def register_runtime_references(store, session_id: str, event: CognitiveEvent) -> None:
    """把事件相关的 runtime 引用写入 runtime_refs 索引表。"""
    values = _extract_runtime_ref_values(event)
    if not values:
        return

    session = await store.get_session(session_id)
    runtime_session_id = (
        event.runtime_session_id
        or (session.runtime_session_id if session else "")
    )
    runtime_type = session.runtime_type if session else "cli-agent"
    visibility = event.visibility.value if hasattr(event.visibility, "value") else str(event.visibility)

    ref_type_map = {
        "message_id": "message",
        "tool_call_id": "tool_call",
        "tool_result_ref": "tool_result",
        "checkpoint_ref": "checkpoint",
        "process_session_id": "process",
    }

    for field_name, ref_id in values.items():
        ref_type = ref_type_map.get(field_name)
        if not ref_type or not ref_id:
            continue

        stable_key = f"{session_id}:{ref_type}:{ref_id}"
        runtime_ref = RuntimeReference(
            kernel_ref_id=f"rref_{uuid.uuid5(uuid.NAMESPACE_URL, stable_key).hex[:16]}",
            kernel_session_id=session_id,
            runtime_session_id=runtime_session_id,
            runtime_type=runtime_type,
            ref_type=ref_type,
            ref_id=str(ref_id),
            summary=_runtime_ref_summary(event, ref_type, str(ref_id))[:200],
            visibility=visibility,
        )
        await store.save_runtime_ref(runtime_ref)
