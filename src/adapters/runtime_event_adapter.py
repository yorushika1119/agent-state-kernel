"""Generic Runtime Event Adapter for host agent runtimes."""

from __future__ import annotations

import uuid
from typing import Any, Optional

import httpx


class RuntimeEventAdapter:
    """Thin HTTP adapter for runtime -> KMS/Kernel integration."""

    def __init__(
        self,
        kernel_url: str = "http://127.0.0.1:8420",
        *,
        runtime_type: str = "generic-runtime",
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.kernel_url = kernel_url.rstrip("/")
        self.runtime_type = runtime_type
        self.session_id = ""
        self.run_id = ""
        self.intent_version = 0
        self.thinker_dispatch_id = ""
        self._client = client
        self._owns_client = False

    async def __aenter__(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
            self._owns_client = True
        return self

    async def __aexit__(self, *args):
        if self._client and self._owns_client:
            await self._client.aclose()
        self._client = None
        self._owns_client = False

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Adapter not entered")
        return self._client

    async def dispatch_user_message(
        self,
        text: str,
        *,
        runtime_session_id: str = "",
        runtime_id: str = "",
        agent_id: str = "",
        user_session_id: str = "",
        target_session_id: str = "",
        mode: str = "auto",
        runtime_refs: Optional[dict] = None,
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.kernel_url}/kms/dispatch-user-message",
            json={
                "text": text,
                "runtime_session_id": runtime_session_id,
                "runtime_id": runtime_id,
                "runtime_type": self.runtime_type,
                "agent_id": agent_id,
                "user_session_id": user_session_id,
                "target_session_id": target_session_id or self.session_id,
                "mode": mode,
                "runtime_refs": runtime_refs or {},
            },
        )
        response.raise_for_status()
        data = response.json()
        self.session_id = data.get("kernel_session_id") or self.session_id
        self.run_id = data.get("run_id") or self.run_id
        self.intent_version = data.get("intent_version") or self.intent_version
        self.thinker_dispatch_id = data.get("thinker_dispatch_id") or self.thinker_dispatch_id
        return data

    async def submit_event(
        self,
        request_type: str,
        *,
        payload: Optional[dict] = None,
        component: str = "thinker",
        session_id: str = "",
        run_id: str = "",
        intent_version: int = 0,
        runtime_refs: Optional[dict] = None,
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.kernel_url}/kms/request",
            json={
                "session_id": session_id or self.session_id,
                "component": component,
                "request_type": request_type,
                "payload": payload or {},
                "run_id": run_id or self.run_id,
                "intent_version": intent_version or self.intent_version,
                "runtime_refs": runtime_refs or {},
            },
        )
        response.raise_for_status()
        return response.json()

    async def submit_tool_started(
        self,
        *,
        step_id: str = "",
        tool: str,
        input_summary: str = "",
        action_id: str = "",
        runtime_refs: Optional[dict] = None,
    ) -> dict[str, Any]:
        refs = runtime_refs or {}
        resolved_action_id = action_id or refs.get("tool_call_id") or f"act_{uuid.uuid4().hex[:8]}"
        return await self.submit_event(
            "ToolStarted",
            payload={
                "action_id": resolved_action_id,
                "step_id": step_id,
                "tool": tool,
                "input_summary": input_summary,
                "runtime_refs": refs,
            },
            runtime_refs=refs,
        )

    async def submit_tool_completed(
        self,
        *,
        action_id: str,
        step_id: str = "",
        output_summary: str = "",
        output_ref: str = "",
        runtime_refs: Optional[dict] = None,
    ) -> dict[str, Any]:
        refs = runtime_refs or {}
        return await self.submit_event(
            "ToolCompleted",
            payload={
                "action_id": action_id,
                "step_id": step_id,
                "output_summary": output_summary,
                "output_ref": output_ref,
                "runtime_refs": refs,
            },
            runtime_refs=refs,
        )

    async def submit_tool_failed(
        self,
        *,
        action_id: str,
        step_id: str = "",
        tool: str = "",
        error: str,
        runtime_refs: Optional[dict] = None,
    ) -> dict[str, Any]:
        refs = runtime_refs or {}
        return await self.submit_event(
            "ToolFailed",
            payload={
                "action_id": action_id,
                "step_id": step_id,
                "tool": tool,
                "error": error,
                "runtime_refs": refs,
            },
            runtime_refs=refs,
        )

    async def submit_action_blocked(
        self,
        *,
        action_id: str = "",
        step_id: str = "",
        tool: str = "",
        reason: str,
        runtime_refs: Optional[dict] = None,
    ) -> dict[str, Any]:
        refs = runtime_refs or {}
        resolved_action_id = action_id or refs.get("tool_call_id") or f"act_{uuid.uuid4().hex[:8]}"
        return await self.submit_event(
            "ActionBlocked",
            payload={
                "action_id": resolved_action_id,
                "step_id": step_id,
                "tool": tool,
                "reason": reason,
                "runtime_refs": refs,
            },
            runtime_refs=refs,
        )

    async def submit_reasoning_summary(
        self,
        summary: str,
        *,
        runtime_refs: Optional[dict] = None,
    ) -> dict[str, Any]:
        refs = runtime_refs or {}
        return await self.submit_event(
            "ReasoningSummary",
            payload={
                "summary": summary,
                "runtime_refs": refs,
            },
            runtime_refs=refs,
        )

    async def submit_raw_result(
        self,
        result_ref: str,
        *,
        result_summary: str = "",
        runtime_refs: Optional[dict] = None,
    ) -> dict[str, Any]:
        refs = runtime_refs or {}
        return await self.submit_event(
            "RawResultAvailable",
            payload={
                "result_ref": result_ref,
                "result_summary": result_summary,
                "runtime_refs": refs,
            },
            runtime_refs=refs,
        )

    async def claim_thinker_dispatch(
        self,
        *,
        dispatch_id: str = "",
        thinker_id: str = "",
        kernel_session_id: str = "",
        task_id: str = "",
    ) -> Optional[dict[str, Any]]:
        response = await self.client.post(
            f"{self.kernel_url}/kms/thinker/dispatches/claim",
            json={
                "dispatch_id": dispatch_id or self.thinker_dispatch_id,
                "thinker_id": thinker_id,
                "kernel_session_id": kernel_session_id or self.session_id,
                "task_id": task_id,
            },
        )
        response.raise_for_status()
        dispatch = response.json().get("dispatch")
        if dispatch:
            self.session_id = dispatch.get("kernel_session_id") or self.session_id
            self.run_id = dispatch.get("run_id") or self.run_id
            self.intent_version = dispatch.get("task_brief_version") or self.intent_version
            self.thinker_dispatch_id = dispatch.get("dispatch_id") or self.thinker_dispatch_id
        return dispatch

    async def heartbeat_thinker_dispatch(self, dispatch_id: str = "") -> dict[str, Any]:
        resolved_id = dispatch_id or self.thinker_dispatch_id
        if not resolved_id:
            raise RuntimeError("No thinker dispatch id")
        response = await self.client.post(
            f"{self.kernel_url}/kms/thinker/dispatches/{resolved_id}/heartbeat"
        )
        response.raise_for_status()
        return response.json()

    async def complete_thinker_dispatch(
        self,
        dispatch_id: str = "",
        *,
        session_status: str = "completed",
        response_summary: str = "",
        runtime_refs: Optional[dict] = None,
    ) -> dict[str, Any]:
        resolved_id = dispatch_id or self.thinker_dispatch_id
        if not resolved_id:
            raise RuntimeError("No thinker dispatch id")
        response = await self.client.post(
            f"{self.kernel_url}/kms/thinker/dispatches/{resolved_id}/complete",
            json={
                "session_status": session_status,
                "response_summary": response_summary,
                "runtime_refs": runtime_refs or {},
            },
        )
        response.raise_for_status()
        return response.json()

    async def fail_thinker_dispatch(
        self,
        dispatch_id: str = "",
        *,
        error: str = "",
        session_status: str = "failed",
    ) -> dict[str, Any]:
        resolved_id = dispatch_id or self.thinker_dispatch_id
        if not resolved_id:
            raise RuntimeError("No thinker dispatch id")
        response = await self.client.post(
            f"{self.kernel_url}/kms/thinker/dispatches/{resolved_id}/fail",
            json={"error": error, "session_status": session_status},
        )
        response.raise_for_status()
        return response.json()

    async def record_conversation_ref(
        self,
        *,
        user_session_id: str = "",
        kernel_session_id: str = "",
        task_id: str = "",
        run_id: str = "",
        role: str = "assistant",
        source: str = "external_reply",
        message_ref_id: str = "",
        text_summary: str = "",
        runtime_refs: Optional[dict] = None,
        metadata: Optional[dict] = None,
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.kernel_url}/kms/conversation-refs",
            json={
                "user_session_id": user_session_id,
                "kernel_session_id": kernel_session_id or self.session_id,
                "task_id": task_id,
                "run_id": run_id or self.run_id,
                "role": role,
                "source": source,
                "message_ref_id": message_ref_id,
                "text_summary": text_summary,
                "runtime_refs": runtime_refs or {},
                "metadata": metadata or {},
            },
        )
        response.raise_for_status()
        return response.json()

    async def get_view(self, view_name: str = "thinker", *, session_id: str = "") -> dict[str, Any]:
        response = await self.client.get(
            f"{self.kernel_url}/kms/sessions/{session_id or self.session_id}/views/{view_name}"
        )
        response.raise_for_status()
        return response.json()
