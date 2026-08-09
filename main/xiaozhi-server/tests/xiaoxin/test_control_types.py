import pytest

from core.xiaoxin.control_types import (
    ControlValidationError,
    XiaoxinEvent,
    build_xiaoxin_event_payload,
    parse_control_event_request,
)


def test_parse_notification_request_defaults_speak_text_to_body():
    request = parse_control_event_request(
        {
            "device_id": "aa:bb:cc:dd:ee:ff",
            "event": "notification",
            "title": "Reminder",
            "body": "Drink water",
            "tag": "notify",
            "priority": 2,
            "ttl_ms": 0,
            "speak": True,
        }
    )

    assert request.device_id == "aa:bb:cc:dd:ee:ff"
    assert request.event == XiaoxinEvent.NOTIFICATION
    assert request.speak is True
    assert request.speak_text == "Drink water"


def test_build_course_payload_uses_ack_capable_xiaoxin_event_shape():
    request = parse_control_event_request(
        {
            "device_id": "aa",
            "event": "course_reminder",
            "title": "Class reminder",
            "body": "Math starts in 15 minutes @ Room 204",
            "tag": "course",
            "priority": 1,
            "ttl_ms": 0,
            "speak": True,
            "speak_text": "Math starts in 15 minutes.",
            "course_name": "Math",
            "classroom": "Room 204",
            "starts_at": "10:10",
            "remind_before_min": 15,
        }
    )

    payload = build_xiaoxin_event_payload("del_1", request)

    assert payload == {
        "type": "xiaoxin_event",
        "delivery_id": "del_1",
        "event": "course_reminder",
        "title": "Class reminder",
        "body": "Math starts in 15 minutes @ Room 204",
        "tag": "course",
        "priority": 1,
        "ttl_ms": 0,
    }


def test_build_todo_payload_uses_ack_capable_xiaoxin_event_shape():
    request = parse_control_event_request(
        {
            "device_id": "aa",
            "event": "todo_reminder",
            "title": "Todo reminder",
            "body": "Submit the lab report",
            "tag": "todo",
            "priority": 2,
            "ttl_ms": 0,
            "speak": False,
            "todo_title": "Lab report",
            "due_at": "2026-07-03T18:00:00+08:00",
        }
    )

    payload = build_xiaoxin_event_payload("del_2", request)

    assert payload == {
        "type": "xiaoxin_event",
        "delivery_id": "del_2",
        "event": "todo_reminder",
        "title": "Todo reminder",
        "body": "Submit the lab report",
        "tag": "todo",
        "priority": 2,
        "ttl_ms": 0,
    }


@pytest.mark.parametrize(
    "payload, field",
    [
        ({}, "device_id"),
        ({"device_id": "aa", "event": "unknown", "title": "t", "body": "b"}, "event"),
        (
            {"device_id": "aa", "event": "notification", "title": "", "body": "b"},
            "title",
        ),
        (
            {"device_id": "aa", "event": "notification", "title": "t", "body": ""},
            "body",
        ),
        (
            {
                "device_id": "aa",
                "event": "notification",
                "title": "t",
                "body": "b",
                "ttl_ms": -1,
            },
            "ttl_ms",
        ),
    ],
)
def test_invalid_request_reports_field(payload, field):
    with pytest.raises(ControlValidationError) as exc:
        parse_control_event_request(payload)

    assert exc.value.field == field
