from __future__ import annotations

import sys
import re
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api import server as api_server
from src.kernel.engine import KernelEngine
from src.kms.state_source_audit import StateSourceAudit
from src.stores.sqlite_store import SqliteStore


def test_state_source_audit_reports_primary_read_switch_complete():
    audit = StateSourceAudit()
    report = audit.as_dict()

    assert report["can_switch_all"] is True
    assert report["legacy_direct_sql_frozen"] is True
    assert report["legacy_tables_removable"] is False
    assert report["legacy_fallback_observed"] is False
    assert report["legacy_fallback_hit_count"] == 0
    assert report["legacy_fallback_hits"] == []
    assert "src/kms/pipeline.py" not in report["remaining_compat_getter_files"]
    assert "src/kernel/engine.py" in report["remaining_compat_getter_files"]
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
    assert "legacy storage fallback" in by_model["task_brief"]["safe_next_step"]
    assert "migrate callers to claim_items" in by_model["claim"]["safe_next_step"]


def test_business_code_does_not_directly_query_legacy_state_tables():
    root = Path(__file__).resolve().parents[1]
    allowed = {
        Path("src/stores/sqlite_store.py"),
    }
    legacy_sql = re.compile(
        r"\b(FROM|JOIN|INTO|UPDATE|DELETE\s+FROM|CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS)\s+"
        r"(intent_states|plan_states|belief_items|commitments)\b",
        re.IGNORECASE,
    )

    offenders: list[str] = []
    for path in (root / "src").rglob("*.py"):
        relative = path.relative_to(root)
        if relative in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        for match in legacy_sql.finditer(text):
            offenders.append(f"{relative}: {match.group(0)}")

    assert offenders == []


def test_business_code_does_not_call_legacy_state_getters():
    root = Path(__file__).resolve().parents[1]
    allowed = {
        Path("src/kernel/engine.py"),
        Path("src/stores/sqlite_store.py"),
    }
    legacy_getter = re.compile(
        r"\.(get_intent|get_plan|get_beliefs|get_commitments)\("
    )

    offenders: list[str] = []
    for path in (root / "src").rglob("*.py"):
        relative = path.relative_to(root)
        if relative in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        for match in legacy_getter.finditer(text):
            offenders.append(f"{relative}: {match.group(0)}")

    assert offenders == []


@pytest.mark.asyncio
async def test_state_source_audit_api_exposes_current_switch_decision(monkeypatch):
    store = SqliteStore(":memory:")
    await store.connect()
    monkeypatch.setattr(api_server, "_store", store)
    monkeypatch.setattr(api_server, "_engine", KernelEngine(store))
    try:
        transport = httpx.ASGITransport(app=api_server.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://kernel.test",
        ) as client:
            response = await client.get("/kms/state-source-audit")
    finally:
        await store.close()

    assert response.status_code == 200
    data = response.json()
    assert data["can_switch_all"] is True
    assert data["legacy_direct_sql_frozen"] is True
    assert data["legacy_tables_removable"] is False
    assert isinstance(data["legacy_fallback_hits"], list)
    assert isinstance(data["legacy_fallback_hit_count"], int)
    assert "src/stores/sqlite_store.py" in data["remaining_compat_getter_files"]
    assert {item["shadow_table"] for item in data["mappings"]} == {
        "task_brief_states",
        "task_flows",
        "claim_items",
        "todo_obligations",
    }
