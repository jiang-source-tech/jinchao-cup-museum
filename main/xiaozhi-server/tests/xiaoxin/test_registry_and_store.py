import asyncio

from core.xiaoxin.control_types import (
    XiaoxinDeliveryState,
    XiaoxinFailureReason,
    build_xiaoxin_event_payload,
    parse_control_event_request,
)
from core.xiaoxin.delivery_store import XiaoxinDeliveryStore
from core.xiaoxin.registry import XiaoxinDeviceRegistry


class FakeConnection:
    pass


def test_registry_reports_connected_wakeable_and_offline():
    registry = XiaoxinDeviceRegistry()
    conn = FakeConnection()

    registry.update_doorbell_status("aa", "online")
    registry.update_doorbell_status("bb", "offline", tenant_id="tenant-b")
    registry.register_connection("cc", conn, "websocket")

    devices = {item["device_id"]: item for item in registry.list_devices()}
    states = {device_id: item["state"] for device_id, item in devices.items()}

    assert states["aa"] == "wakeable"
    assert states["bb"] == "offline"
    assert states["cc"] == "connected"
    assert devices["aa"]["tenant_id"] == "hzcu-iee"
    assert devices["bb"]["tenant_id"] == "tenant-b"
    assert devices["cc"]["tenant_id"] == "hzcu-iee"


def test_unregister_only_removes_matching_connection():
    registry = XiaoxinDeviceRegistry()
    first = FakeConnection()
    second = FakeConnection()

    registry.register_connection("aa", first, "websocket")
    registry.register_connection("aa", second, "websocket")
    registry.unregister_connection("aa", first)

    assert registry.get_connection("aa") is second


def test_registry_keeps_latest_device_telemetry_after_disconnect():
    registry = XiaoxinDeviceRegistry()
    conn = FakeConnection()

    registry.register_connection("aa", conn, "websocket")
    registry.update_device_telemetry(
        "aa",
        battery_level=4,
        battery_percent=73,
        firmware_version="0.1.2",
    )
    registry.unregister_connection("aa", conn)

    device = registry.list_devices()[0]
    assert device["state"] == "offline"
    assert device["battery_level"] == 4
    assert device["battery_percent"] == 73
    assert device["firmware_version"] == "0.1.2"


def test_registry_rejects_invalid_telemetry_without_erasing_latest_values():
    registry = XiaoxinDeviceRegistry()
    registry.update_device_telemetry(
        "aa",
        battery_level=0,
        battery_percent=0,
        firmware_version=" 0.1.2 ",
    )

    registry.update_device_telemetry(
        "aa",
        battery_level=5,
        battery_percent=101,
        firmware_version="   ",
    )
    registry.update_device_telemetry("aa", battery_level=True)

    device = registry.list_devices()[0]
    assert device["battery_level"] == 0
    assert device["battery_percent"] == 0
    assert device["firmware_version"] == "0.1.2"


def test_wait_for_connected_resolves_after_registration():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        waiter = asyncio.create_task(registry.wait_for_connected("aa", 0.5))
        await asyncio.sleep(0.01)
        conn = FakeConnection()
        registry.register_connection("aa", conn, "websocket")
        return await waiter

    assert asyncio.run(scenario()).__class__ is FakeConnection


def test_delivery_store_records_timeline_and_reason():
    store = XiaoxinDeliveryStore(limit=2)
    request = parse_control_event_request(
        {
            "device_id": "aa",
            "event": "notification",
            "title": "提醒",
            "body": "内容",
        }
    )
    payload = build_xiaoxin_event_payload("del_ignored", request)

    created = store.create(request, payload)
    sent = store.transition(created.delivery_id, XiaoxinDeliveryState.SENT)
    failed = store.transition(
        created.delivery_id,
        XiaoxinDeliveryState.FAILED,
        XiaoxinFailureReason.ACK_TIMEOUT,
    )

    assert sent.delivery_id == created.delivery_id
    assert failed.state == XiaoxinDeliveryState.FAILED
    assert failed.reason == XiaoxinFailureReason.ACK_TIMEOUT
    assert [entry.state.value for entry in failed.timeline] == [
        "created",
        "sent",
        "failed",
    ]


def test_delivery_store_keeps_only_the_most_recent_records():
    store = XiaoxinDeliveryStore(limit=2)
    request = parse_control_event_request(
        {
            "device_id": "aa",
            "event": "notification",
            "title": "鎻愰啋",
            "body": "鍐呭",
        }
    )

    first = store.create(request, build_xiaoxin_event_payload("first", request))
    store.transition(first.delivery_id, XiaoxinDeliveryState.DONE)
    second = store.create(request, build_xiaoxin_event_payload("second", request))
    store.transition(second.delivery_id, XiaoxinDeliveryState.DONE)
    third = store.create(request, build_xiaoxin_event_payload("third", request))
    store.transition(third.delivery_id, XiaoxinDeliveryState.DONE)

    assert store.get(first.delivery_id) is None
    assert [record.delivery_id for record in store.list_recent()] == [
        third.delivery_id,
        second.delivery_id,
    ]


def test_delivery_store_copies_transition_details():
    store = XiaoxinDeliveryStore(limit=2)
    request = parse_control_event_request(
        {
            "device_id": "aa",
            "event": "notification",
            "title": "鎻愰啋",
            "body": "鍐呭",
        }
    )
    created = store.create(request, build_xiaoxin_event_payload("del_ignored", request))

    details = {"attempt": 1, "source": "webhook"}
    record = store.transition(
        created.delivery_id,
        XiaoxinDeliveryState.SENT,
        details=details,
    )
    details["attempt"] = 2
    details["source"] = "mutated"

    assert record.timeline[-1].details == {"attempt": 1, "source": "webhook"}
    assert record.timeline[-1].details is not details


def test_delivery_store_tracks_event_and_tts_attempts_separately():
    store = XiaoxinDeliveryStore()
    request = parse_control_event_request(
        {
            "device_id": "aa",
            "event": "notification",
            "title": "Reminder",
            "body": "Content",
            "speak": True,
            "speak_text": "Complete reminder",
        }
    )
    record = store.create(request, build_xiaoxin_event_payload("ignored", request))

    store.mark_event_acknowledged(record.delivery_id, {"state": "device_received"})
    attempt = store.begin_tts_attempt(record.delivery_id, "sentence-1")
    store.mark_tts_attempt_failed(record.delivery_id, "sentence-1", "ready_timeout")

    current = store.require(record.delivery_id)
    assert current.event_acknowledged is True
    assert attempt == 1
    assert current.tts_attempt_count == 1
    assert current.tts_state == "retry_wait"
    assert current.tts_last_failure_reason == "ready_timeout"
    assert current.reason is None
    assert current.state == XiaoxinDeliveryState.RETRY_WAIT
    assert current.to_dict()["tts_playback_mode"] is None


def test_delivery_store_atomic_tts_mutations_save_and_notify_exactly_once():
    class CountingStore(XiaoxinDeliveryStore):
        def __init__(self):
            super().__init__()
            self.saved = []
            self.notifications = 0

        def _save_history(self, record):
            self.saved.append(record.delivery_id)

        def _notify(self):
            self.notifications += 1

    store = CountingStore()
    request = parse_control_event_request(
        {
            "device_id": "aa",
            "event": "notification",
            "title": "Reminder",
            "body": "Content",
            "speak": True,
        }
    )
    record = store.create(request, build_xiaoxin_event_payload("ignored", request))

    operations = [
        lambda: store.mark_event_acknowledged(
            record.delivery_id, {"state": "device_received"}
        ),
        lambda: store.begin_tts_attempt(record.delivery_id, "sentence-1"),
        lambda: store.mark_tts_attempt_failed(
            record.delivery_id, "sentence-1", "done_timeout"
        ),
        lambda: store.begin_tts_attempt(record.delivery_id, "sentence-2"),
        lambda: store.mark_tts_done(record.delivery_id, "sentence-2"),
        lambda: store.begin_tts_attempt(record.delivery_id, "sentence-3"),
        lambda: store.mark_tts_legacy_unverified(record.delivery_id, "sentence-3"),
    ]
    for index, operation in enumerate(operations, start=2):
        operation()
        assert len(store.saved) == index
        assert store.notifications == index

    before = (len(store.saved), store.notifications)
    assert store.mark_tts_done(record.delivery_id, "stale") is False
    assert (len(store.saved), store.notifications) == before

    current = store.require(record.delivery_id)
    assert current.tts_state == "legacy_unverified"
    assert current.tts_playback_mode == "legacy_unverified"
    assert current.tts_playback_mode != "reliable"
    assert current.reason is None


def test_delivery_store_allows_active_overflow_then_trims_terminal_records():
    store = XiaoxinDeliveryStore(limit=1)
    request = parse_control_event_request(
        {
            "device_id": "aa",
            "event": "notification",
            "title": "Reminder",
            "body": "Content",
            "speak": True,
        }
    )
    first = store.create(request, build_xiaoxin_event_payload("first", request))
    second = store.create(request, build_xiaoxin_event_payload("second", request))

    assert [record.delivery_id for record in store.list_recent()] == [
        second.delivery_id,
        first.delivery_id,
    ]

    store.transition(first.delivery_id, XiaoxinDeliveryState.DONE)

    assert store.get(first.delivery_id) is None
    assert store.require(second.delivery_id).state == XiaoxinDeliveryState.CREATED
    assert len(store.list_recent()) == 1
