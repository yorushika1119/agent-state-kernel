"""Runtime user-message intent classification for KMS dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


EXPLICIT_NEW_TASK_MARKERS = (
    "新任务",
    "新会话",
    "另外一个任务",
    "另一个任务",
    "换个任务",
    "重新开始",
    "重新开始一个任务",
    "start a fresh task",
    "new task",
    "new session",
)

RESUME_PREVIOUS_TASK_MARKERS = (
    "继续刚才",
    "继续上一个",
    "继续之前",
    "继续原来的",
    "回到刚才",
    "回到一开始",
    "恢复刚才",
    "刚刚被打断",
    "resume previous",
    "continue previous",
    "continue the task",
)

KERNEL_PROGRESS_QUERY_MARKERS = (
    "现在完成到哪",
    "现在到哪一步",
    "现在什么情况",
    "当前状态",
    "当前进度",
    "当前结果",
    "进度如何",
    "做到哪了",
    "目前状态",
    "目前进度",
    "现在能直接告诉",
    "能直接告诉我结果",
    "what is the progress",
    "current status",
    "progress?",
)

KERNEL_FAILURE_QUERY_MARKERS = (
    "哪个工具失败",
    "哪里失败",
    "失败在哪里",
    "失败原因",
    "有什么失败",
    "有失败吗",
    "报错",
    "error",
    "failed",
)

KERNEL_EVIDENCE_QUERY_MARKERS = (
    "有什么证据",
    "当前证据",
    "目前证据",
    "有什么依据",
    "当前依据",
    "目前依据",
    "依据够不够",
    "手头依据",
    "查到什么",
    "查到了什么",
    "找到什么",
    "有哪些来源",
    "evidence",
    "sources",
)

KERNEL_RESUME_QUERY_MARKERS = (
    "还能继续吗",
    "可以继续吗",
    "有暂停的任务",
    "上一个任务",
    "之前的任务",
    "paused task",
)

KERNEL_RUN_QUERY_MARKERS = (
    "哪个 run",
    "当前 run",
    "active run",
    "处理哪个 run",
)

SAME_TASK_STEER_MARKERS = (
    "继续",
    "补充",
    "详细一点",
    "展开",
    "基于刚才",
    "顺着刚才",
    "输出格式",
    "改成表格",
    "then continue",
    "more detail",
)

WORK_REQUEST_MARKERS = (
    "research ",
    "investigate ",
    "analyze ",
    "analyse ",
    "implement ",
    "build ",
    "write ",
    "调研",
    "研究",
    "分析",
    "实现",
)

ALLOWED_INTENTS = {
    "kernel_answerable_query",
    "new_task",
    "resume_previous_task",
    "same_task_steer",
    "unrelated_chat",
    "uncertain",
}

ALLOWED_KERNEL_ANSWER_KINDS = {
    "",
    "progress",
    "failures",
    "evidence",
    "resume",
    "run",
}

RULE_FAST_PATH_CONFIDENCE = 0.85
LLM_MIN_CONFIDENCE = 0.65

INTENT_CLASSIFIER_SYSTEM = """You classify user messages for an agent runtime scheduler.

Return JSON only:
{
  "intent": "kernel_answerable_query | new_task | resume_previous_task | same_task_steer | unrelated_chat | uncertain",
  "confidence": 0.0-1.0,
  "kernel_answer_kind": "progress | failures | evidence | resume | run |",
  "reason": "short reason"
}

Definitions:
- kernel_answerable_query: user asks about current status, progress, failures, evidence, active run, or resumable tasks already known by kernel.
- new_task: user wants to switch to a different task or asks an unrelated work request.
- resume_previous_task: user explicitly wants to continue a paused/previous task.
- same_task_steer: user adds constraints or changes format for the active task.
- unrelated_chat: casual talk that should not affect the active task.
- uncertain: not enough signal.

Do not choose an action. Only classify intent."""


@dataclass(frozen=True)
class DispatchIntent:
    intent: str
    confidence: float
    source: str
    reason: str = ""
    kernel_answer_kind: str = ""


def normalize_text(text: str) -> str:
    return (text or "").strip().lower()


def _contains_any(content: str, markers: tuple[str, ...]) -> bool:
    return any(marker in content for marker in markers)


def classify_dispatch_intent(
    text: str,
    *,
    mode: str = "auto",
    session: Any = None,
    context: Any = None,
) -> DispatchIntent:
    """Classify a user message before KMS turns it into a dispatch decision.

    This is intentionally a deterministic fast path. LLM fallback should be
    added later behind this structured interface.
    """

    content = normalize_text(text)
    if not content:
        return DispatchIntent(
            intent="uncertain",
            confidence=0.0,
            source="rule",
            reason="empty_message",
        )

    if mode == "new_task":
        return DispatchIntent(
            intent="new_task",
            confidence=1.0,
            source="explicit",
            reason="explicit_new_task_mode",
        )

    if _contains_any(content, EXPLICIT_NEW_TASK_MARKERS):
        return DispatchIntent(
            intent="new_task",
            confidence=0.95,
            source="rule",
            reason="explicit_new_task_marker",
        )

    if _contains_any(content, RESUME_PREVIOUS_TASK_MARKERS):
        return DispatchIntent(
            intent="resume_previous_task",
            confidence=0.95,
            source="rule",
            reason="resume_previous_task_marker",
        )

    has_kernel_context = bool(context.has_session) if context is not None else session is not None

    if has_kernel_context:
        if _contains_any(content, KERNEL_FAILURE_QUERY_MARKERS):
            return DispatchIntent(
                intent="kernel_answerable_query",
                confidence=0.9,
                source="rule",
                reason="kernel_failure_query_marker",
                kernel_answer_kind="failures",
            )
        if _contains_any(content, KERNEL_EVIDENCE_QUERY_MARKERS):
            return DispatchIntent(
                intent="kernel_answerable_query",
                confidence=0.9,
                source="rule",
                reason="kernel_evidence_query_marker",
                kernel_answer_kind="evidence",
            )
        if _contains_any(content, KERNEL_RESUME_QUERY_MARKERS):
            return DispatchIntent(
                intent="kernel_answerable_query",
                confidence=0.85,
                source="rule",
                reason="kernel_resume_query_marker",
                kernel_answer_kind="resume",
            )
        if _contains_any(content, KERNEL_RUN_QUERY_MARKERS):
            return DispatchIntent(
                intent="kernel_answerable_query",
                confidence=0.85,
                source="rule",
                reason="kernel_run_query_marker",
                kernel_answer_kind="run",
            )
        if _contains_any(content, KERNEL_PROGRESS_QUERY_MARKERS):
            return DispatchIntent(
                intent="kernel_answerable_query",
                confidence=0.9,
                source="rule",
                reason="kernel_progress_query_marker",
                kernel_answer_kind="progress",
            )

    if _contains_any(content, SAME_TASK_STEER_MARKERS):
        return DispatchIntent(
            intent="same_task_steer",
            confidence=0.75,
            source="rule",
            reason="same_task_steer_marker",
        )

    if has_kernel_context and _contains_any(content, WORK_REQUEST_MARKERS):
        return DispatchIntent(
            intent="new_task",
            confidence=0.85,
            source="rule",
            reason="work_request_marker",
        )

    return DispatchIntent(
        intent="uncertain",
        confidence=0.4,
        source="rule",
        reason="no_rule_matched",
    )


def _clamp_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def _session_summary(session: Any) -> dict[str, Any]:
    if session is None:
        return {"has_session": False}
    return {
        "has_session": True,
        "status": getattr(getattr(session, "status", None), "value", ""),
        "has_active_run": bool(getattr(session, "active_run_id", "")),
        "has_active_task": bool(getattr(session, "active_task_id", "")),
        "has_paused_task": bool(getattr(session, "last_paused_task_id", "")),
        "intent_version": getattr(session, "intent_version", 0),
    }


def _context_summary(context: Any = None, session: Any = None) -> dict[str, Any]:
    if context is not None:
        return context.to_prompt_summary()
    return _session_summary(session)


async def llm_classify_dispatch_intent(
    text: str,
    *,
    session: Any = None,
    context: Any = None,
    model_call: Any = None,
) -> DispatchIntent | None:
    """Ask an LLM to classify ambiguous dispatch intent.

    The LLM only returns a structured suggestion. KMS still owns the final
    dispatch decision.
    """

    if model_call is None:
        from src.kms.model import ModelCall

        model_call = ModelCall()

    ask_json = getattr(model_call, "ask_json", None)
    if ask_json is None:
        return None

    result = await ask_json(
        system=INTENT_CLASSIFIER_SYSTEM,
        user=(
            f"User message: {text}\n"
            f"Kernel dispatch context: {_context_summary(context, session)}\n"
            "Classify the message."
        ),
        max_tokens=220,
    )
    if not isinstance(result, dict):
        return None

    intent = str(result.get("intent") or "").strip()
    if intent not in ALLOWED_INTENTS:
        return None

    confidence = _clamp_confidence(result.get("confidence"))
    kernel_answer_kind = str(result.get("kernel_answer_kind") or "").strip()
    if kernel_answer_kind not in ALLOWED_KERNEL_ANSWER_KINDS:
        kernel_answer_kind = ""

    has_kernel_context = bool(context.has_session) if context is not None else session is not None
    if intent == "kernel_answerable_query" and not has_kernel_context:
        return None

    return DispatchIntent(
        intent=intent,
        confidence=confidence,
        source="llm",
        reason=str(result.get("reason") or "llm_intent_classifier"),
        kernel_answer_kind=kernel_answer_kind,
    )


async def classify_dispatch_intent_with_llm(
    text: str,
    *,
    mode: str = "auto",
    session: Any = None,
    context: Any = None,
    model_call: Any = None,
    enable_llm: bool = True,
) -> DispatchIntent:
    fast_path = classify_dispatch_intent(text, mode=mode, session=session, context=context)
    if fast_path.source == "explicit" or fast_path.confidence >= RULE_FAST_PATH_CONFIDENCE:
        return fast_path

    has_kernel_context = bool(context.has_session) if context is not None else session is not None
    if not has_kernel_context:
        return fast_path

    if not enable_llm:
        return fast_path

    llm_intent = await llm_classify_dispatch_intent(
        text,
        session=session,
        context=context,
        model_call=model_call,
    )
    if llm_intent is None or llm_intent.confidence < LLM_MIN_CONFIDENCE:
        return fast_path
    return llm_intent
