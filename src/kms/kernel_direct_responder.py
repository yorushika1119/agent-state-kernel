"""Build direct user replies from Kernel state without waking Thinker."""

from __future__ import annotations

from src.schema.state import TaskStatus


class KernelDirectResponder:
    def __init__(self, store, engine):
        self.store = store
        self.engine = engine

    async def build_response(self, session_id: str, kind: str = "progress") -> str:
        progress = await self.engine.get_talker_view(session_id)
        plan = await self.store.get_plan(session_id)
        session = await self.store.get_session(session_id)

        if kind == "failures":
            executions = await self.store.get_executions(session_id)
            failures = [item for item in executions if item.status == "failed"]
            if not failures:
                return "当前没有记录到失败的工具调用。"
            latest = failures[-1]
            return f"最近失败的是 {latest.tool or latest.action_id}。"

        if kind == "evidence":
            evidence = await self.store.get_evidence(session_id)
            if not evidence:
                return "当前还没有可用证据。"
            latest = evidence[-3:]
            parts = [
                f"{item.evidence_id}: {item.title or item.source}"
                for item in latest
            ]
            return "当前已有证据：" + "；".join(parts)

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
