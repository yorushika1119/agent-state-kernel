from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kernel.engine import KernelEngine
from src.kms.context.conversation_refs import ConversationRefCoordinator
from src.kms.dispatch.lifecycle import DispatchLifecycleCoordinator
from src.kms.dispatch.thinker_dispatch import ThinkerDispatchCoordinator
from src.schema.state import TaskRouteDecision
from src.stores.sqlite_store import SqliteStore


@pytest.mark.asyncio
async def test_thinker_dispatch_creation_records_user_message_ref():
    store = SqliteStore(":memory:")
    await store.connect()
    try:
        engine = KernelEngine(store)
        lifecycle = DispatchLifecycleCoordinator(store, engine)
        coordinator = ThinkerDispatchCoordinator(
            lifecycle,
            ConversationRefCoordinator(store),
        )
        session = await engine.create_session(
            agent_id="agent-dispatch",
            runtime_session_id="rt-dispatch",
        )
        user_session = await store.observe_user_session(
            user_session_id="user-dispatch",
            runtime_session_id="rt-dispatch",
            agent_id="agent-dispatch",
        )
        task = await store.create_task(
            session.kernel_session_id,
            title="任务 dispatch",
            goal="验证 thinker dispatch 创建",
        )
        route = TaskRouteDecision(
            route_id="route-dispatch",
            routing_decision="select_existing",
            target_task_id=task.task_id,
        )

        dispatch = await coordinator.create_for_user_message(
            session=session,
            task=task,
            run_id="run-dispatch",
            task_brief_version=3,
            dispatch_type="continue_active_task",
            user_text="继续这个任务",
            action="interrupt_and_replan",
            task_action="continue_active_task",
            route=route,
            user_session_id=user_session.user_session_id,
            resume_context={"task_id": task.task_id},
            runtime_refs={"message_id": "msg-user-dispatch"},
        )

        refs = await store.list_task_conversation_refs(
            user_session_id=user_session.user_session_id,
            task_id=task.task_id,
            limit=10,
        )

        assert dispatch.task_id == task.task_id
        assert dispatch.run_id == "run-dispatch"
        assert dispatch.dispatch_type == "continue_active_task"
        assert dispatch.payload["user_message"] == "继续这个任务"
        assert dispatch.payload["resume_context"]["task_id"] == task.task_id
        assert refs[0].role == "user"
        assert refs[0].message_ref_id == "msg-user-dispatch"
        assert refs[0].metadata["thinker_dispatch_id"] == dispatch.dispatch_id
        assert refs[0].metadata["task_action"] == "continue_active_task"
    finally:
        await store.close()
