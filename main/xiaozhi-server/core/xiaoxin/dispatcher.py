from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from config.logger import setup_logging
from core.xiaoxin.control_types import (
    XiaoxinControlEventRequest,
    XiaoxinDeliveryRecord,
    XiaoxinDeliveryState,
    XiaoxinDeviceState,
    XiaoxinFailureReason,
    XiaoxinEvent,
    build_xiaoxin_event_payload,
)
from core.xiaoxin.delivery_store import XiaoxinDeliveryStore
from core.xiaoxin.registry import XiaoxinDeviceRegistry
from core.xiaoxin.tts_delivery import TtsAttemptError, TtsAttemptOutcome

TAG = __name__


class DispatcherStoppedError(RuntimeError):
    pass


class XiaoxinEventDispatcher:
    def __init__(
        self,
        registry: XiaoxinDeviceRegistry,
        store: XiaoxinDeliveryStore,
        doorbell_client: Any,
        wake_timeout_seconds: float = 15,
        ack_timeout_seconds: float = 10,
        retry_delays_seconds: tuple[float, ...] = (2, 5, 15, 30),
    ):
        self.registry = registry
        self.store = store
        self.doorbell_client = doorbell_client
        self.wake_timeout_seconds = wake_timeout_seconds
        self.ack_timeout_seconds = ack_timeout_seconds
        self.retry_delays_seconds = tuple(retry_delays_seconds or (2, 5, 15, 30))
        self._event_tasks: dict[str, asyncio.Task[Any]] = {}
        self._tts_tasks: dict[str, asyncio.Task[Any]] = {}
        self._connection_tasks: dict[str, asyncio.Task[Any]] = {}
        self._event_ack_futures: dict[str, asyncio.Future[None]] = {}
        self._tts_outcomes: dict[tuple[str, str], asyncio.Future[TtsAttemptOutcome]] = (
            {}
        )
        self._device_tts_locks: dict[str, asyncio.Lock] = {}
        self._stopping = False
        self.logger = setup_logging()

    async def submit(
        self, request: XiaoxinControlEventRequest
    ) -> XiaoxinDeliveryRecord:
        if self._stopping:
            raise DispatcherStoppedError("xiaoxin event dispatcher is stopped")
        payload = build_xiaoxin_event_payload("pending", request)
        record = self.store.create(request, payload)
        delivery_coro = (
            self._run_event_delivery(record.delivery_id)
            if request.speak
            else self._deliver(record.delivery_id)
        )
        self._track_task(self._event_tasks, record.delivery_id, delivery_coro)
        return record

    async def submit_companion_initiative(
        self,
        device_id: str,
        projection: dict[str, object],
    ) -> XiaoxinDeliveryRecord:
        if projection.get("eligible") is not True:
            raise ValueError("companion initiative projection is not eligible")
        decision_id = projection.get("decision_id")
        content_brief = projection.get("content_brief")
        hardware_expression = projection.get("hardware_expression")
        if not isinstance(decision_id, str) or not decision_id.strip():
            raise ValueError("companion initiative requires decision_id")
        if not isinstance(content_brief, str) or not content_brief.strip():
            raise ValueError("companion initiative requires content_brief")
        if not isinstance(hardware_expression, dict):
            raise ValueError("companion initiative requires hardware_expression")
        return await self.submit(
            XiaoxinControlEventRequest(
                device_id=device_id,
                event=XiaoxinEvent.NOTIFICATION,
                title="小芯陪伴",
                body=content_brief,
                tag=f"companion:{decision_id}",
                priority=1,
                speak=True,
                speak_text=content_brief,
                hardware_expression=dict(hardware_expression),
            )
        )

    async def wait_for_delivery_task(self, delivery_id: str) -> None:
        while True:
            tasks = [
                task
                for task in (
                    self._event_tasks.get(delivery_id),
                    self._tts_tasks.get(delivery_id),
                )
                if task is not None
            ]
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)
            if (
                delivery_id not in self._event_tasks
                and delivery_id not in self._tts_tasks
            ):
                return

    async def handle_ack(self, device_id: str, ack: dict[str, Any], conn: Any) -> None:
        delivery_id = str(ack.get("delivery_id", "")).strip()
        record = self.store.get(delivery_id)
        if record is None or record.device_id != device_id:
            return
        if _is_terminal_state(record.state):
            return

        state = str(ack.get("state", "")).strip()
        reason = _failure_reason_or_none(ack.get("reason"))

        if state == XiaoxinDeliveryState.FAILED.value:
            if record.request.speak:
                future = self._event_ack_futures.get(delivery_id)
                if future is not None and not future.done():
                    future.set_exception(
                        RuntimeError((reason or XiaoxinFailureReason.UNKNOWN).value)
                    )
                return
            self.store.transition(
                delivery_id,
                XiaoxinDeliveryState.FAILED,
                reason or XiaoxinFailureReason.UNKNOWN,
                source="device",
                details={"ack": dict(ack)},
            )
            return

        if record.request.speak and state != XiaoxinDeliveryState.DEVICE_RECEIVED.value:
            return

        if state != XiaoxinDeliveryState.DEVICE_RECEIVED.value:
            try:
                ack_state = XiaoxinDeliveryState(state)
            except ValueError:
                return
            self.store.transition(
                delivery_id,
                ack_state,
                source="device",
                details={"ack": dict(ack)},
            )
            return

        current = self.store.mark_event_acknowledged(delivery_id, ack)
        future = self._event_ack_futures.get(delivery_id)
        if future is not None and not future.done():
            future.set_result(None)
        self._maybe_complete(current)

    def mark_tts_done(self, delivery_id: str, sentence_id: str) -> None:
        self._resolve_tts_outcome(delivery_id, TtsAttemptOutcome(sentence_id, "done"))

    def mark_tts_attempt_failed(
        self, delivery_id: str, sentence_id: str, reason: str
    ) -> None:
        self._resolve_tts_outcome(
            delivery_id, TtsAttemptOutcome(sentence_id, "failed", reason)
        )

    def mark_tts_legacy_unverified(self, delivery_id: str, sentence_id: str) -> None:
        self._resolve_tts_outcome(
            delivery_id,
            TtsAttemptOutcome(sentence_id, "legacy_unverified"),
        )

    def _resolve_tts_outcome(
        self, delivery_id: str, outcome: TtsAttemptOutcome
    ) -> None:
        record = self.store.get(delivery_id)
        if record is None or record.control_tts_sentence_id != outcome.sentence_id:
            return
        future = self._tts_outcomes.get((delivery_id, outcome.sentence_id))
        if future is not None and not future.done():
            future.set_result(outcome)

    async def _run_event_delivery(self, delivery_id: str) -> None:
        failures = 0
        while not self._stopping:
            record = self.store.require(delivery_id)
            if self._expire_if_needed(record):
                return
            if record.event_acknowledged:
                self._maybe_complete(record)
                return
            if self.registry.get_connection(record.device_id) is None:
                self.store.transition(
                    delivery_id,
                    XiaoxinDeliveryState.RETRY_WAIT,
                    details={"event_failure": "device_offline"},
                )
            conn = await self._wait_for_connection_before_expiry(record)
            if conn is None:
                return
            record = self.store.require(delivery_id)
            if self._expire_if_needed(record):
                return
            future = asyncio.get_running_loop().create_future()
            self._event_ack_futures[delivery_id] = future
            try:
                await conn.send_xiaoxin_event(record.payload)
                current = self._record_after_possible_terminal_trim(delivery_id, record)
                if current is None:
                    return
                self._ensure_tts_task(current)
                if current.event_acknowledged or _is_terminal_state(current.state):
                    self._maybe_complete(current)
                    return
                self.store.transition(
                    delivery_id,
                    XiaoxinDeliveryState.SENT,
                    details={"event_attempt": failures + 1},
                )
                await asyncio.wait_for(future, self.ack_timeout_seconds)
                current = self._record_after_possible_terminal_trim(delivery_id, record)
                if current is not None:
                    self._maybe_complete(current)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                current = self._record_after_possible_terminal_trim(delivery_id, record)
                if current is None or _is_terminal_state(current.state):
                    return
                failures += 1
                failure_reason = (
                    "ack_timeout"
                    if isinstance(exc, asyncio.TimeoutError)
                    else "ack_or_connection_failure"
                )
                self.store.transition(
                    delivery_id,
                    XiaoxinDeliveryState.RETRY_WAIT,
                    details={"event_failure": failure_reason},
                )
                if not await self._wait_retry_delay_before_expiry(
                    record, self._retry_delay(failures)
                ):
                    return
            finally:
                if self._event_ack_futures.get(delivery_id) is future:
                    self._event_ack_futures.pop(delivery_id, None)

    def _record_after_possible_terminal_trim(
        self,
        delivery_id: str,
        last_known: XiaoxinDeliveryRecord,
    ) -> XiaoxinDeliveryRecord | None:
        current = self.store.get(delivery_id)
        if current is None and not _is_terminal_state(last_known.state):
            raise KeyError(delivery_id)
        return current

    def _ensure_tts_task(self, record: XiaoxinDeliveryRecord) -> None:
        current = self.store.get(record.delivery_id)
        if (
            self._stopping
            or current is None
            or _is_terminal_state(current.state)
            or current.tts_state in {"done", "legacy_unverified"}
            or not current.request.speak
            or record.delivery_id in self._tts_tasks
        ):
            return
        self._track_task(
            self._tts_tasks,
            current.delivery_id,
            self._run_tts_delivery(current.delivery_id),
        )

    async def _run_tts_delivery(self, delivery_id: str) -> None:
        failures = 0
        while not self._stopping:
            record = self.store.require(delivery_id)
            if self._expire_if_needed(record):
                return
            if record.tts_state in {"done", "legacy_unverified"}:
                self._maybe_complete(record)
                return
            lease = self._device_tts_locks.setdefault(record.device_id, asyncio.Lock())
            async with lease:
                record = self.store.require(delivery_id)
                if self._expire_if_needed(record):
                    return
                if record.tts_state in {"done", "legacy_unverified"}:
                    self._maybe_complete(record)
                    return
                if self.registry.get_connection(record.device_id) is None:
                    self.store.transition(
                        delivery_id,
                        XiaoxinDeliveryState.RETRY_WAIT,
                        details={"tts_failure": "device_offline"},
                    )
                conn = await self._wait_for_connection_before_expiry(record)
                if conn is None:
                    return
                record = self.store.require(delivery_id)
                if self._expire_if_needed(record):
                    return
                sentence_id = uuid.uuid4().hex
                self.store.begin_tts_attempt(delivery_id, sentence_id)
                key = (delivery_id, sentence_id)
                future = asyncio.get_running_loop().create_future()
                self._tts_outcomes[key] = future
                try:
                    await conn.speak_from_control_console(
                        record.request.speak_text,
                        delivery_id,
                        sentence_id,
                    )
                    outcome = await future
                except asyncio.CancelledError:
                    raise
                except TtsAttemptError as exc:
                    outcome = self._prefer_resolved_outcome(
                        future,
                        TtsAttemptOutcome(
                            sentence_id=sentence_id,
                            status="failed",
                            reason=exc.reason,
                        ),
                    )
                except (ConnectionError, RuntimeError):
                    outcome = self._prefer_resolved_outcome(
                        future,
                        TtsAttemptOutcome(
                            sentence_id=sentence_id,
                            status="failed",
                            reason="connection_closed_before_done",
                        ),
                    )
                except Exception:
                    outcome = self._prefer_resolved_outcome(
                        future,
                        TtsAttemptOutcome(
                            sentence_id=sentence_id,
                            status="failed",
                            reason="start_failed",
                        ),
                    )
                finally:
                    if self._tts_outcomes.get(key) is future:
                        self._tts_outcomes.pop(key, None)

                if outcome.status == "done":
                    self.store.mark_tts_done(delivery_id, sentence_id)
                    self._maybe_complete(self.store.require(delivery_id))
                    return
                if outcome.status == "legacy_unverified":
                    self.store.mark_tts_legacy_unverified(delivery_id, sentence_id)
                    self._maybe_complete(self.store.require(delivery_id))
                    return

                failures += 1
                failure_reason = outcome.reason or "unknown_tts_failure"
                self.store.mark_tts_attempt_failed(
                    delivery_id, sentence_id, failure_reason
                )
                self.logger.bind(tag=TAG).warning(
                    "delivery_id={} attempt={} sentence_id={} "
                    "tts_state=retry_wait delivery_retry={} "
                    "failure_reason={}".format(
                        delivery_id,
                        self.store.require(delivery_id).tts_attempt_count,
                        sentence_id,
                        failures,
                        failure_reason,
                    )
                )
            if not await self._wait_retry_delay_before_expiry(
                record, self._retry_delay(failures)
            ):
                return

    @staticmethod
    def _prefer_resolved_outcome(
        future: asyncio.Future[TtsAttemptOutcome],
        fallback: TtsAttemptOutcome,
    ) -> TtsAttemptOutcome:
        if future.done() and not future.cancelled():
            return future.result()
        return fallback

    def _maybe_complete(self, record: XiaoxinDeliveryRecord) -> None:
        if record.state == XiaoxinDeliveryState.DONE:
            return
        tts_complete = not record.request.speak or record.tts_state in {
            "done",
            "legacy_unverified",
        }
        if record.event_acknowledged and tts_complete:
            self.store.transition(record.delivery_id, XiaoxinDeliveryState.DONE)

    async def _wait_for_connection(self, device_id: str) -> Any:
        conn = self.registry.get_connection(device_id)
        if conn is not None:
            return conn
        task = self._connection_tasks.get(device_id)
        if task is not None and task.done():
            if self._connection_tasks.get(device_id) is task:
                self._connection_tasks.pop(device_id, None)
            task = None
        if task is None:
            task = self._track_task(
                self._connection_tasks,
                device_id,
                self._run_connection_wait(device_id),
            )
        return await asyncio.shield(task)

    async def _wait_for_connection_before_expiry(
        self, record: XiaoxinDeliveryRecord
    ) -> Any | None:
        remaining = self._remaining_ttl_seconds(record)
        if remaining is None:
            return await self._wait_for_connection(record.device_id)
        if remaining <= 0:
            self._expire_if_needed(record)
            return None
        try:
            return await asyncio.wait_for(
                self._wait_for_connection(record.device_id),
                timeout=remaining,
            )
        except asyncio.TimeoutError:
            current = self.store.get(record.delivery_id)
            if current is not None:
                self._expire_if_needed(current)
            return None

    async def _wait_retry_delay_before_expiry(
        self, record: XiaoxinDeliveryRecord, delay_seconds: float
    ) -> bool:
        remaining = self._remaining_ttl_seconds(record)
        if remaining is None:
            await self._wait_connected_delay(record.device_id, delay_seconds)
            return True
        if remaining <= 0:
            self._expire_if_needed(record)
            return False
        try:
            await asyncio.wait_for(
                self._wait_connected_delay(record.device_id, delay_seconds),
                timeout=remaining,
            )
        except asyncio.TimeoutError:
            current = self.store.get(record.delivery_id)
            if current is not None:
                self._expire_if_needed(current)
            return False
        current = self.store.get(record.delivery_id)
        return current is not None and not self._expire_if_needed(current)

    @staticmethod
    def _remaining_ttl_seconds(record: XiaoxinDeliveryRecord) -> float | None:
        ttl_ms = int(record.request.ttl_ms or 0)
        if ttl_ms <= 0:
            return None
        created_at = datetime.fromisoformat(
            record.created_at.replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        expires_at = created_at + timedelta(milliseconds=ttl_ms)
        return (expires_at - datetime.now(timezone.utc)).total_seconds()

    def _expire_if_needed(self, record: XiaoxinDeliveryRecord) -> bool:
        if _is_terminal_state(record.state):
            return True
        remaining = self._remaining_ttl_seconds(record)
        if remaining is None or remaining > 0:
            return False
        self.store.transition(
            record.delivery_id,
            XiaoxinDeliveryState.FAILED,
            XiaoxinFailureReason.EXPIRED,
            details={"ttl_ms": record.request.ttl_ms},
        )
        return True

    async def _run_connection_wait(self, device_id: str) -> Any:
        while not self._stopping:
            conn = self.registry.get_connection(device_id)
            if conn is not None:
                return conn
            try:
                if self._can_attempt_wake():
                    self.doorbell_client.publish_wake(device_id)
                conn = await self.registry.wait_for_connected(device_id, 30.0)
                if conn is not None:
                    return conn
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.bind(tag=TAG).warning(
                    "device_id={} connection_wait_retry=true failure_reason={}".format(
                        device_id, type(exc).__name__
                    )
                )
                await asyncio.sleep(0.05)
        raise asyncio.CancelledError

    async def _wait_connected_delay(self, device_id: str, delay_seconds: float) -> None:
        remaining = max(delay_seconds, 0.0)
        if remaining == 0:
            await asyncio.sleep(0)
            return
        loop = asyncio.get_running_loop()
        while remaining > 0 and not self._stopping:
            conn = await self._wait_for_connection(device_id)
            slice_seconds = min(remaining, 0.05)
            started = loop.time()
            await asyncio.sleep(slice_seconds)
            if self.registry.get_connection(device_id) is conn:
                remaining -= max(loop.time() - started, 0.0)
        if self._stopping:
            raise asyncio.CancelledError

    def _retry_delay(self, failure_count: int) -> float:
        index = min(max(failure_count - 1, 0), len(self.retry_delays_seconds) - 1)
        return self.retry_delays_seconds[index]

    async def stop(self) -> None:
        self._stopping = True
        owned_delivery_ids = {
            *self._event_tasks.keys(),
            *self._tts_tasks.keys(),
        }
        tasks = [
            *self._event_tasks.values(),
            *self._tts_tasks.values(),
            *self._connection_tasks.values(),
        ]
        for delivery_id in owned_delivery_ids:
            record = self.store.get(delivery_id)
            if record is not None and not _is_terminal_state(record.state):
                self.store.transition(
                    delivery_id,
                    XiaoxinDeliveryState.FAILED,
                    XiaoxinFailureReason.DISPATCHER_STOPPED,
                    details={"shutdown": "dispatcher_stopped"},
                )
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for future in self._event_ack_futures.values():
            if not future.done():
                future.cancel()
        for future in self._tts_outcomes.values():
            if not future.done():
                future.cancel()
        self._event_tasks.clear()
        self._tts_tasks.clear()
        self._connection_tasks.clear()
        self._event_ack_futures.clear()
        self._tts_outcomes.clear()
        self._device_tts_locks.clear()

    async def _deliver(self, delivery_id: str) -> None:
        record = self.store.require(delivery_id)
        if self._expire_if_needed(record):
            return
        state = self.registry.get_device_state(record.device_id)

        if state == XiaoxinDeviceState.CONNECTED:
            await self._send(record, self.registry.get_connection(record.device_id))
            return
        if state == XiaoxinDeviceState.WAKEABLE or self._can_attempt_wake():
            await self._wake_then_send(record)
            return
        self.store.transition(
            delivery_id,
            XiaoxinDeliveryState.FAILED,
            XiaoxinFailureReason.DEVICE_OFFLINE,
        )

    def _can_attempt_wake(self) -> bool:
        can_attempt = getattr(self.doorbell_client, "can_attempt_wake", None)
        return bool(can_attempt and can_attempt())

    async def _wake_then_send(self, record: XiaoxinDeliveryRecord) -> None:
        if self._expire_if_needed(record):
            return
        self.store.transition(record.delivery_id, XiaoxinDeliveryState.WAKING)
        if not self.doorbell_client.publish_wake(record.device_id):
            self.store.transition(
                record.delivery_id,
                XiaoxinDeliveryState.FAILED,
                XiaoxinFailureReason.SEND_FAILED,
            )
            return
        remaining = self._remaining_ttl_seconds(record)
        wait_timeout = self.wake_timeout_seconds
        if remaining is not None:
            wait_timeout = min(wait_timeout, max(remaining, 0.0))
        conn = await self.registry.wait_for_connected(
            record.device_id, wait_timeout
        )
        if conn is None:
            if self._expire_if_needed(record):
                return
            self.store.transition(
                record.delivery_id,
                XiaoxinDeliveryState.FAILED,
                XiaoxinFailureReason.WAKE_TIMEOUT,
            )
            return
        await self._send(record, conn)

    async def _send(self, record: XiaoxinDeliveryRecord, conn: Any | None) -> None:
        if self._expire_if_needed(record):
            return
        if conn is None:
            self.store.transition(
                record.delivery_id,
                XiaoxinDeliveryState.FAILED,
                XiaoxinFailureReason.DEVICE_OFFLINE,
            )
            return
        try:
            await conn.send_xiaoxin_event(record.payload)
        except Exception as exc:
            self.logger.bind(tag=TAG).error(f"Xiaoxin event send failed: {exc}")
            self.store.transition(
                record.delivery_id,
                XiaoxinDeliveryState.FAILED,
                XiaoxinFailureReason.SEND_FAILED,
            )
            return
        self.store.transition(record.delivery_id, XiaoxinDeliveryState.SENT)
        self.store.transition(record.delivery_id, XiaoxinDeliveryState.DONE)

    def _track_task(
        self,
        tasks: dict[str, asyncio.Task[Any]],
        delivery_id: str,
        coro: Any,
    ) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        tasks[delivery_id] = task
        task.add_done_callback(
            lambda finished_task: self._discard_completed_task(
                tasks, delivery_id, finished_task
            )
        )
        return task

    def _discard_completed_task(
        self,
        tasks: dict[str, asyncio.Task[Any]],
        delivery_id: str,
        finished_task: asyncio.Task[Any],
    ) -> None:
        if tasks.get(delivery_id) is finished_task:
            tasks.pop(delivery_id, None)
        if finished_task.cancelled():
            return
        error = finished_task.exception()
        if error is not None:
            self.logger.bind(tag=TAG).error(
                "Xiaoxin dispatcher background task failed "
                f"key={delivery_id}: {error}"
            )


def _failure_reason_or_none(value: Any) -> XiaoxinFailureReason | None:
    if not value:
        return None
    try:
        return XiaoxinFailureReason(str(value))
    except ValueError:
        return XiaoxinFailureReason.UNKNOWN


def _is_terminal_state(state: XiaoxinDeliveryState) -> bool:
    return state in {XiaoxinDeliveryState.FAILED, XiaoxinDeliveryState.DONE}
