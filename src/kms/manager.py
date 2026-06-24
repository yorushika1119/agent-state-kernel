"""KMS runtime message dispatch manager."""

from __future__ import annotations

import time
from typing import Optional

from src.kms.dispatch.decision import (
    DispatchDecision,
    thinker_run_decision_from_execution,
)
from src.kms.manager_components import build_kms_manager_components


class KmsManager:
    """Owns runtime-level user message dispatch and interrupt decisions."""

    def __init__(
        self,
        store,
        engine,
        *,
        enable_llm_router: bool | None = None,
        enable_llm_intent: bool | None = None,
    ):
        self.store = store
        self.engine = engine
        components = build_kms_manager_components(
            store,
            engine,
            enable_llm_router=enable_llm_router,
            enable_llm_intent=enable_llm_intent,
        )
        self.sessions = components.sessions
        self.direct_responder = components.direct_responder
        self.interrupts = components.interrupts
        self.resumes = components.resumes
        self.task_switches = components.task_switches
        self.task_dispatch_planner = components.task_dispatch_planner
        self.lifecycle = components.lifecycle
        self.conversation_refs = components.conversation_refs
        self.thinker_dispatches = components.thinker_dispatches
        self.route_clarifications = components.route_clarifications
        self.direct_replies = components.direct_replies
        self.dispatch_responses = components.dispatch_responses
        self.dispatch_execution = components.dispatch_execution
        self.enable_llm_router = components.enable_llm_router
        self.task_router = components.task_router
        self.dispatch_preparation = components.dispatch_preparation

    async def dispatch_user_message(
        self,
        *,
        text: str,
        runtime_session_id: str = "",
        runtime_id: str = "",
        runtime_type: str = "cli-agent",
        agent_id: str = "",
        external_source: str = "",
        external_workspace_id: str = "",
        external_issue_id: str = "",
        external_task_id: str = "",
        target_session_id: str = "",
        user_session_id: str = "",
        mode: str = "auto",
        runtime_refs: Optional[dict] = None,
        debug_timings: bool = False,
    ) -> DispatchDecision:
        total_started_at = time.perf_counter()
        timings: list[dict] = []

        def record_timing(step: str, started_at: float) -> None:
            if not debug_timings:
                return
            now = time.perf_counter()
            timings.append(
                {
                    "step": step,
                    "duration_s": round(now - started_at, 6),
                    "total_s": round(now - total_started_at, 6),
                }
            )

        def attach_timings(decision: DispatchDecision) -> DispatchDecision:
            if debug_timings:
                decision.debug_timings = timings
            return decision

        started_at = time.perf_counter()
        prepared = await self.dispatch_preparation.prepare(
            text=text,
            runtime_session_id=runtime_session_id,
            runtime_id=runtime_id,
            runtime_type=runtime_type,
            agent_id=agent_id,
            target_session_id=target_session_id,
            user_session_id=user_session_id,
            mode=mode,
        )
        record_timing("prepare", started_at)
        user_session = prepared.user_session
        route = prepared.route
        route_target_task = prepared.route_target_task
        session = prepared.session

        started_at = time.perf_counter()
        response = await self.dispatch_responses.pre_execution_response(
            prepared=prepared,
            user_text=text,
            runtime_refs=runtime_refs,
        )
        record_timing("pre_execution_response", started_at)
        if response:
            return attach_timings(response)

        started_at = time.perf_counter()
        execute_started_offset = started_at - total_started_at
        execution = await self.dispatch_execution.execute(
            text=text,
            session=session,
            route=route,
            route_target_task=route_target_task,
            flags=prepared.flags,
            user_session=user_session,
            agent_id=agent_id,
            runtime_id=runtime_id,
            runtime_session_id=runtime_session_id,
            runtime_type=runtime_type,
            external_source=external_source,
            external_workspace_id=external_workspace_id,
            external_issue_id=external_issue_id,
            external_task_id=external_task_id,
            runtime_refs=runtime_refs,
            debug_timings=debug_timings,
        )
        record_timing("execute", started_at)
        if debug_timings:
            for item in execution.debug_timings:
                timings.append(
                    {
                        "step": f"execute.{item['step']}",
                        "duration_s": item["duration_s"],
                        "total_s": round(execute_started_offset + item["total_s"], 6),
                    }
                )

        started_at = time.perf_counter()
        response = await self.dispatch_responses.post_execution_response(
            execution=execution,
            user_text=text,
            user_session_id=user_session.user_session_id,
            route=route,
            runtime_refs=runtime_refs,
        )
        record_timing("post_execution_response", started_at)
        if response:
            return attach_timings(response)

        started_at = time.perf_counter()
        decision = thinker_run_decision_from_execution(
            execution=execution,
            user_session_id=user_session.user_session_id,
            route_decision=route.routing_decision,
        )
        record_timing("decision_build", started_at)
        return attach_timings(decision)
