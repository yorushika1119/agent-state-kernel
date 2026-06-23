from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kernel.engine import KernelEngine
from src.kms.task.coordinators import (
    InterruptCoordinator,
    ResumeCoordinator,
    TaskSwitchCoordinator,
)
from src.schema.state import (
    IntentState,
    PlanState,
    PlanStep,
    ProgressState,
    StepStatus,
    TaskStatus,
)
from src.stores.sqlite_store import SqliteStore


async def build_runtime():
    store = SqliteStore(":memory:")
    await store.connect()
    engine = KernelEngine(store)
    coordinator = TaskSwitchCoordinator(
        store,
        InterruptCoordinator(store),
        ResumeCoordinator(store),
    )
    return store, engine, coordinator


@pytest.mark.asyncio
async def test_pause_current_task_syncs_global_directory():
    store, engine, coordinator = await build_runtime()
    try:
        session = await engine.create_session(
            agent_id="agent-switch",
            runtime_session_id="rt-switch",
        )
        user_session = await store.observe_user_session(
            user_session_id="user-switch",
            runtime_session_id="rt-switch",
            agent_id="agent-switch",
        )
        task = await store.create_task(
            session.kernel_session_id,
            title="任务 A",
            goal="整理任务切换设计",
            last_run_id="run_old",
        )
        await store.update_session_status(
            session.kernel_session_id,
            "running",
            active_run_id="run_old",
            active_task_id=task.task_id,
        )
        refreshed = await store.get_session(session.kernel_session_id)

        paused_task_id = await coordinator.pause_current_task(
            refreshed,
            interrupted_run_id="run_old",
            user_session_id=user_session.user_session_id,
            agent_id="agent-switch",
            task_brief_version=1,
        )

        paused = await store.get_task(session.kernel_session_id, task.task_id)
        global_task = await store.get_global_task(task.task_id)
        user_session_after = await store.get_user_session(user_session.user_session_id)

        assert paused_task_id == task.task_id
        assert paused.status == TaskStatus.PAUSED
        assert paused.last_interrupted_run_id == "run_old"
        assert global_task.status == TaskStatus.PAUSED
        assert task.task_id in user_session_after.linked_task_ids
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_activate_existing_task_restores_session_and_marks_global_active():
    store, engine, coordinator = await build_runtime()
    try:
        session = await engine.create_session(
            agent_id="agent-switch",
            runtime_session_id="rt-switch-active",
        )
        user_session = await store.observe_user_session(
            user_session_id="user-switch-active",
            runtime_session_id="rt-switch-active",
            agent_id="agent-switch",
        )
        task = await store.create_task(
            session.kernel_session_id,
            title="任务 B",
            goal="恢复任务上下文",
            status=TaskStatus.PAUSED,
            current_step="step_1",
            current_step_name="继续实现",
            last_run_id="run_old",
            last_interrupted_run_id="run_old",
            resume_summary="已经完成前置分析。",
        )

        active_task, resume_context = await coordinator.activate_existing_task(
            session,
            task,
            run_id="run_new",
            user_session_id=user_session.user_session_id,
            agent_id="agent-switch",
            task_brief_version=2,
        )

        stored = await store.get_task(session.kernel_session_id, task.task_id)
        global_task = await store.get_global_task(task.task_id)
        user_session_after = await store.get_user_session(user_session.user_session_id)

        assert active_task.task_id == task.task_id
        assert stored.status == TaskStatus.ACTIVE
        assert stored.last_run_id == "run_new"
        assert global_task.status == TaskStatus.ACTIVE
        assert user_session_after.active_task_id == task.task_id
        assert resume_context["task_id"] == task.task_id
        assert resume_context["resume_summary"] == "已经完成前置分析。"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_refresh_active_task_from_kernel_state_syncs_task_snapshot():
    store, engine, coordinator = await build_runtime()
    try:
        session = await engine.create_session(
            agent_id="agent-refresh",
            runtime_session_id="rt-refresh",
        )
        user_session = await store.observe_user_session(
            user_session_id="user-refresh",
            runtime_session_id="rt-refresh",
            agent_id="agent-refresh",
        )
        task = await store.create_task(
            session.kernel_session_id,
            title="旧标题",
            goal="旧目标",
            last_run_id="run_old",
        )
        await store.update_session_status(
            session.kernel_session_id,
            "running",
            active_run_id="run_refresh",
            active_task_id=task.task_id,
        )
        await store.save_intent(
            session.kernel_session_id,
            IntentState(
                intent_version=2,
                goal="新目标：整理 active task 刷新",
                constraints=["保持兼容"],
            ),
        )
        await store.save_plan(
            session.kernel_session_id,
            PlanState(
                plan_id="plan_refresh",
                current_step="step_2",
                steps=[
                    PlanStep(
                        step_id="step_1",
                        name="分析",
                        status=StepStatus.COMPLETED,
                    ),
                    PlanStep(
                        step_id="step_2",
                        name="实现",
                        status=StepStatus.RUNNING,
                    ),
                ],
                intent_version=2,
            ),
        )
        await store.save_progress(
            session.kernel_session_id,
            ProgressState(
                session_id=session.kernel_session_id,
                status="running",
                summary="已经完成分析，正在实现。",
            ),
        )
        refreshed = await store.get_session(session.kernel_session_id)

        active_task = await coordinator.refresh_active_task_from_kernel_state(
            refreshed,
            run_id="run_refresh",
            user_session_id=user_session.user_session_id,
            agent_id="agent-refresh",
            task_brief_version=2,
        )

        stored = await store.get_task(session.kernel_session_id, task.task_id)
        global_task = await store.get_global_task(task.task_id)

        assert active_task.task_id == task.task_id
        assert stored.goal == "新目标：整理 active task 刷新"
        assert stored.constraints == ["保持兼容"]
        assert stored.plan_id == "plan_refresh"
        assert stored.current_step == "step_2"
        assert stored.current_step_name == "实现"
        assert stored.resume_summary == "已经完成分析，正在实现。"
        assert stored.last_run_id == "run_refresh"
        assert global_task.status == TaskStatus.ACTIVE
        assert global_task.task_brief_version == 2
    finally:
        await store.close()
