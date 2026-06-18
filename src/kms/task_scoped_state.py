"""Task-local state filtering helpers for KMS direct replies."""

from __future__ import annotations

import json
from typing import Any

from src.schema.events import EventType
from src.schema.state import ClaimItem, ExecutionAction, EvidenceItem, TaskSnapshot, TodoObligation


class TaskScopedStateFilter:
    def __init__(self, store):
        self.store = store

    async def filter_evidence(
        self,
        session_id: str,
        evidence: list[EvidenceItem],
        task: TaskSnapshot,
    ) -> list[EvidenceItem]:
        native = [item for item in evidence if item.task_id == task.task_id]
        legacy = [item for item in evidence if not item.task_id]
        evidence_ids: set[str] = set()
        for claim in await self.store.get_claim_items(session_id):
            if claim.task_id == task.task_id:
                evidence_ids.update(claim.supporting_evidence)
                evidence_ids.update(claim.conflicting_evidence)

        payloads = await self._task_event_payloads(
            session_id,
            task,
            {
                EventType.EVIDENCE_CANDIDATE_FOUND.value,
                EventType.EVIDENCE_ACCEPTED.value,
            },
        )
        evidence_ids.update(
            str(payload.get("evidence_id") or "")
            for payload in payloads
            if payload.get("evidence_id")
        )
        return native + [item for item in legacy if item.evidence_id in evidence_ids]

    async def filter_claims(
        self,
        session_id: str,
        claims: list[ClaimItem],
        task: TaskSnapshot,
    ) -> list[ClaimItem]:
        native = [item for item in claims if item.task_id == task.task_id]
        legacy = [item for item in claims if not item.task_id]
        payloads = await self._task_event_payloads(
            session_id,
            task,
            {
                EventType.BELIEF_PROPOSED.value,
                EventType.BELIEF_UPDATED.value,
                EventType.RISK_ASSESSMENT.value,
                EventType.CONFLICT_DETECTED.value,
                EventType.VERIFICATION_WARNING_RAISED.value,
                EventType.VERIFICATION_RESULT.value,
            },
        )
        claim_ids = {
            str(
                payload.get("claim_id")
                or payload.get("belief_id")
                or payload.get("assessment_id")
                or ""
            )
            for payload in payloads
        }
        return native + [item for item in legacy if item.claim_id in claim_ids]

    async def filter_todos(
        self,
        session_id: str,
        todos: list[TodoObligation],
        task: TaskSnapshot,
    ) -> list[TodoObligation]:
        native = [item for item in todos if item.task_id == task.task_id]
        legacy = [item for item in todos if not item.task_id]
        payloads = await self._task_event_payloads(
            session_id,
            task,
            {
                EventType.COMMITMENT_CREATED.value,
                EventType.COMMITMENT_UPDATED.value,
                EventType.USER_CONFIRMATION_REQUIRED.value,
            },
        )
        obligation_ids = {
            str(payload.get("obligation_id") or payload.get("commitment_id") or "")
            for payload in payloads
        }
        return native + [
            item
            for item in legacy
            if item.obligation_id in obligation_ids
        ]

    async def filter_executions(
        self,
        session_id: str,
        executions: list[ExecutionAction],
        task: TaskSnapshot,
    ) -> list[ExecutionAction]:
        native = [item for item in executions if item.task_id == task.task_id]
        legacy = [item for item in executions if not item.task_id]
        payloads = await self._task_event_payloads(
            session_id,
            task,
            {
                EventType.TOOL_STARTED.value,
                EventType.TOOL_COMPLETED.value,
                EventType.TOOL_FAILED.value,
                EventType.TOOL_RETRIED.value,
                EventType.ACTION_BLOCKED.value,
            },
        )
        action_ids = {
            str(payload.get("action_id") or "")
            for payload in payloads
            if payload.get("action_id")
        }
        if action_ids:
            return native + [item for item in legacy if item.action_id in action_ids]

        step_ids = self.task_step_ids(task)
        return native + [item for item in legacy if item.step_id in step_ids]

    def task_step_ids(self, task: TaskSnapshot) -> set[str]:
        step_ids = {task.current_step, task.last_run_id}
        for step in task.steps:
            step_ids.add(str(step.get("step_id") or ""))
        return {item for item in step_ids if item}

    async def _task_run_ids(self, session_id: str, task: TaskSnapshot) -> set[str]:
        run_ids = {
            item
            for item in (task.last_run_id, task.last_interrupted_run_id)
            if item
        }
        dispatches = await self.store.list_thinker_dispatches(
            kernel_session_id=session_id,
            task_id=task.task_id,
            limit=200,
        )
        run_ids.update(dispatch.run_id for dispatch in dispatches if dispatch.run_id)
        return run_ids

    def _event_payload(self, event: dict) -> dict[str, Any]:
        payload = event.get("payload") or {}
        if isinstance(payload, dict):
            return payload
        if not isinstance(payload, str):
            return {}
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def _task_event_payloads(
        self,
        session_id: str,
        task: TaskSnapshot,
        event_types: set[str],
    ) -> list[dict[str, Any]]:
        run_ids = await self._task_run_ids(session_id, task)
        events = await self.store.get_events(session_id, limit=1000)
        payloads: list[dict[str, Any]] = []
        for event in events:
            if event.get("event_type") not in event_types:
                continue
            payload = self._event_payload(event)
            if payload.get("task_id") == task.task_id or event.get("run_id") in run_ids:
                payloads.append(payload)
        return payloads
