"""Notification policy coordinator for Observer / Talker wakeups."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src.schema.state import ObserverNotification
from src.utils.time import utc_now


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
        policy = NOTIFICATION_POLICIES.get(
            notification_type,
            DEFAULT_NOTIFICATION_POLICY,
        )
        dedupe_key = f"{dispatch.task_id or dispatch.kernel_session_id}:{notification_type}"
        existing = await self._find_existing_notification(
            dispatch,
            dedupe_key=dedupe_key,
            min_interval_seconds=policy.min_interval_seconds,
        )
        if existing:
            return existing
        return await self.store.create_observer_notification(
            target="observer",
            kernel_session_id=dispatch.kernel_session_id,
            task_id=dispatch.task_id,
            notification_type=notification_type,
            urgency=policy.urgency,
            reason=reason,
            progress_ref=dispatch.run_id,
            suggested_observer_context={
                "dispatch_id": dispatch.dispatch_id,
                "run_id": dispatch.run_id,
                "task_id": dispatch.task_id,
            },
            delivery_policy={
                "dedupe_key": dedupe_key,
                "priority": policy.priority,
                "min_interval_seconds": policy.min_interval_seconds,
                "requires_user_visible_message": policy.requires_user_visible_message,
                "silent_update": policy.silent_update,
                "interrupt_user": policy.interrupt_user,
            },
        )

    async def _find_existing_notification(
        self,
        dispatch: Any,
        *,
        dedupe_key: str,
        min_interval_seconds: int,
    ) -> Optional[ObserverNotification]:
        notifications = await self.store.list_observer_notifications(
            target="observer",
            kernel_session_id=dispatch.kernel_session_id,
            task_id=dispatch.task_id,
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
