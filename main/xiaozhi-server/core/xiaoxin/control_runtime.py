from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable

from config.config_loader import get_project_dir
from core.xiaoxin.activation_store import XiaoxinActivationStore
from core.xiaoxin.companion import (
    CompanionControlCommand,
    CompanionMind,
    CompanionProjectionRequest,
    CompanionSubjectContext,
    MemoryInterpreter,
)
from core.xiaoxin.companion.adapters import (
    LLMInitiativeComposer,
    LLMMemoryInterpretationModel,
    LLMReflectionModel,
)
from core.xiaoxin.companion.initiative import SafeInitiativeComposer
from core.xiaoxin.companion.observation_ingress import CompanionObservationIngress
from core.xiaoxin.companion.store import CompanionStore
from core.xiaoxin.compliance import (
    Capability,
    ComplianceConfig,
    CompliancePolicyService,
    ComplianceStore,
    GlobalCompanionMode,
)
from core.xiaoxin.course_reminder_scheduler import XiaoxinCourseReminderScheduler
from core.xiaoxin.control_types import XiaoxinDeliveryState
from core.xiaoxin.delivery_store import XiaoxinDeliveryStore
from core.xiaoxin.dispatcher import XiaoxinEventDispatcher
from core.xiaoxin.doorbell_client import DoorbellMqttSettings, XiaoxinDoorbellClient
from core.xiaoxin.doorbell_credentials import DoorbellCredentialStore
from core.xiaoxin.identity import (
    XiaoxinAuthService,
    XiaoxinIdentityResolver,
    XiaoxinIdentityStore,
)
from core.xiaoxin.initiative_delivery import XiaoxinInitiativeDeliveryPort
from core.xiaoxin.identity.models import DEVICE_BOUND, SPEAKER_CONFIRMED
from core.xiaoxin.local_time import local_datetime
from core.xiaoxin.llm_adapter import LLMChatAdapter
from core.xiaoxin.notification_history_store import XiaoxinNotificationHistoryStore
from core.xiaoxin.overview.providers import (
    AmapWeatherProvider,
    OpenMeteoWeatherProvider,
    PconlineIpLocationProvider,
)
from core.xiaoxin.overview.service import (
    DisabledOverviewSyncService,
    OverviewSyncService,
)
from core.xiaoxin.overview.store import XiaoxinOverviewStore
from core.xiaoxin.registry import XiaoxinDeviceRegistry
from core.xiaoxin.tenant_config import load_tenant_config
from core.xiaoxin.todo_reminder_scheduler import XiaoxinTodoReminderScheduler

LOGGER = logging.getLogger(__name__)
WEATHER_RETRY_BACKOFF_SECONDS = (600, 1800, 7200)
BOOT_CHECKIN_RESET_REASONS = frozenset({"poweron", "brownout", "external"})
BOOT_ID_MAX_LENGTH = 128


class _OverviewSessionPublisher:
    def __init__(self, doorbell_client: object, service: object) -> None:
        self._doorbell_client = doorbell_client
        self._service = service

    def publish_overview(
        self,
        device_id: str,
        payload: dict[str, object],
    ) -> int | None:
        ensure_session = getattr(
            self._doorbell_client,
            "ensure_publish_session",
            None,
        )
        publish_in_session = getattr(
            self._doorbell_client,
            "publish_overview_in_session",
            None,
        )
        if callable(ensure_session) and callable(publish_in_session):
            generation = ensure_session()
            if generation is None:
                return None
            self._service.begin_publish_session(generation)
            return publish_in_session(generation, device_id, payload)

        generation = getattr(
            self._doorbell_client,
            "publish_session_generation",
            None,
        )
        self._service.begin_publish_session(generation)
        publish = getattr(self._doorbell_client, "publish_overview", None)
        return publish(device_id, payload) if callable(publish) else None


@dataclass
class XiaoxinControlRuntime:
    registry: XiaoxinDeviceRegistry
    store: XiaoxinDeliveryStore
    notification_history_store: XiaoxinNotificationHistoryStore
    activation_store: XiaoxinActivationStore
    doorbell_client: XiaoxinDoorbellClient
    doorbell_credential_store: DoorbellCredentialStore
    dispatcher: XiaoxinEventDispatcher
    identity_store: XiaoxinIdentityStore
    identity_resolver: XiaoxinIdentityResolver
    auth_service: XiaoxinAuthService
    course_reminder_scheduler: XiaoxinCourseReminderScheduler
    todo_reminder_scheduler: XiaoxinTodoReminderScheduler
    overview_store: XiaoxinOverviewStore | None
    overview_service: OverviewSyncService
    overview_weather_provider: object | None
    compliance_service: CompliancePolicyService | None = None
    course_reminder_scheduler_enabled: bool = False
    todo_reminder_scheduler_enabled: bool = False
    reminder_tick_seconds: float = 60.0
    overview_enabled: bool = False
    overview_retry_tick_seconds: float = 1.0
    overview_daily_refresh_hour: int = 0
    overview_daily_refresh_minute: int = 5
    overview_clock: Callable[[], datetime] = local_datetime
    companion_mind: CompanionMind | None = None
    companion_worker_enabled: bool = False
    companion_worker_tick_seconds: float = 30.0
    companion_worker_limit: int = 20
    companion_clock: Callable[[], datetime] = local_datetime
    companion_store: CompanionStore | None = None
    companion_boot_checkin_enabled: bool = False
    companion_boot_checkin_grace_seconds: float = 90.0
    observation_ingress: CompanionObservationIngress | None = None
    _reminder_task: asyncio.Task | None = None
    _companion_task: asyncio.Task | None = None
    _initiative_feedback_tasks: set[asyncio.Task] = field(default_factory=set)
    _todo_reminder_feedback_tasks: set[asyncio.Task] = field(default_factory=set)
    _overview_task: asyncio.Task | None = None
    _overview_loop: asyncio.AbstractEventLoop | None = None
    _overview_wakeup: asyncio.Event | None = None
    _overview_listeners_registered: bool = False
    _overview_last_daily_refresh_date: str | None = None

    def __post_init__(self) -> None:
        if self.observation_ingress is None and self.companion_mind is not None:
            self.observation_ingress = CompanionObservationIngress(
                self.identity_store,
                self.companion_mind,
            )

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        if self.overview_enabled:
            self._register_overview_listeners()
        self.doorbell_client.start(loop)
        if self.overview_enabled:
            self.overview_service.begin_publish_session(
                getattr(
                    self.doorbell_client,
                    "publish_session_generation",
                    None,
                )
            )
            self._start_overview_loop(loop)
        if (
            self.todo_reminder_scheduler_enabled
            or self.course_reminder_scheduler_enabled
        ):
            self._start_reminder_loop(loop)
        if self.companion_worker_enabled and self.companion_mind is not None:
            self._start_companion_loop(loop)

    async def stop(self) -> None:
        await self._stop_overview_loop()
        await self._stop_reminder_loop()
        await self._stop_companion_loop()
        await self._stop_todo_reminder_feedback_tasks()
        await self.dispatcher.stop()
        await self._stop_initiative_feedback_tasks()
        try:
            self.doorbell_client.stop()
        finally:
            self.overview_service.reset_publish_session()

    def note_device_seen(self, device_id: str) -> None:
        if device_id:
            try:
                self.identity_store.upsert_seen_device(device_id)
            except Exception:
                LOGGER.exception("Failed to record seen Xiaoxin device %s", device_id)

    def note_device_boot(
        self,
        device_id: str,
        *,
        boot_id: str | None = None,
        reset_reason: str | None = None,
        boot_event_id: str | None = None,
        boot_reason: str | None = None,
    ) -> str | None:
        if not self.companion_boot_checkin_enabled:
            return None
        companion_store = self.companion_store
        if companion_store is None:
            return None

        if boot_id is not None:
            if not isinstance(boot_id, str):
                return None
            normalized_boot_id = boot_id.strip()
            normalized_reset_reason = (
                reset_reason.strip().lower() if isinstance(reset_reason, str) else ""
            )
            if (
                not normalized_boot_id
                or len(normalized_boot_id) > BOOT_ID_MAX_LENGTH
                or normalized_reset_reason not in BOOT_CHECKIN_RESET_REASONS
            ):
                return None
            boot_event_id = f"device:{device_id}:boot:{normalized_boot_id}"
            boot_reason = normalized_reset_reason
        elif boot_event_id and boot_reason == "ota_request":
            # OTA is an explicit server-side lifecycle event, not a WebSocket
            # connection-derived boot signal.
            pass
        else:
            return None
        device = self.identity_store.get_device_by_device_id(device_id)
        if (
            device is None
            or device.owner_user_id is None
            or device.bind_status != DEVICE_BOUND
        ):
            return None
        pet = self.identity_store.get_personal_pet_for_user(device.owner_user_id)
        if pet is None or pet.status != "active":
            return None
        subjects = self.identity_store.list_memory_subjects_for_user(
            device.owner_user_id
        )
        subject = None
        for item in subjects:
            if (
                item.device_id != device_id
                or item.kind != "user_speaker"
                or item.merged_into_subject_id is not None
                or item.speaker_profile_id is None
            ):
                continue
            profile = self.identity_store.get_speaker_profile(
                item.speaker_profile_id
            )
            if profile is not None and profile.status == SPEAKER_CONFIRMED:
                subject = item
                break
        if subject is None:
            return None
        epoch = companion_store.get_active_epoch(
            owner_user_id=device.owner_user_id,
            pet_id=pet.id,
        )
        if epoch is None:
            return None
        now = self.companion_clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("companion boot clock must be timezone-aware")
        due_at = (
            now
            + timedelta(
                seconds=max(float(self.companion_boot_checkin_grace_seconds), 0.0)
            )
        ).isoformat()
        return companion_store.create_boot_checkin(
            boot_event_id=boot_event_id,
            device_id=device_id,
            owner_user_id=device.owner_user_id,
            pet_id=pet.id,
            memory_subject_id=subject.id,
            relationship_epoch_id=epoch.epoch_id,
            boot_reason=boot_reason,
            occurred_at=now.isoformat(),
            due_at=due_at,
            now=now.isoformat(),
        )

    def observe_todo_reminder_tts_done(
        self,
        delivery_id: str,
        sentence_id: str,
    ) -> None:
        if self.observation_ingress is None:
            return
        record = self.store.get(delivery_id)
        if record is None or record.request.event.value != "todo_reminder":
            return
        device = self.identity_store.get_device_by_device_id(record.device_id)
        if device is None or device.owner_user_id is None:
            return
        self.identity_store.repair_todo_reminder_outcomes({delivery_id})
        tag = str(record.request.tag or "")
        todo_id = tag.removeprefix("todo:") if tag.startswith("todo:") else ""
        payload = {
            "todo_id": todo_id,
            "delivery_id": delivery_id,
            "delivery_status": record.state.value,
            "tts_state": record.tts_state,
            "sentence_id": sentence_id,
        }
        events = (
            (
                "reminder_delivered",
                "一项待办提醒已送达设备。",
            ),
            (
                "reminder_tts_completed",
                "一项待办提醒已完成可靠语音播放。",
            ),
        )
        for kind, safe_summary in events:
            try:
                self.observation_ingress.observe_user_event(
                    user_id=device.owner_user_id,
                    idempotency_key=f"{kind}:{delivery_id}:{sentence_id}",
                    kind=kind,
                    source_kind="todo_reminder_delivery",
                    source_ref=delivery_id,
                    occurred_at=record.updated_at,
                    payload=payload,
                    safe_summary=safe_summary,
                )
            except Exception:
                LOGGER.exception(
                    "Failed to record todo reminder delivery observation",
                    extra={
                        "delivery_id": delivery_id,
                        "observation_kind": kind,
                    },
                )

    async def dispatch_companion_initiative(
        self,
        *,
        device_id: str,
        subject: CompanionSubjectContext,
        now: str,
        initiative_enabled: bool = True,
        quiet_hours_active: bool = False,
    ) -> tuple[object, object | None]:
        if self.companion_mind is None:
            raise RuntimeError("companion mind is unavailable")
        higher_priority_pending = any(
            record.device_id == device_id
            and record.request.priority > 1
            and record.state
            not in {XiaoxinDeliveryState.DONE, XiaoxinDeliveryState.FAILED}
            for record in self.store.list_recent()
        )
        projection = self.companion_mind.project(
            CompanionProjectionRequest(
                subject=subject,
                surface="initiative",
                now=now,
                initiative_enabled=initiative_enabled,
                quiet_hours_active=quiet_hours_active,
                device_available=(
                    self.registry.get_connection(device_id) is not None
                ),
                higher_priority_pending=higher_priority_pending,
            )
        )
        if projection.payload.get("eligible") is not True:
            return projection, None
        decision_id = projection.payload.get("decision_id")
        if not isinstance(decision_id, str) or not decision_id:
            raise ValueError("eligible companion initiative requires decision_id")
        delivery_projection = self.companion_mind.project(
            CompanionProjectionRequest(
                subject=subject,
                surface="initiative",
                now=now,
                initiative_decision_id=decision_id,
            )
        )
        if delivery_projection.payload.get("eligible") is not True:
            return projection, None
        try:
            delivery = await self.dispatcher.submit_companion_initiative(
                device_id,
                dict(delivery_projection.payload),
            )
        except Exception:
            self._apply_initiative_delivery_failure(
                subject=subject,
                decision_id=decision_id,
                delivery_id="submit",
            )
            raise
        feedback_task = asyncio.create_task(
            self._record_initiative_delivery_failure(
                subject=subject,
                decision_id=decision_id,
                delivery_id=delivery.delivery_id,
            )
        )
        self._initiative_feedback_tasks.add(feedback_task)
        feedback_task.add_done_callback(self._initiative_feedback_tasks.discard)
        return projection, delivery

    async def _record_initiative_delivery_failure(
        self,
        *,
        subject: CompanionSubjectContext,
        decision_id: str,
        delivery_id: str,
    ) -> None:
        try:
            await self.dispatcher.wait_for_delivery_task(delivery_id)
            record = self.store.get(delivery_id)
            if record is None or record.state != XiaoxinDeliveryState.FAILED:
                return
            self._apply_initiative_delivery_failure(
                subject=subject,
                decision_id=decision_id,
                delivery_id=delivery_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception(
                "Failed to record companion initiative delivery outcome",
                extra={"decision_id": decision_id, "delivery_id": delivery_id},
            )

    def _apply_initiative_delivery_failure(
        self,
        *,
        subject: CompanionSubjectContext,
        decision_id: str,
        delivery_id: str,
    ) -> None:
        if self.companion_mind is None:
            return
        now = self.companion_clock().isoformat()
        try:
            self.companion_mind.apply_control(
                CompanionControlCommand(
                    action="record_initiative_feedback",
                    subject=subject,
                    payload={
                        "decision_id": decision_id,
                        "outcome": "delivery_failed",
                        "now": now,
                        "idempotency_key": (
                            f"initiative-delivery-failed:{decision_id}:{delivery_id}"
                        ),
                    },
                )
            )
        except Exception:
            LOGGER.exception(
                "Failed to persist companion initiative delivery failure",
                extra={"decision_id": decision_id, "delivery_id": delivery_id},
            )

    async def _stop_initiative_feedback_tasks(self) -> None:
        tasks = tuple(self._initiative_feedback_tasks)
        self._initiative_feedback_tasks.clear()
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)

    def _watch_todo_reminder_delivery(
        self,
        *,
        todo_id: str,
        delivery_id: str,
    ) -> None:
        task = asyncio.create_task(
            self._record_todo_reminder_delivery_failure(
                todo_id=todo_id,
                delivery_id=delivery_id,
            )
        )
        self._todo_reminder_feedback_tasks.add(task)
        task.add_done_callback(self._todo_reminder_feedback_tasks.discard)

    async def _record_todo_reminder_delivery_failure(
        self,
        *,
        todo_id: str,
        delivery_id: str,
    ) -> None:
        try:
            await self.dispatcher.wait_for_delivery_task(delivery_id)
            record = self.store.get(delivery_id)
            if (
                record is None
                or record.request.event.value != "todo_reminder"
                or record.state != XiaoxinDeliveryState.FAILED
            ):
                return
            tag = str(record.request.tag or "")
            tagged_todo_id = (
                tag.removeprefix("todo:") if tag.startswith("todo:") else ""
            )
            if not tagged_todo_id or tagged_todo_id != todo_id:
                return
            device = self.identity_store.get_device_by_device_id(record.device_id)
            if (
                device is None
                or device.owner_user_id is None
                or self.observation_ingress is None
            ):
                return
            failure_reason = (
                record.reason.value if record.reason is not None else "unknown"
            )
            self.observation_ingress.observe_user_event(
                user_id=device.owner_user_id,
                idempotency_key=f"reminder-delivery-failed:{delivery_id}",
                kind="reminder_delivery_failed",
                source_kind="todo_reminder_delivery",
                source_ref=delivery_id,
                occurred_at=record.updated_at,
                payload={
                    "todo_id": todo_id,
                    "delivery_id": delivery_id,
                    "delivery_status": "failed",
                    "failure_reason": failure_reason,
                },
                safe_summary="一项待办提醒投递失败。",
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception(
                "Failed to record todo reminder delivery failure",
                extra={"todo_id": todo_id, "delivery_id": delivery_id},
            )

    async def _stop_todo_reminder_feedback_tasks(self) -> None:
        tasks = tuple(self._todo_reminder_feedback_tasks)
        self._todo_reminder_feedback_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _start_reminder_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._reminder_task is not None and not self._reminder_task.done():
            return
        self._reminder_task = loop.create_task(self._run_reminder_loop())

    async def _stop_reminder_loop(self) -> None:
        task = self._reminder_task
        self._reminder_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run_reminder_loop(self) -> None:
        tick_seconds = max(float(self.reminder_tick_seconds), 0.001)
        while True:
            now = datetime.now(timezone.utc)
            if self.todo_reminder_scheduler_enabled:
                try:
                    dispatched_todos = (
                        await self.todo_reminder_scheduler.dispatch_due_todos(now)
                    )
                    for dispatched in dispatched_todos:
                        todo_id = str(dispatched.get("todo_id") or "").strip()
                        delivery_id = str(
                            dispatched.get("delivery_id") or ""
                        ).strip()
                        if todo_id and delivery_id:
                            self._watch_todo_reminder_delivery(
                                todo_id=todo_id,
                                delivery_id=delivery_id,
                            )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOGGER.exception("Xiaoxin todo reminder scheduler tick failed")

            if self.course_reminder_scheduler_enabled:
                try:
                    await self.course_reminder_scheduler.dispatch_due_courses(now)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOGGER.exception("Xiaoxin course reminder scheduler tick failed")

            await asyncio.sleep(tick_seconds)

    def _start_companion_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._companion_task is not None and not self._companion_task.done():
            return
        self._companion_task = loop.create_task(self._run_companion_loop())

    async def _stop_companion_loop(self) -> None:
        task = self._companion_task
        self._companion_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run_companion_loop(self) -> None:
        tick_seconds = max(float(self.companion_worker_tick_seconds), 0.001)
        limit = max(int(self.companion_worker_limit), 1)
        while True:
            companion_mind = self.companion_mind
            if companion_mind is not None:
                try:
                    now = self.companion_clock()
                    if now.tzinfo is None or now.utcoffset() is None:
                        raise ValueError("companion worker clock must be timezone-aware")
                    await companion_mind.run_due_work(
                        now=now.isoformat(),
                        limit=limit,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOGGER.exception("Xiaoxin companion worker tick failed")
            await asyncio.sleep(tick_seconds)

    def _register_overview_listeners(self) -> None:
        if not self._overview_listeners_registered:
            self.doorbell_client.add_connect_listener(
                self._handle_overview_connect
            )
            self.doorbell_client.add_publish_ack_listener(
                self.overview_service.handle_publish_ack
            )
            self._overview_listeners_registered = True

    def _start_overview_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.overview_service.publisher = _OverviewSessionPublisher(
            self.doorbell_client,
            self.overview_service,
        )
        if self._overview_task is not None and not self._overview_task.done():
            return
        wakeup = asyncio.Event()
        self._overview_loop = loop
        self._overview_wakeup = wakeup
        self._overview_task = loop.create_task(
            self._run_overview_loop(wakeup)
        )

    def _handle_overview_connect(self) -> None:
        self.overview_service.begin_publish_session(
            getattr(
                self.doorbell_client,
                "publish_session_generation",
                None,
            )
        )
        self._signal_overview_wakeup()

    async def _stop_overview_loop(self) -> None:
        task = self._overview_task
        self._overview_task = None
        self._overview_loop = None
        self._overview_wakeup = None
        self.overview_service.publisher = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _signal_overview_wakeup(self) -> None:
        loop = self._overview_loop
        wakeup = self._overview_wakeup
        if loop is None or wakeup is None or not loop.is_running():
            return
        try:
            loop.call_soon_threadsafe(wakeup.set)
        except RuntimeError:
            return

    async def _run_overview_loop(self, wakeup: asyncio.Event) -> None:
        tick_seconds = max(float(self.overview_retry_tick_seconds), 0.001)
        while True:
            wakeup.clear()
            try:
                await self._run_overview_tick(self.overview_clock())
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Xiaoxin overview runtime tick failed")
            if wakeup.is_set():
                continue
            try:
                await asyncio.wait_for(wakeup.wait(), timeout=tick_seconds)
            except asyncio.TimeoutError:
                pass

    async def _run_overview_tick(self, now: datetime) -> None:
        if now.utcoffset() is None:
            raise ValueError("overview clock must return an aware datetime")
        now_utc = now.astimezone(timezone.utc)
        try:
            await self.overview_service.drain_pending()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Xiaoxin overview pending drain failed")

        try:
            due_retries = self.overview_store.list_due_weather_retries(
                now_utc.isoformat()
            )
        except Exception:
            LOGGER.exception("Xiaoxin overview weather retry scan failed")
            due_retries = []
        for entry in due_retries:
            try:
                refreshed = await self._refresh_weather_entry(entry, now_utc)
                if refreshed:
                    await self._refresh_devices_for_weather_entry(
                        entry,
                        "weather_retry",
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Xiaoxin overview weather retry failed")

        local_now = local_datetime(now)
        local_date = local_now.date().isoformat()
        refresh_time = time(
            hour=self.overview_daily_refresh_hour,
            minute=self.overview_daily_refresh_minute,
        )
        if (
            local_now.timetz().replace(tzinfo=None) >= refresh_time
            and self._overview_last_daily_refresh_date != local_date
        ):
            try:
                await self._run_daily_overview_refresh(local_date, now_utc)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Xiaoxin overview daily refresh failed")
            else:
                self._overview_last_daily_refresh_date = local_date

    async def _refresh_weather_entry(
        self,
        entry: dict[str, object],
        now_utc: datetime,
    ) -> bool:
        try:
            weather = await self.overview_weather_provider.daily(
                str(entry["province"]),
                str(entry["city"]),
                str(entry["date"]),
            )
            expected_country = str(entry.get("country_code") or "CN")
            if (
                weather.province != str(entry["province"])
                or weather.city != str(entry["city"])
                or weather.date != str(entry["date"])
                or weather.country_code != expected_country
            ):
                raise ValueError("weather response does not match retry entry")
            self.overview_store.put_daily_weather(
                weather,
                str(entry.get("provider") or "open_meteo"),
            )
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_weather_failure(entry, now_utc, str(exc))
            return False

    def _record_weather_failure(
        self,
        entry: dict[str, object],
        now_utc: datetime,
        error: str,
    ) -> None:
        attempts = int(entry.get("fetch_attempts") or 0) + 1
        next_attempt_at = None
        if attempts <= len(WEATHER_RETRY_BACKOFF_SECONDS):
            next_attempt_at = (
                now_utc
                + timedelta(
                    seconds=WEATHER_RETRY_BACKOFF_SECONDS[attempts - 1]
                )
            ).isoformat()
        self.overview_store.record_weather_failure(
            str(entry["province"]),
            str(entry["city"]),
            str(entry["date"]),
            str(entry.get("provider") or "open_meteo"),
            error,
            attempts,
            next_attempt_at,
            country_code=str(entry.get("country_code") or "CN"),
        )

    async def _refresh_devices_for_weather_entry(
        self,
        entry: dict[str, object],
        reason: str,
    ) -> None:
        expected_location = (
            str(entry.get("country_code") or "CN"),
            str(entry["province"]),
            str(entry["city"]),
        )
        for device in self.identity_store.list_all_devices():
            if (
                device.bind_status != DEVICE_BOUND
                or device.owner_user_id is None
            ):
                continue
            location = self.overview_store.get_location(device.device_id)
            if location is None:
                continue
            actual_location = (
                str(location.get("country_code") or "CN"),
                str(location["province"]),
                str(location["city"]),
            )
            if actual_location == expected_location:
                await self.overview_service.refresh_device(
                    device.device_id,
                    reason,
                    str(entry["date"]),
                )

    async def _run_daily_overview_refresh(
        self,
        local_date: str,
        now_utc: datetime,
    ) -> None:
        devices = [
            device
            for device in self.identity_store.list_all_devices()
            if device.bind_status == DEVICE_BOUND
            and device.owner_user_id is not None
        ]
        weather_groups: dict[
            tuple[str, str, str], list[object]
        ] = {}
        without_location = []
        for device in devices:
            location = self.overview_store.get_location(device.device_id)
            if location is None:
                without_location.append(device)
                continue
            key = (
                str(location.get("country_code") or "CN"),
                str(location["province"]),
                str(location["city"]),
            )
            weather_groups.setdefault(key, []).append(device)

        for device in without_location:
            await self.overview_service.refresh_device(
                device.device_id,
                "weather_day_changed",
                local_date,
            )

        for (country_code, province, city), grouped_devices in weather_groups.items():
            cached = self.overview_store.get_daily_weather(
                province,
                city,
                local_date,
                self.overview_service.weather_provider_name,
                country_code=country_code,
            )
            first, *remaining = grouped_devices
            await self.overview_service.refresh_device(
                first.device_id,
                "weather_day_changed",
                local_date,
            )
            if cached is None:
                cached = self.overview_store.get_daily_weather(
                    province,
                    city,
                    local_date,
                    self.overview_service.weather_provider_name,
                    country_code=country_code,
                )
                if cached is None:
                    self._record_weather_failure(
                        {
                            "province": province,
                            "city": city,
                            "country_code": country_code,
                            "date": local_date,
                            "provider": self.overview_service.weather_provider_name,
                            "fetch_attempts": 0,
                        },
                        now_utc,
                        "weather_fetch_failed",
                    )
            for device in remaining:
                await self.overview_service.refresh_device(
                    device.device_id,
                    "weather_day_changed",
                    local_date,
                )


def create_xiaoxin_control_runtime(config: dict) -> XiaoxinControlRuntime:
    control = config.get("xiaoxin_control", {}) or {}
    xiaoxin_runtime = config.get("xiaoxin_runtime", {}) or {}
    compliance_config = ComplianceConfig.from_mapping(
        config.get("xiaoxin_compliance", {}) or {}
    )
    tenant_config = load_tenant_config(config)
    registry = XiaoxinDeviceRegistry()
    identity_db = control.get("identity_db") or "data/xiaoxin_control.db"
    identity_db_path = Path(identity_db)
    if not identity_db_path.is_absolute():
        identity_db_path = Path(get_project_dir()) / identity_db_path
    identity_store = XiaoxinIdentityStore(identity_db_path)
    compliance_store = ComplianceStore(identity_db_path)
    compliance_service = CompliancePolicyService(
        compliance_store,
        compliance_config,
    )

    def compliance_capability_allowed(user_id: str, capability: str) -> bool:
        try:
            normalized_capability = Capability(capability)
        except ValueError:
            return False
        return compliance_service.require_capability(
            user_id,
            normalized_capability,
        ).allowed
    history_db = control.get("notification_history_db") or identity_db_path.with_name(
        "xiaoxin_notification_history.db"
    )
    history_db_path = Path(history_db)
    if not history_db_path.is_absolute():
        history_db_path = Path(get_project_dir()) / history_db_path
    notification_history_store = XiaoxinNotificationHistoryStore(
        history_db_path,
        limit=int(control.get("notification_history_limit", 500)),
    )
    _repair_todo_reminder_outcomes_from_history(
        identity_store,
        notification_history_store,
    )
    store = XiaoxinDeliveryStore(
        limit=int(control.get("delivery_history_limit", 100)),
        history_sink=notification_history_store,
    )
    activation_db = control.get("activation_db") or "data/xiaoxin_activation.db"
    activation_db_path = Path(activation_db)
    if not activation_db_path.is_absolute():
        activation_db_path = Path(get_project_dir()) / activation_db_path
    activation_store = XiaoxinActivationStore(activation_db_path)
    doorbell_credentials_db = (
        control.get("doorbell_credentials_db") or "data/xiaoxin_doorbell_credentials.db"
    )
    doorbell_credentials_db_path = Path(doorbell_credentials_db)
    if not doorbell_credentials_db_path.is_absolute():
        doorbell_credentials_db_path = (
            Path(get_project_dir()) / doorbell_credentials_db_path
        )
    doorbell_credential_store = DoorbellCredentialStore(doorbell_credentials_db_path)
    identity_resolver = XiaoxinIdentityResolver(identity_store)
    auth_service = XiaoxinAuthService(identity_store)
    doorbell_client = XiaoxinDoorbellClient(
        DoorbellMqttSettings.from_config(config),
        registry,
        tenant=tenant_config,
    )
    retry_delays_seconds = tuple(
        float(value) / 1000
        for value in config.get(
            "tts_delivery_retry_delays_ms",
            [2000, 5000, 15000, 30000],
        )
    )
    dispatcher = XiaoxinEventDispatcher(
        registry,
        store,
        doorbell_client,
        wake_timeout_seconds=float(control.get("wake_timeout_seconds", 15)),
        ack_timeout_seconds=float(control.get("ack_timeout_seconds", 10)),
        retry_delays_seconds=retry_delays_seconds,
    )
    course_reminder_scheduler = XiaoxinCourseReminderScheduler(
        identity_store, dispatcher
    )
    todo_reminder_scheduler = XiaoxinTodoReminderScheduler(
        identity_store,
        dispatcher,
        replay_window_minutes=float(
            control.get("todo_reminder_replay_window_minutes", 120)
        ),
    )
    overview_config = control.get("overview_mqtt", {}) or {}
    control_enabled = _config_bool(control.get("enabled", True))
    course_reminder_scheduler_enabled = control_enabled and _config_bool(
        control.get("course_reminder_scheduler_enabled", False)
    )
    todo_reminder_scheduler_enabled = control_enabled and _config_bool(
        control.get("todo_reminder_scheduler_enabled", False)
    )
    reminder_tick_seconds = max(
        float(
            control.get(
                "reminder_tick_seconds",
                control.get("todo_reminder_tick_seconds", 60),
            )
        ),
        0.001,
    )
    overview_enabled = control_enabled and _config_bool(
        overview_config.get("enabled", False)
    )
    overview_store = None
    overview_weather_provider = None
    if overview_enabled:
        overview_db = overview_config.get("db") or "data/xiaoxin_overview.db"
        overview_db_path = Path(overview_db)
        if not overview_db_path.is_absolute():
            overview_db_path = Path(get_project_dir()) / overview_db_path
        overview_store = XiaoxinOverviewStore(overview_db_path)
        weather_provider_name = str(
            overview_config.get("weather_provider") or "open_meteo"
        ).strip().lower().replace("-", "_")
        if weather_provider_name == "amap":
            amap_api_key = str(
                os.getenv("XIAOXIN_AMAP_API_KEY")
                or overview_config.get("amap_api_key")
                or ""
            )
            amap_api_host = str(
                os.getenv("XIAOXIN_AMAP_API_HOST")
                or overview_config.get("amap_api_host")
                or AmapWeatherProvider.DEFAULT_API_HOST
            )
            overview_weather_provider = AmapWeatherProvider(
                api_key=amap_api_key,
                api_host=amap_api_host,
                city_adcodes=(
                    overview_config.get("amap_city_adcodes") or {}
                ),
            )
        elif weather_provider_name in {"open_meteo", "openmeteo"}:
            weather_provider_name = "open_meteo"
            overview_weather_provider = OpenMeteoWeatherProvider()
        else:
            raise ValueError(
                f"unsupported overview weather provider: {weather_provider_name}"
            )
        ip_location_provider = PconlineIpLocationProvider()
        ip_hmac_secret = str(
            os.getenv("XIAOXIN_OVERVIEW_IP_HMAC_SECRET")
            or overview_config.get("ip_hmac_secret")
            or ""
        )
        overview_service = OverviewSyncService(
            identity_store=identity_store,
            overview_store=overview_store,
            weather_provider=overview_weather_provider,
            publisher=None,
            ip_location_provider=ip_location_provider,
            registry=registry,
            weather_provider_name=weather_provider_name,
            ip_hmac_key=(
                ip_hmac_secret.encode("utf-8") if ip_hmac_secret else None
            ),
        )
    else:
        overview_service = DisabledOverviewSyncService(
            identity_store=identity_store,
            registry=registry,
        )
    overview_retry_tick_seconds = max(
        float(overview_config.get("retry_tick_seconds", 1)),
        0.001,
    )
    overview_daily_refresh_hour = int(
        overview_config.get("daily_refresh_hour", 0)
    )
    overview_daily_refresh_minute = int(
        overview_config.get("daily_refresh_minute", 5)
    )
    companion_worker_enabled = _config_bool(
        xiaoxin_runtime.get("companion_worker_enabled", False)
    )
    initiative_scheduler_enabled = _config_bool(
        xiaoxin_runtime.get("companion_initiative_scheduler_enabled", False)
    )
    initiative_delivery_enabled = _config_bool(
        xiaoxin_runtime.get("companion_initiative_delivery_enabled", False)
    )
    if (
        compliance_config.enabled
        and compliance_config.companion_service_mode
        is GlobalCompanionMode.TOOL_ONLY
    ):
        initiative_scheduler_enabled = False
        initiative_delivery_enabled = False
    if initiative_delivery_enabled and not initiative_scheduler_enabled:
        raise ValueError(
            "companion initiative delivery requires the initiative scheduler"
        )
    if initiative_delivery_enabled and not companion_worker_enabled:
        raise ValueError(
            "companion initiative delivery requires the model worker"
        )
    companion_worker_tick_seconds = max(
        float(xiaoxin_runtime.get("companion_worker_tick_seconds", 30)),
        0.001,
    )
    companion_boot_checkin_enabled = _config_bool(
        xiaoxin_runtime.get("companion_boot_checkin_enabled", False)
    )
    companion_boot_checkin_grace_seconds = max(
        float(xiaoxin_runtime.get("companion_boot_checkin_grace_seconds", 90)),
        0.0,
    )
    companion_boot_checkin_delivery_window_seconds = max(
        float(
            xiaoxin_runtime.get(
                "companion_boot_checkin_delivery_window_seconds", 600
            )
        ),
        0.001,
    )
    presence_window_minutes = max(
        float(xiaoxin_runtime.get("companion_presence_window_minutes", 45)),
        0.001,
    )
    companion_store = CompanionStore(_companion_database_path(xiaoxin_runtime))
    initiative_composer = None
    initiative_delivery_port = None
    if initiative_scheduler_enabled:
        initiative_composer = SafeInitiativeComposer()
        initiative_delivery_port = XiaoxinInitiativeDeliveryPort(
            identity_store=identity_store,
            registry=registry,
            delivery_store=store,
            companion_store=companion_store,
            dispatcher=dispatcher,
            delivery_enabled=initiative_delivery_enabled,
            quiet_hours_start=str(
                xiaoxin_runtime.get("companion_initiative_quiet_hours_start", "22:30")
            ),
            quiet_hours_end=str(
                xiaoxin_runtime.get("companion_initiative_quiet_hours_end", "07:30")
            ),
        )
    interpreter_mode = str(
        xiaoxin_runtime.get("companion_memory_interpreter_mode", "off")
    )
    active_explicit_release_enabled = _config_bool(
        xiaoxin_runtime.get(
            "companion_memory_active_explicit_release_enabled", False
        )
    )
    turn_behavior_plan_mode = str(
        xiaoxin_runtime.get("companion_turn_behavior_plan_mode", "off")
    )
    companion_mind = CompanionMind(
        store=companion_store,
        memory_interpreter_mode=interpreter_mode,
        memory_active_explicit_release_enabled=active_explicit_release_enabled,
        turn_behavior_plan_mode=turn_behavior_plan_mode,
        initiative_composer=initiative_composer,
        initiative_delivery_port=initiative_delivery_port,
        capability_allowed=compliance_capability_allowed,
        initiative_followup_delay_minutes=float(
            xiaoxin_runtime.get("companion_initiative_followup_delay_minutes", 240)
        ),
        connection_bid_delays_minutes=_connection_bid_delays_minutes(
            xiaoxin_runtime
        ),
        connection_feedback_window_minutes=_connection_feedback_window_minutes(
            xiaoxin_runtime
        ),
        boot_checkin_delivery_window_seconds=(
            companion_boot_checkin_delivery_window_seconds
        ),
        presence_window_minutes=presence_window_minutes,
    )
    companion_worker_available = initiative_scheduler_enabled
    if companion_worker_enabled:
        try:
            companion_mind = _create_companion_worker_mind(
                config,
                xiaoxin_runtime,
                store=companion_store,
                initiative_composer=initiative_composer,
                initiative_delivery_port=initiative_delivery_port,
                capability_allowed=compliance_capability_allowed,
            )
            companion_worker_available = True
        except Exception as exc:
            companion_worker_available = (
                initiative_scheduler_enabled and not initiative_delivery_enabled
            )
            companion_mind = CompanionMind(
                store=companion_store,
                memory_interpreter_mode=interpreter_mode,
                memory_active_explicit_release_enabled=(
                    active_explicit_release_enabled
                ),
                turn_behavior_plan_mode=turn_behavior_plan_mode,
                initiative_composer=(
                    None if initiative_delivery_enabled else initiative_composer
                ),
                initiative_delivery_port=(
                    None
                    if initiative_delivery_enabled
                    else initiative_delivery_port
                ),
                capability_allowed=compliance_capability_allowed,
                initiative_followup_delay_minutes=float(
                    xiaoxin_runtime.get(
                        "companion_initiative_followup_delay_minutes", 240
                    )
                ),
                connection_bid_delays_minutes=_connection_bid_delays_minutes(
                    xiaoxin_runtime
                ),
                connection_feedback_window_minutes=(
                    _connection_feedback_window_minutes(xiaoxin_runtime)
                ),
                boot_checkin_delivery_window_seconds=(
                    companion_boot_checkin_delivery_window_seconds
                ),
                presence_window_minutes=presence_window_minutes,
            )
            LOGGER.error(
                "Xiaoxin companion worker initialization failed: %s",
                type(exc).__name__,
            )
    if not 0 <= overview_daily_refresh_hour <= 23:
        raise ValueError("overview daily refresh hour must be between 0 and 23")
    if not 0 <= overview_daily_refresh_minute <= 59:
        raise ValueError("overview daily refresh minute must be between 0 and 59")
    overview_service.companion_mind = companion_mind
    return XiaoxinControlRuntime(
        registry,
        store,
        notification_history_store,
        activation_store,
        doorbell_client,
        doorbell_credential_store,
        dispatcher,
        identity_store,
        identity_resolver,
        auth_service,
        course_reminder_scheduler,
        todo_reminder_scheduler,
        overview_store,
        overview_service,
        overview_weather_provider,
        compliance_service=compliance_service,
        course_reminder_scheduler_enabled=course_reminder_scheduler_enabled,
        todo_reminder_scheduler_enabled=todo_reminder_scheduler_enabled,
        reminder_tick_seconds=reminder_tick_seconds,
        overview_enabled=overview_enabled,
        overview_retry_tick_seconds=overview_retry_tick_seconds,
        overview_daily_refresh_hour=overview_daily_refresh_hour,
        overview_daily_refresh_minute=overview_daily_refresh_minute,
        companion_worker_enabled=companion_worker_available,
        companion_worker_tick_seconds=companion_worker_tick_seconds,
        companion_mind=companion_mind,
        companion_store=companion_store,
        companion_boot_checkin_enabled=companion_boot_checkin_enabled,
        companion_boot_checkin_grace_seconds=companion_boot_checkin_grace_seconds,
    )


def _repair_todo_reminder_outcomes_from_history(
    identity_store: XiaoxinIdentityStore,
    history_store: XiaoxinNotificationHistoryStore,
) -> int:
    candidate_ids = identity_store.list_pending_student_todo_delivery_ids()
    states = history_store.get_delivery_states(candidate_ids)
    completed_ids = {
        delivery_id
        for delivery_id, state in states.items()
        if state == XiaoxinDeliveryState.DONE.value
    }
    return identity_store.repair_todo_reminder_outcomes(completed_ids)


def _create_companion_worker_mind(
    config: dict,
    xiaoxin_runtime: dict,
    *,
    store: CompanionStore | None = None,
    initiative_composer=None,
    initiative_delivery_port=None,
    capability_allowed=None,
) -> CompanionMind:
    from core.utils import llm as llm_utils

    selected_modules = config.get("selected_module", {}) or {}
    selected_llm = (
        xiaoxin_runtime.get("companion_worker_llm")
        or selected_modules.get("LLM")
    )
    llm_configs = config.get("LLM", {}) or {}
    if not isinstance(selected_llm, str) or selected_llm not in llm_configs:
        raise ValueError("companion worker requires a configured LLM module")
    provider_config = llm_configs[selected_llm]
    if not isinstance(provider_config, dict):
        raise ValueError("companion worker LLM config must be an object")
    provider_type = str(provider_config.get("type") or selected_llm)
    provider = llm_utils.create_instance(provider_type, provider_config)

    timeout_seconds = float(
        xiaoxin_runtime.get("companion_reflection_timeout_seconds", 20)
    )
    reflection_model = LLMReflectionModel(
        LLMChatAdapter(provider, "companion-reflection-worker"),
        timeout_seconds=timeout_seconds,
    )
    interpreter_mode = str(
        xiaoxin_runtime.get("companion_memory_interpreter_mode", "off")
    )
    memory_interpreter = None
    if interpreter_mode != "off":
        memory_interpreter = MemoryInterpreter(
            LLMMemoryInterpretationModel(
                LLMChatAdapter(provider, "companion-memory-interpreter"),
                timeout_seconds=timeout_seconds,
            )
        )
    if initiative_delivery_port is not None:
        initiative_composer = LLMInitiativeComposer(
            LLMChatAdapter(provider, "companion-initiative-composer"),
            timeout_seconds=float(
                xiaoxin_runtime.get("companion_initiative_timeout_seconds", 10)
            ),
        )
    return CompanionMind(
        store=store or CompanionStore(_companion_database_path(xiaoxin_runtime)),
        reflection_model=reflection_model,
        memory_interpreter=memory_interpreter,
        memory_interpreter_mode=interpreter_mode,
        memory_active_explicit_release_enabled=_config_bool(
            xiaoxin_runtime.get(
                "companion_memory_active_explicit_release_enabled", False
            )
        ),
        turn_behavior_plan_mode=str(
            xiaoxin_runtime.get("companion_turn_behavior_plan_mode", "off")
        ),
        initiative_composer=initiative_composer,
        initiative_delivery_port=initiative_delivery_port,
        capability_allowed=capability_allowed,
        initiative_followup_delay_minutes=float(
            xiaoxin_runtime.get("companion_initiative_followup_delay_minutes", 240)
        ),
        connection_bid_delays_minutes=_connection_bid_delays_minutes(xiaoxin_runtime),
        connection_feedback_window_minutes=_connection_feedback_window_minutes(
            xiaoxin_runtime
        ),
        boot_checkin_delivery_window_seconds=max(
            float(
                xiaoxin_runtime.get(
                    "companion_boot_checkin_delivery_window_seconds", 600
                )
            ),
            0.001,
        ),
        presence_window_minutes=max(
            float(xiaoxin_runtime.get("companion_presence_window_minutes", 45)),
            0.001,
        ),
    )


def _companion_database_path(xiaoxin_runtime: dict) -> Path:
    database_path = Path(
        xiaoxin_runtime.get("companion_db_path")
        or "data/xiaoxin_companion.db"
    )
    if not database_path.is_absolute():
        database_path = Path(get_project_dir()) / database_path
    return database_path


def _connection_bid_delays_minutes(
    xiaoxin_runtime: dict,
) -> dict[str, float]:
    return {
        "reserved": float(
            xiaoxin_runtime.get(
                "companion_connection_bid_reserved_delay_minutes", 4320
            )
        ),
        "timely": float(
            xiaoxin_runtime.get(
                "companion_connection_bid_timely_delay_minutes", 2880
            )
        ),
        "proactive": float(
            xiaoxin_runtime.get(
                "companion_connection_bid_proactive_delay_minutes", 1440
            )
        ),
    }


def _connection_feedback_window_minutes(xiaoxin_runtime: dict) -> float:
    return float(
        xiaoxin_runtime.get("companion_connection_feedback_window_minutes", 30)
    )


def _config_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
