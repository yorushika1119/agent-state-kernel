"""Task lifecycle coordinators used by KMS dispatch."""

from __future__ import annotations

from typing import Any, Optional

from src.schema.state import TaskSnapshot, TaskStatus


class InterruptCoordinator:
    def __init__(self, store):
        self.store = store

    async def ensure_active_task_for_session(
        self,
        session,
        *,
        run_id: str,
    ) -> Optional[TaskSnapshot]:
        if not session:
            return None

        existing = await self.store.get_task(session.kernel_session_id, session.active_task_id or "")
        if existing:
            return existing

        intent = await self.store.get_intent(session.kernel_session_id)
        plan = await self.store.get_plan(session.kernel_session_id)
        current_step_name = ""
        step_rows = []
        if plan:
            step_rows = [step.model_dump() for step in plan.steps]
            current = next((step for step in plan.steps if step.step_id == plan.current_step), None)
            if current:
                current_step_name = current.name

        task = await self.store.create_task(
            session.kernel_session_id,
            title=(intent.goal if intent and intent.goal else "")[:80],
            goal=intent.goal if intent else "",
            constraints=intent.constraints if intent else [],
            plan_id=plan.plan_id if plan else "",
            current_step=plan.current_step if plan else "",
            current_step_name=current_step_name,
            steps=step_rows,
            last_run_id=run_id,
        )
        await self.store.update_session_status(
            session.kernel_session_id,
            session.status.value,
            active_task_id=task.task_id,
        )
        return task

    async def snapshot_current_task(
        self,
        session,
        *,
        interrupted_run_id: str = "",
    ) -> Optional[TaskSnapshot]:
        if not session:
            return None

        task = await self.store.get_task(session.kernel_session_id, session.active_task_id or "")
        if task is None:
            task = await self.ensure_active_task_for_session(
                session,
                run_id=session.active_run_id or interrupted_run_id,
            )
        if task is None:
            return None

        intent = await self.store.get_intent(session.kernel_session_id)
        plan = await self.store.get_plan(session.kernel_session_id)
        progress = await self.store.get_progress(session.kernel_session_id)
        current_step_name = ""
        step_rows = []
        if plan:
            step_rows = [step.model_dump() for step in plan.steps]
            current = next((step for step in plan.steps if step.step_id == plan.current_step), None)
            if current:
                current_step_name = current.name

        task.title = (intent.goal if intent and intent.goal else task.title)[:80]
        task.goal = intent.goal if intent else task.goal
        task.constraints = intent.constraints if intent else task.constraints
        task.plan_id = plan.plan_id if plan else task.plan_id
        task.current_step = plan.current_step if plan else task.current_step
        task.current_step_name = current_step_name
        task.steps = step_rows
        task.last_run_id = session.active_run_id or task.last_run_id
        task.last_interrupted_run_id = interrupted_run_id or task.last_interrupted_run_id
        task.resume_summary = progress.summary if progress and progress.summary else task.resume_summary
        task.status = TaskStatus.PAUSED
        await self.store.save_task(task)
        await self.store.update_session_status(
            session.kernel_session_id,
            session.status.value,
            last_paused_task_id=task.task_id,
        )
        return task


class ResumeCoordinator:
    def __init__(self, store):
        self.store = store

    def build_resume_context(self, task: Optional[TaskSnapshot]) -> dict[str, Any]:
        if task is None:
            return {}
        return {
            "task_id": task.task_id,
            "goal": task.goal,
            "current_step": task.current_step,
            "current_step_name": task.current_step_name,
            "resume_summary": task.resume_summary,
            "last_run_id": task.last_run_id,
            "last_interrupted_run_id": task.last_interrupted_run_id,
        }

    async def restore_task_into_session(self, session_id: str, task: TaskSnapshot) -> None:
        from src.schema.state import IntentState, PlanState, PlanStep, PlanStatus, StepStatus

        current_intent = await self.store.get_intent(session_id)
        next_intent_version = (current_intent.intent_version if current_intent else 0) + 1
        await self.store.save_intent(
            session_id,
            IntentState(
                intent_version=next_intent_version,
                goal=task.goal,
                constraints=task.constraints,
            ),
        )
        if task.plan_id or task.steps:
            steps = [
                PlanStep(
                    step_id=item.get("step_id", ""),
                    name=item.get("name", ""),
                    status=StepStatus(item.get("status", "pending")),
                    owner=item.get("owner", "thinker"),
                    depends_on=item.get("depends_on", []),
                )
                for item in task.steps
            ]
            await self.store.save_plan(
                session_id,
                PlanState(
                    plan_id=task.plan_id,
                    status=PlanStatus.ACTIVE,
                    steps=steps,
                    current_step=task.current_step,
                    intent_version=next_intent_version,
                ),
            )
        session = await self.store.get_session(session_id)
        await self.store.update_session_status(
            session_id,
            session.status.value if session else "running",
            intent_version=next_intent_version,
        )


class TaskSwitchCoordinator:
    """Moves task state between active, paused, and global task directory views."""

    def __init__(
        self,
        store,
        interrupts: InterruptCoordinator,
        resumes: ResumeCoordinator,
    ):
        self.store = store
        self.interrupts = interrupts
        self.resumes = resumes

    async def sync_global_task(
        self,
        task: Optional[TaskSnapshot],
        *,
        user_session_id: str = "",
        agent_id: str = "",
        task_brief_version: int = 0,
        active: bool = True,
    ) -> None:
        if task is None:
            return
        await self.store.upsert_global_task_from_snapshot(
            task,
            user_session_id=user_session_id,
            agent_id=agent_id,
            task_brief_version=task_brief_version,
        )
        if user_session_id:
            await self.store.link_task_to_user_session(
                user_session_id,
                task.task_id,
                active=active,
            )

    async def pause_current_task(
        self,
        session,
        *,
        interrupted_run_id: str = "",
        user_session_id: str = "",
        agent_id: str = "",
        task_brief_version: int = 0,
        fallback_last_paused_task_id: str = "",
    ) -> str:
        paused_task = await self.interrupts.snapshot_current_task(
            session,
            interrupted_run_id=interrupted_run_id,
        )
        await self.sync_global_task(
            paused_task,
            user_session_id=user_session_id,
            agent_id=agent_id,
            task_brief_version=task_brief_version,
            active=False,
        )
        return paused_task.task_id if paused_task else fallback_last_paused_task_id

    async def get_resume_task(self, session) -> Optional[TaskSnapshot]:
        resume_task = await self.store.get_task(
            session.kernel_session_id,
            session.last_paused_task_id or "",
        )
        if resume_task is None:
            resume_task = await self.store.get_latest_paused_task(
                session.kernel_session_id,
            )
        return resume_task

    async def activate_existing_task(
        self,
        session,
        task: TaskSnapshot,
        *,
        run_id: str,
        user_session_id: str = "",
        agent_id: str = "",
        task_brief_version: int = 0,
    ) -> tuple[TaskSnapshot, dict[str, Any]]:
        await self.resumes.restore_task_into_session(session.kernel_session_id, task)
        task.status = TaskStatus.ACTIVE
        task.last_run_id = run_id
        await self.store.save_task(task)
        await self.sync_global_task(
            task,
            user_session_id=user_session_id,
            agent_id=agent_id,
            task_brief_version=task_brief_version,
        )
        return task, self.resumes.build_resume_context(task)

    async def ensure_active_task_for_session(
        self,
        session,
        *,
        run_id: str,
    ) -> Optional[TaskSnapshot]:
        return await self.interrupts.ensure_active_task_for_session(
            session,
            run_id=run_id,
        )
