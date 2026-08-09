import sqlite3
import threading
import time

from core.xiaoxin.activation_store import XiaoxinActivationStore


def test_create_or_refresh_activation_reuses_live_code_for_same_device(tmp_path):
    store = XiaoxinActivationStore(tmp_path / "activation.db")

    first = store.create_or_refresh_activation("device-1", ttl_seconds=600)
    second = store.create_or_refresh_activation("device-1", ttl_seconds=600)

    assert first.device_id == "device-1"
    assert first.code == second.code
    assert len(first.code) == 6
    assert first.code.isdigit()
    assert first.challenge == second.challenge
    assert first.consumed_at is None


def test_refresh_updates_last_seen_on_returned_session(tmp_path):
    store = XiaoxinActivationStore(tmp_path / "activation.db")

    first = store.create_or_refresh_activation("device-1", ttl_seconds=600)
    time.sleep(0.02)
    refreshed = store.create_or_refresh_activation("device-1", ttl_seconds=600)

    assert refreshed.code == first.code
    assert refreshed.challenge == first.challenge
    assert refreshed.last_seen_at != first.last_seen_at


def test_consumed_activation_is_not_reused(tmp_path):
    store = XiaoxinActivationStore(tmp_path / "activation.db")

    first = store.create_or_refresh_activation("device-1", ttl_seconds=600)
    store.mark_activation_consumed(first.code)
    second = store.create_or_refresh_activation("device-1", ttl_seconds=600)

    assert second.code != first.code
    assert store.get_activation_by_code(first.code).consumed_at is not None
    assert store.get_activation_by_code(second.code).consumed_at is None


def test_expired_activation_is_deleted(tmp_path):
    store = XiaoxinActivationStore(tmp_path / "activation.db")
    session = store.create_or_refresh_activation("device-1", ttl_seconds=-1)

    assert store.is_expired(session) is True
    assert store.delete_expired_activations() == 1
    assert store.get_activation_by_code(session.code) is None


def test_lookup_by_device_id_returns_latest_live_session(tmp_path):
    store = XiaoxinActivationStore(tmp_path / "activation.db")

    session = store.create_or_refresh_activation("device-1", ttl_seconds=600)

    assert store.get_activation_by_device_id("device-1").code == session.code
    assert store.get_activation_by_device_id("missing") is None


def test_lookup_latest_by_device_id_includes_expired_unconsumed_session(tmp_path):
    store = XiaoxinActivationStore(tmp_path / "activation.db")

    session = store.create_or_refresh_activation("device-1", ttl_seconds=-1)

    latest = store.get_latest_activation_by_device_id("device-1")

    assert latest is not None
    assert latest.code == session.code
    assert latest.challenge == session.challenge
    assert store.get_activation_by_device_id("device-1") is None


def test_concurrent_create_or_refresh_keeps_single_live_session(tmp_path, monkeypatch):
    store_path = tmp_path / "activation.db"
    original_new_unique_code = XiaoxinActivationStore._new_unique_code

    def slow_unique_code(self, conn):
        time.sleep(0.1)
        return original_new_unique_code(self, conn)

    monkeypatch.setattr(XiaoxinActivationStore, "_new_unique_code", slow_unique_code)

    start = threading.Barrier(2)
    results: list[object] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            start.wait()
            local_store = XiaoxinActivationStore(store_path)
            results.append(local_store.create_or_refresh_activation("device-1", ttl_seconds=600))
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert len(results) == 2
    assert {session.code for session in results} == {results[0].code}

    with sqlite3.connect(store_path) as conn:
        live_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM device_activation_codes
            WHERE device_id = ?
              AND consumed_at IS NULL
            """,
            ("device-1",),
        ).fetchone()[0]

    assert live_count == 1
