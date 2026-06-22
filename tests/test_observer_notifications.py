from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api import server as api_server
from src.kernel.engine import KernelEngine
from src.kms.manager import KmsManager
from src.stores.sqlite_store import SqliteStore


async def build_runtime():
    store = SqliteStore(":memory:")
    await store.connect()
    engine = KernelEngine(store)
    manager = KmsManager(store, engine)
    return store, engine, manager


@pytest.mark.asyncio
async def test_observer_and_talker_notification_api_ack_and_resolve():
    store, engine, manager = await build_runtime()
    previous_store = api_server._store
    previous_engine = api_server._engine
    previous_kms_manager = api_server._kms_manager
    api_server._store = store
    api_server._engine = engine
    api_server._kms_manager = manager

    try:
        session = await engine.create_session(agent_id="agent-notify")
        observer = await store.create_observer_notification(
            target="observer",
            kernel_session_id=session.kernel_session_id,
            task_id="task_observer",
            notification_type="progress_update",
            reason="progress changed",
            suggested_observer_context={"one_line_summary": "progress changed"},
        )
        talker = await store.create_observer_notification(
            target="talker",
            kernel_session_id=session.kernel_session_id,
            task_id="task_talker",
            notification_type="needs_user_input",
            urgency="important",
            reason="ask user",
        )

        transport = httpx.ASGITransport(app=api_server.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://kernel.test",
        ) as client:
            observer_list = await client.get(
                "/kms/observer/notifications",
                params={"kernel_session_id": session.kernel_session_id},
            )
            talker_list = await client.get(
                "/kms/talker/notifications",
                params={"kernel_session_id": session.kernel_session_id},
            )
            acked = await client.post(
                f"/kms/observer/notifications/{observer.notification_id}/ack"
            )
            resolved = await client.post(
                f"/kms/observer/notifications/{observer.notification_id}/resolve"
            )

        assert observer_list.status_code == 200
        assert [item["notification_id"] for item in observer_list.json()] == [
            observer.notification_id
        ]
        assert talker_list.status_code == 200
        assert [item["notification_id"] for item in talker_list.json()] == [
            talker.notification_id
        ]
        assert acked.status_code == 200
        assert acked.json()["status"] == "acknowledged"
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "resolved"
    finally:
        api_server._store = previous_store
        api_server._engine = previous_engine
        api_server._kms_manager = previous_kms_manager
        await store.close()


@pytest.mark.asyncio
async def test_dispatch_complete_creates_observer_notification():
    store, engine, manager = await build_runtime()
    previous_store = api_server._store
    previous_engine = api_server._engine
    previous_kms_manager = api_server._kms_manager
    api_server._store = store
    api_server._engine = engine
    api_server._kms_manager = manager

    try:
        transport = httpx.ASGITransport(app=api_server.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://kernel.test",
        ) as client:
            dispatch_response = await client.post(
                "/kms/dispatch-user-message",
                json={
                    "text": "research observer notification",
                    "runtime_session_id": "rt-notification-complete",
                    "runtime_type": "gateway",
                    "agent_id": "agent-notify",
                },
            )
            dispatch_data = dispatch_response.json()
            dispatch_id = dispatch_data["thinker_dispatch_id"]

            await client.post(
                "/kms/thinker/dispatches/claim",
                json={
                    "dispatch_id": dispatch_id,
                    "thinker_id": "worker-notify",
                },
            )
            completed = await client.post(
                f"/kms/thinker/dispatches/{dispatch_id}/complete",
                json={"session_status": "completed"},
            )
            notifications = await client.get(
                "/kms/observer/notifications",
                params={"kernel_session_id": dispatch_data["kernel_session_id"]},
            )

        assert completed.status_code == 200
        items = notifications.json()
        assert len(items) == 1
        assert items[0]["notification_type"] == "task_done"
        assert items[0]["progress_ref"] == dispatch_data["run_id"]
        assert items[0]["suggested_observer_context"]["dispatch_id"] == dispatch_id
    finally:
        api_server._store = previous_store
        api_server._engine = previous_engine
        api_server._kms_manager = previous_kms_manager
        await store.close()


@pytest.mark.asyncio
async def test_stale_dispatch_fail_does_not_create_observer_notification():
    store, engine, manager = await build_runtime()
    previous_store = api_server._store
    previous_engine = api_server._engine
    previous_kms_manager = api_server._kms_manager
    api_server._store = store
    api_server._engine = engine
    api_server._kms_manager = manager

    try:
        session = await engine.create_session(agent_id="agent-stale-notify")
        task = await store.create_task(
            session.kernel_session_id,
            title="stale notification",
            goal="stale notification",
        )
        await store.update_session_status(
            session.kernel_session_id,
            "running",
            active_task_id=task.task_id,
            active_run_id="run-new",
        )
        dispatch = await store.create_thinker_dispatch(
            kernel_session_id=session.kernel_session_id,
            task_id=task.task_id,
            run_id="run-old",
        )

        transport = httpx.ASGITransport(app=api_server.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://kernel.test",
        ) as client:
            failed = await client.post(
                f"/kms/thinker/dispatches/{dispatch.dispatch_id}/fail",
                json={"error": "stale", "session_status": "running"},
            )
            notifications = await client.get(
                "/kms/observer/notifications",
                params={"kernel_session_id": session.kernel_session_id},
            )

        assert failed.status_code == 200
        assert failed.json()["status"] == "failed"
        assert notifications.status_code == 200
        assert notifications.json() == []
    finally:
        api_server._store = previous_store
        api_server._engine = previous_engine
        api_server._kms_manager = previous_kms_manager
        await store.close()


@pytest.mark.asyncio
async def test_notification_stream_returns_pending_sse_events():
    store, engine, manager = await build_runtime()
    previous_store = api_server._store
    previous_engine = api_server._engine
    previous_kms_manager = api_server._kms_manager
    api_server._store = store
    api_server._engine = engine
    api_server._kms_manager = manager

    try:
        session = await engine.create_session(agent_id="agent-notify-stream")
        notification = await store.create_observer_notification(
            target="observer",
            kernel_session_id=session.kernel_session_id,
            task_id="task_stream",
            notification_type="progress_update",
            reason="stream me",
        )

        transport = httpx.ASGITransport(app=api_server.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://kernel.test",
        ) as client:
            response = await client.get(
                "/kms/observer/notifications/stream",
                params={"kernel_session_id": session.kernel_session_id},
            )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert f"id: {notification.notification_id}" in response.text
        assert "event: progress_update" in response.text
        assert notification.notification_id in response.text
    finally:
        api_server._store = previous_store
        api_server._engine = previous_engine
        api_server._kms_manager = previous_kms_manager
        await store.close()


@pytest.mark.asyncio
async def test_notification_stream_filters_target_task_and_pending_status():
    store, engine, manager = await build_runtime()
    previous_store = api_server._store
    previous_engine = api_server._engine
    previous_kms_manager = api_server._kms_manager
    api_server._store = store
    api_server._engine = engine
    api_server._kms_manager = manager

    try:
        session = await engine.create_session(agent_id="agent-notify-filter")
        expected = await store.create_observer_notification(
            target="observer",
            kernel_session_id=session.kernel_session_id,
            task_id="task_keep",
            notification_type="progress_update",
            reason="keep me",
        )
        other_task = await store.create_observer_notification(
            target="observer",
            kernel_session_id=session.kernel_session_id,
            task_id="task_other",
            notification_type="progress_update",
            reason="skip other task",
        )
        acked = await store.create_observer_notification(
            target="observer",
            kernel_session_id=session.kernel_session_id,
            task_id="task_keep",
            notification_type="task_done",
            reason="skip acked",
        )
        await store.ack_observer_notification(acked.notification_id)

        transport = httpx.ASGITransport(app=api_server.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://kernel.test",
        ) as client:
            response = await client.get(
                "/kms/observer/notifications/stream",
                params={
                    "kernel_session_id": session.kernel_session_id,
                    "task_id": "task_keep",
                },
            )

        assert response.status_code == 200
        assert expected.notification_id in response.text
        assert other_task.notification_id not in response.text
        assert acked.notification_id not in response.text
    finally:
        api_server._store = previous_store
        api_server._engine = previous_engine
        api_server._kms_manager = previous_kms_manager
        await store.close()


@pytest.mark.asyncio
async def test_talker_notification_stream_only_returns_talker_events():
    store, engine, manager = await build_runtime()
    previous_store = api_server._store
    previous_engine = api_server._engine
    previous_kms_manager = api_server._kms_manager
    api_server._store = store
    api_server._engine = engine
    api_server._kms_manager = manager

    try:
        session = await engine.create_session(agent_id="agent-notify-talker-stream")
        observer = await store.create_observer_notification(
            target="observer",
            kernel_session_id=session.kernel_session_id,
            task_id="task_stream",
            notification_type="progress_update",
            reason="observer only",
        )
        talker = await store.create_observer_notification(
            target="talker",
            kernel_session_id=session.kernel_session_id,
            task_id="task_stream",
            notification_type="needs_user_input",
            reason="talker only",
        )

        transport = httpx.ASGITransport(app=api_server.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://kernel.test",
        ) as client:
            response = await client.get(
                "/kms/talker/notifications/stream",
                params={"kernel_session_id": session.kernel_session_id},
            )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert f"id: {talker.notification_id}" in response.text
        assert "event: needs_user_input" in response.text
        assert observer.notification_id not in response.text
    finally:
        api_server._store = previous_store
        api_server._engine = previous_engine
        api_server._kms_manager = previous_kms_manager
        await store.close()
