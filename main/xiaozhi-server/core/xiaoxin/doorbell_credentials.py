from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.xiaoxin.identity.ids import new_id
from core.xiaoxin.tenant_config import validate_mqtt_topic_segment


ACTIVE = "active"
DISABLED = "disabled"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class DoorbellCredential:
    id: str
    tenant_id: str
    device_id: str
    client_id: str
    username: str
    password: str
    status: str
    generation: int
    created_at: str
    updated_at: str
    rotated_at: str | None = None


class DoorbellCredentialStore:
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
                CREATE TABLE IF NOT EXISTS doorbell_credentials (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    password TEXT NOT NULL,
                    status TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    rotated_at TEXT,
                    UNIQUE(tenant_id, device_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_doorbell_credentials_active
                ON doorbell_credentials(status, tenant_id, device_id)
                """
            )

    def get(self, tenant_id: str, device_id: str) -> DoorbellCredential | None:
        clean_tenant_id = validate_mqtt_topic_segment(tenant_id.strip(), "tenant_id")
        clean_device_id = validate_mqtt_topic_segment(device_id.strip(), "device_id")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM doorbell_credentials
                WHERE tenant_id = ? AND device_id = ?
                """,
                (clean_tenant_id, clean_device_id),
            ).fetchone()
        return self._from_row(row) if row else None

    def get_or_create(self, tenant_id: str, device_id: str) -> DoorbellCredential:
        clean_tenant_id = validate_mqtt_topic_segment(tenant_id.strip(), "tenant_id")
        clean_device_id = validate_mqtt_topic_segment(device_id.strip(), "device_id")

        now = utc_now_iso()
        identity = f"{clean_tenant_id}:{clean_device_id}"

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT *
                FROM doorbell_credentials
                WHERE tenant_id = ? AND device_id = ?
                """,
                (clean_tenant_id, clean_device_id),
            ).fetchone()
            if row is None:
                credential = DoorbellCredential(
                    id=new_id("mqtt"),
                    tenant_id=clean_tenant_id,
                    device_id=clean_device_id,
                    client_id=identity,
                    username=identity,
                    password=secrets.token_urlsafe(48),
                    status=ACTIVE,
                    generation=1,
                    created_at=now,
                    updated_at=now,
                    rotated_at=None,
                )
                conn.execute(
                    """
                    INSERT INTO doorbell_credentials (
                        id,
                        tenant_id,
                        device_id,
                        client_id,
                        username,
                        password,
                        status,
                        generation,
                        created_at,
                        updated_at,
                        rotated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        credential.id,
                        credential.tenant_id,
                        credential.device_id,
                        credential.client_id,
                        credential.username,
                        credential.password,
                        credential.status,
                        credential.generation,
                        credential.created_at,
                        credential.updated_at,
                        credential.rotated_at,
                    ),
                )
                return credential
        existing = self.get(clean_tenant_id, clean_device_id)
        assert existing is not None
        return existing

    def rotate(self, tenant_id: str, device_id: str) -> DoorbellCredential:
        current = self.get_or_create(tenant_id, device_id)
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE doorbell_credentials
                SET password = ?,
                    status = ?,
                    generation = ?,
                    updated_at = ?,
                    rotated_at = ?
                WHERE tenant_id = ? AND device_id = ?
                """,
                (
                    secrets.token_urlsafe(48),
                    ACTIVE,
                    current.generation + 1,
                    now,
                    now,
                    current.tenant_id,
                    current.device_id,
                ),
            )
        credential = self.get(current.tenant_id, current.device_id)
        assert credential is not None
        return credential

    def disable(self, tenant_id: str, device_id: str) -> None:
        current = self.get(tenant_id, device_id)
        if current is None:
            return
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE doorbell_credentials
                SET status = ?, updated_at = ?
                WHERE tenant_id = ? AND device_id = ?
                """,
                (DISABLED, utc_now_iso(), current.tenant_id, current.device_id),
            )

    def list_active(self) -> list[DoorbellCredential]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM doorbell_credentials
                WHERE status = ?
                ORDER BY tenant_id ASC, device_id ASC
                """,
                (ACTIVE,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def verify_password(self, username: str, device_id: str, password: str) -> bool:
        clean_username = str(username or "").strip()
        try:
            clean_device_id = validate_mqtt_topic_segment(
                str(device_id or "").strip(),
                "device_id",
            )
        except ValueError:
            return False
        if not clean_username or not isinstance(password, str):
            return False

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT password
                FROM doorbell_credentials
                WHERE username = ? AND device_id = ? AND status = ?
                """,
                (clean_username, clean_device_id, ACTIVE),
            ).fetchone()
        if row is None:
            return False
        return secrets.compare_digest(str(row["password"]), password)

    def _from_row(self, row: sqlite3.Row) -> DoorbellCredential:
        return DoorbellCredential(
            id=row["id"],
            tenant_id=row["tenant_id"],
            device_id=row["device_id"],
            client_id=row["client_id"],
            username=row["username"],
            password=row["password"],
            status=row["status"],
            generation=int(row["generation"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            rotated_at=row["rotated_at"],
        )
