"""Kernel session lookup and creation for KMS dispatch."""

from __future__ import annotations


class KernelSessionCoordinator:
    """Resolves the Kernel session that should receive a user message."""

    def __init__(self, store, engine):
        self.store = store
        self.engine = engine

    async def find_target_session(
        self,
        *,
        target_session_id: str = "",
        runtime_session_id: str = "",
    ):
        if target_session_id:
            return await self.store.get_session(target_session_id)
        if runtime_session_id:
            sessions = await self.store.list_sessions_by_runtime_session(
                runtime_session_id,
                limit=1,
            )
            return sessions[0] if sessions else None
        return None

    async def get_or_create_session(
        self,
        session,
        *,
        agent_id: str = "",
        runtime_id: str = "",
        runtime_session_id: str = "",
        runtime_type: str = "cli-agent",
        external_source: str = "",
        external_workspace_id: str = "",
        external_issue_id: str = "",
        external_task_id: str = "",
    ):
        if session is not None:
            return session, False
        created = await self.engine.create_session(
            agent_id=agent_id,
            runtime_id=runtime_id,
            runtime_session_id=runtime_session_id,
            runtime_type=runtime_type,
            external_source=external_source,
            external_workspace_id=external_workspace_id,
            external_issue_id=external_issue_id,
            external_task_id=external_task_id,
        )
        return created, True
