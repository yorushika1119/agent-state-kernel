"""Build direct user replies from Kernel state without waking Thinker."""

from __future__ import annotations

from typing import Any

from src.kms.task_scoped_state import TaskScopedStateFilter
from src.schema.state import (
    ClaimItem,
    TaskFlowState,
    TaskSnapshot,
    TaskStatus,
    TodoObligation,
)


class KernelDirectResponder:
    def __init__(self, store, engine):
        self.store = store
        self.engine = engine
        self.task_scope = TaskScopedStateFilter(store)

    async def build_response(
        self,
        session_id: str,
        kind: str = "progress",
        *,
        target_task_id: str = "",
    ) -> str:
        session = await self.store.get_session(session_id)
        target_task = await self._get_target_task(
            session_id,
            target_task_id=target_task_id,
            session=session,
        )
        task_scoped = bool(target_task_id and target_task)

        if kind == "failures":
            executions = await self.store.get_executions(session_id)
            failures = [item for item in executions if item.status == "failed"]
            if task_scoped:
                failures = await self.task_scope.filter_executions(
                    session_id,
                    failures,
                    target_task,
                )
            if not failures:
                return "当前没有记录到失败的工具调用。"
            latest = failures[-1]
            return f"最近失败的是 {latest.tool or latest.action_id}。"

        if kind == "evidence":
            evidence = await self.store.get_evidence(session_id)
            if task_scoped:
                evidence = await self.task_scope.filter_evidence(
                    session_id,
                    evidence,
                    target_task,
                )
            if not evidence:
                return "当前还没有可用证据。"
            latest = evidence[-3:]
            parts = [
                f"{item.evidence_id}: {item.title or item.source}"
                for item in latest
            ]
            return "当前已有证据：" + "；".join(parts)

        if kind == "claims":
            claims = [
                item
                for item in await self.store.get_claim_items(session_id)
                if item.visibility != "private"
            ]
            if task_scoped:
                claims = await self.task_scope.filter_claims(
                    session_id,
                    claims,
                    target_task,
                )
            if not claims:
                return "当前还没有记录到结论或风险。"
            return "当前记录的判断：" + "；".join(
                self._format_claim(item)
                for item in claims[-3:]
            )

        if kind == "todos":
            todos = await self.store.get_todo_obligations(session_id)
            if task_scoped:
                todos = await self.task_scope.filter_todos(
                    session_id,
                    todos,
                    target_task,
                )
            pending = [
                item
                for item in todos
                if item.status.value == "pending" or item.requires_confirmation
            ]
            if not pending:
                return "当前没有待办或待确认事项。"
            return "当前待办：" + "；".join(
                self._format_todo(item)
                for item in pending[-3:]
            )

        if kind == "resume":
            tasks = await self.store.list_tasks(session_id)
            paused = [task for task in tasks if task.status == TaskStatus.PAUSED]
            if not paused:
                return "当前没有可继续的暂停任务。"
            task = paused[-1]
            return f"可以继续暂停任务：{task.title or task.goal or task.task_id}。"

        if kind == "run":
            if session and session.active_run_id:
                return f"当前 active run 是 {session.active_run_id}。"
            return "当前没有 active run。"

        if task_scoped:
            return await self._build_task_local_progress(
                session_id,
                target_task,
                active=bool(session and session.active_task_id == target_task.task_id),
            )

        progress = await self.engine.get_talker_view(session_id)
        plan = await self.store.get_plan(session_id)
        if progress is None:
            return "No progress is available yet."

        parts: list[str] = []
        if progress.summary:
            parts.append(progress.summary)
        if plan and plan.current_step:
            current = next((step for step in plan.steps if step.step_id == plan.current_step), None)
            if current:
                parts.append(f"Current step: {current.name}")
        if session and session.active_run_id:
            parts.append("thinker is still working on the current task.")
        if not parts:
            parts.append("The task exists, but there is no additional progress yet.")
        return " ".join(parts)

    async def _build_task_local_progress(
        self,
        session_id: str,
        task: TaskSnapshot,
        *,
        active: bool,
    ) -> str:
        flow = await self._get_task_flow_for_task(session_id, task)
        if active:
            progress = await self.engine.get_talker_view(session_id)
            plan = await self.store.get_plan(session_id)
            return self._build_active_task_progress(task, progress, plan, flow)
        if flow:
            return self._build_task_flow_progress(task, flow)
        return self._build_task_snapshot_progress(task)

    async def _get_target_task(
        self,
        session_id: str,
        *,
        target_task_id: str = "",
        session: Any = None,
    ) -> TaskSnapshot | None:
        task_id = target_task_id or (session.active_task_id if session else "")
        if not task_id:
            return None
        return await self.store.get_task(session_id, task_id)

    def _build_task_snapshot_progress(self, task: TaskSnapshot) -> str:
        title = task.title or task.goal or task.task_id
        parts = [f"{title} 当前状态：{task.status.value}。"]
        if task.resume_summary:
            parts.append(task.resume_summary)
        step_name = task.current_step_name or self._step_name(
            task.current_step,
            task.steps,
        )
        if step_name:
            parts.append(f"当前步骤：{step_name}。")
        if len(parts) == 1:
            parts.append("这个任务已记录，但还没有更多进度。")
        return " ".join(parts)

    async def _get_task_flow_for_task(
        self,
        session_id: str,
        task: TaskSnapshot,
    ) -> TaskFlowState | None:
        flow = await self.store.get_task_flow(session_id)
        if flow and flow.task_id == task.task_id:
            return flow
        return None

    def _build_active_task_progress(
        self,
        task: TaskSnapshot,
        progress: Any,
        plan: Any,
        flow: TaskFlowState | None,
    ) -> str:
        parts: list[str] = []
        if progress and progress.summary:
            parts.append(progress.summary)

        step_name = ""
        if plan and plan.current_step:
            current = next((step for step in plan.steps if step.step_id == plan.current_step), None)
            if current:
                step_name = current.name
        if not step_name and flow:
            step_name = self._step_name(flow.current_step, flow.steps)
        if not step_name:
            step_name = task.current_step_name or self._step_name(task.current_step, task.steps)
        if step_name:
            parts.append(f"当前步骤：{step_name}。")

        if flow:
            latest = self._latest_execution_for_task(task, flow)
            if latest:
                parts.append(self._format_execution_progress(latest))

        if not parts:
            parts.append("这个任务已记录，但还没有更多进度。")
        return " ".join(parts)

    def _build_task_flow_progress(
        self,
        task: TaskSnapshot,
        flow: TaskFlowState,
    ) -> str:
        title = task.title or task.goal or task.task_id
        parts = [f"{title} 当前状态：{task.status.value}。"]
        if task.resume_summary:
            parts.append(task.resume_summary)
        step_name = self._step_name(flow.current_step, flow.steps)
        if not step_name:
            step_name = task.current_step_name or self._step_name(task.current_step, task.steps)
        if step_name:
            parts.append(f"当前步骤：{step_name}。")
        latest = self._latest_execution_for_task(task, flow)
        if latest:
            parts.append(self._format_execution_progress(latest))
        if len(parts) == 1:
            parts.append("这个任务已记录，但还没有更多进度。")
        return " ".join(parts)

    def _step_name(self, current_step: str, steps: list[dict[str, Any]]) -> str:
        if current_step:
            current = next(
                (step for step in steps if step.get("step_id") == current_step),
                None,
            )
            if current:
                return str(current.get("name") or current.get("step_id") or "")
            return current_step
        running = next(
            (step for step in steps if step.get("status") == "running"),
            None,
        )
        if running:
            return str(running.get("name") or running.get("step_id") or "")
        return ""

    def _latest_execution_for_task(
        self,
        task: TaskSnapshot,
        flow: TaskFlowState,
    ) -> dict[str, Any] | None:
        step_ids = self.task_scope.task_step_ids(task)
        scoped = []
        for item in flow.execution_summary:
            task_id = str(item.get("task_id") or "")
            step_id = str(item.get("step_id") or "")
            if task_id == task.task_id or (not task_id and step_id in step_ids):
                scoped.append(item)
        return scoped[-1] if scoped else None

    def _format_execution_progress(self, item: dict[str, Any]) -> str:
        label = item.get("tool") or item.get("action_id") or "unknown"
        status = item.get("status") or "unknown"
        return f"最近执行：{label}（{status}）。"

    def _format_claim(self, item: ClaimItem) -> str:
        return f"{item.claim_id}: {item.claim}（{item.status.value}，{item.confidence:.2f}）"

    def _format_todo(self, item: TodoObligation) -> str:
        marker = "，需要确认" if item.requires_confirmation else ""
        return f"{item.obligation_id}: {item.statement}（{item.status.value}{marker}）"
