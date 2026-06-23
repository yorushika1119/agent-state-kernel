"""Report legacy state fallback reads.

This script is read-only for audit rows. It connects through SqliteStore so
newer audit tables are created before querying older local databases.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.stores.sqlite_store import SqliteStore  # noqa: E402


def _format_rows(rows: list[dict]) -> str:
    total = sum(int(row["hit_count"] or 0) for row in rows)
    lines = [
        "LEGACY_FALLBACK_AUDIT",
        f"ROWS={len(rows)}",
        f"HIT_COUNT={total}",
    ]
    if not rows:
        lines.append("NO_LEGACY_FALLBACK_HITS")
        return "\n".join(lines)

    lines.append("| kernel_session_id | model | legacy_table | hit_count | last_hit_at |")
    lines.append("| --- | --- | --- | ---: | --- |")
    for row in rows:
        lines.append(
            "| {kernel_session_id} | {model} | {legacy_table} | {hit_count} | {last_hit_at} |".format(
                kernel_session_id=row["kernel_session_id"],
                model=row["model"],
                legacy_table=row["legacy_table"],
                hit_count=row["hit_count"],
                last_hit_at=row["last_hit_at"] or "",
            )
        )
    return "\n".join(lines)


async def report_legacy_fallback_audit(db_path: str) -> str:
    store = SqliteStore(db_path)
    await store.connect()
    try:
        rows = await store.get_legacy_state_fallback_audit()
    finally:
        await store.close()
    return _format_rows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Report legacy fallback audit rows.")
    parser.add_argument("--db", default="data/kernel.db", help="SQLite database path")
    args = parser.parse_args()
    print(asyncio.run(report_legacy_fallback_audit(args.db)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
