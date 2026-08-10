from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4


_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_METADATA_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CHANNEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_VERSION_PATTERN = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
)
_RELEASE_STATES = frozenset({"draft", "published", "paused", "revoked"})


class FirmwareReleaseError(ValueError):
    """Raised when a release cannot be safely recorded or selected."""


@dataclass(frozen=True)
class FirmwareCheck:
    """Facts supplied by a device while it checks for a firmware release."""

    device_id: str
    model: str
    board_type: str
    partition_layout_id: str
    current_version: str
    channel: str = "stable"


@dataclass(frozen=True)
class FirmwareRelease:
    release_id: str
    sha256: str
    size_bytes: int
    model: str
    board_type: str
    partition_layout_id: str
    version: str
    channel: str
    rollout_percentage: int
    mandatory: bool
    min_current_version: str
    state: str
    created_at: str
    published_at: str | None


@dataclass(frozen=True)
class FirmwareOffer:
    """A selected release in the form the OTA adapter needs to return."""

    release_id: str
    sha256: str
    size_bytes: int
    model: str
    board_type: str
    partition_layout_id: str
    version: str
    url: str
    channel: str
    mandatory: bool
    min_current_version: str

    def to_firmware_payload(self) -> dict[str, object]:
        """Return only additive fields for the existing OTA firmware object."""

        return {
            "version": self.version,
            "url": self.url,
            "schema_version": 1,
            "release_id": self.release_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "model": self.model,
            "board_type": self.board_type,
            "partition_layout_id": self.partition_layout_id,
            "channel": self.channel,
            "mandatory": self.mandatory,
            "min_current_version": self.min_current_version,
        }


@dataclass(frozen=True)
class FirmwareObservation:
    """A server-received, credential-free OTA lifecycle observation."""

    release_id: str
    device_id: str
    event: str
    current_version: str
    target_version: str
    sha256: str
    slot: str
    result: str
    reason: str
    observed_at: str


class FirmwareReleaseCatalog:
    """Owns immutable firmware artifacts and release eligibility.

    Its interface deliberately stays small: publish a file as a release, select an
    offer from device facts, and open an artifact by digest.  Filesystem layout,
    hashing, SQLite persistence, compatibility checks, and URL construction are
    implementation details kept behind this module.
    """

    def __init__(
        self,
        *,
        database_path: str | Path,
        artifact_dir: str | Path,
        public_ota_url: str = "",
        default_channel: str = "stable",
        allow_insecure_http: bool = False,
    ):
        self.database_path = Path(database_path)
        self.artifact_dir = Path(artifact_dir)
        self.allow_insecure_http = bool(allow_insecure_http)
        self.public_ota_url = self._normalize_public_ota_url(
            public_ota_url,
            allow_insecure_http=self.allow_insecure_http,
        )
        self.default_channel = self._validate_channel(default_channel)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @classmethod
    def from_config(
        cls,
        config: dict,
        *,
        project_dir: str | Path | None = None,
    ) -> "FirmwareReleaseCatalog":
        release_config = config.get("ota_release", {}) or {}
        root = Path(project_dir) if project_dir is not None else Path.cwd()

        database_path = cls._resolve_path(
            root,
            release_config.get("db") or "data/museum_firmware_releases.db",
        )
        artifact_dir = cls._resolve_path(
            root,
            release_config.get("artifact_dir") or "data/museum_firmware",
        )
        return cls(
            database_path=database_path,
            artifact_dir=artifact_dir,
            public_ota_url=str(release_config.get("public_ota_url") or ""),
            default_channel=str(release_config.get("default_channel") or "stable"),
            allow_insecure_http=cls._config_bool(
                release_config.get("allow_insecure_http", False)
            ),
        )

    def create_release_from_file(
        self,
        source_path: str | Path,
        *,
        model: str,
        version: str,
        board_type: str = "",
        partition_layout_id: str = "",
        channel: str | None = None,
        mandatory: bool = False,
        min_current_version: str = "",
        state: str = "draft",
        release_id: str | None = None,
        build_git_sha: str = "",
        esp_idf_version: str = "",
        rollout_percentage: int | None = None,
        allowlisted_device_ids: Iterable[str] = (),
    ) -> FirmwareRelease:
        """Copy a file once, hash it, and record an immutable release row."""

        source = Path(source_path)
        if not source.is_file():
            raise FirmwareReleaseError("firmware source must be a regular file")

        state_value = str(state or "").strip().lower()
        if state_value not in _RELEASE_STATES:
            raise FirmwareReleaseError("release state is invalid")
        if state_value == "published" and not all(
            str(value or "").strip()
            for value in (model, board_type, partition_layout_id)
        ):
            raise FirmwareReleaseError(
                "published release requires model, board_type, and partition_layout_id"
            )

        model_value = self._validate_metadata(model, "model", allow_empty=False)
        board_type_value = self._validate_metadata(
            board_type,
            "board_type",
            allow_empty=True,
        )
        partition_value = self._validate_metadata(
            partition_layout_id,
            "partition_layout_id",
            allow_empty=True,
        )
        version_value = self._validate_version(version)
        min_version_value = (
            self._validate_version(min_current_version)
            if str(min_current_version).strip()
            else ""
        )
        channel_value = self._validate_channel(channel or self.default_channel)
        rollout_percentage_value = self._validate_rollout_percentage(
            self._default_rollout_percentage(channel_value)
            if rollout_percentage is None
            else rollout_percentage
        )
        allowlisted_devices = self._normalize_allowlisted_device_ids(
            allowlisted_device_ids
        )
        release_id_value = self._validate_release_id(
            release_id or f"rel-{uuid4().hex}"
        )

        digest, size_bytes = self._store_immutable_artifact(source)
        now = self._utc_now()
        published_at = now if state_value == "published" else None

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO firmware_artifacts (
                    sha256,
                    size_bytes,
                    created_at,
                    build_git_sha,
                    esp_idf_version
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    digest,
                    size_bytes,
                    now,
                    str(build_git_sha or ""),
                    str(esp_idf_version or ""),
                ),
            )
            artifact_row = conn.execute(
                "SELECT size_bytes FROM firmware_artifacts WHERE sha256 = ?",
                (digest,),
            ).fetchone()
            if artifact_row is None or int(artifact_row["size_bytes"]) != size_bytes:
                raise FirmwareReleaseError("artifact metadata conflicts with its digest")
            existing_target_version = conn.execute(
                """
                SELECT release_id
                FROM firmware_releases
                WHERE model = ?
                    AND board_type = ?
                    AND partition_layout_id = ?
                    AND channel = ?
                    AND version = ?
                """,
                (
                    model_value,
                    board_type_value,
                    partition_value,
                    channel_value,
                    version_value,
                ),
            ).fetchone()
            if existing_target_version is not None:
                raise FirmwareReleaseError("target version already exists")
            try:
                conn.execute(
                    """
                    INSERT INTO firmware_releases (
                        release_id,
                        artifact_sha256,
                        model,
                        board_type,
                        partition_layout_id,
                        version,
                        channel,
                        rollout_percentage,
                        mandatory,
                        min_current_version,
                        state,
                        created_at,
                        published_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        release_id_value,
                        digest,
                        model_value,
                        board_type_value,
                        partition_value,
                        version_value,
                        channel_value,
                        rollout_percentage_value,
                        int(bool(mandatory)),
                        min_version_value,
                        state_value,
                        now,
                        published_at,
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO firmware_release_allowlist (
                        release_id,
                        device_id,
                        created_at
                    ) VALUES (?, ?, ?)
                    """,
                    [
                        (release_id_value, device_id, now)
                        for device_id in allowlisted_devices
                    ],
                )
            except sqlite3.IntegrityError as exc:
                if "model, firmware_releases.board_type" in str(exc):
                    raise FirmwareReleaseError("target version already exists") from exc
                raise FirmwareReleaseError("release_id already exists") from exc

        return FirmwareRelease(
            release_id=release_id_value,
            sha256=digest,
            size_bytes=size_bytes,
            model=model_value,
            board_type=board_type_value,
            partition_layout_id=partition_value,
            version=version_value,
            channel=channel_value,
            rollout_percentage=rollout_percentage_value,
            mandatory=bool(mandatory),
            min_current_version=min_version_value,
            state=state_value,
            created_at=now,
            published_at=published_at,
        )

    def select_offer(self, check: FirmwareCheck) -> FirmwareOffer | None:
        """Return the newest compatible published release, or no offer."""

        if not self.public_ota_url:
            return None
        device_id = self._validate_device_id(check.device_id)
        model = self._validate_metadata(check.model, "model", allow_empty=False)
        board_type = self._validate_metadata(
            check.board_type,
            "board_type",
            allow_empty=True,
        )
        partition_layout_id = self._validate_metadata(
            check.partition_layout_id,
            "partition_layout_id",
            allow_empty=True,
        )
        current_version = self._validate_version(
            str(check.current_version or "0.0.0").strip() or "0.0.0"
        )
        channel = self._validate_channel(check.channel or self.default_channel)

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    release_id,
                    artifact_sha256,
                    size_bytes,
                    model,
                    board_type,
                    partition_layout_id,
                    version,
                    channel,
                    rollout_percentage,
                    mandatory,
                    min_current_version,
                    EXISTS (
                        SELECT 1
                        FROM firmware_release_allowlist
                        WHERE firmware_release_allowlist.release_id
                            = firmware_releases.release_id
                            AND firmware_release_allowlist.device_id = ?
                    ) AS is_allowlisted
                FROM firmware_releases
                JOIN firmware_artifacts
                    ON firmware_artifacts.sha256 = firmware_releases.artifact_sha256
                WHERE firmware_releases.state = 'published'
                    AND firmware_releases.model = ?
                    AND firmware_releases.channel = ?
                """,
                (device_id, model, channel),
            ).fetchall()

        eligible = [
            row
            for row in rows
            if self._is_compatible(
                row,
                board_type=board_type,
                partition_layout_id=partition_layout_id,
                current_version=current_version,
            )
            and self._is_in_rollout(row, device_id)
        ]
        if not eligible:
            return None

        for chosen in sorted(
            eligible,
            key=lambda row: self._version_key(str(row["version"])),
            reverse=True,
        ):
            digest = str(chosen["artifact_sha256"])
            # A database row alone is not evidence that the immutable file still
            # exists or has not been modified after publication.
            if self.open_artifact(digest) is None:
                continue
            return FirmwareOffer(
                release_id=str(chosen["release_id"]),
                sha256=digest,
                size_bytes=int(chosen["size_bytes"]),
                model=str(chosen["model"]),
                board_type=str(chosen["board_type"]),
                partition_layout_id=str(chosen["partition_layout_id"]),
                version=str(chosen["version"]),
                url=self._artifact_url(digest),
                channel=str(chosen["channel"]),
                mandatory=bool(chosen["mandatory"]),
                min_current_version=str(chosen["min_current_version"]),
            )
        return None

    def open_artifact(self, sha256: str) -> Path | None:
        """Open only a recorded, still byte-identical immutable artifact."""

        digest = str(sha256 or "").lower()
        if not _DIGEST_PATTERN.fullmatch(digest):
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT firmware_artifacts.size_bytes
                FROM firmware_artifacts
                WHERE firmware_artifacts.sha256 = ?
                    AND EXISTS (
                        SELECT 1
                        FROM firmware_releases
                        WHERE firmware_releases.artifact_sha256 = firmware_artifacts.sha256
                            AND firmware_releases.state = 'published'
                    )
                """,
                (digest,),
            ).fetchone()
        if row is None:
            return None

        path = self._artifact_path(digest)
        if not path.is_file() or path.stat().st_size != int(row["size_bytes"]):
            return None
        actual_digest, _actual_size = self._hash_file(path)
        if actual_digest != digest:
            return None
        return path

    def get_release(self, release_id: str) -> FirmwareRelease:
        """Return one release's immutable manifest and current rollout state."""

        release_id_value = self._validate_release_id(release_id)
        with self._connect() as conn:
            row = self._release_row(conn, release_id_value)
        if row is None:
            raise FirmwareReleaseError("release_id does not exist")
        return self._release_from_row(row)

    def set_release_state(self, release_id: str, state: str) -> FirmwareRelease:
        """Pause, publish, or revoke an existing release without mutating bytes."""

        release_id_value = self._validate_release_id(release_id)
        state_value = str(state or "").strip().lower()
        if state_value not in _RELEASE_STATES:
            raise FirmwareReleaseError("release state is invalid")

        with self._connect() as conn:
            row = self._release_row(conn, release_id_value)
            if row is None:
                raise FirmwareReleaseError("release_id does not exist")
            current_state = str(row["state"])
            if current_state == "revoked" and state_value != "revoked":
                raise FirmwareReleaseError("revoked release cannot be republished")
            if state_value == "published" and not all(
                str(row[field] or "").strip()
                for field in ("model", "board_type", "partition_layout_id")
            ):
                raise FirmwareReleaseError(
                    "published release requires model, board_type, and partition_layout_id"
                )
            now = self._utc_now()
            conn.execute(
                """
                UPDATE firmware_releases
                SET state = ?,
                    published_at = CASE
                        WHEN ? = 'published' AND published_at IS NULL THEN ?
                        ELSE published_at
                    END
                WHERE release_id = ?
                """,
                (state_value, state_value, now, release_id_value),
            )
            updated = self._release_row(conn, release_id_value)
        if updated is None:
            raise FirmwareReleaseError("release update failed")
        return self._release_from_row(updated)

    def set_rollout_percentage(
        self,
        release_id: str,
        rollout_percentage: int,
    ) -> FirmwareRelease:
        """Set a deterministic percentage gate for non-allowlisted devices."""

        release_id_value = self._validate_release_id(release_id)
        percentage_value = self._validate_rollout_percentage(rollout_percentage)
        with self._connect() as conn:
            row = self._release_row(conn, release_id_value)
            if row is None:
                raise FirmwareReleaseError("release_id does not exist")
            if str(row["state"]) == "revoked":
                raise FirmwareReleaseError("revoked release cannot be reconfigured")
            conn.execute(
                """
                UPDATE firmware_releases
                SET rollout_percentage = ?
                WHERE release_id = ?
                """,
                (percentage_value, release_id_value),
            )
            updated = self._release_row(conn, release_id_value)
        if updated is None:
            raise FirmwareReleaseError("release update failed")
        return self._release_from_row(updated)

    def add_allowlisted_device(self, release_id: str, device_id: str) -> None:
        """Allow a named device to receive a release regardless of percentage."""

        release_id_value = self._validate_release_id(release_id)
        device_id_value = self._validate_device_id(device_id)
        with self._connect() as conn:
            row = self._release_row(conn, release_id_value)
            if row is None:
                raise FirmwareReleaseError("release_id does not exist")
            if str(row["state"]) == "revoked":
                raise FirmwareReleaseError("revoked release cannot be reconfigured")
            conn.execute(
                """
                INSERT OR IGNORE INTO firmware_release_allowlist (
                    release_id,
                    device_id,
                    created_at
                ) VALUES (?, ?, ?)
                """,
                (release_id_value, device_id_value, self._utc_now()),
            )

    def remove_allowlisted_device(self, release_id: str, device_id: str) -> bool:
        """Remove a device from a release allowlist and report whether it existed."""

        release_id_value = self._validate_release_id(release_id)
        device_id_value = self._validate_device_id(device_id)
        with self._connect() as conn:
            row = self._release_row(conn, release_id_value)
            if row is None:
                raise FirmwareReleaseError("release_id does not exist")
            if str(row["state"]) == "revoked":
                raise FirmwareReleaseError("revoked release cannot be reconfigured")
            cursor = conn.execute(
                """
                DELETE FROM firmware_release_allowlist
                WHERE release_id = ? AND device_id = ?
                """,
                (release_id_value, device_id_value),
            )
        return cursor.rowcount > 0

    def list_allowlisted_devices(self, release_id: str) -> list[str]:
        release_id_value = self._validate_release_id(release_id)
        with self._connect() as conn:
            return [
                str(row["device_id"])
                for row in conn.execute(
                    """
                    SELECT device_id
                    FROM firmware_release_allowlist
                    WHERE release_id = ?
                    ORDER BY device_id
                    """,
                    (release_id_value,),
                ).fetchall()
            ]

    def record_observation(
        self,
        *,
        device_id: str,
        event: str,
        current_version: str = "",
        release_id: str = "",
        target_version: str = "",
        sha256: str = "",
        slot: str = "",
        result: str = "",
        reason: str = "",
        idempotency_key: str = "",
    ) -> FirmwareObservation:
        """Persist one credential-free OTA lifecycle fact received by the server.

        Callers must pass only protocol facts (versions, identifiers, slots, and
        short reason codes). Request bodies, authorization headers, MQTT
        credentials, and URLs are deliberately outside this audit record.
        """

        device_id_value = self._validate_device_id(device_id)
        event_value = self._validate_metadata(event, "event", allow_empty=False)
        release_id_value = (
            self._validate_release_id(release_id) if str(release_id).strip() else ""
        )
        sha256_value = str(sha256 or "").strip().lower()
        if sha256_value and not _DIGEST_PATTERN.fullmatch(sha256_value):
            raise FirmwareReleaseError("sha256 is invalid")
        current_version_value = self._validate_observation_text(
            current_version,
            "current_version",
        )
        target_version_value = self._validate_observation_text(
            target_version,
            "target_version",
        )
        slot_value = self._validate_metadata(slot, "slot", allow_empty=True)
        result_value = self._validate_metadata(result, "result", allow_empty=True)
        reason_value = self._validate_observation_text(reason, "reason", limit=256)
        idempotency_key_value = self._validate_metadata(
            idempotency_key,
            "idempotency_key",
            allow_empty=True,
        )
        observed_at = self._utc_now()
        with self._connect() as conn:
            if idempotency_key_value:
                existing = self._observation_by_idempotency_key(
                    conn,
                    idempotency_key_value,
                )
                if existing is not None:
                    return self._validate_idempotent_observation(
                        existing,
                        release_id=release_id_value,
                        device_id=device_id_value,
                        event=event_value,
                        current_version=current_version_value,
                        target_version=target_version_value,
                        sha256=sha256_value,
                        slot=slot_value,
                        result=result_value,
                        reason=reason_value,
                    )
            try:
                conn.execute(
                    """
                    INSERT INTO firmware_release_observations (
                        release_id,
                        device_id,
                        event,
                        current_version,
                        target_version,
                        sha256,
                        slot,
                        result,
                        reason,
                        idempotency_key,
                        observed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        release_id_value,
                        device_id_value,
                        event_value,
                        current_version_value,
                        target_version_value,
                        sha256_value,
                        slot_value,
                        result_value,
                        reason_value,
                        idempotency_key_value,
                        observed_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if not idempotency_key_value:
                    raise
                existing = self._observation_by_idempotency_key(
                    conn,
                    idempotency_key_value,
                )
                if existing is None:
                    raise FirmwareReleaseError("observation recording failed") from exc
                return self._validate_idempotent_observation(
                    existing,
                    release_id=release_id_value,
                    device_id=device_id_value,
                    event=event_value,
                    current_version=current_version_value,
                    target_version=target_version_value,
                    sha256=sha256_value,
                    slot=slot_value,
                    result=result_value,
                    reason=reason_value,
                )
        return FirmwareObservation(
            release_id=release_id_value,
            device_id=device_id_value,
            event=event_value,
            current_version=current_version_value,
            target_version=target_version_value,
            sha256=sha256_value,
            slot=slot_value,
            result=result_value,
            reason=reason_value,
            observed_at=observed_at,
        )

    def list_observations(
        self,
        *,
        release_id: str | None = None,
        device_id: str | None = None,
        limit: int = 100,
    ) -> list[FirmwareObservation]:
        """Query server-received OTA lifecycle facts without exposing secrets."""

        try:
            limit_value = int(limit)
        except (TypeError, ValueError) as exc:
            raise FirmwareReleaseError("observation limit is invalid") from exc
        if not 1 <= limit_value <= 1000:
            raise FirmwareReleaseError("observation limit is invalid")

        clauses: list[str] = []
        values: list[object] = []
        if release_id is not None:
            clauses.append("release_id = ?")
            values.append(self._validate_release_id(release_id))
        if device_id is not None:
            clauses.append("device_id = ?")
            values.append(self._validate_device_id(device_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit_value)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    release_id,
                    device_id,
                    event,
                    current_version,
                    target_version,
                    sha256,
                    slot,
                    result,
                    reason,
                    observed_at
                FROM firmware_release_observations
                """
                + where
                + " ORDER BY observation_id ASC LIMIT ?",
                values,
            ).fetchall()
        return [self._observation_from_row(row) for row in rows]

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS firmware_artifacts (
                    sha256 TEXT PRIMARY KEY,
                    size_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    build_git_sha TEXT NOT NULL DEFAULT '',
                    esp_idf_version TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS firmware_releases (
                    release_id TEXT PRIMARY KEY,
                    artifact_sha256 TEXT NOT NULL,
                    model TEXT NOT NULL,
                    board_type TEXT NOT NULL DEFAULT '',
                    partition_layout_id TEXT NOT NULL DEFAULT '',
                    version TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    rollout_percentage INTEGER NOT NULL,
                    mandatory INTEGER NOT NULL DEFAULT 0,
                    min_current_version TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    published_at TEXT,
                    FOREIGN KEY(artifact_sha256) REFERENCES firmware_artifacts(sha256)
                );
                CREATE INDEX IF NOT EXISTS firmware_releases_selection_idx
                    ON firmware_releases(state, model, channel);
                CREATE UNIQUE INDEX IF NOT EXISTS firmware_releases_target_version_idx
                    ON firmware_releases(
                        model,
                        board_type,
                        partition_layout_id,
                        channel,
                        version
                    );
                CREATE TABLE IF NOT EXISTS firmware_release_allowlist (
                    release_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(release_id, device_id),
                    FOREIGN KEY(release_id) REFERENCES firmware_releases(release_id)
                );
                CREATE INDEX IF NOT EXISTS firmware_release_allowlist_device_idx
                    ON firmware_release_allowlist(device_id, release_id);
                CREATE TABLE IF NOT EXISTS firmware_release_observations (
                    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    release_id TEXT NOT NULL DEFAULT '',
                    device_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    current_version TEXT NOT NULL DEFAULT '',
                    target_version TEXT NOT NULL DEFAULT '',
                    sha256 TEXT NOT NULL DEFAULT '',
                    slot TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    observed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS firmware_release_observations_release_idx
                    ON firmware_release_observations(release_id, observation_id);
                CREATE INDEX IF NOT EXISTS firmware_release_observations_device_idx
                    ON firmware_release_observations(device_id, observation_id);
                """
            )
            release_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(firmware_releases)")
            }
            if "rollout_percentage" not in release_columns:
                conn.execute(
                    "ALTER TABLE firmware_releases ADD COLUMN rollout_percentage INTEGER"
                )
            conn.execute(
                """
                UPDATE firmware_releases
                SET rollout_percentage = CASE
                    WHEN channel = 'canary' THEN 0
                    ELSE 100
                END
                WHERE rollout_percentage IS NULL
                """
            )
            observation_columns = {
                str(row["name"])
                for row in conn.execute(
                    "PRAGMA table_info(firmware_release_observations)"
                )
            }
            if "idempotency_key" not in observation_columns:
                conn.execute(
                    """
                    ALTER TABLE firmware_release_observations
                    ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''
                    """
                )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                    firmware_release_observations_idempotency_idx
                ON firmware_release_observations(idempotency_key)
                WHERE idempotency_key != ''
                """
            )

    def _store_immutable_artifact(self, source: Path) -> tuple[str, int]:
        staging_dir = self.artifact_dir / ".staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        descriptor, staging_name = tempfile.mkstemp(
            prefix="firmware-",
            suffix=".part",
            dir=staging_dir,
        )
        staging_path = Path(staging_name)
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            with (
                source.open("rb") as source_file,
                os.fdopen(descriptor, "wb") as staging_file,
            ):
                while True:
                    chunk = source_file.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size_bytes += len(chunk)
                    staging_file.write(chunk)
                staging_file.flush()
                os.fsync(staging_file.fileno())

            if size_bytes <= 0:
                raise FirmwareReleaseError("firmware artifact must not be empty")
            digest_text = digest.hexdigest()
            artifact_path = self._artifact_path(digest_text)
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(staging_path, artifact_path)
            except FileExistsError:
                existing_digest, existing_size = self._hash_file(artifact_path)
                if existing_digest != digest_text or existing_size != size_bytes:
                    raise FirmwareReleaseError("immutable artifact path is inconsistent")
            else:
                try:
                    artifact_path.chmod(0o444)
                except OSError:
                    # The digest is still verified at every open on platforms where
                    # read-only attributes are unavailable.
                    pass
            return digest_text, size_bytes
        finally:
            try:
                staging_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _is_compatible(
        self,
        row: sqlite3.Row,
        *,
        board_type: str,
        partition_layout_id: str,
        current_version: str,
    ) -> bool:
        release_board_type = str(row["board_type"] or "")
        if release_board_type and release_board_type != board_type:
            return False
        release_partition = str(row["partition_layout_id"] or "")
        if release_partition and release_partition != partition_layout_id:
            return False
        min_current_version = str(row["min_current_version"] or "")
        if min_current_version and self._compare_versions(
            current_version,
            min_current_version,
        ) < 0:
            return False
        return self._compare_versions(str(row["version"]), current_version) > 0

    def _is_in_rollout(self, row: sqlite3.Row, device_id: str) -> bool:
        if bool(row["is_allowlisted"]):
            return True
        rollout_percentage = self._validate_rollout_percentage(
            row["rollout_percentage"]
        )
        if rollout_percentage <= 0:
            return False
        if rollout_percentage >= 100:
            return True
        bucket = int.from_bytes(
            hashlib.sha256(
                f"{row['release_id']}:{device_id}".encode("utf-8")
            ).digest()[:8],
            "big",
        ) % 10_000
        return bucket < rollout_percentage * 100

    def _release_row(
        self,
        connection: sqlite3.Connection,
        release_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT
                firmware_releases.release_id,
                firmware_releases.artifact_sha256,
                firmware_artifacts.size_bytes,
                firmware_releases.model,
                firmware_releases.board_type,
                firmware_releases.partition_layout_id,
                firmware_releases.version,
                firmware_releases.channel,
                firmware_releases.rollout_percentage,
                firmware_releases.mandatory,
                firmware_releases.min_current_version,
                firmware_releases.state,
                firmware_releases.created_at,
                firmware_releases.published_at
            FROM firmware_releases
            JOIN firmware_artifacts
                ON firmware_artifacts.sha256 = firmware_releases.artifact_sha256
            WHERE firmware_releases.release_id = ?
            """,
            (release_id,),
        ).fetchone()

    @staticmethod
    def _release_from_row(row: sqlite3.Row) -> FirmwareRelease:
        return FirmwareRelease(
            release_id=str(row["release_id"]),
            sha256=str(row["artifact_sha256"]),
            size_bytes=int(row["size_bytes"]),
            model=str(row["model"]),
            board_type=str(row["board_type"]),
            partition_layout_id=str(row["partition_layout_id"]),
            version=str(row["version"]),
            channel=str(row["channel"]),
            rollout_percentage=int(row["rollout_percentage"]),
            mandatory=bool(row["mandatory"]),
            min_current_version=str(row["min_current_version"]),
            state=str(row["state"]),
            created_at=str(row["created_at"]),
            published_at=(
                str(row["published_at"])
                if row["published_at"] is not None
                else None
            ),
        )

    @staticmethod
    def _observation_from_row(row: sqlite3.Row) -> FirmwareObservation:
        return FirmwareObservation(
            release_id=str(row["release_id"]),
            device_id=str(row["device_id"]),
            event=str(row["event"]),
            current_version=str(row["current_version"]),
            target_version=str(row["target_version"]),
            sha256=str(row["sha256"]),
            slot=str(row["slot"]),
            result=str(row["result"]),
            reason=str(row["reason"]),
            observed_at=str(row["observed_at"]),
        )

    @staticmethod
    def _observation_by_idempotency_key(
        connection: sqlite3.Connection,
        idempotency_key: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT
                release_id,
                device_id,
                event,
                current_version,
                target_version,
                sha256,
                slot,
                result,
                reason,
                observed_at
            FROM firmware_release_observations
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()

    def _validate_idempotent_observation(
        self,
        row: sqlite3.Row,
        *,
        release_id: str,
        device_id: str,
        event: str,
        current_version: str,
        target_version: str,
        sha256: str,
        slot: str,
        result: str,
        reason: str,
    ) -> FirmwareObservation:
        existing = self._observation_from_row(row)
        received = (
            release_id,
            device_id,
            event,
            current_version,
            target_version,
            sha256,
            slot,
            result,
            reason,
        )
        persisted = (
            existing.release_id,
            existing.device_id,
            existing.event,
            existing.current_version,
            existing.target_version,
            existing.sha256,
            existing.slot,
            existing.result,
            existing.reason,
        )
        if persisted != received:
            raise FirmwareReleaseError(
                "idempotency key conflicts with previous observation"
            )
        return existing

    def _artifact_path(self, digest: str) -> Path:
        return self.artifact_dir / "sha256" / digest[:2] / f"{digest}.bin"

    def _artifact_url(self, digest: str) -> str:
        return f"{self.public_ota_url}/artifacts/{digest}.bin"

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _hash_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size_bytes = 0
        with path.open("rb") as artifact:
            while True:
                chunk = artifact.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size_bytes += len(chunk)
        return digest.hexdigest(), size_bytes

    @staticmethod
    def _resolve_path(root: Path, configured_path: object) -> Path:
        path = Path(str(configured_path))
        return path if path.is_absolute() else root / path

    @staticmethod
    def _config_bool(value: object) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _normalize_public_ota_url(
        value: object,
        *,
        allow_insecure_http: bool,
    ) -> str:
        url = str(value or "").strip().rstrip("/")
        if not url:
            return ""
        parsed = urlparse(url)
        accepted_schemes = {"https"}
        if allow_insecure_http:
            accepted_schemes.add("http")
        if (
            parsed.scheme not in accepted_schemes
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/museum/ota"
        ):
            raise FirmwareReleaseError(
                "public_ota_url must be an HTTPS /museum/ota/ URL; "
                "set allow_insecure_http only for development"
            )
        return url

    @staticmethod
    def _validate_metadata(
        value: object,
        field_name: str,
        *,
        allow_empty: bool,
    ) -> str:
        text = str(value or "").strip()
        if not text and allow_empty:
            return ""
        if not _METADATA_PATTERN.fullmatch(text):
            raise FirmwareReleaseError(f"{field_name} is invalid")
        return text

    @staticmethod
    def _validate_channel(value: object) -> str:
        channel = str(value or "").strip().lower()
        if not _CHANNEL_PATTERN.fullmatch(channel):
            raise FirmwareReleaseError("channel is invalid")
        return channel

    @staticmethod
    def _validate_device_id(value: object) -> str:
        return FirmwareReleaseCatalog._validate_metadata(
            value,
            "device_id",
            allow_empty=False,
        )

    @staticmethod
    def _validate_rollout_percentage(value: object) -> int:
        if isinstance(value, bool):
            raise FirmwareReleaseError("rollout_percentage is invalid")
        try:
            percentage = int(value)
        except (TypeError, ValueError) as exc:
            raise FirmwareReleaseError("rollout_percentage is invalid") from exc
        if not 0 <= percentage <= 100:
            raise FirmwareReleaseError("rollout_percentage is invalid")
        return percentage

    @staticmethod
    def _default_rollout_percentage(channel: str) -> int:
        return 0 if channel == "canary" else 100

    @classmethod
    def _normalize_allowlisted_device_ids(
        cls,
        device_ids: Iterable[str],
    ) -> tuple[str, ...]:
        if isinstance(device_ids, (str, bytes)):
            raise FirmwareReleaseError("allowlisted_device_ids must be an iterable")
        try:
            return tuple(sorted({cls._validate_device_id(value) for value in device_ids}))
        except TypeError as exc:
            raise FirmwareReleaseError("allowlisted_device_ids must be an iterable") from exc

    @staticmethod
    def _validate_observation_text(
        value: object,
        field_name: str,
        *,
        limit: int = 128,
    ) -> str:
        text = str(value or "").strip()
        if len(text) > limit or any(ord(character) < 32 for character in text):
            raise FirmwareReleaseError(f"{field_name} is invalid")
        return text

    @staticmethod
    def _validate_version(value: object) -> str:
        version = str(value or "").strip()
        if not _VERSION_PATTERN.fullmatch(version):
            raise FirmwareReleaseError("version is invalid")
        if any(int(component) > 2_147_483_647 for component in version.split(".")):
            raise FirmwareReleaseError("version is invalid")
        return version

    @staticmethod
    def _validate_release_id(value: object) -> str:
        release_id = str(value or "").strip()
        if not _METADATA_PATTERN.fullmatch(release_id):
            raise FirmwareReleaseError("release_id is invalid")
        return release_id

    @staticmethod
    def _version_key(version: str) -> tuple[int, ...]:
        try:
            return tuple(int(component) for component in version.split("."))
        except ValueError:
            return (0, 0, 0)

    @classmethod
    def _compare_versions(cls, left: str, right: str) -> int:
        left_parts = cls._version_key(left)
        right_parts = cls._version_key(right)
        for index in range(max(len(left_parts), len(right_parts))):
            left_part = left_parts[index] if index < len(left_parts) else 0
            right_part = right_parts[index] if index < len(right_parts) else 0
            if left_part > right_part:
                return 1
            if left_part < right_part:
                return -1
        return 0

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()
