"""Kernel state snapshot used by KMS dispatch decisions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from src.schema.state import TaskStatus


@dataclass
class KernelDispatchContext:
    session: Any = None
    intent: Any = None
    plan: Any = None
    progress: Any = None
    tasks: list[Any] = field(default_factory=list)
    evidence: list[Any] = field(default_factory=list)
    executions: list[Any] = field(default_factory=list)

    @property
    def has_session(self) -> bool:
        return self.session is not None

    @property
    def active_task(self) -> Any:
        if not self.session:
            return None
        active_task_id = self.session.active_task_id or ""
        return next((task for task in self.tasks if task.task_id == active_task_id), None)

    @property
    def paused_tasks(self) -> list[Any]:
        return [task for task in self.tasks if task.status == TaskStatus.PAUSED]

    @property
    def failed_executions(self) -> list[Any]:
        return [item for item in self.executions if item.status == "failed"]

    @property
    def has_progress_content(self) -> bool:
        return bool(
            self.progress
            and (
                self.progress.summary
                or self.progress.stage
                or self.progress.safe_facts
                or self.progress.needs_user_input
            )
        )

    def to_prompt_summary(self) -> dict[str, Any]:
        active_task = self.active_task
        return {
            "has_session": self.has_session,
            "status": getattr(getattr(self.session, "status", None), "value", ""),
            "active_run_id": getattr(self.session, "active_run_id", "") if self.session else "",
            "active_task": {
                "task_id": active_task.task_id,
                "title": active_task.title,
                "goal": active_task.goal,
                "status": active_task.status.value,
            }
            if active_task
            else None,
            "task_count": len(self.tasks),
            "paused_task_count": len(self.paused_tasks),
            "evidence_count": len(self.evidence),
            "failed_execution_count": len(self.failed_executions),
            "has_progress_content": self.has_progress_content,
            "progress_status": getattr(self.progress, "status", "") if self.progress else "",
            "progress_stage": getattr(self.progress, "stage", "") if self.progress else "",
            "progress_summary": (getattr(self.progress, "summary", "") or "")[:240]
            if self.progress
            else "",
        }


async def build_kernel_dispatch_context(store, session: Any = None) -> KernelDispatchContext:
    if session is None:
        return KernelDispatchContext()

    session_id = session.kernel_session_id
    intent, plan, progress, tasks, evidence, executions = await asyncio.gather(
        store.get_intent(session_id),
        store.get_plan(session_id),
        store.get_progress(session_id),
        store.list_tasks(session_id),
        store.get_evidence(session_id),
        store.get_executions(session_id),
    )
    return KernelDispatchContext(
        session=session,
        intent=intent,
        plan=plan,
        progress=progress,
        tasks=tasks,
        evidence=evidence,
        executions=executions,
    )
