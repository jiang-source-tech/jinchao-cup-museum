from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from .models import IdentityUser
from .store import XiaoxinIdentityStore


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt_value = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_value,
        200_000,
    )
    return (
        "pbkdf2_sha256$200000$"
        + base64.b64encode(salt_value).decode("ascii")
        + "$"
        + base64.b64encode(digest).decode("ascii")
    )


def _verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(digest_b64.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(iterations),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_username(username: str) -> str:
    normalized = username.strip()
    if not normalized:
        raise ValueError("username must not be empty")
    return normalized


def _require_password(password: str) -> str:
    if password == "":
        raise ValueError("password must not be empty")
    return password


class XiaoxinAuthService:
    def __init__(self, store: XiaoxinIdentityStore, session_days: int = 14):
        self.store = store
        self.session_days = session_days

    def register(
        self,
        username: str,
        password: str,
        display_name: str,
        *,
        role: str = "user",
        require_no_admin: bool = False,
    ) -> tuple[IdentityUser, str]:
        normalized_username = _normalize_username(username)
        password_value = _require_password(password)
        user = self.store.create_user(
            normalized_username,
            _hash_password(password_value),
            display_name.strip() or normalized_username,
            role=role,
            require_no_admin=require_no_admin,
        )
        token = secrets.token_urlsafe(32)
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=self.session_days)
        ).isoformat()
        self.store.create_session(user.id, _token_hash(token), expires_at)
        return user, token

    def create_session_for_user(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=self.session_days)
        ).isoformat()
        self.store.create_session(user_id, _token_hash(token), expires_at)
        self.store.mark_user_login(user_id)
        return token

    def login(self, username: str, password: str) -> tuple[IdentityUser, str] | None:
        normalized_username = username.strip()
        if not normalized_username or password == "":
            return None
        user = self.store.get_user_by_username(normalized_username)
        if user is None or not _verify_password(password, user.password_hash):
            return None
        token = secrets.token_urlsafe(32)
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=self.session_days)
        ).isoformat()
        self.store.create_session(user.id, _token_hash(token), expires_at)
        self.store.mark_user_login(user.id)
        return user, token

    def user_for_token(self, token: str) -> IdentityUser | None:
        session = self.store.get_session_by_token_hash(_token_hash(token or ""))
        if session is None:
            return None
        expires_at = datetime.fromisoformat(session.expires_at)
        if expires_at < datetime.now(timezone.utc):
            self.store.delete_session(session.token_hash)
            return None
        self.store.touch_session(session.token_hash)
        return self.store.get_user_by_id(session.user_id)

    def logout(self, token: str) -> None:
        if token:
            self.store.delete_session(_token_hash(token))
