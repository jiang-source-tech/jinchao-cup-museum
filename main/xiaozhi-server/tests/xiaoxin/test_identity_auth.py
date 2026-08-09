from datetime import datetime, timedelta, timezone

import pytest

from core.xiaoxin.identity.auth import XiaoxinAuthService, _token_hash
from core.xiaoxin.identity.store import XiaoxinIdentityStore


def _session_count(store: XiaoxinIdentityStore) -> int:
    with store._connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM sessions").fetchone()
    return int(row["count"])


def test_register_returns_user_and_session_token(tmp_path):
    auth = XiaoxinAuthService(XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db"))

    user, token = auth.register("liu", "secret-pass", "liu")

    assert user.id.startswith("usr_")
    assert token
    assert auth.user_for_token(token).id == user.id


def test_login_accepts_correct_password_and_rejects_wrong_password(tmp_path):
    auth = XiaoxinAuthService(XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db"))
    user, _ = auth.register("liu", "secret-pass", "liu")

    success = auth.login("liu", "secret-pass")
    failure = auth.login("liu", "wrong-pass")

    assert success is not None
    assert success[0].id == user.id
    assert success[1]
    assert failure is None


def test_logout_invalidates_token(tmp_path):
    auth = XiaoxinAuthService(XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db"))
    _, token = auth.register("liu", "secret-pass", "liu")

    auth.logout(token)

    assert auth.user_for_token(token) is None


@pytest.mark.parametrize("username", ["", "   "])
def test_register_rejects_empty_or_whitespace_username(tmp_path, username):
    auth = XiaoxinAuthService(XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db"))

    with pytest.raises(ValueError):
        auth.register(username, "secret-pass", "liu")


def test_register_rejects_empty_password(tmp_path):
    auth = XiaoxinAuthService(XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db"))

    with pytest.raises(ValueError):
        auth.register("liu", "", "liu")


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("", "secret-pass"),
        ("   ", "secret-pass"),
        ("liu", ""),
    ],
)
def test_login_rejects_empty_credentials_without_creating_session(
    tmp_path, username, password
):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    auth = XiaoxinAuthService(store)
    auth.register("liu", "secret-pass", "liu")
    baseline_sessions = _session_count(store)

    result = auth.login(username, password)

    assert result is None
    assert _session_count(store) == baseline_sessions


def test_user_for_token_deletes_expired_session(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    auth = XiaoxinAuthService(store)
    user = store.create_user("liu", "hash-value", "liu")
    token = "expired-token"
    store.create_session(
        user.id,
        _token_hash(token),
        (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    )

    assert auth.user_for_token(token) is None
    assert store.get_session_by_token_hash(_token_hash(token)) is None


def test_user_for_token_touches_valid_session(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    auth = XiaoxinAuthService(store)
    _, token = auth.register("liu", "secret-pass", "liu")
    token_hash = _token_hash(token)

    stale_last_seen = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with store._connect() as conn:
        conn.execute(
            "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
            (stale_last_seen, token_hash),
        )

    loaded_user = auth.user_for_token(token)
    touched = store.get_session_by_token_hash(token_hash)

    assert loaded_user is not None
    assert touched is not None
    assert touched.last_seen_at != stale_last_seen


def test_login_marks_user_last_login(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    auth = XiaoxinAuthService(store)
    user, _ = auth.register("liu", "secret-pass", "liu")
    before = store.get_user_by_id(user.id)
    assert before is not None
    assert before.last_login_at is None

    result = auth.login("liu", "secret-pass")
    after = store.get_user_by_id(user.id)

    assert result is not None
    assert after is not None
    assert after.last_login_at is not None
