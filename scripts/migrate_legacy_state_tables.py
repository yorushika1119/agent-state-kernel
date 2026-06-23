"""Backfill task-first state tables from legacy compatibility tables.

Default mode is dry-run. Pass --write to upsert missing task_brief/task_flow/
claim/todo rows. This script does not delete legacy tables.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.schema.state import (  # noqa: E402
    BeliefStatus,
    ClaimItem,
    CommitmentStatus,
    PlanStatus,
    TaskBriefState,
    TaskFlowState,
    TodoObligation,
)
from src.stores.sqlite_store import SqliteStore  # noqa: E402


LEGACY_TO_NEW = {
    "intent_states": "task_brief_states",
    "plan_states": "task_flows",
    "belief_items": "claim_items",
    "commitments": "todo_obligations",
}


async def _has_row(store: SqliteStore, table: str, session_id: str) -> bool:
    rows = await store.conn.execute_fetchall(
        f"SELECT 1 FROM {table} WHERE kernel_session_id = ? LIMIT 1",
        (session_id,),
    )
    return bool(rows)


async def _legacy_rows(store: SqliteStore, table: str, session_id: str) -> list:
    rows = await store.conn.execute_fetchall(
        f"SELECT * FROM {table} WHERE kernel_session_id = ?",
        (session_id,),
    )
    return list(rows)


async def migrate_legacy_state_tables(
    db_path: str,
    *,
    write: bool = False,
    overwrite: bool = False,
    limit: int = 10000,
) -> dict[str, int]:
    store = SqliteStore(db_path)
    await store.connect()
    stats = {
        "sessions": 0,
        "migrated": 0,
        "dry_run": 0,
        "skipped_existing": 0,
        "missing_legacy": 0,
    }
    try:
        sessions = await store.list_sessions(limit=limit)
        stats["sessions"] = len(sessions)
        for row in sessions:
            session_id = row["kernel_session_id"]
            session = await store.get_session(session_id)
            task_id = session.active_task_id if session else ""

            for legacy, new in LEGACY_TO_NEW.items():
                legacy_rows = await _legacy_rows(store, legacy, session_id)
                if not legacy_rows:
                    stats["missing_legacy"] += 1
                    continue
                if not overwrite and await _has_row(store, new, session_id):
                    stats["skipped_existing"] += 1
                    continue
                if not write:
                    stats["dry_run"] += len(legacy_rows)
                    continue

                if legacy == "intent_states":
                    r = legacy_rows[0]
                    await store.save_task_brief(
                        TaskBriefState(
                            kernel_session_id=session_id,
                            task_id=task_id,
                            task_brief_version=r["intent_version"] or 0,
                            goal=r["goal"] or "",
                            output_format=r["output_format"] or "",
                            constraints=json.loads(r["constraints"] or "[]"),
                            priority=r["priority"] or "normal",
                            cancelled=bool(r["cancelled"]),
                        )
                    )
                    stats["migrated"] += 1
                elif legacy == "plan_states":
                    r = legacy_rows[0]
                    await store.save_task_flow(
                        TaskFlowState(
                            kernel_session_id=session_id,
                            flow_id=r["plan_id"] or "",
                            task_id=task_id,
                            status=PlanStatus(r["status"] or PlanStatus.ACTIVE.value),
                            current_step=r["current_step"] or "",
                            steps=json.loads(r["steps"] or "[]"),
                            task_brief_version=r["intent_version"] or 0,
                            execution_summary=await store._build_execution_summary(session_id),
                        )
                    )
                    stats["migrated"] += 1
                elif legacy == "belief_items":
                    for r in legacy_rows:
                        await store.save_claim_item(
                            ClaimItem(
                                claim_id=r["belief_id"],
                                kernel_session_id=session_id,
                                task_id=task_id,
                                claim=r["claim"] or "",
                                status=BeliefStatus(r["status"]),
                                confidence=r["confidence"] or 0.0,
                                supporting_evidence=json.loads(r["supporting_evidence"] or "[]"),
                                conflicting_evidence=json.loads(r["conflicting_evidence"] or "[]"),
                                visibility=r["visibility"] or "shared",
                            )
                        )
                        stats["migrated"] += 1
                elif legacy == "commitments":
                    for r in legacy_rows:
                        await store.save_todo_obligation(
                            TodoObligation(
                                obligation_id=r["commitment_id"],
                                kernel_session_id=session_id,
                                task_id=task_id,
                                statement=r["statement"] or "",
                                created_by=r["created_by"] or "talker",
                                status=CommitmentStatus(r["status"]),
                                requires_confirmation=bool(r["requires_confirmation"]),
                                related_task_brief_version=r["related_intent_version"] or 0,
                            )
                        )
                        stats["migrated"] += 1
        return stats
    finally:
        await store.close()


async def check_legacy_state_table_removal(
    db_path: str,
    *,
    limit: int = 10000,
) -> dict[str, object]:
    store = SqliteStore(db_path)
    await store.connect()
    stats: dict[str, object] = {
        "sessions": 0,
        "legacy_rows": 0,
        "unmigrated_sessions": 0,
        "fallback_hit_count": 0,
        "safe_to_remove": False,
        "blockers": [],
        "legacy_rows_by_table": {},
    }
    try:
        sessions = await store.list_sessions(limit=limit)
        stats["sessions"] = len(sessions)
        legacy_rows_by_table: dict[str, int] = {}
        unmigrated_sessions: set[str] = set()

        for row in sessions:
            session_id = row["kernel_session_id"]
            for legacy, new in LEGACY_TO_NEW.items():
                legacy_rows = await _legacy_rows(store, legacy, session_id)
                legacy_rows_by_table[legacy] = (
                    legacy_rows_by_table.get(legacy, 0) + len(legacy_rows)
                )
                if legacy_rows and not await _has_row(store, new, session_id):
                    unmigrated_sessions.add(session_id)

        audit_rows = await store.get_legacy_state_fallback_audit()
        fallback_hit_count = sum(int(row["hit_count"] or 0) for row in audit_rows)
        legacy_rows = sum(legacy_rows_by_table.values())
        blockers: list[str] = []
        if unmigrated_sessions:
            blockers.append("unmigrated_legacy_state")
        if fallback_hit_count:
            blockers.append("legacy_fallback_observed")

        stats.update(
            {
                "legacy_rows": legacy_rows,
                "unmigrated_sessions": len(unmigrated_sessions),
                "fallback_hit_count": fallback_hit_count,
                "safe_to_remove": not blockers,
                "blockers": blockers,
                "legacy_rows_by_table": legacy_rows_by_table,
            }
        )
        return stats
    finally:
        await store.close()


async def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path", help="Path to the kernel SQLite database.")
    parser.add_argument("--write", action="store_true", help="Write missing new-table rows.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing new-table rows.")
    parser.add_argument("--limit", type=int, default=10000)
    parser.add_argument("--removal-check", action="store_true", help="Only check physical removal readiness.")
    args = parser.parse_args()

    if args.removal_check:
        stats = await check_legacy_state_table_removal(
            args.db_path,
            limit=args.limit,
        )
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    stats = await migrate_legacy_state_tables(
        args.db_path,
        write=args.write,
        overwrite=args.overwrite,
        limit=args.limit,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
