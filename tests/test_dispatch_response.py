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


@pytest.mark.asyncio
async def test_dispatch_response_no_resume_records_direct_reply_decision():
    store, engine, manager = await build_runtime()
    try:
        session = await engine.create_session(
            runtime_session_id="rt-response-no-resume",
            runtime_type="gateway",
            agent_id="agent-response",
        )
        user_session = await store.observe_user_session(
            runtime_session_id="rt-response-no-resume",
            runtime_type="gateway",
            agent_id="agent-response",
        )
        route = TaskRouteDecision(
            user_session_id=user_session.user_session_id,
            runtime_session_id="rt-response-no-resume",
            routing_decision="select_existing",
        )

        decision = await manager.dispatch_responses.no_resume_task(
            session=session,
            user_text="continue previous task",
            user_session_id=user_session.user_session_id,
            route=route,
            task_brief_version=session.intent_version,
        )

        assert decision.action == "respond_from_kernel"
        assert decision.requires_thinker is False
        assert decision.reason == "no_paused_task_to_resume"
        assert decision.task_action == "respond_from_kernel"
        assert decision.kernel_response == "当前没有可继续的已挂起任务。"

        refs = await store.list_task_conversation_refs(
            user_session_id=user_session.user_session_id,
            limit=10,
        )
        assert [ref.role for ref in refs] == ["assistant", "user"]
        assert refs[0].metadata["reason"] == "no_paused_task_to_resume"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dispatch_response_clarification_does_not_require_thinker():
    store, engine, manager = await build_runtime()
    try:
        session = await engine.create_session(
            runtime_session_id="rt-response-clarify",
            runtime_type="gateway",
            agent_id="agent-response",
        )
        user_session = await store.observe_user_session(
            runtime_session_id="rt-response-clarify",
            runtime_type="gateway",
            agent_id="agent-response",
        )
        route = TaskRouteDecision(
            user_session_id=user_session.user_session_id,
            runtime_session_id="rt-response-clarify",
            routing_decision="ambiguous",
            needs_user_clarification=True,
            clarification_question="你指的是哪一个任务？",
            candidate_tasks=[
                {"task_id": "task_a", "title": "任务 A", "status": "paused"},
            ],
        )

        decision = await manager.dispatch_responses.clarification(
            session=session,
            user_text="继续那个任务",
            user_session_id=user_session.user_session_id,
            route=route,
        )

        refreshed = await store.get_session(session.kernel_session_id)
        dispatches = await store.list_thinker_dispatches(
            kernel_session_id=session.kernel_session_id,
        )

        assert decision.action == "respond_from_kernel"
        assert decision.task_action == "ask_clarification"
        assert decision.requires_thinker is False
        assert "任务 A" in decision.kernel_response
        assert refreshed.active_run_id == ""
        assert dispatches == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dispatch_response_kernel_direct_reply_uses_target_task():
    store, engine, manager = await build_runtime()
    try:
        session = await engine.create_session(
            runtime_session_id="rt-response-direct",
            runtime_type="gateway",
            agent_id="agent-response",
        )
        user_session = await store.observe_user_session(
            runtime_session_id="rt-response-direct",
            runtime_type="gateway",
            agent_id="agent-response",
        )
        route = TaskRouteDecision(
            user_session_id=user_session.user_session_id,
            runtime_session_id="rt-response-direct",
            routing_decision="select_existing",
            target_task_id="task_direct",
        )

        async def _build_and_record(**kwargs):
            assert kwargs["target_task_id"] == "task_direct"
            return "当前任务正在执行。"

        manager.dispatch_responses.direct_replies = SimpleNamespace(
            build_and_record=_build_and_record,
        )

        decision = await manager.dispatch_responses.kernel_direct_reply(
            session=session,
            user_text="现在进度如何？",
            user_session_id=user_session.user_session_id,
            route=route,
            reason="kernel_direct_status_reply",
            kind="progress",
            target_task_id="task_direct",
        )

        assert decision.action == "respond_from_kernel"
        assert decision.requires_thinker is False
        assert decision.task_id == "task_direct"
        assert decision.kernel_response == "当前任务正在执行。"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dispatch_response_pre_execution_handles_kernel_direct_reply():
    store, engine, manager = await build_runtime()
    try:
        session = await engine.create_session(
            runtime_session_id="rt-response-pre-direct",
            runtime_type="gateway",
            agent_id="agent-response",
        )
        session.active_task_id = "task_active"
        user_session = await store.observe_user_session(
            runtime_session_id="rt-response-pre-direct",
            runtime_type="gateway",
            agent_id="agent-response",
        )
        route = TaskRouteDecision(
            user_session_id=user_session.user_session_id,
            runtime_session_id="rt-response-pre-direct",
            routing_decision="select_existing",
            target_task_id="task_selected",
        )

        async def _build_and_record(**kwargs):
            assert kwargs["target_task_id"] == "task_selected"
            assert kwargs["kind"] == "progress"
            return "任务进度：正在执行。"

        manager.dispatch_responses.direct_replies = SimpleNamespace(
            build_and_record=_build_and_record,
        )
        prepared = SimpleNamespace(
            user_session=user_session,
            route=route,
            session=session,
            intent=SimpleNamespace(
                reason="kernel_direct_status_reply",
                kernel_answer_kind="progress",
            ),
            flags=SimpleNamespace(
                route_clarification_applies=False,
                wants_kernel_response=True,
            ),
        )

        decision = await manager.dispatch_responses.pre_execution_response(
            prepared=prepared,
            user_text="现在进度如何？",
        )

        assert decision.action == "respond_from_kernel"
        assert decision.task_action == "respond_from_kernel"
        assert decision.task_id == "task_selected"
        assert decision.kernel_response == "任务进度：正在执行。"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dispatch_response_post_execution_handles_no_resume_task():
    store, engine, manager = await build_runtime()
    try:
        session = await engine.create_session(
            runtime_session_id="rt-response-post-no-resume",
            runtime_type="gateway",
            agent_id="agent-response",
        )
        user_session = await store.observe_user_session(
            runtime_session_id="rt-response-post-no-resume",
            runtime_type="gateway",
            agent_id="agent-response",
        )
        route = TaskRouteDecision(
            user_session_id=user_session.user_session_id,
            runtime_session_id="rt-response-post-no-resume",
            routing_decision="select_existing",
        )
        execution = SimpleNamespace(
            session=session,
            task_brief_version=session.intent_version,
            task_plan=SimpleNamespace(no_resume_task=True),
        )

        decision = await manager.dispatch_responses.post_execution_response(
            execution=execution,
            user_text="继续刚才的任务",
            user_session_id=user_session.user_session_id,
            route=route,
        )

        assert decision.action == "respond_from_kernel"
        assert decision.reason == "no_paused_task_to_resume"
        assert decision.kernel_response == "当前没有可继续的已挂起任务。"
    finally:
        await store.close()
