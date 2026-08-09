from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import math
from typing import Any, Callable

from core.xiaoxin.identity.models import DEVICE_BOUND
from core.xiaoxin.identity.store import normalize_todo_due_at
from core.xiaoxin.local_time import local_datetime


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class VoiceReminderCreation:
    todo: dict[str, Any]
    response: str


class XiaoxinVoiceReminderCreator:
    def __init__(
        self,
        identity_store: Any,
        overview_service: Any,
        clock: Callable[[], datetime] = local_datetime,
        observation_ingress: Any | None = None,
    ) -> None:
        self.identity_store = identity_store
        self.overview_service = overview_service
        self.clock = clock
        self.observation_ingress = observation_ingress

    async def create(
        self,
        *,
        device_id: str,
        title: str,
        delay_minutes: float | None = None,
        due_at: str | None = None,
    ) -> VoiceReminderCreation:
        device = self.identity_store.get_device_by_device_id(device_id)
        if (
            device is None
            or device.owner_user_id is None
            or device.bind_status != DEVICE_BOUND
        ):
            raise ValueError("device must be bound before creating reminders")

        if (delay_minutes is None) == (due_at is None):
            raise ValueError("reminder time required")
        now = local_datetime(self.clock())
        if delay_minutes is not None:
            if isinstance(delay_minutes, bool):
                raise ValueError("delay_minutes must be positive")
            minutes = float(delay_minutes)
            if not math.isfinite(minutes) or minutes <= 0:
                raise ValueError("delay_minutes must be positive")
            reminder_at = now + timedelta(minutes=minutes)
            response_time = _format_relative_delay(minutes)
        else:
            normalized_due_at = normalize_todo_due_at(due_at)
            reminder_at = datetime.fromisoformat(normalized_due_at)
            if reminder_at <= now:
                raise ValueError("reminder time must be in the future")
            response_time = _format_absolute_time(reminder_at, now)

        todo = self.identity_store.create_student_todo(
            device.owner_user_id,
            {
                "title": title,
                "dueAt": reminder_at.isoformat(timespec="seconds"),
                "source": "voice",
                "sourceDeviceId": device_id,
            },
        )
        self._observe_created_todo(device.owner_user_id, todo)
        asyncio.create_task(self._refresh_overview(device.owner_user_id))
        return VoiceReminderCreation(
            todo=todo,
            response=f"好，{response_time}提醒你{todo['title']}。",
        )

    def _observe_created_todo(self, user_id: str, todo: dict[str, Any]) -> None:
        if self.observation_ingress is None:
            return
        occurred_at = str(todo["created_at"])
        payload: dict[str, object] = {
            "todo_id": str(todo["id"]),
            "title": str(todo["title"]),
            "due_at": str(todo["due_at"]),
            "status": str(todo["status"]),
        }
        notes = str(todo.get("notes") or "").strip()
        if notes:
            payload["notes"] = notes
        try:
            self.observation_ingress.observe_user_event(
                user_id=user_id,
                idempotency_key=f"todo_created:{todo['id']}:{occurred_at}",
                kind="todo_created",
                source_kind="voice_todo",
                source_ref=str(todo["id"]),
                occurred_at=occurred_at,
                payload=payload,
                safe_summary="用户通过语音创建了一项未来待办。",
            )
        except Exception:
            LOGGER.exception(
                "Xiaoxin voice reminder companion observation failed",
                extra={"todo_id": str(todo["id"])},
            )

    async def _refresh_overview(self, user_id: str) -> None:
        try:
            await self.overview_service.refresh_user_devices(
                user_id,
                "voice_todo_created",
            )
        except Exception:
            LOGGER.exception("Xiaoxin voice reminder overview refresh failed")


def _format_minutes(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _format_relative_delay(minutes: float) -> str:
    if minutes.is_integer() and int(minutes) % (24 * 60) == 0:
        return f"{int(minutes) // (24 * 60)}天后"
    if minutes.is_integer() and int(minutes) % 60 == 0:
        return f"{int(minutes) // 60}小时后"
    return f"{_format_minutes(minutes)}分钟后"


def _format_absolute_time(reminder_at: datetime, now: datetime) -> str:
    local_reminder = local_datetime(reminder_at)
    day_delta = (local_reminder.date() - now.date()).days
    if day_delta == 0:
        day_text = "今天"
    elif day_delta == 1:
        day_text = "明天"
    elif local_reminder.year == now.year:
        day_text = f"{local_reminder.month}月{local_reminder.day}日"
    else:
        day_text = (
            f"{local_reminder.year}年{local_reminder.month}月{local_reminder.day}日"
        )
    return f"{day_text}{local_reminder.hour}点{local_reminder.minute:02d}分"
