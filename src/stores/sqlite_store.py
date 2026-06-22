"""SQLite 持久化层 — Agent State Kernel 的存储引擎。

管理 21 张表：
  1. cognitive_events     — 追加事件日志
  2. evidence_items        — 证据条目
  3. belief_items          — 信念条目
  4. execution_actions     — 执行动作
  5. intent_states         — 意图状态
  6. plan_states           — 计划状态
  7. task_snapshots        — task/goal 快照
  8. progress_states       — 进度合成
  9. commitments           — Talker 承诺
  10. sync_cursors         — 外部同步游标（Multica）
  11. runtime_refs         — Runtime 引用索引
  12. session_links        — 会话映射
  13. user_sessions        — 用户会话目录
  14. global_tasks         — 全局任务目录
  15. task_context_routes  — 路由审计
  16. task_brief_states    — 新版 task_brief 兼容影子表
  17. task_flows           — 新版 task_flow 兼容影子表
  18. claim_items          — 新版 claim 兼容影子表
  19. todo_obligations     — 新版 todo 兼容影子表
  20. thinker_dispatches   — Thinker 下发队列
  21. observer_notifications — Observer / Talker 主动通知

所有表通过 kernel_session_id 关联到一个会话。
事件日志为 append-only——从不更新或删除。
派生状态的 upsert 必须 commit——忘记 commit 是常见坑。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

import aiosqlite

from src.schema.events import CognitiveEvent
from src.schema.state import (
    ClaimItem,
    BeliefItem,
    BeliefStatus,
    Commitment,
    CommitmentStatus,
    EvidenceItem,
    EvidenceType,
    ExecutionAction,
    GlobalTask,
    IntentState,
    ObserverNotification,
    ObserverNotificationStatus,
    PlanState,
    PlanStatus,
    ProgressState,
    Reliability,
    RuntimeReference,
    TaskBriefState,
    TaskFlowState,
    SessionLink,
    TaskRouteDecision,
    TaskSnapshot,
    TaskStatus,
    ThinkerDispatch,
    ThinkerDispatchStatus,
    TodoObligation,
    UserSession,
    StepStatus,
)
from src.utils.time import utc_from_iso, utc_now, utc_now_iso

logger = logging.getLogger(__name__)


class SqliteStore:
    """基于 SQLite 的 Kernel 状态存储。

    异步包装 aiosqlite。所有数据库操作通过此类的公共方法
    进行——绝不要在外部直接操作数据库。
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: Optional[aiosqlite.Connection] = None

    # ==================================================================
    # 连接管理
    # ==================================================================

    async def connect(self):
        """打开连接并创建所有表。幂等——重复调用安全。"""
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row
        await self._create_tables()
        logger.info("Store connected: %s", self.db_path)

    async def close(self):
        """关闭数据库连接。"""
        if self.conn:
            await self.conn.close()
            self.conn = None

    async def _create_tables(self):
        """创建所有 10 张表。使用 IF NOT EXISTS 保证幂等。"""
        # ── 1. 认知事件（append-only 审计日志）──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS cognitive_events (
                event_id TEXT PRIMARY KEY,
                kernel_session_id TEXT NOT NULL,
                runtime_session_id TEXT,
                run_id TEXT DEFAULT '',
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                source_component TEXT,
                payload TEXT,
                runtime_refs TEXT,
                visibility TEXT DEFAULT 'shared',
                intent_version INTEGER DEFAULT 0,
                state_version INTEGER DEFAULT 1,
                created_at TEXT
            )
        """)

        # ── 2. 证据条目 ──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS evidence_items (
                evidence_id TEXT NOT NULL,
                kernel_session_id TEXT NOT NULL,
                task_id TEXT DEFAULT '',
                evidence_type TEXT DEFAULT 'web_page',
                source TEXT,
                title TEXT,
                observed_at TEXT,
                source_date TEXT,
                reliability TEXT DEFAULT 'unknown',
                extracted_facts TEXT,
                raw_ref TEXT,
                accepted_by TEXT DEFAULT 'kernel_manager',
                accepted_at TEXT,
                PRIMARY KEY (kernel_session_id, evidence_id)
            )
        """)

        # ── 3. 信念条目 ──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS belief_items (
                belief_id TEXT NOT NULL,
                kernel_session_id TEXT NOT NULL,
                claim TEXT,
                status TEXT DEFAULT 'unverified',
                confidence REAL DEFAULT 0.0,
                supporting_evidence TEXT,
                conflicting_evidence TEXT,
                visibility TEXT DEFAULT 'shared',
                last_verified_at TEXT,
                created_at TEXT,
                PRIMARY KEY (kernel_session_id, belief_id)
            )
        """)

        # ── 4. 执行动作 ──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS execution_actions (
                action_id TEXT NOT NULL,
                kernel_session_id TEXT NOT NULL,
                task_id TEXT DEFAULT '',
                step_id TEXT,
                tool TEXT,
                status TEXT DEFAULT 'running',
                input_summary TEXT,
                output_ref TEXT,
                runtime_refs TEXT,
                retry_count INTEGER DEFAULT 0,
                started_at TEXT,
                ended_at TEXT,
                PRIMARY KEY (kernel_session_id, action_id)
            )
        """)

        # ── 5. 意图状态 ──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS intent_states (
                kernel_session_id TEXT PRIMARY KEY,
                intent_version INTEGER DEFAULT 0,
                goal TEXT,
                constraints TEXT,
                output_format TEXT,
                priority TEXT DEFAULT 'normal',
                cancelled INTEGER DEFAULT 0,
                last_user_update_at TEXT,
                updated_at TEXT
            )
        """)

        # ── 6. 计划状态 ──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS plan_states (
                kernel_session_id TEXT PRIMARY KEY,
                plan_id TEXT,
                status TEXT DEFAULT 'active',
                current_step TEXT,
                steps TEXT,
                intent_version INTEGER DEFAULT 0,
                updated_at TEXT
            )
        """)

        # ── 7. task 快照 ──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS task_snapshots (
                task_id TEXT NOT NULL,
                kernel_session_id TEXT NOT NULL,
                title TEXT,
                goal TEXT,
                constraints TEXT,
                status TEXT DEFAULT 'active',
                plan_id TEXT,
                current_step TEXT,
                current_step_name TEXT,
                steps TEXT,
                last_run_id TEXT DEFAULT '',
                last_interrupted_run_id TEXT DEFAULT '',
                resume_summary TEXT DEFAULT '',
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (kernel_session_id, task_id)
            )
        """)

        # ── 8. 进度状态 ──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS progress_states (
                kernel_session_id TEXT PRIMARY KEY,
                status TEXT DEFAULT 'idle',
                stage TEXT,
                summary TEXT,
                safe_facts TEXT,
                unsafe_claims TEXT,
                needs_user_input INTEGER DEFAULT 0,
                allowed_actions TEXT,
                forbidden_actions TEXT,
                updated_at TEXT
            )
        """)

        # ── 9. 承诺 ──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS commitments (
                commitment_id TEXT NOT NULL,
                kernel_session_id TEXT NOT NULL,
                statement TEXT,
                created_by TEXT DEFAULT 'talker',
                status TEXT DEFAULT 'pending',
                requires_confirmation INTEGER DEFAULT 0,
                related_intent_version INTEGER DEFAULT 0,
                resolved_at TEXT,
                created_at TEXT,
                PRIMARY KEY (kernel_session_id, commitment_id)
            )
        """)

        # ── 10. 同步游标 ──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_cursors (
                kernel_session_id TEXT PRIMARY KEY,
                external_task_id TEXT,
                external_system TEXT DEFAULT 'multica',
                last_synced_event_id TEXT,
                last_synced_state_version INTEGER DEFAULT 0,
                last_synced_at TEXT
            )
        """)

        # ── 11. Runtime Reference Index（§5.13）──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS runtime_refs (
                kernel_ref_id TEXT NOT NULL,
                kernel_session_id TEXT NOT NULL,
                runtime_session_id TEXT,
                runtime_type TEXT DEFAULT 'cli-agent',
                ref_type TEXT,
                ref_id TEXT,
                summary TEXT,
                visibility TEXT DEFAULT 'private',
                created_at TEXT,
                PRIMARY KEY (kernel_session_id, kernel_ref_id)
            )
        """)

        # ── 12. 会话链接 ──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS session_links (
                kernel_session_id TEXT PRIMARY KEY,
                runtime_session_id TEXT,
                runtime_id TEXT,
                runtime_type TEXT DEFAULT 'cli-agent',
                agent_id TEXT,
                external_source TEXT,
                external_workspace_id TEXT,
                external_issue_id TEXT,
                external_task_id TEXT,
                status TEXT DEFAULT 'running',
                intent_version INTEGER DEFAULT 0,
                state_version INTEGER DEFAULT 1,
                active_run_id TEXT DEFAULT '',
                active_task_id TEXT DEFAULT '',
                cancellation_token INTEGER DEFAULT 0,
                last_paused_task_id TEXT DEFAULT '',
                last_interrupted_run_id TEXT DEFAULT '',
                last_interrupting_run_id TEXT DEFAULT '',
                last_interrupt_reason TEXT DEFAULT '',
                last_interrupt_at TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)

        # ── 13. User Session ──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                user_session_id TEXT PRIMARY KEY,
                runtime_session_id TEXT,
                runtime_id TEXT,
                runtime_type TEXT DEFAULT 'cli-agent',
                agent_id TEXT,
                session_kind TEXT DEFAULT 'user_chat',
                created_by TEXT DEFAULT 'runtime',
                linked_task_ids TEXT,
                active_task_id TEXT DEFAULT '',
                created_at TEXT,
                updated_at TEXT
            )
        """)

        # ── 14. Global Task Directory ──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS global_tasks (
                task_id TEXT PRIMARY KEY,
                kernel_session_id TEXT NOT NULL,
                user_session_id TEXT,
                agent_id TEXT,
                title TEXT,
                task_type TEXT DEFAULT 'other',
                task_description TEXT,
                task_brief_version INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                priority TEXT DEFAULT 'normal',
                stage TEXT,
                external_refs TEXT,
                routing_hints TEXT,
                created_at TEXT,
                updated_at TEXT,
                last_user_touch_at TEXT,
                last_activity_at TEXT,
                last_manager_update_at TEXT,
                last_talker_update_at TEXT,
                last_thinker_update_at TEXT
            )
        """)

        # ── 15. Task Context Router audit ──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS task_context_routes (
                route_id TEXT PRIMARY KEY,
                user_session_id TEXT,
                runtime_session_id TEXT,
                user_message TEXT,
                routing_decision TEXT,
                target_task_id TEXT,
                confidence REAL DEFAULT 0.0,
                matched_hints TEXT,
                time_reason TEXT,
                candidate_tasks TEXT,
                needs_user_clarification INTEGER DEFAULT 0,
                clarification_question TEXT,
                created_at TEXT
            )
        """)

        # ── 16. Task Brief State ──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS task_brief_states (
                kernel_session_id TEXT PRIMARY KEY,
                task_id TEXT DEFAULT '',
                task_brief_version INTEGER DEFAULT 0,
                goal TEXT,
                output_format TEXT,
                constraints TEXT,
                priority TEXT DEFAULT 'normal',
                cancelled INTEGER DEFAULT 0,
                updated_at TEXT
            )
        """)

        # ── 17. Task Flow State ──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS task_flows (
                kernel_session_id TEXT PRIMARY KEY,
                flow_id TEXT,
                task_id TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                current_step TEXT,
                steps TEXT,
                task_brief_version INTEGER DEFAULT 0,
                execution_summary TEXT,
                updated_at TEXT
            )
        """)

        # ── 18. Claim Items ──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS claim_items (
                claim_id TEXT NOT NULL,
                kernel_session_id TEXT NOT NULL,
                task_id TEXT DEFAULT '',
                claim TEXT,
                status TEXT DEFAULT 'unverified',
                confidence REAL DEFAULT 0.0,
                supporting_evidence TEXT,
                conflicting_evidence TEXT,
                visibility TEXT DEFAULT 'shared',
                last_verified_at TEXT,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (kernel_session_id, claim_id)
            )
        """)

        # ── 19. Todo Obligations ──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS todo_obligations (
                obligation_id TEXT NOT NULL,
                kernel_session_id TEXT NOT NULL,
                task_id TEXT DEFAULT '',
                statement TEXT,
                created_by TEXT DEFAULT 'talker',
                status TEXT DEFAULT 'pending',
                requires_confirmation INTEGER DEFAULT 0,
                related_task_brief_version INTEGER DEFAULT 0,
                resolved_at TEXT,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (kernel_session_id, obligation_id)
            )
        """)

        # ── 20. Thinker Dispatches ──
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS thinker_dispatches (
                dispatch_id TEXT PRIMARY KEY,
                kernel_session_id TEXT NOT NULL,
                task_id TEXT DEFAULT '',
                run_id TEXT DEFAULT '',
                task_brief_version INTEGER DEFAULT 0,
                dispatch_type TEXT DEFAULT 'start',
                status TEXT DEFAULT 'pending',
                cancellation_token INTEGER DEFAULT 0,
                payload TEXT,
                claimed_by TEXT DEFAULT '',
                claimed_at TEXT,
                heartbeat_at TEXT,
                completed_at TEXT,
                failed_at TEXT,
                error TEXT DEFAULT '',
                created_at TEXT,
                updated_at TEXT
            )
        """)

        # ─── 21. Observer Notifications ───
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS observer_notifications (
                notification_id TEXT PRIMARY KEY,
                target TEXT DEFAULT 'observer',
                kernel_session_id TEXT DEFAULT '',
                task_id TEXT DEFAULT '',
                notification_type TEXT DEFAULT 'progress_update',
                urgency TEXT DEFAULT 'normal',
                reason TEXT DEFAULT '',
                progress_ref TEXT DEFAULT '',
                suggested_observer_context TEXT,
                delivery_policy TEXT,
                status TEXT DEFAULT 'pending',
                acknowledged_at TEXT,
                resolved_at TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)

        await self._ensure_column("cognitive_events", "run_id", "TEXT DEFAULT ''")
        await self._ensure_column("session_links", "active_run_id", "TEXT DEFAULT ''")
        await self._ensure_column("session_links", "active_task_id", "TEXT DEFAULT ''")
        await self._ensure_column("session_links", "last_paused_task_id", "TEXT DEFAULT ''")
        await self._ensure_column("session_links", "last_interrupted_run_id", "TEXT DEFAULT ''")
        await self._ensure_column("session_links", "last_interrupting_run_id", "TEXT DEFAULT ''")
        await self._ensure_column("session_links", "last_interrupt_reason", "TEXT DEFAULT ''")
        await self._ensure_column("session_links", "last_interrupt_at", "TEXT")

        await self._ensure_column("evidence_items", "task_id", "TEXT DEFAULT ''")
        await self._ensure_column("execution_actions", "task_id", "TEXT DEFAULT ''")
        await self._ensure_column("task_brief_states", "task_id", "TEXT DEFAULT ''")
        await self._ensure_column("task_brief_states", "task_brief_version", "INTEGER DEFAULT 0")
        await self._ensure_column("task_flows", "task_id", "TEXT DEFAULT ''")
        await self._ensure_column("task_flows", "task_brief_version", "INTEGER DEFAULT 0")
        await self._ensure_column("task_flows", "execution_summary", "TEXT")
        await self._ensure_column("claim_items", "task_id", "TEXT DEFAULT ''")
        await self._ensure_column("claim_items", "created_at", "TEXT")
        await self._ensure_column("claim_items", "updated_at", "TEXT")
        await self._ensure_column("todo_obligations", "task_id", "TEXT DEFAULT ''")
        await self._ensure_column("todo_obligations", "related_task_brief_version", "INTEGER DEFAULT 0")
        await self._ensure_column("todo_obligations", "created_at", "TEXT")
        await self._ensure_column("todo_obligations", "updated_at", "TEXT")
        await self._ensure_column("thinker_dispatches", "task_id", "TEXT DEFAULT ''")
        await self._ensure_column("thinker_dispatches", "run_id", "TEXT DEFAULT ''")
        await self._ensure_column("thinker_dispatches", "task_brief_version", "INTEGER DEFAULT 0")
        await self._ensure_column("thinker_dispatches", "payload", "TEXT")
        await self._ensure_column("thinker_dispatches", "claimed_by", "TEXT DEFAULT ''")
        await self._ensure_column("thinker_dispatches", "claimed_at", "TEXT")
        await self._ensure_column("thinker_dispatches", "heartbeat_at", "TEXT")
        await self._ensure_column("thinker_dispatches", "completed_at", "TEXT")
        await self._ensure_column("thinker_dispatches", "failed_at", "TEXT")
        await self._ensure_column("thinker_dispatches", "error", "TEXT DEFAULT ''")
        await self._ensure_column("observer_notifications", "target", "TEXT DEFAULT 'observer'")
        await self._ensure_column("observer_notifications", "kernel_session_id", "TEXT DEFAULT ''")
        await self._ensure_column("observer_notifications", "task_id", "TEXT DEFAULT ''")
        await self._ensure_column("observer_notifications", "notification_type", "TEXT DEFAULT 'progress_update'")
        await self._ensure_column("observer_notifications", "urgency", "TEXT DEFAULT 'normal'")
        await self._ensure_column("observer_notifications", "reason", "TEXT DEFAULT ''")
        await self._ensure_column("observer_notifications", "progress_ref", "TEXT DEFAULT ''")
        await self._ensure_column("observer_notifications", "suggested_observer_context", "TEXT")
        await self._ensure_column("observer_notifications", "delivery_policy", "TEXT")
        await self._ensure_column("observer_notifications", "status", "TEXT DEFAULT 'pending'")
        await self._ensure_column("observer_notifications", "acknowledged_at", "TEXT")
        await self._ensure_column("observer_notifications", "resolved_at", "TEXT")

        await self.conn.commit()
        logger.info("Tables created (21 tables)")

    async def _ensure_column(self, table_name: str, column_name: str, ddl: str) -> None:
        rows = await self.conn.execute_fetchall(f"PRAGMA table_info({table_name})")
        existing = {row["name"] for row in rows}
        if column_name in existing:
            return
        await self.conn.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"
        )

    # ==================================================================
    # 会话管理
    # ==================================================================

    async def create_session(
        self,
        agent_id: str = "",
        runtime_id: str = "",
        runtime_session_id: str = "",
        runtime_type: str = "cli-agent",
        external_source: str = "",
        external_workspace_id: str = "",
        external_issue_id: str = "",
        external_task_id: str = "",
    ) -> SessionLink:
        """创建新会话——生成唯一 kernel_session_id 并写入 session_links 表。"""
        session_id = f"ask_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        resolved_runtime_id = runtime_id or runtime_session_id
        await self.conn.execute(
            """INSERT INTO session_links
               (kernel_session_id, runtime_session_id, runtime_id, runtime_type,
                agent_id, external_source, external_workspace_id, external_issue_id,
                external_task_id, cancellation_token, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'running', ?, ?)""",
            (
                session_id,
                runtime_session_id,
                resolved_runtime_id,
                runtime_type,
                agent_id,
                external_source,
                external_workspace_id,
                external_issue_id,
                external_task_id,
                now,
                now,
            ),
        )
        await self.conn.commit()

        session = await self.get_session(session_id)
        return session

    async def observe_user_session(
        self,
        *,
        user_session_id: str = "",
        runtime_session_id: str = "",
        runtime_id: str = "",
        runtime_type: str = "cli-agent",
        agent_id: str = "",
        session_kind: str = "user_chat",
        created_by: str = "runtime",
    ) -> UserSession:
        """Observe or create a user-facing session."""
        existing = None
        if user_session_id:
            existing = await self.get_user_session(user_session_id)
        elif runtime_session_id:
            existing = await self.get_user_session_by_runtime_session(runtime_session_id)
        if existing:
            await self.conn.execute(
                """UPDATE user_sessions
                   SET runtime_id = ?, runtime_type = ?, agent_id = ?,
                       session_kind = ?, updated_at = ?
                   WHERE user_session_id = ?""",
                (
                    runtime_id or existing.runtime_id,
                    runtime_type or existing.runtime_type,
                    agent_id or existing.agent_id,
                    session_kind or existing.session_kind,
                    utc_now_iso(),
                    existing.user_session_id,
                ),
            )
            await self.conn.commit()
            refreshed = await self.get_user_session(existing.user_session_id)
            return refreshed

        resolved_id = user_session_id or f"us_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        await self.conn.execute(
            """INSERT INTO user_sessions
               (user_session_id, runtime_session_id, runtime_id, runtime_type,
                agent_id, session_kind, created_by, linked_task_ids,
                active_task_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, '[]', '', ?, ?)""",
            (
                resolved_id,
                runtime_session_id,
                runtime_id or runtime_session_id,
                runtime_type,
                agent_id,
                session_kind,
                created_by,
                now,
                now,
            ),
        )
        await self.conn.commit()
        session = await self.get_user_session(resolved_id)
        return session

    async def get_user_session(self, user_session_id: str) -> Optional[UserSession]:
        if not user_session_id:
            return None
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM user_sessions WHERE user_session_id = ?",
            (user_session_id,),
        )
        if not rows:
            return None
        return self._row_to_user_session(rows[0])

    async def get_user_session_by_runtime_session(
        self,
        runtime_session_id: str,
    ) -> Optional[UserSession]:
        if not runtime_session_id:
            return None
        rows = await self.conn.execute_fetchall(
            """SELECT * FROM user_sessions
               WHERE runtime_session_id = ?
               ORDER BY updated_at DESC, created_at DESC
               LIMIT 1""",
            (runtime_session_id,),
        )
        if not rows:
            return None
        return self._row_to_user_session(rows[0])

    async def link_task_to_user_session(
        self,
        user_session_id: str,
        task_id: str,
        *,
        active: bool = True,
    ) -> None:
        session = await self.get_user_session(user_session_id)
        if not session:
            return
        linked = list(session.linked_task_ids)
        if task_id not in linked:
            linked.append(task_id)
        await self.conn.execute(
            """UPDATE user_sessions
               SET linked_task_ids = ?, active_task_id = ?, session_kind = ?,
                   updated_at = ?
               WHERE user_session_id = ?""",
            (
                json.dumps(linked, ensure_ascii=False),
                task_id if active else session.active_task_id,
                "task" if len(linked) == 1 else "mixed",
                utc_now_iso(),
                user_session_id,
            ),
        )
        await self.conn.commit()

    async def get_session(self, session_id: str) -> Optional[SessionLink]:
        """按 ID 获取会话。返回 None 如不存在。"""
        row = await self.conn.execute_fetchall(
            "SELECT * FROM session_links WHERE kernel_session_id = ?",
            (session_id,),
        )
        if not row:
            return None
        return self._row_to_session(row[0])

    async def list_sessions(self, status: str = "", limit: int = 20) -> list:
        """列出会话，可按状态过滤，按创建时间倒序。"""
        query = "SELECT * FROM session_links"
        params = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = await self.conn.execute_fetchall(query, params)
        return [
            {
                "kernel_session_id": r["kernel_session_id"],
                "runtime_session_id": r["runtime_session_id"],
                "runtime_type": r["runtime_type"],
                "external_source": r["external_source"],
                "external_task_id": r["external_task_id"],
                "status": r["status"],
                "active_run_id": r["active_run_id"] or "",
                "active_task_id": r["active_task_id"] or "",
                "last_paused_task_id": r["last_paused_task_id"] or "",
                "last_interrupted_run_id": r["last_interrupted_run_id"] or "",
                "last_interrupting_run_id": r["last_interrupting_run_id"] or "",
                "last_interrupt_reason": r["last_interrupt_reason"] or "",
                "last_interrupt_at": r["last_interrupt_at"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    async def list_sessions_by_runtime_session(
        self,
        runtime_session_id: str,
        limit: int = 20,
    ) -> List[SessionLink]:
        """按宿主会话 ID 列出关联的 Kernel 会话。"""
        if not runtime_session_id:
            return []
        rows = await self.conn.execute_fetchall(
            """SELECT * FROM session_links
               WHERE runtime_session_id = ?
               ORDER BY updated_at DESC, created_at DESC
               LIMIT ?""",
            (runtime_session_id, limit),
        )
        return [self._row_to_session(row) for row in rows]

    async def set_cancellation_token(self, session_id: str, token: bool) -> None:
        """设置会话的取消令牌（§5.1）。Thinker 每次读视图时应检查此值。"""
        await self.conn.execute(
            "UPDATE session_links SET cancellation_token = ?, updated_at = ? WHERE kernel_session_id = ?",
            (int(token), utc_now_iso(), session_id),
        )
        await self.conn.commit()

    async def delete_session(self, session_id: str) -> bool:
        """删除会话及其所有派生状态。"""
        session = await self.get_session(session_id)
        if not session:
            return False
        tables = [
            "progress_states", "plan_states", "intent_states",
            "task_snapshots", "global_tasks", "task_brief_states",
            "task_flows", "claim_items", "todo_obligations",
            "thinker_dispatches", "observer_notifications",
            "belief_items", "evidence_items", "execution_actions",
            "commitments", "sync_cursors", "runtime_refs",
            "cognitive_events", "session_links",
        ]
        for table in tables:
            await self.conn.execute(
                f"DELETE FROM {table} WHERE kernel_session_id = ?", (session_id,)
            )
        await self.conn.commit()
        return True

    async def clear_derived_state(self, session_id: str) -> None:
        """清空可由 event log 重建的派生状态。"""
        tables = [
            "progress_states",
            "plan_states",
            "intent_states",
            "task_snapshots",
            "global_tasks",
            "task_brief_states",
            "task_flows",
            "claim_items",
            "todo_obligations",
            "belief_items",
            "evidence_items",
            "execution_actions",
            "commitments",
            "sync_cursors",
            "runtime_refs",
        ]
        for table in tables:
            await self.conn.execute(
                f"DELETE FROM {table} WHERE kernel_session_id = ?", (session_id,)
            )
        await self.conn.commit()

    async def archive_session(self, session_id: str) -> bool:
        """归档会话——标记为 completed，不删除数据。"""
        session = await self.get_session(session_id)
        if not session:
            return False
        await self.conn.execute(
            "UPDATE session_links SET status = 'completed', updated_at = ? WHERE kernel_session_id = ?",
            (utc_now_iso(), session_id),
        )
        await self.conn.commit()
        return True

    async def save_sync_cursor(self, session_id: str, event_id: str,
                                state_version: int, external_task_id: str = "") -> None:
        """记录与外部系统的同步进度（§5.14）。"""
        await self.conn.execute(
            """INSERT OR REPLACE INTO sync_cursors
               (kernel_session_id, external_task_id, external_system,
                last_synced_event_id, last_synced_state_version, last_synced_at)
               VALUES (?, ?, 'multica', ?, ?, ?)""",
            (session_id, external_task_id, event_id, state_version, utc_now_iso()),
        )
        await self.conn.commit()

    async def save_runtime_ref(self, ref: RuntimeReference) -> None:
        """保存 Runtime Reference 索引条目（§5.13）。"""
        await self.conn.execute(
            """INSERT OR REPLACE INTO runtime_refs
               (kernel_ref_id, kernel_session_id, runtime_session_id,
                runtime_type, ref_type, ref_id, summary, visibility, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ref.kernel_ref_id, ref.kernel_session_id, ref.runtime_session_id,
             ref.runtime_type, ref.ref_type, ref.ref_id, ref.summary,
             ref.visibility, ref.created_at.isoformat() if ref.created_at else None),
        )
        await self.conn.commit()

    async def get_runtime_references(self, session_id: str) -> List[RuntimeReference]:
        """获取当前会话的 runtime 引用索引。"""
        rows = await self.conn.execute_fetchall(
            """SELECT * FROM runtime_refs
               WHERE kernel_session_id = ?
               ORDER BY created_at ASC""",
            (session_id,),
        )
        return [
            RuntimeReference(
                kernel_ref_id=r["kernel_ref_id"],
                kernel_session_id=r["kernel_session_id"],
                runtime_session_id=r["runtime_session_id"] or "",
                runtime_type=r["runtime_type"] or "cli-agent",
                ref_type=r["ref_type"] or "",
                ref_id=r["ref_id"] or "",
                summary=r["summary"] or "",
                visibility=r["visibility"] or "private",
                created_at=utc_from_iso(r["created_at"]) if r["created_at"] else utc_now(),
            )
            for r in rows
        ]

    async def update_session_status(
        self, session_id: str, status: str, **kwargs
    ) -> None:
        """更新会话状态——每次事件后同步 state_version。"""
        sets = ["status = ?", "updated_at = ?"]
        params: list = [status, utc_now_iso()]
        for key, val in kwargs.items():
            sets.append(f"{key} = ?")
            params.append(val)
        params.append(session_id)
        await self.conn.execute(
            f"UPDATE session_links SET {', '.join(sets)} WHERE kernel_session_id = ?",
            params,
        )
        await self.conn.commit()

    # ==================================================================
    # 事件日志 — append-only
    # ==================================================================

    async def append_event(self, event: CognitiveEvent) -> CognitiveEvent:
        """追加事件到日志。永远追加，从不更新。

        这是事件溯源架构的基石——所有状态变更都从此日志派生。
        """
        await self.conn.execute(
            """INSERT INTO cognitive_events
               (event_id, kernel_session_id, runtime_session_id, run_id, event_type,
                actor, source_component, payload, runtime_refs, visibility,
                intent_version, state_version, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.event_id,
                event.kernel_session_id,
                event.runtime_session_id if hasattr(event, "runtime_session_id") else "",
                event.run_id if hasattr(event, "run_id") else "",
                event.event_type.value,
                event.actor.value,
                event.source_component,
                json.dumps(event.payload, ensure_ascii=False, default=str),
                json.dumps(event.runtime_refs.model_dump() if event.runtime_refs else {}, default=str),
                event.visibility.value,
                event.intent_version,
                event.state_version,
                utc_now_iso(),
            ),
        )
        await self.conn.commit()
        return event

    async def get_events(
        self, session_id: str, after_event_id: str = "", limit: int = 100
    ) -> list:
        """获取会话的事件日志。支持游标分页（after_event_id）。"""
        query = "SELECT * FROM cognitive_events WHERE kernel_session_id = ?"
        params: list = [session_id]
        if after_event_id:
            query += " AND rowid > (SELECT rowid FROM cognitive_events WHERE event_id = ?)"
            params.append(after_event_id)
        query += " ORDER BY created_at ASC LIMIT ?"
        params.append(limit)
        rows = await self.conn.execute_fetchall(query, params)
        return [dict(r) for r in rows]

    async def get_latest_state_version(self, session_id: str) -> int:
        """获取会话的最新状态版本号。"""
        row = await self.conn.execute_fetchall(
            "SELECT MAX(state_version) as mv FROM cognitive_events WHERE kernel_session_id = ?",
            (session_id,),
        )
        return row[0]["mv"] if row and row[0]["mv"] else 0

    # ==================================================================
    # 派生状态 — 每个 Reducer 类别的 get/save 方法
    # ==================================================================

    # ── 意图 ──
    async def get_intent(self, session_id: str) -> Optional[IntentState]:
        """获取当前意图状态。"""
        row = await self.conn.execute_fetchall(
            "SELECT * FROM intent_states WHERE kernel_session_id = ?",
            (session_id,),
        )
        if not row:
            return None
        r = row[0]
        return IntentState(
            intent_version=r["intent_version"],
            goal=r["goal"] or "",
            constraints=json.loads(r["constraints"] or "[]"),
            output_format=r["output_format"] or "",
            priority=r["priority"] or "normal",
            cancelled=bool(r["cancelled"]),
            last_user_update_at=utc_from_iso(r["last_user_update_at"]),
        )

    async def save_intent(self, session_id: str, intent: IntentState):
        """保存意图状态——使用 REPLACE（upsert）。"""
        await self.conn.execute(
            """INSERT OR REPLACE INTO intent_states
               (kernel_session_id, intent_version, goal, constraints,
                output_format, priority, cancelled, last_user_update_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                intent.intent_version,
                intent.goal,
                json.dumps(intent.constraints, ensure_ascii=False),
                intent.output_format,
                intent.priority,
                int(intent.cancelled),
                intent.last_user_update_at.isoformat() if intent.last_user_update_at else None,
                utc_now_iso(),
            ),
        )
        await self.conn.commit()  # ← 必须 commit！常见遗漏坑
        session = await self.get_session(session_id)
        await self.save_task_brief(
            TaskBriefState(
                kernel_session_id=session_id,
                task_id=session.active_task_id if session else "",
                task_brief_version=intent.intent_version,
                goal=intent.goal,
                output_format=intent.output_format,
                constraints=intent.constraints,
                priority=intent.priority,
                cancelled=intent.cancelled,
            )
        )

    # ── 计划 ──
    async def get_plan(self, session_id: str) -> Optional[PlanState]:
        """获取当前计划状态。"""
        row = await self.conn.execute_fetchall(
            "SELECT * FROM plan_states WHERE kernel_session_id = ?",
            (session_id,),
        )
        if not row:
            return None
        r = row[0]
        from src.schema.state import PlanStep
        steps_data = json.loads(r["steps"] or "[]")
        steps = [PlanStep(**s) for s in steps_data]
        return PlanState(
            plan_id=r["plan_id"],
            status=PlanStatus(r["status"]),
            steps=steps,
            current_step=r["current_step"] or "",
            intent_version=r["intent_version"] or 0,
        )

    async def save_plan(self, session_id: str, plan: PlanState):
        """保存计划状态。"""
        await self.conn.execute(
            """INSERT OR REPLACE INTO plan_states
               (kernel_session_id, plan_id, status, current_step, steps,
                intent_version, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                plan.plan_id,
                plan.status.value,
                plan.current_step,
                json.dumps([s.model_dump() for s in plan.steps], ensure_ascii=False),
                plan.intent_version,
                utc_now_iso(),
            ),
        )
        await self.conn.commit()
        session = await self.get_session(session_id)
        await self.save_task_flow(
            TaskFlowState(
                kernel_session_id=session_id,
                flow_id=plan.plan_id,
                task_id=session.active_task_id if session else "",
                status=plan.status,
                current_step=plan.current_step,
                steps=[step.model_dump() for step in plan.steps],
                task_brief_version=plan.intent_version,
                execution_summary=await self._build_execution_summary(session_id),
            )
        )

    # ── 新版状态兼容层 ──
    async def get_task_brief(self, session_id: str) -> Optional[TaskBriefState]:
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM task_brief_states WHERE kernel_session_id = ?",
            (session_id,),
        )
        if not rows:
            return None
        row = rows[0]
        return TaskBriefState(
            kernel_session_id=row["kernel_session_id"],
            task_id=row["task_id"] or "",
            task_brief_version=row["task_brief_version"] or 0,
            goal=row["goal"] or "",
            output_format=row["output_format"] or "",
            constraints=json.loads(row["constraints"] or "[]"),
            priority=row["priority"] or "normal",
            cancelled=bool(row["cancelled"]),
            updated_at=utc_from_iso(row["updated_at"]) if row["updated_at"] else utc_now(),
        )

    async def save_task_brief(self, state: TaskBriefState) -> None:
        await self.conn.execute(
            """INSERT OR REPLACE INTO task_brief_states
               (kernel_session_id, task_id, task_brief_version, goal,
                output_format, constraints, priority, cancelled, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                state.kernel_session_id,
                state.task_id,
                state.task_brief_version,
                state.goal,
                state.output_format,
                json.dumps(state.constraints, ensure_ascii=False),
                state.priority,
                int(state.cancelled),
                utc_now_iso(),
            ),
        )
        await self.conn.commit()

    async def get_task_flow(self, session_id: str) -> Optional[TaskFlowState]:
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM task_flows WHERE kernel_session_id = ?",
            (session_id,),
        )
        if not rows:
            return None
        row = rows[0]
        return TaskFlowState(
            kernel_session_id=row["kernel_session_id"],
            flow_id=row["flow_id"] or "",
            task_id=row["task_id"] or "",
            status=PlanStatus(row["status"] or PlanStatus.ACTIVE.value),
            current_step=row["current_step"] or "",
            steps=json.loads(row["steps"] or "[]"),
            task_brief_version=row["task_brief_version"] or 0,
            execution_summary=json.loads(row["execution_summary"] or "[]"),
            updated_at=utc_from_iso(row["updated_at"]) if row["updated_at"] else utc_now(),
        )

    async def save_task_flow(self, state: TaskFlowState) -> None:
        await self.conn.execute(
            """INSERT OR REPLACE INTO task_flows
               (kernel_session_id, flow_id, task_id, status, current_step,
                steps, task_brief_version, execution_summary, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                state.kernel_session_id,
                state.flow_id,
                state.task_id,
                state.status.value,
                state.current_step,
                json.dumps(state.steps, ensure_ascii=False, default=str),
                state.task_brief_version,
                json.dumps(state.execution_summary, ensure_ascii=False, default=str),
                utc_now_iso(),
            ),
        )
        await self.conn.commit()

    async def _build_execution_summary(self, session_id: str) -> List[Dict[str, Any]]:
        actions = await self.get_executions(session_id)
        return [
            {
                "action_id": action.action_id,
                "task_id": action.task_id,
                "step_id": action.step_id,
                "tool": action.tool,
                "status": action.status,
                "input_summary": action.input_summary,
                "ended_at": action.ended_at.isoformat() if action.ended_at else None,
            }
            for action in actions[-20:]
        ]

    async def _attach_alias_state_to_task(self, task: TaskSnapshot) -> None:
        await self.conn.execute(
            """UPDATE task_brief_states
               SET task_id = ?, updated_at = ?
               WHERE kernel_session_id = ? AND COALESCE(task_id, '') = ''""",
            (task.task_id, utc_now_iso(), task.kernel_session_id),
        )
        await self.conn.execute(
            """UPDATE task_flows
               SET task_id = ?, updated_at = ?
               WHERE kernel_session_id = ? AND COALESCE(task_id, '') = ''""",
            (task.task_id, utc_now_iso(), task.kernel_session_id),
        )
        await self.conn.commit()

    # ── 任务 ──
    async def create_task(
        self,
        session_id: str,
        *,
        title: str = "",
        goal: str = "",
        constraints: Optional[List[str]] = None,
        status: TaskStatus = TaskStatus.ACTIVE,
        plan_id: str = "",
        current_step: str = "",
        current_step_name: str = "",
        steps: Optional[List[Dict[str, Any]]] = None,
        last_run_id: str = "",
        last_interrupted_run_id: str = "",
        resume_summary: str = "",
    ) -> TaskSnapshot:
        task = TaskSnapshot(
            task_id=f"task_{uuid.uuid4().hex[:12]}",
            kernel_session_id=session_id,
            title=title,
            goal=goal,
            constraints=list(constraints or []),
            status=status,
            plan_id=plan_id,
            current_step=current_step,
            current_step_name=current_step_name,
            steps=list(steps or []),
            last_run_id=last_run_id,
            last_interrupted_run_id=last_interrupted_run_id,
            resume_summary=resume_summary,
        )
        await self.save_task(task)
        return task

    async def get_task(self, session_id: str, task_id: str) -> Optional[TaskSnapshot]:
        if not task_id:
            return None
        rows = await self.conn.execute_fetchall(
            """SELECT * FROM task_snapshots
               WHERE kernel_session_id = ? AND task_id = ?""",
            (session_id, task_id),
        )
        if not rows:
            return None
        return self._row_to_task(rows[0])

    async def list_tasks(self, session_id: str, limit: int = 20) -> List[TaskSnapshot]:
        rows = await self.conn.execute_fetchall(
            """SELECT * FROM task_snapshots
               WHERE kernel_session_id = ?
               ORDER BY updated_at DESC, created_at DESC
               LIMIT ?""",
            (session_id, limit),
        )
        return [self._row_to_task(row) for row in rows]

    async def get_latest_paused_task(self, session_id: str) -> Optional[TaskSnapshot]:
        rows = await self.conn.execute_fetchall(
            """SELECT * FROM task_snapshots
               WHERE kernel_session_id = ? AND status = ?
               ORDER BY updated_at DESC, created_at DESC
               LIMIT 1""",
            (session_id, TaskStatus.PAUSED.value),
        )
        if not rows:
            return None
        return self._row_to_task(rows[0])

    async def save_task(self, task: TaskSnapshot) -> None:
        existing = await self.get_task(task.kernel_session_id, task.task_id)
        created_at = existing.created_at if existing else task.created_at
        await self.conn.execute(
            """INSERT OR REPLACE INTO task_snapshots
               (task_id, kernel_session_id, title, goal, constraints, status,
                plan_id, current_step, current_step_name, steps,
                last_run_id, last_interrupted_run_id, resume_summary,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.task_id,
                task.kernel_session_id,
                task.title,
                task.goal,
                json.dumps(task.constraints, ensure_ascii=False),
                task.status.value,
                task.plan_id,
                task.current_step,
                task.current_step_name,
                json.dumps(task.steps, ensure_ascii=False, default=str),
                task.last_run_id,
                task.last_interrupted_run_id,
                task.resume_summary,
                created_at.isoformat() if created_at else utc_now_iso(),
                utc_now_iso(),
            ),
        )
        await self.conn.commit()
        await self._attach_alias_state_to_task(task)

    def _build_routing_hints(self, text: str) -> List[str]:
        parts = [
            item.strip(" ，。,.：:；;（）()[]【】")
            for item in (text or "").replace("\n", " ").split()
        ]
        hints = [item for item in parts if len(item) >= 2]
        compact = (text or "").strip()
        if compact and compact not in hints:
            hints.insert(0, compact[:80])
        return hints[:12]

    async def upsert_global_task_from_snapshot(
        self,
        task: TaskSnapshot,
        *,
        user_session_id: str = "",
        agent_id: str = "",
        task_brief_version: int = 0,
        external_refs: Optional[Dict[str, Any]] = None,
    ) -> GlobalTask:
        session = await self.get_session(task.kernel_session_id)
        existing = await self.get_global_task(task.task_id)
        now = utc_now_iso()
        created_at = existing.created_at.isoformat() if existing else task.created_at.isoformat()
        resolved_agent_id = agent_id or (session.agent_id if session else "")
        refs = external_refs or {}
        if session:
            refs = {
                "external_source": session.external_source,
                "external_workspace_id": session.external_workspace_id,
                "external_issue_id": session.external_issue_id,
                "external_task_id": session.external_task_id,
                **refs,
            }
        title = task.title or task.goal or task.task_id
        description = task.title or task.goal
        await self.conn.execute(
            """INSERT OR REPLACE INTO global_tasks
               (task_id, kernel_session_id, user_session_id, agent_id, title,
                task_type, task_description, task_brief_version, status,
                priority, stage, external_refs, routing_hints, created_at,
                updated_at, last_user_touch_at, last_activity_at,
                last_manager_update_at, last_talker_update_at, last_thinker_update_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.task_id,
                task.kernel_session_id,
                user_session_id or (existing.user_session_id if existing else ""),
                resolved_agent_id,
                title,
                existing.task_type if existing else "other",
                description,
                task_brief_version,
                task.status.value,
                existing.priority if existing else "normal",
                task.current_step_name or task.current_step,
                json.dumps(refs, ensure_ascii=False),
                json.dumps(self._build_routing_hints(f"{title} {description}"), ensure_ascii=False),
                created_at,
                now,
                now,
                now,
                now,
                now,
                existing.last_thinker_update_at.isoformat()
                if existing and existing.last_thinker_update_at
                else None,
            ),
        )
        await self.conn.commit()
        global_task = await self.get_global_task(task.task_id)
        return global_task

    async def get_global_task(self, task_id: str) -> Optional[GlobalTask]:
        if not task_id:
            return None
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM global_tasks WHERE task_id = ?",
            (task_id,),
        )
        if not rows:
            return None
        return self._row_to_global_task(rows[0])

    async def list_global_tasks(
        self,
        *,
        user_session_id: str = "",
        agent_id: str = "",
        limit: int = 20,
    ) -> List[GlobalTask]:
        query = "SELECT * FROM global_tasks"
        params: list = []
        clauses: list[str] = []
        if user_session_id:
            clauses.append("user_session_id = ?")
            params.append(user_session_id)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY last_user_touch_at DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        rows = await self.conn.execute_fetchall(query, params)
        return [self._row_to_global_task(row) for row in rows]

    async def save_task_route_decision(self, route: TaskRouteDecision) -> TaskRouteDecision:
        route_id = route.route_id or f"route_{uuid.uuid4().hex[:12]}"
        created_at = route.created_at.isoformat() if route.created_at else utc_now_iso()
        await self.conn.execute(
            """INSERT OR REPLACE INTO task_context_routes
               (route_id, user_session_id, runtime_session_id, user_message,
                routing_decision, target_task_id, confidence, matched_hints,
                time_reason, candidate_tasks, needs_user_clarification,
                clarification_question, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                route_id,
                route.user_session_id,
                route.runtime_session_id,
                route.user_message,
                route.routing_decision,
                route.target_task_id,
                route.confidence,
                json.dumps(route.matched_hints, ensure_ascii=False),
                json.dumps(route.time_reason, ensure_ascii=False, default=str),
                json.dumps(route.candidate_tasks, ensure_ascii=False, default=str),
                int(route.needs_user_clarification),
                route.clarification_question,
                created_at,
            ),
        )
        await self.conn.commit()
        route.route_id = route_id
        return route

    async def create_thinker_dispatch(
        self,
        *,
        kernel_session_id: str,
        task_id: str = "",
        run_id: str = "",
        task_brief_version: int = 0,
        dispatch_type: str = "start",
        cancellation_token: bool = False,
        payload: Optional[Dict[str, Any]] = None,
    ) -> ThinkerDispatch:
        dispatch = ThinkerDispatch(
            dispatch_id=f"td_{uuid.uuid4().hex[:12]}",
            kernel_session_id=kernel_session_id,
            task_id=task_id,
            run_id=run_id,
            task_brief_version=task_brief_version,
            dispatch_type=dispatch_type,
            cancellation_token=cancellation_token,
            payload=payload or {},
        )
        await self.save_thinker_dispatch(dispatch)
        return dispatch

    async def save_thinker_dispatch(self, dispatch: ThinkerDispatch) -> None:
        created_at = dispatch.created_at.isoformat() if dispatch.created_at else utc_now_iso()
        updated_at = utc_now_iso()
        await self.conn.execute(
            """INSERT OR REPLACE INTO thinker_dispatches
               (dispatch_id, kernel_session_id, task_id, run_id,
                task_brief_version, dispatch_type, status, cancellation_token,
                payload, claimed_by, claimed_at, heartbeat_at, completed_at,
                failed_at, error, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                dispatch.dispatch_id,
                dispatch.kernel_session_id,
                dispatch.task_id,
                dispatch.run_id,
                dispatch.task_brief_version,
                dispatch.dispatch_type,
                dispatch.status.value,
                int(dispatch.cancellation_token),
                json.dumps(dispatch.payload, ensure_ascii=False, default=str),
                dispatch.claimed_by,
                dispatch.claimed_at.isoformat() if dispatch.claimed_at else None,
                dispatch.heartbeat_at.isoformat() if dispatch.heartbeat_at else None,
                dispatch.completed_at.isoformat() if dispatch.completed_at else None,
                dispatch.failed_at.isoformat() if dispatch.failed_at else None,
                dispatch.error,
                created_at,
                updated_at,
            ),
        )
        await self.conn.commit()

    async def get_thinker_dispatch(self, dispatch_id: str) -> Optional[ThinkerDispatch]:
        if not dispatch_id:
            return None
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM thinker_dispatches WHERE dispatch_id = ?",
            (dispatch_id,),
        )
        if not rows:
            return None
        return self._row_to_thinker_dispatch(rows[0])

    async def list_thinker_dispatches(
        self,
        *,
        kernel_session_id: str = "",
        task_id: str = "",
        status: str = "",
        limit: int = 50,
    ) -> List[ThinkerDispatch]:
        query = "SELECT * FROM thinker_dispatches"
        clauses: list[str] = []
        params: list[Any] = []
        if kernel_session_id:
            clauses.append("kernel_session_id = ?")
            params.append(kernel_session_id)
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at ASC LIMIT ?"
        params.append(limit)
        rows = await self.conn.execute_fetchall(query, params)
        return [self._row_to_thinker_dispatch(row) for row in rows]

    async def claim_thinker_dispatch(
        self,
        *,
        dispatch_id: str = "",
        thinker_id: str = "",
        kernel_session_id: str = "",
        task_id: str = "",
    ) -> Optional[ThinkerDispatch]:
        query = "SELECT * FROM thinker_dispatches WHERE status = ?"
        params: list[Any] = [ThinkerDispatchStatus.PENDING.value]
        if dispatch_id:
            query += " AND dispatch_id = ?"
            params.append(dispatch_id)
        if kernel_session_id:
            query += " AND kernel_session_id = ?"
            params.append(kernel_session_id)
        if task_id:
            query += " AND task_id = ?"
            params.append(task_id)
        query += " ORDER BY created_at ASC LIMIT 1"
        rows = await self.conn.execute_fetchall(query, params)
        if not rows:
            return None
        now = utc_now()
        dispatch = self._row_to_thinker_dispatch(rows[0])
        dispatch.status = ThinkerDispatchStatus.CLAIMED
        dispatch.claimed_by = thinker_id
        dispatch.claimed_at = now
        dispatch.heartbeat_at = now
        await self.save_thinker_dispatch(dispatch)
        return await self.get_thinker_dispatch(dispatch.dispatch_id)

    async def heartbeat_thinker_dispatch(self, dispatch_id: str) -> Optional[ThinkerDispatch]:
        dispatch = await self.get_thinker_dispatch(dispatch_id)
        if not dispatch:
            return None
        dispatch.heartbeat_at = utc_now()
        await self.save_thinker_dispatch(dispatch)
        return await self.get_thinker_dispatch(dispatch_id)

    async def complete_thinker_dispatch(self, dispatch_id: str) -> Optional[ThinkerDispatch]:
        dispatch = await self.get_thinker_dispatch(dispatch_id)
        if not dispatch:
            return None
        dispatch.status = ThinkerDispatchStatus.COMPLETED
        dispatch.completed_at = utc_now()
        await self.save_thinker_dispatch(dispatch)
        return await self.get_thinker_dispatch(dispatch_id)

    async def fail_thinker_dispatch(self, dispatch_id: str, error: str = "") -> Optional[ThinkerDispatch]:
        dispatch = await self.get_thinker_dispatch(dispatch_id)
        if not dispatch:
            return None
        dispatch.status = ThinkerDispatchStatus.FAILED
        dispatch.failed_at = utc_now()
        dispatch.error = error
        await self.save_thinker_dispatch(dispatch)
        return await self.get_thinker_dispatch(dispatch_id)

    async def create_observer_notification(
        self,
        *,
        target: str = "observer",
        kernel_session_id: str = "",
        task_id: str = "",
        notification_type: str = "progress_update",
        urgency: str = "normal",
        reason: str = "",
        progress_ref: str = "",
        suggested_observer_context: Optional[Dict[str, Any]] = None,
        delivery_policy: Optional[Dict[str, Any]] = None,
    ) -> ObserverNotification:
        notification = ObserverNotification(
            notification_id=f"ntf_{uuid.uuid4().hex[:12]}",
            target=target,
            kernel_session_id=kernel_session_id,
            task_id=task_id,
            notification_type=notification_type,
            urgency=urgency,
            reason=reason,
            progress_ref=progress_ref,
            suggested_observer_context=suggested_observer_context or {},
            delivery_policy=delivery_policy or {},
        )
        await self.save_observer_notification(notification)
        return notification

    async def save_observer_notification(self, notification: ObserverNotification) -> None:
        existing = await self.get_observer_notification(notification.notification_id)
        created_at = existing.created_at if existing else notification.created_at
        await self.conn.execute(
            """INSERT OR REPLACE INTO observer_notifications
               (notification_id, target, kernel_session_id, task_id,
                notification_type, urgency, reason, progress_ref,
                suggested_observer_context, delivery_policy, status,
                acknowledged_at, resolved_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                notification.notification_id,
                notification.target,
                notification.kernel_session_id,
                notification.task_id,
                notification.notification_type,
                notification.urgency,
                notification.reason,
                notification.progress_ref,
                json.dumps(notification.suggested_observer_context, ensure_ascii=False, default=str),
                json.dumps(notification.delivery_policy, ensure_ascii=False, default=str),
                notification.status.value,
                notification.acknowledged_at.isoformat() if notification.acknowledged_at else None,
                notification.resolved_at.isoformat() if notification.resolved_at else None,
                created_at.isoformat() if created_at else utc_now_iso(),
                utc_now_iso(),
            ),
        )
        await self.conn.commit()

    async def get_observer_notification(
        self,
        notification_id: str,
    ) -> Optional[ObserverNotification]:
        if not notification_id:
            return None
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM observer_notifications WHERE notification_id = ?",
            (notification_id,),
        )
        if not rows:
            return None
        return self._row_to_observer_notification(rows[0])

    async def list_observer_notifications(
        self,
        *,
        target: str = "",
        kernel_session_id: str = "",
        task_id: str = "",
        status: str = "",
        limit: int = 50,
    ) -> List[ObserverNotification]:
        query = "SELECT * FROM observer_notifications"
        clauses: list[str] = []
        params: list[Any] = []
        if target:
            clauses.append("target = ?")
            params.append(target)
        if kernel_session_id:
            clauses.append("kernel_session_id = ?")
            params.append(kernel_session_id)
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at ASC LIMIT ?"
        params.append(limit)
        rows = await self.conn.execute_fetchall(query, params)
        return [self._row_to_observer_notification(row) for row in rows]

    async def ack_observer_notification(
        self,
        notification_id: str,
    ) -> Optional[ObserverNotification]:
        notification = await self.get_observer_notification(notification_id)
        if not notification:
            return None
        notification.status = ObserverNotificationStatus.ACKNOWLEDGED
        notification.acknowledged_at = utc_now()
        await self.save_observer_notification(notification)
        return await self.get_observer_notification(notification_id)

    async def resolve_observer_notification(
        self,
        notification_id: str,
    ) -> Optional[ObserverNotification]:
        notification = await self.get_observer_notification(notification_id)
        if not notification:
            return None
        notification.status = ObserverNotificationStatus.RESOLVED
        notification.resolved_at = utc_now()
        await self.save_observer_notification(notification)
        return await self.get_observer_notification(notification_id)

    async def get_evidence(self, session_id: str) -> List[EvidenceItem]:
        """获取所有证据条目。"""
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM evidence_items WHERE kernel_session_id = ?",
            (session_id,),
        )
        return [
            EvidenceItem(
                evidence_id=r["evidence_id"],
                task_id=r["task_id"] or "",
                evidence_type=EvidenceType(r["evidence_type"]),
                source=r["source"] or "",
                title=r["title"] or "",
                observed_at=utc_from_iso(r["observed_at"]),
                source_date=r["source_date"] or None,
                reliability=Reliability(r["reliability"]),
                extracted_facts=json.loads(r["extracted_facts"] or "[]"),
                raw_ref=r["raw_ref"],
                accepted_by=r["accepted_by"] or "kernel_manager",
            )
            for r in rows
        ]

    async def save_evidence(self, session_id: str, evidence: EvidenceItem):
        """保存单条证据——使用 REPLACE（upsert）。"""
        if not evidence.task_id:
            session = await self.get_session(session_id)
            evidence.task_id = session.active_task_id if session else ""
        await self.conn.execute(
            """INSERT OR REPLACE INTO evidence_items
               (evidence_id, kernel_session_id, task_id, evidence_type, source, title,
                observed_at, source_date, reliability, extracted_facts,
                raw_ref, accepted_by, accepted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                evidence.evidence_id,
                session_id,
                evidence.task_id,
                evidence.evidence_type.value,
                evidence.source,
                evidence.title,
                evidence.observed_at.isoformat() if evidence.observed_at else None,
                evidence.source_date,
                evidence.reliability.value,
                json.dumps(evidence.extracted_facts, ensure_ascii=False),
                evidence.raw_ref,
                evidence.accepted_by,
                utc_now_iso(),
            ),
        )
        await self.conn.commit()

    # ── 信念 ──
    async def get_claim_items(self, session_id: str) -> List[ClaimItem]:
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM claim_items WHERE kernel_session_id = ?",
            (session_id,),
        )
        return [
            ClaimItem(
                claim_id=r["claim_id"],
                kernel_session_id=r["kernel_session_id"],
                task_id=r["task_id"] or "",
                claim=r["claim"] or "",
                status=BeliefStatus(r["status"] or BeliefStatus.UNVERIFIED.value),
                confidence=r["confidence"] or 0.0,
                supporting_evidence=json.loads(r["supporting_evidence"] or "[]"),
                conflicting_evidence=json.loads(r["conflicting_evidence"] or "[]"),
                visibility=r["visibility"] or "shared",
                last_verified_at=utc_from_iso(r["last_verified_at"]),
                updated_at=utc_from_iso(r["updated_at"]) if r["updated_at"] else utc_now(),
            )
            for r in rows
        ]

    async def get_beliefs(self, session_id: str) -> List[BeliefItem]:
        """获取所有信念条目。"""
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM belief_items WHERE kernel_session_id = ?",
            (session_id,),
        )
        return [
            BeliefItem(
                belief_id=r["belief_id"],
                claim=r["claim"] or "",
                status=BeliefStatus(r["status"]),
                confidence=r["confidence"] or 0.0,
                supporting_evidence=json.loads(r["supporting_evidence"] or "[]"),
                conflicting_evidence=json.loads(r["conflicting_evidence"] or "[]"),
                visibility=r["visibility"] or "shared",
                last_verified_at=utc_from_iso(r["last_verified_at"]),
            )
            for r in rows
        ]

    async def save_belief(self, session_id: str, belief: BeliefItem):
        """保存单条信念——使用 REPLACE（upsert）。"""
        await self.conn.execute(
            """INSERT OR REPLACE INTO belief_items
               (belief_id, kernel_session_id, claim, status, confidence,
                supporting_evidence, conflicting_evidence, visibility,
                last_verified_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                belief.belief_id,
                session_id,
                belief.claim,
                belief.status.value,
                belief.confidence,
                json.dumps(belief.supporting_evidence, ensure_ascii=False),
                json.dumps(belief.conflicting_evidence, ensure_ascii=False),
                belief.visibility,
                belief.last_verified_at.isoformat() if belief.last_verified_at else None,
                utc_now_iso(),
            ),
        )
        await self.conn.commit()
        session = await self.get_session(session_id)
        await self.save_claim_item(
            ClaimItem(
                claim_id=belief.belief_id,
                kernel_session_id=session_id,
                task_id=session.active_task_id if session else "",
                claim=belief.claim,
                status=belief.status,
                confidence=belief.confidence,
                supporting_evidence=belief.supporting_evidence,
                conflicting_evidence=belief.conflicting_evidence,
                visibility=belief.visibility,
                last_verified_at=belief.last_verified_at,
            )
        )

    async def save_claim_item(self, claim: ClaimItem) -> None:
        await self.conn.execute(
            """INSERT OR REPLACE INTO claim_items
               (claim_id, kernel_session_id, task_id, claim, status, confidence,
                supporting_evidence, conflicting_evidence, visibility,
                last_verified_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                claim.claim_id,
                claim.kernel_session_id,
                claim.task_id,
                claim.claim,
                claim.status.value,
                claim.confidence,
                json.dumps(claim.supporting_evidence, ensure_ascii=False),
                json.dumps(claim.conflicting_evidence, ensure_ascii=False),
                claim.visibility,
                claim.last_verified_at.isoformat() if claim.last_verified_at else None,
                utc_now_iso(),
                utc_now_iso(),
            ),
        )
        await self.conn.commit()

    # ── 执行 ──
    async def get_executions(self, session_id: str) -> List[ExecutionAction]:
        """获取所有执行动作。"""
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM execution_actions WHERE kernel_session_id = ?",
            (session_id,),
        )
        return [
            ExecutionAction(
                action_id=r["action_id"],
                task_id=r["task_id"] or "",
                step_id=r["step_id"] or "",
                tool=r["tool"] or "",
                status=r["status"],
                input_summary=r["input_summary"] or "",
                output_ref=r["output_ref"],
                runtime_refs=json.loads(r["runtime_refs"] or "{}"),
                started_at=utc_from_iso(r["started_at"]) or utc_now(),
                ended_at=utc_from_iso(r["ended_at"]),
                retry_count=r["retry_count"] or 0,
            )
            for r in rows
        ]

    async def save_execution(self, session_id: str, action: ExecutionAction):
        """保存单条执行动作——使用 REPLACE（upsert）。"""
        if not action.task_id:
            session = await self.get_session(session_id)
            action.task_id = session.active_task_id if session else ""
        await self.conn.execute(
            """INSERT OR REPLACE INTO execution_actions
               (action_id, kernel_session_id, task_id, step_id, tool, status,
                input_summary, output_ref, runtime_refs, retry_count,
                started_at, ended_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                action.action_id,
                session_id,
                action.task_id,
                action.step_id,
                action.tool,
                action.status,
                action.input_summary,
                action.output_ref,
                json.dumps(action.runtime_refs, default=str),
                action.retry_count,
                action.started_at.isoformat() if action.started_at else utc_now_iso(),
                action.ended_at.isoformat() if action.ended_at else None,
            ),
        )
        await self.conn.commit()
        flow = await self.get_task_flow(session_id)
        if flow:
            flow.execution_summary = await self._build_execution_summary(session_id)
            await self.save_task_flow(flow)

    # ── 承诺 ──
    async def get_todo_obligations(self, session_id: str) -> List[TodoObligation]:
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM todo_obligations WHERE kernel_session_id = ?",
            (session_id,),
        )
        return [
            TodoObligation(
                obligation_id=r["obligation_id"],
                kernel_session_id=r["kernel_session_id"],
                task_id=r["task_id"] or "",
                statement=r["statement"] or "",
                created_by=r["created_by"] or "talker",
                status=CommitmentStatus(r["status"] or CommitmentStatus.PENDING.value),
                requires_confirmation=bool(r["requires_confirmation"]),
                related_task_brief_version=r["related_task_brief_version"] or 0,
                resolved_at=utc_from_iso(r["resolved_at"]),
                updated_at=utc_from_iso(r["updated_at"]) if r["updated_at"] else utc_now(),
            )
            for r in rows
        ]

    async def get_commitments(self, session_id: str) -> List[Commitment]:
        """获取所有 Talker 承诺。"""
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM commitments WHERE kernel_session_id = ?",
            (session_id,),
        )
        return [
            Commitment(
                commitment_id=r["commitment_id"],
                statement=r["statement"] or "",
                created_by=r["created_by"] or "talker",
                status=CommitmentStatus(r["status"]),
                requires_confirmation=bool(r["requires_confirmation"]),
                related_intent_version=r["related_intent_version"] or 0,
                resolved_at=utc_from_iso(r["resolved_at"]),
            )
            for r in rows
        ]

    async def save_commitment(self, session_id: str, commitment: Commitment):
        """保存单条承诺——使用 REPLACE（upsert）。"""
        await self.conn.execute(
            """INSERT OR REPLACE INTO commitments
               (commitment_id, kernel_session_id, statement, created_by,
                status, requires_confirmation, related_intent_version,
                resolved_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                commitment.commitment_id,
                session_id,
                commitment.statement,
                commitment.created_by,
                commitment.status.value,
                int(commitment.requires_confirmation),
                commitment.related_intent_version,
                commitment.resolved_at.isoformat() if commitment.resolved_at else None,
                utc_now_iso(),
            ),
        )
        await self.conn.commit()
        session = await self.get_session(session_id)
        await self.save_todo_obligation(
            TodoObligation(
                obligation_id=commitment.commitment_id,
                kernel_session_id=session_id,
                task_id=session.active_task_id if session else "",
                statement=commitment.statement,
                created_by=commitment.created_by,
                status=commitment.status,
                requires_confirmation=commitment.requires_confirmation,
                related_task_brief_version=commitment.related_intent_version,
                resolved_at=commitment.resolved_at,
            )
        )

    async def save_todo_obligation(self, obligation: TodoObligation) -> None:
        await self.conn.execute(
            """INSERT OR REPLACE INTO todo_obligations
               (obligation_id, kernel_session_id, task_id, statement, created_by,
                status, requires_confirmation, related_task_brief_version,
                resolved_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                obligation.obligation_id,
                obligation.kernel_session_id,
                obligation.task_id,
                obligation.statement,
                obligation.created_by,
                obligation.status.value,
                int(obligation.requires_confirmation),
                obligation.related_task_brief_version,
                obligation.resolved_at.isoformat() if obligation.resolved_at else None,
                utc_now_iso(),
                utc_now_iso(),
            ),
        )
        await self.conn.commit()

    # ── 进度 ──
    async def get_progress(self, session_id: str) -> Optional[ProgressState]:
        """获取进度合成状态。"""
        row = await self.conn.execute_fetchall(
            "SELECT * FROM progress_states WHERE kernel_session_id = ?",
            (session_id,),
        )
        if not row:
            return None
        r = row[0]
        return ProgressState(
            session_id=session_id,
            status=r["status"],
            stage=r["stage"] or "",
            summary=r["summary"] or "",
            safe_facts=json.loads(r["safe_facts"] or "[]"),
            unsafe_claims=json.loads(r["unsafe_claims"] or "[]"),
            needs_user_input=bool(r["needs_user_input"]),
            allowed_actions=json.loads(r["allowed_actions"] or '["report_progress"]'),
            forbidden_actions=json.loads(r["forbidden_actions"] or "[]"),
        )

    async def save_progress(self, session_id: str, progress: ProgressState):
        """保存进度合成状态。"""
        await self.conn.execute(
            """INSERT OR REPLACE INTO progress_states
               (kernel_session_id, status, stage, summary, safe_facts,
                unsafe_claims, needs_user_input, allowed_actions,
                forbidden_actions, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                progress.status,
                progress.stage,
                progress.summary,
                json.dumps(progress.safe_facts, ensure_ascii=False),
                json.dumps(progress.unsafe_claims, ensure_ascii=False),
                int(progress.needs_user_input),
                json.dumps(progress.allowed_actions, ensure_ascii=False),
                json.dumps(progress.forbidden_actions, ensure_ascii=False),
                utc_now_iso(),
            ),
        )
        await self.conn.commit()

    # ==================================================================
    # 工具方法
    # ==================================================================

    def _row_to_session(self, row) -> SessionLink:
        """将数据库行映射为 SessionLink 对象。"""
        return SessionLink(
            kernel_session_id=row["kernel_session_id"],
            runtime_id=row["runtime_id"] or "",
            runtime_session_id=row["runtime_session_id"] or "",
            runtime_type=row["runtime_type"] or "cli-agent",
            agent_id=row["agent_id"] or "",
            external_source=row["external_source"] or "",
            external_workspace_id=row["external_workspace_id"] or "",
            external_issue_id=row["external_issue_id"] or "",
            external_task_id=row["external_task_id"] or "",
            status=row["status"],
            intent_version=row["intent_version"] or 0,
            state_version=row["state_version"] or 1,
            active_run_id=row["active_run_id"] or "",
            active_task_id=row["active_task_id"] or "",
            cancellation_token=bool(row["cancellation_token"]) if "cancellation_token" in row.keys() else False,
            last_paused_task_id=row["last_paused_task_id"] or "",
            last_interrupted_run_id=row["last_interrupted_run_id"] or "",
            last_interrupting_run_id=row["last_interrupting_run_id"] or "",
            last_interrupt_reason=row["last_interrupt_reason"] or "",
            last_interrupt_at=utc_from_iso(row["last_interrupt_at"]) if row["last_interrupt_at"] else None,
            created_at=utc_from_iso(row["created_at"]) if row["created_at"] else utc_now(),
            updated_at=utc_from_iso(row["updated_at"]) if row["updated_at"] else utc_now(),
        )

    def _row_to_task(self, row) -> TaskSnapshot:
        return TaskSnapshot(
            task_id=row["task_id"],
            kernel_session_id=row["kernel_session_id"],
            title=row["title"] or "",
            goal=row["goal"] or "",
            constraints=json.loads(row["constraints"] or "[]"),
            status=TaskStatus(row["status"] or TaskStatus.ACTIVE.value),
            plan_id=row["plan_id"] or "",
            current_step=row["current_step"] or "",
            current_step_name=row["current_step_name"] or "",
            steps=json.loads(row["steps"] or "[]"),
            last_run_id=row["last_run_id"] or "",
            last_interrupted_run_id=row["last_interrupted_run_id"] or "",
            resume_summary=row["resume_summary"] or "",
            created_at=utc_from_iso(row["created_at"]) if row["created_at"] else utc_now(),
            updated_at=utc_from_iso(row["updated_at"]) if row["updated_at"] else utc_now(),
        )

    def _row_to_user_session(self, row) -> UserSession:
        return UserSession(
            user_session_id=row["user_session_id"],
            runtime_session_id=row["runtime_session_id"] or "",
            runtime_id=row["runtime_id"] or "",
            runtime_type=row["runtime_type"] or "cli-agent",
            agent_id=row["agent_id"] or "",
            session_kind=row["session_kind"] or "user_chat",
            created_by=row["created_by"] or "runtime",
            linked_task_ids=json.loads(row["linked_task_ids"] or "[]"),
            active_task_id=row["active_task_id"] or "",
            created_at=utc_from_iso(row["created_at"]) if row["created_at"] else utc_now(),
            updated_at=utc_from_iso(row["updated_at"]) if row["updated_at"] else utc_now(),
        )

    def _row_to_global_task(self, row) -> GlobalTask:
        return GlobalTask(
            task_id=row["task_id"],
            kernel_session_id=row["kernel_session_id"],
            user_session_id=row["user_session_id"] or "",
            agent_id=row["agent_id"] or "",
            title=row["title"] or "",
            task_type=row["task_type"] or "other",
            task_description=row["task_description"] or "",
            task_brief_version=row["task_brief_version"] or 0,
            status=TaskStatus(row["status"] or TaskStatus.ACTIVE.value),
            priority=row["priority"] or "normal",
            stage=row["stage"] or "",
            external_refs=json.loads(row["external_refs"] or "{}"),
            routing_hints=json.loads(row["routing_hints"] or "[]"),
            created_at=utc_from_iso(row["created_at"]) if row["created_at"] else utc_now(),
            updated_at=utc_from_iso(row["updated_at"]) if row["updated_at"] else utc_now(),
            last_user_touch_at=utc_from_iso(row["last_user_touch_at"]) if row["last_user_touch_at"] else None,
            last_activity_at=utc_from_iso(row["last_activity_at"]) if row["last_activity_at"] else None,
            last_manager_update_at=utc_from_iso(row["last_manager_update_at"]) if row["last_manager_update_at"] else None,
            last_talker_update_at=utc_from_iso(row["last_talker_update_at"]) if row["last_talker_update_at"] else None,
            last_thinker_update_at=utc_from_iso(row["last_thinker_update_at"]) if row["last_thinker_update_at"] else None,
        )

    def _row_to_thinker_dispatch(self, row) -> ThinkerDispatch:
        return ThinkerDispatch(
            dispatch_id=row["dispatch_id"],
            kernel_session_id=row["kernel_session_id"],
            task_id=row["task_id"] or "",
            run_id=row["run_id"] or "",
            task_brief_version=row["task_brief_version"] or 0,
            dispatch_type=row["dispatch_type"] or "start",
            status=ThinkerDispatchStatus(row["status"] or ThinkerDispatchStatus.PENDING.value),
            cancellation_token=bool(row["cancellation_token"]),
            payload=json.loads(row["payload"] or "{}"),
            claimed_by=row["claimed_by"] or "",
            claimed_at=utc_from_iso(row["claimed_at"]) if row["claimed_at"] else None,
            heartbeat_at=utc_from_iso(row["heartbeat_at"]) if row["heartbeat_at"] else None,
            completed_at=utc_from_iso(row["completed_at"]) if row["completed_at"] else None,
            failed_at=utc_from_iso(row["failed_at"]) if row["failed_at"] else None,
            error=row["error"] or "",
            created_at=utc_from_iso(row["created_at"]) if row["created_at"] else utc_now(),
            updated_at=utc_from_iso(row["updated_at"]) if row["updated_at"] else utc_now(),
        )

    def _row_to_observer_notification(self, row) -> ObserverNotification:
        return ObserverNotification(
            notification_id=row["notification_id"],
            target=row["target"] or "observer",
            kernel_session_id=row["kernel_session_id"] or "",
            task_id=row["task_id"] or "",
            notification_type=row["notification_type"] or "progress_update",
            urgency=row["urgency"] or "normal",
            reason=row["reason"] or "",
            progress_ref=row["progress_ref"] or "",
            suggested_observer_context=json.loads(row["suggested_observer_context"] or "{}"),
            delivery_policy=json.loads(row["delivery_policy"] or "{}"),
            status=ObserverNotificationStatus(
                row["status"] or ObserverNotificationStatus.PENDING.value
            ),
            acknowledged_at=utc_from_iso(row["acknowledged_at"]) if row["acknowledged_at"] else None,
            resolved_at=utc_from_iso(row["resolved_at"]) if row["resolved_at"] else None,
            created_at=utc_from_iso(row["created_at"]) if row["created_at"] else utc_now(),
            updated_at=utc_from_iso(row["updated_at"]) if row["updated_at"] else utc_now(),
        )
