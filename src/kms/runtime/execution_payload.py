"""Runtime execution payload helpers for KMS events."""

from __future__ import annotations

from typing import Any, Dict

from src.schema.events import CognitiveEvent


def merge_execution_payload(event: CognitiveEvent) -> Dict[str, Any]:
    """把 event.runtime_refs 映射回执行态 payload，供 execution reducer 使用。"""
    payload = dict(event.payload)
    runtime_refs = payload.get("runtime_refs")
    if not isinstance(runtime_refs, dict):
        runtime_refs = {}

    if event.runtime_refs:
        for key, value in event.runtime_refs.model_dump().items():
            if value and key not in runtime_refs:
                runtime_refs[key] = value

        if not payload.get("output_ref") and event.runtime_refs.tool_result_ref:
            payload["output_ref"] = event.runtime_refs.tool_result_ref

    if runtime_refs:
        payload["runtime_refs"] = runtime_refs
    return payload
