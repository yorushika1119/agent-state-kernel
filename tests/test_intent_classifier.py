from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kms.decisioning.intent_classifier import (
    classify_dispatch_intent,
    classify_dispatch_intent_with_llm,
)


class FakeModel:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def ask_json(self, system: str, user: str, max_tokens: int = 200):
        self.calls += 1
        return self.result


def active_session():
    return SimpleNamespace(
        status=SimpleNamespace(value="running"),
        active_run_id="run_active",
        active_task_id="task_active",
        last_paused_task_id="",
        intent_version=1,
    )


def test_rule_fast_path_classifies_clear_progress_query():
    intent = classify_dispatch_intent("现在完成到哪一步了？", session=active_session())

    assert intent.intent == "kernel_answerable_query"
    assert intent.kernel_answer_kind == "progress"
    assert intent.source == "rule"


def test_rule_fast_path_classifies_claim_and_todo_queries():
    claims = classify_dispatch_intent("这个任务目前有什么结论？", session=active_session())
    todos = classify_dispatch_intent("这个任务还有什么待办？", session=active_session())

    assert claims.intent == "kernel_answerable_query"
    assert claims.kernel_answer_kind == "claims"
    assert todos.intent == "kernel_answerable_query"
    assert todos.kernel_answer_kind == "todos"


def test_rule_fast_path_classifies_explicit_new_task_and_resume_markers():
    new_task = classify_dispatch_intent("我们换个任务", session=active_session())
    resume = classify_dispatch_intent("继续刚才那个任务", session=active_session())

    assert new_task.intent == "new_task"
    assert new_task.reason == "explicit_new_task_marker"
    assert resume.intent == "resume_previous_task"
    assert resume.reason == "resume_previous_task_marker"


def test_kernel_answerable_query_wins_over_other_task_marker():
    intent = classify_dispatch_intent("另一个任务当前进度？", session=active_session())

    assert intent.intent == "kernel_answerable_query"
    assert intent.kernel_answer_kind == "progress"
    assert intent.reason == "kernel_progress_query_marker"


def test_explicit_new_task_mode_still_overrides_kernel_answer_marker():
    intent = classify_dispatch_intent(
        "另一个任务当前进度？",
        mode="new_task",
        session=active_session(),
    )

    assert intent.intent == "new_task"
    assert intent.reason == "explicit_new_task_mode"


@pytest.mark.asyncio
async def test_rule_fast_path_does_not_call_llm():
    model = FakeModel(
        {
            "intent": "new_task",
            "confidence": 0.99,
            "reason": "should not be used",
        }
    )

    intent = await classify_dispatch_intent_with_llm(
        "现在完成到哪一步了？",
        session=active_session(),
        model_call=model,
    )

    assert intent.intent == "kernel_answerable_query"
    assert intent.source == "rule"
    assert model.calls == 0


@pytest.mark.asyncio
async def test_work_request_marker_is_new_task_without_llm():
    model = FakeModel(
        {
            "intent": "same_task_steer",
            "confidence": 0.99,
            "reason": "should not be used",
        }
    )

    intent = await classify_dispatch_intent_with_llm(
        "research beta",
        session=active_session(),
        model_call=model,
    )

    assert intent.intent == "new_task"
    assert intent.reason == "work_request_marker"
    assert intent.source == "rule"
    assert model.calls == 0


@pytest.mark.asyncio
async def test_chinese_work_request_marker_is_new_task_without_llm():
    model = FakeModel(
        {
            "intent": "same_task_steer",
            "confidence": 0.99,
            "reason": "should not be used",
        }
    )

    intent = await classify_dispatch_intent_with_llm(
        "请研究一下 A 公司",
        session=active_session(),
        model_call=model,
    )

    assert intent.intent == "new_task"
    assert intent.reason == "work_request_marker"
    assert intent.source == "rule"
    assert model.calls == 0


def test_status_like_sentence_with_work_verb_does_not_become_new_task():
    intent = classify_dispatch_intent("这个任务目前研究到哪了？", session=active_session())

    assert intent.intent == "uncertain"
    assert intent.reason == "no_rule_matched"


@pytest.mark.asyncio
async def test_kernel_query_without_context_does_not_call_llm():
    model = FakeModel(
        {
            "intent": "kernel_answerable_query",
            "confidence": 0.99,
            "kernel_answer_kind": "progress",
            "reason": "should not be used",
        }
    )

    intent = await classify_dispatch_intent_with_llm(
        "现在完成到哪一步了？",
        model_call=model,
    )

    assert intent.intent == "uncertain"
    assert intent.reason == "no_rule_matched"
    assert model.calls == 0


@pytest.mark.asyncio
async def test_llm_fallback_classifies_complex_kernel_answerable_query():
    model = FakeModel(
        {
            "intent": "kernel_answerable_query",
            "confidence": 0.86,
            "kernel_answer_kind": "evidence",
            "reason": "用户询问当前已有依据，不是在请求新任务",
        }
    )

    intent = await classify_dispatch_intent_with_llm(
        "先别动当前任务，我想判断现在掌握的材料是否足以对外说明。",
        session=active_session(),
        model_call=model,
    )

    assert intent.intent == "kernel_answerable_query"
    assert intent.kernel_answer_kind == "evidence"
    assert intent.source == "llm"
    assert model.calls == 1


@pytest.mark.asyncio
async def test_low_confidence_llm_result_falls_back_to_rule_result():
    model = FakeModel(
        {
            "intent": "new_task",
            "confidence": 0.5,
            "reason": "low confidence",
        }
    )

    intent = await classify_dispatch_intent_with_llm(
        "这个事情怎么说比较稳？",
        session=active_session(),
        model_call=model,
    )

    assert intent.intent == "uncertain"
    assert intent.source == "rule"
    assert model.calls == 1
