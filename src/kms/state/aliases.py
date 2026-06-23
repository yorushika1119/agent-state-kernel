"""Task-first state aliases used by the KMS event pipeline."""

from __future__ import annotations

from typing import Any, Optional

from src.schema.state import (
    BeliefItem,
    Commitment,
    IntentState,
    PlanState,
    PlanStep,
)


def intent_from_task_brief(task_brief: Any) -> Optional[IntentState]:
    if not task_brief:
        return None
    return IntentState(
        intent_version=task_brief.task_brief_version,
        goal=task_brief.goal,
        output_format=task_brief.output_format,
        constraints=task_brief.constraints,
        priority=task_brief.priority,
        cancelled=task_brief.cancelled,
        last_user_update_at=task_brief.updated_at,
    )


def plan_from_task_flow(task_flow: Any) -> Optional[PlanState]:
    if not task_flow:
        return None
    return PlanState(
        plan_id=task_flow.flow_id,
        status=task_flow.status,
        steps=[PlanStep(**step) for step in task_flow.steps],
        current_step=task_flow.current_step,
        intent_version=task_flow.task_brief_version,
    )


def beliefs_from_claims(claims: list[Any]) -> list[BeliefItem]:
    return [
        BeliefItem(
            belief_id=claim.claim_id,
            claim=claim.claim,
            status=claim.status,
            confidence=claim.confidence,
            supporting_evidence=claim.supporting_evidence,
            conflicting_evidence=claim.conflicting_evidence,
            visibility=claim.visibility,
            last_verified_at=claim.last_verified_at,
        )
        for claim in claims
    ]


def commitments_from_todos(todos: list[Any]) -> list[Commitment]:
    return [
        Commitment(
            commitment_id=todo.obligation_id,
            statement=todo.statement,
            created_by=todo.created_by,
            status=todo.status,
            requires_confirmation=todo.requires_confirmation,
            related_intent_version=todo.related_task_brief_version,
            resolved_at=todo.resolved_at,
        )
        for todo in todos
    ]
