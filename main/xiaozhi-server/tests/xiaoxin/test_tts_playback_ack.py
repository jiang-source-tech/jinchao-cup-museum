import asyncio
from types import SimpleNamespace

import pytest

from core.connection import (
    ConnectionHandler,
    TTS_PHASE_DONE_WAIT,
    TTS_PHASE_STREAMING,
    TTS_PHASE_TERMINAL,
)
from core.handle.textHandler.ttsMessageHandler import TtsTextMessageHandler
from core.handle.textMessageHandlerRegistry import TextMessageHandlerRegistry
from core.handle.textMessageType import TextMessageType


class FakeServer:
    xiaoxin_runtime = None


def make_conn(features=None):
    conn = ConnectionHandler(
        config={
            "exit_commands": [],
            "close_connection_no_voice_time": 120,
            "xiaozhi": {"audio_params": {"sample_rate": 24000}},
        },
        _vad=None,
        _asr=None,
        _llm=None,
        _memory=None,
        _intent=None,
        server=FakeServer(),
    )
    conn.features = features
    return conn


def test_tts_ack_support_flags_default_to_false():
    conn = make_conn(features=None)

    assert conn.supports_tts_ready_ack() is False
    assert conn.supports_tts_done_ack() is False


def test_tts_ack_support_flags_read_device_features():
    conn = make_conn(features={"tts_ready_ack": True, "tts_done_ack": True})

    assert conn.supports_tts_ready_ack() is True
    assert conn.supports_tts_done_ack() is True


def test_reliable_tts_requires_all_three_features():
    conn = make_conn(
        features={
            "tts_ready_ack": True,
            "tts_done_ack": True,
            "tts_preroll_buffer": True,
        }
    )
    assert conn.supports_reliable_notification_tts() is True
    conn.features.pop("tts_preroll_buffer")
    assert conn.supports_reliable_notification_tts() is False


@pytest.mark.parametrize("malformed", ["true", 1, object(), None])
def test_reliable_tts_capability_flags_require_exact_true(malformed):
    features = {
        "tts_ready_ack": True,
        "tts_done_ack": True,
        "tts_preroll_buffer": True,
    }
    conn = make_conn(features=features)

    for key, method_name in (
        ("tts_ready_ack", "supports_tts_ready_ack"),
        ("tts_done_ack", "supports_tts_done_ack"),
        ("tts_preroll_buffer", "supports_tts_preroll_buffer"),
    ):
        conn.features = dict(features, **{key: malformed})
        assert getattr(conn, method_name)() is False
        assert conn.supports_reliable_notification_tts() is False


def test_wait_for_tts_ack_returns_typed_success():
    async def scenario():
        conn = make_conn()
        conn.begin_tts_ack_wait("ready", "sentence-1")
        conn.resolve_tts_ack("ready", "sentence-1")
        return await conn.wait_for_tts_ack("ready", "sentence-1", 10)

    result = asyncio.run(scenario())
    assert result.state == "ready"
    assert result.sentence_id == "sentence-1"
    assert result.reason is None


def test_error_ack_resolves_current_waiter_with_reason():
    async def scenario():
        conn = make_conn()
        conn.begin_tts_ack_wait("ready", "sentence-1")
        assert conn.resolve_tts_error("sentence-1", "preroll_overflow") is True
        return await conn.wait_for_tts_ack("ready", "sentence-1", 10)

    result = asyncio.run(scenario())
    assert result.state == "error"
    assert result.reason == "preroll_overflow"


def test_tts_handler_rejects_wrong_session_id():
    async def scenario():
        conn = make_conn()
        conn.session_id = "current-session"
        waiter = conn.begin_tts_ack_wait("done", "sentence-1")
        await TtsTextMessageHandler().handle(
            conn,
            {
                "type": "tts",
                "state": "done",
                "session_id": "old-session",
                "sentence_id": "sentence-1",
            },
        )
        return waiter.done()

    assert asyncio.run(scenario()) is False


def test_resolve_tts_ack_releases_matching_waiter():
    async def scenario():
        conn = make_conn()
        waiter = conn.begin_tts_ack_wait("ready", "sentence-1")

        resolved = conn.resolve_tts_ack("ready", "sentence-1")

        return resolved, waiter.done()

    assert asyncio.run(scenario()) == (True, True)


def test_resolve_tts_ack_ignores_wrong_sentence():
    async def scenario():
        conn = make_conn()
        waiter = conn.begin_tts_ack_wait("ready", "sentence-1")

        resolved = conn.resolve_tts_ack("ready", "sentence-2")

        return resolved, waiter.done()

    assert asyncio.run(scenario()) == (False, False)


def test_wait_for_tts_ack_returns_none_on_timeout():
    async def scenario():
        conn = make_conn()
        waiter = conn.begin_tts_ack_wait("ready", "sentence-1")

        result = await conn.wait_for_tts_ack("ready", "sentence-1", timeout_ms=1)
        return (
            result,
            waiter.cancelled(),
            conn.tts_ack_waiters,
            conn._tts_ack_wait_subscribers,
            conn._tts_ack_active_phases,
        )

    result, cancelled, waiters, subscribers, phases = asyncio.run(scenario())

    assert result is None
    assert cancelled is False
    assert waiters == {}
    assert subscribers == {}
    assert phases == {}


def test_wait_for_tts_ack_consumes_early_resolved_ack_without_timeout():
    async def scenario():
        conn = make_conn()
        conn.begin_tts_ack_wait("ready", "sentence-1")

        resolved = conn.resolve_tts_ack("ready", "sentence-1")
        waited = await conn.wait_for_tts_ack("ready", "sentence-1", timeout_ms=1)

        return (
            resolved,
            waited,
            conn.tts_ack_waiters,
            conn.tts_ack_completed,
            conn._tts_ack_wait_subscribers,
            conn._tts_ack_active_phases,
        )

    resolved, waited, waiters, completed, subscribers, phases = asyncio.run(scenario())

    assert resolved is True
    assert waited.successful is True
    assert waiters == {}
    assert completed == {}
    assert subscribers == {}
    assert phases == {}


def test_begin_same_tts_ack_phase_reuses_waiter():
    async def scenario():
        conn = make_conn()
        first = conn.begin_tts_ack_wait("ready", "sentence-1")
        second = conn.begin_tts_ack_wait("ready", "sentence-1")
        return first is second

    assert asyncio.run(scenario()) is True


def test_short_timeout_does_not_orphan_shared_long_waiter():
    async def scenario():
        conn = make_conn()
        conn.begin_tts_ack_wait("ready", "sentence-1")
        short_wait = asyncio.create_task(
            conn.wait_for_tts_ack("ready", "sentence-1", timeout_ms=10)
        )
        long_wait = asyncio.create_task(
            conn.wait_for_tts_ack("ready", "sentence-1", timeout_ms=200)
        )

        assert await short_wait is None
        resolved = conn.resolve_tts_ack("ready", "sentence-1")
        result = await long_wait
        return (
            resolved,
            result,
            conn.tts_ack_waiters,
            conn._tts_ack_wait_subscribers,
            conn._tts_ack_active_phases,
        )

    resolved, result, waiters, subscribers, phases = asyncio.run(scenario())

    assert resolved is True
    assert result.successful is True
    assert waiters == {}
    assert subscribers == {}
    assert phases == {}


def test_cancelled_wait_does_not_orphan_shared_long_waiter():
    async def scenario():
        conn = make_conn()
        conn.begin_tts_ack_wait("ready", "sentence-1")
        cancelled_wait = asyncio.create_task(
            conn.wait_for_tts_ack("ready", "sentence-1", timeout_ms=200)
        )
        long_wait = asyncio.create_task(
            conn.wait_for_tts_ack("ready", "sentence-1", timeout_ms=200)
        )
        await asyncio.sleep(0)

        cancelled_wait.cancel()
        try:
            await cancelled_wait
        except asyncio.CancelledError:
            pass

        resolved = conn.resolve_tts_ack("ready", "sentence-1")
        result = await long_wait
        return (
            resolved,
            result,
            conn.tts_ack_waiters,
            conn._tts_ack_wait_subscribers,
            conn._tts_ack_active_phases,
        )

    resolved, result, waiters, subscribers, phases = asyncio.run(scenario())

    assert resolved is True
    assert result.successful is True
    assert waiters == {}
    assert subscribers == {}
    assert phases == {}


def test_error_ack_resolves_active_done_phase_not_stale_ready_phase():
    async def scenario():
        conn = make_conn()
        stale_ready = conn.begin_tts_ack_wait("ready", "sentence-1")
        conn.resolve_tts_ack("ready", "sentence-1")
        await conn.wait_for_tts_ack("ready", "sentence-1", 10)
        conn.mark_tts_streaming("sentence-1")
        conn.begin_tts_ack_wait("done", "sentence-1")

        resolved = conn.resolve_tts_error("sentence-1", "playback_failed")
        result = await conn.wait_for_tts_ack("done", "sentence-1", 10)
        return (
            resolved,
            result,
            stale_ready.done(),
            conn.tts_ack_waiters,
            conn._tts_ack_active_phases,
        )

    resolved, result, stale_ready_finished, waiters, phases = asyncio.run(scenario())

    assert resolved is True
    assert result.state == "error"
    assert result.reason == "playback_failed"
    assert stale_ready_finished is True
    assert waiters == {}
    assert phases == {}


def test_done_wait_cannot_skip_streaming_phase():
    async def scenario():
        conn = make_conn()
        ready_waiter = conn.begin_tts_ack_wait("ready", "sentence-1")
        invalid_done = conn.begin_tts_ack_wait("done", "sentence-1")
        return await invalid_done, ready_waiter.cancelled()

    result, ready_cancelled = asyncio.run(scenario())
    assert result.state == "error"
    assert result.reason == "invalid_ack_phase"
    assert ready_cancelled is False


def test_premature_done_during_streaming_is_not_cached_or_consumed():
    async def scenario():
        conn = make_conn()
        conn.begin_tts_ack_wait("ready", "sentence-1")
        conn.resolve_tts_ack("ready", "sentence-1")
        ready = await conn.wait_for_tts_ack("ready", "sentence-1", 10)
        assert ready.state == "ready"
        assert conn.mark_tts_streaming("sentence-1") is True

        premature = conn.resolve_tts_ack("done", "sentence-1")
        done_waiter = conn.begin_tts_ack_wait("done", "sentence-1")
        still_pending = not done_waiter.done()
        matched = conn.resolve_tts_ack("done", "sentence-1")
        done = await conn.wait_for_tts_ack("done", "sentence-1", 10)
        return premature, still_pending, matched, done

    premature, still_pending, matched, done = asyncio.run(scenario())
    assert premature is False
    assert still_pending is True
    assert matched is True
    assert done.state == "done"


def test_streaming_error_is_terminal_and_cannot_later_become_done():
    async def scenario():
        conn = make_conn()
        conn.logger = FakeLogger()
        failures = []
        conn.mark_xiaoxin_control_tts_failed = (
            lambda sentence_id, reason: failures.append((sentence_id, reason))
        )
        conn.begin_tts_ack_wait("ready", "sentence-1")
        conn.resolve_tts_ack("ready", "sentence-1")
        await conn.wait_for_tts_ack("ready", "sentence-1", 10)
        conn.mark_tts_streaming("sentence-1")

        message = {
            "type": "tts",
            "state": "error",
            "session_id": conn.session_id,
            "sentence_id": "sentence-1",
            "reason": "decode_failed",
        }
        handler = TtsTextMessageHandler()
        await handler.handle(conn, message)
        await handler.handle(conn, message)
        done_resolved = conn.resolve_tts_ack("done", "sentence-1")
        terminal_wait = conn.begin_tts_ack_wait("done", "sentence-1")
        return failures, done_resolved, await terminal_wait

    failures, done_resolved, terminal_result = asyncio.run(scenario())
    assert failures == [("sentence-1", "decode_failed")]
    assert done_resolved is False
    assert terminal_result.state == "error"
    assert terminal_result.reason == "decode_failed"


def test_late_ready_between_same_sentence_start_retries_is_consumed():
    async def scenario():
        conn = make_conn()
        first = conn.begin_tts_ack_wait("ready", "sentence-1")
        timed_out = await conn.wait_for_tts_ack("ready", "sentence-1", 1)
        late_resolved = conn.resolve_tts_ack("ready", "sentence-1")
        second = conn.begin_tts_ack_wait("ready", "sentence-1")
        result = await conn.wait_for_tts_ack("ready", "sentence-1", 10)
        return timed_out, late_resolved, result

    timed_out, late_resolved, result = asyncio.run(scenario())
    assert timed_out is None
    assert late_resolved is True
    assert result.state == "ready"


def test_terminal_phase_ttl_prunes_waiter_and_duplicate_state():
    async def scenario():
        conn = make_conn()
        conn.begin_tts_ack_wait("ready", "sentence-1")
        assert conn.resolve_tts_error("sentence-1", "decoder_create_failed") is True
        assert conn.resolve_tts_error("sentence-1", "decode_failed") is False
        conn.tts_ack_completed = {
            key: (result, 0.0) for key, (result, _) in conn.tts_ack_completed.items()
        }
        conn._tts_attempt_phase_updated_at["sentence-1"] = 0.0
        conn.tts_ack_completed_ttl_seconds = 1
        conn._prune_tts_completed_acks()
        return (
            conn.tts_ack_waiters,
            conn.tts_ack_completed,
            conn._tts_ack_active_phases,
            conn._tts_attempt_phases,
            conn._tts_attempt_phase_updated_at,
            conn._tts_terminal_results,
        )

    waiters, completed, phases, attempts, timestamps, terminal_results = asyncio.run(
        scenario()
    )
    assert waiters == {}
    assert completed == {}
    assert phases == {}
    assert attempts == {}
    assert timestamps == {}
    assert terminal_results == {}


def test_streaming_phase_does_not_expire_by_age_and_error_still_terminalizes_once():
    async def scenario():
        conn = make_conn()
        conn.logger = FakeLogger()
        failures = []
        conn.mark_xiaoxin_control_tts_failed = (
            lambda sentence_id, reason: failures.append((sentence_id, reason))
        )
        conn._set_tts_attempt_phase("sentence-1", TTS_PHASE_STREAMING)
        conn._tts_attempt_phase_updated_at["sentence-1"] = 0.0
        conn.tts_ack_completed_ttl_seconds = 1
        conn._prune_tts_completed_acks()
        retained_phase = conn._tts_attempt_phases.get("sentence-1")
        message = {
            "type": "tts",
            "state": "error",
            "session_id": conn.session_id,
            "sentence_id": "sentence-1",
            "reason": "output_write_timeout",
        }
        handler = TtsTextMessageHandler()
        await handler.handle(conn, message)
        await handler.handle(conn, message)
        return retained_phase, conn._tts_attempt_phases.get("sentence-1"), failures

    retained, terminal, failures = asyncio.run(scenario())
    assert retained == TTS_PHASE_STREAMING
    assert terminal == TTS_PHASE_TERMINAL
    assert failures == [("sentence-1", "output_write_timeout")]


def test_done_wait_phase_and_waiter_do_not_expire_by_age():
    async def scenario():
        conn = make_conn()
        conn._set_tts_attempt_phase("sentence-1", TTS_PHASE_STREAMING)
        waiter = conn.begin_tts_ack_wait("done", "sentence-1")
        conn._tts_attempt_phase_updated_at["sentence-1"] = 0.0
        conn.tts_ack_completed_ttl_seconds = 1
        conn._prune_tts_completed_acks()
        retained = (
            conn._tts_attempt_phases.get("sentence-1"),
            conn.tts_ack_waiters.get(("done", "sentence-1")) is waiter,
            waiter.cancelled(),
        )
        conn.resolve_tts_error("sentence-1", "connection_closed_before_done")
        result = await conn.wait_for_tts_ack("done", "sentence-1", 10)
        return retained, result

    retained, result = asyncio.run(scenario())
    assert retained == (TTS_PHASE_DONE_WAIT, True, False)
    assert result.state == "error"
    assert result.reason == "connection_closed_before_done"


@pytest.mark.parametrize("state", ["ready", "done"])
def test_external_attempt_failure_completes_active_waiter_with_typed_error(state):
    async def scenario():
        conn = make_conn()
        callbacks = []
        conn.xiaoxin_control_runtime = SimpleNamespace(
            dispatcher=SimpleNamespace(
                mark_tts_attempt_failed=lambda delivery_id, sentence_id, reason: callbacks.append(
                    (delivery_id, sentence_id, reason)
                )
            )
        )
        conn.xiaoxin_control_tts_deliveries["sentence-1"] = "delivery-1"
        conn.sentence_id = "sentence-1"
        if state == "done":
            conn._set_tts_attempt_phase("sentence-1", TTS_PHASE_STREAMING)
        conn.begin_tts_ack_wait(state, "sentence-1")
        wait_task = asyncio.create_task(
            conn.wait_for_tts_ack(state, "sentence-1", 1000)
        )
        await asyncio.sleep(0)
        conn.mark_xiaoxin_control_tts_failed(
            "sentence-1", "connection_closed_before_done"
        )
        result = await wait_task
        return result, callbacks, conn._tts_attempt_phases.get("sentence-1")

    result, callbacks, phase = asyncio.run(scenario())
    assert result.state == "error"
    assert result.reason == "connection_closed_before_done"
    assert callbacks == [("delivery-1", "sentence-1", "connection_closed_before_done")]
    assert phase == TTS_PHASE_TERMINAL


def test_streaming_external_failure_terminalizes_without_waiter_and_notifies_once():
    conn = make_conn()
    callbacks = []
    conn.xiaoxin_control_runtime = SimpleNamespace(
        dispatcher=SimpleNamespace(
            mark_tts_attempt_failed=lambda delivery_id, sentence_id, reason: callbacks.append(
                (delivery_id, sentence_id, reason)
            )
        )
    )
    conn.xiaoxin_control_tts_deliveries["sentence-1"] = "delivery-1"
    conn.sentence_id = "sentence-1"
    conn._set_tts_attempt_phase("sentence-1", TTS_PHASE_STREAMING)

    conn.mark_xiaoxin_control_tts_failed("sentence-1", "connection_closed_before_done")
    conn.mark_xiaoxin_control_tts_failed("sentence-1", "connection_closed_before_done")

    assert conn._tts_attempt_phases["sentence-1"] == TTS_PHASE_TERMINAL
    assert callbacks == [("delivery-1", "sentence-1", "connection_closed_before_done")]


def test_resolved_done_survives_connection_failure_before_waiter_resumes():
    async def scenario():
        conn = make_conn()
        done_callbacks = []
        failed_callbacks = []
        conn.xiaoxin_control_runtime = SimpleNamespace(
            dispatcher=SimpleNamespace(
                mark_tts_done=lambda delivery_id, sentence_id: done_callbacks.append(
                    (delivery_id, sentence_id)
                ),
                mark_tts_attempt_failed=lambda delivery_id, sentence_id, reason: failed_callbacks.append(
                    (delivery_id, sentence_id, reason)
                ),
            )
        )
        conn.sentence_id = "sentence-1"
        conn.xiaoxin_control_tts_deliveries["sentence-1"] = "delivery-1"
        conn._set_tts_attempt_phase("sentence-1", TTS_PHASE_STREAMING)
        conn.begin_tts_ack_wait("done", "sentence-1")
        wait_task = asyncio.create_task(
            conn.wait_for_tts_ack("done", "sentence-1", 1000)
        )
        await asyncio.sleep(0)

        assert conn.resolve_tts_ack("done", "sentence-1") is True
        conn.mark_xiaoxin_control_tts_failed(
            "sentence-1", "connection_closed_before_done"
        )
        result = await wait_task
        conn.mark_xiaoxin_control_tts_done("sentence-1")
        return result, done_callbacks, failed_callbacks

    result, done_callbacks, failed_callbacks = asyncio.run(scenario())
    assert result.state == "done"
    assert done_callbacks == [("delivery-1", "sentence-1")]
    assert failed_callbacks == []


@pytest.mark.parametrize("state", ["ready", "done"])
def test_failed_attempt_terminal_rejects_late_ack_after_wait_timeout(state):
    async def scenario():
        conn = make_conn()
        if state == "done":
            conn.begin_tts_ack_wait("ready", "sentence-1")
            conn.resolve_tts_ack("ready", "sentence-1")
            await conn.wait_for_tts_ack("ready", "sentence-1", 10)
            conn.mark_tts_streaming("sentence-1")
        conn.begin_tts_ack_wait(state, "sentence-1")
        assert await conn.wait_for_tts_ack(state, "sentence-1", 1) is None
        conn.mark_xiaoxin_control_tts_failed("sentence-1", f"{state}_timeout")
        late_resolved = conn.resolve_tts_ack(state, "sentence-1")
        terminal = await conn.begin_tts_ack_wait(state, "sentence-1")
        return late_resolved, terminal

    late_resolved, terminal = asyncio.run(scenario())
    assert late_resolved is False
    assert terminal.state == "error"
    assert terminal.reason == f"{state}_timeout"


class FakeLogger:
    def __init__(self):
        self.messages = []

    def bind(self, **kwargs):
        return SimpleNamespace(
            debug=lambda message, *args, **kwargs: self.messages.append(
                ("debug", message)
            ),
            info=lambda message, *args, **kwargs: self.messages.append(
                ("info", message)
            ),
            warning=lambda message, *args, **kwargs: self.messages.append(
                ("warning", message)
            ),
            error=lambda message, *args, **kwargs: self.messages.append(
                ("error", message)
            ),
        )


def test_tts_message_type_is_registered():
    registry = TextMessageHandlerRegistry()

    assert registry.get_handler("tts").message_type == TextMessageType.TTS


def test_tts_handler_resolves_ready_ack():
    async def scenario():
        conn = make_conn()
        conn.logger = FakeLogger()
        waiter = conn.begin_tts_ack_wait("ready", "sentence-1")
        handler = TtsTextMessageHandler()

        await handler.handle(
            conn,
            {
                "type": "tts",
                "state": "ready",
                "session_id": conn.session_id,
                "sentence_id": "sentence-1",
            },
        )

        return waiter.done()

    assert asyncio.run(scenario()) is True


def test_tts_handler_ignores_missing_sentence_id():
    async def scenario():
        conn = make_conn()
        logger = FakeLogger()
        conn.logger = logger
        handler = TtsTextMessageHandler()

        await handler.handle(
            conn,
            {
                "type": "tts",
                "state": "ready",
                "session_id": conn.session_id,
            },
        )

        return logger.messages

    messages = asyncio.run(scenario())
    assert any(level == "warning" for level, _ in messages)


def test_tts_handler_ignores_non_string_sentence_id():
    async def scenario():
        conn = make_conn()
        logger = FakeLogger()
        conn.logger = logger
        handler = TtsTextMessageHandler()

        await handler.handle(
            conn,
            {
                "type": "tts",
                "state": "done",
                "session_id": conn.session_id,
                "sentence_id": ["sentence-1"],
            },
        )
        await handler.handle(
            conn,
            {
                "type": "tts",
                "state": "done",
                "session_id": conn.session_id,
                "sentence_id": {"id": "sentence-1"},
            },
        )

        return logger.messages, conn.tts_ack_waiters

    messages, waiters = asyncio.run(scenario())

    assert waiters == {}
    assert sum(1 for level, _ in messages if level == "warning") == 2


def test_tts_handler_unrelated_error_does_not_mark_failure():
    async def scenario():
        conn = make_conn()
        conn.logger = FakeLogger()
        conn.begin_tts_ack_wait("ready", "sentence-current")
        failure_calls = []
        conn.mark_xiaoxin_control_tts_failed = (
            lambda sentence_id, reason: failure_calls.append((sentence_id, reason))
        )

        await TtsTextMessageHandler().handle(
            conn,
            {
                "type": "tts",
                "state": "error",
                "session_id": conn.session_id,
                "sentence_id": "sentence-unrelated",
                "reason": "device_error",
            },
        )
        return failure_calls

    assert asyncio.run(scenario()) == []


def test_tts_handler_early_error_without_waiter_does_not_mark_failure():
    async def scenario():
        conn = make_conn()
        conn.logger = FakeLogger()
        failure_calls = []
        conn.mark_xiaoxin_control_tts_failed = (
            lambda sentence_id, reason: failure_calls.append((sentence_id, reason))
        )

        await TtsTextMessageHandler().handle(
            conn,
            {
                "type": "tts",
                "state": "error",
                "session_id": conn.session_id,
                "sentence_id": "sentence-early",
                "reason": "device_error",
            },
        )
        return failure_calls

    assert asyncio.run(scenario()) == []


def test_tts_handler_matched_error_marks_failure_once():
    async def scenario():
        conn = make_conn()
        conn.logger = FakeLogger()
        conn.begin_tts_ack_wait("ready", "sentence-1")
        failure_calls = []
        conn.mark_xiaoxin_control_tts_failed = (
            lambda sentence_id, reason: failure_calls.append((sentence_id, reason))
        )

        await TtsTextMessageHandler().handle(
            conn,
            {
                "type": "tts",
                "state": "error",
                "session_id": conn.session_id,
                "sentence_id": "sentence-1",
                "reason": "preroll_overflow",
            },
        )
        result = await conn.wait_for_tts_ack("ready", "sentence-1", 10)
        return failure_calls, result

    failure_calls, result = asyncio.run(scenario())

    assert failure_calls == [("sentence-1", "preroll_overflow")]
    assert result.reason == "preroll_overflow"


def test_tts_handler_duplicate_error_does_not_mark_failure_again():
    async def scenario():
        conn = make_conn()
        conn.logger = FakeLogger()
        conn.begin_tts_ack_wait("ready", "sentence-1")
        failure_calls = []
        conn.mark_xiaoxin_control_tts_failed = (
            lambda sentence_id, reason: failure_calls.append((sentence_id, reason))
        )
        message = {
            "type": "tts",
            "state": "error",
            "session_id": conn.session_id,
            "sentence_id": "sentence-1",
            "reason": "preroll_overflow",
        }

        await TtsTextMessageHandler().handle(conn, message)
        await TtsTextMessageHandler().handle(conn, message)
        return failure_calls

    assert asyncio.run(scenario()) == [("sentence-1", "preroll_overflow")]
