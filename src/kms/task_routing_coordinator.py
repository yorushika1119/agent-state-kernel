"""User-session task routing orchestration for KMS dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.kms.task_context_router import route_task_context_with_llm


@dataclass
class TaskRoutingResult:
    user_session: Any
    global_tasks: list[Any]
    route: Any
    route_target_task: Any = None
    routed_session_id: str = ""


class TaskRoutingCoordinator:
    def __init__(self, store, *, enable_llm: bool = False):
        self.store = store
        self.enable_llm = enable_llm

    async def route_message(
        self,
        text: str,
        *,
        user_session_id: str = "",
        runtime_session_id: str = "",
        runtime_id: str = "",
        runtime_type: str = "cli-agent",
        agent_id: str = "",
    ) -> TaskRoutingResult:
        user_session = await self.store.observe_user_session(
            user_session_id=user_session_id,
            runtime_session_id=runtime_session_id,
            runtime_id=runtime_id,
            runtime_type=runtime_type,
            agent_id=agent_id,
        )
        global_tasks = await self.store.list_global_tasks(
            user_session_id=user_session.user_session_id,
        )
        route = await route_task_context_with_llm(
            text,
            user_session_id=user_session.user_session_id,
            runtime_session_id=runtime_session_id,
            tasks=global_tasks,
            enable_llm=self.enable_llm,
        )
        await self.store.save_task_route_decision(route)
        route_target_task = next(
            (task for task in global_tasks if task.task_id == route.target_task_id),
            None,
        )
        routed_session_id = (
            route_target_task.kernel_session_id
            if route.routing_decision == "select_existing" and route_target_task
            else ""
        )
        return TaskRoutingResult(
            user_session=user_session,
            global_tasks=global_tasks,
            route=route,
            route_target_task=route_target_task,
            routed_session_id=routed_session_id,
        )
