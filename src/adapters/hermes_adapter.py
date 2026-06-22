"""Hermes Runtime Adapter — translates Hermes callbacks into Kernel events.

This adapter sits between Hermes (the Thinker) and the Kernel API.
It converts Hermes' tool progress callbacks, step events, and
user messages into structured CognitiveEvents for the Kernel.

Usage from Hermes:
    The adapter runs as a thin HTTP client that Hermes calls after
    each tool execution and step completion. In a deeper integration,
    it could be wired into Hermes' callback system directly.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event builders — convert Hermes callbacks → Kernel event payloads
# ---------------------------------------------------------------------------


def build_intent_event(goal: str, constraints: List[str] = None) -> Dict[str, Any]:
    """Build an IntentUpdated event from a user message / goal."""
    return {
        "session_id": "",  # filled by caller
        "component": "thinker",
        "request_type": "IntentUpdated",
        "payload": {
            "goal": goal,
            "constraints": constraints or [],
        },
    }


def build_plan_event(
    plan_id: str,
    steps: List[Dict[str, str]],
    intent_version: int = 0,
) -> Dict[str, Any]:
    """Build a PlanProposed event.

    steps: [{"step_id": "s1", "name": "Search for X"}]
    """
    return {
        "session_id": "",
        "component": "thinker",
        "request_type": "PlanProposed",
        "payload": {
            "plan_id": plan_id,
            "plan": {"steps": steps},
        },
        "intent_version": intent_version,
    }


def build_tool_started_event(
    step_id: str,
    tool: str,
    input_summary: str,
    runtime_refs: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Build a ToolStarted event."""
    action_id = f"act_{uuid.uuid4().hex[:8]}"
    return {
        "session_id": "",
        "component": "thinker",
        "request_type": "ToolStarted",
        "payload": {
            "action_id": action_id,
            "step_id": step_id,
            "tool": tool,
            "input_summary": input_summary,
            "runtime_refs": runtime_refs or {},
        },
    }, action_id


def build_tool_completed_event(
    action_id: str,
    step_id: str,
    output_summary: str,
    output_ref: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a ToolCompleted event."""
    return {
        "session_id": "",
        "component": "thinker",
        "request_type": "ToolCompleted",
        "payload": {
            "action_id": action_id,
            "step_id": step_id,
            "output_summary": output_summary,
            "output_ref": output_ref,
        },
    }


def build_tool_failed_event(
    action_id: str,
    step_id: str,
    error: str,
) -> Dict[str, Any]:
    """Build a ToolFailed event."""
    return {
        "session_id": "",
        "component": "thinker",
        "request_type": "ToolFailed",
        "payload": {
            "action_id": action_id,
            "step_id": step_id,
            "error": error,
        },
    }


def build_evidence_event(
    evidence_type: str,
    source: str,
    title: str,
    extracted_facts: List[str],
    reliability: str = "medium",
    raw_ref: Optional[str] = None,
) -> Dict[str, Any]:
    """Build an EvidenceCandidateFound event."""
    return {
        "session_id": "",
        "component": "thinker",
        "request_type": "EvidenceCandidateFound",
        "payload": {
            "evidence_id": f"ev_{uuid.uuid4().hex[:8]}",
            "evidence_type": evidence_type,  # web_page, file, tool_result, user_statement, database_row
            "source": source,
            "title": title,
            "extracted_facts": extracted_facts,
            "reliability": reliability,
            "raw_ref": raw_ref,
        },
    }


def build_belief_event(
    claim: str,
    status: str,  # unverified, likely, verified, conflicting, retracted
    confidence: float,
    supporting_evidence_ids: List[str],
    conflicting_evidence_ids: List[str] = None,
) -> Dict[str, Any]:
    """Build a BeliefProposed event."""
    return {
        "session_id": "",
        "component": "thinker",
        "request_type": "BeliefProposed",
        "payload": {
            "belief_id": f"b_{uuid.uuid4().hex[:8]}",
            "claim": claim,
            "status": status,
            "confidence": confidence,
            "supporting_evidence": supporting_evidence_ids,
            "conflicting_evidence": conflicting_evidence_ids or [],
        },
    }


def build_step_completed_event(
    step_id: str,
) -> Dict[str, Any]:
    """Build a TaskCompleted event for a step."""
    return {
        "session_id": "",
        "component": "thinker",
        "request_type": "TaskCompleted",
        "payload": {
            "step_id": step_id,
        },
    }


# ---------------------------------------------------------------------------
# Adapter client
# ---------------------------------------------------------------------------


class HermesAdapter:
    """Sends Kernel events from Hermes callbacks.

    Use in Hermes as:
        adapter = HermesAdapter("http://127.0.0.1:8420")
        await adapter.start_session()
        await adapter.submit(intent_event)
        ...
    """

    def __init__(self, kernel_url: str = "http://127.0.0.1:8420"):
        self.kernel_url = kernel_url.rstrip("/")
        self.session_id: Optional[str] = None
        self.run_id: str = ""
        self.intent_version: int = 0
        self.thinker_dispatch_id: str = ""
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Adapter not entered. Use 'async with HermesAdapter() as adapter:'")
        return self._client

    async def start_session(
        self,
        agent_id: str = "",
        runtime_session_id: str = "",
        external_task_id: str = "",
    ) -> str:
        """Create a new Kernel session. Returns session_id."""
        resp = await self.client.post(
            f"{self.kernel_url}/kernel/sessions",
            json={
                "agent_id": agent_id,
                "runtime_session_id": runtime_session_id,
                "external_task_id": external_task_id,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self.session_id = data["kernel_session_id"]
        return self.session_id

    async def submit(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Submit an event to the Kernel."""
        if not self.session_id:
            raise RuntimeError("No active session. Call start_session() first.")
        event["session_id"] = self.session_id
        if self.run_id and not event.get("run_id"):
            event["run_id"] = self.run_id
        if self.intent_version and not event.get("intent_version"):
            event["intent_version"] = self.intent_version
        resp = await self.client.post(
            f"{self.kernel_url}/kms/request",
            json=event,
        )
        resp.raise_for_status()
        return resp.json()

    async def dispatch_user_message(
        self,
        text: str,
        *,
        runtime_session_id: str = "",
        mode: str = "auto",
        agent_id: str = "",
        external_task_id: str = "",
        runtime_refs: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """让 Kernel 决定新消息如何调度。"""
        resp = await self.client.post(
            f"{self.kernel_url}/kms/dispatch-user-message",
            json={
                "text": text,
                "runtime_session_id": runtime_session_id,
                "target_session_id": self.session_id or "",
                "mode": mode,
                "agent_id": agent_id,
                "external_task_id": external_task_id,
                "runtime_refs": runtime_refs or {},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self.session_id = data["kernel_session_id"]
        self.run_id = data["run_id"]
        self.intent_version = data["intent_version"]
        self.thinker_dispatch_id = data.get("thinker_dispatch_id", "")
        return data

    async def claim_thinker_dispatch(
        self,
        *,
        dispatch_id: str = "",
        thinker_id: str = "",
        kernel_session_id: str = "",
        task_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Claim the next pending KMS thinker dispatch."""
        resp = await self.client.post(
            f"{self.kernel_url}/kms/thinker/dispatches/claim",
            json={
                "dispatch_id": dispatch_id or self.thinker_dispatch_id or "",
                "thinker_id": thinker_id,
                "kernel_session_id": kernel_session_id or self.session_id or "",
                "task_id": task_id,
            },
        )
        resp.raise_for_status()
        dispatch = resp.json().get("dispatch")
        if dispatch:
            self.session_id = dispatch.get("kernel_session_id") or self.session_id
            self.run_id = dispatch.get("run_id") or self.run_id
            self.intent_version = dispatch.get("task_brief_version") or self.intent_version
            self.thinker_dispatch_id = dispatch.get("dispatch_id") or ""
        return dispatch

    async def heartbeat_thinker_dispatch(self, dispatch_id: str = "") -> Dict[str, Any]:
        """Send a heartbeat for a claimed thinker dispatch."""
        resolved_id = dispatch_id or self.thinker_dispatch_id
        if not resolved_id:
            raise RuntimeError("No thinker dispatch id")
        resp = await self.client.post(
            f"{self.kernel_url}/kms/thinker/dispatches/{resolved_id}/heartbeat"
        )
        resp.raise_for_status()
        return resp.json()

    async def complete_thinker_dispatch(
        self,
        dispatch_id: str = "",
        *,
        session_status: str = "completed",
        response_summary: str = "",
        runtime_refs: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Mark a thinker dispatch completed and complete its active run."""
        resolved_id = dispatch_id or self.thinker_dispatch_id
        if not resolved_id:
            raise RuntimeError("No thinker dispatch id")
        resp = await self.client.post(
            f"{self.kernel_url}/kms/thinker/dispatches/{resolved_id}/complete",
            json={
                "session_status": session_status,
                "response_summary": response_summary,
                "runtime_refs": runtime_refs or {},
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def record_conversation_ref(
        self,
        *,
        user_session_id: str = "",
        task_id: str = "",
        run_id: str = "",
        role: str = "assistant",
        source: str = "hermes_reply",
        message_ref_id: str = "",
        text_summary: str = "",
        runtime_refs: Optional[Dict[str, str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Record a Hermes-owned reply/message ref without storing full transcript."""
        resp = await self.client.post(
            f"{self.kernel_url}/kms/conversation-refs",
            json={
                "user_session_id": user_session_id,
                "kernel_session_id": self.session_id or "",
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
        resp.raise_for_status()
        return resp.json()

    async def fail_thinker_dispatch(
        self,
        dispatch_id: str = "",
        *,
        error: str = "",
        session_status: str = "failed",
    ) -> Dict[str, Any]:
        """Mark a thinker dispatch failed and fail its active run."""
        resolved_id = dispatch_id or self.thinker_dispatch_id
        if not resolved_id:
            raise RuntimeError("No thinker dispatch id")
        resp = await self.client.post(
            f"{self.kernel_url}/kms/thinker/dispatches/{resolved_id}/fail",
            json={"error": error, "session_status": session_status},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_thinker_view(self) -> Dict[str, Any]:
        """Get the Thinker's view of current state."""
        resp = await self.client.get(
            f"{self.kernel_url}/kms/sessions/{self.session_id}/views/thinker"
        )
        resp.raise_for_status()
        data = resp.json()
        cancellation = data.get("cancellation", {})
        self.run_id = cancellation.get("active_run_id", self.run_id)
        self.intent_version = cancellation.get("intent_version", self.intent_version)
        return data

    async def get_events(self) -> List[Dict[str, Any]]:
        """Get the event log for this session."""
        resp = await self.client.get(
            f"{self.kernel_url}/kernel/sessions/{self.session_id}/events"
        )
        resp.raise_for_status()
        return resp.json()
