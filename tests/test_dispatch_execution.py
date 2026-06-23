from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kernel.engine import KernelEngine
from src.kms import KmsManager
from src.schema.state import TaskRouteDecision
from src.stores.sqlite_store import SqliteStore


async def build_runtime():
    store = SqliteStore(":memory:")
    await store.connect()
    engine = KernelEngine(store)
    manager = KmsManager(store, engine)
    return store, engine, manager


def _flags(**updates):
    data = {
        "explicit_new_task_requested": False,
        "wants_new_task": False,
        "wants_resume": False,
        "wants_same_task_steer": False,
    }
    data.update(updates)
    return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_dispatch_execution_creates_task_and_thinker_dispatch():
    store, _engine, manager = await build_runtime()
    try:
        user_session = await store.observe_user_session(
            runtime_session_id="rt-exec",
            runtime_type="gateway",
            agent_id="agent-exec",
        )
        route = TaskRouteDecision(
            user_session_id=user_session.user_session_id,
            runtime_session_id="rt-exec",
            routing_decision="create_new",
        )

        result = await manager.dispatch_execution.execute(
            text="research dispatch execution split",
            session=None,
            route=route,
            route_target_task=None,
            flags=_flags(),
            user_session=user_session,
            runtime_session_id="rt-exec",
            runtime_type="gateway",
            agent_id="agent-exec",
        )

        assert result.requires_thinker is True
        assert result.run_id.startswith("run_")
        assert result.active_task.task_id
        assert result.thinker_dispatch.dispatch_id.startswith("td_")
        assert result.task_plan.action == "start_new_task"

        global_task = await store.get_global_task(result.active_task.task_id)
        assert global_task is not None
        assert global_task.user_session_id == user_session.user_session_id
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dispatch_execution_no_resume_task_returns_non_thinker_result():
    store, engine, manager = await build_runtime()
    try:
        session = await engine.create_session(
            runtime_session_id="rt-exec-resume",
            runtime_type="gateway",
            agent_id="agent-exec",
        )
        user_session = await store.observe_user_session(
            runtime_session_id="rt-exec-resume",
            runtime_type="gateway",
            agent_id="agent-exec",
        )
        route = TaskRouteDecision(
            user_session_id=user_session.user_session_id,
            runtime_session_id="rt-exec-resume",
            routing_decision="select_existing",
        )

        result = await manager.dispatch_execution.execute(
            text="continue previous task",
            session=session,
            route=route,
            route_target_task=None,
            flags=_flags(wants_resume=True),
            user_session=user_session,
            runtime_session_id="rt-exec-resume",
            runtime_type="gateway",
            agent_id="agent-exec",
        )

        assert result.requires_thinker is False
        assert result.task_plan.no_resume_task is True
        assert result.kernel_response == ""
        assert result.reason == "no_paused_task_to_resume"
        assert result.task_action == "respond_from_kernel"
    finally:
        await store.close()
