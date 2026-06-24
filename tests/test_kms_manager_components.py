from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kernel.engine import KernelEngine
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
