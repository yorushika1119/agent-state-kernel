from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kernel.engine import KernelEngine
from src.kms.kernel_session_coordinator import KernelSessionCoordinator
from src.stores.sqlite_store import SqliteStore


@pytest.mark.asyncio
async def test_kernel_session_coordinator_finds_existing_session():
    store = SqliteStore(":memory:")
    await store.connect()
    try:
        engine = KernelEngine(store)
        coordinator = KernelSessionCoordinator(store, engine)
        session = await engine.create_session(
            agent_id="agent-session",
            runtime_session_id="rt-session",
        )

        by_target = await coordinator.find_target_session(
            target_session_id=session.kernel_session_id,
        )
        by_runtime = await coordinator.find_target_session(
            runtime_session_id="rt-session",
        )
        reused, created = await coordinator.get_or_create_session(
            by_runtime,
            agent_id="agent-session",
            runtime_session_id="rt-session",
        )

        assert by_target.kernel_session_id == session.kernel_session_id
        assert by_runtime.kernel_session_id == session.kernel_session_id
        assert reused.kernel_session_id == session.kernel_session_id
        assert created is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_kernel_session_coordinator_creates_missing_session():
    store = SqliteStore(":memory:")
    await store.connect()
    try:
        engine = KernelEngine(store)
        coordinator = KernelSessionCoordinator(store, engine)

        session, created = await coordinator.get_or_create_session(
            None,
            agent_id="agent-session-new",
            runtime_id="runtime-new",
            runtime_session_id="rt-session-new",
            runtime_type="gateway",
            external_source="github",
            external_workspace_id="workspace-1",
            external_issue_id="issue-1",
            external_task_id="task-1",
        )

        stored = await store.get_session(session.kernel_session_id)

        assert created is True
        assert stored.agent_id == "agent-session-new"
        assert stored.runtime_id == "runtime-new"
        assert stored.runtime_session_id == "rt-session-new"
        assert stored.runtime_type == "gateway"
        assert stored.external_source == "github"
        assert stored.external_workspace_id == "workspace-1"
        assert stored.external_issue_id == "issue-1"
        assert stored.external_task_id == "task-1"
    finally:
        await store.close()
