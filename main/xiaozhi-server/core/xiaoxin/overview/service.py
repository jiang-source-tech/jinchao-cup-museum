from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable

from core.xiaoxin.companion import (
    CompanionProjectionRequest,
    build_companion_subject_context,
)
from core.xiaoxin.identity.models import DEVICE_BOUND
from core.xiaoxin.identity.store import (
    COURSE_LOCAL_TZ,
    DEFAULT_COURSE_REMIND_BEFORE_MIN,
)
from core.xiaoxin.local_time import local_date_text
from core.xiaoxin.overview.models import DailyWeather, OverviewSnapshot
from core.xiaoxin.personal_pet_lifecycle import project_personal_pet


PUBLISH_BACKOFF_SECONDS = (1, 2, 5, 15, 30)
PUBLISH_ACK_TIMEOUT_SECONDS = 10
MAX_PAYLOAD_BYTES = 2048
WEATHER_PROVIDER_NAME = "open_meteo"

logger = logging.getLogger(__name__)

_PLACE_TEXT_LIMIT = 32
_SUMMARY_TEXT_LIMIT = 48
_TITLE_TEXT_LIMIT = 48
_DETAIL_TEXT_LIMIT = 64
_MAX_EARLY_ACKS_PER_PUBLISH = 64
_MAX_RETIRED_MIDS = 256
_WEATHER_PLACE_SUFFIXES = (
    "特别行政区",
    "自治区",
    "自治州",
    "省",
    "市",
)


def _normalized_weather_place(value: object) -> str:
    normalized = "".join(str(value or "").split())
    for suffix in _WEATHER_PLACE_SUFFIXES:
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


class OverviewSyncService:
    def __init__(
        self,
        *,
        identity_store: Any,
        overview_store: Any | None = None,
        weather_provider: Any | None = None,
        publisher: Any | None = None,
        ip_location_provider: Any | None = None,
        registry: Any | None = None,
        clock: Callable[[], datetime] | None = None,
        weather_provider_name: str = WEATHER_PROVIDER_NAME,
        ip_hmac_key: bytes | None = None,
        companion_mind: Any | None = None,
    ) -> None:
        self.identity_store = identity_store
        self.overview_store = overview_store
        self.weather_provider = weather_provider
        self.publisher = publisher
        self.ip_location_provider = ip_location_provider
        self.registry = registry
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.weather_provider_name = weather_provider_name
        self._ip_hmac_key = ip_hmac_key
        self.companion_mind = companion_mind
        self._mid_to_snapshot: dict[int, tuple[str, int]] = {}
        self._early_ack_windows: dict[object, set[int]] = {}
        self._retired_mids: OrderedDict[int, None] = OrderedDict()
        self._mid_lock = Lock()
        self._publish_session_generation: int | None = None
        self._device_locks: dict[str, tuple[asyncio.Lock, int]] = {}
        self._device_locks_guard = Lock()

        add_ack_listener = getattr(publisher, "add_publish_ack_listener", None)
        if callable(add_ack_listener):
            add_ack_listener(self.handle_publish_ack)
            self.begin_publish_session(
                int(getattr(publisher, "publish_session_generation", 0) or 0)
            )

    async def query_daily_weather(
        self,
        province: str,
        city: str,
        date_text: str,
        *,
        device_id: str | None = None,
        country_code: str = "CN",
    ) -> DailyWeather:
        self._require_sync_dependencies()
        resolved_province = str(province or "").strip()
        resolved_city = str(city or "").strip()
        resolved_country = str(country_code or "CN").strip() or "CN"
        if not resolved_province and device_id:
            location = self.overview_store.get_location(device_id)
            if (
                location is not None
                and _normalized_weather_place(location.get("city"))
                == _normalized_weather_place(resolved_city)
            ):
                resolved_city = str(location.get("city") or "").strip()
                resolved_province = str(location.get("province") or "").strip()
                resolved_country = (
                    str(location.get("country_code") or "CN").strip() or "CN"
                )

        cached = self.overview_store.get_daily_weather(
            resolved_province,
            resolved_city,
            date_text,
            self.weather_provider_name,
            country_code=resolved_country,
        )
        if cached is not None:
            logger.debug(
                "weather query cache_hit: %s/%s/%s",
                resolved_province,
                resolved_city,
                date_text,
            )
            return cached
        weather_lock_key = json.dumps(
            [
                "weather-query",
                resolved_country,
                resolved_province,
                resolved_city,
                date_text,
                self.weather_provider_name,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        async with self._serialize_device(weather_lock_key):
            cached = self.overview_store.get_daily_weather(
                resolved_province,
                resolved_city,
                date_text,
                self.weather_provider_name,
                country_code=resolved_country,
            )
            if cached is not None:
                logger.debug(
                    "weather query cache_hit: %s/%s/%s",
                    resolved_province,
                    resolved_city,
                    date_text,
                )
                return cached
            if self.weather_provider is None:
                raise RuntimeError("weather provider unavailable")

            weather = await self.weather_provider.daily(
                resolved_province,
                resolved_city,
                date_text,
            )
            if (
                weather.date != date_text
                or weather.city != resolved_city
                or weather.country_code != resolved_country
                or (
                    resolved_province
                    and weather.province != resolved_province
                )
            ):
                raise ValueError("weather response does not match query")
            self.overview_store.put_daily_weather(
                weather,
                self.weather_provider_name,
            )
            logger.debug(
                "weather query cache_fill: %s/%s/%s",
                resolved_province,
                resolved_city,
                date_text,
            )
            return weather


    async def refresh_device(
        self,
        device_id: str,
        reason: str,
        date_text: str | None = None,
    ) -> dict[str, object]:
        async with self._serialize_device(device_id):
            return await self._refresh_device_locked(
                device_id,
                reason,
                date_text,
            )

    async def _refresh_device_locked(
        self,
        device_id: str,
        reason: str,
        date_text: str | None = None,
    ) -> dict[str, object]:
        self._require_sync_dependencies()
        selected_date = date_text or self._today_text()
        device = self.identity_store.get_device_by_device_id(device_id)
        if (
            device is None
            or device.bind_status != DEVICE_BOUND
            or device.owner_user_id is None
        ):
            return await self._clear_unbound_device_locked(
                device_id, reason, selected_date
            )

        owner_user_id = str(device.owner_user_id)
        overview = self.build_student_overview(
            owner_user_id,
            selected_date,
            device_id=device_id,
        )
        weather = await self._weather_card_for_refresh(
            device_id,
            selected_date,
            reason,
        )
        current_device = self.identity_store.get_device_by_device_id(device_id)
        if (
            current_device is None
            or current_device.bind_status != DEVICE_BOUND
            or current_device.owner_user_id != owner_user_id
        ):
            return self._discarded_result(device_id, reason)
        content = {
            "bound": True,
            "weather": self._wire_weather_card(weather),
            "course": self._wire_course_card(overview["course"]),
            "todo": self._wire_todo_card(overview["todo"]),
        }
        companion = self._wire_companion_card(overview["petStatus"])
        if companion is not None:
            content["companion"] = companion
        return self._persist_and_publish(
            device_id,
            owner_user_id,
            reason,
            content,
        )

    async def refresh_user_devices(
        self,
        user_id: str,
        reason: str,
        date_text: str | None = None,
    ) -> list[dict[str, object]]:
        results = []
        for device in self.identity_store.list_devices_for_user(user_id):
            if (
                device.owner_user_id == user_id
                and device.bind_status == DEVICE_BOUND
            ):
                results.append(
                    await self.refresh_device(
                        device.device_id,
                        reason,
                        date_text,
                    )
                )
        return results

    async def set_manual_location_for_user(
        self,
        user_id: str,
        device_id: str,
        province: str,
        city: str,
        reason: str,
    ) -> dict[str, object] | None:
        async with self._serialize_device(device_id):
            self._require_sync_dependencies()
            device = self.identity_store.get_device_by_device_id(device_id)
            if (
                device is None
                or device.bind_status != DEVICE_BOUND
                or device.owner_user_id != user_id
            ):
                return None
            previous_location = self.overview_store.get_location(device_id)
            persisted = self.overview_store.set_manual_location(
                device_id,
                province,
                city,
            )
            result = await self._refresh_device_locked(device_id, reason)
            current = self.identity_store.get_device_by_device_id(device_id)
            if (
                current is None
                or current.bind_status != DEVICE_BOUND
                or current.owner_user_id != user_id
            ):
                self.overview_store.restore_location(
                    device_id,
                    previous_location,
                    expected_revision=int(persisted["location_revision"]),
                )
                return None
            return result

    async def set_automatic_location_for_user(
        self,
        user_id: str,
        device_id: str,
        reason: str,
    ) -> dict[str, object] | None:
        async with self._serialize_device(device_id):
            self._require_sync_dependencies()
            device = self.identity_store.get_device_by_device_id(device_id)
            if (
                device is None
                or device.bind_status != DEVICE_BOUND
                or device.owner_user_id != user_id
            ):
                return None
            previous_location = self.overview_store.get_location(device_id)
            persisted = self.overview_store.set_location_mode(
                device_id,
                "automatic",
            )
            result = await self._refresh_device_locked(device_id, reason)
            current = self.identity_store.get_device_by_device_id(device_id)
            if (
                current is None
                or current.bind_status != DEVICE_BOUND
                or current.owner_user_id != user_id
            ):
                if persisted is not None:
                    self.overview_store.restore_location(
                        device_id,
                        previous_location,
                        expected_revision=int(persisted["location_revision"]),
                    )
                return None
            return result

    async def clear_unbound_device(
        self, device_id: str, reason: str
    ) -> dict[str, object]:
        async with self._serialize_device(device_id):
            return await self._clear_unbound_device_entry_locked(
                device_id,
                reason,
            )

    async def _clear_unbound_device_entry_locked(
        self, device_id: str, reason: str
    ) -> dict[str, object]:
        self._require_sync_dependencies()
        device = self.identity_store.get_device_by_device_id(device_id)
        if (
            device is not None
            and device.bind_status == DEVICE_BOUND
            and device.owner_user_id is not None
        ):
            return await self._refresh_device_locked(device_id, reason)
        return await self._clear_unbound_device_locked(
            device_id,
            reason,
            self._today_text(),
        )

    async def observe_device_ip(
        self,
        device_id: str,
        public_ip: str,
        reason: str,
    ) -> dict[str, object]:
        self._require_sync_dependencies()
        if not self._ip_hmac_key:
            return {
                "device_id": device_id,
                "reason": reason,
                "location_changed": False,
                "refreshed": False,
                "error_code": "overview_ip_hmac_unconfigured",
            }
        if self.ip_location_provider is None:
            return {
                "device_id": device_id,
                "reason": reason,
                "location_changed": False,
                "refreshed": False,
            }
        public_ip_hmac = hmac.new(
            self._ip_hmac_key,
            public_ip.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        async with self._serialize_device(device_id):
            current = self.identity_store.get_device_by_device_id(device_id)
            if (
                current is None
                or current.bind_status != DEVICE_BOUND
                or current.owner_user_id is None
            ):
                return {
                    "device_id": device_id,
                    "reason": reason,
                    "location_changed": False,
                    "refreshed": False,
                }
            owner_user_id = current.owner_user_id
            previous = self.overview_store.get_location(device_id)
            if previous is not None:
                cached_hmac = (
                    previous.get("automatic_public_ip_hmac")
                    if previous.get("mode") == "manual"
                    else previous.get("public_ip_hmac")
                )
                if cached_hmac == public_ip_hmac:
                    return {
                        "device_id": device_id,
                        "reason": reason,
                        "location_changed": False,
                        "refreshed": False,
                    }
            location = await self.ip_location_provider.locate(public_ip)
            if location is None:
                return {
                    "device_id": device_id,
                    "reason": reason,
                    "location_changed": False,
                    "refreshed": False,
                }
            persisted = self.overview_store.set_automatic_location(
                device_id,
                public_ip_hmac,
                location,
            )
            changed = previous is None or any(
                previous.get(key) != persisted.get(key)
                for key in ("province", "city", "country_code", "mode")
            )
            refresh = await self._refresh_device_locked(device_id, reason)
            final_device = self.identity_store.get_device_by_device_id(device_id)
            if (
                final_device is None
                or final_device.bind_status != DEVICE_BOUND
                or final_device.owner_user_id != owner_user_id
            ):
                self.overview_store.restore_location(
                    device_id,
                    previous,
                    expected_revision=int(persisted["location_revision"]),
                )
                return {
                    **refresh,
                    "location_changed": False,
                    "refreshed": False,
                }
            return {
                **refresh,
                "location_changed": changed,
                "refreshed": True,
            }

    async def drain_pending(self) -> int:
        self._require_sync_dependencies()
        pending = self.overview_store.list_pending_snapshots(self._now_iso())
        handled = 0
        for listed_snapshot in pending:
            async with self._serialize_device(listed_snapshot.device_id):
                handled += await self._drain_snapshot_locked(listed_snapshot)
        return handled

    async def _drain_snapshot_locked(
        self, listed_snapshot: OverviewSnapshot
    ) -> int:
        current_snapshot = self.overview_store.get_snapshot(
            listed_snapshot.device_id
        )
        if (
            current_snapshot is None
            or current_snapshot.revision != listed_snapshot.revision
            or current_snapshot.publish_state != "pending"
            or not self._snapshot_is_due(current_snapshot)
        ):
            return 0

        device = self.identity_store.get_device_by_device_id(
            current_snapshot.device_id
        )
        current_owner = (
            str(device.owner_user_id)
            if device is not None
            and device.bind_status == DEVICE_BOUND
            and device.owner_user_id is not None
            else None
        )
        payload_bound = current_snapshot.payload.get("bound") is True
        owner_matches = (
            payload_bound
            and current_owner is not None
            and current_snapshot.owner_user_id == current_owner
        )
        unbound_matches = (
            not payload_bound
            and current_owner is None
            and current_snapshot.owner_user_id is None
        )
        if owner_matches or unbound_matches:
            self._attempt_publish(current_snapshot)
            return 1

        date_text = self._snapshot_date_text(current_snapshot)
        if current_owner is not None:
            result = await self._refresh_device_locked(
                current_snapshot.device_id,
                "pending_identity_changed",
                date_text,
            )
        else:
            result = await self._clear_unbound_device_locked(
                current_snapshot.device_id,
                "pending_identity_changed",
                date_text,
            )
        if result.get("discarded"):
            return 0
        if result.get("publish_attempted"):
            return 1

        coalesced = self.overview_store.get_snapshot(
            current_snapshot.device_id
        )
        if (
            coalesced is None
            or coalesced.publish_state != "pending"
            or not self._snapshot_is_due(coalesced)
        ):
            return 0
        self._attempt_publish(coalesced)
        return 1

    def _snapshot_is_due(self, snapshot: OverviewSnapshot) -> bool:
        if snapshot.next_attempt_at is None:
            return True
        return datetime.fromisoformat(snapshot.next_attempt_at) <= self._now()

    def _snapshot_date_text(self, snapshot: OverviewSnapshot) -> str:
        weather = snapshot.payload.get("weather")
        if isinstance(weather, dict):
            date_text = str(weather.get("date") or "")
            try:
                datetime.strptime(date_text, "%Y-%m-%d")
            except ValueError:
                pass
            else:
                return date_text
        return self._today_text()

    def handle_publish_ack(self, mid: int, generation: int) -> None:
        mid_value = int(mid)
        with self._mid_lock:
            if generation != self._publish_session_generation:
                return
            if mid_value in self._retired_mids:
                self._retired_mids.pop(mid_value, None)
                return
            target = self._mid_to_snapshot.pop(mid_value, None)
            if target is None:
                for early_mids in self._early_ack_windows.values():
                    if len(early_mids) < _MAX_EARLY_ACKS_PER_PUBLISH:
                        early_mids.add(mid_value)
        if target is None:
            return
        self._mark_published(target)

    def begin_publish_session(self, generation: int | None) -> None:
        with self._mid_lock:
            if generation == self._publish_session_generation:
                return
            self._publish_session_generation = generation
            self._mid_to_snapshot.clear()
            self._retired_mids.clear()
            self._early_ack_windows.clear()

    def reset_publish_session(self) -> None:
        self.begin_publish_session(None)

    def _mark_published(self, target: tuple[str, int]) -> None:
        if self.overview_store is None:
            return
        device_id, revision = target
        self.overview_store.mark_published(
            device_id,
            revision,
            self._now_iso(),
        )

    def build_curriculum_overview(
        self, user_id: str, date_text: str, *, include_started: bool = True
    ) -> dict[str, object]:
        current_date = datetime.strptime(date_text, "%Y-%m-%d").date()
        now_local = self._now().astimezone(COURSE_LOCAL_TZ)
        semester = self.identity_store.get_student_semester(user_id)
        start_date = datetime.strptime(
            str(semester["start_date"]), "%Y-%m-%d"
        ).date()
        total_weeks = int(semester["total_weeks"])
        current_week = ((current_date - start_date).days // 7) + 1
        active_week = current_week if 1 <= current_week <= total_weeks else None
        weekday = current_date.isoweekday()

        courses = self.identity_store.list_student_courses(user_id)
        today_courses = [
            course
            for course in courses
            if int(course["weekday"]) == weekday
            and self._course_active_in_week(course, active_week)
        ]
        today_courses.sort(
            key=lambda course: (course["start_section"], course["title"])
        )
        conflict_map = self._course_conflict_map(today_courses)
        reminder_settings = self.identity_store.get_student_course_reminder_settings(
            user_id
        )
        remind_before_min = int(
            reminder_settings.get(
                "remind_before_min", DEFAULT_COURSE_REMIND_BEFORE_MIN
            )
        )
        course_payloads = []
        for course in today_courses:
            payload = self._student_course_payload(course)
            conflict_ids = sorted(conflict_map.get(str(course["id"]), set()))
            payload["conflictCount"] = len(conflict_ids)
            payload["conflictCourseIds"] = conflict_ids
            payload["remindBeforeMin"] = remind_before_min
            course_payloads.append(payload)
        upcoming_courses = course_payloads
        if not include_started and current_date == now_local.date():
            upcoming_courses = [
                course
                for course in course_payloads
                if self._course_starts_after(course, now_local)
            ]
        return {
            "date": date_text,
            "semester": self._student_semester_payload(semester),
            "currentWeek": active_week,
            "weekStatus": f"第{active_week}周" if active_week else "假期中",
            "todayCourses": course_payloads,
            "nextCourse": upcoming_courses[0] if upcoming_courses else None,
            "conflictCount": sum(len(ids) for ids in conflict_map.values()) // 2,
        }

    def build_student_overview(
        self,
        user_id: str,
        date_text: str,
        *,
        device_id: str | None = None,
        include_started: bool = True,
    ) -> dict[str, object]:
        curriculum = self.build_curriculum_overview(
            user_id, date_text, include_started=include_started
        )
        courses = self.identity_store.list_student_courses(user_id)
        device = self._bound_device_for_user(user_id, device_id=device_id)
        device_payload = self._miniprogram_device_payload(device)
        course_count = len(curriculum.get("todayCourses") or [])
        reminder_count = self._today_reminder_count(user_id, date_text)
        pet_status = self._personal_pet_status(user_id, date_text)
        if device is not None and device_payload.get("state") == "offline":
            pet_status.update(
                {
                    "todayState": "设备已离线",
                    "recentSummary": "暂时无法同步课程和提醒，请检查设备网络或电源。",
                }
            )
        elif device is not None and device_payload.get("state") == "wakeable":
            pet_status.update(
                {
                    "todayState": "待机中",
                    "recentSummary": "设备已联网，有任务时会自动唤醒。",
                }
            )
        elif device is not None and device_payload.get("state") == "connected":
            if course_count == 0 and reminder_count == 0:
                pet_status.update(
                    {
                        "todayState": "轻松待机",
                        "recentSummary": "今天暂时没有课程和提醒，可以先补充课表或待办。",
                    }
                )
            else:
                pet_status.update(
                    {
                        "todayState": "已准备好",
                        "recentSummary": (
                            f"今天有 {course_count} 门课程、{reminder_count} 个提醒。"
                        ),
                    }
                )
        return {
            "date": date_text,
            "generatedAt": self._now_iso(timespec="seconds"),
            "device": device_payload,
            "petStatus": pet_status,
            "todaySummary": {
                "courseCount": course_count,
                "reminderCount": reminder_count,
                "latestNotificationState": "暂无通知",
            },
            "latestNotification": None,
            "weather": self._cached_weather_card(
                device.device_id if device is not None else None,
                date_text,
            ),
            "course": self._course_overview_card(
                curriculum,
                has_configured_courses=bool(courses),
            ),
            "todo": self._todo_overview_card(user_id, date_text),
            "curriculum": curriculum,
        }

    async def _clear_unbound_device_locked(
        self,
        device_id: str,
        reason: str,
        date_text: str,
    ) -> dict[str, object]:
        content = {
            "bound": False,
            "weather": {
                "configured": False,
                "available": False,
                "province": "",
                "city": "",
                "date": date_text,
                "summary": "设备未绑定",
                "detail": "绑定后显示天气",
                "fetched_at": "",
            },
            "course": {
                "configured": False,
                "available_today": False,
                "title": "设备未绑定",
                "detail": "绑定后显示课程",
            },
            "todo": {
                "configured": False,
                "count": 0,
                "detail": "绑定后显示待办",
            },
        }
        current_device = self.identity_store.get_device_by_device_id(device_id)
        if (
            current_device is not None
            and current_device.bind_status == DEVICE_BOUND
            and current_device.owner_user_id is not None
        ):
            return self._discarded_result(device_id, reason)
        return self._persist_and_publish(device_id, None, reason, content)

    def _persist_and_publish(
        self,
        device_id: str,
        owner_user_id: str | None,
        reason: str,
        content: dict[str, object],
    ) -> dict[str, object]:
        generated_at = self._now_iso(timespec="seconds")
        snapshot, changed = self.overview_store.upsert_snapshot(
            device_id,
            owner_user_id,
            content,
            generated_at,
        )
        self._ensure_payload_size(snapshot.payload)
        publish_attempted = False
        publish_accepted = False
        if changed:
            publish_attempted = True
            publish_accepted = self._attempt_publish(snapshot)
        return {
            "device_id": device_id,
            "reason": reason,
            "revision": snapshot.revision,
            "changed": changed,
            "publish_attempted": publish_attempted,
            "publish_accepted": publish_accepted,
            "publish_state": snapshot.publish_state,
            "payload": snapshot.payload,
            "discarded": False,
        }

    @staticmethod
    def _discarded_result(
        device_id: str, reason: str
    ) -> dict[str, object]:
        return {
            "device_id": device_id,
            "reason": reason,
            "revision": None,
            "changed": False,
            "publish_attempted": False,
            "publish_accepted": False,
            "publish_state": "discarded",
            "payload": None,
            "discarded": True,
        }

    @asynccontextmanager
    async def _serialize_device(self, device_id: str):
        with self._device_locks_guard:
            lock, users = self._device_locks.get(
                device_id,
                (asyncio.Lock(), 0),
            )
            self._device_locks[device_id] = (lock, users + 1)
        acquired = False
        try:
            await lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                lock.release()
            with self._device_locks_guard:
                current = self._device_locks.get(device_id)
                if current is not None and current[0] is lock:
                    remaining = current[1] - 1
                    if remaining == 0:
                        self._device_locks.pop(device_id, None)
                    else:
                        self._device_locks[device_id] = (lock, remaining)

    def _attempt_publish(self, snapshot: OverviewSnapshot) -> bool:
        publish = getattr(self.publisher, "publish_overview", None)
        early_ack_window = object()
        with self._mid_lock:
            self._retire_device_mappings_locked(snapshot.device_id)
            self._early_ack_windows[early_ack_window] = set()
        try:
            mid = (
                publish(snapshot.device_id, snapshot.payload)
                if callable(publish)
                else None
            )
        except Exception:
            mid = None
        early_ack = False
        with self._mid_lock:
            early_mids = self._early_ack_windows.pop(early_ack_window, set())
            if mid is not None:
                mid_value = int(mid)
                early_ack = mid_value in early_mids
                if not early_ack:
                    self._mid_to_snapshot[mid_value] = (
                        snapshot.device_id,
                        snapshot.revision,
                    )
        if mid is None:
            delay = PUBLISH_BACKOFF_SECONDS[
                min(snapshot.publish_attempts, len(PUBLISH_BACKOFF_SECONDS) - 1)
            ]
            self.overview_store.mark_publish_attempt(
                snapshot.device_id,
                snapshot.revision,
                (self._now() + timedelta(seconds=delay)).isoformat(),
                "overview_publish_failed",
            )
            return False
        if early_ack:
            self._mark_published(
                (
                    snapshot.device_id,
                    snapshot.revision,
                )
            )
        else:
            self.overview_store.mark_publish_in_flight(
                snapshot.device_id,
                snapshot.revision,
                (
                    self._now()
                    + timedelta(seconds=PUBLISH_ACK_TIMEOUT_SECONDS)
                ).isoformat(),
            )
        return True

    def _retire_device_mappings_locked(self, device_id: str) -> None:
        stale_mids = [
            mid
            for mid, target in self._mid_to_snapshot.items()
            if target[0] == device_id
        ]
        for mid in stale_mids:
            self._mid_to_snapshot.pop(mid, None)
            self._retired_mids[mid] = None
            self._retired_mids.move_to_end(mid)
        while len(self._retired_mids) > _MAX_RETIRED_MIDS:
            self._retired_mids.popitem(last=False)

    async def _weather_card_for_refresh(
        self,
        device_id: str,
        date_text: str,
        reason: str,
    ) -> dict[str, object]:
        location = self.overview_store.get_location(device_id)
        if location is None:
            return self._empty_weather_card(date_text)
        country_code = str(location.get("country_code") or "CN")
        province = str(location["province"])
        city = str(location["city"])
        weather_lock_key = json.dumps(
            ["weather", country_code, province, city, date_text, self.weather_provider_name],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        async with self._serialize_device(weather_lock_key):
            cached = self._weather_from_location(location, date_text)
            if cached is not None:
                return self._weather_card(cached)
            retry_state = self.overview_store.get_weather_retry_state(
                province, city, date_text, self.weather_provider_name,
                country_code=country_code,
            )
            if retry_state is not None and reason != "manual_resync":
                return self._unavailable_weather_card(location, date_text)
            if self.weather_provider is not None:
                try:
                    weather = await self.query_daily_weather(
                        province,
                        city,
                        date_text,
                        country_code=country_code,
                    )
                    return self._weather_card(weather)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.overview_store.record_weather_failure(
                        province,
                        city,
                        date_text,
                        self.weather_provider_name,
                        "overview_weather_fetch_failed",
                        attempts=1,
                        next_attempt_at=(
                            self._now() + timedelta(seconds=600)
                        ).isoformat(),
                        country_code=country_code,
                    )
            return self._unavailable_weather_card(location, date_text)

    def _cached_weather_card(
        self, device_id: str | None, date_text: str
    ) -> dict[str, object]:
        if self.overview_store is None or device_id is None:
            return self._empty_weather_card(date_text)
        location = self.overview_store.get_location(device_id)
        if location is None:
            return self._empty_weather_card(date_text)
        weather = self._weather_from_location(location, date_text)
        if weather is None:
            return self._unavailable_weather_card(location, date_text)
        return self._weather_card(weather)

    def _weather_from_location(
        self, location: dict[str, object], date_text: str
    ) -> DailyWeather | None:
        return self.overview_store.get_daily_weather(
            str(location["province"]),
            str(location["city"]),
            date_text,
            self.weather_provider_name,
            country_code=str(location.get("country_code") or "CN"),
        )

    @staticmethod
    def _empty_weather_card(date_text: str) -> dict[str, object]:
        return {
            "configured": False,
            "available": False,
            "province": "",
            "city": "",
            "date": date_text,
            "summary": "天气位置未知",
            "detail": "可在小程序中设置城市",
            "fetched_at": "",
        }

    def _unavailable_weather_card(
        self, location: dict[str, object], date_text: str
    ) -> dict[str, object]:
        province = self._truncate(location.get("province"), _PLACE_TEXT_LIMIT)
        city = self._truncate(location.get("city"), _PLACE_TEXT_LIMIT)
        return {
            "configured": True,
            "available": False,
            "province": province,
            "city": city,
            "date": date_text,
            "summary": self._truncate(
                f"{city} · 天气暂不可用", _SUMMARY_TEXT_LIMIT
            ),
            "detail": "",
            "fetched_at": "",
        }

    def _weather_card(self, weather: DailyWeather) -> dict[str, object]:
        province = self._truncate(weather.province, _PLACE_TEXT_LIMIT)
        city = self._truncate(weather.city, _PLACE_TEXT_LIMIT)
        return {
            "configured": True,
            "available": True,
            "province": province,
            "city": city,
            "date": weather.date,
            "summary": self._truncate(
                f"{city} · {weather.weather_text}", _SUMMARY_TEXT_LIMIT
            ),
            "detail": self._truncate(
                "今日 "
                f"{self._temperature_text(weather.temperature_min_c)}～"
                f"{self._temperature_text(weather.temperature_max_c)}℃",
                _DETAIL_TEXT_LIMIT,
            ),
            "fetched_at": self._truncate(weather.fetched_at, 40),
        }

    def _wire_weather_card(
        self, card: dict[str, object]
    ) -> dict[str, object]:
        return {
            "configured": bool(card.get("configured")),
            "available": bool(card.get("available")),
            "province": self._truncate(card.get("province"), _PLACE_TEXT_LIMIT),
            "city": self._truncate(card.get("city"), _PLACE_TEXT_LIMIT),
            "date": self._truncate(card.get("date"), 10),
            "summary": self._truncate(card.get("summary"), _SUMMARY_TEXT_LIMIT),
            "detail": self._truncate(card.get("detail"), _DETAIL_TEXT_LIMIT),
            "fetched_at": self._truncate(card.get("fetched_at"), 40),
        }

    def _wire_course_card(
        self, card: dict[str, object]
    ) -> dict[str, object]:
        return {
            "configured": bool(card.get("configured")),
            "available_today": bool(card.get("available_today")),
            "title": self._truncate(card.get("title"), _TITLE_TEXT_LIMIT),
            "detail": self._truncate(card.get("detail"), _DETAIL_TEXT_LIMIT),
        }

    def _wire_todo_card(self, card: dict[str, object]) -> dict[str, object]:
        return {
            "configured": bool(card.get("configured")),
            "count": max(0, int(card.get("count") or 0)),
            "detail": self._truncate(card.get("detail"), _DETAIL_TEXT_LIMIT),
        }

    def _wire_companion_card(
        self, pet_status: dict[str, object]
    ) -> dict[str, object] | None:
        if "academicStage" not in pet_status:
            return None
        growth = pet_status.get("growthMoment")
        growth = growth if isinstance(growth, dict) else {}
        age = pet_status.get("xiaoxinAge")
        return {
            "xiaoxin_age": age if isinstance(age, int) and 1 <= age <= 4 else None,
            "academic_stage": self._truncate_utf8(
                pet_status.get("academicStage") or "unknown", 16
            ),
            "growth_moment_id": self._truncate_utf8(growth.get("momentId"), 64),
            "growth_summary": self._truncate_utf8(growth.get("safeSummary"), 63),
            "expression": "growth" if growth else "idle",
        }

    def _bound_device_for_user(
        self, user_id: str, *, device_id: str | None
    ) -> Any | None:
        if device_id:
            candidate = self.identity_store.get_device_by_device_id(device_id)
            if (
                candidate is not None
                and candidate.owner_user_id == user_id
                and candidate.bind_status == DEVICE_BOUND
            ):
                return candidate
            return None
        for candidate in self.identity_store.list_devices_for_user(user_id):
            if (
                candidate.owner_user_id == user_id
                and candidate.bind_status == DEVICE_BOUND
            ):
                return candidate
        return None

    def _miniprogram_device_payload(self, device: Any | None) -> dict[str, object]:
        if device is None:
            return {
                "bound": False,
                "deviceId": "",
                "name": "",
                "state": "offline",
                "batteryLevel": None,
                "batteryPercent": None,
                "firmwareVersion": "",
                "lastSeenAt": "",
                "boundAt": "",
            }
        runtime_state = {}
        if self.registry is not None:
            runtime_state = {
                item["device_id"]: item for item in self.registry.list_devices()
            }.get(device.device_id, {})
        battery_percent = runtime_state.get("battery_percent")
        if battery_percent is None:
            battery_percent = runtime_state.get("battery")
        return {
            "bound": True,
            "deviceId": device.device_id,
            "name": device.display_name,
            "state": runtime_state.get("state", "offline"),
            "batteryLevel": runtime_state.get("battery_level"),
            "batteryPercent": battery_percent,
            "firmwareVersion": runtime_state.get("firmware_version", ""),
            "lastSeenAt": runtime_state.get("last_seen_at")
            or device.last_seen_at
            or "",
            "boundAt": getattr(device, "bound_at", None) or "",
        }

    def _todo_overview_card(
        self, user_id: str, date_text: str
    ) -> dict[str, object]:
        todos = self.identity_store.list_student_todos(user_id)
        pending = [todo for todo in todos if todo["status"] == "pending"]
        future_boundary = f"{date_text}T00:00:00"
        upcoming = [
            todo
            for todo in pending
            if str(todo.get("due_at") or "") >= future_boundary
        ]
        next_todo = upcoming[0] if upcoming else None
        if next_todo is None:
            return {
                "configured": bool(todos),
                "count": len(pending),
                "detail": "暂无待提醒事项" if todos else "暂无待办",
                "nextTodo": None,
            }
        return {
            "configured": True,
            "count": len(pending),
            "detail": self._todo_detail_text(next_todo),
            "nextTodo": self._student_todo_payload(next_todo),
        }

    def _course_overview_card(
        self,
        curriculum: dict[str, object],
        *,
        has_configured_courses: bool,
    ) -> dict[str, object]:
        next_course = curriculum.get("nextCourse")
        if not isinstance(next_course, dict):
            return {
                "configured": has_configured_courses,
                "available_today": False,
                "title": "暂无课程",
                "detail": (
                    "今日无课"
                    if has_configured_courses
                    else "在小程序中添加课表后显示"
                ),
            }
        starts_at = str(next_course.get("startsAt") or "").strip()
        start_section = int(next_course.get("startSection") or 0)
        end_section = int(next_course.get("endSection") or 0)
        section_text = (
            f"第{start_section}-{end_section}节"
            if start_section and end_section
            else ""
        )
        title_suffix = starts_at or section_text
        classroom = str(next_course.get("classroom") or "").strip()
        detail_parts = [part for part in (classroom, section_text) if part]
        title = str(next_course.get("title") or "").strip()
        return {
            "configured": True,
            "available_today": True,
            "title": f"{title} {title_suffix}".strip()
            if title_suffix
            else title,
            "detail": " · ".join(detail_parts),
            "course": next_course,
        }

    @staticmethod
    def _course_starts_after(course: dict[str, object], now_local: datetime) -> bool:
        starts_at = str(course.get("startsAt") or "").strip()
        try:
            start_time = datetime.strptime(starts_at, "%H:%M").time()
        except ValueError:
            return True
        course_start = datetime.combine(
            now_local.date(), start_time, tzinfo=COURSE_LOCAL_TZ
        )
        return course_start > now_local

    @staticmethod
    def _student_semester_payload(semester: dict[str, Any]) -> dict[str, object]:
        return {
            "label": semester["label"],
            "startDate": semester["start_date"],
            "totalWeeks": semester["total_weeks"],
        }

    @staticmethod
    def _student_course_payload(course: dict[str, Any]) -> dict[str, object]:
        return {
            "id": course["id"],
            "title": course["title"],
            "classroom": course["classroom"],
            "teacher": course["teacher"],
            "weekday": course["weekday"],
            "startSection": course["start_section"],
            "endSection": course["end_section"],
            "weekRange": course["week_range"],
            "startsAt": course["starts_at"],
            "endsAt": course["ends_at"],
            "notes": course["notes"],
        }

    @staticmethod
    def _student_todo_payload(todo: dict[str, Any]) -> dict[str, object]:
        return {
            "id": todo["id"],
            "title": todo["title"],
            "dueAt": todo["due_at"],
            "notes": todo["notes"],
            "status": todo["status"],
            "reminderStatus": todo["reminder_status"],
            "source": todo["source"],
            "sourceDeviceId": todo["source_device_id"],
            "createdAt": todo["created_at"],
            "updatedAt": todo["updated_at"],
        }

    @staticmethod
    def _todo_detail_text(todo: dict[str, Any]) -> str:
        due_at = str(todo.get("due_at") or "").strip()
        time_text = due_at[11:16] if len(due_at) >= 16 and "T" in due_at else due_at
        title = str(todo.get("title") or "").strip()
        return f"{time_text} {title}".strip()

    def _today_reminder_count(self, user_id: str, date_text: str) -> int:
        return sum(
            1
            for todo in self.identity_store.list_student_todos(user_id)
            if todo["status"] == "pending"
            and str(todo.get("due_at") or "").startswith(f"{date_text}T")
        )

    def _personal_pet_status(self, user_id: str, date_text: str) -> dict[str, object]:
        pet = self.identity_store.get_personal_pet_for_user(user_id)
        if pet is None:
            return {
                "petId": "",
                "lifecycleStatus": "pending",
                "companionStartedAt": None,
                "companionDays": 0,
                "companionYear": 0,
                "anniversaryDate": None,
            }

        try:
            projection = project_personal_pet(
                pet,
                as_of=datetime.strptime(date_text, "%Y-%m-%d").date(),
            )
        except ValueError as exc:
            logger.warning(
                "invalid personal pet lifecycle for pet %s: %s",
                pet.id,
                exc,
            )
            return {
                "petId": pet.id,
                "lifecycleStatus": pet.status,
                "companionStartedAt": pet.companion_started_at,
                "companionDays": 0,
                "companionYear": 0,
                "anniversaryDate": None,
            }
        status = {
            "petId": projection.pet_id,
            "lifecycleStatus": projection.status,
            "companionStartedAt": projection.companion_started_at,
            "companionDays": projection.companion_days,
            "companionYear": projection.companion_year,
            "anniversaryDate": projection.anniversary_date,
        }
        companion = self._companion_status_projection(user_id, pet.id)
        if companion is not None:
            status.update(companion)
        return status

    def _companion_status_projection(
        self,
        user_id: str,
        pet_id: str,
    ) -> dict[str, object] | None:
        if self.companion_mind is None:
            return None
        subjects = tuple(
            item
            for item in self.identity_store.list_memory_subjects_for_user(user_id)
            if item.kind == "user_speaker" and item.merged_into_subject_id is None
        )
        if len(subjects) != 1:
            return None
        profile = self.identity_store.get_student_profile_for_user(user_id)
        subject = build_companion_subject_context(
            owner_user_id=user_id,
            pet_id=pet_id,
            memory_subject_id=subjects[0].id,
            subject_kind=subjects[0].kind,
            raw_grade=profile.get("grade") if profile is not None else None,
        )
        projection = self.companion_mind.project(
            CompanionProjectionRequest(
                subject=subject,
                surface="miniprogram",
                now=self._now_iso(timespec="seconds"),
            )
        )
        growth = projection.payload.get("growth_moment")
        growth_payload = None
        if isinstance(growth, dict):
            growth_payload = {
                "momentId": growth.get("moment_id"),
                "fromStage": growth.get("from_stage"),
                "toStage": growth.get("to_stage"),
                "xiaoxinAge": growth.get("xiaoxin_age"),
                "safeSummary": growth.get("safe_summary"),
                "occurredAt": growth.get("occurred_at"),
            }
        return {
            "academicStage": subject.academic_stage,
            "xiaoxinAge": projection.xiaoxin_age,
            "relationshipStage": projection.relationship_stage,
            "growthMoment": growth_payload,
        }

    @staticmethod
    def _course_active_in_week(
        course: dict[str, Any], current_week: int | None
    ) -> bool:
        if current_week is None:
            return False
        week_range = str(course.get("week_range") or "").strip()
        if not week_range:
            return True
        if week_range == "非本周" or "非本" in week_range:
            return False
        numeric_ranges = [
            (int(match.group(1)), int(match.group(2) or match.group(1)))
            for match in re.finditer(
                r"(\d+)(?:\s*[-~—至到]\s*(\d+))?", week_range
            )
        ]
        numeric_ranges = [
            (start, end)
            for start, end in numeric_ranges
            if start > 0 and end >= start
        ]
        if numeric_ranges:
            return any(start <= current_week <= end for start, end in numeric_ranges)
        return True

    @classmethod
    def _course_conflict_map(
        cls, courses: list[dict[str, Any]]
    ) -> dict[str, set[str]]:
        conflicts = {str(course["id"]): set() for course in courses}
        for index, left in enumerate(courses):
            for right in courses[index + 1 :]:
                if cls._course_sections_overlap(left, right):
                    left_id = str(left["id"])
                    right_id = str(right["id"])
                    conflicts[left_id].add(right_id)
                    conflicts[right_id].add(left_id)
        return conflicts

    @staticmethod
    def _course_sections_overlap(
        left: dict[str, Any], right: dict[str, Any]
    ) -> bool:
        left_start = int(left.get("start_section") or 0)
        left_end = int(left.get("end_section") or 0)
        right_start = int(right.get("start_section") or 0)
        right_end = int(right.get("end_section") or 0)
        if min(left_start, left_end, right_start, right_end) <= 0:
            return False
        return max(left_start, right_start) <= min(left_end, right_end)

    def _require_sync_dependencies(self) -> None:
        if self.overview_store is None:
            raise RuntimeError("overview store unavailable")

    def _now(self) -> datetime:
        value = self._clock()
        if value.utcoffset() is None:
            raise ValueError("clock must return a datetime with a UTC offset")
        return value.astimezone(timezone.utc)

    def _now_iso(self, *, timespec: str = "microseconds") -> str:
        return self._now().isoformat(timespec=timespec)

    def _today_text(self) -> str:
        return local_date_text(self._clock())

    @staticmethod
    def _temperature_text(value: float) -> str:
        number = float(value)
        return str(int(number)) if number.is_integer() else f"{number:g}"

    @staticmethod
    def _truncate(value: object, limit: int) -> str:
        return str(value or "").strip()[:limit]

    @staticmethod
    def _truncate_utf8(value: object, byte_limit: int) -> str:
        encoded = str(value or "").strip().encode("utf-8")
        if len(encoded) <= byte_limit:
            return encoded.decode("utf-8")
        return encoded[:byte_limit].decode("utf-8", errors="ignore")

    @staticmethod
    def _ensure_payload_size(payload: dict[str, object]) -> None:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_PAYLOAD_BYTES:
            raise ValueError("overview payload exceeds 2048 bytes")


class DisabledOverviewSyncService(OverviewSyncService):
    """Projection-only service used when Overview MQTT is feature-gated off."""

    overview_enabled = False

    def __init__(self, *, identity_store: Any, registry: Any | None = None) -> None:
        super().__init__(identity_store=identity_store, registry=registry)

    @staticmethod
    def _disabled(device_id: str = "", reason: str = "") -> dict[str, object]:
        return {
            "device_id": device_id,
            "reason": reason,
            "revision": None,
            "changed": False,
            "publish_attempted": False,
            "publish_accepted": False,
            "publish_state": "disabled",
            "payload": None,
            "discarded": False,
            "location_changed": False,
            "refreshed": False,
            "error_code": "overview_mqtt_disabled",
        }

    async def refresh_device(self, device_id, reason, date_text=None):
        return self._disabled(device_id, reason)

    async def clear_unbound_device(self, device_id, reason):
        return self._disabled(device_id, reason)

    async def refresh_user_devices(self, user_id, reason, date_text=None):
        return [self._disabled("", reason)]

    async def observe_device_ip(self, device_id, public_ip, reason):
        return self._disabled(device_id, reason)

    async def set_manual_location_for_user(
        self, user_id, device_id, province, city, reason
    ):
        return self._disabled(device_id, reason)

    async def set_automatic_location_for_user(self, user_id, device_id, reason):
        return self._disabled(device_id, reason)

    async def drain_pending(self):
        return self._disabled()

    def begin_publish_session(self, generation):
        return None

    def reset_publish_session(self):
        return None

    def handle_publish_ack(self, mid, generation=None):
        return None


__all__ = [
    "MAX_PAYLOAD_BYTES",
    "DisabledOverviewSyncService",
    "OverviewSyncService",
    "PUBLISH_ACK_TIMEOUT_SECONDS",
    "PUBLISH_BACKOFF_SECONDS",
]
