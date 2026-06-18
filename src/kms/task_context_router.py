"""Task Context Router for user-session scoped task selection."""

from __future__ import annotations

import re
from typing import Any, Iterable

from src.schema.state import GlobalTask, TaskRouteDecision, TaskStatus


NEW_TASK_REFERENCES = (
    "new task",
    "new session",
    "start a fresh task",
    "fresh task",
    "restart task",
    "新任务",
    "新会话",
    "另开",
    "重新开始",
)

RECENT_REFERENCES = (
    "previous",
    "last task",
    "just now",
    "刚才",
    "刚刚",
    "上一个",
    "之前",
    "原来的",
)

OTHER_REFERENCES = (
    "other task",
    "the other",
    "another one",
    "另一个",
    "另外一个",
    "不是这个",
)

AMBIGUOUS_REFERENCES = (
    "that one",
    "那个",
    "这个",
    "那件事",
    "那个任务",
)

STATUS_QUERY_REFERENCES = (
    "current status",
    "what is the progress",
    "progress",
    "status",
    "当前状态",
    "当前进度",
    "现在进度",
    "做到哪",
    "完成到哪",
    "怎么样了",
    "现在怎么样",
)

WORK_REQUEST_REFERENCES = (
    "research ",
    "investigate ",
    "analyze ",
    "analyse ",
    "implement ",
    "build ",
    "write ",
    "fix ",
    "create ",
    "调研",
    "研究",
    "分析",
    "实现",
    "修复",
    "编写",
)

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "task",
    "current",
    "status",
    "progress",
    "continue",
    "please",
    "research",
    "investigate",
    "analyze",
    "analyse",
    "implement",
    "build",
    "write",
    "fix",
    "create",
}

ALLOWED_ROUTING_DECISIONS = {"select_existing", "create_new", "ask_clarification"}
ROUTE_FAST_PATH_CONFIDENCE = 0.75
LLM_ROUTE_MIN_CONFIDENCE = 0.65
LLM_SELECT_WITH_WEAK_TASK_SCORE_MIN_CONFIDENCE = 0.8
LLM_CREATE_NEW_MIN_CONFIDENCE = 0.72
LLM_CLARIFICATION_MIN_CONFIDENCE = 0.55

TASK_ROUTE_SYSTEM = """You route a user message to an existing task or a new task.

Return JSON only:
{
  "routing_decision": "select_existing | create_new | ask_clarification",
  "target_task_id": "task id when selecting existing, otherwise empty",
  "confidence": 0.0-1.0,
  "clarification_question": "short question when clarification is needed",
  "reason": "short reason"
}

Rules:
- select_existing only when one candidate clearly matches the user's reference.
- create_new when the user is asking for unrelated new work.
- ask_clarification when more than one candidate is plausible.
- target_task_id must be one of the candidate task ids.
- Do not decide whether to interrupt or execute. Only route task context."""


def _normalize(text: str) -> str:
    return (text or "").strip().lower()


def _contains_any(text: str, markers: Iterable[str]) -> bool:
    content = _normalize(text)
    return any(_normalize(marker) in content for marker in markers)


def _tokens(text: str) -> set[str]:
    content = _normalize(text)
    raw = re.findall(r"[a-z0-9][a-z0-9_-]{1,}|\d+|[\u4e00-\u9fff]{2,}", content)
    return {token for token in raw if token not in STOPWORDS}


def _latest_task(tasks: list[GlobalTask], *, exclude_task_id: str = "") -> GlobalTask | None:
    pool = [task for task in tasks if task.task_id != exclude_task_id]
    if not pool:
        return None
    return max(
        pool,
        key=lambda task: (
            task.last_user_touch_at or task.updated_at,
            task.last_activity_at or task.updated_at,
        ),
    )


def _single_active_task(tasks: list[GlobalTask]) -> GlobalTask | None:
    active = [task for task in tasks if task.status == TaskStatus.ACTIVE]
    if len(active) == 1:
        return active[0]
    return None


def _score_task(text: str, task: GlobalTask) -> tuple[float, list[str]]:
    matched: list[str] = []
    score = 0.0
    content = _normalize(text)
    task_text = f"{task.title} {task.task_description} {' '.join(task.routing_hints)}"

    for hint in task.routing_hints:
        marker = _normalize(hint)
        if marker in STOPWORDS:
            continue
        if marker and marker in content:
            matched.append(hint)
            score += 0.35

    for value in (task.title, task.task_description):
        marker = _normalize(value)
        if marker and marker[:24] in content:
            matched.append(value[:24])
            score += 0.4

    overlap = _tokens(content) & _tokens(task_text)
    if overlap:
        matched.extend(sorted(overlap)[:6])
        score += min(0.45, 0.12 * len(overlap))

    task_type = _normalize(task.task_type)
    if task_type and task_type != "other" and task_type in content:
        matched.append(task.task_type)
        score += 0.2

    if task.status in {TaskStatus.ACTIVE, TaskStatus.PAUSED, TaskStatus.BLOCKED}:
        score += 0.05
    if matched and task_text.strip():
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

    if _contains_any(content, NEW_TASK_REFERENCES):
        return TaskRouteDecision(
            user_session_id=user_session_id,
            runtime_session_id=runtime_session_id,
            user_message=content,
            routing_decision="create_new",
            confidence=0.95,
            time_reason={"reference": "new_task"},
        )

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
            item[2].last_activity_at or item[2].updated_at,
        ),
        reverse=True,
    )

    best = ranked[0]
    if _contains_any(content, WORK_REQUEST_REFERENCES) and best[0] < 0.7:
        return TaskRouteDecision(
            user_session_id=user_session_id,
            runtime_session_id=runtime_session_id,
            user_message=content,
            routing_decision="create_new",
            confidence=0.76,
            candidate_tasks=[_candidate(score, task) for score, _, task in ranked[:3]],
            time_reason={"reference": "work_request"},
        )

    if _contains_any(content, AMBIGUOUS_REFERENCES) and len(candidates) > 1 and best[0] < 0.45:
        return _clarify(
            content,
            user_session_id=user_session_id,
            runtime_session_id=runtime_session_id,
            ranked=ranked,
        )

    if _contains_any(content, STATUS_QUERY_REFERENCES):
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        if best[0] >= 0.45 and best[0] - second_score >= 0.12:
            return _select(
                content,
                user_session_id=user_session_id,
                runtime_session_id=runtime_session_id,
                task=best[2],
                confidence=best[0],
                matched_hints=best[1],
                ranked=ranked,
                time_reason={"reference": "status_query", "matched_by": "task_score"},
            )
        active = _single_active_task(candidates)
        if active:
            return _select(
                content,
                user_session_id=user_session_id,
                runtime_session_id=runtime_session_id,
                task=active,
                confidence=0.82,
                matched_hints=["active_task"],
                ranked=ranked,
                time_reason={"reference": "status_query", "matched_by": "active_task"},
            )
        if len(candidates) == 1:
            return _select(
                content,
                user_session_id=user_session_id,
                runtime_session_id=runtime_session_id,
                task=candidates[0],
                confidence=0.74,
                matched_hints=["single_task_status_query"],
                ranked=ranked,
                time_reason={"reference": "status_query", "matched_by": "single_task"},
            )

    if _contains_any(content, OTHER_REFERENCES) and len(ranked) > 1:
        current = _single_active_task(candidates) or ranked[0][2]
        other = _latest_task(candidates, exclude_task_id=current.task_id)
        if other:
            selected = next(item for item in ranked if item[2].task_id == other.task_id)
            return _select(
                content,
                user_session_id=user_session_id,
                runtime_session_id=runtime_session_id,
                task=selected[2],
                confidence=0.72,
                matched_hints=selected[1],
                ranked=ranked,
                time_reason={"reference": "other", "matched_by": "other_recent_task"},
            )

    if _contains_any(content, RECENT_REFERENCES):
        selected = ranked[0]
        return _select(
            content,
            user_session_id=user_session_id,
            runtime_session_id=runtime_session_id,
            task=selected[2],
            confidence=0.78,
            matched_hints=selected[1],
            ranked=ranked,
            time_reason={"reference": "recent", "matched_by": "last_user_touch_at"},
        )

    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    if best[0] >= 0.45 and best[0] - second_score >= 0.12:
        return _select(
            content,
            user_session_id=user_session_id,
            runtime_session_id=runtime_session_id,
            task=best[2],
            confidence=best[0],
            matched_hints=best[1],
            ranked=ranked,
        )

    if len(candidates) > 1:
        return _clarify(
            content,
            user_session_id=user_session_id,
            runtime_session_id=runtime_session_id,
            ranked=ranked,
        )

    return TaskRouteDecision(
        user_session_id=user_session_id,
        runtime_session_id=runtime_session_id,
        user_message=content,
        routing_decision="create_new",
        confidence=0.65,
        candidate_tasks=[_candidate(best[0], best[2])],
    )


async def route_task_context_with_llm(
    text: str,
    *,
    user_session_id: str = "",
    runtime_session_id: str = "",
    tasks: list[GlobalTask] | None = None,
    model_call: Any = None,
    enable_llm: bool = True,
) -> TaskRouteDecision:
    rule_route = route_task_context(
        text,
        user_session_id=user_session_id,
        runtime_session_id=runtime_session_id,
        tasks=tasks,
    )
    candidates = tasks or []
    if (
        not enable_llm
        or not candidates
        or (
            rule_route.confidence >= ROUTE_FAST_PATH_CONFIDENCE
            and not rule_route.needs_user_clarification
        )
    ):
        return rule_route

    llm_route = await llm_route_task_context(
        text,
        user_session_id=user_session_id,
        runtime_session_id=runtime_session_id,
        tasks=candidates,
        rule_route=rule_route,
        model_call=model_call,
    )
    accepted, reason = _accept_llm_route(rule_route, llm_route)
    if not accepted:
        return rule_route
    llm_route.time_reason["acceptance_reason"] = reason
    return llm_route


async def llm_route_task_context(
    text: str,
    *,
    user_session_id: str = "",
    runtime_session_id: str = "",
    tasks: list[GlobalTask],
    rule_route: TaskRouteDecision,
    model_call: Any = None,
) -> TaskRouteDecision | None:
    if model_call is None:
        from src.kms.model import ModelCall

        model_call = ModelCall()

    ask_json = getattr(model_call, "ask_json", None)
    if ask_json is None:
        return None

    candidates = _route_candidates(text, tasks, rule_route)
    result = await ask_json(
        system=TASK_ROUTE_SYSTEM,
        user=(
            f"User message: {text}\n"
            f"Rule route: {_route_summary(rule_route)}\n"
            f"Candidate tasks: {candidates}\n"
            "Route the message."
        ),
        max_tokens=260,
    )
    if not isinstance(result, dict):
        return None

    routing_decision = str(result.get("routing_decision") or "").strip()
    if routing_decision not in ALLOWED_ROUTING_DECISIONS:
        return None

    confidence = _clamp_confidence(result.get("confidence"))
    target_task_id = str(result.get("target_task_id") or "").strip()
    candidate_ids = {str(item.get("task_id") or "") for item in candidates}
    if routing_decision == "select_existing" and target_task_id not in candidate_ids:
        return None

    reason = str(result.get("reason") or "llm_task_router")
    time_reason = {
        "source": "llm_router",
        "rule_decision": rule_route.routing_decision,
        "rule_confidence": rule_route.confidence,
        "reason": reason,
    }
    if routing_decision == "select_existing":
        return TaskRouteDecision(
            user_session_id=user_session_id,
            runtime_session_id=runtime_session_id,
            user_message=(text or "").strip(),
            routing_decision="select_existing",
            target_task_id=target_task_id,
            confidence=confidence,
            matched_hints=["llm_router"],
            time_reason=time_reason,
            candidate_tasks=candidates,
        )
    if routing_decision == "ask_clarification":
        return TaskRouteDecision(
            user_session_id=user_session_id,
            runtime_session_id=runtime_session_id,
            user_message=(text or "").strip(),
            routing_decision="ask_clarification",
            confidence=confidence,
            time_reason=time_reason,
            candidate_tasks=candidates,
            needs_user_clarification=True,
            clarification_question=str(
                result.get("clarification_question") or "你指的是哪一个任务？"
            ),
        )
    return TaskRouteDecision(
        user_session_id=user_session_id,
        runtime_session_id=runtime_session_id,
        user_message=(text or "").strip(),
        routing_decision="create_new",
        confidence=confidence,
        time_reason=time_reason,
        candidate_tasks=candidates,
    )


def _select(
    content: str,
    *,
    user_session_id: str,
    runtime_session_id: str,
    task: GlobalTask,
    confidence: float,
    matched_hints: list[str],
    ranked: list[tuple[float, list[str], GlobalTask]],
    time_reason: dict | None = None,
) -> TaskRouteDecision:
    return TaskRouteDecision(
        user_session_id=user_session_id,
        runtime_session_id=runtime_session_id,
        user_message=content,
        routing_decision="select_existing",
        target_task_id=task.task_id,
        confidence=confidence,
        matched_hints=matched_hints,
        time_reason=time_reason or {},
        candidate_tasks=[_candidate(score, item) for score, _, item in ranked[:3]],
    )


def _clarify(
    content: str,
    *,
    user_session_id: str,
    runtime_session_id: str,
    ranked: list[tuple[float, list[str], GlobalTask]],
) -> TaskRouteDecision:
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


def _route_candidates(
    text: str,
    tasks: list[GlobalTask],
    rule_route: TaskRouteDecision,
) -> list[dict[str, Any]]:
    if rule_route.candidate_tasks:
        return rule_route.candidate_tasks[:5]
    ranked = []
    for task in tasks:
        score, _matched = _score_task(text, task)
        ranked.append((score, task))
    ranked.sort(
        key=lambda item: (
            item[0],
            item[1].last_user_touch_at or item[1].updated_at,
            item[1].last_activity_at or item[1].updated_at,
        ),
        reverse=True,
    )
    return [_candidate(score, task) for score, task in ranked[:5]]


def _route_summary(route: TaskRouteDecision) -> dict[str, Any]:
    return {
        "routing_decision": route.routing_decision,
        "target_task_id": route.target_task_id,
        "confidence": route.confidence,
        "needs_user_clarification": route.needs_user_clarification,
        "matched_hints": route.matched_hints,
        "time_reason": route.time_reason,
    }


def _accept_llm_route(
    rule_route: TaskRouteDecision,
    llm_route: TaskRouteDecision | None,
) -> tuple[bool, str]:
    if llm_route is None:
        return False, "missing_llm_route"
    if llm_route.confidence < LLM_ROUTE_MIN_CONFIDENCE:
        return False, "llm_confidence_below_min"

    if llm_route.routing_decision == "ask_clarification":
        if llm_route.confidence >= LLM_CLARIFICATION_MIN_CONFIDENCE:
            return True, "llm_requested_clarification"
        return False, "llm_clarification_confidence_below_min"

    if rule_route.routing_decision == llm_route.routing_decision:
        if llm_route.routing_decision != "select_existing":
            return True, "llm_agrees_with_rule_decision"
        if rule_route.target_task_id == llm_route.target_task_id:
            return True, "llm_agrees_with_rule_target"

    if llm_route.routing_decision == "select_existing":
        selected = _candidate_by_task_id(llm_route.candidate_tasks, llm_route.target_task_id)
        selected_score = float(selected.get("score") or 0.0) if selected else 0.0
        if selected_score >= 0.45:
            return True, "llm_target_has_rule_support"
        if (
            rule_route.needs_user_clarification
            and llm_route.confidence >= LLM_SELECT_WITH_WEAK_TASK_SCORE_MIN_CONFIDENCE
        ):
            return True, "llm_resolved_rule_clarification"
        return False, "llm_select_lacks_rule_support"

    if llm_route.routing_decision == "create_new":
        if (
            rule_route.routing_decision == "create_new"
            and llm_route.confidence >= LLM_ROUTE_MIN_CONFIDENCE
        ):
            return True, "llm_agrees_with_create_new"
        if llm_route.confidence >= LLM_CREATE_NEW_MIN_CONFIDENCE:
            return True, "llm_create_new_high_confidence"
        return False, "llm_create_new_confidence_below_min"

    return False, "unsupported_llm_route"


def _candidate_by_task_id(candidate_tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any] | None:
    return next(
        (item for item in candidate_tasks if str(item.get("task_id") or "") == task_id),
        None,
    )


def _clamp_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))
