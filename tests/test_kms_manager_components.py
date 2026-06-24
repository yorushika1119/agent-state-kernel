from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kernel.engine import KernelEngine
from src.kms.manager import KmsManager
from src.kms.manager_components import build_kms_manager_components
from src.stores.sqlite_store import SqliteStore


@pytest.mark.asyncio
async def test_build_kms_manager_components_respects_llm_router_env(monkeypatch):
    monkeypatch.setenv("KMS_ENABLE_LLM_ROUTER", "1")
    store = SqliteStore(":memory:")
    await store.connect()
    try:
        components = build_kms_manager_components(store, KernelEngine(store))
        assert components.enable_llm_router is True
        assert components.task_router.enable_llm is True
        assert components.dispatch_preparation is not None
        assert components.dispatch_execution is not None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_build_kms_manager_components_explicit_flag_overrides_env(monkeypatch):
    monkeypatch.setenv("KMS_ENABLE_LLM_ROUTER", "1")
    store = SqliteStore(":memory:")
    await store.connect()
    try:
        components = build_kms_manager_components(
            store,
            KernelEngine(store),
            enable_llm_router=False,
        )
        assert components.enable_llm_router is False
        assert components.task_router.enable_llm is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_build_kms_manager_components_can_disable_llm_intent(monkeypatch):
    monkeypatch.setenv("KMS_ENABLE_LLM_INTENT", "1")
    store = SqliteStore(":memory:")
    await store.connect()
    try:
        components = build_kms_manager_components(
            store,
            KernelEngine(store),
            enable_llm_intent=False,
        )
        assert components.dispatch_preparation.enable_llm_intent is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_kms_manager_can_return_debug_timings():
    store = SqliteStore(":memory:")
    await store.connect()
    try:
        manager = KmsManager(
            store,
            KernelEngine(store),
            enable_llm_router=False,
            enable_llm_intent=False,
        )
        decision = await manager.dispatch_user_message(
            text="start a timing task",
            runtime_session_id="runtime-timing",
            agent_id="agent-timing",
            debug_timings=True,
        )

        steps = [item["step"] for item in decision.debug_timings]
        assert "prepare" in steps
        assert "execute" in steps
        assert "execute.get_or_create_session" in steps
        assert "execute.task_dispatch_plan" in steps
        assert "execute.create_thinker_dispatch" in steps
        assert "decision_build" in steps
        assert all(item["duration_s"] >= 0 for item in decision.debug_timings)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_kms_dispatch_submits_structured_user_message(monkeypatch):
    async def fail_raw_normalize(*_args, **_kwargs):
        raise AssertionError("raw normalize should not run for KMS dispatch")

    from src.kms.pipeline_stages import normalize as normalize_stage

    monkeypatch.setattr(normalize_stage, "_normalize_from_text", fail_raw_normalize)
    store = SqliteStore(":memory:")
    await store.connect()
    try:
        manager = KmsManager(
            store,
            KernelEngine(store),
            enable_llm_router=False,
            enable_llm_intent=False,
        )
        decision = await manager.dispatch_user_message(
            text="start a structured dispatch task",
            runtime_session_id="runtime-structured",
            agent_id="agent-structured",
        )

        assert decision.action == "start_new_task"
        assert decision.kernel_session_id
    finally:
        await store.close()
