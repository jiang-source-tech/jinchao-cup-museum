from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from core.xiaoxin.control_types import (
    XiaoxinControlEventRequest,
    XiaoxinEvent,
)
from core.xiaoxin.identity.models import DEVICE_BOUND
from core.xiaoxin.identity.store import normalize_todo_due_at

LOGGER = logging.getLogger(__name__)
MAX_EVENT_TTL_MS = 24 * 60 * 60 * 1000


class XiaoxinTodoReminderScheduler:
    def __init__(
        self,
        identity_store: Any,
        dispatcher: Any,
        replay_window_minutes: float = 120,
    ):
        if replay_window_minutes <= 0:
            raise ValueError("todo reminder replay window must be positive")
        self.identity_store = identity_store
        self.dispatcher = dispatcher
        self.replay_window = timedelta(minutes=replay_window_minutes)

    async def dispatch_due_todos(
        self, now: str | datetime
    ) -> list[dict[str, str]]:
        now_text = normalize_todo_due_at(now)
        now_at = _parse_iso_datetime(now_text)
        dispatched: list[dict[str, str]] = []
        for todo in self.identity_store.list_due_student_todos(now_text):
            if now_at >= self._expires_at(todo):
                self.identity_store.mark_student_todo_reminder_missed(
                    todo["user_id"],
                    todo["id"],
                    now_text,
                )
                continue
            device_id = self._first_bound_device_id(todo["user_id"])
            if not device_id:
                continue

            claimed = self.identity_store.claim_student_todo_for_reminder(
                todo["user_id"],
                todo["id"],
                now_text,
            )
            if claimed is None:
                continue

            try:
                record = await self.dispatcher.submit(
                    self._event_request(device_id, claimed, now_at)
                )
            except asyncio.CancelledError:
                try:
                    self.identity_store.release_student_todo_reminder_claim(
                        todo["user_id"],
                        todo["id"],
                    )
                except Exception:
                    LOGGER.exception(
                        "Failed to release Xiaoxin todo reminder claim after cancellation"
                    )
                raise
            except Exception:
                self.identity_store.release_student_todo_reminder_claim(
                    todo["user_id"],
                    todo["id"],
                )
                raise

            marked = self.identity_store.mark_student_todo_reminded(
                todo["user_id"],
                todo["id"],
                record.delivery_id,
                now_text,
            )
            if marked is not None:
                dispatched.append(
                    {"todo_id": todo["id"], "delivery_id": record.delivery_id}
                )
        return dispatched

    def _first_bound_device_id(self, user_id: str) -> str:
        for device in self.identity_store.list_devices_for_user(user_id):
            if device.owner_user_id == user_id and device.bind_status == DEVICE_BOUND:
                return device.device_id
        return ""

    def _event_request(
        self,
        device_id: str,
        todo: dict[str, Any],
        now_at: datetime,
    ) -> XiaoxinControlEventRequest:
        title = str(todo["title"]).strip()
        expires_at = self._expires_at(todo)
        ttl_ms = min(
            max(int((expires_at - now_at).total_seconds() * 1000), 1),
            MAX_EVENT_TTL_MS,
        )
        return XiaoxinControlEventRequest(
            device_id=device_id,
            event=XiaoxinEvent.TODO_REMINDER,
            title="提醒事项",
            body=title,
            tag=f"todo:{todo['id']}",
            priority=2,
            ttl_ms=ttl_ms,
            speak=True,
            speak_text=f"小芯提醒你，{title}。",
            todo_title=title,
            due_at=str(todo["due_at"]),
        )

    def _expires_at(self, todo: dict[str, Any]) -> datetime:
        return _parse_iso_datetime(str(todo["due_at"])) + self.replay_window


def _parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("todo reminder timestamp must include timezone")
    return parsed
