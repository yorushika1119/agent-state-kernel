from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api import server as api_server
from src.kernel.engine import KernelEngine
from src.kms.manager import KmsManager
from src.schema.events import EventType
from src.stores.sqlite_store import SqliteStore


async def build_runtime():
    store = SqliteStore(":memory:")
    await store.connect()
    engine = KernelEngine(store)
    manager = KmsManager(store, engine)
    return store, engine, manager


@pytest.mark.asyncio
async def test_approval_request_updates_observer_view_and_projection():
    store, engine, _manager = await build_runtime()
    try:
        session = await engine.create_session(agent_id="agent-approval")
        session_id = session.kernel_session_id

        await engine.append_kernel_event(
            session_id,
            EventType.APPROVAL_REQUESTED,
            payload={
                "approval_request_id": "apr_observer_view",
                "task_id": "task_approval_view",
                "requested_action": "send_external_message",
                "action_summary": "Send the draft to the external recipient",
                "risk_summary": "External side effect",
                "task_brief_version": 4,
            },
        )

        approvals = await store.list_approval_requests(kernel_session_id=session_id)
        assert len(approvals) == 1
        assert approvals[0].status.value == "pending"

        observer_view = await engine.get_observer_view(session_id)
        assert observer_view["needs_user_input"] is True
        assert observer_view["blocking_reason"] == "awaiting_approval"
        assert observer_view["pending_approvals"][0]["approval_request_id"] == "apr_observer_view"

        persisted = await store.get_observer_task_view(session_id)
        assert persisted is not None
        assert persisted.approval_request_ids == ["apr_observer_view"]
        assert persisted.needs_user_input is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_approval_api_grant_appends_decision_event():
    store, engine, manager = await build_runtime()
    previous = (api_server._store, api_server._engine, api_server._kms_manager)
    api_server._store = store
    api_server._engine = engine
    api_server._kms_manager = manager
    try:
        session = await engine.create_session(agent_id="agent-approval-api")
        session_id = session.kernel_session_id
        await engine.append_kernel_event(
            session_id,
            EventType.APPROVAL_REQUESTED,
            payload={
                "approval_request_id": "apr_api_grant",
                "task_id": "task_api_grant",
                "requested_action": "send_external_message",
                "action_summary": "Send the approved status update",
            },
        )

        transport = httpx.ASGITransport(app=api_server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            listed = await client.get("/kms/tasks/task_api_grant/approvals")
            assert listed.status_code == 200
            assert listed.json()[0]["approval_request_id"] == "apr_api_grant"

            granted = await client.post(
                "/kms/approvals/apr_api_grant/grant",
                json={"decided_by": "user_123", "comment": "Approved"},
            )
            assert granted.status_code == 200
            assert granted.json()["status"] == "granted"
            assert granted.json()["decided_by"] == "user_123"

            fetched = await client.get("/kms/approvals/apr_api_grant")
            assert fetched.status_code == 200
            assert fetched.json()["status"] == "granted"

        events = await store.get_events(session_id, limit=20)
        assert any(event["event_type"] == EventType.APPROVAL_GRANTED.value for event in events)
    finally:
        api_server._store = previous[0]
        api_server._engine = previous[1]
        api_server._kms_manager = previous[2]
        await store.close()
