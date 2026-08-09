from core.xiaoxin.control_types import (
    XiaoxinDeliveryState,
    XiaoxinFailureReason,
    build_xiaoxin_event_payload,
    parse_control_event_request,
)
from core.xiaoxin.delivery_store import XiaoxinDeliveryStore
from core.xiaoxin.notification_history_store import XiaoxinNotificationHistoryStore


def test_notification_history_store_persists_latest_delivery_snapshot(tmp_path):
    history_store = XiaoxinNotificationHistoryStore(tmp_path / "xiaoxin_history.db")
    delivery_store = XiaoxinDeliveryStore(history_sink=history_store)
    request = parse_control_event_request(
        {
            "device_id": "device-a",
            "event": "notification",
            "title": "Reminder",
            "body": "Remember the task.",
        }
    )

    created = delivery_store.create(request, build_xiaoxin_event_payload("ignored", request))
    delivery_store.transition(
        created.delivery_id,
        XiaoxinDeliveryState.FAILED,
        XiaoxinFailureReason.ACK_TIMEOUT,
    )

    reloaded_store = XiaoxinNotificationHistoryStore(tmp_path / "xiaoxin_history.db")
    records = reloaded_store.list_for_device_ids({"device-a"})

    assert len(records) == 1
    assert records[0]["delivery_id"] == created.delivery_id
    assert records[0]["device_id"] == "device-a"
    assert records[0]["event"] == "notification"
    assert records[0]["state"] == "failed"
    assert records[0]["reason"] == "ack_timeout"
    assert [entry["state"] for entry in records[0]["timeline"]] == [
        "created",
        "failed",
    ]


def test_notification_history_store_filters_by_device_ids(tmp_path):
    history_store = XiaoxinNotificationHistoryStore(tmp_path / "xiaoxin_history.db")
    delivery_store = XiaoxinDeliveryStore(history_sink=history_store)

    for device_id in ("device-a", "device-b"):
        request = parse_control_event_request(
            {
                "device_id": device_id,
                "event": "notification",
                "title": f"Reminder {device_id}",
                "body": "Remember the task.",
            }
        )
        delivery_store.create(request, build_xiaoxin_event_payload("ignored", request))

    records = history_store.list_for_device_ids({"device-a"})

    assert [record["device_id"] for record in records] == ["device-a"]


def test_notification_history_store_reads_final_states_by_delivery_id(tmp_path):
    history_store = XiaoxinNotificationHistoryStore(tmp_path / "xiaoxin_history.db")
    delivery_store = XiaoxinDeliveryStore(history_sink=history_store)
    request = parse_control_event_request(
        {
            "device_id": "device-a",
            "event": "notification",
            "title": "Reminder",
            "body": "Remember the task.",
        }
    )
    done = delivery_store.create(request, build_xiaoxin_event_payload("ignored", request))
    delivery_store.transition(done.delivery_id, XiaoxinDeliveryState.DONE)
    failed = delivery_store.create(request, build_xiaoxin_event_payload("ignored", request))
    delivery_store.transition(failed.delivery_id, XiaoxinDeliveryState.FAILED)

    states = history_store.get_delivery_states(
        {done.delivery_id, failed.delivery_id, "missing"}
    )

    assert states == {
        done.delivery_id: "done",
        failed.delivery_id: "failed",
    }


def test_delivery_store_keeps_delivery_when_history_sink_fails():
    class FailingHistorySink:
        def save_delivery_record(self, record):
            raise RuntimeError("history unavailable")

    delivery_store = XiaoxinDeliveryStore(history_sink=FailingHistorySink())
    request = parse_control_event_request(
        {
            "device_id": "device-a",
            "event": "notification",
            "title": "Reminder",
            "body": "Remember the task.",
        }
    )

    created = delivery_store.create(
        request,
        build_xiaoxin_event_payload("ignored", request),
    )
    updated = delivery_store.transition(created.delivery_id, XiaoxinDeliveryState.SENT)

    assert delivery_store.get(created.delivery_id) is updated
    assert updated.state == XiaoxinDeliveryState.SENT
