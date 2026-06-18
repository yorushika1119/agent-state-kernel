"""Minimal Task Context Router for user-session scoped task selection."""

from __future__ import annotations

from typing import Iterable

from src.schema.state import GlobalTask, TaskRouteDecision, TaskStatus


RECENT_REFERENCES = (
    "刚才",
    "刚刚",
    "上一个",
    "之前",
    "原来的",
    "previous",
    "last task",
)

OTHER_REFERENCES = (
    "另一个",
    "另外一个",
    "不是这个",
    "other task",
)


def _contains_any(text: str, markers: Iterable[str]) -> bool:
    return any(marker in text for marker in markers)


def _score_task(text: str, task: GlobalTask) -> tuple[float, list[str]]:
    matched: list[str] = []
    score = 0.0
    haystack = f"{task.title} {task.task_description} {' '.join(task.routing_hints)}".lower()
    content = text.lower()

    for hint in task.routing_hints:
        marker = hint.lower().strip()
        if marker and marker in content:
            matched.append(hint)
            score += 0.35

    for token in (task.title, task.task_description):
        marker = (token or "").lower().strip()
        if marker and marker[:24] in content:
            matched.append(token[:24])
            score += 0.4

    if task.status in {TaskStatus.ACTIVE, TaskStatus.PAUSED, TaskStatus.BLOCKED}:
        score += 0.05
    if matched and haystack:
        score += 0.1
    return min(score, 1.0), matched[:6]


def route_task_context(
    text: str,
    *,
    user_session_id: str = "",
    runtime_session_id: str = "",
    tasks: list[GlobalTask] | None = None,
) -> TaskRouteDecision:
    content = (text or "").strip()
    candidates = tasks or []

    if not candidates:
        return TaskRouteDecision(
            user_session_id=user_session_id,
            runtime_session_id=runtime_session_id,
            user_message=content,
            routing_decision="create_new",
            confidence=0.8,
        )

    ranked = []
    for task in candidates:
        score, matched = _score_task(content, task)
        ranked.append((score, matched, task))
    ranked.sort(
        key=lambda item: (
            item[0],
            item[2].last_user_touch_at or item[2].updated_at,
        ),
        reverse=True,
    )

    if _contains_any(content, OTHER_REFERENCES) and len(ranked) > 1:
        selected = ranked[1]
        return TaskRouteDecision(
            user_session_id=user_session_id,
            runtime_session_id=runtime_session_id,
            user_message=content,
            routing_decision="select_existing",
            target_task_id=selected[2].task_id,
            confidence=0.72,
            matched_hints=selected[1],
            time_reason={"reference": "other", "matched_by": "second_recent_task"},
            candidate_tasks=[_candidate(score, task) for score, _, task in ranked[:3]],
        )

    if _contains_any(content, RECENT_REFERENCES):
        selected = ranked[0]
        return TaskRouteDecision(
            user_session_id=user_session_id,
            runtime_session_id=runtime_session_id,
            user_message=content,
            routing_decision="select_existing",
            target_task_id=selected[2].task_id,
            confidence=0.78,
            matched_hints=selected[1],
            time_reason={"reference": "recent", "matched_by": "last_user_touch_at"},
            candidate_tasks=[_candidate(score, task) for score, _, task in ranked[:3]],
        )

    best = ranked[0]
    if best[0] >= 0.45:
        return TaskRouteDecision(
            user_session_id=user_session_id,
            runtime_session_id=runtime_session_id,
            user_message=content,
            routing_decision="select_existing",
            target_task_id=best[2].task_id,
            confidence=best[0],
            matched_hints=best[1],
            candidate_tasks=[_candidate(score, task) for score, _, task in ranked[:3]],
        )

    if len(candidates) > 1:
        return TaskRouteDecision(
            user_session_id=user_session_id,
            runtime_session_id=runtime_session_id,
            user_message=content,
            routing_decision="ask_clarification",
            confidence=0.35,
            candidate_tasks=[_candidate(score, task) for score, _, task in ranked[:3]],
            needs_user_clarification=True,
            clarification_question="你指的是哪一个任务？",
        )

    return TaskRouteDecision(
        user_session_id=user_session_id,
        runtime_session_id=runtime_session_id,
        user_message=content,
        routing_decision="create_new",
        confidence=0.65,
        candidate_tasks=[_candidate(best[0], best[2])],
    )


def _candidate(score: float, task: GlobalTask) -> dict:
    return {
        "task_id": task.task_id,
        "title": task.title,
        "task_type": task.task_type,
        "task_description": task.task_description,
        "score": round(score, 3),
        "status": task.status.value,
        "last_user_touch_at": task.last_user_touch_at.isoformat()
        if task.last_user_touch_at
        else None,
        "last_activity_at": task.last_activity_at.isoformat()
        if task.last_activity_at
        else None,
    }
