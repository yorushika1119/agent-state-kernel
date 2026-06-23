"""Standalone KMS service entrypoint."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.kms.decisioning.belief import BeliefReviewJudge
from src.kms.decisioning.judges import (
    BaseJudge,
    ConflictJudge,
    DedupJudge,
    JudgeResult,
    ReliabilityJudge,
)
from src.kms.decisioning.model import ContentReliabilityJudge, SemanticConflictJudge

logger = logging.getLogger(__name__)


class EvidenceItemInput(BaseModel):
    evidence_id: str = ""
    evidence_type: str = "web_page"
    source: str = ""
    title: str = ""
    extracted_facts: List[str] = Field(default_factory=list)
    reliability: str = "unknown"


class EvidenceCandidateEvent(BaseModel):
    kernel_session_id: str
    event_type: str = "EvidenceAccepted"
    payload: Dict[str, Any] = Field(default_factory=dict)


class EvaluateRequest(BaseModel):
    event: EvidenceCandidateEvent
    existing_evidence: List[EvidenceItemInput] = Field(default_factory=list)
    existing_beliefs: List[Dict[str, Any]] = Field(default_factory=list)


class JudgeResponse(BaseModel):
    judge_name: str
    verdict: str
    reason: str = ""
    modifications: Dict[str, Any] = Field(default_factory=dict)
    side_effects_count: int = 0


class EvaluateResponse(BaseModel):
    results: List[JudgeResponse]
    has_rejections: bool
    modifications: Dict[str, Any] = Field(default_factory=dict)
    side_effect_events: List[Dict[str, Any]] = Field(default_factory=list)


class KMSService:
    def __init__(self) -> None:
        self.base_judges: List[BaseJudge] = [
            ReliabilityJudge(),
            DedupJudge(),
            ConflictJudge(),
            SemanticConflictJudge(),
            ContentReliabilityJudge(),
        ]

    async def evaluate(
        self,
        event: EvidenceCandidateEvent,
        existing_evidence: List[EvidenceItemInput],
        existing_beliefs: List[Dict[str, Any]],
    ) -> EvaluateResponse:
        from src.schema.events import Actor, CognitiveEvent, EventType
        from src.schema.state import BeliefItem, EvidenceItem

        candidate = CognitiveEvent(
            event_id="remote",
            kernel_session_id=event.kernel_session_id,
            event_type=EventType(event.event_type),
            actor=Actor.THINKER,
            source_component="thinker",
            payload=event.payload,
        )

        evidence_items = [
            EvidenceItem(
                evidence_id=item.evidence_id,
                evidence_type=item.evidence_type,
                source=item.source,
                title=item.title,
                extracted_facts=item.extracted_facts,
                reliability=item.reliability,
            )
            for item in existing_evidence
        ]
        belief_items = [BeliefItem(**item) for item in existing_beliefs]

        judges: List[BaseJudge] = list(self.base_judges)
        if candidate.event_type.name in {"BELIEF_PROPOSED", "BELIEF_UPDATED"}:
            judges.append(BeliefReviewJudge())

        results: List[JudgeResult] = []
        for judge in judges:
            try:
                result = await judge.evaluate(candidate, evidence_items, belief_items)
                results.append(result)
                if result.verdict == "modify":
                    candidate.payload.update(result.modifications)
            except Exception as exc:
                logger.warning("KMS judge '%s' failed: %s", judge.name, exc)
                results.append(
                    JudgeResult(
                        judge_name=judge.name,
                        verdict="accept",
                        reason=f"judge error: {exc}",
                    )
                )

        side_effects: List[Dict[str, Any]] = []
        modifications: Dict[str, Any] = {}
        for result in results:
            if result.verdict == "modify":
                modifications.update(result.modifications)
            for side_effect in result.side_effects:
                side_effects.append(
                    {
                        "event_type": side_effect.event_type.value,
                        "actor": side_effect.actor.value,
                        "source_component": side_effect.source_component,
                        "payload": side_effect.payload,
                        "visibility": side_effect.visibility.value,
                    }
                )

        return EvaluateResponse(
            results=[
                JudgeResponse(
                    judge_name=result.judge_name,
                    verdict=result.verdict,
                    reason=result.reason,
                    modifications=result.modifications,
                    side_effects_count=len(result.side_effects),
                )
                for result in results
            ],
            has_rejections=any(result.verdict == "reject" for result in results),
            modifications=modifications,
            side_effect_events=side_effects,
        )


_kms_service: Optional[KMSService] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _kms_service
    _kms_service = KMSService()
    logger.info("KMS service started with %d base judges", len(_kms_service.base_judges))
    yield
    _kms_service = None
    logger.info("KMS service stopped")


app = FastAPI(
    title="Agent State Kernel - KMS Service",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    return {
        "service": "KMS (Kernel Manager Service)",
        "version": "0.2.0",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/evaluate", response_model=EvaluateResponse)
async def evaluate(req: EvaluateRequest):
    if _kms_service is None:
        raise HTTPException(status_code=503, detail="KMS service not initialized")
    return await _kms_service.evaluate(
        req.event,
        req.existing_evidence,
        req.existing_beliefs,
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("KMS_PORT", "8421"))
    uvicorn.run("src.kms.server:app", host="127.0.0.1", port=port, reload=False)
