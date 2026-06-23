"""Remote KMS client — HTTP-based alternative to inline KMSPipeline.

When KMS_MODE=remote (or KMS_URL is set), the Kernel engine uses
this client instead of the inline KMSPipeline. It POSTs events to
the standalone KMS service and receives judge verdicts.

Same interface as KMSPipeline — the engine doesn't know which one
it's talking to.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from src.schema.events import Actor, CognitiveEvent, EventType, Visibility
from src.schema.state import BeliefItem, EvidenceItem

logger = logging.getLogger(__name__)


class RemoteKMSClient:
    """HTTP client that mirrors the KMSPipeline interface.

    Usage:
        client = RemoteKMSClient("http://127.0.0.1:8421")
        results = await client.evaluate(event, evidence, beliefs)
        mods = client.get_modifications(results)
        side = client.get_side_effects(results)
    """

    def __init__(self, kms_url: str = "http://127.0.0.1:8421", timeout: float = 30.0):
        self.kms_url = kms_url.rstrip("/")
        self.timeout = timeout
        self._last_results: List[Dict[str, Any]] = []
        self._last_response: Dict[str, Any] = {}

    async def evaluate(
        self,
        event: CognitiveEvent,
        existing_evidence: List[EvidenceItem],
        existing_beliefs: List[BeliefItem],
    ) -> List[Dict[str, Any]]:
        """Call remote KMS /evaluate endpoint. Returns dict-based results
        (compatible with how engine.py consumes judge results)."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.kms_url}/evaluate",
                    json={
                        "event": {
                            "kernel_session_id": event.kernel_session_id,
                            "event_type": event.event_type.value,
                            "payload": event.payload,
                        },
                        "existing_evidence": [
                            {
                                "evidence_id": e.evidence_id,
                                "evidence_type": e.evidence_type.value,
                                "source": e.source,
                                "title": e.title,
                                "extracted_facts": e.extracted_facts,
                                "reliability": e.reliability.value,
                            }
                            for e in existing_evidence
                        ],
                        "existing_beliefs": [
                            b.model_dump() for b in existing_beliefs
                        ],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                self._last_response = data
                self._last_results = data.get("results", [])
                return self._last_results
        except Exception as e:
            logger.warning("Remote KMS call failed: %s", e)
            self._last_response = {}
            self._last_results = []
            return []

    def has_rejections(self, results: List[Dict[str, Any]]) -> bool:
        if self._last_response:
            return bool(self._last_response.get("has_rejections"))
        return any(r.get("verdict") == "reject" for r in results)

    def get_side_effects(self, results: List[Dict[str, Any]]) -> List[CognitiveEvent]:
        """Convert remote side_effect_events back to CognitiveEvent objects."""
        side_effects = []
        for raw in self._last_response.get("side_effect_events", []):
            try:
                side_effects.append(
                    CognitiveEvent(
                        event_id="",
                        kernel_session_id="",
                        event_type=EventType(raw["event_type"]),
                        actor=Actor(raw["actor"]),
                        source_component=raw.get("source_component", "kms"),
                        payload=raw.get("payload", {}),
                        visibility=Visibility(raw.get("visibility", "shared")),
                    )
                )
            except Exception as e:
                logger.warning("Remote KMS side effect parse failed: %s", e)
        return side_effects

    def get_modifications(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        mods = {}
        for r in results:
            if r.get("verdict") == "modify":
                mods.update(r.get("modifications", {}))
        return mods
