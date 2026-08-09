from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.xiaoxin.identity.ids import new_id


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass(frozen=True)
class ActivationSession:
    id: str
    device_id: str
    code: str
    challenge: str
    message: str
    expires_at: str
    consumed_at: str | None
    created_at: str
    last_seen_at: str


class XiaoxinActivationStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS device_activation_codes (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    code TEXT UNIQUE NOT NULL,
                    challenge TEXT NOT NULL,
                    message TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_device_activation_codes_device_live
                ON device_activation_codes(device_id, consumed_at, expires_at, created_at)
                """
            )

    def create_or_refresh_activation(
        self, device_id: str, ttl_seconds: int = 600
    ) -> ActivationSession:
        clean_device_id = device_id.strip()
        if not clean_device_id:
            raise ValueError("device_id required")

        now = utc_now_iso()
        expires_after = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        ).isoformat()

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT *
                FROM device_activation_codes
                WHERE device_id = ?
                  AND consumed_at IS NULL
                  AND expires_at > ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (clean_device_id, now),
            ).fetchone()
            if row is not None:
                conn.execute(
                    """
                    UPDATE device_activation_codes
                    SET last_seen_at = ?
                    WHERE id = ?
                    """,
                    (now, row["id"]),
                )
                refreshed = conn.execute(
                    "SELECT * FROM device_activation_codes WHERE id = ?",
                    (row["id"],),
                ).fetchone()
                return self._from_row(refreshed)

            conn.execute(
                """
                UPDATE device_activation_codes
                SET consumed_at = ?
                WHERE device_id = ?
                  AND consumed_at IS NULL
                """,
                (now, clean_device_id),
            )

            session_id = new_id("act")
            code = self._new_unique_code(conn)
            challenge = secrets.token_urlsafe(24)
            message = f"请在控制台输入绑定码 {code}"
            conn.execute(
                """
                INSERT INTO device_activation_codes (
                    id,
                    device_id,
                    code,
                    challenge,
                    message,
                    expires_at,
                    consumed_at,
                    created_at,
                    last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    clean_device_id,
                    code,
                    challenge,
                    message,
                    expires_after,
                    None,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM device_activation_codes WHERE id = ?",
                (session_id,),
            ).fetchone()
            return self._from_row(row)

    def get_activation_by_code(self, code: str) -> ActivationSession | None:
        clean_code = code.strip()
        if not clean_code:
            return None

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM device_activation_codes WHERE code = ?",
                (clean_code,),
            ).fetchone()
        return self._from_row(row) if row else None

    def get_activation_by_device_id(
        self, device_id: str
    ) -> ActivationSession | None:
        return self._get_latest_activation_by_device_id(
            device_id, include_expired=False
        )

    def get_latest_activation_by_device_id(
        self, device_id: str
    ) -> ActivationSession | None:
        return self._get_latest_activation_by_device_id(device_id, include_expired=True)

    def _get_latest_activation_by_device_id(
        self, device_id: str, include_expired: bool
    ) -> ActivationSession | None:
        clean_device_id = device_id.strip()
        if not clean_device_id:
            return None

        where_clauses = ["device_id = ?", "consumed_at IS NULL"]
        params: list[str] = [clean_device_id]
        if not include_expired:
            where_clauses.append("expires_at > ?")
            params.append(utc_now_iso())

        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT *
                FROM device_activation_codes
                WHERE {" AND ".join(where_clauses)}
                ORDER BY created_at DESC
                LIMIT 1
                """,
                params,
            ).fetchone()
        return self._from_row(row) if row else None

    def mark_activation_consumed(self, code: str) -> None:
        clean_code = code.strip()
        if not clean_code:
            return

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE device_activation_codes
                SET consumed_at = ?
                WHERE code = ?
                  AND consumed_at IS NULL
                """,
                (utc_now_iso(), clean_code),
            )

    def is_expired(self, session: ActivationSession) -> bool:
        return _parse_iso(session.expires_at) <= datetime.now(timezone.utc)

    def delete_expired_activations(self) -> int:
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM device_activation_codes
                WHERE expires_at <= ?
                """,
                (now,),
            )
            return cursor.rowcount

    def _new_unique_code(self, conn: sqlite3.Connection) -> str:
        for _ in range(20):
            code = f"{secrets.randbelow(1_000_000):06d}"
            row = conn.execute(
                "SELECT 1 FROM device_activation_codes WHERE code = ?",
                (code,),
            ).fetchone()
            if row is None:
                return code
        raise RuntimeError("failed to generate unique activation code")

    def _from_row(self, row: sqlite3.Row) -> ActivationSession:
        return ActivationSession(
            id=row["id"],
            device_id=row["device_id"],
            code=row["code"],
            challenge=row["challenge"],
            message=row["message"],
            expires_at=row["expires_at"],
            consumed_at=row["consumed_at"],
            created_at=row["created_at"],
            last_seen_at=row["last_seen_at"],
        )
