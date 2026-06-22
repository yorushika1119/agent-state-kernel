from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kms.conversation_ref_coordinator import ConversationRefCoordinator
from src.kms.kernel_direct_reply_coordinator import KernelDirectReplyCoordinator
from src.schema.state import TaskRouteDecision
from src.stores.sqlite_store import SqliteStore


class FakeDirectResponder:
    def __init__(self):
        self.calls = []

    async def build_response(self, session_id: str, kind: str, *, target_task_id: str = ""):
        self.calls.append(
            {
                "session_id": session_id,
                "kind": kind,
                "target_task_id": target_task_id,
            }
        )
        return "当前任务正在执行。"


@pytest.mark.asyncio
async def test_kernel_direct_reply_builds_response_and_records_refs():
    store = SqliteStore(":memory:")
    await store.connect()
    try:
        responder = FakeDirectResponder()
        coordinator = KernelDirectReplyCoordinator(
            responder,
            ConversationRefCoordinator(store),
        )
        session = SimpleNamespace(
            kernel_session_id="ks_direct",
            active_run_id="run_direct",
        )
        route = TaskRouteDecision(
            route_id="route_direct",
            routing_decision="select_existing",
            target_task_id="task_direct",
        )

        response = await coordinator.build_and_record(
            session=session,
            user_text="这个任务现在怎么样？",
            user_session_id="user_direct",
            route=route,
            kind="progress",
            target_task_id="task_direct",
            runtime_refs={"message_id": "msg_user_direct"},
        )

        refs = await store.list_task_conversation_refs(
            user_session_id="user_direct",
            task_id="task_direct",
            limit=10,
        )

        assert response == "当前任务正在执行。"
        assert responder.calls == [
            {
                "session_id": "ks_direct",
                "kind": "progress",
                "target_task_id": "task_direct",
            }
        ]
        assert [ref.role for ref in refs] == ["assistant", "user"]
        assert refs[0].source == "kernel_direct_response"
        assert refs[0].text_summary == response
        assert refs[0].metadata["kernel_answer_kind"] == "progress"
        assert refs[1].message_ref_id == "msg_user_direct"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_kernel_direct_static_reply_records_reason_metadata():
    store = SqliteStore(":memory:")
    await store.connect()
    try:
        coordinator = KernelDirectReplyCoordinator(
            FakeDirectResponder(),
            ConversationRefCoordinator(store),
        )
        session = SimpleNamespace(
            kernel_session_id="ks_static",
            active_run_id="run_static",
        )
        route = TaskRouteDecision(
            route_id="route_static",
            routing_decision="create_new",
        )

        await coordinator.record_static_reply(
            session=session,
            user_text="继续刚才的任务",
            response_text="当前没有可继续的已挂起任务。",
            user_session_id="user_static",
            route=route,
            task_id="task_static",
            metadata={"reason": "no_paused_task_to_resume"},
        )

        refs = await store.list_task_conversation_refs(
            user_session_id="user_static",
            task_id="task_static",
            limit=10,
        )

        assert [ref.role for ref in refs] == ["assistant", "user"]
        assert refs[0].metadata["reason"] == "no_paused_task_to_resume"
        assert refs[1].metadata["task_action"] == "respond_from_kernel"
    finally:
        await store.close()
