from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kernel.engine import KernelEngine
from src.schema.state import (
    BeliefItem,
    BeliefStatus,
    ClaimItem,
    Commitment,
    CommitmentStatus,
    IntentState,
    PlanState,
    PlanStatus,
    PlanStep,
    StepStatus,
    TaskBriefState,
    TaskFlowState,
    TodoObligation,
)
from src.stores.sqlite_store import SqliteStore


async def build_runtime():
    store = SqliteStore(":memory:")
    await store.connect()
    engine = KernelEngine(store)
    return store, engine


async def count_rows(store: SqliteStore, table: str, session_id: str) -> int:
    rows = await store.conn.execute_fetchall(
        f"SELECT COUNT(*) AS count FROM {table} WHERE kernel_session_id = ?",
        (session_id,),
    )
    return rows[0]["count"]


async def table_exists(store: SqliteStore, table: str) -> bool:
    rows = await store.conn.execute_fetchall(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    )
    return bool(rows)


@pytest.mark.asyncio
async def test_store_can_run_without_legacy_state_tables():
    store = SqliteStore(":memory:", create_legacy_state_tables=False)
    await store.connect()
    engine = KernelEngine(store)
    try:
        for table in ("intent_states", "plan_states", "belief_items", "commitments"):
            assert not await table_exists(store, table)

        session = await engine.create_session(agent_id="agent-new-only")
        sid = session.kernel_session_id

        await store.save_intent(sid, IntentState(intent_version=1, goal="new only"))
        await store.save_plan(
            sid,
            PlanState(
                plan_id="plan_new_only",
                status=PlanStatus.ACTIVE,
                current_step="step_1",
                steps=[
                    PlanStep(
                        step_id="step_1",
                        name="new step",
                        status=StepStatus.RUNNING,
                    )
                ],
                intent_version=1,
            ),
        )
        await store.save_belief(
            sid,
            BeliefItem(
                belief_id="claim_new_only",
                claim="new only claim",
                status=BeliefStatus.VERIFIED,
                confidence=0.9,
            ),
        )
        await store.save_commitment(
            sid,
            Commitment(
                commitment_id="todo_new_only",
                statement="new only todo",
                status=CommitmentStatus.PENDING,
            ),
        )

        assert (await store.get_intent(sid)).goal == "new only"
        assert (await store.get_plan(sid)).current_step == "step_1"
        assert (await store.get_beliefs(sid))[0].claim == "new only claim"
        assert (await store.get_commitments(sid))[0].statement == "new only todo"
        assert await store.get_legacy_state_fallback_audit() == []
        assert await store.delete_session(sid) is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_legacy_getters_prefer_task_first_state_when_both_exist():
    store, engine = await build_runtime()
    try:
        session = await engine.create_session(agent_id="agent-primary-read")
        task = await store.create_task(
            session.kernel_session_id,
            title="new table primary",
            goal="new table primary",
        )
        await store.update_session_status(
            session.kernel_session_id,
            session.status.value,
            active_task_id=task.task_id,
        )

        await store.save_intent(
            session.kernel_session_id,
            IntentState(intent_version=1, goal="old goal"),
        )
        await store.save_task_brief(
            TaskBriefState(
                kernel_session_id=session.kernel_session_id,
                task_id=task.task_id,
                task_brief_version=5,
                goal="new goal",
            )
        )

        await store.save_plan(
            session.kernel_session_id,
            PlanState(
                plan_id="plan_primary",
                status=PlanStatus.ACTIVE,
                current_step="old_step",
                steps=[
                    PlanStep(
                        step_id="old_step",
                        name="old step",
                        status=StepStatus.RUNNING,
                    )
                ],
                intent_version=1,
            ),
        )
        await store.save_task_flow(
            TaskFlowState(
                kernel_session_id=session.kernel_session_id,
                flow_id="plan_primary",
                task_id=task.task_id,
                status=PlanStatus.ACTIVE,
                current_step="new_step",
                steps=[
                    {
                        "step_id": "new_step",
                        "name": "new step",
                        "status": "running",
                    }
                ],
                task_brief_version=5,
            )
        )

        await store.save_belief(
            session.kernel_session_id,
            BeliefItem(
                belief_id="claim_primary",
                claim="old claim",
                status=BeliefStatus.UNVERIFIED,
            ),
        )
        await store.save_claim_item(
            ClaimItem(
                claim_id="claim_primary",
                kernel_session_id=session.kernel_session_id,
                task_id=task.task_id,
                claim="new claim",
                status=BeliefStatus.VERIFIED,
                confidence=0.9,
            )
        )

        await store.save_commitment(
            session.kernel_session_id,
            Commitment(
                commitment_id="todo_primary",
                statement="old todo",
                status=CommitmentStatus.PENDING,
            ),
        )
        await store.save_todo_obligation(
            TodoObligation(
                obligation_id="todo_primary",
                kernel_session_id=session.kernel_session_id,
                task_id=task.task_id,
                statement="new todo",
                status=CommitmentStatus.PENDING,
                requires_confirmation=True,
                related_task_brief_version=5,
            )
        )

        intent = await store.get_intent(session.kernel_session_id)
        plan = await store.get_plan(session.kernel_session_id)
        beliefs = await store.get_beliefs(session.kernel_session_id)
        commitments = await store.get_commitments(session.kernel_session_id)

        assert intent.intent_version == 5
        assert intent.goal == "new goal"
        assert plan.current_step == "new_step"
        assert plan.steps[0].name == "new step"
        assert beliefs[0].claim == "new claim"
        assert beliefs[0].status == BeliefStatus.VERIFIED
        assert commitments[0].statement == "new todo"
        assert commitments[0].requires_confirmation is True

        thinker_view = await engine.get_thinker_view(session.kernel_session_id)
        assert thinker_view["task_brief"]["goal"] == "new goal"
        assert thinker_view["task_flow"]["current_step"] == "new_step"
        assert thinker_view["claims"][0]["claim"] == "new claim"
        assert thinker_view["todos"][0]["statement"] == "new todo"
        assert thinker_view["current_step"]["step_id"] == "new_step"

        for legacy_key in ("intent", "plan", "beliefs", "commitments", "legacy_debug"):
            assert legacy_key not in thinker_view

        debug_view = await engine.get_debug_view(session.kernel_session_id)
        for legacy_key in ("intent", "plan", "beliefs", "commitments"):
            assert legacy_key not in debug_view
        assert debug_view["legacy_debug"]["intent"]["goal"] == "new goal"
        assert debug_view["legacy_debug"]["plan"]["current_step"] == "new_step"
        assert debug_view["legacy_debug"]["beliefs"][0]["claim"] == "new claim"
        assert debug_view["legacy_debug"]["commitments"][0]["statement"] == "new todo"

        manager_view = await engine.get_manager_view(session.kernel_session_id)
        assert manager_view["legacy_debug"]["intent"]["goal"] == "new goal"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_legacy_state_table_writes_are_disabled_by_default():
    store, engine = await build_runtime()
    try:
        session = await engine.create_session(agent_id="agent-no-legacy-write")
        task = await store.create_task(
            session.kernel_session_id,
            title="new only",
            goal="new only",
        )
        await store.update_session_status(
            session.kernel_session_id,
            session.status.value,
            active_task_id=task.task_id,
        )

        await store.save_intent(
            session.kernel_session_id,
            IntentState(intent_version=7, goal="new table only"),
        )
        await store.save_plan(
            session.kernel_session_id,
            PlanState(
                plan_id="plan_new_only",
                status=PlanStatus.ACTIVE,
                current_step="s1",
                steps=[
                    PlanStep(
                        step_id="s1",
                        name="write new tables",
                        status=StepStatus.RUNNING,
                    )
                ],
                intent_version=7,
            ),
        )
        await store.save_belief(
            session.kernel_session_id,
            BeliefItem(
                belief_id="claim_new_only",
                claim="new table claim",
                status=BeliefStatus.VERIFIED,
                confidence=0.9,
            ),
        )
        await store.save_commitment(
            session.kernel_session_id,
            Commitment(
                commitment_id="todo_new_only",
                statement="new table todo",
                status=CommitmentStatus.PENDING,
            ),
        )

        assert await count_rows(store, "task_brief_states", session.kernel_session_id) == 1
        assert await count_rows(store, "task_flows", session.kernel_session_id) == 1
        assert await count_rows(store, "claim_items", session.kernel_session_id) == 1
        assert await count_rows(store, "todo_obligations", session.kernel_session_id) == 1
        assert await count_rows(store, "intent_states", session.kernel_session_id) == 0
        assert await count_rows(store, "plan_states", session.kernel_session_id) == 0
        assert await count_rows(store, "belief_items", session.kernel_session_id) == 0
        assert await count_rows(store, "commitments", session.kernel_session_id) == 0

        thinker_view = await engine.get_thinker_view(session.kernel_session_id)
        assert thinker_view["task_brief"]["goal"] == "new table only"
        assert thinker_view["task_flow"]["flow_id"] == "plan_new_only"
        assert thinker_view["claims"][0]["claim"] == "new table claim"
        assert thinker_view["todos"][0]["statement"] == "new table todo"
        assert await store.get_legacy_state_fallback_audit() == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_legacy_state_fallback_audit_records_compat_reads():
    store, engine = await build_runtime()
    try:
        session = await store.create_session(agent_id="agent-legacy-fallback-audit")
        session_id = session.kernel_session_id
        await store.conn.execute(
            """INSERT INTO intent_states
               (kernel_session_id, intent_version, goal, constraints,
                output_format, priority, cancelled, last_user_update_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                2,
                "legacy goal",
                json.dumps([], ensure_ascii=False),
                "",
                "normal",
                0,
                None,
                "2026-06-23T00:00:00+00:00",
            ),
        )
        await store.conn.execute(
            """INSERT INTO plan_states
               (kernel_session_id, plan_id, status, current_step, steps,
                intent_version, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                "legacy_plan",
                PlanStatus.ACTIVE.value,
                "legacy_step",
                json.dumps(
                    [
                        {
                            "step_id": "legacy_step",
                            "name": "legacy step",
                            "status": StepStatus.RUNNING.value,
                        }
                    ],
                    ensure_ascii=False,
                ),
                2,
                "2026-06-23T00:00:00+00:00",
            ),
        )
        await store.conn.execute(
            """INSERT INTO belief_items
               (belief_id, kernel_session_id, claim, status, confidence,
                supporting_evidence, conflicting_evidence, visibility,
                last_verified_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "legacy_claim",
                session_id,
                "legacy claim",
                BeliefStatus.VERIFIED.value,
                0.8,
                json.dumps([], ensure_ascii=False),
                json.dumps([], ensure_ascii=False),
                "shared",
                None,
                "2026-06-23T00:00:00+00:00",
            ),
        )
        await store.conn.execute(
            """INSERT INTO commitments
               (commitment_id, kernel_session_id, statement, created_by,
                status, requires_confirmation, related_intent_version,
                resolved_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "legacy_todo",
                session_id,
                "legacy todo",
                "talker",
                CommitmentStatus.PENDING.value,
                1,
                2,
                None,
                "2026-06-23T00:00:00+00:00",
            ),
        )
        await store.conn.commit()

        assert (await store.get_intent(session_id)).goal == "legacy goal"
        assert (await store.get_plan(session_id)).plan_id == "legacy_plan"
        assert (await store.get_beliefs(session_id))[0].claim == "legacy claim"
        assert (await store.get_commitments(session_id))[0].statement == "legacy todo"

        audit = await store.get_legacy_state_fallback_audit()
        by_model = {
            row["model"]: row
            for row in audit
        }
        assert by_model["task_brief"]["legacy_table"] == "intent_states"
        assert by_model["task_flow"]["legacy_table"] == "plan_states"
        assert by_model["claim"]["legacy_table"] == "belief_items"
        assert by_model["todo"]["legacy_table"] == "commitments"
        assert all(row["hit_count"] == 1 for row in by_model.values())
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_manager_view_risks_and_blocking_use_task_first_state():
    store, engine = await build_runtime()
    try:
        session = await engine.create_session(agent_id="agent-primary-view")
        task = await store.create_task(
            session.kernel_session_id,
            title="view primary",
            goal="view primary",
        )
        await store.update_session_status(
            session.kernel_session_id,
            session.status.value,
            active_task_id=task.task_id,
        )
        await store.save_task_brief(
            TaskBriefState(
                kernel_session_id=session.kernel_session_id,
                task_id=task.task_id,
                task_brief_version=3,
                goal="view primary",
            )
        )
        await store.save_claim_item(
            ClaimItem(
                claim_id="claim_view",
                kernel_session_id=session.kernel_session_id,
                task_id=task.task_id,
                claim="new conflict",
                status=BeliefStatus.CONFLICTING,
            )
        )
        await store.save_todo_obligation(
            TodoObligation(
                obligation_id="todo_view",
                kernel_session_id=session.kernel_session_id,
                task_id=task.task_id,
                statement="new confirmation",
                status=CommitmentStatus.PENDING,
                requires_confirmation=True,
                related_task_brief_version=3,
            )
        )

        view = await engine.get_manager_view(session.kernel_session_id)

        assert view["blocking_reason"] == "awaiting_user_confirmation"
        assert view["pending_confirmations"] == ["new confirmation"]
        assert "claim_conflict:new conflict" in view["risks"]
        assert "awaiting_confirmation:new confirmation" in view["risks"]
    finally:
        await store.close()
