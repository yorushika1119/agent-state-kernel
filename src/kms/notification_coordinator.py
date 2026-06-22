"""Notification policy coordinator for Observer / Talker wakeups."""

from __future__ import annotations

from typing import Any, Optional

from src.schema.state import ObserverNotification


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
            urgency="normal",
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
            urgency="important",
            reason=error or "thinker_dispatch_failed",
        )

    async def _create_dispatch_notification(
        self,
        dispatch: Any,
        *,
        notification_type: str,
        urgency: str,
        reason: str,
    ) -> ObserverNotification:
        return await self.store.create_observer_notification(
            target="observer",
            kernel_session_id=dispatch.kernel_session_id,
            task_id=dispatch.task_id,
            notification_type=notification_type,
            urgency=urgency,
            reason=reason,
            progress_ref=dispatch.run_id,
            suggested_observer_context={
                "dispatch_id": dispatch.dispatch_id,
                "run_id": dispatch.run_id,
                "task_id": dispatch.task_id,
            },
            delivery_policy={
                "dedupe_key": f"{dispatch.task_id or dispatch.kernel_session_id}:{notification_type}",
                "requires_user_visible_message": notification_type
                in {"task_failed", "task_done"},
            },
        )
