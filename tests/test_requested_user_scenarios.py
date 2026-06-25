"""User-requested KMS dispatch scenarios."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.kms.dispatch.preparation as kms_preparation
from src.kernel.engine import KernelEngine
from src.kms.decisioning.intent_classifier import DispatchIntent
from src.kms.manager import KmsManager
from src.schema.events import EventSubmission, EventType
from src.stores.sqlite_store import SqliteStore


@dataclass
class PureHermesBaseline:
    """Old architecture baseline: thinker transcript only, no KMS/kernel state."""

    transcript: list[str] = field(default_factory=list)
    active_run_id: str = ""
    run_counter: int = 0

    def user_message(self, text: str) -> str:
        self.transcript.append(f"user:{text}")
        if not self.active_run_id:
            self.run_counter += 1
            self.active_run_id = f"old_run_{self.run_counter}"
        return self.active_run_id

    def has_kernel_direct_reply(self) -> bool:
        return False

    def has_paused_task_snapshot(self) -> bool:
        return False

    def has_resume_context(self) -> bool:
        return False

    def rejects_stale_run(self) -> bool:
        return False


async def build_runtime():
    store = SqliteStore(":memory:")
    await store.connect()
    engine = KernelEngine(store)
    manager = KmsManager(store, engine)
    return store, engine, manager


@pytest.mark.asyncio
async def test_status_question_responds_from_kernel_and_original_thinker_continues():
    old = PureHermesBaseline()
    old_first_run = old.user_message("请执行第一个长任务，整理实时打断机制材料")
    old_second_run = old.user_message("现在完成到哪一步了？")

    assert old_second_run == old_first_run
    assert not old.has_kernel_direct_reply()

    store, engine, manager = await build_runtime()
    try:
        first = await manager.dispatch_user_message(
            text="请执行第一个长任务，整理实时打断机制材料",
            runtime_session_id="rt-user-status-direct",
            runtime_type="gateway",
            agent_id="agent-user-scenario",
        )

        ok, reason, _ = await engine.submit_event(
            EventSubmission(
                session_id=first.kernel_session_id,
                component="thinker",
                request_type="ToolStarted",
                run_id=first.run_id,
                payload={
                    "action_id": "act_first_collect",
                    "tool": "local.read",
                    "input_summary": "读取实时打断机制材料",
                },
            )
        )
        assert ok, reason

        second = await manager.dispatch_user_message(
            text="现在完成到哪一步了？",
            runtime_session_id="rt-user-status-direct",
            runtime_type="gateway",
            agent_id="agent-user-scenario",
        )

        assert second.action == "respond_from_kernel"
        assert second.task_action == "respond_from_kernel"
        assert second.requires_thinker is False
        assert second.kernel_session_id == first.kernel_session_id
        assert second.run_id == first.run_id
        assert second.kernel_response

        thinker_after_question = await engine.get_thinker_view(first.kernel_session_id)
        assert thinker_after_question["cancellation"]["active_run_id"] == first.run_id

        events_after_question = await store.get_events(first.kernel_session_id, limit=50)
        assert all(
            event["event_type"] != EventType.RUN_INTERRUPTED.value
            for event in events_after_question
        )

        ok, reason, completed_event = await engine.submit_event(
            EventSubmission(
                session_id=first.kernel_session_id,
                component="thinker",
                request_type="ToolCompleted",
                run_id=first.run_id,
                payload={
                    "action_id": "act_first_collect",
                    "output_summary": "第一个任务继续执行并完成资料读取",
                },
            )
        )
        assert ok, reason
        assert completed_event is not None
        assert completed_event.run_id == first.run_id

        assert await engine.complete_run(
            first.kernel_session_id,
            first.run_id,
            session_status="completed",
        )

        thinker_after_done = await engine.get_thinker_view(first.kernel_session_id)
        assert thinker_after_done["cancellation"]["active_run_id"] == ""
        assert thinker_after_done["cancellation"]["last_interrupted_run_id"] == ""
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_unrelated_second_request_then_resume_first_task():
    old = PureHermesBaseline()
    old_first_run = old.user_message("请研究任务 A：实时打断机制的状态恢复方案")
    old_second_run = old.user_message("请改成回答一个无关请求：解释一下 Python 装饰器")
    old_third_run = old.user_message("继续刚才的任务")

    assert old_second_run == old_first_run
    assert old_third_run == old_first_run
    assert not old.has_paused_task_snapshot()
    assert not old.has_resume_context()
    assert not old.rejects_stale_run()

    store, engine, manager = await build_runtime()
    try:
        first = await manager.dispatch_user_message(
            text="请研究任务 A：实时打断机制的状态恢复方案",
            runtime_session_id="rt-user-resume-first",
            runtime_type="gateway",
            agent_id="agent-user-scenario",
        )

        ok, reason, _ = await engine.submit_event(
            EventSubmission(
                session_id=first.kernel_session_id,
                component="thinker",
                request_type="PlanProposed",
                run_id=first.run_id,
                intent_version=first.intent_version,
                payload={
                    "plan_id": "plan_first_task",
                    "plan": {
                        "steps": [
                            {"step_id": "s1", "name": "整理状态恢复现状"},
                            {"step_id": "s2", "name": "输出恢复方案"},
                        ]
                    },
                },
            )
        )
        assert ok, reason

        second = await manager.dispatch_user_message(
            text="请改成回答一个无关请求：解释一下 Python 装饰器",
            runtime_session_id="rt-user-resume-first",
            runtime_type="gateway",
            agent_id="agent-user-scenario",
        )

        assert second.action == "interrupt_and_replan"
        assert second.task_action == "start_new_task"
        assert second.kernel_session_id == first.kernel_session_id
        assert second.run_id != first.run_id
        assert second.task_id != first.task_id

        view_after_second = await engine.get_thinker_view(first.kernel_session_id)
        assert view_after_second["cancellation"]["active_run_id"] == second.run_id
        assert view_after_second["cancellation"]["last_interrupted_run_id"] == first.run_id
        assert view_after_second["cancellation"]["last_paused_task_id"] == first.task_id

        resume = await manager.dispatch_user_message(
            text="继续刚才的任务",
            runtime_session_id="rt-user-resume-first",
            runtime_type="gateway",
            agent_id="agent-user-scenario",
        )

        assert resume.action == "interrupt_and_replan"
        assert resume.task_action == "continue_paused_task"
        assert resume.reason == "resume_previous_task"
        assert resume.kernel_session_id == first.kernel_session_id
        assert resume.run_id not in {first.run_id, second.run_id}
        assert resume.task_id == first.task_id
        assert resume.resume_context["task_id"] == first.task_id
        assert "实时打断机制" in resume.resume_context["goal"]
        assert "Python 装饰器" not in resume.resume_context["goal"]

        view_after_resume = await engine.get_thinker_view(first.kernel_session_id)
        assert view_after_resume["cancellation"]["active_run_id"] == resume.run_id
        assert view_after_resume["cancellation"]["active_task_id"] == first.task_id
        assert view_after_resume["cancellation"]["last_interrupted_run_id"] == second.run_id
        assert view_after_resume["task_brief"]["goal"] == resume.resume_context["goal"]

        ok, reason, resumed_event = await engine.submit_event(
            EventSubmission(
                session_id=first.kernel_session_id,
                component="thinker",
                request_type="ToolStarted",
                run_id=resume.run_id,
                payload={
                    "action_id": "act_resume_first",
                    "tool": "local.read",
                    "input_summary": "继续第一次任务的状态恢复方案",
                },
            )
        )
        assert ok, reason
        assert resumed_event is not None
        assert resumed_event.run_id == resume.run_id

        ok, reason, stale_event = await engine.submit_event(
            EventSubmission(
                session_id=first.kernel_session_id,
                component="thinker",
                request_type="ToolStarted",
                run_id=second.run_id,
                payload={
                    "action_id": "act_stale_second",
                    "tool": "local.read",
                    "input_summary": "第二个无关任务的迟到写入",
                },
            )
        )
        assert not ok
        assert stale_event is None
        assert "Stale thinker run" in (reason or "")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_kernel_answerable_queries_cover_failures_evidence_run_and_resume_state():
    store, engine, manager = await build_runtime()
    try:
        first = await manager.dispatch_user_message(
            text="请研究任务 A：整理状态治理材料",
            runtime_session_id="rt-user-kernel-answerable",
            runtime_type="gateway",
            agent_id="agent-user-scenario",
        )

        ok, reason, _ = await engine.submit_event(
            EventSubmission(
                session_id=first.kernel_session_id,
                component="thinker",
                request_type="ToolStarted",
                run_id=first.run_id,
                payload={
                    "action_id": "act_failed_read",
                    "tool": "local.read",
                    "input_summary": "读取不存在的文件",
                },
            )
        )
        assert ok, reason

        ok, reason, _ = await engine.submit_event(
            EventSubmission(
                session_id=first.kernel_session_id,
                component="thinker",
                request_type="ToolFailed",
                run_id=first.run_id,
                payload={
                    "action_id": "act_failed_read",
                    "tool": "local.read",
                    "error": "file not found",
                },
            )
        )
        assert ok, reason

        ok, reason, _ = await engine.submit_event(
            EventSubmission(
                session_id=first.kernel_session_id,
                component="thinker",
                request_type="EvidenceCandidateFound",
                run_id=first.run_id,
                intent_version=first.intent_version,
                payload={
                    "evidence_id": "ev_kernel_design",
                    "evidence_type": "file",
                    "source": "Runtime-side Agent State Kernel 功能设计.md",
                    "title": "Kernel 功能设计",
                    "extracted_facts": ["KMS 是唯一状态解释层。"],
                    "reliability": "high",
                },
            )
        )
        assert ok, reason

        failure_reply = await manager.dispatch_user_message(
            text="刚才哪里失败了？",
            runtime_session_id="rt-user-kernel-answerable",
            runtime_type="gateway",
            agent_id="agent-user-scenario",
        )
        assert failure_reply.action == "respond_from_kernel"
        assert failure_reply.requires_thinker is False
        assert "local.read" in failure_reply.kernel_response

        evidence_reply = await manager.dispatch_user_message(
            text="目前有什么证据？",
            runtime_session_id="rt-user-kernel-answerable",
            runtime_type="gateway",
            agent_id="agent-user-scenario",
        )
        assert evidence_reply.action == "respond_from_kernel"
        assert evidence_reply.requires_thinker is False
        assert "ev_kernel_design" in evidence_reply.kernel_response

        run_reply = await manager.dispatch_user_message(
            text="当前 run 是哪个？",
            runtime_session_id="rt-user-kernel-answerable",
            runtime_type="gateway",
            agent_id="agent-user-scenario",
        )
        assert run_reply.action == "respond_from_kernel"
        assert run_reply.requires_thinker is False
        assert first.run_id in run_reply.kernel_response

        second = await manager.dispatch_user_message(
            text="请改成处理另一个无关任务",
            runtime_session_id="rt-user-kernel-answerable",
            runtime_type="gateway",
            agent_id="agent-user-scenario",
        )
        assert second.action == "interrupt_and_replan"

        resume_state_reply = await manager.dispatch_user_message(
            text="上一个任务还能继续吗？",
            runtime_session_id="rt-user-kernel-answerable",
            runtime_type="gateway",
            agent_id="agent-user-scenario",
        )
        assert resume_state_reply.action == "respond_from_kernel"
        assert resume_state_reply.requires_thinker is False
        assert "可以继续" in resume_state_reply.kernel_response

        thinker = await engine.get_thinker_view(first.kernel_session_id)
        assert thinker["cancellation"]["active_run_id"] == second.run_id
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_manager_uses_llm_intent_suggestion_without_letting_it_execute_directly(monkeypatch):
    store, engine, manager = await build_runtime()
    try:
        first = await manager.dispatch_user_message(
            text="请研究任务 A：整理状态治理材料",
            runtime_session_id="rt-user-llm-intent",
            runtime_type="gateway",
            agent_id="agent-user-scenario",
        )

        async def fake_classifier(
            text,
            *,
            mode="auto",
            session=None,
            context=None,
            enable_llm=True,
        ):
            assert session is not None
            assert context is not None
            assert context.has_session
            return DispatchIntent(
                intent="kernel_answerable_query",
                confidence=0.86,
                source="llm",
                reason="复杂表达询问当前证据",
                kernel_answer_kind="evidence",
            )

        monkeypatch.setattr(
            kms_preparation,
            "classify_dispatch_intent_with_llm",
            fake_classifier,
        )

        reply = await manager.dispatch_user_message(
            text="先别动当前任务，我只是想看看手头依据够不够。",
            runtime_session_id="rt-user-llm-intent",
            runtime_type="gateway",
            agent_id="agent-user-scenario",
        )

        assert reply.action == "respond_from_kernel"
        assert reply.requires_thinker is False
        assert reply.reason == "复杂表达询问当前证据"
        assert reply.run_id == first.run_id

        thinker = await engine.get_thinker_view(first.kernel_session_id)
        assert thinker["cancellation"]["active_run_id"] == first.run_id
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_complex_evidence_question_reads_kernel_context_without_interrupting():
    store, engine, manager = await build_runtime()
    try:
        first = await manager.dispatch_user_message(
            text="请研究任务 A：整理状态治理材料",
            runtime_session_id="rt-user-context-aware-query",
            runtime_type="gateway",
            agent_id="agent-user-scenario",
        )

        reply = await manager.dispatch_user_message(
            text="先别动当前任务，我只是想看看手头依据够不够。",
            runtime_session_id="rt-user-context-aware-query",
            runtime_type="gateway",
            agent_id="agent-user-scenario",
        )

        assert reply.action == "respond_from_kernel"
        assert reply.requires_thinker is False
        assert reply.run_id == first.run_id
        assert "还没有可用证据" in reply.kernel_response

        thinker = await engine.get_thinker_view(first.kernel_session_id)
        assert thinker["cancellation"]["active_run_id"] == first.run_id

        events = await store.get_events(first.kernel_session_id, limit=50)
        assert all(
            event["event_type"] != EventType.RUN_INTERRUPTED.value
            for event in events
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_manager_passes_loaded_kernel_context_to_classifier(monkeypatch):
    store, engine, manager = await build_runtime()
    try:
        first = await manager.dispatch_user_message(
            text="请研究任务 A：整理状态治理材料",
            runtime_session_id="rt-user-loaded-context",
            runtime_type="gateway",
            agent_id="agent-user-scenario",
        )

        ok, reason, _ = await engine.submit_event(
            EventSubmission(
                session_id=first.kernel_session_id,
                component="thinker",
                request_type="EvidenceCandidateFound",
                run_id=first.run_id,
                intent_version=first.intent_version,
                payload={
                    "evidence_id": "ev_context",
                    "evidence_type": "file",
                    "source": "context.md",
                    "title": "上下文证据",
                    "extracted_facts": ["KMS 需要读取 Kernel 状态后再仲裁。"],
                    "reliability": "high",
                },
            )
        )
        assert ok, reason

        captured = {}

        async def fake_classifier(
            text,
            *,
            mode="auto",
            session=None,
            context=None,
            enable_llm=True,
        ):
            captured["context"] = context
            return DispatchIntent(
                intent="kernel_answerable_query",
                confidence=0.9,
                source="test",
                reason="context_loaded",
                kernel_answer_kind="evidence",
            )

        monkeypatch.setattr(
            kms_preparation,
            "classify_dispatch_intent_with_llm",
            fake_classifier,
        )

        reply = await manager.dispatch_user_message(
            text="现在有哪些依据？",
            runtime_session_id="rt-user-loaded-context",
            runtime_type="gateway",
            agent_id="agent-user-scenario",
        )

        context = captured["context"]
        assert context.has_session
        assert len(context.evidence) == 1
        assert context.active_task is not None
        assert context.active_task.task_id == first.task_id
        assert reply.action == "respond_from_kernel"
        assert "ev_context" in reply.kernel_response
    finally:
        await store.close()
