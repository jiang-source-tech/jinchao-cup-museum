import asyncio
from types import SimpleNamespace

import pytest

from core.connection import (
    ConnectionHandler,
    TTS_PHASE_STREAMING,
)
from core.xiaoxin.control_types import (
    XiaoxinDeliveryState,
    build_xiaoxin_event_payload,
    parse_control_event_request,
)
from core.xiaoxin.delivery_store import XiaoxinDeliveryStore
from core.xiaoxin.dispatcher import XiaoxinEventDispatcher
from core.xiaoxin.registry import XiaoxinDeviceRegistry
from core.xiaoxin.tts_delivery import TtsAttemptError


class FakeDoorbell:
    def __init__(self, can_attempt_wake=False):
        self.can_attempt_wake_value = can_attempt_wake
        self.wakes = []

    def can_attempt_wake(self):
        return self.can_attempt_wake_value

    def publish_wake(self, device_id):
        self.wakes.append(device_id)
        return True


class FakeConnection:
    def __init__(self):
        self.sent = []
        self.spoken = []

    async def send_xiaoxin_event(self, payload):
        self.sent.append(payload)

    async def speak_from_control_console(self, text, delivery_id, sentence_id):
        self.spoken.append((text, delivery_id, sentence_id))


class FlakyTtsConnection(FakeConnection):
    def __init__(self, failures_before_done):
        super().__init__()
        self.failures_before_done = failures_before_done
        self.dispatcher = None

    async def speak_from_control_console(self, text, delivery_id, sentence_id):
        self.spoken.append((text, delivery_id, sentence_id))
        if len(self.spoken) <= self.failures_before_done:
            raise TtsAttemptError(sentence_id, "ready_timeout")
        self.dispatcher.mark_tts_done(delivery_id, sentence_id)


def _request(speak=False, ttl_ms=0):
    return parse_control_event_request(
        {
            "device_id": "aa",
            "event": "notification",
            "title": "Reminder",
            "body": "Content",
            "ttl_ms": ttl_ms,
            "speak": speak,
            "speak_text": "Complete reminder text",
        }
    )


def test_companion_initiative_dispatcher_consumes_only_projection_brief():
    dispatcher = XiaoxinEventDispatcher(
        XiaoxinDeviceRegistry(),
        XiaoxinDeliveryStore(),
        FakeDoorbell(),
    )
    captured = []

    async def fake_submit(request):
        captured.append(request)
        return SimpleNamespace(delivery_id="delivery-1")

    dispatcher.submit = fake_submit
    result = asyncio.run(
        dispatcher.submit_companion_initiative(
            "device-1",
            {
                "eligible": True,
                "decision_id": "decision-1",
                "reason_code": "evidence_backed_followup",
                "evidence_ids": ("evidence-1",),
                "content_brief": "记得你完成了那件重要的事。",
                "hardware_expression": {"intensity": "low"},
            },
        )
    )

    assert result.delivery_id == "delivery-1"
    assert len(captured) == 1
    request = captured[0]
    assert request.device_id == "device-1"
    assert request.priority == 1
    assert request.body == "记得你完成了那件重要的事。"
    assert request.speak_text == request.body
    assert request.tag == "companion:decision-1"
    assert request.hardware_expression == {"intensity": "low"}
    assert build_xiaoxin_event_payload("delivery-1", request)[
        "hardware_expression"
    ] == {"intensity": "low"}


def _device_request(device_id, speak=True):
    return parse_control_event_request(
        {
            "device_id": device_id,
            "event": "notification",
            "title": "Reminder",
            "body": f"Content for {device_id}",
            "speak": speak,
            "speak_text": f"Complete reminder text for {device_id}",
        }
    )


def _speaking_record(store):
    request = _request(speak=True)
    record = store.create(request, build_xiaoxin_event_payload("ignored", request))
    store.mark_event_acknowledged(record.delivery_id, {"state": "device_received"})
    return record


async def _wait_until(predicate, timeout=0.5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


def test_connected_non_speaking_delivery_keeps_immediate_behavior():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        conn = FakeConnection()
        registry.register_connection("aa", conn, "websocket")
        dispatcher = XiaoxinEventDispatcher(registry, store, FakeDoorbell())
        record = await dispatcher.submit(_request())
        await dispatcher.wait_for_delivery_task(record.delivery_id)
        return store.require(record.delivery_id), conn

    record, conn = asyncio.run(scenario())
    assert conn.sent == [record.payload]
    assert conn.sent[0]["type"] == "xiaoxin_event"
    assert record.state == XiaoxinDeliveryState.DONE


def test_expired_non_speaking_delivery_is_not_sent():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        conn = FakeConnection()
        registry.register_connection("aa", conn, "websocket")
        dispatcher = XiaoxinEventDispatcher(registry, store, FakeDoorbell())
        request = _request(ttl_ms=1000)
        record = store.create(
            request, build_xiaoxin_event_payload("ignored", request)
        )
        record.created_at = "2000-01-01T00:00:00+00:00"

        await dispatcher._deliver(record.delivery_id)

        return store.require(record.delivery_id), conn

    record, conn = asyncio.run(scenario())

    assert record.state == XiaoxinDeliveryState.FAILED
    assert record.reason.value == "expired"
    assert conn.sent == []


def test_offline_non_speaking_delivery_fails_immediately():
    async def scenario():
        dispatcher = XiaoxinEventDispatcher(
            XiaoxinDeviceRegistry(), XiaoxinDeliveryStore(), FakeDoorbell()
        )
        record = await dispatcher.submit(_request())
        await dispatcher.wait_for_delivery_task(record.delivery_id)
        return dispatcher.store.require(record.delivery_id)

    record = asyncio.run(scenario())
    assert record.state == XiaoxinDeliveryState.FAILED
    assert record.reason.value == "device_offline"


def test_offline_non_speaking_delivery_wakes_then_sends_after_reconnect():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        doorbell = FakeDoorbell(can_attempt_wake=True)
        dispatcher = XiaoxinEventDispatcher(
            registry, store, doorbell, wake_timeout_seconds=0.5
        )
        record = await dispatcher.submit(_request())
        await asyncio.sleep(0.01)
        conn = FakeConnection()
        registry.register_connection("aa", conn, "websocket")
        await dispatcher.wait_for_delivery_task(record.delivery_id)
        return store.require(record.delivery_id), conn, doorbell

    record, conn, doorbell = asyncio.run(scenario())
    assert doorbell.wakes == ["aa"]
    assert conn.sent[0]["delivery_id"] == record.delivery_id
    assert record.state == XiaoxinDeliveryState.DONE


def test_offline_speaking_delivery_waits_for_reconnect_without_terminal_reason():
    async def scenario():
        dispatcher = XiaoxinEventDispatcher(
            XiaoxinDeviceRegistry(),
            XiaoxinDeliveryStore(),
            FakeDoorbell(),
            retry_delays_seconds=(0.001,),
        )
        record = await dispatcher.submit(_request(speak=True))
        await asyncio.sleep(0.01)
        current = dispatcher.store.require(record.delivery_id)
        before_stop = (current.state, current.reason)
        live = not dispatcher._event_tasks[record.delivery_id].done()
        await dispatcher.stop()
        return before_stop, current, live

    before_stop, record, live = asyncio.run(scenario())
    assert before_stop == (XiaoxinDeliveryState.RETRY_WAIT, None)
    assert live is True
    assert record.state == XiaoxinDeliveryState.FAILED
    assert record.reason.value == "dispatcher_stopped"


def test_offline_speaking_delivery_expires_instead_of_sending_stale_content():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        dispatcher = XiaoxinEventDispatcher(
            registry,
            store,
            FakeDoorbell(),
            retry_delays_seconds=(0.001,),
        )
        record = await dispatcher.submit(_request(speak=True, ttl_ms=20))
        await asyncio.wait_for(
            dispatcher.wait_for_delivery_task(record.delivery_id),
            timeout=0.3,
        )
        conn = FakeConnection()
        registry.register_connection("aa", conn, "websocket")
        await asyncio.sleep(0.01)
        return store.require(record.delivery_id), conn

    record, conn = asyncio.run(scenario())

    assert record.state == XiaoxinDeliveryState.FAILED
    assert record.reason.value == "expired"
    assert conn.sent == []
    assert conn.spoken == []


def test_tts_delivery_retries_with_new_sentence_ids_and_full_text():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        conn = FlakyTtsConnection(failures_before_done=2)
        registry.register_connection("aa", conn, "websocket")
        dispatcher = XiaoxinEventDispatcher(
            registry, store, FakeDoorbell(), retry_delays_seconds=(0.001,)
        )
        conn.dispatcher = dispatcher
        record = _speaking_record(store)
        await dispatcher._run_tts_delivery(record.delivery_id)
        return store.require(record.delivery_id), conn

    record, conn = asyncio.run(scenario())
    assert len(conn.spoken) == 3
    assert len({item[2] for item in conn.spoken}) == 3
    assert all(item[0] == "Complete reminder text" for item in conn.spoken)
    assert record.tts_attempt_count == 3
    assert record.tts_state == "done"
    assert record.tts_playback_mode == "reliable"
    assert record.state == XiaoxinDeliveryState.DONE


def test_retry_delay_list_does_not_cap_sixth_attempt_success():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        conn = FlakyTtsConnection(failures_before_done=5)
        registry.register_connection("aa", conn, "websocket")
        dispatcher = XiaoxinEventDispatcher(
            registry,
            store,
            FakeDoorbell(),
            retry_delays_seconds=(0.001, 0.001),
        )
        conn.dispatcher = dispatcher
        record = _speaking_record(store)
        await dispatcher._run_tts_delivery(record.delivery_id)
        return store.require(record.delivery_id), conn

    record, conn = asyncio.run(scenario())
    assert len(conn.spoken) == 6
    assert len({item[2] for item in conn.spoken}) == 6
    assert all(item[0] == "Complete reminder text" for item in conn.spoken)
    assert record.tts_attempt_count == 6
    assert record.state == XiaoxinDeliveryState.DONE


def test_stale_done_error_and_legacy_callbacks_cannot_resolve_current_attempt():
    async def scenario():
        dispatcher = XiaoxinEventDispatcher(
            XiaoxinDeviceRegistry(),
            XiaoxinDeliveryStore(),
            FakeDoorbell(),
            retry_delays_seconds=(0.001,),
        )
        record = _speaking_record(dispatcher.store)
        dispatcher.store.begin_tts_attempt(record.delivery_id, "current")
        future = asyncio.get_running_loop().create_future()
        dispatcher._tts_outcomes[(record.delivery_id, "current")] = future
        dispatcher.mark_tts_done(record.delivery_id, "stale-done")
        dispatcher.mark_tts_attempt_failed(
            record.delivery_id, "stale-error", "device_busy"
        )
        dispatcher.mark_tts_legacy_unverified(record.delivery_id, "stale-legacy")
        stale_resolved = future.done()
        dispatcher.mark_tts_done(record.delivery_id, "current")
        return stale_resolved, await future

    stale_resolved, outcome = asyncio.run(scenario())
    assert stale_resolved is False
    assert outcome.sentence_id == "current"
    assert outcome.status == "done"


def test_offline_time_does_not_consume_connected_retry_delay():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        conn = FakeConnection()
        registry.register_connection("aa", conn, "websocket")
        dispatcher = XiaoxinEventDispatcher(
            registry,
            XiaoxinDeliveryStore(),
            FakeDoorbell(),
            retry_delays_seconds=(0.04,),
        )
        loop = asyncio.get_running_loop()
        started = loop.time()
        waiter = asyncio.create_task(dispatcher._wait_connected_delay("aa", 0.04))
        await asyncio.sleep(0.015)
        registry.unregister_connection("aa", conn)
        await asyncio.sleep(0.06)
        still_waiting = not waiter.done()
        replacement = FakeConnection()
        registry.register_connection("aa", replacement, "websocket")
        await waiter
        return still_waiting, loop.time() - started

    still_waiting, elapsed = asyncio.run(scenario())
    assert still_waiting is True
    assert elapsed >= 0.085


def test_card_send_precedes_voice_and_retries_do_not_duplicate_tts_task():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        operations = []

        class OrderedConnection(FakeConnection):
            async def send_xiaoxin_event(self, payload):
                self.sent.append(payload)
                operations.append(("event", payload["delivery_id"]))

            async def speak_from_control_console(self, text, delivery_id, sentence_id):
                self.spoken.append((text, delivery_id, sentence_id))
                operations.append(("tts", sentence_id))

        conn = OrderedConnection()
        registry.register_connection("aa", conn, "websocket")
        dispatcher = XiaoxinEventDispatcher(
            registry,
            store,
            FakeDoorbell(),
            ack_timeout_seconds=0.01,
            retry_delays_seconds=(0.005,),
        )
        record = await dispatcher.submit(_request(speak=True))
        await _wait_until(lambda: len(conn.sent) >= 2 and len(conn.spoken) == 1)
        tts_task = dispatcher._tts_tasks[record.delivery_id]
        ack = {
            "type": "xiaoxin_ack",
            "delivery_id": record.delivery_id,
            "state": "device_received",
        }
        await dispatcher.handle_ack("aa", ack, conn)
        await dispatcher.handle_ack("aa", ack, conn)
        await dispatcher._event_tasks[record.delivery_id]
        same_tts_task = dispatcher._tts_tasks[record.delivery_id] is tts_task
        dispatcher.mark_tts_done(record.delivery_id, conn.spoken[0][2])
        await tts_task
        return store.require(record.delivery_id), conn, operations, same_tts_task

    record, conn, operations, same_tts_task = asyncio.run(scenario())
    assert operations[0][0] == "event"
    assert operations[1][0] == "tts"
    assert len(conn.sent) >= 2
    assert {payload["delivery_id"] for payload in conn.sent} == {record.delivery_id}
    assert len(conn.spoken) == 1
    assert same_tts_task is True
    assert record.state == XiaoxinDeliveryState.DONE


def test_speaking_event_delivery_retries_generic_send_failure():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()

        class GenericFailureConnection(FakeConnection):
            def __init__(self):
                super().__init__()
                self.send_calls = 0

            async def send_xiaoxin_event(self, payload):
                self.send_calls += 1
                if self.send_calls == 1:
                    raise ValueError("temporary encoder failure")
                self.sent.append(payload)
                await dispatcher.handle_ack(
                    "aa",
                    {
                        "type": "xiaoxin_ack",
                        "delivery_id": payload["delivery_id"],
                        "state": "device_received",
                    },
                    self,
                )

            async def speak_from_control_console(self, text, delivery_id, sentence_id):
                self.spoken.append((text, delivery_id, sentence_id))
                dispatcher.mark_tts_done(delivery_id, sentence_id)

        conn = GenericFailureConnection()
        registry.register_connection("aa", conn, "websocket")
        dispatcher = XiaoxinEventDispatcher(
            registry, store, FakeDoorbell(), retry_delays_seconds=(0.001,)
        )
        record = await dispatcher.submit(_request(speak=True))
        await dispatcher.wait_for_delivery_task(record.delivery_id)
        return store.require(record.delivery_id), conn

    record, conn = asyncio.run(scenario())
    assert conn.send_calls == 2
    assert len(conn.spoken) == 1
    assert record.state == XiaoxinDeliveryState.DONE


def test_connection_callback_reason_wins_over_following_generic_exception():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()

        class CallbackThenRaiseConnection(FakeConnection):
            async def speak_from_control_console(self, text, delivery_id, sentence_id):
                self.spoken.append((text, delivery_id, sentence_id))
                if len(self.spoken) == 1:
                    dispatcher.mark_tts_attempt_failed(
                        delivery_id, sentence_id, "device_busy"
                    )
                    raise RuntimeError("connection wrapper propagated failure")
                dispatcher.mark_tts_done(delivery_id, sentence_id)

        conn = CallbackThenRaiseConnection()
        registry.register_connection("aa", conn, "websocket")
        dispatcher = XiaoxinEventDispatcher(
            registry, store, FakeDoorbell(), retry_delays_seconds=(0.001,)
        )
        record = _speaking_record(store)
        await dispatcher._run_tts_delivery(record.delivery_id)
        return store.require(record.delivery_id), conn

    record, conn = asyncio.run(scenario())
    first_failure = next(
        entry
        for entry in record.timeline
        if entry.details.get("failure_reason") is not None
    )
    assert first_failure.details["failure_reason"] == "device_busy"
    assert len(conn.spoken) == 2
    assert record.state == XiaoxinDeliveryState.DONE


def test_speaking_delivery_ignores_non_device_received_ack_state():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        conn = FakeConnection()
        registry.register_connection("aa", conn, "websocket")
        dispatcher = XiaoxinEventDispatcher(
            registry,
            store,
            FakeDoorbell(),
            ack_timeout_seconds=1,
            retry_delays_seconds=(0.001,),
        )
        record = await dispatcher.submit(_request(speak=True))
        await _wait_until(lambda: bool(conn.spoken))
        await dispatcher.handle_ack(
            "aa",
            {
                "type": "xiaoxin_ack",
                "delivery_id": record.delivery_id,
                "state": "done",
            },
            conn,
        )
        current = store.require(record.delivery_id)
        result = (current.state, current.event_acknowledged)
        await dispatcher.stop()
        return result

    state, event_acknowledged = asyncio.run(scenario())
    assert state != XiaoxinDeliveryState.DONE
    assert event_acknowledged is False


def test_card_ack_before_tts_done_converges_only_after_tts_done():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        conn = FakeConnection()
        registry.register_connection("aa", conn, "websocket")
        dispatcher = XiaoxinEventDispatcher(
            registry, store, FakeDoorbell(), retry_delays_seconds=(0.001,)
        )
        record = await dispatcher.submit(_request(speak=True))
        await _wait_until(lambda: bool(conn.spoken))
        await dispatcher.handle_ack(
            "aa",
            {
                "type": "xiaoxin_ack",
                "delivery_id": record.delivery_id,
                "state": "device_received",
            },
            conn,
        )
        state_after_ack = store.require(record.delivery_id).state
        dispatcher.mark_tts_done(record.delivery_id, conn.spoken[0][2])
        await dispatcher.wait_for_delivery_task(record.delivery_id)
        return state_after_ack, store.require(record.delivery_id)

    state_after_ack, record = asyncio.run(scenario())
    assert state_after_ack != XiaoxinDeliveryState.DONE
    assert record.event_acknowledged is True
    assert record.tts_state == "done"
    assert record.state == XiaoxinDeliveryState.DONE


def test_tts_done_before_card_ack_converges_only_after_card_ack():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        conn = FakeConnection()
        registry.register_connection("aa", conn, "websocket")
        dispatcher = XiaoxinEventDispatcher(
            registry,
            store,
            FakeDoorbell(),
            ack_timeout_seconds=0.2,
            retry_delays_seconds=(0.001,),
        )
        record = await dispatcher.submit(_request(speak=True))
        await _wait_until(lambda: bool(conn.spoken))
        dispatcher.mark_tts_done(record.delivery_id, conn.spoken[0][2])
        await dispatcher._tts_tasks[record.delivery_id]
        state_after_tts = store.require(record.delivery_id).state
        await dispatcher.handle_ack(
            "aa",
            {
                "type": "xiaoxin_ack",
                "delivery_id": record.delivery_id,
                "state": "device_received",
            },
            conn,
        )
        await dispatcher.wait_for_delivery_task(record.delivery_id)
        return state_after_tts, store.require(record.delivery_id)

    state_after_tts, record = asyncio.run(scenario())
    assert state_after_tts != XiaoxinDeliveryState.DONE
    assert record.event_acknowledged is True
    assert record.tts_state == "done"
    assert record.state == XiaoxinDeliveryState.DONE


def test_legacy_unverified_is_visible_and_never_claims_reliable_playback():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()

        class LegacyConnection(FakeConnection):
            async def speak_from_control_console(self, text, delivery_id, sentence_id):
                self.spoken.append((text, delivery_id, sentence_id))
                dispatcher.mark_tts_legacy_unverified(delivery_id, sentence_id)

        conn = LegacyConnection()
        registry.register_connection("aa", conn, "websocket")
        dispatcher = XiaoxinEventDispatcher(
            registry, store, FakeDoorbell(), retry_delays_seconds=(0.001,)
        )
        record = _speaking_record(store)
        await dispatcher._run_tts_delivery(record.delivery_id)
        return store.require(record.delivery_id)

    record = asyncio.run(scenario())
    assert record.tts_state == "legacy_unverified"
    assert record.tts_playback_mode == "legacy_unverified"
    assert record.tts_playback_mode != "reliable"
    assert record.state == XiaoxinDeliveryState.DONE


def test_shutdown_cancels_tasks_and_outcome_futures_without_retry_churn():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        doorbell = FakeDoorbell(can_attempt_wake=True)
        conn = FakeConnection()
        registry.register_connection("aa", conn, "websocket")
        dispatcher = XiaoxinEventDispatcher(
            registry,
            store,
            doorbell,
            ack_timeout_seconds=1,
            retry_delays_seconds=(0.001,),
        )
        record = await dispatcher.submit(_request(speak=True))
        await _wait_until(
            lambda: bool(dispatcher._event_ack_futures)
            and bool(dispatcher._tts_outcomes)
        )
        counts_before = (len(conn.sent), len(conn.spoken), len(doorbell.wakes))
        await dispatcher.stop()
        await asyncio.sleep(0.02)
        counts_after = (len(conn.sent), len(conn.spoken), len(doorbell.wakes))
        return dispatcher, counts_before, counts_after

    dispatcher, counts_before, counts_after = asyncio.run(scenario())
    assert counts_after == counts_before
    assert dispatcher._event_tasks == {}
    assert dispatcher._tts_tasks == {}
    assert dispatcher._event_ack_futures == {}
    assert dispatcher._tts_outcomes == {}


def test_offline_wait_publishes_only_one_wake_until_connection_or_stop():
    async def scenario():
        doorbell = FakeDoorbell(can_attempt_wake=True)
        dispatcher = XiaoxinEventDispatcher(
            XiaoxinDeviceRegistry(),
            XiaoxinDeliveryStore(),
            doorbell,
            retry_delays_seconds=(0.001,),
        )
        record = await dispatcher.submit(_request(speak=True))
        await asyncio.sleep(0.02)
        await dispatcher.stop()
        return doorbell.wakes, dispatcher.store.require(record.delivery_id)

    wakes, record = asyncio.run(scenario())
    assert wakes == ["aa"]
    assert record.reason.value == "dispatcher_stopped"


def test_concurrent_connection_waiters_share_one_wake_flow():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        doorbell = FakeDoorbell(can_attempt_wake=True)
        dispatcher = XiaoxinEventDispatcher(
            registry,
            XiaoxinDeliveryStore(),
            doorbell,
            retry_delays_seconds=(0.001,),
        )
        first = asyncio.create_task(dispatcher._wait_for_connection("aa"))
        second = asyncio.create_task(dispatcher._wait_for_connection("aa"))
        await asyncio.sleep(0.02)
        conn = FakeConnection()
        registry.register_connection("aa", conn, "websocket")
        results = await asyncio.gather(first, second)
        await dispatcher.stop()
        return doorbell.wakes, results, conn

    wakes, results, conn = asyncio.run(scenario())
    assert wakes == ["aa"]
    assert results == [conn, conn]


def test_track_task_completion_cannot_remove_replacement_task_by_key():
    async def scenario():
        dispatcher = XiaoxinEventDispatcher(
            XiaoxinDeviceRegistry(), XiaoxinDeliveryStore(), FakeDoorbell()
        )
        tasks = {}
        release = asyncio.Event()

        async def first():
            await asyncio.sleep(0)

        async def replacement():
            await release.wait()

        first_task = dispatcher._track_task(tasks, "delivery", first())
        replacement_task = dispatcher._track_task(tasks, "delivery", replacement())
        await first_task
        await asyncio.sleep(0)
        retained = tasks.get("delivery") is replacement_task
        release.set()
        await replacement_task
        return retained, tasks

    retained, tasks = asyncio.run(scenario())
    assert retained is True
    assert tasks == {}


def test_active_speaking_delivery_survives_history_trimming_and_reconnects():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore(limit=2)
        dispatcher = XiaoxinEventDispatcher(
            registry,
            store,
            FakeDoorbell(),
            retry_delays_seconds=(0.001,),
        )
        active = await dispatcher.submit(_request(speak=True))
        await _wait_until(
            lambda: store.require(active.delivery_id).state
            == XiaoxinDeliveryState.RETRY_WAIT
        )

        for index in range(4):
            request = parse_control_event_request(
                {
                    "device_id": f"history-{index}",
                    "event": "notification",
                    "title": "History",
                    "body": str(index),
                }
            )
            record = store.create(
                request, build_xiaoxin_event_payload("ignored", request)
            )
            store.transition(record.delivery_id, XiaoxinDeliveryState.DONE)

        retained_before_reconnect = store.get(active.delivery_id) is not None

        class CompletingConnection(FakeConnection):
            async def send_xiaoxin_event(self, payload):
                self.sent.append(payload)
                await dispatcher.handle_ack(
                    "aa",
                    {
                        "type": "xiaoxin_ack",
                        "delivery_id": payload["delivery_id"],
                        "state": "device_received",
                    },
                    self,
                )

            async def speak_from_control_console(self, text, delivery_id, sentence_id):
                self.spoken.append((text, delivery_id, sentence_id))
                dispatcher.mark_tts_done(delivery_id, sentence_id)

        conn = CompletingConnection()
        registry.register_connection("aa", conn, "websocket")
        await dispatcher.wait_for_delivery_task(active.delivery_id)
        return retained_before_reconnect, store.require(active.delivery_id), conn

    retained, record, conn = asyncio.run(scenario())
    assert retained is True
    assert conn.sent[0]["delivery_id"] == record.delivery_id
    assert len(conn.spoken) == 1
    assert record.state == XiaoxinDeliveryState.DONE


def test_retry_ack_inside_send_does_not_regress_done_or_start_second_tts_task():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()

        class CountingDispatcher(XiaoxinEventDispatcher):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.tts_run_count = 0

            async def _run_tts_delivery(self, delivery_id):
                self.tts_run_count += 1
                await super()._run_tts_delivery(delivery_id)

        class AckOnRetryConnection(FakeConnection):
            async def send_xiaoxin_event(self, payload):
                self.sent.append(payload)
                if len(self.sent) == 2:
                    await dispatcher.handle_ack(
                        "aa",
                        {
                            "type": "xiaoxin_ack",
                            "delivery_id": payload["delivery_id"],
                            "state": "device_received",
                        },
                        self,
                    )

            async def speak_from_control_console(self, text, delivery_id, sentence_id):
                self.spoken.append((text, delivery_id, sentence_id))
                dispatcher.mark_tts_done(delivery_id, sentence_id)

        conn = AckOnRetryConnection()
        registry.register_connection("aa", conn, "websocket")
        dispatcher = CountingDispatcher(
            registry,
            store,
            FakeDoorbell(),
            ack_timeout_seconds=0.01,
            retry_delays_seconds=(0.001,),
        )
        record = await dispatcher.submit(_request(speak=True))
        await dispatcher.wait_for_delivery_task(record.delivery_id)
        current = store.require(record.delivery_id)
        states = [entry.state for entry in current.timeline]
        first_done = states.index(XiaoxinDeliveryState.DONE)
        return current, conn, dispatcher.tts_run_count, states[first_done + 1 :]

    record, conn, tts_run_count, states_after_done = asyncio.run(scenario())
    assert len(conn.sent) == 2
    assert len(conn.spoken) == 1
    assert tts_run_count == 1
    assert states_after_done == []
    assert record.state == XiaoxinDeliveryState.DONE


def test_submit_after_stop_rejects_without_record_or_task_leak():
    async def scenario():
        store = XiaoxinDeliveryStore()
        dispatcher = XiaoxinEventDispatcher(
            XiaoxinDeviceRegistry(), store, FakeDoorbell()
        )
        await dispatcher.stop()
        with pytest.raises(RuntimeError, match="stopped") as exc_info:
            await dispatcher.submit(_request(speak=True))
        return store.list_recent(), dispatcher, exc_info.type.__name__

    records, dispatcher, exception_type = asyncio.run(scenario())
    assert records == []
    assert exception_type == "DispatcherStoppedError"
    assert dispatcher._event_tasks == {}
    assert dispatcher._tts_tasks == {}
    assert dispatcher._connection_tasks == {}


def test_tracked_task_exception_is_consumed_logged_and_identity_cleaned():
    async def scenario():
        errors = []

        class FakeLogger:
            def bind(self, **kwargs):
                return self

            def error(self, message):
                errors.append(message)

        dispatcher = XiaoxinEventDispatcher(
            XiaoxinDeviceRegistry(), XiaoxinDeliveryStore(), FakeDoorbell()
        )
        dispatcher.logger = FakeLogger()
        tasks = {}

        async def fail():
            raise ValueError("unexpected task failure")

        task = dispatcher._track_task(tasks, "delivery-1", fail())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return errors, tasks, task

    errors, tasks, task = asyncio.run(scenario())
    assert tasks == {}
    assert len(errors) == 1
    assert "delivery-1" in errors[0]
    assert "unexpected task failure" in errors[0]
    assert task._log_traceback is False


def test_terminal_trim_while_event_task_unwinds_finishes_without_error():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore(limit=1)
        errors = []

        class FakeLogger:
            def bind(self, **kwargs):
                return self

            def error(self, message):
                errors.append(message)

        other_request = parse_control_event_request(
            {
                "device_id": "other",
                "event": "notification",
                "title": "Other active",
                "body": "Keep me",
                "speak": True,
            }
        )
        other = store.create(
            other_request,
            build_xiaoxin_event_payload("ignored", other_request),
        )

        class TtsFirstConnection(FakeConnection):
            async def speak_from_control_console(self, text, delivery_id, sentence_id):
                self.spoken.append((text, delivery_id, sentence_id))
                dispatcher.mark_tts_done(delivery_id, sentence_id)

        conn = TtsFirstConnection()
        registry.register_connection("aa", conn, "websocket")
        dispatcher = XiaoxinEventDispatcher(
            registry,
            store,
            FakeDoorbell(),
            ack_timeout_seconds=1,
            retry_delays_seconds=(0.001,),
        )
        dispatcher.logger = FakeLogger()
        delivery = await dispatcher.submit(_request(speak=True))
        await _wait_until(
            lambda: delivery.tts_state == "done"
            and delivery.delivery_id in dispatcher._event_ack_futures
        )

        await dispatcher.handle_ack(
            "aa",
            {
                "type": "xiaoxin_ack",
                "delivery_id": delivery.delivery_id,
                "state": "device_received",
            },
            conn,
        )
        await dispatcher.wait_for_delivery_task(delivery.delivery_id)
        return delivery, other, store, dispatcher, errors

    delivery, other, store, dispatcher, errors = asyncio.run(scenario())
    assert delivery.state == XiaoxinDeliveryState.DONE
    assert store.get(delivery.delivery_id) is None
    assert store.get(other.delivery_id) is other
    assert dispatcher._event_tasks == {}
    assert errors == []
    states = [entry.state for entry in delivery.timeline]
    assert states[-1] == XiaoxinDeliveryState.DONE
    assert (
        XiaoxinDeliveryState.RETRY_WAIT
        not in states[states.index(XiaoxinDeliveryState.DONE) + 1 :]
    )


def test_immediate_stop_terminalizes_accepted_delivery_once_without_wake():
    async def scenario():
        class CountingStore(XiaoxinDeliveryStore):
            def __init__(self):
                super().__init__()
                self.notifications = 0

            def _notify(self):
                self.notifications += 1

        doorbell = FakeDoorbell(can_attempt_wake=True)
        store = CountingStore()
        dispatcher = XiaoxinEventDispatcher(XiaoxinDeviceRegistry(), store, doorbell)
        record = await dispatcher.submit(_request(speak=True))
        await dispatcher.stop()
        await asyncio.sleep(0.02)
        return record, store.notifications, doorbell.wakes, dispatcher

    record, notifications, wakes, dispatcher = asyncio.run(scenario())
    assert record.state == XiaoxinDeliveryState.FAILED
    assert record.reason is not None
    assert record.reason.value == "dispatcher_stopped"
    assert [entry.state for entry in record.timeline] == [
        XiaoxinDeliveryState.CREATED,
        XiaoxinDeliveryState.FAILED,
    ]
    assert notifications == 2
    assert wakes == []
    assert dispatcher._event_tasks == {}
    assert dispatcher._tts_tasks == {}


def test_same_device_reliable_tts_deliveries_are_serialized_without_supersession_loop():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        dispatcher = XiaoxinEventDispatcher(
            registry,
            store,
            FakeDoorbell(),
            retry_delays_seconds=(0.001,),
        )

        class FirmwareFaithfulConnection(FakeConnection):
            def __init__(self):
                super().__init__()
                self.active = None
                self.completion_task = None

            async def speak_from_control_console(self, text, delivery_id, sentence_id):
                self.spoken.append((text, delivery_id, sentence_id))
                if self.active is not None:
                    old_delivery, old_sentence = self.active
                    dispatcher.mark_tts_attempt_failed(
                        old_delivery, old_sentence, "superseded"
                    )
                    if self.completion_task is not None:
                        self.completion_task.cancel()
                self.active = (delivery_id, sentence_id)

                async def complete_if_still_owned():
                    await asyncio.sleep(0.01)
                    if self.active == (delivery_id, sentence_id):
                        dispatcher.mark_tts_done(delivery_id, sentence_id)
                        self.active = None

                self.completion_task = asyncio.create_task(complete_if_still_owned())

        conn = FirmwareFaithfulConnection()
        registry.register_connection("aa", conn, "websocket")
        records = []
        for _ in range(2):
            request = _request(speak=True)
            record = store.create(
                request, build_xiaoxin_event_payload("ignored", request)
            )
            store.mark_event_acknowledged(
                record.delivery_id, {"state": "device_received"}
            )
            records.append(record)

        tasks = [
            asyncio.create_task(dispatcher._run_tts_delivery(record.delivery_id))
            for record in records
        ]
        completed = True
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), 0.3)
        except asyncio.TimeoutError:
            completed = False
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            if conn.completion_task is not None:
                await asyncio.gather(conn.completion_task, return_exceptions=True)
            await dispatcher.stop()
        return completed, [store.require(r.delivery_id) for r in records], conn.spoken

    completed, records, spoken = asyncio.run(scenario())
    assert completed is True
    assert [record.state for record in records] == [
        XiaoxinDeliveryState.DONE,
        XiaoxinDeliveryState.DONE,
    ]
    assert len(spoken) == 2
    assert len({item[2] for item in spoken}) == 2
    assert [item[0] for item in spoken] == ["Complete reminder text"] * 2


def test_different_device_reliable_tts_deliveries_can_overlap():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        dispatcher = XiaoxinEventDispatcher(registry, store, FakeDoorbell())
        both_started = asyncio.Event()
        started = []

        class OverlapConnection(FakeConnection):
            def __init__(self, device_id):
                super().__init__()
                self.device_id = device_id

            async def speak_from_control_console(self, text, delivery_id, sentence_id):
                started.append(self.device_id)
                if len(started) == 2:
                    both_started.set()
                await asyncio.wait_for(both_started.wait(), 0.1)
                dispatcher.mark_tts_done(delivery_id, sentence_id)

        records = []
        for device_id in ("aa", "bb"):
            conn = OverlapConnection(device_id)
            registry.register_connection(device_id, conn, "websocket")
            request = _device_request(device_id)
            record = store.create(
                request, build_xiaoxin_event_payload("ignored", request)
            )
            store.mark_event_acknowledged(
                record.delivery_id, {"state": "device_received"}
            )
            records.append(record)

        await asyncio.gather(
            *(dispatcher._run_tts_delivery(record.delivery_id) for record in records)
        )
        return started, [store.require(r.delivery_id).state for r in records]

    started, states = asyncio.run(scenario())
    assert set(started) == {"aa", "bb"}
    assert states == [XiaoxinDeliveryState.DONE, XiaoxinDeliveryState.DONE]


def test_connection_wait_recovers_from_wake_and_registry_exceptions_then_completes():
    async def scenario():
        class FlakyRegistry(XiaoxinDeviceRegistry):
            def __init__(self):
                super().__init__()
                self.wait_calls = 0

            async def wait_for_connected(self, device_id, timeout_seconds):
                self.wait_calls += 1
                if self.wait_calls == 1:
                    raise RuntimeError("registry transient")
                return await super().wait_for_connected(device_id, timeout_seconds)

        class FlakyDoorbell(FakeDoorbell):
            def __init__(self):
                super().__init__(can_attempt_wake=True)
                self.calls = 0

            def publish_wake(self, device_id):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("wake transient")
                return super().publish_wake(device_id)

        registry = FlakyRegistry()
        store = XiaoxinDeliveryStore()
        doorbell = FlakyDoorbell()
        dispatcher = XiaoxinEventDispatcher(
            registry,
            store,
            doorbell,
            ack_timeout_seconds=0.2,
            retry_delays_seconds=(0.001,),
        )

        class CompletingConnection(FakeConnection):
            async def send_xiaoxin_event(self, payload):
                self.sent.append(payload)
                await dispatcher.handle_ack(
                    "aa",
                    {
                        "type": "xiaoxin_ack",
                        "delivery_id": payload["delivery_id"],
                        "state": "device_received",
                    },
                    self,
                )

            async def speak_from_control_console(self, text, delivery_id, sentence_id):
                self.spoken.append((text, delivery_id, sentence_id))
                dispatcher.mark_tts_done(delivery_id, sentence_id)

        record = await dispatcher.submit(_request(speak=True))
        await asyncio.sleep(0.12)
        conn = CompletingConnection()
        registry.register_connection("aa", conn, "websocket")
        await asyncio.wait_for(
            dispatcher.wait_for_delivery_task(record.delivery_id), 0.5
        )
        return store.require(record.delivery_id), doorbell, registry, dispatcher

    record, doorbell, registry, dispatcher = asyncio.run(scenario())
    assert doorbell.calls >= 2
    assert registry.wait_calls >= 2
    assert record.state == XiaoxinDeliveryState.DONE
    assert dispatcher._event_tasks == {}
    assert dispatcher._tts_tasks == {}


def test_repeated_wake_failures_are_bounded_stoppable_and_keep_delivery_owned():
    async def scenario():
        class AlwaysRaisingDoorbell(FakeDoorbell):
            def __init__(self):
                super().__init__(can_attempt_wake=True)
                self.calls = 0

            def publish_wake(self, device_id):
                self.calls += 1
                raise RuntimeError("wake unavailable")

        doorbell = AlwaysRaisingDoorbell()
        dispatcher = XiaoxinEventDispatcher(
            XiaoxinDeviceRegistry(),
            XiaoxinDeliveryStore(),
            doorbell,
            retry_delays_seconds=(0.001,),
        )
        record = await dispatcher.submit(_request(speak=True))
        await asyncio.sleep(0.12)
        owned_before_stop = record.delivery_id in dispatcher._event_tasks
        current_before_stop = dispatcher.store.require(record.delivery_id).state
        await asyncio.wait_for(dispatcher.stop(), 0.2)
        return (
            doorbell.calls,
            owned_before_stop,
            current_before_stop,
            record,
            dispatcher,
        )

    calls, owned, before_stop, record, dispatcher = asyncio.run(scenario())
    assert 1 <= calls <= 4
    assert owned is True
    assert before_stop == XiaoxinDeliveryState.RETRY_WAIT
    assert record.state == XiaoxinDeliveryState.FAILED
    assert record.reason.value == "dispatcher_stopped"
    assert dispatcher._connection_tasks == {}


def test_stop_cancellation_releases_device_tts_lease():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        started = asyncio.Event()

        class BlockingConnection(FakeConnection):
            async def speak_from_control_console(self, text, delivery_id, sentence_id):
                self.spoken.append((text, delivery_id, sentence_id))
                started.set()
                await asyncio.Event().wait()

        conn = BlockingConnection()
        registry.register_connection("aa", conn, "websocket")
        dispatcher = XiaoxinEventDispatcher(registry, store, FakeDoorbell())
        record = _speaking_record(store)
        dispatcher._track_task(
            dispatcher._tts_tasks,
            record.delivery_id,
            dispatcher._run_tts_delivery(record.delivery_id),
        )
        await started.wait()
        lease = dispatcher._device_tts_locks["aa"]
        locked_before_stop = lease.locked()
        await dispatcher.stop()
        return locked_before_stop, lease.locked(), record, dispatcher

    locked_before, locked_after, record, dispatcher = asyncio.run(scenario())
    assert locked_before is True
    assert locked_after is False
    assert record.state == XiaoxinDeliveryState.FAILED
    assert record.reason.value == "dispatcher_stopped"
    assert dispatcher._tts_tasks == {}


@pytest.mark.parametrize("ack_state", ["ready", "done"])
def test_connection_close_waiter_failure_releases_lease_and_retries_on_replacement(
    ack_state,
):
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        waiter_started = asyncio.Event()
        failure_at = None
        replacement_started_at = None

        class CountingDispatcher(XiaoxinEventDispatcher):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.failure_callbacks = []

            def mark_tts_attempt_failed(self, delivery_id, sentence_id, reason):
                self.failure_callbacks.append((delivery_id, sentence_id, reason))
                super().mark_tts_attempt_failed(delivery_id, sentence_id, reason)

        dispatcher = CountingDispatcher(
            registry,
            store,
            FakeDoorbell(),
            retry_delays_seconds=(0.03,),
        )

        class AckLifecycleConnection(FakeConnection):
            _tts_ack_key = ConnectionHandler._tts_ack_key
            _prune_tts_completed_acks = ConnectionHandler._prune_tts_completed_acks
            _set_tts_attempt_phase = ConnectionHandler._set_tts_attempt_phase
            _terminalize_tts_attempt_failure = (
                ConnectionHandler._terminalize_tts_attempt_failure
            )
            _retire_tts_ack_phase = ConnectionHandler._retire_tts_ack_phase
            begin_tts_ack_wait = ConnectionHandler.begin_tts_ack_wait
            wait_for_tts_ack = ConnectionHandler.wait_for_tts_ack
            mark_xiaoxin_control_tts_failed = (
                ConnectionHandler.mark_xiaoxin_control_tts_failed
            )

            def __init__(self):
                super().__init__()
                self.sentence_id = None
                self.client_abort = False
                self.client_is_speaking = False
                self.tts_ack_waiters = {}
                self.tts_ack_completed = {}
                self._tts_ack_wait_subscribers = {}
                self._tts_ack_active_phases = {}
                self._tts_attempt_phases = {}
                self._tts_attempt_phase_updated_at = {}
                self._tts_terminal_results = {}
                self.tts_ack_completed_ttl_seconds = 30.0
                self.xiaoxin_control_tts_deliveries = {}
                self.xiaoxin_control_runtime = SimpleNamespace(dispatcher=dispatcher)
                self.wait_results = []

            async def speak_from_control_console(self, text, delivery_id, sentence_id):
                self.spoken.append((text, delivery_id, sentence_id))
                self.sentence_id = sentence_id
                self.xiaoxin_control_tts_deliveries[sentence_id] = delivery_id
                if ack_state == "done":
                    self._set_tts_attempt_phase(sentence_id, TTS_PHASE_STREAMING)
                self.begin_tts_ack_wait(ack_state, sentence_id)
                waiter_started.set()
                result = await self.wait_for_tts_ack(ack_state, sentence_id, 1000)
                self.wait_results.append(result)
                if result.state == "error":
                    raise TtsAttemptError(sentence_id, result.reason)

        class ReplacementConnection(FakeConnection):
            async def speak_from_control_console(self, text, delivery_id, sentence_id):
                nonlocal replacement_started_at
                replacement_started_at = asyncio.get_running_loop().time()
                self.spoken.append((text, delivery_id, sentence_id))
                dispatcher.mark_tts_done(delivery_id, sentence_id)

        first = AckLifecycleConnection()
        replacement = ReplacementConnection()
        registry.register_connection("aa", first, "websocket")
        record = _speaking_record(store)
        delivery_task = asyncio.create_task(
            dispatcher._run_tts_delivery(record.delivery_id)
        )

        await waiter_started.wait()
        failure_at = asyncio.get_running_loop().time()
        first.mark_xiaoxin_control_tts_failed(
            first.sentence_id, "connection_closed_before_done"
        )
        registry.unregister_connection("aa", first)
        registry.register_connection("aa", replacement, "websocket")
        await asyncio.wait_for(delivery_task, 0.5)
        lease = dispatcher._device_tts_locks["aa"]
        return (
            store.require(record.delivery_id),
            first,
            replacement,
            dispatcher.failure_callbacks,
            replacement_started_at - failure_at,
            lease.locked(),
        )

    record, first, replacement, callbacks, retry_elapsed, lease_locked = asyncio.run(
        scenario()
    )
    assert len(first.wait_results) == 1
    assert first.wait_results[0].state == "error"
    assert first.wait_results[0].reason == "connection_closed_before_done"
    assert callbacks == [
        (
            record.delivery_id,
            first.spoken[0][2],
            "connection_closed_before_done",
        )
    ]
    assert retry_elapsed >= 0.02
    assert len(first.spoken) == 1
    assert len(replacement.spoken) == 1
    assert first.spoken[0][2] != replacement.spoken[0][2]
    assert replacement.spoken[0][0] == "Complete reminder text"
    assert record.state == XiaoxinDeliveryState.DONE
    assert lease_locked is False


def test_resolved_done_then_connection_cleanup_completes_once_without_retry():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        waiter_started = asyncio.Event()

        class CountingDispatcher(XiaoxinEventDispatcher):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.done_callbacks = []
                self.failure_callbacks = []

            def mark_tts_done(self, delivery_id, sentence_id):
                self.done_callbacks.append((delivery_id, sentence_id))
                super().mark_tts_done(delivery_id, sentence_id)

            def mark_tts_attempt_failed(self, delivery_id, sentence_id, reason):
                self.failure_callbacks.append((delivery_id, sentence_id, reason))
                super().mark_tts_attempt_failed(delivery_id, sentence_id, reason)

        dispatcher = CountingDispatcher(
            registry,
            store,
            FakeDoorbell(),
            retry_delays_seconds=(0.001,),
        )

        class DoneRaceConnection(FakeConnection):
            _tts_ack_key = ConnectionHandler._tts_ack_key
            _prune_tts_completed_acks = ConnectionHandler._prune_tts_completed_acks
            _set_tts_attempt_phase = ConnectionHandler._set_tts_attempt_phase
            _terminalize_tts_attempt_failure = (
                ConnectionHandler._terminalize_tts_attempt_failure
            )
            _retire_tts_ack_phase = ConnectionHandler._retire_tts_ack_phase
            begin_tts_ack_wait = ConnectionHandler.begin_tts_ack_wait
            resolve_tts_ack = ConnectionHandler.resolve_tts_ack
            wait_for_tts_ack = ConnectionHandler.wait_for_tts_ack
            mark_xiaoxin_control_tts_done = (
                ConnectionHandler.mark_xiaoxin_control_tts_done
            )
            mark_xiaoxin_control_tts_failed = (
                ConnectionHandler.mark_xiaoxin_control_tts_failed
            )

            def __init__(self):
                super().__init__()
                self.sentence_id = None
                self.client_abort = False
                self.client_is_speaking = False
                self.tts_ack_waiters = {}
                self.tts_ack_completed = {}
                self._tts_ack_wait_subscribers = {}
                self._tts_ack_active_phases = {}
                self._tts_attempt_phases = {}
                self._tts_attempt_phase_updated_at = {}
                self._tts_terminal_results = {}
                self.tts_ack_completed_ttl_seconds = 30.0
                self.xiaoxin_control_tts_deliveries = {}
                self.xiaoxin_control_runtime = SimpleNamespace(dispatcher=dispatcher)

            async def speak_from_control_console(self, text, delivery_id, sentence_id):
                self.spoken.append((text, delivery_id, sentence_id))
                if len(self.spoken) > 1:
                    dispatcher.mark_tts_done(delivery_id, sentence_id)
                    return
                self.sentence_id = sentence_id
                self.xiaoxin_control_tts_deliveries[sentence_id] = delivery_id
                self._set_tts_attempt_phase(sentence_id, TTS_PHASE_STREAMING)
                self.begin_tts_ack_wait("done", sentence_id)
                waiter_started.set()
                result = await self.wait_for_tts_ack("done", sentence_id, 1000)
                if result.state == "done":
                    self.mark_xiaoxin_control_tts_done(sentence_id)

        conn = DoneRaceConnection()
        registry.register_connection("aa", conn, "websocket")
        record = _speaking_record(store)
        delivery_task = asyncio.create_task(
            dispatcher._run_tts_delivery(record.delivery_id)
        )
        await waiter_started.wait()
        first_sentence_id = conn.sentence_id
        assert conn.resolve_tts_ack("done", first_sentence_id) is True
        conn.mark_xiaoxin_control_tts_failed(
            first_sentence_id, "connection_closed_before_done"
        )
        await asyncio.wait_for(delivery_task, 0.5)
        return store.require(record.delivery_id), conn, dispatcher

    record, conn, dispatcher = asyncio.run(scenario())
    assert conn.spoken == [
        ("Complete reminder text", record.delivery_id, conn.spoken[0][2])
    ]
    assert record.tts_attempt_count == 1
    assert dispatcher.done_callbacks == [(record.delivery_id, conn.spoken[0][2])]
    assert dispatcher.failure_callbacks == []
    assert record.state == XiaoxinDeliveryState.DONE
