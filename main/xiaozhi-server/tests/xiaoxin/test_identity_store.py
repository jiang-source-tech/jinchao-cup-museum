import sqlite3

import pytest

from core.xiaoxin.identity import store as identity_store_module
from core.xiaoxin.identity.ids import stable_hash
from core.xiaoxin.identity.store import XiaoxinIdentityStore


def _bind_seen_device(
    store: XiaoxinIdentityStore,
    device_id: str,
    owner_user_id: str,
    display_name: str,
) -> None:
    store.upsert_seen_device(device_id)
    store.bind_device(device_id, owner_user_id, display_name)


class _InsertRaceConnection:
    def __init__(self, conn, insert_marker: str, hook):
        self._conn = conn
        self._insert_marker = insert_marker
        self._hook = hook
        self._triggered = False

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self._conn.__exit__(exc_type, exc, traceback)

    def execute(self, sql, parameters=()):
        if not self._triggered and self._insert_marker in sql:
            self._triggered = True
            self._hook()
        return self._conn.execute(sql, parameters)

    def executescript(self, sql):
        return self._conn.executescript(sql)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_store_initializes_schema_and_creates_user(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")

    user = store.create_user(
        username="liu",
        password_hash="hash-value",
        display_name="liu-display",
    )

    loaded = store.get_user_by_username("liu")

    assert user.id.startswith("usr_")
    assert loaded is not None
    assert loaded.id == user.id
    assert loaded.display_name == "liu-display"


def test_student_profile_revision_is_monotonic_and_legacy_rows_are_backfilled(
    tmp_path,
):
    db_path = tmp_path / "xiaoxin_control.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL,
                last_login_at TEXT
            );
            CREATE TABLE student_profiles (
                user_id TEXT PRIMARY KEY,
                openid TEXT UNIQUE NOT NULL,
                nickname TEXT NOT NULL,
                student_no TEXT NOT NULL DEFAULT '',
                college TEXT NOT NULL DEFAULT '',
                major TEXT NOT NULL DEFAULT '',
                class_name TEXT NOT NULL DEFAULT '',
                grade TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            INSERT INTO users(
                id, username, password_hash, display_name, role, created_at
            ) VALUES (
                'user-1', 'mp:legacy-openid', 'hash', '旧用户', 'user',
                '2026-01-01T00:00:00+00:00'
            );
            INSERT INTO student_profiles(
                user_id, openid, nickname, created_at, updated_at
            ) VALUES (
                'user-1', 'legacy-openid', '旧用户',
                '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:00+00:00'
            );
            """
        )

    store = XiaoxinIdentityStore(db_path)
    migrated = store.get_student_profile_for_user("user-1")
    assert migrated is not None
    assert migrated["revision"] == 1
    assert migrated["academic_status"] == "active"

    updated = store.update_student_profile(
        "user-1", {"grade": "大二", "academic_status": "leave"}
    )
    updated_again = store.update_student_profile("user-1", {"major": "人工智能"})
    non_academic = store.update_student_profile("user-1", {"nickname": "新昵称"})
    unchanged = store.update_student_profile("user-1", {"major": "人工智能"})

    assert updated is not None
    assert updated_again is not None
    assert non_academic is not None
    assert unchanged is not None
    assert updated["revision"] == 2
    assert updated["academic_status"] == "leave"
    assert updated_again["revision"] == 3
    assert non_academic["revision"] == 3
    assert unchanged["revision"] == 3


def test_miniprogram_openid_creates_one_pending_personal_pet(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")

    first_user, _first_profile = store.get_or_create_student_by_openid(
        "wx-openid-1",
        "小杭",
    )
    second_user, _second_profile = store.get_or_create_student_by_openid(
        "wx-openid-1",
        "小杭",
    )

    first_pet = store.get_personal_pet_for_user(first_user.id)
    second_pet = store.get_personal_pet_for_user(second_user.id)

    assert first_pet is not None
    assert first_pet.id.startswith("pet_")
    assert first_pet.owner_user_id == first_user.id
    assert first_pet.status == "pending"
    assert first_pet.companion_started_at is None
    assert first_pet.started_at_source == ""
    assert second_pet == first_pet


def test_different_openids_get_isolated_personal_pets(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")

    user_a, _profile_a = store.get_or_create_student_by_openid("wx-openid-a", "甲")
    user_b, _profile_b = store.get_or_create_student_by_openid("wx-openid-b", "乙")

    pet_a = store.get_personal_pet_for_user(user_a.id)
    pet_b = store.get_personal_pet_for_user(user_b.id)

    assert pet_a is not None
    assert pet_b is not None
    assert pet_a.id != pet_b.id
    assert pet_a.owner_user_id == user_a.id
    assert pet_b.owner_user_id == user_b.id


def test_first_device_binding_activates_personal_pet_once(tmp_path, monkeypatch):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user, _profile = store.get_or_create_student_by_openid("wx-openid-1", "小杭")
    store.upsert_seen_device("device-1")
    monkeypatch.setattr(
        identity_store_module,
        "utc_now_iso",
        lambda: "2026-09-01T08:00:00+08:00",
    )

    store.bind_device("device-1", user.id, "桌面小芯")

    pet = store.get_personal_pet_for_user(user.id)

    assert pet is not None
    assert pet.status == "active"
    assert pet.companion_started_at == "2026-09-01T08:00:00+08:00"
    assert pet.started_at_source == "first_device_bind"


def test_device_replacement_preserves_personal_pet_and_companion_start(
    tmp_path,
    monkeypatch,
):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user, _profile = store.get_or_create_student_by_openid("wx-openid-1", "小杭")
    store.upsert_seen_device("device-1")
    monkeypatch.setattr(
        identity_store_module,
        "utc_now_iso",
        lambda: "2026-09-01T08:00:00+08:00",
    )
    store.bind_device("device-1", user.id, "第一台小芯")
    first_pet = store.get_personal_pet_for_user(user.id)

    assert store.unbind_device("device-1", user.id) is True
    store.upsert_seen_device("device-2")
    monkeypatch.setattr(
        identity_store_module,
        "utc_now_iso",
        lambda: "2027-01-15T08:00:00+08:00",
    )
    store.bind_device("device-2", user.id, "第二台小芯")
    replacement_pet = store.get_personal_pet_for_user(user.id)

    assert first_pet is not None
    assert replacement_pet is not None
    assert replacement_pet.id == first_pet.id
    assert replacement_pet.status == "active"
    assert replacement_pet.companion_started_at == "2026-09-01T08:00:00+08:00"
    assert replacement_pet.started_at_source == "first_device_bind"


def test_existing_bound_wechat_subject_backfills_personal_pet_from_bound_at(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "xiaoxin_control.db"
    store = XiaoxinIdentityStore(db_path)
    user, _profile = store.get_or_create_student_by_openid("wx-openid-1", "小杭")
    store.upsert_seen_device("device-1")
    monkeypatch.setattr(
        identity_store_module,
        "utc_now_iso",
        lambda: "2026-09-01T08:00:00+08:00",
    )
    store.bind_device("device-1", user.id, "桌面小芯")
    with store._connect() as conn:
        conn.execute("DELETE FROM personal_pets WHERE owner_user_id = ?", (user.id,))

    migrated_store = XiaoxinIdentityStore(db_path)
    startup_backfilled_pet = migrated_store.get_personal_pet_for_user(user.id)
    assert startup_backfilled_pet is not None

    migrated_user, _migrated_profile = migrated_store.get_or_create_student_by_openid(
        "wx-openid-1",
        "小杭",
    )
    migrated_pet = migrated_store.get_personal_pet_for_user(migrated_user.id)

    assert migrated_pet is not None
    assert migrated_pet.status == "active"
    assert migrated_pet.companion_started_at == "2026-09-01T08:00:00+08:00"
    assert migrated_pet.started_at_source == "legacy_bound_at"


def test_existing_bound_console_owner_backfills_personal_pet_without_student_profile(
    tmp_path,
):
    db_path = tmp_path / "xiaoxin_control.db"
    store = XiaoxinIdentityStore(db_path)
    user = store.create_user("legacy-console", "hash-value", "Legacy Console")
    _bind_seen_device(store, "device-1", user.id, "桌面小芯")
    store.get_or_create_memory_subject(
        user.id,
        "device-1",
        None,
        "device_unknown",
        "未知说话人",
    )
    created_pet = store.get_personal_pet_for_user(user.id)

    assert created_pet is not None
    assert created_pet.status == "active"

    with store._connect() as conn:
        conn.execute("DELETE FROM personal_pets WHERE owner_user_id = ?", (user.id,))

    migrated_store = XiaoxinIdentityStore(db_path)
    migrated_pet = migrated_store.get_personal_pet_for_user(user.id)

    assert migrated_store.get_student_profile_for_user(user.id) is None
    assert migrated_pet is not None
    assert migrated_pet.status == "active"
    assert migrated_pet.started_at_source == "legacy_bound_at"


def test_legacy_pet_backfill_uses_earliest_real_instant_across_offsets(
    tmp_path,
):
    db_path = tmp_path / "xiaoxin_control.db"
    store = XiaoxinIdentityStore(db_path)
    user, _profile = store.get_or_create_student_by_openid("wx-openid-1", "小林")
    _bind_seen_device(store, "device-1", user.id, "第一台小芯")
    _bind_seen_device(store, "device-2", user.id, "第二台小芯")
    _bind_seen_device(store, "device-invalid", user.id, "损坏时间设备")

    with store._connect() as conn:
        conn.execute(
            "UPDATE devices SET bound_at = ? WHERE device_id = ?",
            ("2026-09-01T00:30:00+00:00", "device-1"),
        )
        conn.execute(
            "UPDATE devices SET bound_at = ? WHERE device_id = ?",
            ("2026-09-01T08:00:00+08:00", "device-2"),
        )
        conn.execute(
            "UPDATE devices SET bound_at = ? WHERE device_id = ?",
            ("not-a-timestamp", "device-invalid"),
        )
        conn.execute("DELETE FROM personal_pets WHERE owner_user_id = ?", (user.id,))

    migrated_user, _profile = store.get_or_create_student_by_openid(
        "wx-openid-1",
        "小林",
    )
    migrated_pet = store.get_personal_pet_for_user(migrated_user.id)

    assert migrated_pet is not None
    assert migrated_pet.companion_started_at == "2026-09-01T08:00:00+08:00"
    assert migrated_pet.started_at_source == "legacy_bound_at"


def test_device_binding_and_subject_creation_are_stable(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user = store.create_user("liu", "hash-value", "liu-display")
    seen = store.upsert_seen_device("device-1")
    bound = store.bind_device("device-1", user.id, "desk-xiaoxin")
    speaker = store.get_or_create_speaker_profile(
        owner_user_id=user.id,
        device_id="device-1",
        speaker_key="speaker-1",
        display_name="liu-display",
    )

    subject_a = store.get_or_create_memory_subject(
        owner_user_id=user.id,
        device_id="device-1",
        speaker_profile_id=speaker.id,
        kind="user_speaker",
        display_name="liu-display",
    )
    subject_b = store.get_or_create_memory_subject(
        owner_user_id=user.id,
        device_id="device-1",
        speaker_profile_id=speaker.id,
        kind="user_speaker",
        display_name="liu-display",
    )

    assert seen.device_id == "device-1"
    assert bound.owner_user_id == user.id
    assert bound.bound_at
    assert subject_a.id.startswith("ms_")
    assert subject_b.id == subject_a.id


def test_rebinding_same_owned_device_refreshes_binding_timestamp(tmp_path, monkeypatch):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user = store.create_user("liu", "hash-value", "liu-display")
    store.upsert_seen_device("device-1")

    monkeypatch.setattr(
        identity_store_module,
        "utc_now_iso",
        lambda: "2026-03-07T09:30:00+08:00",
    )
    first_bound = store.bind_device("device-1", user.id, "desk-xiaoxin")

    monkeypatch.setattr(
        identity_store_module,
        "utc_now_iso",
        lambda: "2026-03-10T09:30:00+08:00",
    )
    rebound = store.bind_device("device-1", user.id, "desk-xiaoxin")

    assert first_bound.bound_at == "2026-03-07T09:30:00+08:00"
    assert rebound.bound_at == "2026-03-10T09:30:00+08:00"


def test_device_unbind_clears_binding_timestamp(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user = store.create_user("liu", "hash-value", "liu-display")
    store.upsert_seen_device("device-1")
    bound = store.bind_device("device-1", user.id, "desk-xiaoxin")

    unbound = store.unbind_device("device-1", user.id)
    device = store.get_device_by_device_id("device-1")

    assert bound.bound_at
    assert unbound is True
    assert device is not None
    assert device.bound_at is None


def test_existing_bound_devices_backfill_binding_timestamp(tmp_path):
    db_path = tmp_path / "legacy_identity.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE devices (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT,
                device_id TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                bind_status TEXT NOT NULL,
                tenant_id TEXT NOT NULL DEFAULT 'hzcu-iee',
                created_at TEXT NOT NULL,
                last_seen_at TEXT
            )
            """
        )
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
                "dev_legacy",
                "usr_legacy",
                "device-legacy",
                "Legacy Device",
                "bound",
                "hzcu-iee",
                "2026-03-08T09:30:00+08:00",
                "2026-03-10T09:30:00+08:00",
            ),
        )

    store = XiaoxinIdentityStore(db_path)
    device = store.get_device_by_device_id("device-legacy")

    assert device is not None
    assert device.bound_at is None


def test_bad_created_at_binding_timestamp_backfill_is_not_repaired_from_seen_time(tmp_path):
    db_path = tmp_path / "bad_backfill_identity.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE devices (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT,
                device_id TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                bind_status TEXT NOT NULL,
                tenant_id TEXT NOT NULL DEFAULT 'hzcu-iee',
                created_at TEXT NOT NULL,
                last_seen_at TEXT,
                bound_at TEXT
            )
            """
        )
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
                last_seen_at,
                bound_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "dev_bad_backfill",
                "usr_legacy",
                "device-bad-backfill",
                "Bad Backfill Device",
                "bound",
                "hzcu-iee",
                "2026-03-08T09:30:00+08:00",
                "2026-03-10T09:30:00+08:00",
                "2026-03-08T09:30:00+08:00",
            ),
        )

    store = XiaoxinIdentityStore(db_path)
    device = store.get_device_by_device_id("device-bad-backfill")

    assert device is not None
    assert device.bound_at == "2026-03-08T09:30:00+08:00"


def test_seen_device_defaults_to_first_tenant(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "identity.db")

    device = store.upsert_seen_device("device-1")

    assert device.tenant_id == "hzcu-iee"


def test_bound_device_preserves_tenant(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "identity.db")
    user = store.create_user("liu", "hash", "Liu")
    store.upsert_seen_device("device-1", tenant_id="hzcu-iee")

    device = store.bind_device("device-1", user.id, "Desk", tenant_id="hzcu-iee")

    assert device.tenant_id == "hzcu-iee"
    assert store.get_device_for_owner("hzcu-iee", "device-1", user.id) is not None
    assert store.get_device_for_owner("other-tenant", "device-1", user.id) is None


def test_student_todos_crud_sorting_and_user_isolation(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user_a = store.create_user("liu-a", "hash-a", "A")
    user_b = store.create_user("liu-b", "hash-b", "B")

    later = store.create_student_todo(
        user_a.id,
        {
            "title": "交实验报告",
            "dueAt": "2026-07-06T20:00:00+08:00",
            "notes": "别忘了附件",
        },
    )
    earlier = store.create_student_todo(
        user_a.id,
        {
            "title": "带学生证",
            "dueAt": "2026-07-06T08:00:00+08:00",
        },
    )
    store.create_student_todo(
        user_b.id,
        {
            "title": "B 的事项",
            "dueAt": "2026-07-06T07:00:00+08:00",
        },
    )

    todos = store.list_student_todos(user_a.id)

    assert [todo["id"] for todo in todos] == [earlier["id"], later["id"]]
    assert todos[0]["status"] == "pending"
    assert todos[0]["notes"] == ""
    assert store.get_student_todo(user_b.id, earlier["id"]) is None

    updated = store.update_student_todo(
        user_a.id,
        later["id"],
        {"status": "done", "title": "交实验报告终稿"},
    )

    assert updated is not None
    assert updated["status"] == "done"
    assert updated["title"] == "交实验报告终稿"
    assert store.delete_student_todo(user_a.id, earlier["id"]) is True
    assert store.delete_student_todo(user_b.id, later["id"]) is False
    assert [todo["id"] for todo in store.list_student_todos(user_a.id)] == [later["id"]]


def test_student_todos_reject_invalid_due_at(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user = store.create_user("liu", "hash-value", "Liu")

    with pytest.raises(ValueError, match="dueAt must use"):
        store.create_student_todo(
            user.id,
            {"title": "格式错误", "dueAt": "2026-7-6T08:00"},
        )


def test_student_todos_list_due_unreminded_and_mark_reminded(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user = store.create_user("liu", "hash-value", "Liu")
    due = store.create_student_todo(
        user.id,
        {"title": "带学生证", "dueAt": "2026-07-06T08:00:00+08:00"},
    )
    future = store.create_student_todo(
        user.id,
        {"title": "晚上交报告", "dueAt": "2026-07-06T20:00:00+08:00"},
    )

    due_todos = store.list_due_student_todos("2026-07-06T09:00:00+08:00")

    assert [todo["id"] for todo in due_todos] == [due["id"]]
    assert due_todos[0]["reminded_at"] == ""
    assert due_todos[0]["reminder_delivery_id"] == ""
    assert store.claim_student_todo_for_reminder(
        user.id,
        due["id"],
        "2026-07-06T09:00:00+08:00",
    ) is not None
    marked = store.mark_student_todo_reminded(
        user.id,
        due["id"],
        "del_20260706_010000_abcd1234",
        "2026-07-06T09:00:00+08:00",
    )
    assert marked is not None
    assert marked["status"] == "pending"
    assert marked["reminder_status"] == "dispatched"
    assert marked["reminded_at"] == "2026-07-06T09:00:00+08:00"
    assert marked["reminder_delivery_id"] == "del_20260706_010000_abcd1234"
    assert store.list_due_student_todos("2026-07-06T21:00:00+08:00") == [future]


def test_student_todo_reschedule_clears_previous_reminder_marker(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user = store.create_user("liu", "hash-value", "Liu")
    todo = store.create_student_todo(
        user.id,
        {"title": "带学生证", "dueAt": "2026-07-06T08:00:00+08:00"},
    )
    store.claim_student_todo_for_reminder(
        user.id,
        todo["id"],
        "2026-07-06T09:00:00+08:00",
    )
    store.mark_student_todo_reminded(
        user.id,
        todo["id"],
        "del-1",
        "2026-07-06T09:00:00+08:00",
    )

    updated = store.update_student_todo(
        user.id,
        todo["id"],
        {"dueAt": "2026-07-06T20:00:00+08:00", "status": "pending"},
    )

    assert updated["reminded_at"] == ""
    assert updated["reminder_delivery_id"] == ""
    assert store.list_due_student_todos("2026-07-06T21:00:00+08:00")[0]["id"] == todo["id"]


def test_student_todo_reminder_mark_requires_pending_status(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user = store.create_user("liu", "hash-value", "Liu")
    todo = store.create_student_todo(
        user.id,
        {"title": "带学生证", "dueAt": "2026-07-06T08:00:00+08:00"},
    )
    store.update_student_todo(user.id, todo["id"], {"status": "done"})

    assert store.mark_student_todo_reminded(
        user.id,
        todo["id"],
        "del-1",
        "2026-07-06T09:00:00+08:00",
    ) is None


def test_identity_store_repairs_only_supplied_completed_delivery_ids(tmp_path):
    db_path = tmp_path / "xiaoxin_control.db"
    store = XiaoxinIdentityStore(db_path)
    user = store.create_user("liu", "hash-value", "Liu")
    completed = store.create_student_todo(
        user.id,
        {"title": "已播报", "dueAt": "2026-07-06T08:00:00+08:00"},
    )
    failed = store.create_student_todo(
        user.id,
        {"title": "投递失败", "dueAt": "2026-07-06T08:05:00+08:00"},
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE student_todos SET reminded_at = ?, reminder_delivery_id = ? WHERE id = ?",
            ("2026-07-06T09:00:00+08:00", "del-done", completed["id"]),
        )
        conn.execute(
            "UPDATE student_todos SET reminded_at = ?, reminder_delivery_id = ? WHERE id = ?",
            ("2026-07-06T09:05:00+08:00", "del-failed", failed["id"]),
        )

    repaired_count = store.repair_todo_reminder_outcomes({"del-done"})
    repaired = store.get_student_todo(user.id, completed["id"])
    unrepaired = store.get_student_todo(user.id, failed["id"])

    assert repaired_count == 1
    assert repaired["status"] == "pending"
    assert repaired["reminder_status"] == "tts_completed"
    assert repaired["reminder_delivery_id"] == "del-done"
    assert unrepaired["status"] == "pending"
    assert unrepaired["reminder_status"] == "not_sent"
    assert store.repair_todo_reminder_outcomes({"del-done"}) == 0


def test_speaker_profile_reselects_when_unique_insert_race_loses(tmp_path, monkeypatch):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user = store.create_user("liu", "hash-value", "liu-display")
    _bind_seen_device(store, "device-1", user.id, "device-1")
    identity_key = stable_hash("speaker", user.id, "device-1", "speaker-1")
    original_connect = store._connect
    inserted = False

    def insert_winner():
        nonlocal inserted
        if inserted:
            return
        inserted = True
        with sqlite3.connect(store.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                """
                INSERT INTO speaker_profiles (
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
                    "spk_race_winner",
                    identity_key,
                    user.id,
                    "device-1",
                    "speaker-1",
                    "race winner",
                    "confirmed",
                    "2026-07-04T00:00:00+00:00",
                    "2026-07-04T00:00:00+00:00",
                ),
            )

    def connect_with_race():
        return _InsertRaceConnection(
            original_connect(),
            "speaker_profiles",
            insert_winner,
        )

    monkeypatch.setattr(store, "_connect", connect_with_race)

    speaker = store.get_or_create_speaker_profile(
        user.id,
        "device-1",
        "speaker-1",
        "liu-display",
    )

    assert speaker.id == "spk_race_winner"
    assert speaker.display_name == "race winner"


def test_memory_subject_reselects_when_unique_insert_race_loses(tmp_path, monkeypatch):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user = store.create_user("liu", "hash-value", "liu-display")
    _bind_seen_device(store, "device-1", user.id, "device-1")
    subject_key = stable_hash("subject", user.id, "device-1", "", "device_unknown")
    original_connect = store._connect
    inserted = False

    def insert_winner():
        nonlocal inserted
        if inserted:
            return
        inserted = True
        with sqlite3.connect(store.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                """
                INSERT INTO memory_subjects (
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
                    "ms_race_winner",
                    subject_key,
                    user.id,
                    "device-1",
                    None,
                    "device_unknown",
                    "race winner",
                    "2026-07-04T00:00:00+00:00",
                    None,
                ),
            )

    def connect_with_race():
        return _InsertRaceConnection(
            original_connect(),
            "memory_subjects",
            insert_winner,
        )

    monkeypatch.setattr(store, "_connect", connect_with_race)

    subject = store.get_or_create_memory_subject(
        user.id,
        "device-1",
        None,
        "device_unknown",
        "unknown-speaker",
    )

    assert subject.id == "ms_race_winner"
    assert subject.display_name == "race winner"


def test_subject_alias_resolves_and_rejects_cycles(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    unknown = store.get_or_create_memory_subject(
        None,
        "device-1",
        None,
        "device_unknown",
        "unknown-speaker",
    )
    confirmed = store.get_or_create_memory_subject(
        None,
        "device-2",
        None,
        "device_unknown",
        "unknown-speaker-2",
    )
    archived = store.get_or_create_memory_subject(
        None,
        "device-3",
        None,
        "device_unknown",
        "unknown-speaker-3",
    )

    store.create_subject_alias(unknown.id, confirmed.id, "confirmed_by_user")
    store.create_subject_alias(confirmed.id, archived.id, "archive_subject")

    assert store.resolve_subject_alias(unknown.id) == archived.id
    assert store.resolve_subject_alias(confirmed.id) == archived.id

    with pytest.raises(ValueError, match="subject alias cycle detected"):
        store.create_subject_alias(archived.id, unknown.id, "cycle_attempt")


def test_subject_alias_rejects_repointing_intermediate_node_into_cycle(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    subject_a = store.get_or_create_memory_subject(
        None,
        "device-1",
        None,
        "device_unknown",
        "subject-a",
    )
    subject_b = store.get_or_create_memory_subject(
        None,
        "device-2",
        None,
        "device_unknown",
        "subject-b",
    )
    subject_c = store.get_or_create_memory_subject(
        None,
        "device-3",
        None,
        "device_unknown",
        "subject-c",
    )

    store.create_subject_alias(subject_a.id, subject_b.id, "a_to_b")
    store.create_subject_alias(subject_b.id, subject_c.id, "b_to_c")

    with pytest.raises(ValueError, match="subject alias cycle detected"):
        store.create_subject_alias(subject_b.id, subject_a.id, "b_to_a")


def test_subject_alias_rejects_overwrite_unless_identical(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    subject_a = store.get_or_create_memory_subject(
        None,
        "device-1",
        None,
        "device_unknown",
        "subject-a",
    )
    subject_b = store.get_or_create_memory_subject(
        None,
        "device-2",
        None,
        "device_unknown",
        "subject-b",
    )
    subject_c = store.get_or_create_memory_subject(
        None,
        "device-3",
        None,
        "device_unknown",
        "subject-c",
    )

    first_alias = store.create_subject_alias(subject_a.id, subject_b.id, "first_link")
    second_alias = store.create_subject_alias(subject_a.id, subject_b.id, "first_link")

    assert second_alias.to_subject_id == first_alias.to_subject_id

    with pytest.raises(ValueError, match="already aliased"):
        store.create_subject_alias(subject_a.id, subject_c.id, "overwrite_attempt")


def test_same_display_name_does_not_determine_subject_identity(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user_a = store.create_user("liu-a", "hash-a", "same-name")
    user_b = store.create_user("liu-b", "hash-b", "same-name")
    _bind_seen_device(store, "device-a", user_a.id, "device-a")
    _bind_seen_device(store, "device-b", user_b.id, "device-b")
    speaker_a = store.get_or_create_speaker_profile(
        user_a.id,
        "device-a",
        "speaker-a",
        "same-name",
    )
    speaker_b = store.get_or_create_speaker_profile(
        user_b.id,
        "device-b",
        "speaker-b",
        "same-name",
    )

    subject_a = store.get_or_create_memory_subject(
        user_a.id,
        "device-a",
        speaker_a.id,
        "user_speaker",
        "same-name",
    )
    subject_b = store.get_or_create_memory_subject(
        user_b.id,
        "device-b",
        speaker_b.id,
        "user_speaker",
        "same-name",
    )

    assert subject_a.id != subject_b.id


def test_subject_references_require_real_rows(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user = store.create_user("liu", "hash-value", "liu-display")
    _bind_seen_device(store, "device-1", user.id, "device-1")
    subject = store.get_or_create_memory_subject(
        user.id,
        "device-1",
        None,
        "device_unknown",
        "unknown-speaker",
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.get_or_create_memory_subject(
            user.id,
            "device-1",
            "missing-speaker-profile",
            "user_speaker",
            "liu-display",
        )

    with pytest.raises(ValueError, match="to subject does not exist"):
        store.create_subject_alias(subject.id, "missing-subject", "bad_target")

    with pytest.raises(ValueError, match="from subject does not exist"):
        store.create_subject_alias("missing-subject", subject.id, "bad_source")


def test_memory_subject_rejects_speaker_profile_owner_or_device_mismatch(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user_a = store.create_user("liu-a", "hash-a", "user-a")
    user_b = store.create_user("liu-b", "hash-b", "user-b")
    _bind_seen_device(store, "device-a", user_a.id, "device-a")
    _bind_seen_device(store, "device-b", user_b.id, "device-b")
    _bind_seen_device(store, "device-c", user_a.id, "device-c")

    speaker = store.get_or_create_speaker_profile(
        owner_user_id=user_a.id,
        device_id="device-a",
        speaker_key="speaker-a",
        display_name="speaker-a",
    )

    with pytest.raises(ValueError, match="speaker_profile_id does not belong"):
        store.get_or_create_memory_subject(
            owner_user_id=user_b.id,
            device_id="device-b",
            speaker_profile_id=speaker.id,
            kind="user_speaker",
            display_name="speaker-a",
        )

    with pytest.raises(ValueError, match="speaker_profile_id does not belong"):
        store.get_or_create_memory_subject(
            owner_user_id=user_a.id,
            device_id="device-c",
            speaker_profile_id=speaker.id,
            kind="user_speaker",
            display_name="speaker-a",
        )


def test_speaker_profile_rejects_unbound_or_foreign_bound_device_for_user_scope(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user_a = store.create_user("liu-a", "hash-a", "user-a")
    user_b = store.create_user("liu-b", "hash-b", "user-b")

    store.upsert_seen_device("device-seen")
    _bind_seen_device(store, "device-owned-by-b", user_b.id, "device-b")

    with pytest.raises(ValueError, match="owner_user_id must match a bound device"):
        store.get_or_create_speaker_profile(
            owner_user_id=user_a.id,
            device_id="device-seen",
            speaker_key="speaker-seen",
            display_name="speaker-seen",
        )

    with pytest.raises(ValueError, match="owner_user_id must match a bound device"):
        store.get_or_create_speaker_profile(
            owner_user_id=user_a.id,
            device_id="device-owned-by-b",
            speaker_key="speaker-foreign",
            display_name="speaker-foreign",
        )


def test_memory_subject_rejects_unbound_or_foreign_bound_device_for_user_scope(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user_a = store.create_user("liu-a", "hash-a", "user-a")
    user_b = store.create_user("liu-b", "hash-b", "user-b")

    store.upsert_seen_device("device-seen")
    _bind_seen_device(store, "device-owned-by-b", user_b.id, "device-b")

    with pytest.raises(ValueError, match="owner_user_id must match a bound device"):
        store.get_or_create_memory_subject(
            owner_user_id=user_a.id,
            device_id="device-seen",
            speaker_profile_id=None,
            kind="device_unknown",
            display_name="unknown",
        )

    with pytest.raises(ValueError, match="owner_user_id must match a bound device"):
        store.get_or_create_memory_subject(
            owner_user_id=user_a.id,
            device_id="device-owned-by-b",
            speaker_profile_id=None,
            kind="device_unknown",
            display_name="unknown",
        )


def test_stable_hash_preserves_case_but_trims_whitespace():
    assert stable_hash(" Device-1 ", " SpeakerKey ") == stable_hash(
        "Device-1",
        "SpeakerKey",
    )
    assert stable_hash("Device-1", "SpeakerKey") != stable_hash(
        "device-1",
        "SpeakerKey",
    )
    assert stable_hash("Device-1", "SpeakerKey") != stable_hash(
        "Device-1",
        "speakerkey",
    )
