"""KMS manager component wiring."""

from __future__ import annotations

import os
from dataclasses import dataclass

from src.kms.context.conversation_refs import ConversationRefCoordinator
from src.kms.context.kernel_session import KernelSessionCoordinator
from src.kms.dispatch.execution import DispatchExecutionCoordinator
from src.kms.dispatch.lifecycle import DispatchLifecycleCoordinator
from src.kms.dispatch.preparation import DispatchPreparationCoordinator
from src.kms.dispatch.response import DispatchResponseCoordinator
from src.kms.dispatch.thinker_dispatch import ThinkerDispatchCoordinator
from src.kms.response.clarification import RouteClarificationCoordinator
from src.kms.response.direct_reply import KernelDirectReplyCoordinator
from src.kms.response.kernel_direct_responder import KernelDirectResponder
from src.kms.routing.task_routing import TaskRoutingCoordinator
from src.kms.task.coordinators import (
    InterruptCoordinator,
    ResumeCoordinator,
    TaskSwitchCoordinator,
)
from src.kms.task.dispatch_planner import TaskDispatchPlanner


@dataclass
class KmsManagerComponents:
    sessions: KernelSessionCoordinator
    direct_responder: KernelDirectResponder
    interrupts: InterruptCoordinator
    resumes: ResumeCoordinator
    task_switches: TaskSwitchCoordinator
    task_dispatch_planner: TaskDispatchPlanner
    lifecycle: DispatchLifecycleCoordinator
    conversation_refs: ConversationRefCoordinator
    thinker_dispatches: ThinkerDispatchCoordinator
    route_clarifications: RouteClarificationCoordinator
    direct_replies: KernelDirectReplyCoordinator
    dispatch_responses: DispatchResponseCoordinator
    dispatch_execution: DispatchExecutionCoordinator
    task_router: TaskRoutingCoordinator
    dispatch_preparation: DispatchPreparationCoordinator
    enable_llm_router: bool


def build_kms_manager_components(
    store,
    engine,
    *,
    enable_llm_router: bool | None = None,
    enable_llm_intent: bool | None = None,
) -> KmsManagerComponents:
    llm_enabled = (
        os.getenv("KMS_ENABLE_LLM_ROUTER") == "1"
        if enable_llm_router is None
        else enable_llm_router
    )
    llm_intent_enabled = (
        os.getenv("KMS_ENABLE_LLM_INTENT", "1") != "0"
        if enable_llm_intent is None
        else enable_llm_intent
    )
    sessions = KernelSessionCoordinator(store, engine)
    direct_responder = KernelDirectResponder(store, engine)
    interrupts = InterruptCoordinator(store)
    resumes = ResumeCoordinator(store)
    task_switches = TaskSwitchCoordinator(store, interrupts, resumes)
    task_dispatch_planner = TaskDispatchPlanner(store, task_switches)
    lifecycle = DispatchLifecycleCoordinator(store, engine)
    conversation_refs = ConversationRefCoordinator(store)
    thinker_dispatches = ThinkerDispatchCoordinator(
        lifecycle,
        conversation_refs,
    )
    route_clarifications = RouteClarificationCoordinator(store)
    direct_replies = KernelDirectReplyCoordinator(
        direct_responder,
        conversation_refs,
    )
    dispatch_responses = DispatchResponseCoordinator(
        store=store,
        route_clarifications=route_clarifications,
        direct_replies=direct_replies,
    )
    dispatch_execution = DispatchExecutionCoordinator(
        store=store,
        sessions=sessions,
        lifecycle=lifecycle,
        task_dispatch_planner=task_dispatch_planner,
        task_switches=task_switches,
        thinker_dispatches=thinker_dispatches,
    )
    task_router = TaskRoutingCoordinator(
        store,
        enable_llm=llm_enabled,
    )
    dispatch_preparation = DispatchPreparationCoordinator(
        task_router,
        sessions,
    )
    dispatch_preparation.enable_llm_intent = llm_intent_enabled
    return KmsManagerComponents(
        sessions=sessions,
        direct_responder=direct_responder,
        interrupts=interrupts,
        resumes=resumes,
        task_switches=task_switches,
        task_dispatch_planner=task_dispatch_planner,
        lifecycle=lifecycle,
        conversation_refs=conversation_refs,
        thinker_dispatches=thinker_dispatches,
        route_clarifications=route_clarifications,
        direct_replies=direct_replies,
        dispatch_responses=dispatch_responses,
        dispatch_execution=dispatch_execution,
        task_router=task_router,
        dispatch_preparation=dispatch_preparation,
        enable_llm_router=llm_enabled,
    )
