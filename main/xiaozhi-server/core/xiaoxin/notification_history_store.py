from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


class XiaoxinNotificationHistoryStore:
    def __init__(self, db_path: str | Path, limit: int = 500):
        self.db_path = Path(db_path)
        self.limit = limit
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
                CREATE TABLE IF NOT EXISTS notification_history (
                    delivery_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_notification_history_device_updated
                ON notification_history(device_id, updated_at DESC)
                """
            )

    def save_delivery_record(self, record: Any) -> None:
        payload = record.to_dict()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO notification_history (
                    delivery_id,
                    device_id,
                    created_at,
                    updated_at,
                    record_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(delivery_id) DO UPDATE SET
                    device_id = excluded.device_id,
                    updated_at = excluded.updated_at,
                    record_json = excluded.record_json
                """,
                (
                    record.delivery_id,
                    record.device_id,
                    record.created_at,
                    record.updated_at,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            self._trim(conn)

    def list_for_device_ids(self, device_ids: Iterable[str]) -> list[dict[str, Any]]:
        ids = sorted({str(device_id) for device_id in device_ids if str(device_id)})
        if not ids:
            return []
        placeholders = ", ".join("?" for _ in ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT record_json
                FROM notification_history
                WHERE device_id IN ({placeholders})
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                (*ids, self.limit),
            ).fetchall()
        return [json.loads(row["record_json"]) for row in rows]

    def get_delivery_states(self, delivery_ids: Iterable[str]) -> dict[str, str]:
        ids = sorted({str(delivery_id) for delivery_id in delivery_ids if str(delivery_id)})
        if not ids:
            return {}
        placeholders = ", ".join("?" for _ in ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT delivery_id, record_json
                FROM notification_history
                WHERE delivery_id IN ({placeholders})
                """,
                ids,
            ).fetchall()
        return {
            str(row["delivery_id"]): str(json.loads(row["record_json"]).get("state") or "")
            for row in rows
        }

    def _trim(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            DELETE FROM notification_history
            WHERE delivery_id NOT IN (
                SELECT delivery_id
                FROM notification_history
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
            )
            """,
            (self.limit,),
        )
