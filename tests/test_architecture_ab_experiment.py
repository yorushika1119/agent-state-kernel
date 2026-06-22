"""Deterministic A/B checks for pure Hermes vs Hermes + KMS + Kernel."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kernel.engine import KernelEngine
from src.kms.manager import KmsManager
from src.schema.events import EventSubmission, EventType
from src.stores.sqlite_store import SqliteStore


@dataclass
class PureHermesBaseline:
    """A minimal model of the old architecture: one thinker, no KMS/kernel ledger."""

    transcript: list[str] = field(default_factory=list)
    active_run_id: str = ""
    visible_outputs: list[str] = field(default_factory=list)
    run_counter: int = 0

    def user_message(self, text: str) -> str:
        self.transcript.append(f"user:{text}")
        if not self.active_run_id:
            self.run_counter += 1
            self.active_run_id = f"old_run_{self.run_counter}"
        return self.active_run_id

    def new_user_message_without_dispatch(self, text: str) -> str:
        self.transcript.append(f"user:{text}")
        return self.active_run_id

    def thinker_output(self, run_id: str, text: str) -> None:
        self.transcript.append(f"assistant[{run_id}]:{text}")
        self.visible_outputs.append(text)

    def has_structured_state(self) -> bool:
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
async def test_new_architecture_has_queryable_state_old_architecture_has_transcript_only():
    old = PureHermesBaseline()
    old_run = old.user_message("请调研实时打断机制的状态管理优势")
    old.thinker_output(old_run, "我会收集资料、验证风险、输出结论。")
    old.thinker_output(old_run, "证据显示 kernel 可以结构化记录执行状态。")

    store, engine, manager = await build_runtime()
    try:
        decision = await manager.dispatch_user_message(
            text="请调研实时打断机制的状态管理优势",
            runtime_session_id="rt-ab-state",
            runtime_type="gateway",
            agent_id="agent-ab",
        )
        sid = decision.kernel_session_id

        ok, reason, _ = await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="PlanProposed",
                run_id=decision.run_id,
                intent_version=decision.intent_version,
                payload={
                    "plan_id": "plan_ab_state",
                    "plan": {
                        "steps": [
                            {"step_id": "s1", "name": "收集状态事件"},
                            {"step_id": "s2", "name": "验证打断风险"},
                            {"step_id": "s3", "name": "输出结论"},
                        ]
                    },
                },
            )
        )
        assert ok, reason

        ok, reason, _ = await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="ToolStarted",
                run_id=decision.run_id,
                payload={
                    "action_id": "act_collect_state",
                    "tool": "local.read",
                    "input_summary": "读取 kernel 状态文档",
                },
            )
        )
        assert ok, reason

        ok, reason, _ = await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="ToolCompleted",
                run_id=decision.run_id,
                payload={
                    "action_id": "act_collect_state",
                    "output_summary": "找到状态管理说明",
                },
            )
        )
        assert ok, reason

        ok, reason, _ = await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="EvidenceCandidateFound",
                run_id=decision.run_id,
                intent_version=decision.intent_version,
                payload={
                    "evidence_id": "ev_state_ledger",
                    "evidence_type": "file",
                    "source": "docs/progress/interrupt-mechanism-summary-2026-06-18.md",
                    "title": "实时打断机制总结",
                    "extracted_facts": ["Kernel 记录 session、run、event log、views 和 task snapshot。"],
                    "reliability": "high",
                },
            )
        )
        assert ok, reason

        ok, reason, _ = await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="BeliefProposed",
                run_id=decision.run_id,
                intent_version=decision.intent_version,
                payload={
                    "belief_id": "b_state_advantage",
                    "claim": "新架构可以把执行过程转成可查询状态。",
                    "status": "verified",
                    "confidence": 0.9,
                    "supporting_evidence": ["ev_state_ledger"],
                },
            )
        )
        assert ok, reason

        thinker_view = await engine.get_thinker_view(sid)
        talker_view = await engine.get_talker_view(sid)

        assert not old.has_structured_state()
        assert old.transcript

        assert thinker_view["task_flow"]["flow_id"] == "plan_ab_state"
        assert len(thinker_view["task_flow"]["steps"]) == 3
        assert thinker_view["executions"][0]["action_id"] == "act_collect_state"
        assert thinker_view["evidence"][0]["evidence_id"] == "ev_state_ledger"
        assert thinker_view["claims"][0]["claim_id"] == "b_state_advantage"
        assert talker_view is not None
        assert talker_view.status in {"running", "completed"}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_new_architecture_answers_status_query_without_interrupting_active_run():
    old = PureHermesBaseline()
    old_run = old.user_message("请执行一个长调研任务")
    old_status_run = old.new_user_message_without_dispatch("现在完成到哪一步了？")

    store, engine, manager = await build_runtime()
    try:
        first = await manager.dispatch_user_message(
            text="请执行一个长调研任务",
            runtime_session_id="rt-ab-status",
            runtime_type="gateway",
            agent_id="agent-ab",
        )
        reply = await manager.dispatch_user_message(
            text="现在完成到哪一步了？",
            runtime_session_id="rt-ab-status",
            runtime_type="gateway",
            agent_id="agent-ab",
        )

        assert old_status_run == old_run
        assert "现在完成到哪一步了？" in old.transcript[-1]

        assert reply.action == "respond_from_kernel"
        assert reply.requires_thinker is False
        assert reply.run_id == first.run_id
        assert reply.kernel_response

        thinker_view = await engine.get_thinker_view(first.kernel_session_id)
        assert thinker_view["cancellation"]["active_run_id"] == first.run_id

        events = await store.get_events(first.kernel_session_id, limit=50)
        assert all(event["event_type"] != EventType.RUN_INTERRUPTED.value for event in events)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_new_architecture_interrupts_and_rejects_stale_old_run_events():
    old = PureHermesBaseline()
    old_run = old.user_message("请研究任务 A")
    old.new_user_message_without_dispatch("请改成研究任务 B")
    old.thinker_output(old_run, "任务 A 的迟到结果")

    store, engine, manager = await build_runtime()
    try:
        first = await manager.dispatch_user_message(
            text="请研究任务 A",
            runtime_session_id="rt-ab-interrupt",
            runtime_type="gateway",
            agent_id="agent-ab",
        )
        second = await manager.dispatch_user_message(
            text="请改成研究任务 B",
            runtime_session_id="rt-ab-interrupt",
            runtime_type="gateway",
            agent_id="agent-ab",
        )

        ok_old, reason_old, event_old = await engine.submit_event(
            EventSubmission(
                session_id=first.kernel_session_id,
                component="thinker",
                request_type="ToolCompleted",
                run_id=first.run_id,
                payload={
                    "action_id": "act_old_late",
                    "output_summary": "任务 A 的迟到结果",
                },
            )
        )
        ok_new, reason_new, event_new = await engine.submit_event(
            EventSubmission(
                session_id=first.kernel_session_id,
                component="thinker",
                request_type="ToolStarted",
                run_id=second.run_id,
                payload={
                    "action_id": "act_new",
                    "tool": "local.read",
                    "input_summary": "执行任务 B",
                },
            )
        )

        assert not old.rejects_stale_run()
        assert old.visible_outputs == ["任务 A 的迟到结果"]

        assert second.action == "interrupt_and_replan"
        assert second.run_id != first.run_id

        thinker_view = await engine.get_thinker_view(first.kernel_session_id)
        cancellation = thinker_view["cancellation"]
        assert cancellation["active_run_id"] == second.run_id
        assert cancellation["last_interrupted_run_id"] == first.run_id
        assert cancellation["last_interrupting_run_id"] == second.run_id

        assert not ok_old
        assert event_old is None
        assert "Stale thinker run" in (reason_old or "")
        assert ok_new
        assert reason_new is None
        assert event_new is not None
        assert event_new.run_id == second.run_id
    finally:
        await store.close()
