from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api import server as api_server
from src.kernel.engine import KernelEngine
from src.kms.manager import KmsManager
from src.schema.state import (
    BeliefItem,
    Commitment,
    ExecutionAction,
    ProgressState,
)
from src.stores.sqlite_store import SqliteStore


async def build_runtime():
    store = SqliteStore(":memory:")
    await store.connect()
    engine = KernelEngine(store)
    manager = KmsManager(store, engine)
    return store, engine, manager


@pytest.fixture
def patch_api_runtime():
    async def apply(store, engine, manager):
        previous_store = api_server._store
        previous_engine = api_server._engine
        previous_kms_manager = api_server._kms_manager
        api_server._store = store
        api_server._engine = engine
        api_server._kms_manager = manager
        return previous_store, previous_engine, previous_kms_manager

    return apply


def restore_api_runtime(previous):
    api_server._store = previous[0]
    api_server._engine = previous[1]
    api_server._kms_manager = previous[2]


@pytest.mark.asyncio
async def test_observer_view_exposes_safe_progress_without_internal_state(patch_api_runtime):
    store, engine, manager = await build_runtime()
    previous = await patch_api_runtime(store, engine, manager)
    try:
        session = await engine.create_session(agent_id="agent-observer-view")
        task = await store.create_task(
            session.kernel_session_id,
            title="验证融资金额",
            goal="验证 A 公司融资金额",
            current_step_name="核对来源",
        )
        await store.update_session_status(
            session.kernel_session_id,
            "running",
            active_task_id=task.task_id,
        )
        await store.save_progress(
            session.kernel_session_id,
            ProgressState(
                session_id=session.kernel_session_id,
                status="running",
                stage="verification",
                summary="正在验证 A 公司融资金额。",
                safe_facts=["已找到 2 个相关来源"],
                unsafe_claims=["融资金额还未确认"],
                allowed_actions=["report_progress"],
                forbidden_actions=["claim_task_completed"],
            ),
        )
        notification = await store.create_observer_notification(
            target="observer",
            kernel_session_id=session.kernel_session_id,
            task_id=task.task_id,
            notification_type="progress_update",
            reason="progress changed",
        )

        transport = httpx.ASGITransport(app=api_server.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://kernel.test",
        ) as client:
            response = await client.get(
                f"/kms/sessions/{session.kernel_session_id}/views/observer"
            )

        assert response.status_code == 200
        view = response.json()
        assert view["task_id"] == task.task_id
        assert view["summary_for_observer"] == "正在验证 A 公司融资金额。"
        assert view["safe_facts"] == ["已找到 2 个相关来源"]
        assert view["uncertain_points"] == ["融资金额还未确认"]
        assert view["notifications"][0]["notification_id"] == notification.notification_id
        assert "beliefs" not in view
        assert "evidence" not in view
        assert "thinker_dispatches" not in view
    finally:
        restore_api_runtime(previous)
        await store.close()


@pytest.mark.asyncio
async def test_manager_view_exposes_task_risks_notifications_and_dispatches(patch_api_runtime):
    store, engine, manager = await build_runtime()
    previous = await patch_api_runtime(store, engine, manager)
    try:
        session = await engine.create_session(agent_id="agent-manager-view")
        task = await store.create_task(
            session.kernel_session_id,
            title="生成调研报告",
            goal="生成调研报告",
        )
        await store.update_session_status(
            session.kernel_session_id,
            "running",
            active_task_id=task.task_id,
        )
        await store.save_progress(
            session.kernel_session_id,
            ProgressState(
                session_id=session.kernel_session_id,
                status="blocked",
                stage="research",
                summary="调研需要用户确认范围。",
                needs_user_input=True,
            ),
        )
        await store.save_commitment(
            session.kernel_session_id,
            Commitment(
                commitment_id="todo_scope",
                statement="请确认调研范围",
                requires_confirmation=True,
            ),
        )
        await store.save_belief(
            session.kernel_session_id,
            BeliefItem(
                belief_id="belief_unverified",
                claim="A 公司融资金额为 3000 万美元",
            ),
        )
        await store.save_execution(
            session.kernel_session_id,
            ExecutionAction(
                action_id="tool_search",
                tool="search",
                status="failed",
                input_summary="搜索公司公告",
            ),
        )
        dispatch = await store.create_thinker_dispatch(
            kernel_session_id=session.kernel_session_id,
            task_id=task.task_id,
            run_id="run_manager_view",
        )
        notification = await store.create_observer_notification(
            target="talker",
            kernel_session_id=session.kernel_session_id,
            task_id=task.task_id,
            notification_type="needs_user_input",
            reason="ask user",
        )

        transport = httpx.ASGITransport(app=api_server.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://kernel.test",
        ) as client:
            response = await client.get(
                f"/kms/sessions/{session.kernel_session_id}/views/manager"
            )

        assert response.status_code == 200
        view = response.json()
        assert view["task_id"] == task.task_id
        assert view["blocking_reason"] == "awaiting_user_confirmation"
        assert view["pending_confirmations"] == ["请确认调研范围"]
        assert "unverified:A 公司融资金额为 3000 万美元" in view["risks"]
        assert "tool_failed:search" in view["risks"]
        assert view["notifications"][0]["notification_id"] == notification.notification_id
        assert view["thinker_dispatches"][0]["dispatch_id"] == dispatch.dispatch_id
    finally:
        restore_api_runtime(previous)
        await store.close()
