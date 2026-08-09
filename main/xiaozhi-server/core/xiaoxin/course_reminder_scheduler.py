from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from core.xiaoxin.control_types import XiaoxinControlEventRequest, XiaoxinEvent
from core.xiaoxin.identity.models import DEVICE_BOUND
from core.xiaoxin.identity.store import normalize_todo_due_at

LOGGER = logging.getLogger(__name__)
MAX_EVENT_TTL_MS = 24 * 60 * 60 * 1000


class XiaoxinCourseReminderScheduler:
    def __init__(self, identity_store: Any, dispatcher: Any):
        self.identity_store = identity_store
        self.dispatcher = dispatcher

    async def dispatch_due_courses(
        self, now: str | datetime
    ) -> list[dict[str, str]]:
        now_text = normalize_todo_due_at(now)
        now_at = _parse_iso_datetime(now_text)
        dispatched: list[dict[str, str]] = []
        for course in self.identity_store.list_due_student_courses(now_text):
            occurrence_at = str(course["occurrence_at"])
            expires_at = _parse_iso_datetime(occurrence_at)
            if now_at >= expires_at:
                continue

            device_id = self._first_bound_device_id(course["user_id"])
            if not device_id:
                continue

            claimed = self.identity_store.claim_student_course_for_reminder(
                course["user_id"],
                course["id"],
                occurrence_at,
            )
            if claimed is None:
                continue
            claimed["remind_before_min"] = int(
                course.get("remind_before_min") or 0
            )

            try:
                record = await self.dispatcher.submit(
                    self._event_request(device_id, claimed, now_at, expires_at)
                )
            except asyncio.CancelledError:
                self._release_claim(claimed)
                raise
            except Exception:
                self._release_claim(claimed)
                raise

            marked = self.identity_store.mark_student_course_reminded(
                course["user_id"],
                course["id"],
                record.delivery_id,
                occurrence_at,
            )
            if marked is not None:
                dispatched.append(
                    {
                        "course_id": course["id"],
                        "delivery_id": record.delivery_id,
                        "occurrence_at": occurrence_at,
                    }
                )
        return dispatched

    def _first_bound_device_id(self, user_id: str) -> str:
        for device in self.identity_store.list_devices_for_user(user_id):
            if device.owner_user_id == user_id and device.bind_status == DEVICE_BOUND:
                return device.device_id
        return ""

    def _release_claim(self, course: dict[str, Any]) -> None:
        try:
            self.identity_store.release_student_course_reminder_claim(
                course["user_id"],
                course["id"],
                course["occurrence_at"],
            )
        except Exception:
            LOGGER.exception("Failed to release Xiaoxin course reminder claim")

    def _event_request(
        self,
        device_id: str,
        course: dict[str, Any],
        now_at: datetime,
        expires_at: datetime,
    ) -> XiaoxinControlEventRequest:
        title = str(course["title"]).strip()
        classroom = str(course.get("classroom") or "").strip()
        starts_at = str(course.get("starts_at") or "").strip()
        remind_before_min = int(course.get("remind_before_min") or 0)
        detail_parts = [part for part in (starts_at, classroom) if part]
        body = f"{title} {' '.join(detail_parts)}".strip()
        location_text = f"，地点在{classroom}" if classroom else ""
        starts_text = f"{starts_at}有" if starts_at else "有"
        ttl_ms = min(
            max(int((expires_at - now_at).total_seconds() * 1000), 1),
            MAX_EVENT_TTL_MS,
        )
        return XiaoxinControlEventRequest(
            device_id=device_id,
            event=XiaoxinEvent.COURSE_REMINDER,
            title="上课提醒",
            body=body,
            tag=f"course:{course['id']}:{course['occurrence_at']}",
            priority=2,
            ttl_ms=ttl_ms,
            speak=True,
            speak_text=f"小芯提醒你，{starts_text}{title}课{location_text}。",
            course_name=title,
            classroom=classroom,
            starts_at=starts_at,
            remind_before_min=remind_before_min,
        )


def _parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("course reminder timestamp must include timezone")
    return parsed
