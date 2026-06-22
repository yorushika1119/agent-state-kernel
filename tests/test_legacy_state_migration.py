from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate_legacy_state_tables import migrate_legacy_state_tables
from src.kernel.engine import KernelEngine
from src.stores.sqlite_store import SqliteStore


async def _count_rows(store: SqliteStore, table: str, session_id: str) -> int:
    rows = await store.conn.execute_fetchall(
        f"SELECT COUNT(*) AS count FROM {table} WHERE kernel_session_id = ?",
        (session_id,),
    )
    return rows[0]["count"]


@pytest.mark.asyncio
async def test_migrate_legacy_state_tables_dry_run_then_write(tmp_path):
    db_path = tmp_path / "kernel.db"
    store = SqliteStore(str(db_path))
    await store.connect()
    engine = KernelEngine(store)
    session = await engine.create_session(agent_id="agent-migrate")
    sid = session.kernel_session_id
    try:
        for table in (
            "task_brief_states",
            "task_flows",
            "claim_items",
            "todo_obligations",
            "intent_states",
            "plan_states",
            "belief_items",
            "commitments",
        ):
            await store.conn.execute(
                f"DELETE FROM {table} WHERE kernel_session_id = ?",
                (sid,),
            )
        await store.conn.execute(
            """INSERT INTO intent_states
               (kernel_session_id, intent_version, goal, constraints,
                output_format, priority, cancelled, last_user_update_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, 3, "legacy goal", json.dumps(["c1"]), "table", "high", 0, None, None),
        )
        await store.conn.execute(
            """INSERT INTO plan_states
               (kernel_session_id, plan_id, status, current_step, steps,
                intent_version, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                sid,
                "legacy_plan",
                "active",
                "step_1",
                json.dumps([{"step_id": "step_1", "name": "legacy step"}]),
                3,
                None,
            ),
        )
        await store.conn.execute(
            """INSERT INTO belief_items
               (belief_id, kernel_session_id, claim, status, confidence,
                supporting_evidence, conflicting_evidence, visibility,
                last_verified_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "belief_legacy",
                sid,
                "legacy claim",
                "verified",
                0.9,
                "[]",
                "[]",
                "shared",
                None,
                None,
            ),
        )
        await store.conn.execute(
            """INSERT INTO commitments
               (commitment_id, kernel_session_id, statement, created_by, status,
                requires_confirmation, related_intent_version, resolved_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "todo_legacy",
                sid,
                "legacy todo",
                "talker",
                "pending",
                1,
                3,
                None,
                None,
            ),
        )
        await store.conn.commit()
    finally:
        await store.close()

    dry = await migrate_legacy_state_tables(str(db_path))
    assert dry["dry_run"] == 4
    assert dry["migrated"] == 0

    store = SqliteStore(str(db_path))
    await store.connect()
    try:
        assert await _count_rows(store, "task_brief_states", sid) == 0
        assert await _count_rows(store, "task_flows", sid) == 0
    finally:
        await store.close()

    written = await migrate_legacy_state_tables(str(db_path), write=True)
    assert written["migrated"] == 4

    store = SqliteStore(str(db_path))
    await store.connect()
    try:
        task_brief = await store.get_task_brief(sid)
        task_flow = await store.get_task_flow(sid)
        claims = await store.get_claim_items(sid)
        todos = await store.get_todo_obligations(sid)

        assert task_brief.goal == "legacy goal"
        assert task_brief.task_brief_version == 3
        assert task_flow.flow_id == "legacy_plan"
        assert task_flow.current_step == "step_1"
        assert claims[0].claim == "legacy claim"
        assert todos[0].statement == "legacy todo"
    finally:
        await store.close()
