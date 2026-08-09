from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from core.xiaoxin.overview.models import (
    DailyWeather,
    IpCityLocation,
    OverviewSnapshot,
)

_WIRE_FIELDS = {"type", "version", "device_id", "revision", "generated_at"}


def _normalize_utc_iso(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc).isoformat()


def overview_content_hash(content: dict[str, object]) -> str:
    encoded = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _weather_cache_key(
    country_code: str,
    province: str,
    city: str,
    date_text: str,
    provider: str,
) -> str:
    return json.dumps(
        [country_code, province, city, date_text, provider],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _weather_expires_at(date_text: str, timezone_id: str) -> str:
    next_date = date.fromisoformat(date_text) + timedelta(days=1)
    local_expiry = datetime.combine(next_date, time.min, tzinfo=ZoneInfo(timezone_id))
    return local_expiry.astimezone(timezone.utc).isoformat()


class XiaoxinOverviewStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.db_path = Path(db_path)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _now_iso(self) -> str:
        value = self._clock()
        if value.utcoffset() is None:
            raise ValueError("clock must return a datetime with a UTC offset")
        return value.astimezone(timezone.utc).isoformat()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS device_weather_locations (
                    device_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL CHECK (mode IN ('automatic', 'manual')),
                    public_ip_hmac TEXT,
                    province TEXT NOT NULL,
                    city TEXT NOT NULL,
                    country_code TEXT NOT NULL DEFAULT 'CN',
                    latitude REAL,
                    longitude REAL,
                    located_at TEXT NOT NULL,
                    automatic_public_ip_hmac TEXT,
                    automatic_province TEXT,
                    automatic_city TEXT,
                    automatic_country_code TEXT,
                    automatic_located_at TEXT,
                    location_revision INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                )
                """
            )
            location_columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(device_weather_locations)"
                )
            }
            location_migrations = {
                "country_code": "TEXT NOT NULL DEFAULT 'CN'",
                "automatic_public_ip_hmac": "TEXT",
                "automatic_province": "TEXT",
                "automatic_city": "TEXT",
                "automatic_country_code": "TEXT",
                "automatic_located_at": "TEXT",
            }
            for column, declaration in location_migrations.items():
                if column not in location_columns:
                    conn.execute(
                        f"ALTER TABLE device_weather_locations ADD COLUMN {column} {declaration}"
                    )
            if "location_revision" not in location_columns:
                conn.execute(
                    """
                    ALTER TABLE device_weather_locations
                    ADD COLUMN location_revision INTEGER NOT NULL DEFAULT 1
                    """
                )
            conn.execute(
                """
                UPDATE device_weather_locations
                SET automatic_public_ip_hmac = COALESCE(automatic_public_ip_hmac, public_ip_hmac),
                    automatic_province = COALESCE(automatic_province, province),
                    automatic_city = COALESCE(automatic_city, city),
                    automatic_country_code = COALESCE(automatic_country_code, country_code),
                    automatic_located_at = COALESCE(automatic_located_at, located_at)
                WHERE mode = 'automatic'
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_city_weather (
                    cache_key TEXT PRIMARY KEY,
                    country_code TEXT NOT NULL,
                    province TEXT NOT NULL,
                    city TEXT NOT NULL,
                    date TEXT NOT NULL,
                    weather_code INTEGER,
                    weather_text TEXT,
                    temperature_min_c REAL,
                    temperature_max_c REAL,
                    fetched_at TEXT,
                    timezone_id TEXT NOT NULL DEFAULT 'UTC',
                    expires_at TEXT,
                    fetch_attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT,
                    last_error TEXT,
                    quarantined INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            weather_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(daily_city_weather)")
            }
            if "timezone_id" not in weather_columns:
                conn.execute(
                    "ALTER TABLE daily_city_weather ADD COLUMN timezone_id TEXT NOT NULL DEFAULT 'UTC'"
                )
            if "quarantined" not in weather_columns:
                conn.execute(
                    "ALTER TABLE daily_city_weather ADD COLUMN quarantined INTEGER NOT NULL DEFAULT 0"
                )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_daily_city_weather_retry
                ON daily_city_weather(next_attempt_at)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS device_overview_snapshots (
                    device_id TEXT PRIMARY KEY,
                    owner_user_id TEXT,
                    revision INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    publish_state TEXT NOT NULL
                        CHECK (publish_state IN ('pending', 'published')),
                    publish_attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT,
                    last_error TEXT,
                    generated_at TEXT NOT NULL,
                    published_at TEXT,
                    quarantined INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            snapshot_columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(device_overview_snapshots)")
            }
            if "quarantined" not in snapshot_columns:
                conn.execute(
                    "ALTER TABLE device_overview_snapshots ADD COLUMN quarantined INTEGER NOT NULL DEFAULT 0"
                )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_device_overview_pending
                ON device_overview_snapshots(publish_state, next_attempt_at)
                """
            )

    def get_location(self, device_id: str) -> dict[str, object] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM device_weather_locations WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def restore_location(
        self,
        device_id: str,
        location: dict[str, object] | None,
        *,
        expected_revision: int,
    ) -> bool:
        with self._connect() as conn:
            if location is None:
                cursor = conn.execute(
                    """
                    DELETE FROM device_weather_locations
                    WHERE device_id = ? AND location_revision = ?
                    """,
                    (device_id, expected_revision),
                )
                return cursor.rowcount == 1
            cursor = conn.execute(
                """
                UPDATE device_weather_locations
                SET mode = ?,
                    public_ip_hmac = ?,
                    province = ?,
                    city = ?,
                    country_code = ?,
                    latitude = ?,
                    longitude = ?,
                    located_at = ?,
                    automatic_public_ip_hmac = ?,
                    automatic_province = ?,
                    automatic_city = ?,
                    automatic_country_code = ?,
                    automatic_located_at = ?,
                    location_revision = location_revision + 1,
                    updated_at = ?
                WHERE device_id = ? AND location_revision = ?
                """,
                (
                    location["mode"],
                    location.get("public_ip_hmac"),
                    location["province"],
                    location["city"],
                    location["country_code"],
                    location.get("latitude"),
                    location.get("longitude"),
                    location["located_at"],
                    location.get("automatic_public_ip_hmac"),
                    location.get("automatic_province"),
                    location.get("automatic_city"),
                    location.get("automatic_country_code"),
                    location.get("automatic_located_at"),
                    location["updated_at"],
                    device_id,
                    expected_revision,
                ),
            )
        return cursor.rowcount == 1

    def set_automatic_location(
        self,
        device_id: str,
        public_ip_hmac: str,
        location: IpCityLocation,
    ) -> dict[str, object]:
        now = self._now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT mode FROM device_weather_locations WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            if row is not None and row["mode"] == "manual":
                conn.execute(
                    """
                    UPDATE device_weather_locations
                    SET automatic_public_ip_hmac = ?,
                        automatic_province = ?,
                        automatic_city = ?,
                        automatic_country_code = ?,
                        automatic_located_at = ?,
                        location_revision = location_revision + 1,
                        updated_at = ?
                    WHERE device_id = ?
                    """,
                    (
                        public_ip_hmac,
                        location.province,
                        location.city,
                        location.country_code,
                        location.located_at,
                        now,
                        device_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO device_weather_locations (
                        device_id, mode, public_ip_hmac, province, city,
                        country_code, latitude, longitude, located_at,
                        automatic_public_ip_hmac, automatic_province,
                        automatic_city, automatic_country_code,
                        automatic_located_at, location_revision, updated_at
                    ) VALUES (?, 'automatic', ?, ?, ?, ?, NULL, NULL, ?,
                              ?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(device_id) DO UPDATE SET
                        mode = 'automatic',
                        public_ip_hmac = excluded.public_ip_hmac,
                        province = excluded.province,
                        city = excluded.city,
                        country_code = excluded.country_code,
                        latitude = excluded.latitude,
                        longitude = excluded.longitude,
                        located_at = excluded.located_at,
                        automatic_public_ip_hmac = excluded.automatic_public_ip_hmac,
                        automatic_province = excluded.automatic_province,
                        automatic_city = excluded.automatic_city,
                        automatic_country_code = excluded.automatic_country_code,
                        automatic_located_at = excluded.automatic_located_at,
                        location_revision =
                            device_weather_locations.location_revision + 1,
                        updated_at = excluded.updated_at
                    """,
                    (
                        device_id,
                        public_ip_hmac,
                        location.province,
                        location.city,
                        location.country_code,
                        location.located_at,
                        public_ip_hmac,
                        location.province,
                        location.city,
                        location.country_code,
                        location.located_at,
                        now,
                    ),
                )
        result = self.get_location(device_id)
        if result is None:
            raise RuntimeError("failed to persist automatic weather location")
        return result

    def set_manual_location(
        self,
        device_id: str,
        province: str,
        city: str,
        country_code: str = "CN",
    ) -> dict[str, object]:
        now = self._now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO device_weather_locations (
                    device_id, mode, public_ip_hmac, province, city,
                    country_code, latitude, longitude, located_at,
                    automatic_public_ip_hmac, automatic_province,
                    automatic_city, automatic_country_code,
                    automatic_located_at, location_revision, updated_at
                ) VALUES (?, 'manual', NULL, ?, ?, ?, NULL, NULL, ?,
                          NULL, NULL, NULL, NULL, NULL, 1, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    mode = 'manual',
                    public_ip_hmac = NULL,
                    province = excluded.province,
                    city = excluded.city,
                    country_code = excluded.country_code,
                    latitude = NULL,
                    longitude = NULL,
                    located_at = excluded.located_at,
                    location_revision =
                        device_weather_locations.location_revision + 1,
                    updated_at = excluded.updated_at
                """,
                (device_id, province, city, country_code, now, now),
            )
        result = self.get_location(device_id)
        if result is None:
            raise RuntimeError("failed to persist manual weather location")
        return result

    def set_location_mode(
        self, device_id: str, mode: str
    ) -> dict[str, object] | None:
        if mode not in {"automatic", "manual"}:
            raise ValueError("mode must be 'automatic' or 'manual'")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if mode == "automatic":
                cursor = conn.execute(
                    """
                    UPDATE device_weather_locations
                    SET mode = 'automatic',
                        public_ip_hmac = COALESCE(
                            automatic_public_ip_hmac, public_ip_hmac
                        ),
                        province = COALESCE(automatic_province, province),
                        city = COALESCE(automatic_city, city),
                        country_code = COALESCE(
                            automatic_country_code, country_code
                        ),
                        located_at = COALESCE(automatic_located_at, located_at),
                        location_revision = location_revision + 1,
                        updated_at = ?
                    WHERE device_id = ?
                    """,
                    (self._now_iso(), device_id),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE device_weather_locations
                    SET mode = 'manual',
                        location_revision = location_revision + 1,
                        updated_at = ?
                    WHERE device_id = ?
                    """,
                    (self._now_iso(), device_id),
                )
        return self.get_location(device_id) if cursor.rowcount else None

    def get_daily_weather(
        self,
        province: str,
        city: str,
        date_text: str,
        provider: str,
        country_code: str = "CN",
    ) -> DailyWeather | None:
        cache_key = _weather_cache_key(
            country_code, province, city, date_text, provider
        )
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM daily_city_weather WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if row is None or row["weather_code"] is None:
            return None
        if row["expires_at"] is not None and row["expires_at"] <= self._now_iso():
            return None
        return DailyWeather(
            province=row["province"],
            city=row["city"],
            date=row["date"],
            weather_code=row["weather_code"],
            weather_text=row["weather_text"],
            temperature_min_c=row["temperature_min_c"],
            temperature_max_c=row["temperature_max_c"],
            fetched_at=row["fetched_at"],
            country_code=row["country_code"],
            timezone_id=row["timezone_id"],
        )

    def put_daily_weather(
        self, weather: DailyWeather, provider: str
    ) -> DailyWeather:
        cache_key = _weather_cache_key(
            weather.country_code,
            weather.province,
            weather.city,
            weather.date,
            provider,
        )
        _normalize_utc_iso(weather.fetched_at)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_city_weather (
                    cache_key, country_code, province, city, date,
                    weather_code, weather_text, temperature_min_c,
                    temperature_max_c, fetched_at, timezone_id, expires_at,
                    fetch_attempts, next_attempt_at, last_error, quarantined, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, 0, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    weather_code = excluded.weather_code,
                    weather_text = excluded.weather_text,
                    temperature_min_c = excluded.temperature_min_c,
                    temperature_max_c = excluded.temperature_max_c,
                    fetched_at = excluded.fetched_at,
                    timezone_id = excluded.timezone_id,
                    expires_at = excluded.expires_at,
                    fetch_attempts = 0,
                    next_attempt_at = NULL,
                    last_error = NULL,
                    quarantined = 0,
                    updated_at = excluded.updated_at
                """,
                (
                    cache_key,
                    weather.country_code,
                    weather.province,
                    weather.city,
                    weather.date,
                    weather.weather_code,
                    weather.weather_text,
                    weather.temperature_min_c,
                    weather.temperature_max_c,
                    weather.fetched_at,
                    weather.timezone_id,
                    _weather_expires_at(weather.date, weather.timezone_id),
                    self._now_iso(),
                ),
            )
        return weather

    def record_weather_failure(
        self,
        province: str,
        city: str,
        date_text: str,
        provider: str,
        error: str,
        attempts: int,
        next_attempt_at: str | None,
        country_code: str = "CN",
    ) -> None:
        cache_key = _weather_cache_key(
            country_code, province, city, date_text, provider
        )
        normalized_next_attempt = (
            _normalize_utc_iso(next_attempt_at)
            if next_attempt_at is not None
            else None
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_city_weather (
                    cache_key, country_code, province, city, date,
                    weather_code, weather_text, temperature_min_c,
                    temperature_max_c, fetched_at, timezone_id, expires_at,
                    fetch_attempts, next_attempt_at, last_error, quarantined, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL,
                          NULL, 'UTC', NULL, ?, ?, ?, 0, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    fetch_attempts = excluded.fetch_attempts,
                    next_attempt_at = excluded.next_attempt_at,
                    last_error = excluded.last_error,
                    quarantined = 0,
                    updated_at = excluded.updated_at
                """,
                (
                    cache_key,
                    country_code,
                    province,
                    city,
                    date_text,
                    attempts,
                    normalized_next_attempt,
                    error,
                    self._now_iso(),
                ),
            )

    def list_due_weather_retries(
        self, now_iso: str, limit: int = 50
    ) -> list[dict[str, object]]:
        normalized_now = _normalize_utc_iso(now_iso)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT cache_key, country_code, province, city, date,
                       fetch_attempts,
                       next_attempt_at, last_error
                FROM daily_city_weather
                WHERE next_attempt_at IS NOT NULL
                  AND next_attempt_at <= ?
                  AND quarantined = 0
                ORDER BY next_attempt_at, cache_key
                """,
                (normalized_now,),
            ).fetchall()
        results = []
        for row in rows:
            try:
                parts = json.loads(row["cache_key"])
                if (
                    not isinstance(parts, list)
                    or len(parts) != 5
                    or parts[:4] != [
                        row["country_code"], row["province"], row["city"], row["date"]
                    ]
                    or not isinstance(parts[4], str)
                ):
                    raise ValueError("invalid weather cache key")
            except (json.JSONDecodeError, TypeError, ValueError):
                with self._connect() as conn:
                    conn.execute(
                        """UPDATE daily_city_weather
                           SET quarantined = 1, next_attempt_at = NULL,
                               last_error = 'overview_retry_row_malformed', updated_at = ?
                           WHERE cache_key = ?""",
                        (self._now_iso(), row["cache_key"]),
                    )
                continue
            results.append({
                "province": row["province"], "city": row["city"],
                "date": row["date"], "country_code": row["country_code"],
                "provider": parts[4], "fetch_attempts": row["fetch_attempts"],
                "next_attempt_at": row["next_attempt_at"],
                "last_error": row["last_error"],
            })
            if len(results) >= limit:
                break
        return results

    def get_weather_retry_state(
        self,
        province: str,
        city: str,
        date_text: str,
        provider: str,
        country_code: str = "CN",
    ) -> dict[str, object] | None:
        cache_key = _weather_cache_key(
            country_code, province, city, date_text, provider
        )
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT country_code, province, city, date, fetch_attempts,
                       next_attempt_at, last_error
                FROM daily_city_weather
                WHERE cache_key = ? AND last_error IS NOT NULL
                """,
                (cache_key,),
            ).fetchone()
        if row is None:
            return None
        return {
            "country_code": row["country_code"],
            "province": row["province"],
            "city": row["city"],
            "date": row["date"],
            "provider": provider,
            "fetch_attempts": row["fetch_attempts"],
            "next_attempt_at": row["next_attempt_at"],
            "last_error": row["last_error"],
        }

    def upsert_snapshot(
        self,
        device_id: str,
        owner_user_id: str | None,
        content: dict[str, object],
        generated_at: str,
    ) -> tuple[OverviewSnapshot, bool]:
        business_content = {
            key: value for key, value in content.items() if key not in _WIRE_FIELDS
        }
        content_hash = overview_content_hash(business_content)
        now = self._now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM device_overview_snapshots WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            changed = (
                existing is None
                or existing["content_hash"] != content_hash
                or bool(existing["quarantined"])
            )
            if changed:
                revision = 1 if existing is None else existing["revision"] + 1
                payload = dict(business_content)
                payload.update(
                    {
                        "type": "xiaoxin_overview_update",
                        "version": 1,
                        "device_id": device_id,
                        "revision": revision,
                        "generated_at": generated_at,
                    }
                )
                payload_json = json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                conn.execute(
                    """
                    INSERT INTO device_overview_snapshots (
                        device_id, owner_user_id, revision, content_hash,
                        payload_json, publish_state, publish_attempts,
                        next_attempt_at, last_error, generated_at,
                        published_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'pending', 0, NULL, NULL, ?, NULL, ?)
                    ON CONFLICT(device_id) DO UPDATE SET
                        owner_user_id = excluded.owner_user_id,
                        revision = excluded.revision,
                        content_hash = excluded.content_hash,
                        payload_json = excluded.payload_json,
                        publish_state = 'pending',
                        publish_attempts = 0,
                        next_attempt_at = NULL,
                        last_error = NULL,
                        quarantined = 0,
                        generated_at = excluded.generated_at,
                        published_at = NULL,
                        updated_at = excluded.updated_at
                    """,
                    (
                        device_id,
                        owner_user_id,
                        revision,
                        content_hash,
                        payload_json,
                        generated_at,
                        now,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE device_overview_snapshots
                    SET owner_user_id = ?, updated_at = ?
                    WHERE device_id = ?
                    """,
                    (owner_user_id, now, device_id),
                )
            row = conn.execute(
                "SELECT * FROM device_overview_snapshots WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        return self._snapshot_from_row(row), changed

    def mark_publish_attempt(
        self,
        device_id: str,
        revision: int,
        next_attempt_at: str | None,
        error: str | None,
    ) -> None:
        normalized_next_attempt = (
            _normalize_utc_iso(next_attempt_at)
            if next_attempt_at is not None
            else None
        )
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE device_overview_snapshots
                SET publish_attempts = publish_attempts + 1,
                    next_attempt_at = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE device_id = ?
                  AND revision = ?
                  AND publish_state = 'pending'
                """,
                (
                    normalized_next_attempt,
                    error,
                    self._now_iso(),
                    device_id,
                    revision,
                ),
            )

    def get_snapshot(self, device_id: str) -> OverviewSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM device_overview_snapshots WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        return self._snapshot_from_row(row) if row is not None else None

    def get_snapshot_diagnostics(
        self, device_id: str
    ) -> dict[str, object] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT revision, publish_state, publish_attempts,
                       last_error, published_at, payload_json
                FROM device_overview_snapshots
                WHERE device_id = ?
                """,
                (device_id,),
            ).fetchone()
        if row is None:
            return None
        result = {
            key: row[key]
            for key in (
                "revision",
                "publish_state",
                "publish_attempts",
                "last_error",
                "published_at",
            )
        }
        weather_date = ""
        try:
            payload = json.loads(row["payload_json"])
            weather = payload.get("weather") if isinstance(payload, dict) else None
            candidate = weather.get("date") if isinstance(weather, dict) else None
            if isinstance(candidate, str):
                parsed = date.fromisoformat(candidate)
                if parsed.isoformat() == candidate:
                    weather_date = candidate
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        result["weather_date"] = weather_date
        return result

    def mark_publish_in_flight(
        self,
        device_id: str,
        revision: int,
        next_attempt_at: str,
    ) -> bool:
        normalized_next_attempt = _normalize_utc_iso(next_attempt_at)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE device_overview_snapshots
                SET next_attempt_at = ?,
                    last_error = NULL,
                    updated_at = ?
                WHERE device_id = ?
                  AND revision = ?
                  AND publish_state = 'pending'
                """,
                (
                    normalized_next_attempt,
                    self._now_iso(),
                    device_id,
                    revision,
                ),
            )
        return cursor.rowcount == 1

    def mark_published(
        self, device_id: str, revision: int, published_at: str
    ) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE device_overview_snapshots
                SET publish_state = 'published',
                    next_attempt_at = NULL,
                    last_error = NULL,
                    published_at = ?,
                    updated_at = ?
                WHERE device_id = ?
                  AND revision = ?
                  AND publish_state = 'pending'
                """,
                (published_at, self._now_iso(), device_id, revision),
            )
        return cursor.rowcount == 1

    def list_pending_snapshots(
        self, now_iso: str, limit: int = 100
    ) -> list[OverviewSnapshot]:
        normalized_now = _normalize_utc_iso(now_iso)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM device_overview_snapshots
                WHERE publish_state = 'pending'
                  AND quarantined = 0
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY updated_at, device_id
                """,
                (normalized_now,),
            ).fetchall()
        snapshots = []
        for row in rows:
            try:
                snapshot = self._snapshot_from_row(row)
                if not isinstance(snapshot.payload, dict):
                    raise ValueError("snapshot payload must be an object")
            except (json.JSONDecodeError, TypeError, ValueError):
                with self._connect() as conn:
                    conn.execute(
                        """UPDATE device_overview_snapshots
                           SET quarantined = 1, last_error = 'overview_payload_invalid',
                               next_attempt_at = NULL, updated_at = ?
                           WHERE device_id = ? AND revision = ?""",
                        (self._now_iso(), row["device_id"], row["revision"]),
                    )
                continue
            snapshots.append(snapshot)
            if len(snapshots) >= limit:
                break
        return snapshots

    @staticmethod
    def _snapshot_from_row(row: sqlite3.Row) -> OverviewSnapshot:
        return OverviewSnapshot(
            device_id=row["device_id"],
            owner_user_id=row["owner_user_id"],
            revision=row["revision"],
            content_hash=row["content_hash"],
            payload=json.loads(row["payload_json"]),
            publish_state=row["publish_state"],
            publish_attempts=row["publish_attempts"],
            next_attempt_at=row["next_attempt_at"],
        )
