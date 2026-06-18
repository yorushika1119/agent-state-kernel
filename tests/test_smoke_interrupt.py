"""Smoke tests for interrupt-and-replan run switching."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api import server as api_server
from src.kms.manager import KmsManager
from src.kernel.engine import KernelEngine
from src.schema.events import EventSubmission
from src.stores.sqlite_store import SqliteStore


async def build_engine():
    store = SqliteStore(":memory:")
    await store.connect()
    engine = KernelEngine(store)
    manager = KmsManager(store, engine)
    return store, engine, manager


@pytest.mark.asyncio
async def test_http_api_interrupt_flow_rejects_stale_run_and_clears_active_run():
    store, engine, manager = await build_engine()
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
            first = await client.post(
                "/kms/dispatch-user-message",
                json={
                    "text": "first task",
                    "runtime_session_id": "rt-http-interrupt",
                    "runtime_type": "gateway",
                    "agent_id": "agent-http",
                },
            )
            assert first.status_code == 200
            first_data = first.json()
            assert first_data["action"] == "start_new_task"
            session_id = first_data["kernel_session_id"]
            run_1 = first_data["run_id"]
            assert session_id.startswith("ask_")
            assert run_1.startswith("run_")

            second = await client.post(
                "/kms/dispatch-user-message",
                json={
                    "text": "interrupt with another task",
                    "runtime_session_id": "rt-http-interrupt",
                    "runtime_type": "gateway",
                    "agent_id": "agent-http",
                },
            )
            assert second.status_code == 200
            second_data = second.json()
            run_2 = second_data["run_id"]
            assert second_data["action"] == "interrupt_and_replan"
            assert second_data["kernel_session_id"] == session_id
            assert run_2 != run_1

            thinker = await client.get(f"/kms/sessions/{session_id}/views/thinker")
            assert thinker.status_code == 200
            thinker_data = thinker.json()
            assert thinker_data["cancellation"]["active_run_id"] == run_2
            assert thinker_data["cancellation"]["last_interrupted_run_id"] == run_1
            assert thinker_data["cancellation"]["last_interrupting_run_id"] == run_2
            assert thinker_data["cancellation"]["last_interrupt_reason"] == "superseded_by_new_user_message"

            events = await client.get(f"/kernel/sessions/{session_id}/events")
            assert events.status_code == 200
            event_types = [item["event_type"] for item in events.json()]
            assert "RunInterrupted" in event_types

            stale = await client.post(
                "/kms/request",
                json={
                    "session_id": session_id,
                    "component": "thinker",
                    "request_type": "ToolStarted",
                    "run_id": run_1,
                    "payload": {
                        "action_id": "act_stale_http",
                        "tool": "web.search",
                        "input_summary": "stale run event",
                    },
                },
            )
            assert stale.status_code == 400
            assert "Stale thinker run" in stale.json()["detail"]

            fresh = await client.post(
                "/kms/request",
                json={
                    "session_id": session_id,
                    "component": "thinker",
                    "request_type": "ToolStarted",
                    "run_id": run_2,
                    "payload": {
                        "action_id": "act_fresh_http",
                        "tool": "web.search",
                        "input_summary": "fresh run event",
                    },
                },
            )
            assert fresh.status_code == 200
            assert fresh.json()["accepted"] is True

            old_complete = await client.post(
                "/kms/complete-run",
                json={"session_id": session_id, "run_id": run_1},
            )
            assert old_complete.status_code == 409

            current_complete = await client.post(
                "/kms/complete-run",
                json={"session_id": session_id, "run_id": run_2},
            )
            assert current_complete.status_code == 200
            assert current_complete.json()["ok"] is True

            thinker = await client.get(f"/kms/sessions/{session_id}/views/thinker")
            assert thinker.status_code == 200
            assert thinker.json()["cancellation"]["active_run_id"] == ""
    finally:
        api_server._store = previous_store
        api_server._engine = previous_engine
        api_server._kms_manager = previous_kms_manager
        await store.close()


@pytest.mark.asyncio
async def test_engine_interrupt_flow_switches_active_run_and_rejects_old_run():
    store, engine, manager = await build_engine()
    try:
        first = await manager.dispatch_user_message(
            text="first task",
            runtime_session_id="rt-engine-interrupt",
            runtime_type="gateway",
            agent_id="agent-engine",
        )
        second = await manager.dispatch_user_message(
            text="interrupt with another task",
            runtime_session_id="rt-engine-interrupt",
            runtime_type="gateway",
            agent_id="agent-engine",
        )

        assert first.action == "start_new_task"
        assert second.action == "interrupt_and_replan"
        assert second.reason == "reuse_existing_session"
        assert second.kernel_session_id == first.kernel_session_id
        assert second.run_id != first.run_id

        thinker = await engine.get_thinker_view(first.kernel_session_id)
        assert thinker["cancellation"]["active_run_id"] == second.run_id
        assert thinker["cancellation"]["last_interrupted_run_id"] == first.run_id
        assert thinker["cancellation"]["last_interrupting_run_id"] == second.run_id
        assert thinker["cancellation"]["last_interrupt_reason"] == "superseded_by_new_user_message"

        events = await store.get_events(first.kernel_session_id, limit=50)
        interrupted_events = [e for e in events if e["event_type"] == "RunInterrupted"]
        assert len(interrupted_events) == 1
        assert interrupted_events[0]["run_id"] == first.run_id

        ok, reason, event = await engine.submit_event(
            EventSubmission(
                session_id=first.kernel_session_id,
                component="thinker",
                request_type="ToolStarted",
                run_id=first.run_id,
                payload={
                    "action_id": "act_stale",
                    "tool": "web.search",
                    "input_summary": "stale run should fail",
                },
            )
        )
        assert not ok
        assert event is None
        assert "Stale thinker run" in (reason or "")

        ok, reason, event = await engine.submit_event(
            EventSubmission(
                session_id=first.kernel_session_id,
                component="thinker",
                request_type="ToolStarted",
                run_id=second.run_id,
                payload={
                    "action_id": "act_fresh",
                    "tool": "web.search",
                    "input_summary": "fresh run should pass",
                },
            )
        )
        assert ok
        assert reason is None
        assert event is not None
        assert event.run_id == second.run_id
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_old_run_tool_failed_is_rejected_after_interrupt():
    store, engine, manager = await build_engine()
    try:
        first = await manager.dispatch_user_message(
            text="task A",
            runtime_session_id="rt-tool-failed",
            runtime_type="gateway",
            agent_id="agent-engine",
        )
        second = await manager.dispatch_user_message(
            text="task B",
            runtime_session_id="rt-tool-failed",
            runtime_type="gateway",
            agent_id="agent-engine",
        )

        ok, reason, event = await engine.submit_event(
            EventSubmission(
                session_id=first.kernel_session_id,
                component="thinker",
                request_type="ToolFailed",
                run_id=first.run_id,
                payload={
                    "action_id": "fail_old",
                    "tool": "browser.open",
                    "error": "timeout",
                },
            )
        )
        assert not ok
        assert event is None
        assert "Stale thinker run" in (reason or "")

        ok, reason, event = await engine.submit_event(
            EventSubmission(
                session_id=first.kernel_session_id,
                component="thinker",
                request_type="ToolFailed",
                run_id=second.run_id,
                payload={
                    "action_id": "fail_new",
                    "tool": "browser.open",
                    "error": "timeout",
                },
            )
        )
        assert ok
        assert reason is None
        assert event is not None
        assert event.run_id == second.run_id
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_multiple_interrupts_leave_only_latest_run_active():
    store, engine, manager = await build_engine()
    try:
        runtime_session_id = "rt-multi-interrupt"
        runs: list[str] = []
        messages = [
            "research alpha",
            "research beta",
            "research gamma",
            "research delta",
        ]

        first = await manager.dispatch_user_message(
            text=messages[0],
            runtime_session_id=runtime_session_id,
            runtime_type="gateway",
            agent_id="agent-engine",
        )
        session_id = first.kernel_session_id
        runs.append(first.run_id)

        for text in messages[1:]:
            decision = await manager.dispatch_user_message(
                text=text,
                runtime_session_id=runtime_session_id,
                runtime_type="gateway",
                agent_id="agent-engine",
            )
            assert decision.action == "interrupt_and_replan"
            assert decision.kernel_session_id == session_id
            assert decision.run_id != runs[-1]
            runs.append(decision.run_id)

        thinker = await engine.get_thinker_view(session_id)
        assert thinker["cancellation"]["active_run_id"] == runs[-1]
        assert thinker["cancellation"]["last_interrupted_run_id"] == runs[-2]
        assert thinker["cancellation"]["last_interrupting_run_id"] == runs[-1]
        assert thinker["cancellation"]["last_interrupt_reason"] == "superseded_by_new_user_message"

        for old_run in runs[:-1]:
            ok, reason, event = await engine.submit_event(
                EventSubmission(
                    session_id=session_id,
                    component="thinker",
                    request_type="ToolStarted",
                    run_id=old_run,
                    payload={
                        "action_id": f"act_{old_run}",
                        "tool": "web.search",
                        "input_summary": "old run should be rejected",
                    },
                )
            )
            assert not ok
            assert event is None
            assert "Stale thinker run" in (reason or "")

        ok, reason, event = await engine.submit_event(
            EventSubmission(
                session_id=session_id,
                component="thinker",
                request_type="ToolStarted",
                run_id=runs[-1],
                payload={
                    "action_id": "act_latest",
                    "tool": "web.search",
                    "input_summary": "latest run should pass",
                },
            )
        )
        assert ok
        assert reason is None
        assert event is not None
        assert event.run_id == runs[-1]

        events = await store.get_events(session_id, limit=100)
        interrupted_runs = [e["run_id"] for e in events if e["event_type"] == "RunInterrupted"]
        assert interrupted_runs == runs[:-1]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_new_task_mode_stays_in_same_session_and_creates_new_task():
    store, engine, manager = await build_engine()
    try:
        first = await manager.dispatch_user_message(
            text="task in original session",
            runtime_session_id="rt-explicit-new-task",
            runtime_type="gateway",
            agent_id="agent-engine",
        )

        second = await manager.dispatch_user_message(
            text="start a fresh task",
            runtime_session_id="rt-explicit-new-task",
            runtime_type="gateway",
            agent_id="agent-engine",
            target_session_id=first.kernel_session_id,
            mode="new_task",
        )

        assert second.action == "start_new_task"
        assert second.reason == "explicit_new_task_requested"
        assert second.kernel_session_id == first.kernel_session_id
        assert second.task_id != first.task_id
        assert second.run_id != first.run_id
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_kernel_direct_status_reply_does_not_interrupt_active_run():
    store, engine, manager = await build_engine()
    try:
        first = await manager.dispatch_user_message(
            text="research alpha",
            runtime_session_id="rt-kernel-direct-reply",
            runtime_type="gateway",
            agent_id="agent-engine",
        )

        reply = await manager.dispatch_user_message(
            text="现在完成到哪一步了？",
            runtime_session_id="rt-kernel-direct-reply",
            runtime_type="gateway",
            agent_id="agent-engine",
        )

        assert reply.action == "respond_from_kernel"
        assert reply.task_action == "respond_from_kernel"
        assert reply.requires_thinker is False
        assert reply.kernel_session_id == first.kernel_session_id
        assert reply.run_id == first.run_id
        assert reply.kernel_response

        thinker = await engine.get_thinker_view(first.kernel_session_id)
        assert thinker["cancellation"]["active_run_id"] == first.run_id

        events = await store.get_events(first.kernel_session_id, limit=50)
        interrupted_events = [e for e in events if e["event_type"] == "RunInterrupted"]
        assert interrupted_events == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_http_api_kernel_direct_status_reply_exposes_kernel_fields():
    store, engine, manager = await build_engine()
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
            first = await client.post(
                "/kms/dispatch-user-message",
                json={
                    "text": "research alpha",
                    "runtime_session_id": "rt-http-kernel-direct",
                    "runtime_type": "gateway",
                    "agent_id": "agent-http",
                },
            )
            first_data = first.json()

            second = await client.post(
                "/kms/dispatch-user-message",
                json={
                    "text": "当前状态如何？",
                    "runtime_session_id": "rt-http-kernel-direct",
                    "runtime_type": "gateway",
                    "agent_id": "agent-http",
                },
            )
            second_data = second.json()

            assert second.status_code == 200
            assert second_data["action"] == "respond_from_kernel"
            assert second_data["requires_thinker"] is False
            assert second_data["kernel_response"]
            assert second_data["run_id"] == first_data["run_id"]
    finally:
        api_server._store = previous_store
        api_server._engine = previous_engine
        api_server._kms_manager = previous_kms_manager
        await store.close()


@pytest.mark.asyncio
async def test_resume_previous_task_returns_resume_context():
    store, engine, manager = await build_engine()
    try:
        first = await manager.dispatch_user_message(
            text="research alpha",
            runtime_session_id="rt-resume-task",
            runtime_type="gateway",
            agent_id="agent-engine",
        )
        second = await manager.dispatch_user_message(
            text="research beta",
            runtime_session_id="rt-resume-task",
            runtime_type="gateway",
            agent_id="agent-engine",
        )

        resume = await manager.dispatch_user_message(
            text="继续刚才的任务",
            runtime_session_id="rt-resume-task",
            runtime_type="gateway",
            agent_id="agent-engine",
        )

        assert second.action == "interrupt_and_replan"
        assert resume.task_action == "continue_paused_task"
        assert resume.reason == "resume_previous_task"
        assert resume.run_id != second.run_id
        assert resume.resume_context["task_id"]
        assert resume.resume_context["goal"]

        thinker = await engine.get_thinker_view(first.kernel_session_id)
        assert thinker["cancellation"]["active_run_id"] == resume.run_id
        assert thinker["cancellation"]["active_task_id"] == resume.task_id
    finally:
        await store.close()
