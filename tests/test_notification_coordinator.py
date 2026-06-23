from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kms.notification.coordinator import NotificationCoordinator
from src.stores.sqlite_store import SqliteStore


async def build_store():
    store = SqliteStore(":memory:")
    await store.connect()
    return store


@pytest.mark.asyncio
async def test_notification_coordinator_creates_task_done_notification():
    store = await build_store()
    try:
        dispatch = await store.create_thinker_dispatch(
            kernel_session_id="ask_notify_done",
            task_id="task_notify_done",
            run_id="run_notify_done",
        )
        notification = await NotificationCoordinator(store).notify_dispatch_completed(
            dispatch,
            session_status="completed",
            active_run_completed=True,
        )

        assert notification is not None
        assert notification.notification_type == "task_done"
        assert notification.urgency == "normal"
        assert notification.progress_ref == "run_notify_done"
        assert notification.suggested_observer_context["dispatch_id"] == dispatch.dispatch_id
        assert notification.delivery_policy["priority"] == "normal"
        assert notification.delivery_policy["requires_user_visible_message"] is True
        assert notification.delivery_policy["interrupt_user"] is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_notification_coordinator_skips_stale_dispatch():
    store = await build_store()
    try:
        dispatch = await store.create_thinker_dispatch(
            kernel_session_id="ask_notify_stale",
            task_id="task_notify_stale",
            run_id="run_old",
        )
        notification = await NotificationCoordinator(store).notify_dispatch_completed(
            dispatch,
            session_status="completed",
            active_run_completed=False,
        )
        notifications = await store.list_observer_notifications(
            kernel_session_id="ask_notify_stale",
        )

        assert notification is None
        assert notifications == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_notification_coordinator_creates_task_failed_notification():
    store = await build_store()
    try:
        dispatch = await store.create_thinker_dispatch(
            kernel_session_id="ask_notify_failed",
            task_id="task_notify_failed",
            run_id="run_notify_failed",
        )
        notification = await NotificationCoordinator(store).notify_dispatch_failed(
            dispatch,
            error="tool crashed",
            active_run_completed=True,
        )

        assert notification is not None
        assert notification.notification_type == "task_failed"
        assert notification.urgency == "important"
        assert notification.reason == "tool crashed"
        assert notification.delivery_policy["priority"] == "high"
        assert notification.delivery_policy["requires_user_visible_message"] is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_notification_coordinator_dedupes_pending_notifications():
    store = await build_store()
    try:
        dispatch = await store.create_thinker_dispatch(
            kernel_session_id="ask_notify_dedupe",
            task_id="task_notify_dedupe",
            run_id="run_notify_dedupe",
        )
        coordinator = NotificationCoordinator(store)
        first = await coordinator.notify_dispatch_completed(
            dispatch,
            session_status="completed",
            active_run_completed=True,
        )
        second = await coordinator.notify_dispatch_completed(
            dispatch,
            session_status="completed",
            active_run_completed=True,
        )
        notifications = await store.list_observer_notifications(
            kernel_session_id="ask_notify_dedupe",
        )

        assert first.notification_id == second.notification_id
        assert len(notifications) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_notification_coordinator_throttles_progress_updates():
    store = await build_store()
    try:
        dispatch = await store.create_thinker_dispatch(
            kernel_session_id="ask_notify_throttle",
            task_id="task_notify_throttle",
            run_id="run_notify_throttle",
        )
        coordinator = NotificationCoordinator(store)
        first = await coordinator.notify_dispatch_completed(
            dispatch,
            session_status="running",
            active_run_completed=True,
        )
        await store.resolve_observer_notification(first.notification_id)
        second = await coordinator.notify_dispatch_completed(
            dispatch,
            session_status="running",
            active_run_completed=True,
        )
        notifications = await store.list_observer_notifications(
            kernel_session_id="ask_notify_throttle",
            status="",
        )

        assert first.notification_id == second.notification_id
        assert len(notifications) == 1
        assert first.delivery_policy["min_interval_seconds"] == 300
        assert first.delivery_policy["priority"] == "low"
        assert first.delivery_policy["requires_user_visible_message"] is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_high_priority_failure_is_not_deduped_by_progress_update():
    store = await build_store()
    try:
        dispatch = await store.create_thinker_dispatch(
            kernel_session_id="ask_notify_priority",
            task_id="task_notify_priority",
            run_id="run_notify_priority",
        )
        coordinator = NotificationCoordinator(store)
        progress = await coordinator.notify_dispatch_completed(
            dispatch,
            session_status="running",
            active_run_completed=True,
        )
        failed = await coordinator.notify_dispatch_failed(
            dispatch,
            error="blocked by tool failure",
            active_run_completed=True,
        )
        notifications = await store.list_observer_notifications(
            kernel_session_id="ask_notify_priority",
            status="",
        )

        assert progress.notification_type == "progress_update"
        assert failed.notification_type == "task_failed"
        assert failed.delivery_policy["priority"] == "high"
        assert len(notifications) == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_repeated_task_failures_escalate_to_interrupt_user():
    store = await build_store()
    try:
        dispatch = await store.create_thinker_dispatch(
            kernel_session_id="ask_notify_escalate",
            task_id="task_notify_escalate",
            run_id="run_notify_escalate",
        )
        coordinator = NotificationCoordinator(store)

        first = await coordinator.notify_dispatch_failed(
            dispatch,
            error="failure 1",
            active_run_completed=True,
        )
        await store.resolve_observer_notification(first.notification_id)
        second = await coordinator.notify_dispatch_failed(
            dispatch,
            error="failure 2",
            active_run_completed=True,
        )
        await store.resolve_observer_notification(second.notification_id)
        third = await coordinator.notify_dispatch_failed(
            dispatch,
            error="failure 3",
            active_run_completed=True,
        )

        assert third.urgency == "critical"
        assert third.delivery_policy["priority"] == "urgent"
        assert third.delivery_policy["interrupt_user"] is True
        assert third.delivery_policy["requires_user_visible_message"] is True
    finally:
        await store.close()
