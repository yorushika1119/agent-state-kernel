"""Notification policy coordinator for Observer / Talker wakeups."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.schema.events import EventType
from src.schema.state import ObserverNotification
from src.utils.time import utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotificationPolicy:
    urgency: str
    priority: str
    min_interval_seconds: int = 0
    requires_user_visible_message: bool = False
    silent_update: bool = False
    interrupt_user: bool = False


DEFAULT_NOTIFICATION_POLICY = NotificationPolicy(
    urgency="normal",
    priority="normal",
)


NOTIFICATION_POLICIES = {
    "progress_update": NotificationPolicy(
        urgency="normal",
        priority="low",
        min_interval_seconds=300,
    ),
    "task_done": NotificationPolicy(
        urgency="normal",
        priority="normal",
        requires_user_visible_message=True,
    ),
    "task_failed": NotificationPolicy(
        urgency="important",
        priority="high",
        requires_user_visible_message=True,
    ),
    "needs_user_input": NotificationPolicy(
        urgency="important",
        priority="high",
        requires_user_visible_message=True,
    ),
    "task_blocked": NotificationPolicy(
        urgency="important",
        priority="high",
        requires_user_visible_message=True,
    ),
    "clarification_needed": NotificationPolicy(
        urgency="important",
        priority="high",
        requires_user_visible_message=True,
    ),
    "conflict_detected": NotificationPolicy(
        urgency="important",
        priority="high",
        requires_user_visible_message=True,
    ),
}


class NotificationCoordinator:
    """Creates observer notifications from KMS lifecycle decisions."""

    def __init__(self, store):
        self.store = store

    async def notify_dispatch_completed(
        self,
        dispatch: Any,
        *,
        session_status: str,
        active_run_completed: bool,
    ) -> Optional[ObserverNotification]:
        if not active_run_completed:
            return None
        notification_type = (
            "task_done"
            if session_status == "completed"
            else "progress_update"
        )
        return await self._create_dispatch_notification(
            dispatch,
            notification_type=notification_type,
            reason="thinker_dispatch_completed",
        )

    async def notify_dispatch_failed(
        self,
        dispatch: Any,
        *,
        error: str = "",
        active_run_completed: bool,
    ) -> Optional[ObserverNotification]:
        if not active_run_completed:
            return None
        return await self._create_dispatch_notification(
            dispatch,
            notification_type="task_failed",
            reason=error or "thinker_dispatch_failed",
        )

    async def _create_dispatch_notification(
        self,
        dispatch: Any,
        *,
        notification_type: str,
        reason: str,
    ) -> ObserverNotification:
        return await self._emit(
            kernel_session_id=dispatch.kernel_session_id,
            task_id=dispatch.task_id,
            notification_type=notification_type,
            reason=reason,
            progress_ref=dispatch.run_id,
            extra_context={
                "dispatch_id": dispatch.dispatch_id,
                "run_id": dispatch.run_id,
                "task_id": dispatch.task_id,
            },
        )

    # ------------------------------------------------------------------
    # 管线触发：任务跑到一半就主动汇报（冲突 / 阻塞 / 需要输入）
    # ------------------------------------------------------------------

    async def evaluate_pipeline_event(
        self,
        session_id: str,
        primary_event: Any,
        side_effects: Optional[List[Any]] = None,
    ) -> List[ObserverNotification]:
        """在管线 Reduce 之后调用：看刚处理的事件 + 新状态，决定是否主动汇报。

        通知只是输出，这里永远不向上抛异常打断事件管线。
        """
        created: List[ObserverNotification] = []
        try:
            session = await self.store.get_session(session_id)
            if not session:
                return created
            task_id = getattr(session, "active_task_id", "") or ""
            status_value = session.status.value if session.status else "running"
            if status_value not in ("running", ""):
                return created  # 任务已结束，进度类不再主动报

            events = [primary_event] + list(side_effects or [])

            # ① 冲突：claim/belief 被接受为 conflicting，或出现 ConflictDetected
            for event in events:
                if self._is_conflict_event(event):
                    notif = await self.notify_conflict(
                        kernel_session_id=session_id,
                        task_id=task_id,
                        claim=(getattr(event, "payload", None) or {}).get("claim", ""),
                    )
                    if notif:
                        created.append(notif)
                    break

            # ② 阻塞：task_flow 进入 blocked
            task_flow = await self.store.get_task_flow(session_id)
            flow_status = getattr(getattr(task_flow, "status", None), "value", None)
            if flow_status == "blocked":
                notif = await self.notify_task_blocked(
                    kernel_session_id=session_id, task_id=task_id
                )
                if notif:
                    created.append(notif)

            # ③ 需要用户输入
            progress = await self.store.get_progress(session_id)
            if progress and progress.needs_user_input:
                notif = await self.notify_needs_user_input(
                    kernel_session_id=session_id, task_id=task_id
                )
                if notif:
                    created.append(notif)
        except Exception:  # noqa: BLE001 — 通知失败绝不能打断事件管线
            logger.warning("evaluate_pipeline_event failed", exc_info=True)
        return created

    @staticmethod
    def _is_conflict_event(event: Any) -> bool:
        event_type = getattr(event, "event_type", None)
        if event_type == EventType.CONFLICT_DETECTED:
            return True
        if event_type in (EventType.BELIEF_UPDATED, EventType.BELIEF_PROPOSED):
            return (getattr(event, "payload", None) or {}).get("status") == "conflicting"
        return False

    async def notify_conflict(
        self, *, kernel_session_id: str, task_id: str = "", claim: str = ""
    ) -> Optional[ObserverNotification]:
        reason = f"发现冲突：{claim}" if claim else "发现证据冲突，正在核实"
        return await self._emit(
            kernel_session_id=kernel_session_id,
            task_id=task_id,
            notification_type="conflict_detected",
            reason=reason,
        )

    async def notify_task_blocked(
        self, *, kernel_session_id: str, task_id: str = "", reason: str = ""
    ) -> Optional[ObserverNotification]:
        return await self._emit(
            kernel_session_id=kernel_session_id,
            task_id=task_id,
            notification_type="task_blocked",
            reason=reason or "任务被阻塞",
        )

    async def notify_needs_user_input(
        self, *, kernel_session_id: str, task_id: str = "", question: str = ""
    ) -> Optional[ObserverNotification]:
        extra = {"question_for_user": question} if question else None
        return await self._emit(
            kernel_session_id=kernel_session_id,
            task_id=task_id,
            notification_type="needs_user_input",
            reason=question or "需要用户补充信息",
            extra_context=extra,
        )

    # ------------------------------------------------------------------
    # 通用发通知：策略 + 去重/节流 + 填内容 + 写一条记录
    # ------------------------------------------------------------------

    async def _emit(
        self,
        *,
        kernel_session_id: str,
        task_id: str,
        notification_type: str,
        reason: str,
        progress_ref: str = "",
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[ObserverNotification]:
        policy = await self._policy_for(kernel_session_id, task_id, notification_type)
        dedupe_key = f"{task_id or kernel_session_id}:{notification_type}"
        existing = await self._find_existing(
            kernel_session_id,
            task_id,
            dedupe_key=dedupe_key,
            min_interval_seconds=policy.min_interval_seconds,
        )
        if existing:
            return existing
        context = await self._build_observer_context(kernel_session_id)
        if extra_context:
            context.update(extra_context)
        return await self.store.create_observer_notification(
            target="observer",
            kernel_session_id=kernel_session_id,
            task_id=task_id,
            notification_type=notification_type,
            urgency=policy.urgency,
            reason=reason,
            progress_ref=progress_ref,
            suggested_observer_context=context,
            delivery_policy={
                "dedupe_key": dedupe_key,
                "priority": policy.priority,
                "min_interval_seconds": policy.min_interval_seconds,
                "requires_user_visible_message": policy.requires_user_visible_message,
                "silent_update": policy.silent_update,
                "interrupt_user": policy.interrupt_user,
            },
        )

    async def _build_observer_context(self, session_id: str) -> Dict[str, Any]:
        """把白板上现成的进度内容装进通知，前端拿到直接能播报。"""
        context: Dict[str, Any] = {"session_id": session_id}
        progress = await self.store.get_progress(session_id)
        if progress:
            context.update(
                {
                    "one_line_summary": progress.summary,
                    "safe_facts": list(progress.safe_facts),
                    "uncertain_points": list(progress.unsafe_claims),
                    "forbidden_claims": list(progress.unsafe_claims),
                    "status": progress.status,
                    "stage": progress.stage,
                }
            )
        return context

    async def _policy_for(
        self,
        kernel_session_id: str,
        task_id: str,
        notification_type: str,
    ) -> NotificationPolicy:
        policy = NOTIFICATION_POLICIES.get(
            notification_type,
            DEFAULT_NOTIFICATION_POLICY,
        )
        if notification_type != "task_failed":
            return policy

        notifications = await self.store.list_observer_notifications(
            target="observer",
            kernel_session_id=kernel_session_id,
            task_id=task_id,
            status="",
            limit=100,
        )
        previous_failures = [
            n for n in notifications if n.notification_type == "task_failed"
        ]
        if len(previous_failures) < 2:
            return policy

        return NotificationPolicy(
            urgency="critical",
            priority="urgent",
            requires_user_visible_message=True,
            interrupt_user=True,
        )

    async def _find_existing(
        self,
        kernel_session_id: str,
        task_id: str,
        *,
        dedupe_key: str,
        min_interval_seconds: int,
    ) -> Optional[ObserverNotification]:
        notifications = await self.store.list_observer_notifications(
            target="observer",
            kernel_session_id=kernel_session_id,
            task_id=task_id,
            status="",
            limit=100,
        )
        now = utc_now()
        for notification in notifications:
            if notification.delivery_policy.get("dedupe_key") != dedupe_key:
                continue
            if notification.status.value == "pending":
                return notification
            if (
                min_interval_seconds > 0
                and notification.created_at
                and (now - notification.created_at).total_seconds() < min_interval_seconds
            ):
                return notification
        return None
