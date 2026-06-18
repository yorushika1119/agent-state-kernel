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
    BeliefStatus,
    Commitment,
    CommitmentStatus,
    ExecutionAction,
    IntentState,
    PlanState,
    PlanStatus,
    PlanStep,
    StepStatus,
)
from src.stores.sqlite_store import SqliteStore
from src.utils.time import utc_now


async def build_runtime():
    store = SqliteStore(":memory:")
    await store.connect()
    engine = KernelEngine(store)
    manager = KmsManager(store, engine)
    return store, engine, manager


@pytest.mark.asyncio
async def test_http_thinker_dispatch_claim_by_dispatch_id():
    store, engine, manager = await build_runtime()
    previous_store = api_server._store
    previous_engine = api_server._engine
    previous_kms_manager = api_server._kms_manager
    api_server._store = store
    api_server._engine = engine
    api_server._kms_manager = manager

    try:
        session = await engine.create_session(agent_id="agent-claim-exact")
        task = await store.create_task(
            session.kernel_session_id,
            title="dispatch exact claim",
            goal="dispatch exact claim",
        )
        first = await store.create_thinker_dispatch(
            kernel_session_id=session.kernel_session_id,
            task_id=task.task_id,
            run_id="run-first",
        )
        second = await store.create_thinker_dispatch(
            kernel_session_id=session.kernel_session_id,
            task_id=task.task_id,
            run_id="run-second",
        )

        transport = httpx.ASGITransport(app=api_server.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://kernel.test",
        ) as client:
            claimed = await client.post(
                "/kms/thinker/dispatches/claim",
                json={
                    "dispatch_id": second.dispatch_id,
                    "thinker_id": "hermes-worker-exact",
                    "kernel_session_id": session.kernel_session_id,
                },
            )

        assert claimed.status_code == 200
        claimed_dispatch = claimed.json()["dispatch"]
        assert claimed_dispatch["dispatch_id"] == second.dispatch_id
        assert claimed_dispatch["run_id"] == "run-second"
        assert (await store.get_thinker_dispatch(first.dispatch_id)).status.value == "pending"
    finally:
        api_server._store = previous_store
        api_server._engine = previous_engine
        api_server._kms_manager = previous_kms_manager
        await store.close()


@pytest.mark.asyncio
async def test_http_thinker_dispatch_fail_stale_run_does_not_clear_active_run():
    store, engine, manager = await build_runtime()
    previous_store = api_server._store
    previous_engine = api_server._engine
    previous_kms_manager = api_server._kms_manager
    api_server._store = store
    api_server._engine = engine
    api_server._kms_manager = manager

    try:
        session = await engine.create_session(agent_id="agent-stale-dispatch")
        task = await store.create_task(
            session.kernel_session_id,
            title="stale dispatch",
            goal="stale dispatch",
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

        assert failed.status_code == 200
        assert failed.json()["status"] == "failed"
        refreshed = await store.get_session(session.kernel_session_id)
        assert refreshed.active_run_id == "run-new"
    finally:
        api_server._store = previous_store
        api_server._engine = previous_engine
        api_server._kms_manager = previous_kms_manager
        await store.close()


@pytest.mark.asyncio
async def test_new_state_aliases_mirror_existing_state_tables():
    store, engine, _manager = await build_runtime()
    try:
        session = await engine.create_session(agent_id="agent-alias")
        sid = session.kernel_session_id
        task = await store.create_task(
            sid,
            title="整理实时打断机制",
            goal="整理实时打断机制",
            last_run_id="run_alias",
        )
        await store.update_session_status(
            sid,
            session.status.value,
            active_task_id=task.task_id,
        )

        await store.save_intent(
            sid,
            IntentState(
                intent_version=3,
                goal="整理实时打断机制",
                constraints=["保留 KMS/kernel 分层"],
                output_format="markdown",
            ),
        )
        await store.save_plan(
            sid,
            PlanState(
                plan_id="plan_alias",
                status=PlanStatus.ACTIVE,
                current_step="s1",
                steps=[
                    PlanStep(step_id="s1", name="分析", status=StepStatus.RUNNING),
                    PlanStep(step_id="s2", name="输出", status=StepStatus.PENDING),
                ],
                intent_version=3,
            ),
        )
        await store.save_execution(
            sid,
            ExecutionAction(
                action_id="act_alias",
                step_id="s1",
                tool="browser.search",
                status="success",
                input_summary="搜索 Codex interrupt 机制",
                ended_at=utc_now(),
            ),
        )
        await store.save_belief(
            sid,
            BeliefItem(
                belief_id="belief_alias",
                claim="KMS 应该负责调度",
                status=BeliefStatus.VERIFIED,
                confidence=0.9,
            ),
        )
        await store.save_commitment(
            sid,
            Commitment(
                commitment_id="todo_alias",
                statement="后续补 thinker dispatch",
                status=CommitmentStatus.PENDING,
                related_intent_version=3,
            ),
        )

        task_brief = await store.get_task_brief(sid)
        task_flow = await store.get_task_flow(sid)
        claims = await store.get_claim_items(sid)
        todos = await store.get_todo_obligations(sid)
        thinker_view = await engine.get_thinker_view(sid)

        assert task_brief is not None
        assert task_brief.task_id == task.task_id
        assert task_brief.task_brief_version == 3
        assert task_brief.goal == "整理实时打断机制"

        assert task_flow is not None
        assert task_flow.flow_id == "plan_alias"
        assert task_flow.task_id == task.task_id
        assert task_flow.execution_summary[-1]["action_id"] == "act_alias"

        assert [claim.claim_id for claim in claims] == ["belief_alias"]
        assert claims[0].claim == "KMS 应该负责调度"
        assert [todo.obligation_id for todo in todos] == ["todo_alias"]
        assert todos[0].related_task_brief_version == 3

        assert thinker_view["task_brief"]["task_id"] == task.task_id
        assert thinker_view["task_flow"]["flow_id"] == "plan_alias"
        assert thinker_view["claims"][0]["claim_id"] == "belief_alias"
        assert thinker_view["todos"][0]["obligation_id"] == "todo_alias"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_http_thinker_dispatch_claim_heartbeat_and_complete():
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
                    "text": "请整理 KMS thinker dispatch 设计",
                    "runtime_session_id": "rt-dispatch",
                    "runtime_type": "gateway",
                    "agent_id": "agent-dispatch",
                },
            )
            assert dispatch_response.status_code == 200
            dispatch_data = dispatch_response.json()
            dispatch_id = dispatch_data["thinker_dispatch_id"]
            assert dispatch_id.startswith("td_")

            listed = await client.get(
                "/kms/thinker/dispatches",
                params={
                    "kernel_session_id": dispatch_data["kernel_session_id"],
                    "status": "pending",
                },
            )
            assert listed.status_code == 200
            assert [item["dispatch_id"] for item in listed.json()] == [dispatch_id]

            claimed = await client.post(
                "/kms/thinker/dispatches/claim",
                json={
                    "thinker_id": "hermes-worker-1",
                    "kernel_session_id": dispatch_data["kernel_session_id"],
                },
            )
            assert claimed.status_code == 200
            claimed_dispatch = claimed.json()["dispatch"]
            assert claimed_dispatch["dispatch_id"] == dispatch_id
            assert claimed_dispatch["status"] == "claimed"
            assert claimed_dispatch["run_id"] == dispatch_data["run_id"]

            heartbeat = await client.post(
                f"/kms/thinker/dispatches/{dispatch_id}/heartbeat",
            )
            assert heartbeat.status_code == 200
            assert heartbeat.json()["heartbeat_at"] is not None

            completed = await client.post(
                f"/kms/thinker/dispatches/{dispatch_id}/complete",
                json={"session_status": "completed"},
            )
            assert completed.status_code == 200
            assert completed.json()["status"] == "completed"

            thinker = await client.get(
                f"/kms/sessions/{dispatch_data['kernel_session_id']}/views/thinker"
            )
            assert thinker.status_code == 200
            thinker_data = thinker.json()
            assert thinker_data["cancellation"]["active_run_id"] == ""
            assert thinker_data["thinker_dispatches"][0]["status"] == "completed"
    finally:
        api_server._store = previous_store
        api_server._engine = previous_engine
        api_server._kms_manager = previous_kms_manager
        await store.close()
