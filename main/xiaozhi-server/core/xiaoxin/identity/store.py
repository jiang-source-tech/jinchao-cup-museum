from __future__ import annotations

import re
import sqlite3
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from .ids import new_id, stable_hash
from .models import (
    DEVICE_BOUND,
    DEVICE_SEEN,
    PET_ACTIVE,
    PET_PENDING,
    SPEAKER_ARCHIVED,
    SPEAKER_CONFIRMED,
    IdentityDevice,
    IdentitySession,
    IdentityUser,
    MemorySubject,
    PersonalPet,
    SpeakerProfile,
    SubjectAlias,
    SUBJECT_DEVICE_FALLBACK,
    SUBJECT_DEVICE_UNKNOWN,
    SUBJECT_USER_SPEAKER,
    USER_ROLE_ADMIN,
    USER_ROLE_USER,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


TODO_DUE_AT_MESSAGE = "dueAt must use YYYY-MM-DDTHH:MM:SS+08:00 format"
TODO_DUE_AT_PATTERN = (
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$"
)
COURSE_LOCAL_TZ = timezone(timedelta(hours=8))
DEFAULT_TENANT_ID = "hzcu-iee"
DEFAULT_COURSE_REMIND_BEFORE_MIN = 15
MAX_COURSE_REMIND_BEFORE_MIN = 120


def normalize_todo_due_at(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(TODO_DUE_AT_MESSAGE)
        return value.isoformat(timespec="seconds")

    due_at = str(value or "").strip()
    if not due_at:
        raise ValueError("dueAt required")
    if not re.fullmatch(TODO_DUE_AT_PATTERN, due_at):
        raise ValueError(TODO_DUE_AT_MESSAGE)
    try:
        parsed = datetime.fromisoformat(due_at)
    except ValueError as exc:
        raise ValueError(TODO_DUE_AT_MESSAGE) from exc
    return parsed.isoformat(timespec="seconds")


def _todo_due_at_as_utc(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _earliest_aware_iso(values: list[Any]) -> str:
    candidates: list[tuple[datetime, str]] = []
    for value in values:
        raw = str(value or "").strip()
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.utcoffset() is None:
            continue
        candidates.append((parsed.astimezone(timezone.utc), raw))
    if not candidates:
        return ""
    return min(candidates, key=lambda candidate: candidate[0])[1]


def normalize_course_remind_before_min(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("remindBeforeMin must be 0-120")
    if isinstance(value, int):
        minutes = value
    elif isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        minutes = int(value.strip())
    else:
        raise ValueError("remindBeforeMin must be 0-120")
    if minutes < 0 or minutes > MAX_COURSE_REMIND_BEFORE_MIN:
        raise ValueError("remindBeforeMin must be 0-120")
    return minutes


def _parse_course_start_time(value: str) -> time | None:
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return time(hour=hour, minute=minute)


def _course_week_for_date(current_date, semester_start_date: str, total_weeks: int) -> int | None:
    start_date = datetime.strptime(semester_start_date, "%Y-%m-%d").date()
    current_week = ((current_date - start_date).days // 7) + 1
    if current_week < 1 or current_week > total_weeks:
        return None
    return current_week


def _course_active_in_week(week_range: str, current_week: int | None) -> bool:
    if current_week is None:
        return False

    text = str(week_range or "").strip()
    if not text:
        return True
    if "非本" in text:
        return False

    numeric_ranges = [
        (int(match.group(1)), int(match.group(2) or match.group(1)))
        for match in re.finditer(r"(\d+)(?:\s*[-~—至到]\s*(\d+))?", text)
    ]
    numeric_ranges = [
        (start, end)
        for start, end in numeric_ranges
        if start > 0 and end >= start
    ]
    if not numeric_ranges:
        return True
    return any(start <= current_week <= end for start, end in numeric_ranges)


class XiaoxinIdentityStore:
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
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user'
                        CHECK (role IN ('admin', 'user')),
                    created_at TEXT NOT NULL,
                    last_login_at TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT UNIQUE NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT,
                    device_id TEXT UNIQUE NOT NULL,
                    display_name TEXT NOT NULL,
                    bind_status TEXT NOT NULL,
                    tenant_id TEXT NOT NULL DEFAULT 'hzcu-iee',
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT,
                    bound_at TEXT,
                    FOREIGN KEY(owner_user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS speaker_profiles (
                    id TEXT PRIMARY KEY,
                    identity_key TEXT UNIQUE NOT NULL,
                    owner_user_id TEXT,
                    device_id TEXT NOT NULL,
                    speaker_key TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT,
                    FOREIGN KEY(owner_user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS memory_subjects (
                    id TEXT PRIMARY KEY,
                    subject_key TEXT UNIQUE NOT NULL,
                    owner_user_id TEXT,
                    device_id TEXT NOT NULL,
                    speaker_profile_id TEXT,
                    kind TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    merged_into_subject_id TEXT,
                    FOREIGN KEY(owner_user_id) REFERENCES users(id),
                    FOREIGN KEY(speaker_profile_id) REFERENCES speaker_profiles(id),
                    FOREIGN KEY(merged_into_subject_id) REFERENCES memory_subjects(id)
                );
                CREATE TABLE IF NOT EXISTS subject_aliases (
                    from_subject_id TEXT PRIMARY KEY,
                    to_subject_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(from_subject_id) REFERENCES memory_subjects(id),
                    FOREIGN KEY(to_subject_id) REFERENCES memory_subjects(id)
                );
                CREATE TABLE IF NOT EXISTS student_profiles (
                    user_id TEXT PRIMARY KEY,
                    openid TEXT UNIQUE NOT NULL,
                    nickname TEXT NOT NULL,
                    student_no TEXT NOT NULL DEFAULT '',
                    college TEXT NOT NULL DEFAULT '',
                    major TEXT NOT NULL DEFAULT '',
                    class_name TEXT NOT NULL DEFAULT '',
                    grade TEXT NOT NULL DEFAULT '',
                    academic_status TEXT NOT NULL DEFAULT 'active'
                        CHECK (academic_status IN ('active', 'leave', 'graduated', 'unknown')),
                    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS personal_pets (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    companion_started_at TEXT,
                    started_at_source TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(owner_user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS student_semesters (
                    user_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    total_weeks INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS student_course_reminder_settings (
                    user_id TEXT PRIMARY KEY,
                    remind_before_min INTEGER NOT NULL DEFAULT 15,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS student_courses (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    classroom TEXT NOT NULL DEFAULT '',
                    teacher TEXT NOT NULL DEFAULT '',
                    weekday INTEGER NOT NULL,
                    start_section INTEGER NOT NULL,
                    end_section INTEGER NOT NULL,
                    week_range TEXT NOT NULL DEFAULT '',
                    starts_at TEXT NOT NULL DEFAULT '',
                    ends_at TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    reminded_at TEXT NOT NULL DEFAULT '',
                    reminder_delivery_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS student_todos (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'miniprogram',
                    source_device_id TEXT NOT NULL DEFAULT '',
                    reminded_at TEXT NOT NULL DEFAULT '',
                    reminder_delivery_id TEXT NOT NULL DEFAULT '',
                    reminder_status TEXT NOT NULL DEFAULT 'not_sent',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS admin_audit_log (
                    id TEXT PRIMARY KEY,
                    actor_user_id TEXT NOT NULL,
                    target_owner_user_id TEXT,
                    target_subject_id TEXT,
                    action TEXT NOT NULL,
                    result_status TEXT NOT NULL,
                    failure_code TEXT,
                    idempotency_key TEXT,
                    reason_code TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(actor_user_id) REFERENCES users(id),
                    FOREIGN KEY(target_owner_user_id) REFERENCES users(id),
                    FOREIGN KEY(target_subject_id) REFERENCES memory_subjects(id)
                );
                CREATE INDEX IF NOT EXISTS idx_admin_audit_actor_created
                ON admin_audit_log(actor_user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_admin_audit_subject_created
                ON admin_audit_log(target_subject_id, created_at DESC);
                """
            )
            self._ensure_column(
                conn,
                "users",
                "role",
                "TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user'))",
            )
            self._ensure_column(
                conn,
                "student_profiles",
                "academic_status",
                "TEXT NOT NULL DEFAULT 'active'",
            )
            self._ensure_column(
                conn,
                "student_profiles",
                "revision",
                "INTEGER NOT NULL DEFAULT 1",
            )
            self._ensure_column(conn, "student_todos", "reminded_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(
                conn,
                "student_todos",
                "source",
                "TEXT NOT NULL DEFAULT 'miniprogram'",
            )
            self._ensure_column(
                conn,
                "student_todos",
                "source_device_id",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(conn, "student_courses", "reminded_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(
                conn,
                "devices",
                "tenant_id",
                f"TEXT NOT NULL DEFAULT '{DEFAULT_TENANT_ID}'",
            )
            self._ensure_column(conn, "devices", "bound_at", "TEXT")
            self._ensure_column(
                conn,
                "student_courses",
                "reminder_delivery_id",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn,
                "student_todos",
                "reminder_delivery_id",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn,
                "student_todos",
                "reminder_status",
                "TEXT NOT NULL DEFAULT 'not_sent'",
            )
            legacy_owner_rows = conn.execute(
                """
                SELECT legacy_owners.user_id
                FROM (
                    SELECT user_id
                    FROM student_profiles
                    UNION
                    SELECT owner_user_id AS user_id
                    FROM devices
                    WHERE owner_user_id IS NOT NULL
                      AND bind_status = ?
                    UNION
                    SELECT owner_user_id AS user_id
                    FROM memory_subjects
                ) AS legacy_owners
                JOIN users
                  ON users.id = legacy_owners.user_id
                LEFT JOIN personal_pets
                  ON personal_pets.owner_user_id = legacy_owners.user_id
                WHERE personal_pets.id IS NULL
                """,
                (DEVICE_BOUND,),
            ).fetchall()
            if legacy_owner_rows:
                now = utc_now_iso()
                for row in legacy_owner_rows:
                    self._ensure_personal_pet_in_transaction(conn, row["user_id"], now)

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        declaration: str,
    ) -> None:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        if column_name not in {str(row["name"]) for row in rows}:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {declaration}")

    def create_user(
        self,
        username: str,
        password_hash: str,
        display_name: str,
        *,
        role: str = USER_ROLE_USER,
        require_no_admin: bool = False,
    ) -> IdentityUser:
        username_value = username.strip()
        role_value = str(role or "").strip()
        if role_value not in {USER_ROLE_ADMIN, USER_ROLE_USER}:
            raise ValueError("role must be admin or user")
        now = utc_now_iso()
        user = IdentityUser(
            new_id("usr"),
            username_value,
            password_hash,
            display_name.strip() or username_value,
            role_value,
            now,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if require_no_admin:
                existing = conn.execute(
                    "SELECT 1 FROM users WHERE role = ? LIMIT 1",
                    (USER_ROLE_ADMIN,),
                ).fetchone()
                if existing is not None:
                    raise ValueError("administrator already exists")
            conn.execute(
                """
                INSERT INTO users (
                    id,
                    username,
                    password_hash,
                    display_name,
                    role,
                    created_at,
                    last_login_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user.id,
                    user.username,
                    user.password_hash,
                    user.display_name,
                    user.role,
                    user.created_at,
                    user.last_login_at,
                ),
            )
        return user

    def has_admin(self) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM users WHERE role = ? LIMIT 1",
                (USER_ROLE_ADMIN,),
            ).fetchone()
        return row is not None

    def set_user_role(self, username: str, role: str) -> tuple[str, str]:
        role_value = str(role or "").strip()
        if role_value not in {USER_ROLE_ADMIN, USER_ROLE_USER}:
            raise ValueError("role must be admin or user")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT role FROM users WHERE username = ?",
                (username.strip(),),
            ).fetchone()
            if row is None:
                raise LookupError("user not found")
            previous = str(row["role"])
            conn.execute(
                "UPDATE users SET role = ? WHERE username = ?",
                (role_value, username.strip()),
            )
        return previous, role_value

    def get_user_by_username(self, username: str) -> IdentityUser | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?",
                (username.strip(),),
            ).fetchone()
        return self._user_from_row(row) if row else None

    def get_user_by_id(self, user_id: str) -> IdentityUser | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        return self._user_from_row(row) if row else None

    def get_or_create_student_by_openid(
        self, openid: str, nickname: str | None = None
    ) -> tuple[IdentityUser, dict[str, object]]:
        openid_value = openid.strip()
        if not openid_value:
            raise ValueError("openid must not be empty")

        nickname_value = (nickname or "小芯同学").strip() or "小芯同学"
        now = utc_now_iso()
        username = f"mp:{openid_value}"
        with self._connect() as conn:
            profile_row = conn.execute(
                "SELECT * FROM student_profiles WHERE openid = ?",
                (openid_value,),
            ).fetchone()
            if profile_row is None:
                user_row = conn.execute(
                    "SELECT * FROM users WHERE username = ?",
                    (username,),
                ).fetchone()
                if user_row is None:
                    user_id = new_id("usr")
                    conn.execute(
                        """
                        INSERT INTO users (
                            id,
                            username,
                            password_hash,
                            display_name,
                            role,
                            created_at,
                            last_login_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            user_id,
                            username,
                            stable_hash("miniprogram-password", openid_value),
                            nickname_value,
                            USER_ROLE_USER,
                            now,
                            None,
                        ),
                    )
                else:
                    user_id = user_row["id"]
                    if nickname is not None:
                        conn.execute(
                            "UPDATE users SET display_name = ? WHERE id = ?",
                            (nickname_value, user_id),
                        )

                conn.execute(
                    """
                    INSERT INTO student_profiles (
                        user_id,
                        openid,
                        nickname,
                        student_no,
                        college,
                        major,
                        class_name,
                        grade,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, '', '', '', '', '', ?, ?)
                    """,
                    (user_id, openid_value, nickname_value, now, now),
                )
            elif nickname is not None and nickname_value != profile_row["nickname"]:
                conn.execute(
                    """
                    UPDATE student_profiles
                    SET nickname = ?, updated_at = ?
                    WHERE openid = ?
                    """,
                    (nickname_value, now, openid_value),
                )
                conn.execute(
                    "UPDATE users SET display_name = ? WHERE id = ?",
                    (nickname_value, profile_row["user_id"]),
                )

            user_row = conn.execute(
                """
                SELECT users.*
                FROM users
                JOIN student_profiles ON student_profiles.user_id = users.id
                WHERE student_profiles.openid = ?
                """,
                (openid_value,),
            ).fetchone()
            profile_row = conn.execute(
                "SELECT * FROM student_profiles WHERE openid = ?",
                (openid_value,),
            ).fetchone()
            self._ensure_personal_pet_in_transaction(conn, user_row["id"], now)

        return self._user_from_row(user_row), self._student_profile_from_row(profile_row)

    def get_or_create_guardian_by_openid(
        self,
        openid: str,
        nickname: str | None = None,
    ) -> IdentityUser:
        openid_value = openid.strip()
        if not openid_value:
            raise ValueError("openid must not be empty")
        nickname_value = (nickname or "小芯监护人").strip() or "小芯监护人"
        username = f"mp-guardian:{openid_value}"
        now = utc_now_iso()
        with self._connect() as conn:
            student_row = conn.execute(
                "SELECT 1 FROM student_profiles WHERE openid = ?",
                (openid_value,),
            ).fetchone()
            if student_row is not None:
                raise ValueError("wechat account is already registered as a student")
            user_row = conn.execute(
                "SELECT * FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if user_row is None:
                user_id = new_id("usr")
                conn.execute(
                    """
                    INSERT INTO users (
                        id, username, password_hash, display_name,
                        role, created_at, last_login_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        username,
                        stable_hash("miniprogram-guardian-password", openid_value),
                        nickname_value,
                        USER_ROLE_USER,
                        now,
                        None,
                    ),
                )
            elif nickname is not None:
                conn.execute(
                    "UPDATE users SET display_name = ? WHERE id = ?",
                    (nickname_value, user_row["id"]),
                )
            user_row = conn.execute(
                "SELECT * FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        return self._user_from_row(user_row)

    def get_personal_pet_for_user(self, owner_user_id: str) -> PersonalPet | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM personal_pets WHERE owner_user_id = ?",
                (owner_user_id,),
            ).fetchone()
        return self._personal_pet_from_row(row) if row else None

    @staticmethod
    def _ensure_personal_pet_in_transaction(
        conn: sqlite3.Connection,
        owner_user_id: str,
        now: str,
    ) -> None:
        bound_rows = conn.execute(
            """
            SELECT bound_at
            FROM devices
            WHERE owner_user_id = ?
              AND bind_status = ?
              AND bound_at IS NOT NULL
            """,
            (owner_user_id, DEVICE_BOUND),
        ).fetchall()
        legacy_started_at = _earliest_aware_iso(
            [row["bound_at"] for row in bound_rows]
        )
        status = PET_ACTIVE if legacy_started_at else PET_PENDING
        started_at_source = "legacy_bound_at" if legacy_started_at else ""
        conn.execute(
            """
            INSERT OR IGNORE INTO personal_pets (
                id,
                owner_user_id,
                status,
                created_at,
                companion_started_at,
                started_at_source,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("pet"),
                owner_user_id,
                status,
                now,
                legacy_started_at or None,
                started_at_source,
                now,
            ),
        )

    @classmethod
    def _activate_personal_pet_in_transaction(
        cls,
        conn: sqlite3.Connection,
        owner_user_id: str,
        now: str,
    ) -> None:
        cls._ensure_personal_pet_in_transaction(conn, owner_user_id, now)
        conn.execute(
            """
            UPDATE personal_pets
            SET status = ?,
                companion_started_at = COALESCE(companion_started_at, ?),
                started_at_source = CASE
                    WHEN companion_started_at IS NULL THEN 'first_device_bind'
                    ELSE started_at_source
                END,
                updated_at = ?
            WHERE owner_user_id = ?
            """,
            (PET_ACTIVE, now, now, owner_user_id),
        )

    def get_student_profile_for_user(self, user_id: str) -> dict[str, object] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM student_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return self._student_profile_from_row(row) if row else None

    def update_student_profile(
        self, user_id: str, fields: dict[str, object]
    ) -> dict[str, object] | None:
        allowed_fields = {
            "nickname",
            "student_no",
            "college",
            "major",
            "class_name",
            "grade",
            "academic_status",
        }
        updates = {
            key: str(value or "").strip()
            for key, value in fields.items()
            if key in allowed_fields
        }
        if not updates:
            return self.get_student_profile_for_user(user_id)
        if updates.get("academic_status", "active") not in {
            "active",
            "leave",
            "graduated",
            "unknown",
        }:
            raise ValueError("academic_status is invalid")
        with self._connect() as conn:
            current = conn.execute(
                "SELECT * FROM student_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if current is None:
                return None
            changed = {
                key: value
                for key, value in updates.items()
                if str(current[key]) != value
            }
            if not changed:
                return self._student_profile_from_row(current)

            changed["updated_at"] = utc_now_iso()
            assignments = ", ".join(f"{key} = ?" for key in changed)
            if {"grade", "academic_status", "major"} & changed.keys():
                assignments = f"{assignments}, revision = revision + 1"
            values = [*changed.values(), user_id]
            cursor = conn.execute(
                f"UPDATE student_profiles SET {assignments} WHERE user_id = ?",
                values,
            )
            if cursor.rowcount == 0:
                return None
            if "nickname" in changed:
                conn.execute(
                    "UPDATE users SET display_name = ? WHERE id = ?",
                    (changed["nickname"], user_id),
                )
            row = conn.execute(
                "SELECT * FROM student_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return self._student_profile_from_row(row)

    def get_student_semester(self, user_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM student_semesters WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row:
            return self._student_semester_from_row(row)
        now = utc_now_iso()
        return {
            "user_id": user_id,
            "label": "2025-2026 第1学期",
            "start_date": "2025-09-01",
            "total_weeks": 16,
            "created_at": now,
            "updated_at": now,
        }

    def update_student_semester(
        self, user_id: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        current = self.get_student_semester(user_id)
        label = str(fields.get("label") or current["label"]).strip() or current["label"]
        start_date = str(fields.get("startDate") or fields.get("start_date") or current["start_date"]).strip()
        total_weeks = int(fields.get("totalWeeks") or fields.get("total_weeks") or current["total_weeks"])
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO student_semesters (
                    user_id,
                    label,
                    start_date,
                    total_weeks,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    label = excluded.label,
                    start_date = excluded.start_date,
                    total_weeks = excluded.total_weeks,
                    updated_at = excluded.updated_at
                """,
                (user_id, label, start_date, total_weeks, now, now),
            )
            row = conn.execute(
                "SELECT * FROM student_semesters WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return self._student_semester_from_row(row)

    def get_student_course_reminder_settings(self, user_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM student_course_reminder_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row:
            return self._student_course_reminder_settings_from_row(row)
        now = utc_now_iso()
        return {
            "user_id": user_id,
            "remind_before_min": DEFAULT_COURSE_REMIND_BEFORE_MIN,
            "created_at": now,
            "updated_at": now,
        }

    def update_student_course_reminder_settings(
        self, user_id: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        current = self.get_student_course_reminder_settings(user_id)
        value = fields.get("remindBeforeMin", fields.get("remind_before_min"))
        remind_before_min = normalize_course_remind_before_min(
            current["remind_before_min"] if value is None else value
        )
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO student_course_reminder_settings (
                    user_id,
                    remind_before_min,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    remind_before_min = excluded.remind_before_min,
                    updated_at = excluded.updated_at
                """,
                (user_id, remind_before_min, now, now),
            )
            row = conn.execute(
                "SELECT * FROM student_course_reminder_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return self._student_course_reminder_settings_from_row(row)

    def list_student_courses(self, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM student_courses
                WHERE user_id = ?
                ORDER BY weekday ASC, start_section ASC, title ASC
                """,
                (user_id,),
            ).fetchall()
        return [self._student_course_from_row(row) for row in rows]

    def list_due_student_courses(self, now: str | datetime) -> list[dict[str, Any]]:
        now_utc = _todo_due_at_as_utc(normalize_todo_due_at(now))
        local_now = now_utc.astimezone(COURSE_LOCAL_TZ)
        local_date = local_now.date()
        next_local_date = local_date + timedelta(days=1)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT c.*,
                       COALESCE(s.start_date, '2025-09-01') AS semester_start_date,
                       COALESCE(s.total_weeks, 16) AS semester_total_weeks,
                       COALESCE(
                           r.remind_before_min,
                           ?
                       ) AS course_remind_before_min
                FROM student_courses c
                LEFT JOIN student_semesters s ON s.user_id = c.user_id
                LEFT JOIN student_course_reminder_settings r ON r.user_id = c.user_id
                WHERE c.weekday IN (?, ?)
                  AND c.starts_at != ''
                ORDER BY c.weekday ASC, c.start_section ASC, c.title ASC
                """,
                (
                    DEFAULT_COURSE_REMIND_BEFORE_MIN,
                    local_date.isoweekday(),
                    next_local_date.isoweekday(),
                ),
            ).fetchall()

        due: list[dict[str, Any]] = []
        for row in rows:
            course = self._student_course_from_row(row)
            course_date = (
                local_date
                if int(row["weekday"]) == local_date.isoweekday()
                else next_local_date
            )
            current_week = _course_week_for_date(
                course_date,
                str(row["semester_start_date"]),
                int(row["semester_total_weeks"]),
            )
            if not _course_active_in_week(course["week_range"], current_week):
                continue
            start_time = _parse_course_start_time(course["starts_at"])
            if start_time is None:
                continue
            occurrence_at = datetime.combine(
                course_date,
                start_time,
                tzinfo=COURSE_LOCAL_TZ,
            ).isoformat(timespec="seconds")
            occurrence_utc = _todo_due_at_as_utc(occurrence_at)
            remind_before_min = normalize_course_remind_before_min(
                row["course_remind_before_min"]
            )
            reminder_at_utc = occurrence_utc - timedelta(minutes=remind_before_min)
            if reminder_at_utc > now_utc:
                continue
            reminded_at = str(course.get("reminded_at") or "")
            if reminded_at and _todo_due_at_as_utc(reminded_at) >= occurrence_utc:
                continue
            due.append(
                {
                    **course,
                    "occurrence_at": occurrence_at,
                    "remind_before_min": remind_before_min,
                }
            )
        return due

    def create_student_course(
        self, user_id: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        now = utc_now_iso()
        course = self._normalize_course_fields(fields)
        course_id = new_id("course")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO student_courses (
                    id,
                    user_id,
                    title,
                    classroom,
                    teacher,
                    weekday,
                    start_section,
                    end_section,
                    week_range,
                    starts_at,
                    ends_at,
                    notes,
                    reminded_at,
                    reminder_delivery_id,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    course_id,
                    user_id,
                    course["title"],
                    course["classroom"],
                    course["teacher"],
                    course["weekday"],
                    course["start_section"],
                    course["end_section"],
                    course["week_range"],
                    course["starts_at"],
                    course["ends_at"],
                    course["notes"],
                    "",
                    "",
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM student_courses WHERE id = ? AND user_id = ?",
                (course_id, user_id),
            ).fetchone()
        return self._student_course_from_row(row)

    def get_student_course(
        self, user_id: str, course_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM student_courses WHERE id = ? AND user_id = ?",
                (course_id, user_id),
            ).fetchone()
        return self._student_course_from_row(row) if row else None

    def update_student_course(
        self, user_id: str, course_id: str, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        current = self.get_student_course(user_id, course_id)
        if current is None:
            return None
        normalized = self._normalize_course_fields({**current, **fields})
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE student_courses
                SET title = ?,
                    classroom = ?,
                    teacher = ?,
                    weekday = ?,
                    start_section = ?,
                    end_section = ?,
                    week_range = ?,
                    starts_at = ?,
                    ends_at = ?,
                    notes = ?,
                    reminded_at = '',
                    reminder_delivery_id = '',
                    updated_at = ?
                WHERE id = ?
                  AND user_id = ?
                """,
                (
                    normalized["title"],
                    normalized["classroom"],
                    normalized["teacher"],
                    normalized["weekday"],
                    normalized["start_section"],
                    normalized["end_section"],
                    normalized["week_range"],
                    normalized["starts_at"],
                    normalized["ends_at"],
                    normalized["notes"],
                    now,
                    course_id,
                    user_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM student_courses WHERE id = ? AND user_id = ?",
                (course_id, user_id),
            ).fetchone()
        return self._student_course_from_row(row)

    def delete_student_course(self, user_id: str, course_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM student_courses WHERE id = ? AND user_id = ?",
                (course_id, user_id),
            )
        return cursor.rowcount > 0

    def claim_student_course_for_reminder(
        self,
        user_id: str,
        course_id: str,
        occurrence_at: str | datetime,
    ) -> dict[str, Any] | None:
        occurrence_at_value = normalize_todo_due_at(occurrence_at)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE student_courses
                SET reminded_at = ?,
                    reminder_delivery_id = '',
                    updated_at = ?
                WHERE id = ?
                  AND user_id = ?
                  AND (reminded_at = '' OR reminded_at < ?)
                """,
                (
                    occurrence_at_value,
                    utc_now_iso(),
                    course_id,
                    user_id,
                    occurrence_at_value,
                ),
            )
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM student_courses WHERE id = ? AND user_id = ?",
                (course_id, user_id),
            ).fetchone()
        course = self._student_course_from_row(row)
        return {**course, "occurrence_at": occurrence_at_value}

    def mark_student_course_reminded(
        self,
        user_id: str,
        course_id: str,
        delivery_id: str,
        occurrence_at: str | datetime,
    ) -> dict[str, Any] | None:
        occurrence_at_value = normalize_todo_due_at(occurrence_at)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE student_courses
                SET reminder_delivery_id = ?,
                    updated_at = ?
                WHERE id = ?
                  AND user_id = ?
                  AND reminded_at = ?
                  AND reminder_delivery_id = ''
                """,
                (
                    delivery_id,
                    utc_now_iso(),
                    course_id,
                    user_id,
                    occurrence_at_value,
                ),
            )
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM student_courses WHERE id = ? AND user_id = ?",
                (course_id, user_id),
            ).fetchone()
        course = self._student_course_from_row(row)
        return {**course, "occurrence_at": occurrence_at_value}

    def release_student_course_reminder_claim(
        self,
        user_id: str,
        course_id: str,
        occurrence_at: str | datetime,
    ) -> dict[str, Any] | None:
        occurrence_at_value = normalize_todo_due_at(occurrence_at)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE student_courses
                SET reminded_at = '',
                    reminder_delivery_id = '',
                    updated_at = ?
                WHERE id = ?
                  AND user_id = ?
                  AND reminded_at = ?
                  AND reminder_delivery_id = ''
                """,
                (utc_now_iso(), course_id, user_id, occurrence_at_value),
            )
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM student_courses WHERE id = ? AND user_id = ?",
                (course_id, user_id),
            ).fetchone()
        return self._student_course_from_row(row)

    def list_student_todos(self, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM student_todos
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchall()
        todos = [self._student_todo_from_row(row) for row in rows]
        return sorted(todos, key=self._student_todo_sort_key)

    def list_pending_student_todo_delivery_ids(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT reminder_delivery_id
                FROM student_todos
                WHERE status = 'pending'
                  AND reminder_delivery_id != ''
                """
            ).fetchall()
        return {str(row["reminder_delivery_id"]) for row in rows}

    def repair_todo_reminder_outcomes(self, delivery_ids: set[str]) -> int:
        ids = sorted({str(delivery_id).strip() for delivery_id in delivery_ids if str(delivery_id).strip()})
        if not ids:
            return 0
        placeholders = ", ".join("?" for _ in ids)
        with self._connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE student_todos
                SET reminder_status = 'tts_completed',
                    updated_at = ?
                WHERE status = 'pending'
                  AND reminder_delivery_id IN ({placeholders})
                  AND reminder_status != 'tts_completed'
                """,
                (utc_now_iso(), *ids),
            )
        return cursor.rowcount

    def list_due_student_todos(self, now: str | datetime) -> list[dict[str, Any]]:
        now_utc = _todo_due_at_as_utc(normalize_todo_due_at(now))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM student_todos
                WHERE status = 'pending'
                  AND reminded_at = ''
                """
            ).fetchall()
        todos = [
            self._student_todo_from_row(row)
            for row in rows
            if _todo_due_at_as_utc(str(row["due_at"])) <= now_utc
        ]
        return sorted(todos, key=self._student_todo_sort_key)

    def create_student_todo(
        self, user_id: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        now = utc_now_iso()
        todo = self._normalize_todo_fields(fields)
        todo_id = new_id("todo")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO student_todos (
                    id,
                    user_id,
                    title,
                    due_at,
                    notes,
                    status,
                    source,
                    source_device_id,
                    reminded_at,
                    reminder_delivery_id,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, ?)
                """,
                (
                    todo_id,
                    user_id,
                    todo["title"],
                    todo["due_at"],
                    todo["notes"],
                    todo["status"],
                    todo["source"],
                    todo["source_device_id"],
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM student_todos WHERE id = ? AND user_id = ?",
                (todo_id, user_id),
            ).fetchone()
        return self._student_todo_from_row(row)

    def get_student_todo(
        self, user_id: str, todo_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM student_todos WHERE id = ? AND user_id = ?",
                (todo_id, user_id),
            ).fetchone()
        return self._student_todo_from_row(row) if row else None

    def update_student_todo(
        self, user_id: str, todo_id: str, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        current = self.get_student_todo(user_id, todo_id)
        if current is None:
            return None
        normalized = self._normalize_todo_fields({**current, **fields})
        now = utc_now_iso()
        reset_reminder = (
            "dueAt" in fields
            or "due_at" in fields
            or (
                fields.get("status") == "pending"
                and current.get("status") != "pending"
            )
        )
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE student_todos
                SET title = ?,
                    due_at = ?,
                    notes = ?,
                    status = ?,
                    reminded_at = ?,
                    reminder_delivery_id = ?,
                    reminder_status = ?,
                    updated_at = ?
                WHERE id = ?
                  AND user_id = ?
                """,
                (
                    normalized["title"],
                    normalized["due_at"],
                    normalized["notes"],
                    normalized["status"],
                    "" if reset_reminder else current["reminded_at"],
                    "" if reset_reminder else current["reminder_delivery_id"],
                    "not_sent" if reset_reminder else current["reminder_status"],
                    now,
                    todo_id,
                    user_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM student_todos WHERE id = ? AND user_id = ?",
                (todo_id, user_id),
            ).fetchone()
        return self._student_todo_from_row(row)

    def delete_student_todo(self, user_id: str, todo_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM student_todos WHERE id = ? AND user_id = ?",
                (todo_id, user_id),
            )
        return cursor.rowcount > 0

    def mark_student_todo_reminded(
        self,
        user_id: str,
        todo_id: str,
        delivery_id: str,
        reminded_at: str | datetime | None = None,
    ) -> dict[str, Any] | None:
        reminded_at_value = normalize_todo_due_at(reminded_at or datetime.now(timezone.utc))
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE student_todos
                SET reminded_at = ?,
                    reminder_delivery_id = ?,
                    reminder_status = 'dispatched',
                    updated_at = ?
                WHERE id = ?
                  AND user_id = ?
                  AND status = 'pending'
                  AND reminded_at != ''
                  AND reminder_delivery_id = ''
                """,
                (reminded_at_value, delivery_id, utc_now_iso(), todo_id, user_id),
            )
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM student_todos WHERE id = ? AND user_id = ?",
                (todo_id, user_id),
            ).fetchone()
        return self._student_todo_from_row(row)

    def mark_student_todo_reminder_missed(
        self,
        user_id: str,
        todo_id: str,
        missed_at: str | datetime | None = None,
    ) -> dict[str, Any] | None:
        missed_at_value = normalize_todo_due_at(
            missed_at or datetime.now(timezone.utc)
        )
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE student_todos
                SET reminded_at = ?,
                    reminder_delivery_id = '',
                    reminder_status = 'missed',
                    updated_at = ?
                WHERE id = ?
                  AND user_id = ?
                  AND status = 'pending'
                  AND reminded_at = ''
                """,
                (missed_at_value, utc_now_iso(), todo_id, user_id),
            )
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM student_todos WHERE id = ? AND user_id = ?",
                (todo_id, user_id),
            ).fetchone()
        return self._student_todo_from_row(row)

    def claim_student_todo_for_reminder(
        self,
        user_id: str,
        todo_id: str,
        claimed_at: str | datetime | None = None,
    ) -> dict[str, Any] | None:
        claimed_at_value = normalize_todo_due_at(claimed_at or datetime.now(timezone.utc))
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE student_todos
                SET reminded_at = ?,
                    reminder_delivery_id = '',
                    reminder_status = 'dispatching',
                    updated_at = ?
                WHERE id = ?
                  AND user_id = ?
                  AND status = 'pending'
                  AND reminded_at = ''
                """,
                (claimed_at_value, utc_now_iso(), todo_id, user_id),
            )
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM student_todos WHERE id = ? AND user_id = ?",
                (todo_id, user_id),
            ).fetchone()
        return self._student_todo_from_row(row)

    def release_student_todo_reminder_claim(
        self,
        user_id: str,
        todo_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE student_todos
                SET reminded_at = '',
                    reminder_delivery_id = '',
                    reminder_status = 'not_sent',
                    updated_at = ?
                WHERE id = ?
                  AND user_id = ?
                  AND reminder_delivery_id = ''
                """,
                (utc_now_iso(), todo_id, user_id),
            )
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM student_todos WHERE id = ? AND user_id = ?",
                (todo_id, user_id),
            ).fetchone()
        return self._student_todo_from_row(row)

    def create_session(
        self, user_id: str, token_hash: str, expires_at: str
    ) -> IdentitySession:
        now = utc_now_iso()
        session = IdentitySession(
            new_id("sess"),
            user_id,
            token_hash,
            expires_at,
            now,
            now,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id,
                    user_id,
                    token_hash,
                    expires_at,
                    created_at,
                    last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.user_id,
                    session.token_hash,
                    session.expires_at,
                    session.created_at,
                    session.last_seen_at,
                ),
            )
        return session

    def get_session_by_token_hash(self, token_hash: str) -> IdentitySession | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        return self._session_from_row(row) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE token_hash = ?",
                (token_hash,),
            )

    def touch_session(self, token_hash: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
                (utc_now_iso(), token_hash),
            )

    def mark_user_login(self, user_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?",
                (utc_now_iso(), user_id),
            )

    def upsert_seen_device(
        self,
        device_id: str,
        display_name: str | None = None,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> IdentityDevice:
        now = utc_now_iso()
        label = (display_name or device_id).strip()
        tenant_value = tenant_id.strip() or DEFAULT_TENANT_ID
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO devices (
                        id,
                        owner_user_id,
                        device_id,
                        display_name,
                        bind_status,
                        tenant_id,
                        created_at,
                        last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("dev"),
                        None,
                        device_id,
                        label,
                        DEVICE_SEEN,
                        tenant_value,
                        now,
                        now,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE devices
                    SET last_seen_at = ?,
                        tenant_id = CASE
                            WHEN COALESCE(TRIM(tenant_id), '') = '' THEN ?
                            ELSE tenant_id
                        END
                    WHERE device_id = ?
                    """,
                    (now, tenant_value, device_id),
                )
            row = conn.execute(
                "SELECT * FROM devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        return self._device_from_row(row)

    def bind_device(
        self,
        device_id: str,
        owner_user_id: str,
        display_name: str | None = None,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> IdentityDevice:
        display_name_value = display_name.strip() if display_name is not None else None
        tenant_value = tenant_id.strip() or DEFAULT_TENANT_ID
        now = utc_now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner_user_id FROM devices WHERE device_id = ? AND tenant_id = ?",
                (device_id, tenant_value),
            ).fetchone()
            if row is None:
                raise ValueError("device has not been seen")
            elif row["owner_user_id"] not in (None, owner_user_id):
                raise ValueError("device is already bound to another user")
            else:
                conn.execute(
                    "UPDATE devices SET last_seen_at = ? WHERE device_id = ? AND tenant_id = ?",
                    (now, device_id, tenant_value),
                )
            cursor = conn.execute(
                """
                UPDATE devices
                SET owner_user_id = ?,
                    display_name = COALESCE(?, display_name),
                    bind_status = ?,
                    last_seen_at = ?,
                    bound_at = ?
                WHERE device_id = ?
                  AND tenant_id = ?
                  AND (owner_user_id IS NULL OR owner_user_id = ?)
                """,
                (
                    owner_user_id,
                    display_name_value,
                    DEVICE_BOUND,
                    now,
                    now,
                    device_id,
                    tenant_value,
                    owner_user_id,
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError("device is already bound to another user")
            self._activate_personal_pet_in_transaction(conn, owner_user_id, now)
            row = conn.execute(
                "SELECT * FROM devices WHERE device_id = ? AND tenant_id = ?",
                (device_id, tenant_value),
            ).fetchone()
        return self._device_from_row(row)

    def get_device_for_owner(
        self, tenant_id: str, device_id: str, owner_user_id: str
    ) -> IdentityDevice | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM devices
                WHERE tenant_id = ?
                  AND device_id = ?
                  AND owner_user_id = ?
                  AND bind_status = ?
                """,
                (tenant_id, device_id, owner_user_id, DEVICE_BOUND),
            ).fetchone()
        return self._device_from_row(row) if row else None

    def unbind_device(self, device_id: str, owner_user_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE devices
                SET owner_user_id = NULL,
                    bind_status = ?,
                    last_seen_at = ?,
                    bound_at = NULL
                WHERE device_id = ?
                  AND owner_user_id = ?
                """,
                (DEVICE_SEEN, utc_now_iso(), device_id, owner_user_id),
            )
        return cursor.rowcount > 0

    def list_devices_for_user(self, user_id: str) -> list[IdentityDevice]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM devices
                WHERE owner_user_id = ? OR owner_user_id IS NULL
                ORDER BY last_seen_at DESC, created_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [self._device_from_row(row) for row in rows]

    def list_all_devices(self) -> list[IdentityDevice]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM devices
                ORDER BY last_seen_at DESC, created_at DESC
                """
            ).fetchall()
        return [self._device_from_row(row) for row in rows]

    def get_device_by_device_id(self, device_id: str) -> IdentityDevice | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        return self._device_from_row(row) if row else None

    def list_speakers_for_user(self, user_id: str) -> list[SpeakerProfile]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM speaker_profiles
                WHERE owner_user_id = ?
                ORDER BY COALESCE(last_seen_at, created_at) DESC
                """,
                (user_id,),
            ).fetchall()
        return [self._speaker_from_row(row) for row in rows]

    def list_all_speakers(self) -> list[SpeakerProfile]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM speaker_profiles
                ORDER BY COALESCE(last_seen_at, created_at) DESC, id DESC
                """
            ).fetchall()
        return [self._speaker_from_row(row) for row in rows]

    def list_speakers_for_device(
        self, owner_user_id: str, device_id: str
    ) -> list[SpeakerProfile]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM speaker_profiles
                WHERE owner_user_id = ? AND device_id = ?
                ORDER BY COALESCE(last_seen_at, created_at) DESC
                """,
                (owner_user_id, device_id),
            ).fetchall()
        return [self._speaker_from_row(row) for row in rows]

    def list_memory_subjects_for_user(self, user_id: str) -> list[MemorySubject]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM memory_subjects
                WHERE owner_user_id = ?
                ORDER BY created_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [self._subject_from_row(row) for row in rows]

    def list_all_memory_subject_ids(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id
                FROM memory_subjects
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def list_all_memory_subjects(self) -> list[MemorySubject]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM memory_subjects
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()
        return [self._subject_from_row(row) for row in rows]

    def get_memory_subject_for_user(
        self, subject_id: str, user_id: str
    ) -> MemorySubject | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_subjects WHERE id = ? AND owner_user_id = ?",
                (subject_id, user_id),
            ).fetchone()
        return self._subject_from_row(row) if row else None

    def get_memory_subject(self, subject_id: str) -> MemorySubject | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_subjects WHERE id = ?",
                (subject_id,),
            ).fetchone()
        return self._subject_from_row(row) if row else None

    def get_speaker_profile(self, speaker_id: str) -> SpeakerProfile | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM speaker_profiles WHERE id = ?",
                (speaker_id,),
            ).fetchone()
        return self._speaker_from_row(row) if row else None

    def record_admin_audit(
        self,
        *,
        actor_user_id: str,
        action: str,
        result_status: str,
        target_owner_user_id: str | None = None,
        target_subject_id: str | None = None,
        failure_code: str | None = None,
        idempotency_key: str | None = None,
        reason_code: str | None = None,
    ) -> dict[str, str | None]:
        record = {
            "id": new_id("aud"),
            "actor_user_id": actor_user_id,
            "target_owner_user_id": target_owner_user_id,
            "target_subject_id": target_subject_id,
            "action": action,
            "result_status": result_status,
            "failure_code": failure_code,
            "idempotency_key": idempotency_key,
            "reason_code": reason_code,
            "created_at": utc_now_iso(),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO admin_audit_log (
                    id, actor_user_id, target_owner_user_id, target_subject_id,
                    action, result_status, failure_code, idempotency_key,
                    reason_code, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["actor_user_id"],
                    record["target_owner_user_id"],
                    record["target_subject_id"],
                    record["action"],
                    record["result_status"],
                    record["failure_code"],
                    record["idempotency_key"],
                    record["reason_code"],
                    record["created_at"],
                ),
            )
        return record

    def list_admin_audits(
        self,
        *,
        actor_user_id: str | None = None,
        target_subject_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, str | None]]:
        clauses: list[str] = []
        values: list[Any] = []
        if actor_user_id:
            clauses.append("actor_user_id = ?")
            values.append(actor_user_id)
        if target_subject_id:
            clauses.append("target_subject_id = ?")
            values.append(target_subject_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        safe_limit = max(1, min(int(limit), 500))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, actor_user_id, target_owner_user_id,
                       target_subject_id, action, result_status, failure_code,
                       idempotency_key, reason_code, created_at
                FROM admin_audit_log
                {where}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (*values, safe_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_admin_audit_by_idempotency(
        self,
        *,
        actor_user_id: str,
        action: str,
        idempotency_key: str,
    ) -> dict[str, str | None] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, actor_user_id, target_owner_user_id,
                       target_subject_id, action, result_status, failure_code,
                       idempotency_key, reason_code, created_at
                FROM admin_audit_log
                WHERE actor_user_id = ?
                  AND action = ?
                  AND idempotency_key = ?
                  AND (
                      failure_code IS NULL
                      OR failure_code NOT IN (
                          'invalid_json',
                          'invalid_body',
                          'merge_fields_required',
                          'idempotency_conflict'
                      )
                  )
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (actor_user_id, action, idempotency_key),
            ).fetchone()
        return dict(row) if row is not None else None

    def archive_speaker(self, speaker_id: str, user_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE speaker_profiles SET status = ? WHERE id = ? AND owner_user_id = ?",
                (SPEAKER_ARCHIVED, speaker_id, user_id),
            )
        return cursor.rowcount > 0

    def update_speaker_display_name(
        self, speaker_id: str, user_id: str, display_name: str
    ) -> bool:
        clean_name = display_name.strip()
        if not clean_name:
            return False
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE speaker_profiles
                SET display_name = ?
                WHERE id = ? AND owner_user_id = ?
                """,
                (clean_name, speaker_id, user_id),
            )
        return cursor.rowcount > 0

    def get_or_create_speaker_profile(
        self,
        owner_user_id: str | None,
        device_id: str,
        speaker_key: str,
        display_name: str,
        status: str = SPEAKER_CONFIRMED,
        reactivate: bool = False,
    ) -> SpeakerProfile:
        identity_key = stable_hash("speaker", owner_user_id or "", device_id, speaker_key)
        now = utc_now_iso()
        with self._connect() as conn:
            self._require_owned_bound_device(conn, owner_user_id, device_id)
            row = conn.execute(
                "SELECT * FROM speaker_profiles WHERE identity_key = ?",
                (identity_key,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO speaker_profiles (
                        id,
                        identity_key,
                        owner_user_id,
                        device_id,
                        speaker_key,
                        display_name,
                        status,
                        created_at,
                        last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("spk"),
                        identity_key,
                        owner_user_id,
                        device_id,
                        speaker_key,
                        display_name,
                        status,
                        now,
                        now,
                    ),
                )
            else:
                if reactivate:
                    conn.execute(
                        """
                        UPDATE speaker_profiles
                        SET status = ?, last_seen_at = ?
                        WHERE identity_key = ?
                        """,
                        (status, now, identity_key),
                    )
                else:
                    conn.execute(
                        "UPDATE speaker_profiles SET last_seen_at = ? WHERE identity_key = ?",
                        (now, identity_key),
                    )
            row = conn.execute(
                "SELECT * FROM speaker_profiles WHERE identity_key = ?",
                (identity_key,),
            ).fetchone()
        return self._speaker_from_row(row)

    def get_or_create_memory_subject(
        self,
        owner_user_id: str | None,
        device_id: str,
        speaker_profile_id: str | None,
        kind: str,
        display_name: str,
    ) -> MemorySubject:
        subject_key = stable_hash(
            "subject",
            owner_user_id or "",
            device_id,
            speaker_profile_id or "",
            kind,
        )
        now = utc_now_iso()
        with self._connect() as conn:
            self._require_owned_bound_device(conn, owner_user_id, device_id)
            if speaker_profile_id is not None:
                speaker_row = conn.execute(
                    "SELECT * FROM speaker_profiles WHERE id = ?",
                    (speaker_profile_id,),
                ).fetchone()
                if speaker_row is not None and (
                    speaker_row["owner_user_id"] != owner_user_id
                    or speaker_row["device_id"] != device_id
                ):
                    raise ValueError(
                        "speaker_profile_id does not belong to the requested owner_user_id/device_id"
                    )
            row = conn.execute(
                "SELECT * FROM memory_subjects WHERE subject_key = ?",
                (subject_key,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO memory_subjects (
                        id,
                        subject_key,
                        owner_user_id,
                        device_id,
                        speaker_profile_id,
                        kind,
                        display_name,
                        created_at,
                        merged_into_subject_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("ms"),
                        subject_key,
                        owner_user_id,
                        device_id,
                        speaker_profile_id,
                        kind,
                        display_name,
                        now,
                        None,
                    ),
                )
            row = conn.execute(
                "SELECT * FROM memory_subjects WHERE subject_key = ?",
                (subject_key,),
            ).fetchone()
        return self._subject_from_row(row)

    def create_subject_alias(
        self, from_subject_id: str, to_subject_id: str, reason: str
    ) -> SubjectAlias:
        if from_subject_id == to_subject_id:
            raise ValueError("cannot alias a subject to itself")

        with self._connect() as conn:
            from_row = conn.execute(
                "SELECT * FROM memory_subjects WHERE id = ?",
                (from_subject_id,),
            ).fetchone()
            to_row = conn.execute(
                "SELECT * FROM memory_subjects WHERE id = ?",
                (to_subject_id,),
            ).fetchone()
            if from_row is None:
                raise ValueError("from subject does not exist")
            if to_row is None:
                raise ValueError("to subject does not exist")
            self._validate_subject_alias_compatibility(from_row, to_row)

            if self._alias_chain_contains(conn, to_subject_id, from_subject_id):
                raise ValueError("subject alias cycle detected")

            existing_row = conn.execute(
                "SELECT * FROM subject_aliases WHERE from_subject_id = ?",
                (from_subject_id,),
            ).fetchone()
            if existing_row is not None:
                if (
                    existing_row["to_subject_id"] == to_subject_id
                    and existing_row["reason"] == reason
                ):
                    return self._alias_from_row(existing_row)
                raise ValueError("from_subject_id is already aliased")

            now = utc_now_iso()
            alias = SubjectAlias(from_subject_id, to_subject_id, reason, now)
            conn.execute(
                """
                INSERT INTO subject_aliases (
                    from_subject_id,
                    to_subject_id,
                    reason,
                    created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    alias.from_subject_id,
                    alias.to_subject_id,
                    alias.reason,
                    alias.created_at,
                ),
            )
            conn.execute(
                "UPDATE memory_subjects SET merged_into_subject_id = ? WHERE id = ?",
                (to_subject_id, from_subject_id),
            )
        return alias

    @staticmethod
    def _validate_subject_alias_compatibility(
        from_row: sqlite3.Row,
        to_row: sqlite3.Row,
    ) -> None:
        if from_row["owner_user_id"] != to_row["owner_user_id"]:
            raise ValueError("subject alias owner is incompatible")
        allowed_transitions = {
            (SUBJECT_USER_SPEAKER, SUBJECT_USER_SPEAKER),
            (SUBJECT_DEVICE_UNKNOWN, SUBJECT_DEVICE_UNKNOWN),
            (SUBJECT_DEVICE_FALLBACK, SUBJECT_DEVICE_FALLBACK),
        }
        transition = (from_row["kind"], to_row["kind"])
        if transition not in allowed_transitions:
            raise ValueError("subject alias kind is incompatible")

    def resolve_subject_alias(self, subject_id: str) -> str | None:
        seen: set[str] = set()
        current = subject_id
        with self._connect() as conn:
            while current not in seen:
                seen.add(current)
                row = conn.execute(
                    "SELECT to_subject_id FROM subject_aliases WHERE from_subject_id = ?",
                    (current,),
                ).fetchone()
                if row is None:
                    exists = conn.execute(
                        "SELECT 1 FROM memory_subjects WHERE id = ?",
                        (current,),
                    ).fetchone()
                    return current if exists else None
                current = row["to_subject_id"]
        raise ValueError("subject alias cycle detected")

    def _alias_chain_contains(
        self,
        conn: sqlite3.Connection,
        start_subject_id: str,
        expected_subject_id: str,
    ) -> bool:
        seen: set[str] = set()
        current = start_subject_id
        while current not in seen:
            if current == expected_subject_id:
                return True
            seen.add(current)
            row = conn.execute(
                "SELECT to_subject_id FROM subject_aliases WHERE from_subject_id = ?",
                (current,),
            ).fetchone()
            if row is None:
                return False
            current = row["to_subject_id"]
        raise ValueError("subject alias cycle detected")

    def _require_owned_bound_device(
        self,
        conn: sqlite3.Connection,
        owner_user_id: str | None,
        device_id: str,
    ) -> None:
        if owner_user_id is None:
            return

        device_row = conn.execute(
            "SELECT owner_user_id, bind_status FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if (
            device_row is None
            or device_row["bind_status"] != DEVICE_BOUND
            or device_row["owner_user_id"] != owner_user_id
        ):
            raise ValueError("owner_user_id must match a bound device")

    def _user_from_row(self, row: sqlite3.Row) -> IdentityUser:
        return IdentityUser(
            row["id"],
            row["username"],
            row["password_hash"],
            row["display_name"],
            row["role"],
            row["created_at"],
            row["last_login_at"],
        )

    def _session_from_row(self, row: sqlite3.Row) -> IdentitySession:
        return IdentitySession(
            row["id"],
            row["user_id"],
            row["token_hash"],
            row["expires_at"],
            row["created_at"],
            row["last_seen_at"],
        )

    def _device_from_row(self, row: sqlite3.Row) -> IdentityDevice:
        return IdentityDevice(
            row["id"],
            row["owner_user_id"],
            row["device_id"],
            row["display_name"],
            row["bind_status"],
            row["tenant_id"],
            row["created_at"],
            row["last_seen_at"],
            row["bound_at"],
        )

    def _personal_pet_from_row(self, row: sqlite3.Row) -> PersonalPet:
        return PersonalPet(
            row["id"],
            row["owner_user_id"],
            row["status"],
            row["created_at"],
            row["companion_started_at"],
            row["started_at_source"],
            row["updated_at"],
        )

    def _student_profile_from_row(self, row: sqlite3.Row) -> dict[str, object]:
        return {
            "user_id": row["user_id"],
            "openid": row["openid"],
            "nickname": row["nickname"],
            "student_no": row["student_no"],
            "college": row["college"],
            "major": row["major"],
            "class_name": row["class_name"],
            "grade": row["grade"],
            "academic_status": row["academic_status"],
            "revision": int(row["revision"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _student_semester_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "user_id": row["user_id"],
            "label": row["label"],
            "start_date": row["start_date"],
            "total_weeks": int(row["total_weeks"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _student_course_reminder_settings_from_row(
        self, row: sqlite3.Row
    ) -> dict[str, Any]:
        return {
            "user_id": row["user_id"],
            "remind_before_min": normalize_course_remind_before_min(
                row["remind_before_min"]
            ),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _normalize_course_fields(self, fields: dict[str, Any]) -> dict[str, Any]:
        title = str(fields.get("title") or "").strip()
        if not title:
            raise ValueError("title required")

        weekday = int(fields.get("weekday") or 0)
        start_section = int(
            fields.get("startSection") or fields.get("start_section") or 0
        )
        end_section = int(fields.get("endSection") or fields.get("end_section") or 0)
        if weekday < 1 or weekday > 7:
            raise ValueError("weekday must be 1-7")
        if start_section < 1 or end_section < start_section:
            raise ValueError("invalid course sections")

        return {
            "title": title,
            "classroom": str(fields.get("classroom") or "").strip(),
            "teacher": str(fields.get("teacher") or "").strip(),
            "weekday": weekday,
            "start_section": start_section,
            "end_section": end_section,
            "week_range": str(
                fields.get("weekRange") or fields.get("week_range") or "第1-16周"
            ).strip(),
            "starts_at": str(
                fields.get("startsAt") or fields.get("starts_at") or ""
            ).strip(),
            "ends_at": str(fields.get("endsAt") or fields.get("ends_at") or "").strip(),
            "notes": str(fields.get("notes") or "").strip(),
        }

    def _student_course_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "title": row["title"],
            "classroom": row["classroom"],
            "teacher": row["teacher"],
            "weekday": int(row["weekday"]),
            "start_section": int(row["start_section"]),
            "end_section": int(row["end_section"]),
            "week_range": row["week_range"],
            "starts_at": row["starts_at"],
            "ends_at": row["ends_at"],
            "notes": row["notes"],
            "reminded_at": row["reminded_at"],
            "reminder_delivery_id": row["reminder_delivery_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _normalize_todo_fields(self, fields: dict[str, Any]) -> dict[str, Any]:
        title = str(fields.get("title") or "").strip()
        if not title:
            raise ValueError("title required")

        due_at = normalize_todo_due_at(fields.get("dueAt") or fields.get("due_at"))

        status = str(fields.get("status") or "pending").strip() or "pending"
        if status not in {"pending", "done"}:
            raise ValueError("status must be pending or done")

        source = str(fields.get("source") or "miniprogram").strip()
        if source not in {"miniprogram", "voice"}:
            raise ValueError("source must be miniprogram or voice")

        return {
            "title": title,
            "due_at": due_at,
            "notes": str(fields.get("notes") or "").strip(),
            "status": status,
            "source": source,
            "source_device_id": str(
                fields.get("sourceDeviceId") or fields.get("source_device_id") or ""
            ).strip(),
        }

    def _student_todo_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "title": row["title"],
            "due_at": row["due_at"],
            "notes": row["notes"],
            "status": row["status"],
            "source": row["source"],
            "source_device_id": row["source_device_id"],
            "reminded_at": row["reminded_at"],
            "reminder_delivery_id": row["reminder_delivery_id"],
            "reminder_status": row["reminder_status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _student_todo_sort_key(self, todo: dict[str, Any]) -> tuple[int, datetime, str]:
        return (
            0 if todo["status"] == "pending" else 1,
            _todo_due_at_as_utc(todo["due_at"]),
            todo["title"],
        )

    def _speaker_from_row(self, row: sqlite3.Row) -> SpeakerProfile:
        return SpeakerProfile(
            row["id"],
            row["owner_user_id"],
            row["device_id"],
            row["speaker_key"],
            row["display_name"],
            row["status"],
            row["created_at"],
            row["last_seen_at"],
        )

    def _subject_from_row(self, row: sqlite3.Row) -> MemorySubject:
        return MemorySubject(
            row["id"],
            row["owner_user_id"],
            row["device_id"],
            row["speaker_profile_id"],
            row["kind"],
            row["display_name"],
            row["created_at"],
            row["merged_into_subject_id"],
        )

    def _alias_from_row(self, row: sqlite3.Row) -> SubjectAlias:
        return SubjectAlias(
            row["from_subject_id"],
            row["to_subject_id"],
            row["reason"],
            row["created_at"],
        )
