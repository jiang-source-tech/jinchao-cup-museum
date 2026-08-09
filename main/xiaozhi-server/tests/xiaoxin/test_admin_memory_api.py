import asyncio
import json
import sqlite3

from aiohttp.test_utils import make_mocked_request

from core.api.xiaoxin_control_handler import XiaoxinControlHandler, _csrf_token
from core.xiaoxin.companion import CompanionControlResult, CompanionProjection
from core.xiaoxin.identity.auth import XiaoxinAuthService
from core.xiaoxin.identity.admin_cli import promote_admin
from core.xiaoxin.identity.resolver import XiaoxinIdentityResolver
from core.xiaoxin.identity.store import XiaoxinIdentityStore
from core.xiaoxin.registry import XiaoxinDeviceRegistry


class _Runtime:
    def __init__(self, db_path):
        self.identity_store = XiaoxinIdentityStore(db_path)
        self.auth_service = XiaoxinAuthService(self.identity_store)
        self.identity_resolver = XiaoxinIdentityResolver(self.identity_store)
        self.registry = XiaoxinDeviceRegistry()


class _ProjectionSpy:
    def __init__(self):
        self.requests = []

    def project(self, request):
        self.requests.append(request)
        return CompanionProjection(
            surface=request.surface,
            xiaoxin_age=2,
            relationship_stage="familiar",
            payload={
                "pet_id": request.subject.pet_id,
                "memory_subject_id": request.subject.memory_subject_id,
                "evidence": (),
                "diagnostics": {"health": {"evidence_by_status": {}}},
            },
        )


class _ControlSpy(_ProjectionSpy):
    def __init__(self):
        super().__init__()
        self.commands = []

    def apply_control(self, command):
        self.commands.append(command)
        return CompanionControlResult(action=command.action, status="applied", forgotten=1)


def _request(method, path, token="", *, bearer=False, match_info=None, remote="127.0.0.1"):
    headers = {}
    if token:
        if bearer:
            headers["Authorization"] = f"Bearer {token}"
        else:
            headers["Cookie"] = f"xiaoxin_session={token}"
    request = make_mocked_request(
        method,
        path,
        headers=headers,
        match_info=match_info or {},
    )
    request._transport_peername = (remote, 12345)
    return request


def _admin_write_request(
    method,
    path,
    token,
    *,
    bearer=False,
    match_info=None,
    payload=None,
):
    csrf_token = _csrf_token(token)
    auth_headers = (
        {"Authorization": f"Bearer {token}"}
        if bearer
        else {"Cookie": f"xiaoxin_session={token}; xiaoxin_csrf={csrf_token}"}
    )
    request = make_mocked_request(
        method,
        path,
        headers={
            **auth_headers,
            "X-Xiaoxin-CSRF": csrf_token,
            "Content-Type": "application/json",
        },
        match_info=match_info or {},
    )
    request._transport_peername = ("127.0.0.1", 12345)
    request._read_bytes = json.dumps(payload or {}).encode()
    return request


def _register(runtime, username, role="user"):
    user, token = runtime.auth_service.register(
        username,
        "secret-pass",
        username.title(),
        role=role,
    )
    return user, token


def _confirmed_subject(runtime, owner, *, device_id="device-owner"):
    runtime.identity_store.upsert_seen_device(device_id, "Owner Device")
    runtime.identity_store.bind_device(device_id, owner.id, "Owner Device")
    speaker = runtime.identity_store.get_or_create_speaker_profile(
        owner.id,
        device_id,
        "voiceprint-owner-secret",
        "Owner Speaker",
    )
    return runtime.identity_store.get_or_create_memory_subject(
        owner.id,
        device_id,
        speaker.id,
        "user_speaker",
        "Owner Speaker",
    )


def test_existing_users_migrate_to_user_role(tmp_path):
    db_path = tmp_path / "identity.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_login_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
            ("usr_legacy", "jiang", "hash", "Jiang", "2026-01-01T00:00:00+00:00", None),
        )

    store = XiaoxinIdentityStore(db_path)

    assert store.get_user_by_username("jiang").role == "user"
    assert store.has_admin() is False


def test_first_console_registration_creates_only_public_admin(tmp_path):
    async def scenario():
        runtime = _Runtime(tmp_path / "identity.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)

        first = _request("POST", "/api/xiaoxin/auth/register")
        first._read_bytes = json.dumps(
            {"username": "jiang", "password": "secret-pass", "display_name": "Jiang"}
        ).encode()
        first_response = await handler.handle_register(first)

        second = _request("POST", "/api/xiaoxin/auth/register")
        second._read_bytes = json.dumps(
            {"username": "other", "password": "secret-pass", "display_name": "Other"}
        ).encode()
        second_response = await handler.handle_register(second)
        return first_response, second_response, runtime

    first, second, runtime = asyncio.run(scenario())

    assert first.status == 200
    assert json.loads(first.text)["user"]["role"] == "admin"
    session_token = first.cookies["xiaoxin_session"].value
    assert first.cookies["xiaoxin_csrf"].value == _csrf_token(session_token)
    assert runtime.identity_store.get_user_by_username("jiang").role == "admin"
    assert second.status == 403
    assert json.loads(second.text)["code"] == "registration_closed"


def test_first_admin_registration_rejects_non_local_request(tmp_path):
    async def scenario():
        runtime = _Runtime(tmp_path / "identity.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        request = _request(
            "POST",
            "/api/xiaoxin/auth/register",
            remote="192.0.2.10",
        )
        request._read_bytes = json.dumps(
            {"username": "jiang", "password": "secret-pass", "display_name": "Jiang"}
        ).encode()
        return await handler.handle_register(request)

    response = asyncio.run(scenario())

    assert response.status == 403
    assert json.loads(response.text)["code"] == "local_registration_required"


def test_miniprogram_account_is_never_admin(tmp_path):
    runtime = _Runtime(tmp_path / "identity.db")

    user, _profile = runtime.identity_store.get_or_create_student_by_openid(
        "wx-first-user",
        "Mini Program User",
    )

    assert user.role == "user"
    assert runtime.identity_store.has_admin() is False


def test_admin_promotion_is_explicit_idempotent_and_rejects_missing_user(tmp_path):
    db_path = tmp_path / "identity.db"
    store = XiaoxinIdentityStore(db_path)
    store.create_user("jiang", "hash", "Jiang")

    first = promote_admin(db_path, "jiang")
    second = promote_admin(db_path, "jiang")

    assert first == {"username": "jiang", "before": "user", "after": "admin"}
    assert second == {"username": "jiang", "before": "admin", "after": "admin"}
    try:
        promote_admin(db_path, "missing")
    except LookupError as exc:
        assert str(exc) == "user not found"
    else:
        raise AssertionError("missing user promotion must fail")


def test_admin_memory_api_rejects_regular_user_for_cookie_and_bearer(tmp_path):
    async def scenario():
        runtime = _Runtime(tmp_path / "identity.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _user, token = _register(runtime, "regular")
        results = []
        for bearer in (False, True):
            response = await handler.handle_admin_memory_subjects(
                _request(
                    "GET",
                    "/api/xiaoxin/admin/memory-subjects",
                    token,
                    bearer=bearer,
                )
            )
            results.append((response.status, json.loads(response.text)))
        return results

    results = asyncio.run(scenario())

    assert results == [
        (403, {"success": False, "code": "admin_required", "message": "admin required"}),
        (403, {"success": False, "code": "admin_required", "message": "admin required"}),
    ]


def test_regular_user_device_list_does_not_expose_other_owner_or_unbound_devices(tmp_path):
    async def scenario():
        runtime = _Runtime(tmp_path / "identity.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        alice, token = _register(runtime, "alice")
        bob, _ = _register(runtime, "bob")
        for device_id, owner in (("alice-device", alice), ("bob-device", bob)):
            runtime.identity_store.upsert_seen_device(device_id, device_id)
            runtime.identity_store.bind_device(device_id, owner.id, device_id)
        runtime.identity_store.upsert_seen_device("unbound-device", "unbound-device")

        response = await handler.handle_devices(
            _request("GET", "/api/xiaoxin/devices", token)
        )
        return response

    response = asyncio.run(scenario())
    body = json.loads(response.text)

    assert response.status == 200
    assert [item["device_id"] for item in body["devices"]] == ["alice-device"]


def test_regular_user_cannot_dispatch_to_other_owner_with_cookie_or_bearer(tmp_path):
    async def scenario():
        runtime = _Runtime(tmp_path / "identity.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _alice, token = _register(runtime, "alice")
        bob, _ = _register(runtime, "bob")
        runtime.identity_store.upsert_seen_device("bob-device", "Bob Device")
        runtime.identity_store.bind_device("bob-device", bob.id, "Bob Device")
        results = []
        for bearer in (False, True):
            request = _request(
                "POST",
                "/api/xiaoxin/events",
                token,
                bearer=bearer,
            )
            request._read_bytes = json.dumps(
                {
                    "device_id": "bob-device",
                    "event": "notification",
                    "title": "Private",
                    "body": "Private",
                }
            ).encode()
            response = await handler.handle_create_event(request)
            results.append((response.status, json.loads(response.text)))
        return results

    results = asyncio.run(scenario())

    assert results == [
        (404, {"success": False, "message": "device not found"}),
        (404, {"success": False, "message": "device not found"}),
    ]


def test_admin_list_sees_multiple_owners_and_recommends_ready_subject(tmp_path):
    async def scenario():
        runtime = _Runtime(tmp_path / "identity.db")
        from core.xiaoxin.companion import CompanionMind
        from core.xiaoxin.companion.store import CompanionStore

        runtime.companion_mind = CompanionMind(
            store=CompanionStore(tmp_path / "companion.db")
        )
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _admin, admin_token = _register(runtime, "admin", role="admin")
        owner_a, _ = _register(runtime, "owner-a")
        owner_b, _ = _register(runtime, "owner-b")
        ready = _confirmed_subject(runtime, owner_a, device_id="online-device")
        runtime.registry.update_doorbell_status("online-device", "online")
        runtime.identity_store.upsert_seen_device("old-device", "Old Device")
        runtime.identity_store.bind_device("old-device", owner_b.id, "Old Device")
        blocked = runtime.identity_store.get_or_create_memory_subject(
            owner_b.id,
            "old-device",
            None,
            "device_unknown",
            "Unknown Speaker",
        )

        response = await handler.handle_admin_memory_subjects(
            _request("GET", "/api/xiaoxin/admin/memory-subjects", admin_token)
        )
        return response, ready, blocked

    response, ready, blocked = asyncio.run(scenario())
    body = json.loads(response.text)

    assert response.status == 200
    assert body["total"] == 2
    assert {item["owner"]["username"] for item in body["memory_subjects"]} == {
        "owner-a",
        "owner-b",
    }
    assert body["recommended_subject_id"] == ready.id
    assert body["memory_subjects"][0]["id"] == ready.id
    assert body["memory_subjects"][0]["readiness"]["code"] == "ready"
    assert body["memory_subjects"][0]["counts"] == {
        "available": True,
        "evidence": 0,
        "candidate_facts": 0,
        "jobs": 0,
        "errors": 0,
    }
    blocked_item = next(item for item in body["memory_subjects"] if item["id"] == blocked.id)
    assert blocked_item["readiness"]["code"] == "speaker_unconfirmed"
    assert "voiceprint-owner-secret" not in json.dumps(body)


def test_admin_detail_uses_target_owner_context_and_records_safe_audit(tmp_path):
    async def scenario():
        runtime = _Runtime(tmp_path / "identity.db")
        projection = _ProjectionSpy()
        runtime.companion_mind = projection
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        admin, admin_token = _register(runtime, "admin", role="admin")
        owner, _ = runtime.identity_store.get_or_create_student_by_openid(
            "wx-owner",
            "Target Owner",
        )
        runtime.identity_store.update_student_profile(owner.id, {"grade": "大二"})
        subject = _confirmed_subject(runtime, owner)

        response = await handler.handle_admin_memory_subject_detail(
            _request(
                "GET",
                f"/api/xiaoxin/admin/memory-subjects/{subject.id}",
                admin_token,
                match_info={"subject_id": subject.id},
            )
        )
        audits = runtime.identity_store.list_admin_audits(actor_user_id=admin.id)
        return response, projection, owner, subject, audits

    response, projection, owner, subject, audits = asyncio.run(scenario())
    body = json.loads(response.text)

    assert response.status == 200
    request = projection.requests[0]
    assert request.subject.owner_user_id == owner.id
    assert request.subject.memory_subject_id == subject.id
    assert request.subject.pet_id == body["identity"]["pet"]["id"]
    assert body["readiness"]["code"] == "ready"
    assert body["projection"]["payload"]["pet_id"] == request.subject.pet_id
    assert len(audits) == 1
    assert audits[0]["action"] == "memory_detail_read"
    assert audits[0]["actor_user_id"] != audits[0]["target_owner_user_id"]
    serialized = json.dumps(audits, ensure_ascii=False)
    assert "voiceprint-owner-secret" not in serialized
    assert "大二" not in serialized


def test_admin_speaker_overview_omits_raw_voiceprint_key(tmp_path):
    async def scenario():
        runtime = _Runtime(tmp_path / "identity.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _admin, token = _register(runtime, "admin", role="admin")
        owner, _ = _register(runtime, "owner")
        _confirmed_subject(runtime, owner)
        response = await handler.handle_admin_speakers(
            _request("GET", "/api/xiaoxin/admin/speakers", token)
        )
        return response

    response = asyncio.run(scenario())
    serialized = response.text

    assert response.status == 200
    assert "voiceprint-owner-secret" not in serialized
    assert "speaker_key" not in serialized


def test_admin_subject_dto_does_not_expose_miniprogram_openid(tmp_path):
    async def scenario():
        runtime = _Runtime(tmp_path / "identity.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _admin, token = _register(runtime, "admin", role="admin")
        owner, _ = runtime.identity_store.get_or_create_student_by_openid(
            "wx-sensitive-openid",
            "Target Owner",
        )
        _confirmed_subject(runtime, owner)
        return await handler.handle_admin_memory_subjects(
            _request("GET", "/api/xiaoxin/admin/memory-subjects", token)
        )

    response = asyncio.run(scenario())

    assert response.status == 200
    assert "wx-sensitive-openid" not in response.text


def test_admin_memory_control_requires_csrf_before_target_access(tmp_path):
    async def scenario():
        runtime = _Runtime(tmp_path / "identity.db")
        control = _ControlSpy()
        runtime.companion_mind = control
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _admin, token = _register(runtime, "admin", role="admin")
        owner, _ = _register(runtime, "owner")
        subject = _confirmed_subject(runtime, owner)
        request = _request(
            "POST",
            f"/api/xiaoxin/admin/memory-subjects/{subject.id}/control",
            token,
            match_info={"subject_id": subject.id},
        )
        request._read_bytes = json.dumps(
            {
                "action": "forget_theme",
                "idempotency_key": "admin-control-1",
                "payload": {"theme": "考试"},
            }
        ).encode()
        response = await handler.handle_admin_memory_control(request)
        return response, control

    response, control = asyncio.run(scenario())

    assert response.status == 403
    assert json.loads(response.text)["code"] == "csrf_invalid"
    assert control.commands == []


def test_regular_user_cannot_call_admin_write_apis_with_valid_csrf(tmp_path):
    async def scenario():
        runtime = _Runtime(tmp_path / "identity.db")
        control = _ControlSpy()
        runtime.companion_mind = control
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token = _register(runtime, "regular-user")
        source = _confirmed_subject(runtime, user, device_id="regular-source")
        target = _confirmed_subject(runtime, user, device_id="regular-target")
        control_response = await handler.handle_admin_memory_control(
            _admin_write_request(
                "POST",
                f"/api/xiaoxin/admin/memory-subjects/{source.id}/control",
                token,
                match_info={"subject_id": source.id},
                payload={
                    "action": "forget_theme",
                    "idempotency_key": "regular-control",
                    "payload": {"theme": "private-theme"},
                },
            )
        )
        merge_response = await handler.handle_admin_merge_memory_subject(
            _admin_write_request(
                "POST",
                f"/api/xiaoxin/admin/memory-subjects/{source.id}/merge",
                token,
                match_info={"subject_id": source.id},
                payload={
                    "to_subject_id": target.id,
                    "idempotency_key": "regular-merge",
                },
            )
        )
        return control_response, merge_response, control, runtime

    control_response, merge_response, control, runtime = asyncio.run(scenario())

    assert [control_response.status, merge_response.status] == [403, 403]
    assert json.loads(control_response.text)["code"] == "admin_required"
    assert json.loads(merge_response.text)["code"] == "admin_required"
    assert control.commands == []
    assert runtime.identity_store.list_admin_audits() == []


def test_admin_bearer_write_accepts_derived_csrf_header(tmp_path):
    async def scenario():
        runtime = _Runtime(tmp_path / "identity.db")
        control = _ControlSpy()
        runtime.companion_mind = control
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        admin, token = _register(runtime, "admin", role="admin")
        owner, _ = _register(runtime, "owner")
        subject = _confirmed_subject(runtime, owner)
        response = await handler.handle_admin_memory_control(
            _admin_write_request(
                "POST",
                f"/api/xiaoxin/admin/memory-subjects/{subject.id}/control",
                token,
                bearer=True,
                match_info={"subject_id": subject.id},
                payload={
                    "action": "forget_theme",
                    "idempotency_key": "admin-bearer-control",
                    "payload": {"theme": "private-theme"},
                },
            )
        )
        audits = runtime.identity_store.list_admin_audits(actor_user_id=admin.id)
        return response, control, audits

    response, control, audits = asyncio.run(scenario())

    assert response.status == 200
    assert len(control.commands) == 1
    assert audits[0]["result_status"] == "success"


def test_admin_memory_control_uses_target_owner_and_records_write_audit(tmp_path):
    async def scenario():
        runtime = _Runtime(tmp_path / "identity.db")
        control = _ControlSpy()
        runtime.companion_mind = control
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        admin, token = _register(runtime, "admin", role="admin")
        owner, _ = runtime.identity_store.get_or_create_student_by_openid(
            "wx-control-owner",
            "Control Owner",
        )
        subject = _confirmed_subject(runtime, owner)
        response = await handler.handle_admin_memory_control(
            _admin_write_request(
                "POST",
                f"/api/xiaoxin/admin/memory-subjects/{subject.id}/control",
                token,
                match_info={"subject_id": subject.id},
                payload={
                    "action": "forget_theme",
                    "idempotency_key": "admin-control-2",
                    "payload": {
                        "theme": "private-theme",
                        "replacement_content": "private-replacement",
                        "raw_conversation": "private-conversation",
                        "speaker_key": "voiceprint-owner-secret",
                        "openid": "wx-control-owner",
                    },
                },
            )
        )
        audits = runtime.identity_store.list_admin_audits(actor_user_id=admin.id)
        return response, control, owner, subject, audits

    response, control, owner, subject, audits = asyncio.run(scenario())
    body = json.loads(response.text)

    assert response.status == 200
    assert control.commands[0].subject.owner_user_id == owner.id
    assert control.commands[0].subject.memory_subject_id == subject.id
    assert control.commands[0].subject.pet_id.startswith("pet_")
    assert body["audit_id"].startswith("aud_")
    assert audits[0]["action"] == "memory_control:forget_theme"
    assert audits[0]["target_owner_user_id"] == owner.id
    assert audits[0]["target_subject_id"] == subject.id
    assert audits[0]["idempotency_key"] == "admin-control-2"
    serialized = json.dumps(audits, ensure_ascii=False)
    assert "private-theme" not in serialized
    assert "private-replacement" not in serialized
    assert "private-conversation" not in serialized
    assert "voiceprint-owner-secret" not in serialized
    assert "wx-control-owner" not in serialized


def test_admin_destructive_control_requires_server_confirmation_phrase(tmp_path):
    async def scenario():
        runtime = _Runtime(tmp_path / "identity.db")
        control = _ControlSpy()
        runtime.companion_mind = control
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        admin, token = _register(runtime, "admin", role="admin")
        owner, _ = _register(runtime, "owner")
        subject = _confirmed_subject(runtime, owner)
        response = await handler.handle_admin_memory_control(
            _admin_write_request(
                "POST",
                f"/api/xiaoxin/admin/memory-subjects/{subject.id}/control",
                token,
                match_info={"subject_id": subject.id},
                payload={
                    "action": "reset_relationship",
                    "idempotency_key": "admin-reset-1",
                    "confirmation": "wrong",
                    "payload": {},
                },
            )
        )
        audits = runtime.identity_store.list_admin_audits(actor_user_id=admin.id)
        return response, control, audits

    response, control, audits = asyncio.run(scenario())

    assert response.status == 400
    assert json.loads(response.text)["code"] == "confirmation_required"
    assert control.commands == []
    assert audits[0]["failure_code"] == "confirmation_required"


def test_admin_merge_rejects_cross_owner_and_audits_failure(tmp_path):
    async def scenario():
        runtime = _Runtime(tmp_path / "identity.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        admin, token = _register(runtime, "admin", role="admin")
        owner_a, _ = _register(runtime, "owner-a")
        owner_b, _ = _register(runtime, "owner-b")
        prior_source = _confirmed_subject(runtime, owner_a, device_id="device-a-source")
        target = _confirmed_subject(runtime, owner_a, device_id="device-a-target")
        source = _confirmed_subject(runtime, owner_b, device_id="device-b-source")
        await handler.handle_admin_merge_memory_subject(
            _admin_write_request(
                "POST",
                f"/api/xiaoxin/admin/memory-subjects/{prior_source.id}/merge",
                token,
                match_info={"subject_id": prior_source.id},
                payload={
                    "to_subject_id": target.id,
                    "idempotency_key": "admin-merge-shared-key",
                },
            )
        )
        response = await handler.handle_admin_merge_memory_subject(
            _admin_write_request(
                "POST",
                f"/api/xiaoxin/admin/memory-subjects/{source.id}/merge",
                token,
                match_info={"subject_id": source.id},
                payload={
                    "to_subject_id": target.id,
                    "idempotency_key": "admin-merge-shared-key",
                },
            )
        )
        audits = runtime.identity_store.list_admin_audits(actor_user_id=admin.id)
        return response, source, audits, runtime

    response, source, audits, runtime = asyncio.run(scenario())

    assert response.status == 403
    assert json.loads(response.text)["code"] == "cross_owner_merge_forbidden"
    assert runtime.identity_store.resolve_subject_alias(source.id) == source.id
    assert audits[0]["failure_code"] == "cross_owner_merge_forbidden"


def test_admin_merge_audits_invalid_json_and_missing_fields(tmp_path):
    async def scenario():
        runtime = _Runtime(tmp_path / "identity.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        admin, token = _register(runtime, "admin", role="admin")
        owner, _ = _register(runtime, "owner")
        source = _confirmed_subject(runtime, owner)

        invalid_json = _admin_write_request(
            "POST",
            f"/api/xiaoxin/admin/memory-subjects/{source.id}/merge",
            token,
            match_info={"subject_id": source.id},
        )
        invalid_json._read_bytes = b"{"
        invalid_response = await handler.handle_admin_merge_memory_subject(invalid_json)
        missing_response = await handler.handle_admin_merge_memory_subject(
            _admin_write_request(
                "POST",
                f"/api/xiaoxin/admin/memory-subjects/{source.id}/merge",
                token,
                match_info={"subject_id": source.id},
                payload={"private_field": "must-not-be-audited"},
            )
        )
        audits = runtime.identity_store.list_admin_audits(actor_user_id=admin.id)
        return invalid_response, missing_response, source, audits

    invalid_response, missing_response, source, audits = asyncio.run(scenario())

    assert [invalid_response.status, missing_response.status] == [400, 400]
    assert {audit["failure_code"] for audit in audits} == {
        "invalid_json",
        "merge_fields_required",
    }
    assert all(audit["target_subject_id"] == source.id for audit in audits)
    assert "must-not-be-audited" not in json.dumps(audits, ensure_ascii=False)


def test_admin_merge_audits_idempotency_conflict_and_merged_source(tmp_path):
    async def scenario():
        runtime = _Runtime(tmp_path / "identity.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        admin, token = _register(runtime, "admin", role="admin")
        owner, _ = _register(runtime, "owner")
        source = _confirmed_subject(runtime, owner, device_id="source")
        target_a = _confirmed_subject(runtime, owner, device_id="target-a")
        target_b = _confirmed_subject(runtime, owner, device_id="target-b")
        other_source = _confirmed_subject(runtime, owner, device_id="other-source")

        applied = await handler.handle_admin_merge_memory_subject(
            _admin_write_request(
                "POST",
                f"/api/xiaoxin/admin/memory-subjects/{source.id}/merge",
                token,
                match_info={"subject_id": source.id},
                payload={
                    "to_subject_id": target_a.id,
                    "idempotency_key": "merge-shared-key",
                },
            )
        )
        conflict = await handler.handle_admin_merge_memory_subject(
            _admin_write_request(
                "POST",
                f"/api/xiaoxin/admin/memory-subjects/{other_source.id}/merge",
                token,
                match_info={"subject_id": other_source.id},
                payload={
                    "to_subject_id": target_b.id,
                    "idempotency_key": "merge-shared-key",
                },
            )
        )
        replay = await handler.handle_admin_merge_memory_subject(
            _admin_write_request(
                "POST",
                f"/api/xiaoxin/admin/memory-subjects/{source.id}/merge",
                token,
                match_info={"subject_id": source.id},
                payload={
                    "to_subject_id": target_a.id,
                    "idempotency_key": "merge-shared-key",
                },
            )
        )
        already_merged = await handler.handle_admin_merge_memory_subject(
            _admin_write_request(
                "POST",
                f"/api/xiaoxin/admin/memory-subjects/{source.id}/merge",
                token,
                match_info={"subject_id": source.id},
                payload={
                    "to_subject_id": target_b.id,
                    "idempotency_key": "merge-new-key",
                },
            )
        )
        audits = runtime.identity_store.list_admin_audits(actor_user_id=admin.id)
        return applied, conflict, replay, already_merged, audits

    applied, conflict, replay, already_merged, audits = asyncio.run(scenario())

    assert [applied.status, conflict.status, replay.status, already_merged.status] == [
        200,
        409,
        200,
        409,
    ]
    assert json.loads(conflict.text)["code"] == "idempotency_conflict"
    assert json.loads(replay.text)["status"] == "already_applied"
    assert json.loads(already_merged.text)["code"] == "subject_merged"
    failure_codes = {audit["failure_code"] for audit in audits}
    assert "idempotency_conflict" in failure_codes
    assert "subject_merged" in failure_codes


def test_admin_same_owner_merge_is_idempotent_and_audited(tmp_path):
    async def scenario():
        runtime = _Runtime(tmp_path / "identity.db")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        admin, token = _register(runtime, "admin", role="admin")
        owner, _ = _register(runtime, "owner")
        source = _confirmed_subject(runtime, owner, device_id="device-source")
        target = _confirmed_subject(runtime, owner, device_id="device-target")
        payload = {
            "to_subject_id": target.id,
            "idempotency_key": "admin-merge-same-owner",
        }
        responses = []
        for _ in range(2):
            responses.append(
                await handler.handle_admin_merge_memory_subject(
                    _admin_write_request(
                        "POST",
                        f"/api/xiaoxin/admin/memory-subjects/{source.id}/merge",
                        token,
                        match_info={"subject_id": source.id},
                        payload=payload,
                    )
                )
            )
        audits = runtime.identity_store.list_admin_audits(actor_user_id=admin.id)
        return responses, source, target, audits, runtime

    responses, source, target, audits, runtime = asyncio.run(scenario())

    assert [response.status for response in responses] == [200, 200]
    assert json.loads(responses[1].text)["status"] == "already_applied"
    assert runtime.identity_store.resolve_subject_alias(source.id) == target.id
    assert audits[0]["action"] == "memory_subject_merge"
    assert audits[0]["idempotency_key"] == "admin-merge-same-owner"
    assert len(audits) == 2
    assert all(audit["result_status"] == "success" for audit in audits)
