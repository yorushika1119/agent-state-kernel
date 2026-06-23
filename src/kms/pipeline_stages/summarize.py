"""Summarize stage for KMS progress views."""

from __future__ import annotations

import logging

from src.kernel.state_reducer import synthesize_progress
from src.kms.state.aliases import (
    beliefs_from_claims,
    intent_from_task_brief,
    plan_from_task_flow,
)
from src.schema.state import ProgressState

logger = logging.getLogger(__name__)


async def refresh_progress(store, session_id: str) -> ProgressState:
    """同步刷新 progress_states，供 Gate/Sync/Thinker 直接读取。"""
    plan = plan_from_task_flow(await store.get_task_flow(session_id))
    beliefs = beliefs_from_claims(await store.get_claim_items(session_id))
    intent = intent_from_task_brief(await store.get_task_brief(session_id))
    constraints = intent.constraints if intent else []
    progress = synthesize_progress(session_id, plan, beliefs, constraints)
    await store.save_progress(session_id, progress)
    return progress


async def summarize(store, session_id: str, *, api_key: str = "") -> ProgressState:
    """合成面向用户的进度视图（§5.12）。"""
    progress = await refresh_progress(store, session_id)
    safe_fact_count = len(progress.safe_facts)

    if safe_fact_count and api_key:
        try:
            from src.kms.decisioning.model import ModelCall

            model = ModelCall()
            safe_text = "\n".join(f"- {fact}" for fact in progress.safe_facts[:5])
            prompt = (
                f"Status: {progress.status}\n"
                f"Stage: {progress.stage or 'ongoing'}\n"
                f"Safe facts:\n{safe_text}\n\n"
                f"Unresolved items count: {len(progress.unsafe_claims)}\n\n"
                "Write ONE sentence in Chinese summarizing the current progress. "
                "Only mention safe facts and high-level progress. "
                "Do not quote or infer unresolved claims. "
                "Only write the sentence, nothing else."
            )
            raw = await model.ask(system="", user=prompt, max_tokens=150)
            if raw and isinstance(raw, str) and len(raw.strip()) > 5:
                progress.summary = raw.strip()[:300]
        except Exception as exc:
            logger.debug("Summarize DeepSeek call failed: %s", exc)

    await store.save_progress(session_id, progress)
    return progress
