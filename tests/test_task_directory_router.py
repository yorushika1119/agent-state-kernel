from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kernel.engine import KernelEngine
from src.kms.manager import KmsManager
from src.kms.task_context_router import route_task_context
from src.schema.events import EventSubmission
from src.stores.sqlite_store import SqliteStore


async def build_runtime():
    store = SqliteStore(":memory:")
    await store.connect()
    engine = KernelEngine(store)
    manager = KmsManager(store, engine)
    return store, engine, manager


@pytest.mark.asyncio
async def test_dispatch_creates_user_session_and_global_task_directory_entry():
    store, _engine, manager = await build_runtime()
    try:
        decision = await manager.dispatch_user_message(
            text="请研究 A 公司融资情况并整理邮件草稿",
            runtime_session_id="rt-directory-1",
            runtime_type="gateway",
            agent_id="agent-directory",
        )

        user_session = await store.get_user_session(decision.user_session_id)
        global_tasks = await store.list_global_tasks(user_session_id=decision.user_session_id)
        global_task = await store.get_global_task(decision.task_id)

        assert user_session is not None
        assert user_session.runtime_session_id == "rt-directory-1"
        assert user_session.active_task_id == decision.task_id
        assert decision.task_id in user_session.linked_task_ids

        assert len(global_tasks) == 1
        assert global_task is not None
        assert global_task.task_id == decision.task_id
        assert global_task.kernel_session_id == decision.kernel_session_id
        assert global_task.user_session_id == decision.user_session_id
        assert global_task.agent_id == "agent-directory"
        assert "A 公司" in global_task.task_description
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_global_task_directory_tracks_pause_resume_and_completion():
    store, engine, manager = await build_runtime()
    try:
        first = await manager.dispatch_user_message(
            text="请研究任务 A：实时打断机制",
            runtime_session_id="rt-directory-2",
            runtime_type="gateway",
            agent_id="agent-directory",
        )
        second = await manager.dispatch_user_message(
            text="这是一个新任务，研究任务 B：任务路由机制",
            runtime_session_id="rt-directory-2",
            runtime_type="gateway",
            agent_id="agent-directory",
        )

        first_global = await store.get_global_task(first.task_id)
        second_global = await store.get_global_task(second.task_id)
        user_session = await store.get_user_session(first.user_session_id)

        assert first_global.status.value == "paused"
        assert second_global.status.value == "active"
        assert user_session.active_task_id == second.task_id
        assert set(user_session.linked_task_ids) == {first.task_id, second.task_id}

        resume = await manager.dispatch_user_message(
            text="继续刚才的任务",
            runtime_session_id="rt-directory-2",
            runtime_type="gateway",
            agent_id="agent-directory",
        )
        resumed_global = await store.get_global_task(resume.task_id)
        assert resumed_global.status.value == "active"

        assert await engine.complete_run(resume.kernel_session_id, resume.run_id, session_status="completed")
        completed_global = await store.get_global_task(resume.task_id)
        assert completed_global.status.value == "completed"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dispatch_uses_router_target_task_to_switch_existing_task():
    store, engine, manager = await build_runtime()
    try:
        first = await manager.dispatch_user_message(
            text="请研究任务 A：实时打断机制",
            runtime_session_id="rt-router-target",
            runtime_type="gateway",
            agent_id="agent-router",
        )
        second = await manager.dispatch_user_message(
            text="这是一个新任务，请研究任务 B：任务路由机制",
            runtime_session_id="rt-router-target",
            runtime_type="gateway",
            agent_id="agent-router",
        )

        assert second.task_id != first.task_id
        assert (await store.get_global_task(first.task_id)).status.value == "paused"
        assert (await store.get_global_task(second.task_id)).status.value == "active"

        routed = await manager.dispatch_user_message(
            text="任务 A：实时打断机制 那个继续做",
            runtime_session_id="rt-router-target",
            runtime_type="gateway",
            agent_id="agent-router",
        )

        assert routed.route_decision == "select_existing"
        assert routed.reason == "route_selected_existing_task"
        assert routed.task_action == "continue_routed_task"
        assert routed.task_id == first.task_id
        assert routed.run_id not in {first.run_id, second.run_id}

        thinker = await engine.get_thinker_view(first.kernel_session_id)
        assert thinker["cancellation"]["active_task_id"] == first.task_id
        assert thinker["cancellation"]["active_run_id"] == routed.run_id
        assert thinker["cancellation"]["last_interrupted_run_id"] == second.run_id

        assert (await store.get_global_task(first.task_id)).status.value == "active"
        assert (await store.get_global_task(second.task_id)).status.value == "paused"

        ok, reason, event = await engine.submit_event(
            EventSubmission(
                session_id=first.kernel_session_id,
                component="thinker",
                request_type="ToolStarted",
                run_id=second.run_id,
                payload={
                    "action_id": "act_stale_router",
                    "tool": "local.read",
                    "input_summary": "stale routed task write",
                },
            )
        )
        assert not ok
        assert event is None
        assert "Stale thinker run" in (reason or "")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_ambiguous_router_target_asks_clarification_without_interrupting():
    store, engine, manager = await build_runtime()
    try:
        first = await manager.dispatch_user_message(
            text="请研究任务 A：实时打断机制",
            runtime_session_id="rt-router-clarify",
            runtime_type="gateway",
            agent_id="agent-router",
        )
        second = await manager.dispatch_user_message(
            text="这是一个新任务，请研究任务 B：任务路由机制",
            runtime_session_id="rt-router-clarify",
            runtime_type="gateway",
            agent_id="agent-router",
        )
        before_events = await store.get_events(second.kernel_session_id, limit=100)
        before_interrupts = [
            item for item in before_events if item["event_type"] == "RunInterrupted"
        ]

        reply = await manager.dispatch_user_message(
            text="那个现在怎么样？",
            runtime_session_id="rt-router-clarify",
            runtime_type="gateway",
            agent_id="agent-router",
        )

        assert reply.action == "respond_from_kernel"
        assert reply.task_action == "ask_clarification"
        assert reply.requires_thinker is False
        assert reply.reason == "task_route_needs_clarification"
        assert reply.run_id == second.run_id
        assert "你指的是哪一个任务" in reply.kernel_response

        thinker = await engine.get_thinker_view(second.kernel_session_id)
        assert thinker["cancellation"]["active_run_id"] == second.run_id
        assert thinker["cancellation"]["active_task_id"] == second.task_id

        after_events = await store.get_events(second.kernel_session_id, limit=100)
        after_interrupts = [
            item for item in after_events if item["event_type"] == "RunInterrupted"
        ]
        assert len(after_interrupts) == len(before_interrupts)
    finally:
        await store.close()


def test_task_context_router_selects_by_recent_other_and_hints():
    from src.schema.state import GlobalTask

    task_a = GlobalTask(
        task_id="task_a",
        kernel_session_id="ask_a",
        user_session_id="us_1",
        title="研究 A 公司融资并写邮件",
        task_description="整理 A 公司融资情况",
        routing_hints=["A 公司", "融资", "邮件"],
        last_user_touch_at=datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc),
    )
    task_b = GlobalTask(
        task_id="task_b",
        kernel_session_id="ask_b",
        user_session_id="us_1",
        title="修复测试失败",
        task_description="修复 repo 的 pytest 失败",
        routing_hints=["测试失败", "pytest", "repo"],
        last_user_touch_at=datetime(2026, 6, 18, 11, 0, tzinfo=timezone.utc),
    )

    by_hint = route_task_context(
        "A 公司那个进度如何？",
        user_session_id="us_1",
        runtime_session_id="rt",
        tasks=[task_b, task_a],
    )
    assert by_hint.routing_decision == "select_existing"
    assert by_hint.target_task_id == "task_a"

    other = route_task_context(
        "另一个任务继续做",
        user_session_id="us_1",
        runtime_session_id="rt",
        tasks=[task_b, task_a],
    )
    assert other.routing_decision == "select_existing"
    assert other.target_task_id == "task_a"

    unclear = route_task_context(
        "那个现在怎么样？",
        user_session_id="us_1",
        runtime_session_id="rt",
        tasks=[task_b, task_a],
    )
    assert unclear.routing_decision == "ask_clarification"
    assert unclear.needs_user_clarification is True
