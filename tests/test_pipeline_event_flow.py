import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kernel.engine import KernelEngine
from src.kms.manager import KmsManager
import src.kms.pipeline as kms_pipeline
from src.kms.pipeline import reduce, register_runtime_references, summarize
from src.schema.events import (
    Actor,
    CognitiveEvent,
    EventSubmission,
    EventType,
    RuntimeRef,
    Visibility,
)
from src.schema.state import (
    BeliefItem,
    BeliefStatus,
    Commitment,
    CommitmentStatus,
    EvidenceItem,
    EvidenceType,
    ExecutionAction,
    IntentState,
    PlanState,
    PlanStatus,
    PlanStep,
    Reliability,
    RuntimeReference,
    StepStatus,
)
from src.stores.sqlite_store import SqliteStore
from src.utils.time import utc_now


async def build_engine():
    store = SqliteStore(":memory:")
    await store.connect()
    return store, KernelEngine(store)


async def build_runtime():
    store, engine = await build_engine()
    return store, engine, KmsManager(store, engine)


def rebuild_event(session_id: str, ev_dict: dict) -> CognitiveEvent:
    payload = ev_dict.get("payload", {})
    if isinstance(payload, str):
        payload = json.loads(payload)

    runtime_refs = ev_dict.get("runtime_refs", {})
    if isinstance(runtime_refs, str):
        runtime_refs = json.loads(runtime_refs or "{}")

    return CognitiveEvent(
        event_id=ev_dict["event_id"],
        kernel_session_id=session_id,
        runtime_session_id=ev_dict.get("runtime_session_id", "") or "",
        run_id=ev_dict.get("run_id", "") or "",
        event_type=EventType(ev_dict["event_type"]),
        actor=Actor(ev_dict["actor"]),
        source_component=ev_dict.get("source_component", ""),
        payload=payload,
        runtime_refs=RuntimeRef(**runtime_refs),
        visibility=Visibility(ev_dict.get("visibility", "shared")),
        intent_version=ev_dict.get("intent_version", 0) or 0,
        state_version=ev_dict.get("state_version", 0) or 0,
    )


@pytest.mark.asyncio
async def test_session_created_event_ids_are_unique_and_progress_initialized():
    store, engine = await build_engine()
    try:
        s1 = await engine.create_session(agent_id="agent-a")
        s2 = await engine.create_session(agent_id="agent-b")

        e1 = await store.get_events(s1.kernel_session_id)
        e2 = await store.get_events(s2.kernel_session_id)
        p1 = await store.get_progress(s1.kernel_session_id)
        p2 = await store.get_progress(s2.kernel_session_id)

        assert e1[0]["event_type"] == EventType.SESSION_CREATED.value
        assert e2[0]["event_type"] == EventType.SESSION_CREATED.value
        assert e1[0]["event_id"] != e2[0]["event_id"]
        assert p1 is not None and p1.status == "idle"
        assert p2 is not None and p2.status == "idle"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_session_links_and_runtime_refs_are_exposed_to_thinker_view():
    store, engine = await build_engine()
    try:
        session = await engine.create_session(
            agent_id="agent-a",
            runtime_id="rt-main",
            runtime_session_id="sess-42",
            runtime_type="codex-cli",
            external_source="jira",
            external_workspace_id="ws-1",
            external_issue_id="issue-7",
            external_task_id="task-9",
        )
        sid = session.kernel_session_id

        ok, _, started = await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="ToolStarted",
                payload={
                    "action_id": "act_1",
                    "tool": "web.search",
                    "input_summary": "search for architecture notes",
                },
                runtime_refs={
                    "tool_call_id": "call_1",
                    "process_session_id": "proc_1",
                },
            )
        )
        assert ok
        assert started.event_type == EventType.TOOL_STARTED

        ok, _, completed = await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="ToolCompleted",
                payload={
                    "action_id": "act_1",
                    "output_summary": "found 3 notes",
                },
                runtime_refs={
                    "tool_result_ref": "result_1",
                },
            )
        )
        assert ok
        assert completed.event_type == EventType.TOOL_COMPLETED

        thinker_view = await engine.get_thinker_view(sid)
        executions = await store.get_executions(sid)

        assert thinker_view["session"]["runtime_id"] == "rt-main"
        assert thinker_view["session"]["runtime_session_id"] == "sess-42"
        assert thinker_view["session"]["runtime_type"] == "codex-cli"
        assert thinker_view["session"]["external_source"] == "jira"
        assert thinker_view["session"]["external_workspace_id"] == "ws-1"
        assert thinker_view["session"]["external_issue_id"] == "issue-7"
        assert thinker_view["session"]["external_task_id"] == "task-9"

        ref_types = {ref["ref_type"] for ref in thinker_view["runtime_references"]}
        assert {"tool_call", "tool_result", "process"} <= ref_types
        assert all(ref["runtime_session_id"] == "sess-42" for ref in thinker_view["runtime_references"])

        assert len(executions) == 1
        assert executions[0].runtime_refs["tool_call_id"] == "call_1"
        assert executions[0].runtime_refs["process_session_id"] == "proc_1"
        assert executions[0].output_ref == "result_1"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_candidate_events_are_logged_before_final_events():
    store, engine = await build_engine()
    try:
        session = await engine.create_session(agent_id="agent-a")
        sid = session.kernel_session_id

        ok, _, intent_event = await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="talker",
                request_type="REGISTER_USER_INTENT_UPDATE",
                payload={"goal": "review the kernel architecture"},
            )
        )
        assert ok
        assert intent_event.event_type == EventType.INTENT_UPDATED

        ok, _, plan_event = await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="PlanProposed",
                intent_version=1,
                payload={
                    "plan_id": "plan_1",
                    "plan": {
                        "steps": [
                            {"step_id": "s1", "name": "collect notes"},
                            {"step_id": "s2", "name": "write report"},
                        ]
                    },
                },
            )
        )
        assert ok
        assert plan_event.event_type == EventType.PLAN_ACCEPTED

        ok, _, evidence_event = await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="EvidenceCandidateFound",
                intent_version=1,
                payload={
                    "evidence_id": "ev_1",
                    "evidence_type": "web_page",
                    "source": "https://example.com/kernel",
                    "title": "Kernel design note",
                    "extracted_facts": ["KMS controls all state writes"],
                    "reliability": "unknown",
                },
            )
        )
        assert ok
        assert evidence_event.event_type == EventType.EVIDENCE_ACCEPTED

        ok, _, belief_event = await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="BeliefProposed",
                intent_version=1,
                payload={
                    "belief_id": "b_1",
                    "claim": "KMS should be the only state write gate",
                    "status": "likely",
                    "confidence": 0.7,
                    "supporting_evidence": ["ev_1"],
                },
            )
        )
        assert ok
        assert belief_event.event_type == EventType.BELIEF_UPDATED

        events = await store.get_events(sid, limit=50)
        event_types = [e["event_type"] for e in events]

        assert EventType.PLAN_PROPOSED.value in event_types
        assert EventType.PLAN_ACCEPTED.value in event_types
        assert event_types.index(EventType.PLAN_PROPOSED.value) < event_types.index(EventType.PLAN_ACCEPTED.value)

        assert EventType.EVIDENCE_CANDIDATE_FOUND.value in event_types
        assert EventType.EVIDENCE_ACCEPTED.value in event_types
        assert event_types.index(EventType.EVIDENCE_CANDIDATE_FOUND.value) < event_types.index(EventType.EVIDENCE_ACCEPTED.value)

        assert EventType.BELIEF_PROPOSED.value in event_types
        assert EventType.BELIEF_UPDATED.value in event_types
        assert event_types.index(EventType.BELIEF_PROPOSED.value) < event_types.index(EventType.BELIEF_UPDATED.value)

        plan = await store.get_plan(sid)
        evidence = await store.get_evidence(sid)
        beliefs = await store.get_beliefs(sid)
        progress = await store.get_progress(sid)

        assert plan is not None and plan.plan_id == "plan_1"
        assert len(evidence) == 1
        assert len(beliefs) == 1
        assert progress is not None and progress.status == "running"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_progress_is_available_without_requesting_talker_view():
    store, engine = await build_engine()
    try:
        session = await engine.create_session(agent_id="agent-a")
        sid = session.kernel_session_id

        await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="talker",
                request_type="REGISTER_USER_INTENT_UPDATE",
                payload={"goal": "write a minimal test"},
            )
        )
        await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="PlanProposed",
                intent_version=1,
                payload={
                    "plan_id": "plan_2",
                    "plan": {"steps": [{"step_id": "s1", "name": "execute"}]},
                },
            )
        )

        progress = await store.get_progress(sid)
        gate_result = await engine.ask_can_say(sid, "already completed")

        assert progress is not None
        assert gate_result["reason"] != "No progress state available"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_rebuild_replays_formal_state_and_runtime_refs():
    store, engine = await build_engine()
    try:
        session = await engine.create_session(
            agent_id="agent-a",
            runtime_session_id="sess-rebuild",
            runtime_type="codex-cli",
        )
        sid = session.kernel_session_id

        await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="talker",
                request_type="REGISTER_USER_INTENT_UPDATE",
                payload={"goal": "verify rebuild"},
            )
        )
        await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="PlanProposed",
                intent_version=1,
                payload={
                    "plan_id": "plan_rebuild",
                    "plan": {"steps": [{"step_id": "s1", "name": "collect evidence"}]},
                },
            )
        )
        await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="ToolStarted",
                payload={
                    "action_id": "act_rebuild",
                    "tool": "browser.open",
                    "input_summary": "open source page",
                },
                runtime_refs={
                    "tool_call_id": "call_rebuild",
                    "process_session_id": "proc_rebuild",
                },
            )
        )
        await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="ToolCompleted",
                payload={
                    "action_id": "act_rebuild",
                    "output_summary": "page content captured",
                },
                runtime_refs={
                    "tool_result_ref": "result_rebuild",
                },
            )
        )
        await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="EvidenceCandidateFound",
                intent_version=1,
                payload={
                    "evidence_id": "ev_rebuild",
                    "evidence_type": "web_page",
                    "source": "https://example.com/rebuild",
                    "title": "Rebuild evidence",
                    "extracted_facts": ["rebuild should depend on event log only"],
                    "reliability": "unknown",
                    "raw_ref": "result_rebuild",
                },
            )
        )
        await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="BeliefProposed",
                intent_version=1,
                payload={
                    "belief_id": "b_rebuild",
                    "claim": "rebuild should only depend on the event log",
                    "status": "likely",
                    "confidence": 0.6,
                    "supporting_evidence": ["ev_rebuild"],
                },
            )
        )

        events_raw = await store.get_events(sid, limit=100)
        await store.clear_derived_state(sid)

        assert await store.get_plan(sid) is None
        assert await store.get_progress(sid) is None
        assert await store.get_evidence(sid) == []
        assert await store.get_beliefs(sid) == []
        assert await store.get_runtime_references(sid) == []

        processed = set()
        for ev_dict in events_raw:
            event = rebuild_event(sid, ev_dict)
            await register_runtime_references(store, sid, event)
            await reduce(store, sid, event, _processed=processed)

        progress = await summarize(store, sid)
        plan = await store.get_plan(sid)
        evidence = await store.get_evidence(sid)
        beliefs = await store.get_beliefs(sid)
        refs = await store.get_runtime_references(sid)

        assert plan is not None and plan.plan_id == "plan_rebuild"
        assert len(evidence) == 1
        assert len(beliefs) == 1
        assert progress is not None and progress.status == "running"
        assert {ref.ref_type for ref in refs} >= {"tool_call", "tool_result", "process"}
        assert all(ref.runtime_session_id == "sess-rebuild" for ref in refs)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_view_governance_separates_thinker_and_debug_views():
    store, engine = await build_engine()
    try:
        session = await engine.create_session(agent_id="agent-a", runtime_session_id="sess-view")
        sid = session.kernel_session_id

        await store.save_intent(
            sid,
            IntentState(
                intent_version=1,
                goal="inspect governed views",
                constraints=["do not send anything externally"],
            ),
        )
        await store.save_plan(
            sid,
            PlanState(
                plan_id="plan_view",
                status=PlanStatus.ACTIVE,
                current_step="s1",
                steps=[
                    PlanStep(step_id="s1", name="inspect", status=StepStatus.RUNNING),
                    PlanStep(step_id="s2", name="report", status=StepStatus.PENDING),
                ],
                intent_version=1,
            ),
        )
        await store.save_evidence(
            sid,
            EvidenceItem(
                evidence_id="ev_view",
                evidence_type=EvidenceType.TOOL_RESULT,
                source="tool://browser",
                title="captured page",
                reliability=Reliability.HIGH,
                extracted_facts=["page says the kernel owns state writes"],
                raw_ref="raw_view_ref",
            ),
        )
        await store.save_belief(
            sid,
            BeliefItem(
                belief_id="b_shared",
                claim="shared belief",
                status=BeliefStatus.VERIFIED,
                confidence=0.9,
                supporting_evidence=["ev_view"],
                visibility="shared",
            ),
        )
        await store.save_belief(
            sid,
            BeliefItem(
                belief_id="b_private",
                claim="private scratch belief",
                status=BeliefStatus.CONFLICTING,
                confidence=0.2,
                visibility="private",
            ),
        )
        await store.save_execution(
            sid,
            ExecutionAction(
                action_id="act_view",
                step_id="s1",
                tool="browser.open",
                status="success",
                input_summary="open the result page",
                output_ref="tool_result_view",
                runtime_refs={"tool_call_id": "call_view"},
                started_at=utc_now(),
                ended_at=utc_now(),
            ),
        )
        await store.save_runtime_ref(
            RuntimeReference(
                kernel_ref_id="rref_shared",
                kernel_session_id=sid,
                runtime_session_id="sess-view",
                runtime_type="codex-cli",
                ref_type="tool_result",
                ref_id="tool_result_view",
                summary="shared runtime result",
                visibility="shared",
            )
        )
        await store.save_runtime_ref(
            RuntimeReference(
                kernel_ref_id="rref_private",
                kernel_session_id=sid,
                runtime_session_id="sess-view",
                runtime_type="codex-cli",
                ref_type="checkpoint",
                ref_id="cp_private",
                summary="private checkpoint",
                visibility="private",
            )
        )

        thinker_view = await engine.get_thinker_view(sid)
        debug_view = await engine.get_debug_view(sid)

        assert thinker_view["current_step"]["step_id"] == "s1"
        assert "raw_ref" not in thinker_view["evidence"][0]
        assert "output_ref" not in thinker_view["executions"][0]
        assert thinker_view["executions"][0]["has_output"] is True
        assert all(b["claim_id"] != "b_private" for b in thinker_view["claims"])
        assert all(r["kernel_ref_id"] != "rref_private" for r in thinker_view["runtime_references"])
        assert "tool_constraints" in thinker_view
        assert "cancellation" in thinker_view
        assert thinker_view["risks"] == []

        assert debug_view["evidence"][0]["raw_ref"] == "raw_view_ref"
        assert debug_view["executions"][0]["output_ref"] == "tool_result_view"
        assert any(b["claim_id"] == "b_private" for b in debug_view["claims"])
        assert any(r["kernel_ref_id"] == "rref_private" for r in debug_view["runtime_references"])
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_hermes_bridge_execution_events_land_in_views_and_sync():
    store, engine, manager = await build_runtime()
    try:
        decision = await manager.dispatch_user_message(
            text="检查 Hermes thinker 事件桥接",
            runtime_session_id="sess-hermes-bridge",
            runtime_type="cli-agent",
            agent_id="agent-a",
        )
        sid = decision.kernel_session_id
        run_id = decision.run_id

        ok, _, _ = await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="ToolStarted",
                run_id=run_id,
                payload={
                    "action_id": "act_ok",
                    "step_id": run_id,
                    "tool": "web.search",
                    "input_summary": "search architecture docs",
                    "runtime_refs": {"tool_call_id": "call_ok"},
                },
            )
        )
        assert ok

        ok, _, _ = await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="ToolCompleted",
                run_id=run_id,
                payload={
                    "action_id": "act_ok",
                    "step_id": run_id,
                    "output_summary": "found design notes",
                    "runtime_refs": {"tool_call_id": "call_ok"},
                },
            )
        )
        assert ok

        ok, _, _ = await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="ToolStarted",
                run_id=run_id,
                payload={
                    "action_id": "act_fail",
                    "step_id": run_id,
                    "tool": "browser.open",
                    "input_summary": "open the live page",
                    "runtime_refs": {"tool_call_id": "call_fail"},
                },
            )
        )
        assert ok

        ok, _, _ = await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="ToolFailed",
                run_id=run_id,
                payload={
                    "action_id": "act_fail",
                    "step_id": run_id,
                    "error": "timeout",
                    "runtime_refs": {"tool_call_id": "call_fail"},
                },
            )
        )
        assert ok

        ok, _, _ = await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="ReasoningSummary",
                run_id=run_id,
                payload={
                    "reasoning_id": "reason_1",
                    "summary": "先确认中断 run，再检查 kernel 视图。",
                },
            )
        )
        assert ok

        ok, _, _ = await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="RawResultAvailable",
                run_id=run_id,
                payload={
                    "result_id": run_id,
                    "ref": "result_run_1",
                    "status": "failed",
                    "summary": "the run stopped after timeout",
                    "error": "timeout",
                },
            )
        )
        assert ok

        thinker_view = await engine.get_thinker_view(sid)
        debug_view = await engine.get_debug_view(sid)
        sync_view = await engine.get_sync_view(sid)

        thinker_exec_by_id = {
            action["action_id"]: action
            for action in thinker_view["executions"]
        }
        assert set(thinker_exec_by_id) == {"act_ok", "act_fail"}
        assert thinker_exec_by_id["act_ok"]["status"] == "success"
        assert thinker_exec_by_id["act_fail"]["status"] == "failed"
        assert all(action["tool"] != "reasoning" for action in thinker_view["executions"])
        assert all(action["tool"] != "raw_result" for action in thinker_view["executions"])
        assert "tool_failed:browser.open" in thinker_view["risks"]

        debug_exec_by_id = {
            action["action_id"]: action
            for action in debug_view["executions"]
        }
        assert debug_exec_by_id["reason_1"]["tool"] == "reasoning"
        assert debug_exec_by_id["reason_1"]["input_summary"] == "先确认中断 run，再检查 kernel 视图。"
        assert debug_exec_by_id[run_id]["tool"] == "raw_result"
        assert debug_exec_by_id[run_id]["input_summary"] == "raw: result_run_1"
        assert debug_exec_by_id["act_fail"]["status"] == "failed"

        assert sync_view is not None
        assert sync_view.blocking_reason == "tool_failed:browser.open"
        debug_refs = debug_view["runtime_references"]
        assert any(
            ref["ref_type"] == "tool_result" and ref["ref_id"] == "result_run_1"
            for ref in debug_refs
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_sync_view_is_minimal_and_exports_final_safe_facts():
    store, engine = await build_engine()
    try:
        session = await engine.create_session(
            agent_id="agent-a",
            runtime_session_id="sess-sync",
            external_task_id="task-sync",
        )
        sid = session.kernel_session_id

        await store.save_plan(
            sid,
            PlanState(
                plan_id="plan_sync",
                status=PlanStatus.COMPLETED,
                current_step="",
                steps=[PlanStep(step_id="s1", name="done", status=StepStatus.COMPLETED)],
                intent_version=1,
            ),
        )
        await store.save_belief(
            sid,
            BeliefItem(
                belief_id="b_sync",
                claim="kernel completed the review",
                status=BeliefStatus.VERIFIED,
                confidence=0.95,
                visibility="shared",
            ),
        )

        sync_view = await engine.get_sync_view(sid)
        payload = sync_view.model_dump()

        assert payload["external_task_id"] == "task-sync"
        assert payload["status"] == "completed"
        assert payload["final_facts"] == ["kernel completed the review"]
        assert payload["pending_confirmations"] == []
        assert set(payload.keys()) == {
            "external_task_id",
            "status",
            "stage",
            "summary",
            "needs_user_input",
            "blocking_reason",
            "pending_confirmations",
            "final_facts",
        }
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_task_terminal_events_ignore_unknown_step_id():
    store, engine = await build_engine()
    try:
        session = await engine.create_session(agent_id="agent-a")
        sid = session.kernel_session_id

        await store.save_plan(
            sid,
            PlanState(
                plan_id="plan_unknown_step",
                status=PlanStatus.ACTIVE,
                current_step="s1",
                steps=[
                    PlanStep(step_id="s1", name="first", status=StepStatus.RUNNING),
                    PlanStep(step_id="s2", name="second", status=StepStatus.PENDING),
                ],
                intent_version=1,
            ),
        )

        await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="TaskCompleted",
                payload={"step_id": "missing_step"},
            )
        )

        plan = await store.get_plan(sid)
        assert plan is not None
        assert plan.current_step == "s1"
        assert plan.steps[0].status == StepStatus.RUNNING
        assert plan.steps[1].status == StepStatus.PENDING
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_task_completed_run_step_id_maps_to_current_plan_step():
    store, engine, manager = await build_runtime()
    try:
        decision = await manager.dispatch_user_message(
            text="执行 run 级 task completed 映射测试",
            runtime_session_id="rt-task-completed-mapping",
            runtime_type="gateway",
            agent_id="agent-a",
        )
        sid = decision.kernel_session_id
        run_id = decision.run_id

        await store.save_plan(
            sid,
            PlanState(
                plan_id="plan_unknown_failed_step",
                status=PlanStatus.ACTIVE,
                current_step="s1",
                steps=[
                    PlanStep(step_id="s1", name="first", status=StepStatus.RUNNING),
                    PlanStep(step_id="s2", name="second", status=StepStatus.PENDING),
                ],
                intent_version=1,
            ),
        )

        ok, reason, event = await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="TaskCompleted",
                run_id=run_id,
                payload={"step_id": run_id},
            )
        )

        assert ok
        assert reason is None
        assert event is not None
        assert event.event_type == EventType.TASK_COMPLETED

        plan = await store.get_plan(sid)
        assert plan is not None
        assert plan.status == PlanStatus.ACTIVE
        assert plan.current_step == "s2"
        assert plan.steps[0].status == StepStatus.COMPLETED
        assert plan.steps[1].status == StepStatus.RUNNING

        events = await store.get_events(sid, limit=50)
        assert any(e["event_type"] == EventType.TASK_COMPLETED.value for e in events)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_task_failed_run_step_id_maps_to_current_plan_step():
    store, engine, manager = await build_runtime()
    try:
        decision = await manager.dispatch_user_message(
            text="执行 run 级 task failed 映射测试",
            runtime_session_id="rt-task-failed-mapping",
            runtime_type="gateway",
            agent_id="agent-a",
        )
        sid = decision.kernel_session_id
        run_id = decision.run_id

        await store.save_plan(
            sid,
            PlanState(
                plan_id="plan_failed_mapping",
                status=PlanStatus.ACTIVE,
                current_step="s1",
                steps=[
                    PlanStep(step_id="s1", name="first", status=StepStatus.RUNNING),
                    PlanStep(step_id="s2", name="second", status=StepStatus.PENDING),
                ],
                intent_version=1,
            ),
        )

        ok, reason, event = await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="TaskFailed",
                run_id=run_id,
                payload={"step_id": run_id, "error": "interrupted by new request"},
            )
        )

        assert ok
        assert reason is None
        assert event is not None
        assert event.event_type == EventType.TASK_FAILED

        plan = await store.get_plan(sid)
        assert plan is not None
        assert plan.status == PlanStatus.BLOCKED
        assert plan.current_step == "s1"
        assert plan.steps[0].status == StepStatus.FAILED
        assert plan.steps[1].status == StepStatus.PENDING

        events = await store.get_events(sid, limit=50)
        assert any(e["event_type"] == EventType.TASK_FAILED.value for e in events)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_talker_summary_prompt_avoids_unsafe_claims(monkeypatch):
    store, engine = await build_engine()
    try:
        session = await engine.create_session(agent_id="agent-a")
        sid = session.kernel_session_id

        await store.save_belief(
            sid,
            BeliefItem(
                belief_id="b_safe",
                claim="safe verified fact",
                status=BeliefStatus.VERIFIED,
                confidence=0.9,
                visibility="shared",
            ),
        )
        await store.save_belief(
            sid,
            BeliefItem(
                belief_id="b_unsafe",
                claim="unsafe conflicting claim",
                status=BeliefStatus.CONFLICTING,
                confidence=0.2,
                visibility="shared",
            ),
        )

        captured = {}

        class FakeModel:
            async def ask(self, system: str, user: str, max_tokens: int):
                captured["prompt"] = user
                return "safe summary sentence"

        monkeypatch.setattr(kms_pipeline, "DEEPSEEK_API_KEY", "test-key")
        monkeypatch.setattr("src.kms.decisioning.model.ModelCall", lambda: FakeModel())

        talker_view = await engine.get_talker_view(sid)

        assert talker_view.summary == "safe summary sentence"
        assert "safe verified fact" in captured["prompt"]
        assert "unsafe conflicting claim" not in captured["prompt"]
        assert "Unresolved items count: 1" in captured["prompt"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dispatch_user_message_creates_run_and_updates_thinker_view():
    store, engine, manager = await build_runtime()
    try:
        decision = await manager.dispatch_user_message(
            text="帮我研究 kernel 调度设计",
            runtime_session_id="rt-dispatch-1",
            runtime_type="gateway",
            agent_id="agent-a",
        )

        assert decision.action == "start_new_task"
        assert decision.kernel_session_id.startswith("ask_")
        assert decision.run_id.startswith("run_")
        assert decision.intent_version == 1

        thinker_view = await engine.get_thinker_view(decision.kernel_session_id)
        assert thinker_view["cancellation"]["active_run_id"] == decision.run_id
        assert thinker_view["cancellation"]["intent_version"] == 1
        assert thinker_view["task_brief"]["goal"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_existing_session_defaults_to_interrupt_and_replan():
    store, engine, manager = await build_runtime()
    try:
        first = await manager.dispatch_user_message(
            text="研究 A 方案",
            runtime_session_id="rt-dispatch-default-reuse",
            runtime_type="gateway",
            agent_id="agent-a",
        )
        assert first.action == "start_new_task"

        assert await engine.complete_run(first.kernel_session_id, first.run_id)

        second = await manager.dispatch_user_message(
            text="再补充一下实现细节",
            runtime_session_id="rt-dispatch-default-reuse",
            runtime_type="gateway",
            agent_id="agent-a",
        )

        assert second.action == "interrupt_and_replan"
        assert second.reason == "reuse_existing_session"
        assert second.kernel_session_id == first.kernel_session_id
        assert second.run_id != first.run_id
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_stale_run_is_rejected_after_interrupt_and_replan():
    store, engine, manager = await build_runtime()
    try:
        first = await manager.dispatch_user_message(
            text="研究 A 方案",
            runtime_session_id="rt-dispatch-2",
            runtime_type="gateway",
            agent_id="agent-a",
        )
        second = await manager.dispatch_user_message(
            text="改成研究 B 方案",
            runtime_session_id="rt-dispatch-2",
            runtime_type="gateway",
            agent_id="agent-a",
        )

        assert second.action == "interrupt_and_replan"
        assert second.kernel_session_id == first.kernel_session_id
        assert second.run_id != first.run_id
        assert second.intent_version == 2

        ok, reason, event = await engine.submit_event(
            EventSubmission(
                session_id=first.kernel_session_id,
                component="thinker",
                request_type="ToolStarted",
                run_id=first.run_id,
                intent_version=second.intent_version,
                payload={
                    "action_id": "act_stale",
                    "tool": "web.search",
                    "input_summary": "stale run should fail",
                },
            )
        )
        assert not ok
        assert event is None
        assert "Stale thinker run" in (reason or "")

        ok, reason, event = await engine.submit_event(
            EventSubmission(
                session_id=first.kernel_session_id,
                component="thinker",
                request_type="ToolStarted",
                run_id=second.run_id,
                intent_version=second.intent_version,
                payload={
                    "action_id": "act_fresh",
                    "tool": "web.search",
                    "input_summary": "fresh run should pass",
                },
            )
        )
        assert ok
        assert reason is None
        assert event is not None
        assert event.run_id == second.run_id
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_run_interrupted_event_is_visible_in_views_and_sync():
    store, engine, manager = await build_runtime()
    try:
        first = await manager.dispatch_user_message(
            text="research the first task",
            runtime_session_id="rt-run-interrupted-view",
            runtime_type="gateway",
            agent_id="agent-a",
        )
        second = await manager.dispatch_user_message(
            text="switch focus to a different angle",
            runtime_session_id="rt-run-interrupted-view",
            runtime_type="gateway",
            agent_id="agent-a",
        )

        sid = first.kernel_session_id
        thinker_view = await engine.get_thinker_view(sid)
        debug_view = await engine.get_debug_view(sid)
        sync_view = await engine.get_sync_view(sid)

        cancellation = thinker_view["cancellation"]
        assert cancellation["active_run_id"] == second.run_id
        assert cancellation["last_interrupted_run_id"] == first.run_id
        assert cancellation["last_interrupting_run_id"] == second.run_id
        assert cancellation["last_interrupt_reason"] == "superseded_by_new_user_message"
        assert cancellation["last_interrupt_at"] is not None

        interrupted_events = [e for e in debug_view["events"] if e["event_type"] == EventType.RUN_INTERRUPTED.value]
        assert len(interrupted_events) == 1
        interrupted = interrupted_events[0]
        assert interrupted["run_id"] == first.run_id
        payload = json.loads(interrupted["payload"]) if isinstance(interrupted["payload"], str) else interrupted["payload"]
        assert payload["interrupted_run_id"] == first.run_id
        assert payload["interrupting_run_id"] == second.run_id
        assert payload["reason"] == "superseded_by_new_user_message"

        assert sync_view is not None
        assert sync_view.blocking_reason == "interrupted_by_new_request"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_explicit_new_task_text_stays_in_same_session():
    store, engine, manager = await build_runtime()
    try:
        first = await manager.dispatch_user_message(
            text="先研究当前问题",
            runtime_session_id="rt-explicit-new-task",
            runtime_type="gateway",
            agent_id="agent-a",
        )

        second = await manager.dispatch_user_message(
            text="这是一个新任务，重新开始另一个问题",
            runtime_session_id="rt-explicit-new-task",
            runtime_type="gateway",
            agent_id="agent-a",
        )

        assert second.action == "start_new_task"
        assert second.kernel_session_id == first.kernel_session_id
        assert second.task_id != first.task_id
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_complete_run_clears_only_matching_active_run():
    store, engine, manager = await build_runtime()
    try:
        first = await manager.dispatch_user_message(
            text="任务 A",
            runtime_session_id="rt-dispatch-3",
            runtime_type="gateway",
            agent_id="agent-a",
        )
        second = await manager.dispatch_user_message(
            text="任务 B",
            runtime_session_id="rt-dispatch-3",
            runtime_type="gateway",
            agent_id="agent-a",
        )

        assert not await engine.complete_run(first.kernel_session_id, first.run_id)

        thinker_view = await engine.get_thinker_view(first.kernel_session_id)
        assert thinker_view["cancellation"]["active_run_id"] == second.run_id

        assert await engine.complete_run(first.kernel_session_id, second.run_id)

        thinker_view = await engine.get_thinker_view(first.kernel_session_id)
        assert thinker_view["cancellation"]["active_run_id"] == ""
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_pipeline_fires_task_blocked_notification():
    """任务某步 TaskFailed → task_flow 进入 blocked → 主动弹 task_blocked 通知。"""
    store, engine = await build_engine()
    try:
        session = await engine.create_session(agent_id="agent-a")
        sid = session.kernel_session_id

        await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="talker",
                request_type="REGISTER_USER_INTENT_UPDATE",
                payload={"goal": "research and report"},
            )
        )
        await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="PlanProposed",
                intent_version=1,
                payload={
                    "plan_id": "plan_block",
                    "plan": {"steps": [{"step_id": "s1", "name": "search"}]},
                },
            )
        )
        await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="TaskFailed",
                intent_version=1,
                payload={"step_id": "s1", "error": "tool timed out"},
            )
        )

        # 先确认状态确实进了 blocked（这是评估器触发的前提）
        task_flow = await store.get_task_flow(sid)
        assert task_flow is not None and task_flow.status == PlanStatus.BLOCKED

        notifs = await store.list_observer_notifications(kernel_session_id=sid, status="")
        types = [n.notification_type for n in notifs]
        assert "task_blocked" in types
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_pipeline_fires_needs_user_input_notification():
    """有"没核实的判断"但没有任何可信事实 → 主动弹 needs_user_input 通知。"""
    store, engine = await build_engine()
    try:
        session = await engine.create_session(agent_id="agent-a")
        sid = session.kernel_session_id

        await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="talker",
                request_type="REGISTER_USER_INTENT_UPDATE",
                payload={"goal": "research"},
            )
        )
        # unverified + 低置信 → 进 unsafe_claims，且没有任何 safe_facts → needs_user_input
        await engine.submit_event(
            EventSubmission(
                session_id=sid,
                component="thinker",
                request_type="BeliefProposed",
                intent_version=1,
                payload={
                    "belief_id": "b_unsure",
                    "claim": "还不能确定的初步判断",
                    "status": "unverified",
                    "confidence": 0.3,
                },
            )
        )

        # 先确认状态确实进了 needs_user_input
        progress = await store.get_progress(sid)
        assert progress is not None and progress.needs_user_input is True

        notifs = await store.list_observer_notifications(kernel_session_id=sid, status="")
        types = [n.notification_type for n in notifs]
        assert "needs_user_input" in types
    finally:
        await store.close()
