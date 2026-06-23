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


@pytest.mark.asyncio
async def test_runtime_event_adapter_submits_hermes_tool_events():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read().decode())
        requests.append((request.url.path, payload))
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://kernel.test",
    )
    async with RuntimeEventAdapter("http://kernel.test", client=client) as adapter:
        adapter.session_id = "ask_adapter"
        adapter.run_id = "run_adapter"
        adapter.intent_version = 4
        await adapter.submit_tool_started(
            action_id="act_tool",
            step_id="step_1",
            tool="shell",
            input_summary="run command",
            runtime_refs={"tool_call_id": "call_1"},
        )
        await adapter.submit_tool_completed(
            action_id="act_tool",
            step_id="step_1",
            output_summary="command finished",
            output_ref="tool-result-1",
            runtime_refs={"tool_result_ref": "tool-result-1"},
        )
        await adapter.submit_tool_failed(
            action_id="act_fail",
            step_id="step_2",
            tool="shell",
            error="command failed",
            runtime_refs={"tool_call_id": "call_2"},
        )
        await adapter.submit_action_blocked(
            action_id="act_blocked",
            step_id="step_3",
            tool="shell",
            reason="interrupted by new user request",
            runtime_refs={"tool_call_id": "call_3"},
        )

    assert [item[1]["request_type"] for item in requests] == [
        "ToolStarted",
        "ToolCompleted",
        "ToolFailed",
        "ActionBlocked",
    ]
    assert requests[0] == (
        "/kms/request",
        {
            "session_id": "ask_adapter",
            "component": "thinker",
            "request_type": "ToolStarted",
            "payload": {
                "action_id": "act_tool",
                "step_id": "step_1",
                "tool": "shell",
                "input_summary": "run command",
                "runtime_refs": {"tool_call_id": "call_1"},
            },
            "run_id": "run_adapter",
            "intent_version": 4,
            "runtime_refs": {"tool_call_id": "call_1"},
        },
    )
    assert requests[1][1]["payload"]["output_ref"] == "tool-result-1"
    assert requests[2][1]["payload"]["error"] == "command failed"
    assert requests[3][1]["payload"]["reason"] == "interrupted by new user request"


@pytest.mark.asyncio
async def test_runtime_event_adapter_submits_hermes_summary_events():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read().decode())
        requests.append(payload)
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://kernel.test",
    )
    async with RuntimeEventAdapter("http://kernel.test", client=client) as adapter:
        adapter.session_id = "ask_adapter"
        adapter.run_id = "run_adapter"
        await adapter.submit_reasoning_summary(
            "checked available context",
            runtime_refs={"message_id": "reasoning-1"},
        )
        await adapter.submit_raw_result(
            "raw-result-1",
            result_summary="final raw result available",
            runtime_refs={"message_id": "raw-1"},
        )

    assert requests[0]["request_type"] == "ReasoningSummary"
    assert requests[0]["payload"] == {
        "summary": "checked available context",
        "runtime_refs": {"message_id": "reasoning-1"},
    }
    assert requests[1]["request_type"] == "RawResultAvailable"
    assert requests[1]["payload"]["result_ref"] == "raw-result-1"
