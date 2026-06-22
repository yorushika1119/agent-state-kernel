from __future__ import annotations

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
