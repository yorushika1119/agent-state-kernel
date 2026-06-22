from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api import server as api_server
from src.kernel.engine import KernelEngine
from src.kms.manager import KmsManager
from src.stores.sqlite_store import SqliteStore


async def build_runtime(*, enable_llm_router: bool = False):
    store = SqliteStore(":memory:")
    await store.connect()
    engine = KernelEngine(store)
    manager = KmsManager(store, engine, enable_llm_router=enable_llm_router)
    return store, engine, manager


@pytest.mark.asyncio
async def test_dispatch_records_user_message_as_task_conversation_ref():
    store, engine, manager = await build_runtime()
    try:
        first = await manager.dispatch_user_message(
            text="请研究 DeepSeek API key 轮换方案",
            runtime_session_id="rt-task-conversation",
            agent_id="agent-conversation",
            runtime_refs={"message_id": "msg_first"},
        )
        second = await manager.dispatch_user_message(
            text="这个任务现在进度怎么样",
            runtime_session_id="rt-task-conversation",
            agent_id="agent-conversation",
            runtime_refs={"message_id": "msg_second"},
        )

        user_refs = await store.list_task_conversation_refs(
            user_session_id=first.user_session_id,
            task_id=first.task_id,
            role="user",
            limit=10,
        )
        assistant_refs = await store.list_task_conversation_refs(
            user_session_id=first.user_session_id,
            task_id=first.task_id,
            role="assistant",
            limit=10,
        )
        observer_view = await engine.get_observer_view(first.kernel_session_id)
        manager_view = await engine.get_manager_view(first.kernel_session_id)

        assert second.requires_thinker is False
        assert second.task_id == first.task_id
        assert [ref.message_ref_id for ref in user_refs] == ["msg_second", "msg_first"]
        assert user_refs[0].text_summary == "这个任务现在进度怎么样"
        assert assistant_refs[0].source == "kernel_direct_response"
        assert assistant_refs[0].text_summary == second.kernel_response
        assert observer_view["recent_conversation_refs"][0]["role"] == "assistant"
        assert manager_view["recent_conversation_refs"][0]["role"] == "assistant"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_complete_dispatch_can_record_assistant_conversation_ref():
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
            dispatched = await client.post(
                "/kms/dispatch-user-message",
                json={
                    "text": "请总结 task conversation refs 的设计",
                    "runtime_session_id": "rt-assistant-ref",
                    "agent_id": "agent-assistant-ref",
                    "runtime_refs": {"message_id": "msg_user_design"},
                },
            )
            data = dispatched.json()
            completed = await client.post(
                f"/kms/thinker/dispatches/{data['thinker_dispatch_id']}/complete",
                json={
                    "session_status": "completed",
                    "response_summary": "已总结 task conversation refs 的设计。",
                    "runtime_refs": {"message_id": "msg_assistant_design"},
                },
            )

        refs = await store.list_task_conversation_refs(
            task_id=data["task_id"],
            role="assistant",
            limit=5,
        )

        assert completed.status_code == 200
        assert refs[0].source == "thinker_dispatch_complete"
        assert refs[0].message_ref_id == "msg_assistant_design"
        assert refs[0].text_summary == "已总结 task conversation refs 的设计。"
    finally:
        api_server._store = previous_store
        api_server._engine = previous_engine
        api_server._kms_manager = previous_kms_manager
        await store.close()


@pytest.mark.asyncio
async def test_task_router_uses_recent_task_conversation_refs_as_hints():
    store, engine, manager = await build_runtime()
    try:
        user_session = await store.observe_user_session(
            user_session_id="user-router-conversation",
            runtime_session_id="rt-router-conversation",
            agent_id="agent-router",
        )
        session_a = await engine.create_session(
            agent_id="agent-router",
            runtime_session_id="rt-router-conversation-a",
        )
        task_a = await store.create_task(
            session_a.kernel_session_id,
            title="任务 A",
            goal="整理资料",
        )
        await store.upsert_global_task_from_snapshot(
            task_a,
            user_session_id=user_session.user_session_id,
            agent_id="agent-router",
        )
        session_b = await engine.create_session(
            agent_id="agent-router",
            runtime_session_id="rt-router-conversation-b",
        )
        task_b = await store.create_task(
            session_b.kernel_session_id,
            title="任务 B",
            goal="整理资料",
        )
        await store.upsert_global_task_from_snapshot(
            task_b,
            user_session_id=user_session.user_session_id,
            agent_id="agent-router",
        )
        await store.create_task_conversation_ref(
            user_session_id=user_session.user_session_id,
            kernel_session_id=session_a.kernel_session_id,
            task_id=task_a.task_id,
            text_summary="DeepSeek API key 更新和轮换方案",
        )

        decision = await manager.dispatch_user_message(
            text="DeepSeek API key 当前进度",
            runtime_session_id="rt-router-conversation",
            user_session_id=user_session.user_session_id,
            agent_id="agent-router",
        )

        assert decision.route_decision == "select_existing"
        assert decision.kernel_session_id == session_a.kernel_session_id
        assert decision.task_id == task_a.task_id
        refs = await store.list_task_conversation_refs(
            user_session_id=user_session.user_session_id,
            task_id=task_a.task_id,
            role="user",
            limit=5,
        )
        assert refs[0].text_summary == "DeepSeek API key 当前进度"
    finally:
        await store.close()
