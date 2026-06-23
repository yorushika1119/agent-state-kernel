"""Smoke test: real LLM fallback resolves an ambiguous task route."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_local_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip().lstrip("\ufeff")] = value.strip().strip('"').strip("'")


load_local_env()

from src.kernel.engine import KernelEngine
from src.kms.manager import KmsManager
from src.kms.routing.task_context_router import route_task_context
from src.stores.sqlite_store import SqliteStore


async def main() -> int:
    if not os.getenv("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY is required for live LLM router smoke.")
        return 2

    store = SqliteStore(":memory:")
    await store.connect()
    engine = KernelEngine(store)
    manager = KmsManager(store, engine, enable_llm_router=True)

    runtime_session_id = "rt-live-llm-router-smoke"
    agent_id = "agent-live-llm-router-smoke"

    try:
        first = await manager.dispatch_user_message(
            text="Please maintain the payment webhook retry policy for failed payment callbacks.",
            runtime_session_id=runtime_session_id,
            runtime_type="smoke",
            agent_id=agent_id,
        )
        second = await manager.dispatch_user_message(
            text="new task: maintain the vector search index migration plan.",
            runtime_session_id=runtime_session_id,
            runtime_type="smoke",
            agent_id=agent_id,
        )

        tasks = await store.list_global_tasks(user_session_id=first.user_session_id)
        query = "那个付款回调任务继续做"
        rule_route = route_task_context(
            query,
            user_session_id=first.user_session_id,
            runtime_session_id=runtime_session_id,
            tasks=tasks,
        )
        routed = await manager.dispatch_user_message(
            text=query,
            runtime_session_id=runtime_session_id,
            runtime_type="smoke",
            agent_id=agent_id,
            user_session_id=first.user_session_id,
        )
        other_status = await manager.dispatch_user_message(
            text="另一个任务当前进度？",
            runtime_session_id=runtime_session_id,
            runtime_type="smoke",
            agent_id=agent_id,
            user_session_id=first.user_session_id,
        )
        explicit_new = await manager.dispatch_user_message(
            text="新任务：整理 observer notification 推送策略",
            runtime_session_id=runtime_session_id,
            runtime_type="smoke",
            agent_id=agent_id,
            user_session_id=first.user_session_id,
        )

        print("LIVE_LLM_ROUTER_SMOKE")
        print(f"FIRST_TASK_ID={first.task_id}")
        print(f"SECOND_TASK_ID={second.task_id}")
        print(f"RULE_DECISION={rule_route.routing_decision}")
        print(f"RULE_CONFIDENCE={rule_route.confidence}")
        print(f"FINAL_DECISION={routed.route_decision}")
        print(f"FINAL_TASK_ID={routed.task_id}")
        print(f"FINAL_ACTION={routed.action}")
        print(f"FINAL_TASK_ACTION={routed.task_action}")
        print(f"FINAL_REASON={routed.reason}")
        print(f"OTHER_STATUS_ACTION={other_status.action}")
        print(f"OTHER_STATUS_TASK_ID={other_status.task_id}")
        print(f"OTHER_STATUS_REQUIRES_THINKER={other_status.requires_thinker}")
        print(f"EXPLICIT_NEW_TASK_ACTION={explicit_new.action}")
        print(f"EXPLICIT_NEW_TASK_ID={explicit_new.task_id}")
        print(f"EXPLICIT_NEW_TASK_ACTION_KIND={explicit_new.task_action}")

        if rule_route.routing_decision != "ask_clarification":
            raise AssertionError("rule route should be ambiguous before LLM fallback")
        if routed.route_decision != "select_existing":
            raise AssertionError("LLM router did not select an existing task")
        if routed.task_id != first.task_id:
            raise AssertionError("LLM router selected the wrong task")
        if routed.task_id == second.task_id:
            raise AssertionError("LLM router stayed on the active unrelated task")
        if other_status.action != "respond_from_kernel":
            raise AssertionError("other-task status query should be answered by Kernel")
        if other_status.requires_thinker:
            raise AssertionError("other-task status query should not wake Thinker")
        if other_status.task_id != second.task_id:
            raise AssertionError("other-task status query selected the wrong task")
        if explicit_new.task_action != "start_new_task":
            raise AssertionError("explicit new task marker did not create a new task")
        if explicit_new.task_id in {first.task_id, second.task_id, routed.task_id}:
            raise AssertionError("explicit new task reused an existing task")
        return 0
    finally:
        await store.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
