from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kernel.engine import KernelEngine
from src.kms.dispatch.preparation import DispatchPreparationCoordinator
from src.kms.kernel_session_coordinator import KernelSessionCoordinator
from src.schema.state import TaskRouteDecision
from src.stores.sqlite_store import SqliteStore


@pytest.mark.asyncio
async def test_dispatch_preparation_builds_routing_session_and_intent_flags():
    store = SqliteStore(":memory:")
    await store.connect()
    try:
        engine = KernelEngine(store)
        session = await engine.create_session(
            runtime_session_id="rt-prep",
            runtime_type="gateway",
            agent_id="agent-prep",
        )
        user_session = await store.observe_user_session(
            runtime_session_id="rt-prep",
            runtime_type="gateway",
            agent_id="agent-prep",
        )
        route = TaskRouteDecision(
            user_session_id=user_session.user_session_id,
            runtime_session_id="rt-prep",
            routing_decision="ambiguous",
            needs_user_clarification=True,
            clarification_question="Which task?",
        )

        class FakeRouter:
            async def route_message(self, text: str, **kwargs):
                assert text == "what is the progress?"
                return type(
                    "Routing",
                    (),
                    {
                        "user_session": user_session,
                        "route": route,
                        "route_target_task": None,
                        "routed_session_id": session.kernel_session_id,
                    },
                )()

        coordinator = DispatchPreparationCoordinator(
            FakeRouter(),
            KernelSessionCoordinator(store, engine),
        )

        prepared = await coordinator.prepare(
            text="what is the progress?",
            runtime_session_id="rt-prep",
        )

        assert prepared.user_session.user_session_id == user_session.user_session_id
        assert prepared.session.kernel_session_id == session.kernel_session_id
        assert prepared.intent.intent == "kernel_answerable_query"
        assert prepared.flags.wants_kernel_response is True
        assert prepared.flags.route_clarification_applies is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dispatch_preparation_new_task_skips_route_clarification():
    store = SqliteStore(":memory:")
    await store.connect()
    try:
        engine = KernelEngine(store)
        user_session = await store.observe_user_session(
            runtime_session_id="rt-prep-new",
            runtime_type="gateway",
            agent_id="agent-prep",
        )
        route = TaskRouteDecision(
            user_session_id=user_session.user_session_id,
            runtime_session_id="rt-prep-new",
            routing_decision="ambiguous",
            needs_user_clarification=True,
            clarification_question="Which task?",
        )

        class FakeRouter:
            async def route_message(self, text: str, **kwargs):
                return type(
                    "Routing",
                    (),
                    {
                        "user_session": user_session,
                        "route": route,
                        "route_target_task": None,
                        "routed_session_id": "",
                    },
                )()

        coordinator = DispatchPreparationCoordinator(
            FakeRouter(),
            KernelSessionCoordinator(store, engine),
        )

        prepared = await coordinator.prepare(
            text="new task: research cache invalidation",
            runtime_session_id="rt-prep-new",
        )

        assert prepared.session is None
        assert prepared.intent.intent == "new_task"
        assert prepared.flags.explicit_new_task_requested is True
        assert prepared.flags.route_clarification_applies is False
    finally:
        await store.close()
