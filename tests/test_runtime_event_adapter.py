from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.adapters.runtime_event_adapter import RuntimeEventAdapter


@pytest.mark.asyncio
async def test_runtime_event_adapter_dispatch_and_complete_payloads():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        payload = json.loads(body.decode()) if body else {}
        requests.append((request.url.path, payload))
        if request.url.path == "/kms/dispatch-user-message":
            return httpx.Response(
                200,
                json={
                    "kernel_session_id": "ask_adapter",
                    "run_id": "run_adapter",
                    "intent_version": 3,
                    "thinker_dispatch_id": "td_adapter",
                },
            )
        if request.url.path == "/kms/thinker/dispatches/td_adapter/complete":
            return httpx.Response(200, json={"status": "completed"})
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://kernel.test",
    )
    async with RuntimeEventAdapter(
        "http://kernel.test",
        runtime_type="test-runtime",
        client=client,
    ) as adapter:
        await adapter.dispatch_user_message(
            "hello",
            runtime_session_id="rt_adapter",
            runtime_refs={"message_id": "msg_user"},
        )
        await adapter.complete_thinker_dispatch(
            response_summary="done",
            runtime_refs={"message_id": "msg_assistant"},
        )

    assert requests[0] == (
        "/kms/dispatch-user-message",
        {
            "text": "hello",
            "runtime_session_id": "rt_adapter",
            "runtime_id": "",
            "runtime_type": "test-runtime",
            "agent_id": "",
            "user_session_id": "",
            "target_session_id": "",
            "mode": "auto",
            "runtime_refs": {"message_id": "msg_user"},
        },
    )
    assert requests[1] == (
        "/kms/thinker/dispatches/td_adapter/complete",
        {
            "session_status": "completed",
            "response_summary": "done",
            "runtime_refs": {"message_id": "msg_assistant"},
        },
    )


@pytest.mark.asyncio
async def test_runtime_event_adapter_records_external_conversation_ref():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read().decode())
        requests.append((request.url.path, payload))
        return httpx.Response(200, json={"conversation_ref_id": "tmsg_adapter"})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://kernel.test",
    )
    async with RuntimeEventAdapter("http://kernel.test", client=client) as adapter:
        adapter.session_id = "ask_adapter"
        adapter.run_id = "run_adapter"
        result = await adapter.record_conversation_ref(
            task_id="task_adapter",
            role="talker",
            source="external_talker_reply",
            message_ref_id="msg_talker",
            text_summary="reported progress",
        )

    assert result["conversation_ref_id"] == "tmsg_adapter"
    assert requests[0] == (
        "/kms/conversation-refs",
        {
            "user_session_id": "",
            "kernel_session_id": "ask_adapter",
            "task_id": "task_adapter",
            "run_id": "run_adapter",
            "role": "talker",
            "source": "external_talker_reply",
            "message_ref_id": "msg_talker",
            "text_summary": "reported progress",
            "runtime_refs": {},
            "metadata": {},
        },
    )
