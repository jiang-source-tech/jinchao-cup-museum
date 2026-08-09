from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any

from core.xiaoxin.control_types import (
    XiaoxinControlEventRequest,
    XiaoxinDeliveryRecord,
    XiaoxinDeliveryState,
    XiaoxinDeliveryTimelineEntry,
    XiaoxinFailureReason,
    new_delivery_id,
    utc_now_iso,
)

LOGGER = logging.getLogger(__name__)


class XiaoxinDeliveryStore:
    def __init__(self, limit: int = 100, history_sink: Any | None = None):
        self.limit = limit
        self.history_sink = history_sink
        self._records: OrderedDict[str, XiaoxinDeliveryRecord] = OrderedDict()
        self._condition = asyncio.Condition()

    def create(
        self, request: XiaoxinControlEventRequest, payload: dict[str, Any]
    ) -> XiaoxinDeliveryRecord:
        now = utc_now_iso()
        record = XiaoxinDeliveryRecord(
            delivery_id=new_delivery_id(),
            device_id=request.device_id,
            event=request.event,
            payload=dict(payload),
            request=request,
            state=XiaoxinDeliveryState.CREATED,
            reason=None,
            created_at=now,
            updated_at=now,
            timeline=[
                XiaoxinDeliveryTimelineEntry(
                    state=XiaoxinDeliveryState.CREATED,
                    at=now,
                    source="server",
                )
            ],
        )
        record.payload["delivery_id"] = record.delivery_id
        self._records[record.delivery_id] = record
        self._trim()
        self._save_history(record)
        self._notify()
        return record

    def transition(
        self,
        delivery_id: str,
        state: XiaoxinDeliveryState,
        reason: XiaoxinFailureReason | None = None,
        source: str = "server",
        details: dict[str, Any] | None = None,
    ) -> XiaoxinDeliveryRecord:
        record = self.require(delivery_id)
        now = utc_now_iso()
        record.state = state
        record.reason = reason
        record.updated_at = now
        record.timeline.append(
            XiaoxinDeliveryTimelineEntry(
                state=state,
                at=now,
                reason=reason,
                source=source,
                details=dict(details) if details is not None else {},
            )
        )
        self._records.move_to_end(delivery_id)
        self._save_history(record)
        self._trim()
        self._notify()
        return record

    def mark_event_acknowledged(
        self, delivery_id: str, ack: dict[str, Any]
    ) -> XiaoxinDeliveryRecord:
        record = self.require(delivery_id)
        if record.event_acknowledged:
            return record
        record.event_acknowledged = True
        return self.transition(
            delivery_id,
            XiaoxinDeliveryState.DEVICE_RECEIVED,
            source="device",
            details={"ack": dict(ack)},
        )

    def begin_tts_attempt(self, delivery_id: str, sentence_id: str) -> int:
        record = self.require(delivery_id)
        record.tts_attempt_count += 1
        record.control_tts_sentence_id = sentence_id
        record.tts_state = "preparing"
        record.tts_last_failure_reason = None
        record.tts_playback_mode = None
        self.transition(
            delivery_id,
            XiaoxinDeliveryState.SPEAKING,
            details={
                "attempt": record.tts_attempt_count,
                "sentence_id": sentence_id,
                "tts_state": "preparing",
            },
        )
        return record.tts_attempt_count

    def mark_tts_attempt_failed(
        self, delivery_id: str, sentence_id: str, reason: str
    ) -> bool:
        record = self.require(delivery_id)
        if record.control_tts_sentence_id != sentence_id:
            return False
        record.tts_state = "retry_wait"
        record.tts_last_failure_reason = reason
        record.tts_playback_mode = None
        self.transition(
            delivery_id,
            XiaoxinDeliveryState.RETRY_WAIT,
            details={
                "attempt": record.tts_attempt_count,
                "sentence_id": sentence_id,
                "failure_reason": reason,
            },
        )
        return True

    def mark_tts_done(self, delivery_id: str, sentence_id: str) -> bool:
        record = self.require(delivery_id)
        if record.control_tts_sentence_id != sentence_id:
            return False
        record.tts_state = "done"
        record.tts_playback_mode = "reliable"
        record.tts_last_failure_reason = None
        self._save_field_mutation(record)
        return True

    def mark_tts_legacy_unverified(self, delivery_id: str, sentence_id: str) -> bool:
        record = self.require(delivery_id)
        if record.control_tts_sentence_id != sentence_id:
            return False
        record.tts_state = "legacy_unverified"
        record.tts_playback_mode = "legacy_unverified"
        record.tts_last_failure_reason = None
        self._save_field_mutation(record)
        return True

    def get(self, delivery_id: str) -> XiaoxinDeliveryRecord | None:
        return self._records.get(delivery_id)

    def require(self, delivery_id: str) -> XiaoxinDeliveryRecord:
        record = self.get(delivery_id)
        if record is None:
            raise KeyError(delivery_id)
        return record

    def list_recent(self) -> list[XiaoxinDeliveryRecord]:
        return list(reversed(self._records.values()))

    async def wait_for_state(
        self,
        delivery_id: str,
        states: set[XiaoxinDeliveryState],
        timeout_seconds: float,
    ) -> XiaoxinDeliveryRecord | None:
        async with self._condition:
            record = self.get(delivery_id)
            if record and record.state in states:
                return record
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(
                        lambda: (
                            self.get(delivery_id) is not None
                            and self.require(delivery_id).state in states
                        )
                    ),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                return None
            return self.get(delivery_id)

    def _trim(self) -> None:
        while len(self._records) > self.limit:
            terminal_delivery_id = next(
                (
                    delivery_id
                    for delivery_id, record in self._records.items()
                    if record.state
                    in {XiaoxinDeliveryState.DONE, XiaoxinDeliveryState.FAILED}
                ),
                None,
            )
            if terminal_delivery_id is None:
                return
            self._records.pop(terminal_delivery_id, None)

    def _save_field_mutation(self, record: XiaoxinDeliveryRecord) -> None:
        record.updated_at = utc_now_iso()
        self._records.move_to_end(record.delivery_id)
        self._save_history(record)
        self._trim()
        self._notify()

    def _save_history(self, record: XiaoxinDeliveryRecord) -> None:
        if self.history_sink is None:
            return
        try:
            self.history_sink.save_delivery_record(record)
        except Exception:
            LOGGER.exception(
                "Failed to save Xiaoxin notification history delivery_id=%s",
                record.delivery_id,
            )

    def _notify(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._notify_async())

    async def _notify_async(self) -> None:
        async with self._condition:
            self._condition.notify_all()
