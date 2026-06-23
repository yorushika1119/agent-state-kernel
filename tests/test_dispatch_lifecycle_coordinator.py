from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kernel.engine import KernelEngine
from src.kms.dispatch.lifecycle import DispatchLifecycleCoordinator
from src.stores.sqlite_store import SqliteStore


async def build_runtime():
    store = SqliteStore(":memory:")
    await store.connect()
    engine = KernelEngine(store)
    lifecycle = DispatchLifecycleCoordinator(store, engine)
    return store, engine, lifecycle


@pytest.mark.asyncio
async def test_dispatch_lifecycle_activates_interrupts_and_creates_dispatch():
    store, engine, lifecycle = await build_runtime()
    try:
        session = await engine.create_session(
            runtime_session_id="rt-lifecycle",
            runtime_type="gateway",
            agent_id="agent-lifecycle",
        )
        task = await store.create_task(
            session.kernel_session_id,
            title="lifecycle task",
            goal="lifecycle task",
        )

        first_run = lifecycle.new_run_id()
        first_activation = await lifecycle.activate_run(
            session,
            run_id=first_run,
            active_task_id=task.task_id,
            user_message="first task",
        )
        assert first_activation.previous_run_id == ""

        first_session = await store.get_session(session.kernel_session_id)
        assert first_session.active_run_id == first_run
        assert first_session.active_task_id == task.task_id
        assert first_session.last_interrupted_run_id == ""

        second_run = lifecycle.new_run_id()
        second_activation = await lifecycle.activate_run(
            first_session,
            run_id=second_run,
            active_task_id=task.task_id,
            user_message="second task",
        )
        assert second_activation.previous_run_id == first_run
        assert second_activation.interrupt_event is not None

        second_session = await store.get_session(session.kernel_session_id)
        assert second_session.active_run_id == second_run
        assert second_session.last_interrupted_run_id == first_run
        assert second_session.last_interrupting_run_id == second_run
        assert second_session.last_interrupt_reason == "superseded_by_new_user_message"

        events = await store.get_events(session.kernel_session_id, limit=50)
        interrupted = [item for item in events if item["event_type"] == "RunInterrupted"]
        assert len(interrupted) == 1
        assert interrupted[0]["run_id"] == first_run

        dispatch = await lifecycle.create_thinker_dispatch(
            session_id=session.kernel_session_id,
            task_id=task.task_id,
            run_id=second_run,
            task_brief_version=second_session.intent_version,
            dispatch_type="interrupt_and_replan",
            payload={"user_message": "second task"},
        )
        assert dispatch.dispatch_id.startswith("td_")
        assert dispatch.run_id == second_run
        assert dispatch.task_id == task.task_id

        listed = await store.list_thinker_dispatches(
            kernel_session_id=session.kernel_session_id,
        )
        assert [item.dispatch_id for item in listed] == [dispatch.dispatch_id]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dispatch_lifecycle_submits_user_message_and_creates_task():
    store, engine, lifecycle = await build_runtime()
    try:
        session = await engine.create_session(
            runtime_session_id="rt-lifecycle-message",
            runtime_type="gateway",
            agent_id="agent-lifecycle",
        )
        run_id = lifecycle.new_run_id()
        await lifecycle.activate_run(
            session,
            run_id=run_id,
            user_message="research lifecycle message",
        )

        event = await lifecycle.submit_user_message(
            session.kernel_session_id,
            "research lifecycle message",
        )
        task = await lifecycle.create_task_from_user_message(
            session.kernel_session_id,
            text="research lifecycle message",
            run_id=run_id,
            event=event,
            session_status="running",
        )

        refreshed = await store.get_session(session.kernel_session_id)
        assert refreshed.active_task_id == task.task_id
        assert task.last_run_id == run_id
        assert task.goal
    finally:
        await store.close()
