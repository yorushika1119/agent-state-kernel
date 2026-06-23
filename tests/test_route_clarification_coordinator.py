from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kms.response.clarification import RouteClarificationCoordinator
from src.schema.state import TaskRouteDecision
from src.stores.sqlite_store import SqliteStore


@pytest.mark.asyncio
async def test_route_clarification_response_and_refs_are_recorded():
    store = SqliteStore(":memory:")
    await store.connect()
    try:
        coordinator = RouteClarificationCoordinator(store)
        route = TaskRouteDecision(
            route_id="route_clarify_1",
            user_session_id="user_clarify",
            routing_decision="ask_clarification",
            candidate_tasks=[
                {
                    "task_id": "task_a",
                    "title": "任务 A：打断机制",
                    "status": "active",
                },
                {
                    "task_id": "task_b",
                    "task_description": "任务 B：任务路由",
                    "status": "paused",
                },
            ],
            needs_user_clarification=True,
        )

        response = coordinator.build_response(route)
        await coordinator.record_exchange(
            user_text="那个现在怎么样？",
            response_text=response,
            user_session_id="user_clarify",
            kernel_session_id="ks_clarify",
            route=route,
            runtime_refs={"message_id": "msg_user"},
        )

        refs = await store.list_task_conversation_refs(
            user_session_id="user_clarify",
            limit=10,
        )

        assert "你指的是哪一个任务？" in response
        assert "1. 任务 A：打断机制（active）" in response
        assert "2. 任务 B：任务路由（paused）" in response
        assert [ref.role for ref in refs] == ["assistant", "user"]
        assert refs[0].source == "kernel_route_clarification"
        assert refs[0].text_summary == response
        assert refs[1].message_ref_id == "msg_user"
        assert refs[1].metadata["task_action"] == "ask_clarification"
    finally:
        await store.close()
