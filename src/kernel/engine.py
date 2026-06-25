"""Kernel engine entry point."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from src.schema.events import Actor, CognitiveEvent, EventSubmission, EventType, Visibility
from src.schema.state import ObserverTaskView, ProgressState, SyncView, TaskStatus

logger = logging.getLogger(__name__)


class KernelEngine:
    """Connects the store and the KMS pipeline."""

    def __init__(self, store):
        self.store = store

    async def append_kernel_event(
        self,
        session_id: str,
        event_type: EventType,
        *,
        payload: Optional[Dict[str, Any]] = None,
        run_id: str = "",
        visibility: Visibility = Visibility.SHARED,
    ) -> CognitiveEvent:
        from src.kms.pipeline import _assign_event_metadata, reduce, refresh_progress

        session = await self.store.get_session(session_id)
        event = CognitiveEvent(
            event_id="",
            kernel_session_id=session_id,
            runtime_session_id=session.runtime_session_id if session else "",
            run_id=run_id,
            event_type=event_type,
            actor=Actor.KERNEL_MANAGER,
            source_component="kernel_manager",
            payload=payload or {},
            visibility=visibility,
            intent_version=0,
        )
        await _assign_event_metadata(self.store, session_id, event)
        await self.store.append_event(event)
        await reduce(self.store, session_id, event)
        await self.store.update_session_status(
            session_id,
            session.status.value if session else "running",
            state_version=event.state_version,
        )
        await refresh_progress(self.store, session_id)
        return event

    async def create_session(
        self,
        agent_id: str = "",
        runtime_id: str = "",
        runtime_session_id: str = "",
        runtime_type: str = "cli-agent",
        external_source: str = "",
        external_workspace_id: str = "",
        external_issue_id: str = "",
        external_task_id: str = "",
    ) -> Any:
        session = await self.store.create_session(
            agent_id=agent_id,
            runtime_id=runtime_id,
            runtime_session_id=runtime_session_id,
            runtime_type=runtime_type,
            external_source=external_source,
            external_workspace_id=external_workspace_id,
            external_issue_id=external_issue_id,
            external_task_id=external_task_id,
        )
        await self.append_kernel_event(session.kernel_session_id, EventType.SESSION_CREATED)
        return await self.store.get_session(session.kernel_session_id)

    async def get_session(self, session_id: str) -> Optional[Any]:
        return await self.store.get_session(session_id)

    async def submit_event(
        self, submission: EventSubmission
    ) -> Tuple[bool, Optional[str], Optional[CognitiveEvent]]:
        import os

        kms_url = os.getenv("KMS_URL", "")
        from src.kms.pipeline import run_pipeline

        result = await run_pipeline(self.store, submission, kms_url)

        if not result.accepted:
            return False, result.reason, None

        if result.is_read_only:
            return True, None, None

        event = result.event
        session = await self.store.get_session(submission.session_id)
        await self.store.update_session_status(
            submission.session_id,
            session.status.value if session else "running",
            state_version=result.latest_state_version or event.state_version,
        )
        return True, None, event

    async def complete_run(
        self,
        session_id: str,
        run_id: str,
        *,
        session_status: str = "running",
    ) -> bool:
        session = await self.store.get_session(session_id)
        if not session:
            return False
        if session.active_run_id != run_id:
            return False

        await self.store.update_session_status(
            session_id,
            session_status,
            active_run_id="",
            cancellation_token=0,
        )

        task = await self.store.get_task(session_id, session.active_task_id or "")
        if task is not None:
            task.last_run_id = run_id
            if session_status == "completed":
                task.status = TaskStatus.COMPLETED
            elif session_status == "cancelled":
                task.status = TaskStatus.CANCELLED
            elif session_status == "failed":
                task.status = TaskStatus.BLOCKED
            else:
                task.status = TaskStatus.ACTIVE
            await self.store.save_task(task)
            global_task = await self.store.get_global_task(task.task_id)
            await self.store.upsert_global_task_from_snapshot(
                task,
                user_session_id=global_task.user_session_id if global_task else "",
                agent_id=global_task.agent_id if global_task else "",
                task_brief_version=global_task.task_brief_version if global_task else 0,
            )
        return True

    async def get_talker_view(self, session_id: str) -> Optional[ProgressState]:
        from src.kms.pipeline import summarize

        return await summarize(self.store, session_id)

    def _is_thinker_visible(self, visibility: str) -> bool:
        return visibility != "private"

    def _build_risks(self, claims, executions, todos) -> list[str]:
        risks: list[str] = []

        for claim in claims:
            if not self._is_thinker_visible(claim.visibility):
                continue
            if claim.status.value == "conflicting":
                risks.append(f"claim_conflict:{claim.claim}")
            elif claim.status.value == "unverified":
                risks.append(f"unverified:{claim.claim}")
            elif claim.status.value == "likely" and claim.confidence < 0.5:
                risks.append(f"low_confidence:{claim.claim}")

        for action in executions:
            if action.status == "failed":
                risks.append(f"tool_failed:{action.tool or action.action_id}")

        for todo in todos:
            if todo.requires_confirmation and todo.status.value == "pending":
                risks.append(f"awaiting_confirmation:{todo.statement}")

        seen = set()
        ordered: list[str] = []
        for risk in risks:
            if risk not in seen:
                seen.add(risk)
                ordered.append(risk)
        return ordered

    def _build_recent_updates(self, executions, dispatches, notifications) -> list[dict[str, Any]]:
        updates: list[dict[str, Any]] = []
        for action in executions:
            if action.tool in {"reasoning", "raw_result"}:
                continue
            text = action.input_summary or action.tool or action.action_id
            if not text:
                continue
            updates.append(
                {
                    "at": (
                        action.ended_at or action.started_at
                    ).isoformat()
                    if action.ended_at or action.started_at
                    else None,
                    "text": f"{action.status}:{text}",
                    "source": "execution",
                }
            )
        for dispatch in dispatches:
            updates.append(
                {
                    "at": dispatch.updated_at.isoformat() if dispatch.updated_at else None,
                    "text": f"dispatch:{dispatch.status.value}",
                    "source": "thinker_dispatch",
                }
            )
        for notification in notifications:
            updates.append(
                {
                    "at": notification.created_at.isoformat() if notification.created_at else None,
                    "text": notification.reason or notification.notification_type,
                    "source": "notification",
                }
            )
        updates.sort(key=lambda item: item["at"] or "", reverse=True)
        return updates[:8]

    def _build_blocking_reason(self, task_brief, progress, executions, todos, session) -> Optional[str]:
        pending_confirmation = next(
            (
                todo.statement
                for todo in todos
                if todo.requires_confirmation
                and todo.status.value == "pending"
            ),
            "",
        )
        failed_action = next(
            (action.tool or action.action_id for action in executions if action.status == "failed"),
            "",
        )
        if task_brief and task_brief.cancelled:
            return "session_cancelled"
        if pending_confirmation:
            return "awaiting_user_confirmation"
        if progress and progress.needs_user_input:
            return "awaiting_user_input"
        if progress and progress.status == "blocked":
            return "task_blocked"
        if failed_action:
            return f"tool_failed:{failed_action}"
        if session and session.last_interrupted_run_id:
            return "interrupted_by_new_request"
        return None

    def _build_legacy_debug(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "note": "Legacy-shaped compatibility aliases. Prefer task_brief/task_flow/claims/todos.",
            "intent": state["intent"].model_dump() if state["intent"] else None,
            "plan": state["plan"].model_dump() if state["plan"] else None,
            "beliefs": [belief.model_dump() for belief in state["beliefs"]],
            "commitments": [
                commitment.model_dump() for commitment in state["commitments"]
            ],
        }

    async def _get_legacy_debug_state(self, session_id: str) -> Dict[str, Any]:
        return {
            "intent": await self.store.get_intent(session_id),
            "plan": await self.store.get_plan(session_id),
            "beliefs": await self.store.get_beliefs(session_id),
            "commitments": await self.store.get_commitments(session_id),
        }

    def _current_step_from_flow(self, task_flow) -> Optional[Dict[str, Any]]:
        if not task_flow or not task_flow.current_step:
            return None
        for step in task_flow.steps:
            step_id = step.get("step_id") if isinstance(step, dict) else getattr(step, "step_id", "")
            if step_id == task_flow.current_step:
                return step if isinstance(step, dict) else step.model_dump()
        return None

    def _build_observer_checklist(self, task_flow) -> list[dict[str, Any]]:
        if not task_flow:
            return []
        checklist: list[dict[str, Any]] = []
        for step in task_flow.steps:
            if isinstance(step, dict):
                checklist.append(
                    {
                        "step_id": step.get("step_id", ""),
                        "label": step.get("name", ""),
                        "status": step.get("status", "pending"),
                    }
                )
            else:
                checklist.append(
                    {
                        "step_id": getattr(step, "step_id", ""),
                        "label": getattr(step, "name", ""),
                        "status": getattr(step, "status", "pending"),
                    }
                )
        return checklist

    async def _ensure_progress(self, session_id: str) -> Optional[ProgressState]:
        progress = await self.store.get_progress(session_id)
        if progress:
            return progress
        from src.kms.pipeline import refresh_progress

        return await refresh_progress(self.store, session_id)

    async def _get_raw_state(self, session_id: str) -> Dict[str, Any]:
        return {
            "session": await self.store.get_session(session_id),
            "task_brief": await self.store.get_task_brief(session_id),
            "task_flow": await self.store.get_task_flow(session_id),
            "progress": await self.store.get_progress(session_id),
            "tasks": await self.store.list_tasks(session_id),
            "evidence": await self.store.get_evidence(session_id),
            "claims": await self.store.get_claim_items(session_id),
            "executions": await self.store.get_executions(session_id),
            "todos": await self.store.get_todo_obligations(session_id),
            "thinker_dispatches": await self.store.list_thinker_dispatches(
                kernel_session_id=session_id
            ),
            "runtime_references": await self.store.get_runtime_references(session_id),
            "approvals": await self.store.list_approval_requests(kernel_session_id=session_id),
            "observer_task_view": await self.store.get_observer_task_view(session_id),
        }

    async def get_thinker_view(self, session_id: str) -> Dict[str, Any]:
        state = await self._get_raw_state(session_id)
        session = state["session"]
        task_brief = state["task_brief"]
        task_flow = state["task_flow"]
        progress = state["progress"]
        tasks = state["tasks"]
        evidence = state["evidence"]
        claims = state["claims"]
        executions = state["executions"]
        todos = state["todos"]
        thinker_dispatches = state["thinker_dispatches"]
        runtime_references = state["runtime_references"]
        approvals = state["approvals"]

        if progress is None:
            from src.kms.pipeline import refresh_progress

            progress = await refresh_progress(self.store, session_id)

        current_step = self._current_step_from_flow(task_flow)

        return {
            "session": session.model_dump() if session else None,
            "task_brief": task_brief.model_dump() if task_brief else None,
            "task_flow": task_flow.model_dump() if task_flow else None,
            "tasks": [task.model_dump() for task in tasks],
            "current_step": current_step,
            "progress": {
                "status": progress.status,
                "stage": progress.stage,
                "needs_user_input": progress.needs_user_input,
            }
            if progress
            else None,
            "tool_constraints": {
                "allowed_actions": progress.allowed_actions if progress else [],
                "forbidden_actions": progress.forbidden_actions if progress else [],
            },
            "cancellation": {
                "cancelled": bool(task_brief.cancelled) if task_brief else False,
                "session_status": session.status.value if session else "unknown",
                "intent_version": task_brief.task_brief_version if task_brief else 0,
                "active_run_id": session.active_run_id if session else "",
                "active_task_id": session.active_task_id if session else "",
                "last_paused_task_id": session.last_paused_task_id if session else "",
                "cancellation_token": bool(session.cancellation_token) if session else False,
                "last_interrupted_run_id": session.last_interrupted_run_id if session else "",
                "last_interrupting_run_id": session.last_interrupting_run_id if session else "",
                "last_interrupt_reason": session.last_interrupt_reason if session else "",
                "last_interrupt_at": (
                    session.last_interrupt_at.isoformat()
                    if session and session.last_interrupt_at
                    else None
                ),
            },
            "evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "task_id": item.task_id,
                    "evidence_type": item.evidence_type.value,
                    "source": item.source,
                    "title": item.title,
                    "source_date": item.source_date,
                    "reliability": item.reliability.value,
                    "observed_at": item.observed_at.isoformat() if item.observed_at else None,
                    "extracted_facts": item.extracted_facts,
                    "fact_count": len(item.extracted_facts),
                }
                for item in evidence
            ],
            "claims": [
                claim.model_dump()
                for claim in claims
                if self._is_thinker_visible(claim.visibility)
            ],
            "executions": [
                {
                    "action_id": action.action_id,
                    "task_id": action.task_id,
                    "step_id": action.step_id,
                    "tool": action.tool,
                    "status": action.status,
                    "input_summary": action.input_summary,
                    "retry_count": action.retry_count,
                    "has_output": bool(action.output_ref),
                    "started_at": action.started_at.isoformat() if action.started_at else None,
                    "ended_at": action.ended_at.isoformat() if action.ended_at else None,
                }
                for action in executions
                if action.tool not in {"reasoning", "raw_result"}
            ],
            "todos": [todo.model_dump() for todo in todos],
            "thinker_dispatches": [dispatch.model_dump() for dispatch in thinker_dispatches],
            "approvals": [approval.model_dump() for approval in approvals],
            "pending_approvals": [
                approval.model_dump()
                for approval in approvals
                if approval.status.value == "pending"
            ],
            "runtime_references": [
                {
                    "kernel_ref_id": ref.kernel_ref_id,
                    "runtime_session_id": ref.runtime_session_id,
                    "runtime_type": ref.runtime_type,
                    "ref_type": ref.ref_type,
                    "ref_id": ref.ref_id,
                    "summary": ref.summary,
                    "visibility": ref.visibility,
                    "created_at": ref.created_at.isoformat() if ref.created_at else None,
                }
                for ref in runtime_references
                if self._is_thinker_visible(ref.visibility)
            ],
            "risks": self._build_risks(claims, executions, todos),
        }

    async def get_observer_view(self, session_id: str) -> Optional[Dict[str, Any]]:
        state = await self._get_raw_state(session_id)
        session = state["session"]
        if not session:
            return None

        progress = state["progress"] or await self._ensure_progress(session_id)
        active_task = await self.store.get_task(
            session_id,
            session.active_task_id or "",
        )
        task_brief = state["task_brief"]
        todos = state["todos"]
        executions = state["executions"]
        approvals = state["approvals"]
        notifications = await self.store.list_observer_notifications(
            target="observer",
            kernel_session_id=session_id,
            status="pending",
        )
        conversation_refs = await self.store.list_task_conversation_refs(
            kernel_session_id=session_id,
            task_id=session.active_task_id,
            limit=8,
        )
        dispatches = state["thinker_dispatches"]
        pending_approvals = [
            approval for approval in approvals if approval.status.value == "pending"
        ]

        pending_confirmations = [
            todo.statement
            for todo in todos
            if todo.requires_confirmation and todo.status.value == "pending"
        ]
        blocking_reason = self._build_blocking_reason(
            task_brief,
            progress,
            executions,
            todos,
            session,
        )
        if pending_approvals:
            blocking_reason = blocking_reason or "awaiting_approval"
        open_todos = [
            todo.statement or todo.obligation_id
            for todo in todos
            if todo.status.value == "pending"
        ]
        status = progress.status if progress else session.status.value
        if session.status.value in {"completed", "failed", "cancelled", "paused"}:
            status = session.status.value
        stage = (
            progress.stage
            if progress and progress.stage
            else active_task.current_step_name
            if active_task
            else ""
        )
        view = ObserverTaskView(
            view_id=f"{session_id}:{session.active_task_id or 'session'}",
            kernel_session_id=session_id,
            task_id=session.active_task_id,
            status=status,
            stage=stage,
            summary=progress.summary if progress else "",
            checklist=self._build_observer_checklist(state["task_flow"]),
            needs_user_input=bool(
                (progress.needs_user_input if progress else False)
                or pending_confirmations
                or pending_approvals
            ),
            blocking_reason=blocking_reason,
            approval_request_ids=[
                approval.approval_request_id for approval in pending_approvals
            ],
            safe_facts=progress.safe_facts if progress else [],
            uncertain_points=progress.unsafe_claims if progress else [],
            open_todos=open_todos,
        )
        await self.store.save_observer_task_view(view)

        return {
            "session_id": session_id,
            "task_id": session.active_task_id,
            "status": status,
            "stage": stage,
            "one_line_summary": progress.summary if progress else "",
            "summary_for_observer": progress.summary if progress else "",
            "current_focus": active_task.current_step_name if active_task else "",
            "recent_conversation_refs": [
                ref.model_dump() for ref in conversation_refs
            ],
            "recent_updates": self._build_recent_updates(
                executions,
                dispatches,
                notifications,
            ),
            "safe_facts": progress.safe_facts if progress else [],
            "uncertain_points": progress.unsafe_claims if progress else [],
            "open_todos": open_todos,
            "blocking_reason": blocking_reason,
            "needs_user_input": bool(
                (progress.needs_user_input if progress else False)
                or pending_confirmations
                or pending_approvals
            ),
            "user_question": (
                pending_confirmations[0]
                if pending_confirmations
                else pending_approvals[0].action_summary
                if pending_approvals
                else None
            ),
            "allowed_observer_actions": progress.allowed_actions if progress else [],
            "forbidden_observer_actions": progress.forbidden_actions if progress else [],
            "pending_approvals": [
                approval.model_dump() for approval in pending_approvals
            ],
            "observer_task_view": view.model_dump(),
            "notifications": [notification.model_dump() for notification in notifications],
        }

    async def get_manager_view(self, session_id: str) -> Optional[Dict[str, Any]]:
        state = await self._get_raw_state(session_id)
        session = state["session"]
        if not session:
            return None

        progress = state["progress"] or await self._ensure_progress(session_id)
        task_brief = state["task_brief"]
        task_flow = state["task_flow"]
        tasks = state["tasks"]
        claims = state["claims"]
        executions = state["executions"]
        todos = state["todos"]
        approvals = state["approvals"]
        dispatches = state["thinker_dispatches"]
        active_task = await self.store.get_task(
            session_id,
            session.active_task_id or "",
        )
        global_task = await self.store.get_global_task(session.active_task_id)
        notifications = await self.store.list_observer_notifications(
            kernel_session_id=session_id,
            status="pending",
        )
        conversation_refs = await self.store.list_task_conversation_refs(
            kernel_session_id=session_id,
            task_id=session.active_task_id,
            limit=12,
        )
        pending_confirmations = [
            todo.statement
            for todo in todos
            if todo.requires_confirmation and todo.status.value == "pending"
        ]
        pending_approvals = [
            approval for approval in approvals if approval.status.value == "pending"
        ]
        blocking_reason = self._build_blocking_reason(
            task_brief,
            progress,
            executions,
            todos,
            session,
        )
        if pending_approvals:
            blocking_reason = blocking_reason or "awaiting_approval"
        summary = progress.summary if progress else ""
        if session.status.value == "completed":
            summary_for_manager = "任务已完成。"
        elif blocking_reason:
            summary_for_manager = f"当前阻塞: {blocking_reason}"
        else:
            summary_for_manager = summary

        return {
            "session": session.model_dump(),
            "task_id": session.active_task_id,
            "status": session.status.value,
            "task_brief": task_brief.model_dump() if task_brief else None,
            "task_flow": task_flow.model_dump() if task_flow else None,
            "global_task": global_task.model_dump() if global_task else None,
            "active_task": active_task.model_dump() if active_task else None,
            "tasks": [task.model_dump() for task in tasks],
            "summary_for_manager": summary_for_manager,
            "progress": {
                "status": progress.status if progress else session.status.value,
                "stage": progress.stage if progress else "",
                "summary": summary,
                "safe_facts": progress.safe_facts if progress else [],
                "uncertain_points": progress.unsafe_claims if progress else [],
                "needs_user_input": progress.needs_user_input if progress else False,
            },
            "blocking_reason": blocking_reason,
            "pending_confirmations": pending_confirmations,
            "pending_approvals": [
                approval.model_dump() for approval in pending_approvals
            ],
            "approvals": [approval.model_dump() for approval in approvals],
            "recent_conversation_refs": [
                ref.model_dump() for ref in conversation_refs
            ],
            "open_todos": [
                todo.model_dump()
                for todo in todos
                if todo.status.value == "pending"
            ],
            "risks": self._build_risks(claims, executions, todos),
            "notifications": [notification.model_dump() for notification in notifications],
            "thinker_dispatches": [dispatch.model_dump() for dispatch in dispatches],
            "allowed_actions": progress.allowed_actions if progress else [],
            "forbidden_actions": progress.forbidden_actions if progress else [],
            "legacy_debug": self._build_legacy_debug(
                await self._get_legacy_debug_state(session_id)
            ),
        }

    async def get_sync_view(self, session_id: str) -> Optional[SyncView]:
        from src.kms.pipeline import sync

        return await sync(self.store, session_id)

    async def get_debug_view(self, session_id: str) -> Dict[str, Any]:
        state = await self._get_raw_state(session_id)
        events = await self.store.get_events(session_id, limit=500)
        return {
            "events": events,
            "session": state["session"].model_dump() if state["session"] else None,
            "task_brief": state["task_brief"].model_dump() if state["task_brief"] else None,
            "task_flow": state["task_flow"].model_dump() if state["task_flow"] else None,
            "progress": state["progress"].model_dump() if state["progress"] else None,
            "tasks": [task.model_dump() for task in state["tasks"]],
            "evidence": [item.model_dump() for item in state["evidence"]],
            "claims": [claim.model_dump() for claim in state["claims"]],
            "executions": [action.model_dump() for action in state["executions"]],
            "todos": [todo.model_dump() for todo in state["todos"]],
            "thinker_dispatches": [dispatch.model_dump() for dispatch in state["thinker_dispatches"]],
            "runtime_references": [ref.model_dump() for ref in state["runtime_references"]],
            "approvals": [approval.model_dump() for approval in state["approvals"]],
            "observer_task_view": (
                state["observer_task_view"].model_dump()
                if state["observer_task_view"]
                else None
            ),
            "legacy_debug": self._build_legacy_debug(
                await self._get_legacy_debug_state(session_id)
            ),
        }

    async def ask_can_say(
        self, session_id: str, proposed_message: str
    ) -> Dict[str, Any]:
        from src.kms.pipeline import gate

        result = await gate(self.store, session_id, proposed_message)
        return {
            "allowed": result.allowed,
            "reason": result.reason,
            "safe_alternative": result.safe_alternative,
        }
