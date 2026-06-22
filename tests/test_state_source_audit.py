from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api import server as api_server
from src.kms.state_source_audit import StateSourceAudit


def test_state_source_audit_reports_primary_read_switch_complete():
    audit = StateSourceAudit()
    report = audit.as_dict()

    assert report["can_switch_all"] is True
    assert {item["new_model"] for item in report["mappings"]} == {
        "task_brief",
        "task_flow",
        "claim",
        "todo",
    }
    assert all(
        item["can_switch_primary"] is True
        for item in report["mappings"]
    )
    assert report["blocking_reasons"] == []


def test_state_source_audit_records_legacy_compat_next_steps():
    report = StateSourceAudit().as_dict()
    by_model = {
        item["new_model"]: item
        for item in report["mappings"]
    }

    assert "intent_states" in by_model["task_brief"]["legacy_source"]
    assert "plan_states" in by_model["task_flow"]["legacy_source"]
    assert "belief_items" in by_model["claim"]["legacy_source"]
    assert "commitments" in by_model["todo"]["legacy_source"]
    assert "compatibility output" in by_model["task_brief"]["safe_next_step"]
    assert "reducer write ownership" in by_model["claim"]["safe_next_step"]


@pytest.mark.asyncio
async def test_state_source_audit_api_exposes_current_switch_decision():
    transport = httpx.ASGITransport(app=api_server.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://kernel.test",
    ) as client:
        response = await client.get("/kms/state-source-audit")

    assert response.status_code == 200
    data = response.json()
    assert data["can_switch_all"] is True
    assert {item["shadow_table"] for item in data["mappings"]} == {
        "task_brief_states",
        "task_flows",
        "claim_items",
        "todo_obligations",
    }
