"""Reduce stage for KMS derived state updates."""

from __future__ import annotations

import logging
from typing import Optional

from src.kernel.state_reducer import (
    reduce_beliefs,
    reduce_commitments,
    reduce_evidence,
    reduce_execution,
    reduce_intent,
    reduce_plan,
)
from src.kms.runtime.execution_payload import merge_execution_payload
from src.kms.state.aliases import (
    beliefs_from_claims,
    commitments_from_todos,
    intent_from_task_brief,
    plan_from_task_flow,
)
from src.schema.events import CognitiveEvent, EventType
from src.schema.state import ApprovalRequest, ApprovalRequestStatus, BeliefStatus

logger = logging.getLogger(__name__)


async def reduce(
    store,
    session_id: str,
    event: CognitiveEvent,
    _processed: Optional[set] = None,
) -> None:
    """调用 State Reducer 从事件更新派生状态。"""

    if _processed is not None:
        if event.event_id in _processed:
            return
        _processed.add(event.event_id)

    et = event.event_type
    payload = event.payload

    current_intent = intent_from_task_brief(await store.get_task_brief(session_id))
    old_goal = current_intent.goal if current_intent else ""
    old_version = current_intent.intent_version if current_intent else 0
    new_intent = reduce_intent(current_intent, et, payload)
    if new_intent:
        await store.save_intent(session_id, new_intent)
        if et == EventType.SESSION_CANCELLED:
            await store.update_session_status(
                session_id,
                "cancelled",
                active_run_id="",
            )
            await store.set_cancellation_token(session_id, True)
        if current_intent and new_intent.intent_version != old_version:
            session = await store.get_session(session_id)
            await store.update_session_status(
                session_id,
                session.status.value if session else "running",
                intent_version=new_intent.intent_version,
            )
            if new_intent.goal and old_goal and new_intent.goal != old_goal:
                await store.set_cancellation_token(session_id, True)

    current_plan = plan_from_task_flow(await store.get_task_flow(session_id))
    new_plan = reduce_plan(current_plan, et, payload)
    if et in (
        EventType.PLAN_ACCEPTED,
        EventType.REPLAN_REQUEST,
        EventType.STEP_STARTED,
        EventType.TASK_COMPLETED,
        EventType.TASK_FAILED,
    ):
        if new_plan:
            await store.save_plan(session_id, new_plan)

    evidence_items = await store.get_evidence(session_id)
    reduce_evidence(evidence_items, et, payload)
    if et == EventType.EVIDENCE_ACCEPTED:
        ev_id = payload.get("evidence_id", "")
        saved = False
        for evidence in evidence_items:
            if evidence.evidence_id == ev_id:
                await store.save_evidence(session_id, evidence)
                saved = True
                break
        if not saved and evidence_items:
            await store.save_evidence(session_id, evidence_items[-1])

    beliefs = beliefs_from_claims(await store.get_claim_items(session_id))
    reduce_beliefs(beliefs, et, payload)
    if et in (
        EventType.BELIEF_UPDATED,
        EventType.CONFLICT_DETECTED,
        EventType.VERIFICATION_WARNING_RAISED,
        EventType.RISK_ASSESSMENT,
        EventType.VERIFICATION_RESULT,
    ):
        target_id = payload.get("belief_id") or payload.get("assessment_id", "")
        for belief in beliefs:
            is_risk = et == EventType.RISK_ASSESSMENT and belief.claim.startswith("[风险]")
            if belief.belief_id == target_id or is_risk:
                if not belief.claim or belief.confidence < 0.0 or belief.confidence > 1.0:
                    logger.warning("KMS: Belief %s integrity check failed", belief.belief_id)
                    belief.status = BeliefStatus.UNVERIFIED
                    belief.confidence = 0.0
                await store.save_belief(session_id, belief)
                break
        if et == EventType.RISK_ASSESSMENT and beliefs:
            await store.save_belief(session_id, beliefs[-1])

    executions = await store.get_executions(session_id)
    execution_payload = merge_execution_payload(event)
    reduce_execution(executions, et, execution_payload)
    if et in (
        EventType.TOOL_STARTED,
        EventType.TOOL_COMPLETED,
        EventType.TOOL_FAILED,
        EventType.REASONING_SUMMARY,
        EventType.RAW_RESULT_AVAILABLE,
        EventType.ACTION_BLOCKED,
    ):
        action_id = (
            execution_payload.get("action_id")
            or execution_payload.get("reasoning_id")
            or execution_payload.get("result_id", "")
        )
        for action in executions:
            if action.action_id == action_id:
                await store.save_execution(session_id, action)
                break
        else:
            if executions:
                await store.save_execution(session_id, executions[-1])

    commitments = commitments_from_todos(await store.get_todo_obligations(session_id))
    reduce_commitments(commitments, et, payload)
    if et in (EventType.COMMITMENT_CREATED, EventType.COMMITMENT_UPDATED):
        commitment_id = payload.get("commitment_id", "")
        for commitment in commitments:
            if commitment.commitment_id == commitment_id:
                await store.save_commitment(session_id, commitment)
                break

    if et == EventType.APPROVAL_REQUESTED:
        approval_request_id = (
            payload.get("approval_request_id")
            or payload.get("approval_id")
            or f"apr_{event.event_id[-12:]}"
        )
        # 防护：重放/重复的 ApprovalRequested 不能把一个"已决"的批准打回 pending
        existing = await store.get_approval_request(approval_request_id)
        already_decided = existing is not None and (
            getattr(existing.status, "value", existing.status)
            != ApprovalRequestStatus.PENDING.value
        )
        if not already_decided:
            # 防御：payload 由外部(Thinker/工作台/集成)填入，字段可能是 None 或类型不对，
            # 一律兜底，避免构造 ApprovalRequest 时 pydantic 校验失败而崩掉整个事件写入。
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            approval = ApprovalRequest(
                approval_request_id=approval_request_id,
                kernel_session_id=session_id,
                task_id=payload.get("task_id") or "",
                requested_action=payload.get("requested_action") or "",
                action_summary=payload.get("action_summary") or "",
                risk_summary=payload.get("risk_summary") or "",
                requested_by=payload.get("requested_by")
                or (event.source_component or event.actor.value),
                task_brief_version=payload.get("task_brief_version") or 0,
                metadata=metadata,
            )
            await store.save_approval_request(approval)
    elif et in (
        EventType.APPROVAL_GRANTED,
        EventType.APPROVAL_DENIED,
        EventType.APPROVAL_REVOKED,
    ):
        status_by_event = {
            EventType.APPROVAL_GRANTED: ApprovalRequestStatus.GRANTED,
            EventType.APPROVAL_DENIED: ApprovalRequestStatus.DENIED,
            EventType.APPROVAL_REVOKED: ApprovalRequestStatus.REVOKED,
        }
        await store.update_approval_decision(
            payload.get("approval_request_id", ""),
            status_by_event[et],
            decided_by=payload.get("decided_by", ""),
            comment=payload.get("comment", ""),
            task_brief_version=payload.get("task_brief_version", 0),
        )
