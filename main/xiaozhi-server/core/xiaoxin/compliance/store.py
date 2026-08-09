from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.xiaoxin.identity.ids import new_id
from core.xiaoxin.identity.store import utc_now_iso

from .contracts import (
    AgeBand,
    AgeSource,
    ComplianceError,
    ComplianceRecord,
    GuardianBinding,
    MiniprogramAccount,
    ServiceMode,
)


class ComplianceStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS miniprogram_accounts (
                    id TEXT PRIMARY KEY,
                    openid TEXT NOT NULL UNIQUE,
                    account_role TEXT NOT NULL DEFAULT 'student'
                        CHECK (account_role IN ('student', 'guardian')),
                    linked_user_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(linked_user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS companion_compliance (
                    user_id TEXT PRIMARY KEY,
                    age_band TEXT NOT NULL DEFAULT 'UNKNOWN'
                        CHECK (age_band IN ('UNDER_14', 'AGE_14_17', 'AGE_18_PLUS', 'UNKNOWN')),
                    age_source TEXT
                        CHECK (age_source IS NULL OR age_source IN ('self_declared', 'guardian_confirmed', 'admin_verified')),
                    age_confirmed_at TEXT,
                    service_agreement_version TEXT,
                    privacy_policy_version TEXT,
                    risk_notice_version TEXT,
                    agreement_accepted_at TEXT,
                    proactive_enabled INTEGER NOT NULL DEFAULT 0 CHECK (proactive_enabled IN (0, 1)),
                    memory_enabled INTEGER NOT NULL DEFAULT 0 CHECK (memory_enabled IN (0, 1)),
                    mode_override TEXT
                        CHECK (mode_override IS NULL OR mode_override IN ('tool_only', 'blocked')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS guardian_bindings (
                    id TEXT PRIMARY KEY,
                    student_user_id TEXT NOT NULL,
                    guardian_account_id TEXT,
                    invitation_token_hash TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL
                        CHECK (status IN ('pending', 'confirmed', 'expired', 'revoked')),
                    consent_version TEXT,
                    expires_at TEXT NOT NULL,
                    confirmed_at TEXT,
                    revoked_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(student_user_id) REFERENCES users(id),
                    FOREIGN KEY(guardian_account_id) REFERENCES miniprogram_accounts(id)
                );
                CREATE INDEX IF NOT EXISTS idx_guardian_bindings_student_status
                ON guardian_bindings(student_user_id, status);
                CREATE INDEX IF NOT EXISTS idx_guardian_bindings_guardian_status
                ON guardian_bindings(guardian_account_id, status);
                """
            )
            self._backfill_existing_students(conn)
            self._backfill_existing_users(conn)

    def _backfill_existing_students(self, conn: sqlite3.Connection) -> None:
        if not self._table_exists(conn, "student_profiles"):
            return
        now = utc_now_iso()
        rows = conn.execute(
            "SELECT user_id, openid FROM student_profiles ORDER BY created_at"
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                INSERT OR IGNORE INTO miniprogram_accounts (
                    id, openid, account_role, linked_user_id, created_at, updated_at
                ) VALUES (?, ?, 'student', ?, ?, ?)
                """,
                (new_id("mpa"), row["openid"], row["user_id"], now, now),
            )

    def _backfill_existing_users(self, conn: sqlite3.Connection) -> None:
        if not self._table_exists(conn, "users"):
            return
        now = utc_now_iso()
        conn.execute(
            """
            INSERT OR IGNORE INTO companion_compliance (
                user_id, created_at, updated_at
            )
            SELECT id, ?, ? FROM users
            """,
            (now, now),
        )

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    def ensure_user_record(self, user_id: str) -> ComplianceRecord:
        user_id_value = str(user_id or "").strip()
        if not user_id_value:
            raise ValueError("user_id must not be empty")
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO companion_compliance (
                    user_id, created_at, updated_at
                ) VALUES (?, ?, ?)
                """,
                (user_id_value, now, now),
            )
            row = conn.execute(
                "SELECT * FROM companion_compliance WHERE user_id = ?",
                (user_id_value,),
            ).fetchone()
        if row is None:
            raise LookupError("compliance record unavailable")
        return self._record_from_row(row)

    def get_user_record(self, user_id: str) -> ComplianceRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM companion_compliance WHERE user_id = ?",
                (str(user_id or "").strip(),),
            ).fetchone()
        return self._record_from_row(row) if row else None

    def has_confirmed_guardian(
        self,
        user_id: str,
        *,
        consent_version: str | None = None,
    ) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM guardian_bindings
                WHERE student_user_id = ? AND status = 'confirmed'
                  AND (? IS NULL OR consent_version = ?)
                LIMIT 1
                """,
                (
                    str(user_id or "").strip(),
                    consent_version,
                    consent_version,
                ),
            ).fetchone()
        return row is not None

    def ensure_miniprogram_account(
        self,
        openid: str,
        *,
        account_role: str,
        linked_user_id: str | None,
    ) -> MiniprogramAccount:
        openid_value = str(openid or "").strip()
        role_value = str(account_role or "").strip()
        if not openid_value:
            raise ComplianceError("openid_required", "openid must not be empty")
        if role_value not in {"student", "guardian"}:
            raise ComplianceError("account_role_invalid", "unsupported account role")
        now = utc_now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM miniprogram_accounts WHERE openid = ?",
                (openid_value,),
            ).fetchone()
            if row is None:
                account_id = new_id("mpa")
                conn.execute(
                    """
                    INSERT OR IGNORE INTO miniprogram_accounts (
                        id, openid, account_role, linked_user_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        openid_value,
                        role_value,
                        linked_user_id,
                        now,
                        now,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM miniprogram_accounts WHERE openid = ?",
                    (openid_value,),
                ).fetchone()
            if str(row["account_role"]) != role_value:
                raise ComplianceError(
                    "account_role_conflict",
                    "wechat account role conflicts with its existing role",
                )
            existing_user_id = row["linked_user_id"]
            if existing_user_id and linked_user_id and existing_user_id != linked_user_id:
                raise ComplianceError(
                    "account_user_conflict",
                    "wechat account is linked to another user",
                )
            if linked_user_id and not existing_user_id:
                conn.execute(
                    """
                    UPDATE miniprogram_accounts
                    SET linked_user_id = ?, updated_at = ?
                    WHERE id = ? AND linked_user_id IS NULL
                    """,
                    (linked_user_id, now, row["id"]),
                )
                row = conn.execute(
                    "SELECT * FROM miniprogram_accounts WHERE id = ?",
                    (row["id"],),
                ).fetchone()
                if row["linked_user_id"] != linked_user_id:
                    raise ComplianceError(
                        "account_user_conflict",
                        "wechat account is linked to another user",
                    )
        return self._account_from_row(row)

    def get_miniprogram_account_for_user(
        self,
        user_id: str,
    ) -> MiniprogramAccount | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM miniprogram_accounts WHERE linked_user_id = ?",
                (str(user_id or "").strip(),),
            ).fetchone()
        return self._account_from_row(row) if row else None

    def declare_age_band(
        self,
        user_id: str,
        age_band: AgeBand,
        age_source: AgeSource,
    ) -> ComplianceRecord:
        self.ensure_user_record(user_id)
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE companion_compliance
                SET age_band = ?, age_source = ?, age_confirmed_at = ?,
                    proactive_enabled = CASE
                        WHEN ? = 'AGE_18_PLUS' THEN proactive_enabled ELSE 0
                    END,
                    memory_enabled = CASE
                        WHEN ? = 'AGE_18_PLUS' THEN memory_enabled ELSE 0
                    END,
                    updated_at = ?
                WHERE user_id = ? AND age_band = 'UNKNOWN'
                """,
                (
                    age_band.value,
                    age_source.value,
                    now,
                    age_band.value,
                    age_band.value,
                    now,
                    user_id,
                ),
            )
            if cursor.rowcount == 0:
                row = conn.execute(
                    "SELECT age_band FROM companion_compliance WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
                if row is None or str(row["age_band"]) != age_band.value:
                    raise ComplianceError(
                        "age_band_locked",
                        "age band changes require an appeal or administrator review",
                    )
        return self.ensure_user_record(user_id)

    def accept_agreements(
        self,
        user_id: str,
        *,
        service_agreement_version: str,
        privacy_policy_version: str,
        risk_notice_version: str,
    ) -> ComplianceRecord:
        self.ensure_user_record(user_id)
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE companion_compliance
                SET service_agreement_version = ?, privacy_policy_version = ?,
                    risk_notice_version = ?, agreement_accepted_at = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (
                    service_agreement_version,
                    privacy_policy_version,
                    risk_notice_version,
                    now,
                    now,
                    user_id,
                ),
            )
        return self.ensure_user_record(user_id)

    def update_settings(
        self,
        user_id: str,
        *,
        proactive_enabled: bool,
        memory_enabled: bool,
    ) -> ComplianceRecord:
        self.ensure_user_record(user_id)
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE companion_compliance
                SET proactive_enabled = ?, memory_enabled = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (int(proactive_enabled), int(memory_enabled), now, user_id),
            )
        return self.ensure_user_record(user_id)

    def create_guardian_invitation(
        self,
        user_id: str,
        *,
        token_hash: str,
        ttl_seconds: int,
    ) -> GuardianBinding:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires_at = (now_dt + timedelta(seconds=ttl_seconds)).isoformat()
        binding_id = new_id("gdn")
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE guardian_bindings
                SET status = 'revoked', revoked_at = ?, updated_at = ?
                WHERE student_user_id = ? AND status = 'pending'
                """,
                (now, now, user_id),
            )
            conn.execute(
                """
                INSERT INTO guardian_bindings (
                    id, student_user_id, invitation_token_hash, status,
                    expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', ?, ?, ?)
                """,
                (binding_id, user_id, token_hash, expires_at, now, now),
            )
            row = conn.execute(
                "SELECT * FROM guardian_bindings WHERE id = ?",
                (binding_id,),
            ).fetchone()
        return self._guardian_binding_from_row(row)

    def get_guardian_binding_by_token_hash(
        self,
        token_hash: str,
    ) -> GuardianBinding | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM guardian_bindings WHERE invitation_token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row and row["status"] == "pending" and self._is_expired(row["expires_at"]):
                now = utc_now_iso()
                conn.execute(
                    """
                    UPDATE guardian_bindings
                    SET status = 'expired', updated_at = ? WHERE id = ?
                    """,
                    (now, row["id"]),
                )
                row = conn.execute(
                    "SELECT * FROM guardian_bindings WHERE id = ?",
                    (row["id"],),
                ).fetchone()
        return self._guardian_binding_from_row(row) if row else None

    def latest_guardian_binding(self, user_id: str) -> GuardianBinding | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM guardian_bindings
                WHERE student_user_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (str(user_id or "").strip(),),
            ).fetchone()
            if row and row["status"] == "pending" and self._is_expired(row["expires_at"]):
                now = utc_now_iso()
                conn.execute(
                    """
                    UPDATE guardian_bindings
                    SET status = 'expired', updated_at = ? WHERE id = ?
                    """,
                    (now, row["id"]),
                )
                row = conn.execute(
                    "SELECT * FROM guardian_bindings WHERE id = ?",
                    (row["id"],),
                ).fetchone()
        return self._guardian_binding_from_row(row) if row else None

    def confirm_guardian_binding(
        self,
        binding_id: str,
        *,
        guardian_account_id: str,
        consent_version: str,
    ) -> GuardianBinding:
        now = utc_now_iso()
        expired = False
        unavailable = False
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM guardian_bindings WHERE id = ?",
                (binding_id,),
            ).fetchone()
            if row is None:
                raise ComplianceError("guardian_invitation_not_found", "invitation not found")
            if row["status"] != "pending":
                raise ComplianceError(
                    "guardian_invitation_unavailable",
                    "invitation is no longer available",
                )
            if self._is_expired(row["expires_at"]):
                cursor = conn.execute(
                    """
                    UPDATE guardian_bindings
                    SET status = 'expired', updated_at = ? WHERE id = ?
                      AND status = 'pending'
                    """,
                    (now, binding_id),
                )
                expired = cursor.rowcount == 1
                unavailable = not expired
            else:
                cursor = conn.execute(
                    """
                    UPDATE guardian_bindings
                    SET guardian_account_id = ?, status = 'confirmed',
                        consent_version = ?, confirmed_at = ?, updated_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (guardian_account_id, consent_version, now, now, binding_id),
                )
                unavailable = cursor.rowcount == 0
                if not unavailable:
                    row = conn.execute(
                        "SELECT * FROM guardian_bindings WHERE id = ?",
                        (binding_id,),
                    ).fetchone()
        if expired:
            raise ComplianceError(
                "guardian_invitation_expired",
                "invitation has expired",
            )
        if unavailable:
            raise ComplianceError(
                "guardian_invitation_unavailable",
                "invitation is no longer available",
            )
        return self._guardian_binding_from_row(row)

    def revoke_guardian_binding(
        self,
        user_id: str,
        binding_id: str,
    ) -> GuardianBinding:
        now = utc_now_iso()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM guardian_bindings
                WHERE id = ? AND student_user_id = ?
                """,
                (binding_id, user_id),
            ).fetchone()
            if row is None:
                raise ComplianceError("guardian_binding_not_found", "binding not found")
            conn.execute(
                """
                UPDATE guardian_bindings
                SET status = 'revoked', revoked_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, binding_id),
            )
            row = conn.execute(
                "SELECT * FROM guardian_bindings WHERE id = ?",
                (binding_id,),
            ).fetchone()
        return self._guardian_binding_from_row(row)

    @staticmethod
    def _is_expired(expires_at: str) -> bool:
        parsed = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc) <= datetime.now(timezone.utc)

    @staticmethod
    def _account_from_row(row: sqlite3.Row) -> MiniprogramAccount:
        return MiniprogramAccount(
            id=str(row["id"]),
            openid=str(row["openid"]),
            account_role=str(row["account_role"]),
            linked_user_id=row["linked_user_id"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _guardian_binding_from_row(row: sqlite3.Row) -> GuardianBinding:
        return GuardianBinding(
            id=str(row["id"]),
            student_user_id=str(row["student_user_id"]),
            guardian_account_id=row["guardian_account_id"],
            status=str(row["status"]),
            consent_version=row["consent_version"],
            expires_at=str(row["expires_at"]),
            confirmed_at=row["confirmed_at"],
            revoked_at=row["revoked_at"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> ComplianceRecord:
        return ComplianceRecord(
            user_id=str(row["user_id"]),
            age_band=AgeBand(str(row["age_band"])),
            age_source=(
                AgeSource(str(row["age_source"])) if row["age_source"] else None
            ),
            age_confirmed_at=row["age_confirmed_at"],
            service_agreement_version=row["service_agreement_version"],
            privacy_policy_version=row["privacy_policy_version"],
            risk_notice_version=row["risk_notice_version"],
            agreement_accepted_at=row["agreement_accepted_at"],
            proactive_enabled=bool(row["proactive_enabled"]),
            memory_enabled=bool(row["memory_enabled"]),
            mode_override=(
                ServiceMode(str(row["mode_override"]))
                if row["mode_override"]
                else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
