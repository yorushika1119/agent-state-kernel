"""Run and thinker-dispatch lifecycle helpers for KMS dispatch."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from src.schema.events import EventSubmission, EventType


@dataclass
class ActivatedRun:
    run_id: str
    previous_run_id: str = ""
    interrupt_event: Any = None


class DispatchLifecycleCoordinator:
    """Owns run activation, stale-run bookkeeping, and thinker dispatch creation."""

    def __init__(self, store, engine):
        self.store = store
        self.engine = engine

    def new_run_id(self) -> str:
        return f"run_{uuid.uuid4().hex[:12]}"

    async def activate_run(
        self,
        session,
        *,
        run_id: str,
        active_task_id: str = "",
        last_paused_task_id: str = "",
        user_message: str = "",
    ) -> ActivatedRun:
        previous_run_id = session.active_run_id or ""
        interrupt_event = None
        if previous_run_id:
            interrupt_event = await self.engine.append_kernel_event(
                session.kernel_session_id,
                EventType.RUN_INTERRUPTED,
                run_id=previous_run_id,
                payload={
                    "interrupted_run_id": previous_run_id,
                    "interrupting_run_id": run_id,
                    "reason": "superseded_by_new_user_message",
                    "user_message": user_message,
                },
            )

        await self.store.update_session_status(
            session.kernel_session_id,
            "running",
            active_run_id=run_id,
            active_task_id=active_task_id,
            cancellation_token=0,
            last_paused_task_id=last_paused_task_id,
            last_interrupted_run_id=previous_run_id,
            last_interrupting_run_id=run_id if previous_run_id else "",
            last_interrupt_reason="superseded_by_new_user_message" if previous_run_id else "",
            last_interrupt_at=interrupt_event.created_at.isoformat() if interrupt_event else None,
        )
        return ActivatedRun(
            run_id=run_id,
            previous_run_id=previous_run_id,
            interrupt_event=interrupt_event,
        )

    async def submit_user_message(self, session_id: str, text: str, *, payload: dict | None = None):
        ok, err, event = await self.engine.submit_event(
            EventSubmission(
                session_id=session_id,
                component="talker",
                request_type="SUBMIT_USER_MESSAGE",
                payload=payload or {"goal": text, "text": text},
            )
        )
        if not ok:
            raise RuntimeError(err or "dispatch_user_message failed")
        return event

    async def create_task_from_user_message(
        self,
        session_id: str,
        *,
        text: str,
        run_id: str,
        event,
        session_status: str = "running",
    ):
        task = await self.store.create_task(
            session_id,
            title=text[:80],
            goal=event.payload.get("goal", text) if event else text,
            constraints=event.payload.get("constraints", []) if event else [],
            last_run_id=run_id,
        )
        await self.store.update_session_status(
            session_id,
            session_status,
            active_task_id=task.task_id,
        )
        return task

    async def create_thinker_dispatch(
        self,
        *,
        session_id: str,
        task_id: str,
        run_id: str,
        task_brief_version: int,
        dispatch_type: str,
        payload: dict[str, Any],
        cancellation_token: bool = False,
    ):
        return await self.store.create_thinker_dispatch(
            kernel_session_id=session_id,
            task_id=task_id,
            run_id=run_id,
            task_brief_version=task_brief_version,
            dispatch_type=dispatch_type,
            cancellation_token=cancellation_token,
            payload=payload,
        )
