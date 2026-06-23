from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from report_legacy_fallback_audit import report_legacy_fallback_audit  # noqa: E402
from src.stores.sqlite_store import SqliteStore  # noqa: E402


@pytest.mark.asyncio
async def test_report_legacy_fallback_audit_outputs_empty_state(tmp_path):
    db_path = tmp_path / "kernel.db"

    report = await report_legacy_fallback_audit(str(db_path))

    assert "ROWS=0" in report
    assert "HIT_COUNT=0" in report
    assert "NO_LEGACY_FALLBACK_HITS" in report


@pytest.mark.asyncio
async def test_report_legacy_fallback_audit_outputs_hits(tmp_path):
    db_path = tmp_path / "kernel.db"
    store = SqliteStore(str(db_path))
    await store.connect()
    try:
        await store.conn.execute(
            """INSERT INTO legacy_state_fallback_audits
               (kernel_session_id, model, legacy_table, hit_count, first_hit_at, last_hit_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "ask_test",
                "task_brief",
                "intent_states",
                2,
                "2026-06-23T00:00:00+00:00",
                "2026-06-23T00:01:00+00:00",
            ),
        )
        await store.conn.commit()
    finally:
        await store.close()

    report = await report_legacy_fallback_audit(str(db_path))

    assert "ROWS=1" in report
    assert "HIT_COUNT=2" in report
    assert "| ask_test | task_brief | intent_states | 2 |" in report
