from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import uuid


class ControlValidationError(ValueError):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


class _StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class XiaoxinEvent(_StringEnum):
    NOTIFICATION = "notification"
    COURSE_REMINDER = "course_reminder"
    TODO_REMINDER = "todo_reminder"


class XiaoxinDeviceState(_StringEnum):
    CONNECTED = "connected"
    WAKEABLE = "wakeable"
    OFFLINE = "offline"


class XiaoxinDeliveryState(_StringEnum):
    CREATED = "created"
    WAKING = "waking"
    SENT = "sent"
    DEVICE_RECEIVED = "device_received"
    SPEAKING = "speaking"
    RETRY_WAIT = "retry_wait"
    DONE = "done"
    FAILED = "failed"


class XiaoxinFailureReason(_StringEnum):
    DEVICE_OFFLINE = "device_offline"
    WAKE_TIMEOUT = "wake_timeout"
    DEVICE_BUSY = "device_busy"
    SEND_FAILED = "send_failed"
    TTS_FAILED = "tts_failed"
    ACK_TIMEOUT = "ack_timeout"
    INVALID_PAYLOAD = "invalid_payload"
    EXPIRED = "expired"
    DISPATCHER_STOPPED = "dispatcher_stopped"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class XiaoxinControlEventRequest:
    device_id: str
    event: XiaoxinEvent
    title: str
    body: str
    tag: str = ""
    priority: int = 2
    ttl_ms: int = 0
    speak: bool = False
    speak_text: str = ""
    course_name: str = ""
    classroom: str = ""
    starts_at: str = ""
    remind_before_min: int | None = None
    todo_title: str = ""
    due_at: str = ""
    hardware_expression: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "event": self.event.value,
            "title": self.title,
            "body": self.body,
            "tag": self.tag,
            "priority": self.priority,
            "ttl_ms": self.ttl_ms,
            "speak": self.speak,
        }


@dataclass(frozen=True)
class XiaoxinDeliveryTimelineEntry:
    state: XiaoxinDeliveryState
    at: str
    reason: XiaoxinFailureReason | None = None
    source: str = "server"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "at": self.at,
            "reason": self.reason.value if self.reason else None,
            "source": self.source,
            "details": self.details,
        }


@dataclass
class XiaoxinDeliveryRecord:
    delivery_id: str
    device_id: str
    event: XiaoxinEvent
    payload: dict[str, Any]
    request: XiaoxinControlEventRequest
    state: XiaoxinDeliveryState
    reason: XiaoxinFailureReason | None
    created_at: str
    updated_at: str
    timeline: list[XiaoxinDeliveryTimelineEntry] = field(default_factory=list)
    control_tts_sentence_id: str | None = None
    event_acknowledged: bool = False
    tts_attempt_count: int = 0
    tts_state: str = "not_requested"
    tts_last_failure_reason: str | None = None
    tts_playback_mode: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "delivery_id": self.delivery_id,
            "device_id": self.device_id,
            "event": self.event.value,
            "state": self.state.value,
            "reason": self.reason.value if self.reason else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "payload": self.payload,
            "request": self.request.summary(),
            "timeline": [entry.to_dict() for entry in self.timeline],
            "control_tts_sentence_id": self.control_tts_sentence_id,
            "event_acknowledged": self.event_acknowledged,
            "tts_attempt_count": self.tts_attempt_count,
            "tts_state": self.tts_state,
            "tts_last_failure_reason": self.tts_last_failure_reason,
            "tts_playback_mode": self.tts_playback_mode,
        }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_delivery_id(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"del_{stamp}_{uuid.uuid4().hex[:8]}"


def parse_control_event_request(data: dict[str, Any]) -> XiaoxinControlEventRequest:
    device_id = _required_str(data, "device_id")
    raw_event = _required_str(data, "event")
    try:
        event = XiaoxinEvent(raw_event)
    except ValueError as exc:
        raise ControlValidationError("unsupported xiaoxin event", "event") from exc

    title = _required_str(data, "title")
    body = _required_str(data, "body")
    tag = _optional_str(data, "tag")
    priority = _int_range(data.get("priority", 2), "priority", 0, 4)
    ttl_ms = _int_range(data.get("ttl_ms", 0), "ttl_ms", 0, 24 * 60 * 60 * 1000)
    speak = bool(data.get("speak", False))
    speak_text = _optional_str(data, "speak_text")
    if speak and not speak_text:
        speak_text = body

    hardware_expression = data.get("hardware_expression", {})
    if not isinstance(hardware_expression, dict):
        raise ControlValidationError(
            "hardware_expression must be an object",
            "hardware_expression",
        )

    return XiaoxinControlEventRequest(
        device_id=device_id,
        event=event,
        title=title,
        body=body,
        tag=tag,
        priority=priority,
        ttl_ms=ttl_ms,
        speak=speak,
        speak_text=speak_text,
        course_name=_optional_str(data, "course_name"),
        classroom=_optional_str(data, "classroom"),
        starts_at=_optional_str(data, "starts_at"),
        remind_before_min=_optional_int(data, "remind_before_min"),
        todo_title=_optional_str(data, "todo_title"),
        due_at=_optional_str(data, "due_at"),
        hardware_expression=dict(hardware_expression),
    )


def build_xiaoxin_event_payload(
    delivery_id: str, request: XiaoxinControlEventRequest
) -> dict[str, Any]:
    payload = {
        "type": "xiaoxin_event",
        "delivery_id": delivery_id,
        "event": request.event.value,
        "title": request.title,
        "body": request.body,
        "tag": request.tag,
        "priority": request.priority,
        "ttl_ms": request.ttl_ms,
    }
    if request.hardware_expression:
        payload["hardware_expression"] = dict(request.hardware_expression)
    return payload


def _required_str(data: dict[str, Any], field: str) -> str:
    value = str(data.get(field, "")).strip()
    if not value:
        raise ControlValidationError(f"{field} is required", field)
    return value


def _optional_str(data: dict[str, Any], field: str) -> str:
    value = data.get(field, "")
    return "" if value is None else str(value).strip()


def _optional_int(data: dict[str, Any], field: str) -> int | None:
    if field not in data or data[field] in ("", None):
        return None
    return _int_range(data[field], field, 0, 10080)


def _int_range(value: Any, field: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ControlValidationError(f"{field} must be an integer", field) from exc
    if number < minimum or number > maximum:
        raise ControlValidationError(
            f"{field} must be between {minimum} and {maximum}", field
        )
    return number
