import asyncio
import json

from aiohttp.test_utils import make_mocked_request

from core.api.xiaoxin_control_handler import XiaoxinControlHandler
from core.xiaoxin.companion import (
    CompanionMind,
    CompanionProjectionRequest,
    CompanionTurnOutcome,
    CompanionTurnRequest,
    build_companion_subject_context,
)
from core.xiaoxin.companion.store import CompanionStore
from core.xiaoxin.companion.observation_ingress import CompanionObservationIngress
from core.xiaoxin.companion.reflection import ReflectionProposal
from core.xiaoxin.identity.auth import XiaoxinAuthService
from core.xiaoxin.identity.store import XiaoxinIdentityStore


class _ControlApiRuntime:
    def __init__(self, tmp_path):
        self.identity_store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        self.auth_service = XiaoxinAuthService(self.identity_store)
        self.companion_mind = CompanionMind(
            store=CompanionStore(tmp_path / "xiaoxin_companion.db")
        )


def _request(path, token, subject_id):
    return make_mocked_request(
        "GET",
        path,
        headers={"Cookie": f"xiaoxin_session={token}"},
        match_info={"subject_id": subject_id},
    )


def _control_request(path, token, subject_id, payload):
    request = make_mocked_request(
        "POST",
        path,
        headers={
            "Cookie": f"xiaoxin_session={token}",
            "Content-Type": "application/json",
        },
        match_info={"subject_id": subject_id},
    )
    request._read_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return request


def _miniprogram_request(path, token, payload=None):
    method = "POST" if payload is not None else "GET"
    request = make_mocked_request(
        method,
        path,
        headers={
            "Cookie": f"xiaoxin_session={token}",
            "Content-Type": "application/json",
        },
    )
    if payload is not None:
        request._read_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return request


def _create_owner(runtime, *, openid, nickname, device_id, grade):
    user, _ = runtime.identity_store.get_or_create_student_by_openid(openid, nickname)
    runtime.identity_store.update_student_profile(user.id, {"grade": grade})
    token = runtime.auth_service.create_session_for_user(user.id)
    runtime.identity_store.upsert_seen_device(device_id, nickname)
    runtime.identity_store.bind_device(device_id, user.id, nickname)
    speaker = runtime.identity_store.get_or_create_speaker_profile(
        user.id,
        device_id,
        f"speaker-{device_id}",
        nickname,
    )
    subject = runtime.identity_store.get_or_create_memory_subject(
        user.id,
        device_id,
        speaker.id,
        "user_speaker",
        nickname,
    )
    return user, token, subject


def _prepare_relationship(runtime, user, subject, *, turn_id):
    pet = runtime.identity_store.get_personal_pet_for_user(user.id)
    profile = runtime.identity_store.get_student_profile_for_user(user.id)
    context = build_companion_subject_context(
        owner_user_id=user.id,
        pet_id=pet.id,
        memory_subject_id=subject.id,
        subject_kind=subject.kind,
        raw_grade=profile["grade"],
    )
    prepared = runtime.companion_mind.prepare_turn(
        CompanionTurnRequest(
            turn_id=turn_id,
            subject=context,
            request_digest=f"digest-{turn_id}",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    runtime.companion_mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="已准备好。",
            assistant_action="reply",
            delivery_status="delivered",
        ),
    )


def test_owner_can_project_own_companion_memory_but_not_another_owner(tmp_path):
    async def scenario():
        runtime = _ControlApiRuntime(tmp_path)
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _, token_a, subject_a = _create_owner(
            runtime,
            openid="wx-owner-a",
            nickname="A",
            device_id="device-a",
            grade="大二",
        )
        _, _, subject_b = _create_owner(
            runtime,
            openid="wx-owner-b",
            nickname="B",
            device_id="device-b",
            grade="大一",
        )

        own_response = await handler.handle_memory_subject_detail(
            _request(
                f"/api/xiaoxin/memory-subjects/{subject_a.id}/memory",
                token_a,
                subject_a.id,
            )
        )
        other_response = await handler.handle_memory_subject_detail(
            _request(
                f"/api/xiaoxin/memory-subjects/{subject_b.id}/memory",
                token_a,
                subject_b.id,
            )
        )
        return own_response, other_response

    own_response, other_response = asyncio.run(scenario())
    own_body = json.loads(own_response.text)

    assert own_response.status == 200
    assert own_body["surface"] == "operator"
    assert own_body["xiaoxin_age"] == 2
    assert own_body["relationship_stage"] == "first_meeting"
    assert own_body["payload"]["memory_subject_id"]
    assert other_response.status == 404


def test_miniprogram_companion_control_maps_user_action_and_updates_settings(tmp_path):
    async def scenario():
        runtime = _ControlApiRuntime(tmp_path)
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token, _ = _create_owner(
            runtime,
            openid="wx-miniprogram-controls",
            nickname="用户控制",
            device_id="device-miniprogram-controls",
            grade="大一",
        )
        subject = runtime.identity_store.list_memory_subjects_for_user(user.id)[0]
        _prepare_relationship(runtime, user, subject, turn_id="turn-miniprogram-controls")
        settings_before = await handler.handle_miniprogram_companion_settings(
            _miniprogram_request("/api/miniprogram/companion/settings", token)
        )
        control = await handler.handle_miniprogram_companion_control(
            _miniprogram_request(
                "/api/miniprogram/companion/control",
                token,
                {"action": "do_not_mention", "idempotencyKey": "mini-control-1"},
            )
        )
        settings_after = await handler.handle_miniprogram_companion_settings(
            _miniprogram_request("/api/miniprogram/companion/settings", token)
        )
        return settings_before, control, settings_after

    before, control, after = asyncio.run(scenario())
    control_body = json.loads(control.text)
    after_body = json.loads(after.text)
    assert before.status == 200
    assert control.status == 200
    assert control_body["mapped_action"] == "set_boundary"
    assert control_body["result"]["status"] == "applied"
    assert after.status == 200
    assert after_body["settings"]["explicit_settings"]


def test_miniprogram_can_save_and_read_initiative_quiet_hours(tmp_path):
    async def scenario():
        runtime = _ControlApiRuntime(tmp_path)
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token, subject = _create_owner(
            runtime,
            openid="wx-miniprogram-quiet-hours",
            nickname="安静时段用户",
            device_id="device-miniprogram-quiet-hours",
            grade="大一",
        )
        _prepare_relationship(runtime, user, subject, turn_id="turn-miniprogram-quiet-hours")
        control_path = f"/api/xiaoxin/memory-subjects/{subject.id}/memory/control"
        control_response = await handler.handle_companion_memory_control(
            _control_request(
                control_path,
                token,
                subject.id,
                {
                    "action": "set_initiative_quiet_hours",
                    "idempotency_key": "mini-quiet-hours-1",
                    "payload": {
                        "enabled": True,
                        "start": "23:00",
                        "end": "08:00",
                    },
                },
            )
        )
        projection_response = await handler.handle_memory_subject_detail(
            _request(
                f"/api/xiaoxin/memory-subjects/{subject.id}/memory?surface=miniprogram",
                token,
                subject.id,
            )
        )
        return control_response, projection_response

    control_response, projection_response = asyncio.run(scenario())
    control_body = json.loads(control_response.text)
    projection_body = json.loads(projection_response.text)
    quiet_hours = projection_body["payload"]["companion_preferences"]["quiet_hours"]

    assert control_response.status == 200
    assert control_body["action"] == "set_initiative_quiet_hours"
    assert projection_response.status == 200
    assert quiet_hours["enabled"] is True
    assert (quiet_hours["start"], quiet_hours["end"]) == ("23:00", "08:00")


def test_miniprogram_companion_control_rejects_unconfirmed_subject(tmp_path):
    async def scenario():
        runtime = _ControlApiRuntime(tmp_path)
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token, subject = _create_owner(
            runtime,
            openid="wx-miniprogram-unconfirmed",
            nickname="未确认用户",
            device_id="device-miniprogram-unconfirmed",
            grade="大一",
        )
        runtime.identity_store.archive_speaker(subject.speaker_profile_id, user.id)
        return await handler.handle_miniprogram_companion_control(
            _miniprogram_request(
                "/api/miniprogram/companion/control",
                token,
                {"action": "too_personal"},
            )
        )

    response = asyncio.run(scenario())
    body = json.loads(response.text)
    assert response.status == 409
    assert body["code"] == "confirmed_subject_required"


def test_miniprogram_companion_control_rejects_ambiguous_multiple_subjects(tmp_path):
    async def scenario():
        runtime = _ControlApiRuntime(tmp_path)
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token, _ = _create_owner(
            runtime,
            openid="wx-miniprogram-ambiguous",
            nickname="多设备用户",
            device_id="device-miniprogram-ambiguous-a",
            grade="大一",
        )
        _create_owner(
            runtime,
            openid="wx-miniprogram-ambiguous",
            nickname="多设备用户",
            device_id="device-miniprogram-ambiguous-b",
            grade="大一",
        )
        return await handler.handle_miniprogram_companion_settings(
            _miniprogram_request("/api/miniprogram/companion/settings", token)
        )

    response = asyncio.run(scenario())
    body = json.loads(response.text)
    assert response.status == 409
    assert body["code"] == "subject_selection_required"


def test_typed_control_endpoint_distinguishes_relationship_reset_from_purge(tmp_path):
    async def scenario():
        runtime = _ControlApiRuntime(tmp_path)
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token, subject = _create_owner(
            runtime,
            openid="wx-control-owner",
            nickname="控制用户",
            device_id="device-control",
            grade="大二",
        )
        _prepare_relationship(runtime, user, subject, turn_id="turn-control")
        path = f"/api/xiaoxin/memory-subjects/{subject.id}/memory/control"

        reset_response = await handler.handle_companion_memory_control(
            _control_request(
                path,
                token,
                subject.id,
                {
                    "action": "reset_relationship",
                    "idempotency_key": "api-reset-1",
                    "payload": {"confirmed_consequences": True},
                },
            )
        )
        purge_response = await handler.handle_companion_memory_control(
            _control_request(
                path,
                token,
                subject.id,
                {
                    "action": "purge_personal_memory",
                    "idempotency_key": "api-purge-1",
                    "payload": {"confirmation_phrase": "清空个人记忆"},
                },
            )
        )
        return reset_response, purge_response

    reset_response, purge_response = asyncio.run(scenario())
    reset_body = json.loads(reset_response.text)
    purge_body = json.loads(purge_response.text)

    assert reset_response.status == 200
    assert reset_body["action"] == "reset_relationship"
    assert "保留" in reset_body["message"]
    assert "旧关系记忆已停用" in reset_body["message"]
    assert purge_response.status == 200
    assert purge_body["action"] == "purge_personal_memory"
    assert "陪伴记忆已清除" in purge_body["message"]
    assert "账号、设备绑定" in purge_body["message"]


def test_control_endpoint_replays_same_idempotency_key_across_server_times(tmp_path):
    async def scenario():
        runtime = _ControlApiRuntime(tmp_path)
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token, subject = _create_owner(
            runtime,
            openid="wx-control-replay",
            nickname="重放用户",
            device_id="device-control-replay",
            grade="大二",
        )
        _prepare_relationship(runtime, user, subject, turn_id="turn-control-replay")
        path = f"/api/xiaoxin/memory-subjects/{subject.id}/memory/control"
        body = {
            "action": "reset_relationship",
            "idempotency_key": "api-reset-replay-1",
            "payload": {"confirmed_consequences": True},
        }

        first = await handler.handle_companion_memory_control(
            _control_request(path, token, subject.id, body)
        )
        first_projection = await handler.handle_memory_subject_detail(
            _request(
                f"/api/xiaoxin/memory-subjects/{subject.id}/memory",
                token,
                subject.id,
            )
        )
        second = await handler.handle_companion_memory_control(
            _control_request(path, token, subject.id, body)
        )
        second_projection = await handler.handle_memory_subject_detail(
            _request(
                f"/api/xiaoxin/memory-subjects/{subject.id}/memory",
                token,
                subject.id,
            )
        )
        return first, first_projection, second, second_projection

    first, first_projection, second, second_projection = asyncio.run(scenario())
    first_body = json.loads(first.text)
    second_body = json.loads(second.text)
    first_projection_body = json.loads(first_projection.text)
    second_projection_body = json.loads(second_projection.text)

    assert first.status == second.status == 200
    assert first_body == second_body
    assert (
        first_projection_body["payload"]["relationship_epoch_id"]
        == second_projection_body["payload"]["relationship_epoch_id"]
    )


def test_control_endpoint_returns_conflict_for_reused_key_with_changed_command(tmp_path):
    async def scenario():
        runtime = _ControlApiRuntime(tmp_path)
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token, subject = _create_owner(
            runtime,
            openid="wx-control-conflict",
            nickname="冲突用户",
            device_id="device-control-conflict",
            grade="大二",
        )
        _prepare_relationship(runtime, user, subject, turn_id="turn-control-conflict")
        path = f"/api/xiaoxin/memory-subjects/{subject.id}/memory/control"

        first = await handler.handle_companion_memory_control(
            _control_request(
                path,
                token,
                subject.id,
                {
                    "action": "set_boundary",
                    "idempotency_key": "api-boundary-conflict-1",
                    "payload": {
                        "boundary_key": "question_frequency",
                        "value": "never",
                        "source_summary": "用户明确要求不要追问。",
                    },
                },
            )
        )
        second = await handler.handle_companion_memory_control(
            _control_request(
                path,
                token,
                subject.id,
                {
                    "action": "set_boundary",
                    "idempotency_key": "api-boundary-conflict-1",
                    "payload": {
                        "boundary_key": "question_frequency",
                        "value": "less",
                        "source_summary": "用户改为允许少量追问。",
                    },
                },
            )
        )
        return first, second

    first, second = asyncio.run(scenario())
    second_body = json.loads(second.text)

    assert first.status == 200
    assert second.status == 409
    assert second_body == {
        "success": False,
        "message": "idempotency key reused for a different command",
        "field": "idempotency_key",
    }


def test_profile_grade_update_schedules_academic_stage_boundary_through_mind(tmp_path):
    async def scenario():
        runtime = _ControlApiRuntime(tmp_path)
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token, subject = _create_owner(
            runtime,
            openid="wx-profile-stage",
            nickname="阶段用户",
            device_id="device-profile-stage",
            grade="大二",
        )
        _prepare_relationship(runtime, user, subject, turn_id="turn-profile-stage")
        detail_path = f"/api/xiaoxin/memory-subjects/{subject.id}/memory"
        before = await handler.handle_memory_subject_detail(
            _request(detail_path, token, subject.id)
        )

        updated = await handler.handle_miniprogram_profile_update(
            _control_request(
                "/api/miniprogram/profile",
                token,
                subject.id,
                {"grade": "大三"},
            )
        )
        after = await handler.handle_memory_subject_detail(
            _request(detail_path, token, subject.id)
        )
        return before, updated, after

    before, updated, after = asyncio.run(scenario())
    before_body = json.loads(before.text)
    updated_body = json.loads(updated.text)
    after_body = json.loads(after.text)

    assert updated.status == 200
    assert updated_body["profile"]["grade"] == "大三"
    assert before_body["xiaoxin_age"] == 2
    assert after_body["xiaoxin_age"] == 3
    assert (
        before_body["payload"]["relationship_epoch_id"]
        == after_body["payload"]["relationship_epoch_id"]
    )
    assert any(
        job["job_kind"] == "academic_stage_changed"
        and job["status"] == "pending"
        for job in after_body["payload"]["jobs"]
    )


def test_miniprogram_projection_is_safe_and_contains_growth_summary_fields(tmp_path):
    async def scenario():
        runtime = _ControlApiRuntime(tmp_path)
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token, subject = _create_owner(
            runtime,
            openid="wx-miniprogram-owner",
            nickname="小程序用户",
            device_id="device-miniprogram",
            grade="大二",
        )
        _prepare_relationship(runtime, user, subject, turn_id="turn-miniprogram")
        control_path = f"/api/xiaoxin/memory-subjects/{subject.id}/memory/control"
        await handler.handle_companion_memory_control(
            _control_request(
                control_path,
                token,
                subject.id,
                {
                    "action": "set_boundary",
                    "idempotency_key": "api-boundary-1",
                    "payload": {
                        "boundary_key": "question_frequency",
                        "value": "none",
                        "source_summary": "用户明确要求少追问。",
                    },
                },
            )
        )

        response = await handler.handle_memory_subject_detail(
            _request(
                f"/api/xiaoxin/memory-subjects/{subject.id}/memory?surface=miniprogram",
                token,
                subject.id,
            )
        )
        return response

    response = asyncio.run(scenario())
    body = json.loads(response.text)
    serialized = json.dumps(body, ensure_ascii=False).lower()

    assert response.status == 200
    assert body["surface"] == "miniprogram"
    assert body["xiaoxin_age"] == 2
    assert body["relationship_stage"] == "重新认识中"
    assert body["payload"]["explicit_settings"][0]["label"] == "用户明确要求少追问。"
    assert "learned_behaviors" in body["payload"]
    assert "growth_moments_enabled" in body["payload"]
    assert "policy" not in body["payload"]
    for private_name in (
        "expressiveness",
        "initiative",
        "warmth",
        "humor",
        "closure",
        "seed",
        "generator_version",
        "confidence",
        "candidate",
        "trial",
        "valence",
        "arousal",
    ):
        assert private_name not in serialized
    assert "chain-of-thought" not in serialized
    assert "prompt" not in serialized
    assert "content_json" not in serialized


def test_typed_control_endpoint_executes_evidence_and_boundary_controls(tmp_path):
    async def scenario():
        runtime = _ControlApiRuntime(tmp_path)
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token, subject = _create_owner(
            runtime,
            openid="wx-evidence-owner",
            nickname="依据用户",
            device_id="device-evidence",
            grade="大三",
        )
        _prepare_relationship(runtime, user, subject, turn_id="turn-evidence")
        detail_path = f"/api/xiaoxin/memory-subjects/{subject.id}/memory"
        control_path = f"{detail_path}/control"

        async def control(action, key, payload):
            response = await handler.handle_companion_memory_control(
                _control_request(
                    control_path,
                    token,
                    subject.id,
                    {
                        "action": action,
                        "idempotency_key": key,
                        "payload": payload,
                    },
                )
            )
            return response.status, json.loads(response.text)

        async def projection():
            response = await handler.handle_memory_subject_detail(
                _request(detail_path, token, subject.id)
            )
            return json.loads(response.text)

        def boundary_evidence_id(projected):
            return next(
                item["evidence_id"]
                for item in projected["payload"]["evidence"]
                if item["kind"] == "explicit_boundary"
            )

        set_status, set_body = await control(
            "set_boundary",
            "api-set-boundary",
            {
                "boundary_key": "question_frequency",
                "value": "none",
                "source_summary": "用户明确要求不要追问。",
            },
        )
        first_projection = await projection()
        first_evidence_id = boundary_evidence_id(first_projection)
        correct_status, correct_body = await control(
            "correct_evidence",
            "api-correct-boundary",
            {
                "evidence_id": first_evidence_id,
                "replacement_content": {
                    "boundary_key": "question_frequency",
                    "value": "low",
                },
                "source_summary": "用户纠正为可以少量追问。",
            },
        )
        corrected_projection = await projection()
        corrected_evidence_id = boundary_evidence_id(corrected_projection)
        forget_status, forget_body = await control(
            "forget_evidence",
            "api-forget-boundary",
            {"evidence_id": corrected_evidence_id},
        )
        second_set_status, _ = await control(
            "set_boundary",
            "api-set-boundary-2",
            {
                "boundary_key": "closure_style",
                "value": "brief",
                "source_summary": "用户明确要求简短收尾。",
            },
        )
        second_projection = await projection()
        second_evidence_id = boundary_evidence_id(second_projection)
        revoke_status, revoke_body = await control(
            "revoke_boundary",
            "api-revoke-boundary",
            {"evidence_id": second_evidence_id},
        )
        return {
            "statuses": (
                set_status,
                correct_status,
                forget_status,
                second_set_status,
                revoke_status,
            ),
            "actions": (
                set_body["action"],
                correct_body["action"],
                forget_body["action"],
                revoke_body["action"],
            ),
            "first_projection": first_projection,
            "first_evidence_id": first_evidence_id,
            "corrected_evidence_id": corrected_evidence_id,
        }

    result = asyncio.run(scenario())

    assert result["statuses"] == (200, 200, 200, 200, 200)
    assert result["actions"] == (
        "set_boundary",
        "correct_evidence",
        "forget_evidence",
        "revoke_boundary",
    )
    assert result["first_evidence_id"] != result["corrected_evidence_id"]
    projection = result["first_projection"]
    assert projection["payload"]["relationship_epoch_id"]
    assert all(item["status"] == "active" for item in projection["payload"]["evidence"])
    assert "active_adjustments" in projection["payload"]
    assert "chapters" in projection["payload"]


def test_unknown_subject_is_private_memory_neutral_and_merged_subject_is_rejected(
    tmp_path,
):
    async def scenario():
        runtime = _ControlApiRuntime(tmp_path)
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token, confirmed_subject = _create_owner(
            runtime,
            openid="wx-unknown-owner",
            nickname="未知入口用户",
            device_id="device-confirmed",
            grade="大二",
        )
        _prepare_relationship(runtime, user, confirmed_subject, turn_id="turn-unknown")
        confirmed_control_path = (
            f"/api/xiaoxin/memory-subjects/{confirmed_subject.id}/memory/control"
        )
        await handler.handle_companion_memory_control(
            _control_request(
                confirmed_control_path,
                token,
                confirmed_subject.id,
                {
                    "action": "set_boundary",
                    "idempotency_key": "unknown-private-boundary",
                    "payload": {
                        "boundary_key": "question_frequency",
                        "value": "none",
                        "source_summary": "只属于已确认主人的私人边界。",
                    },
                },
            )
        )

        unknown_subjects = []
        for suffix in ("source", "target"):
            device_id = f"device-unknown-{suffix}"
            runtime.identity_store.upsert_seen_device(device_id, device_id)
            runtime.identity_store.bind_device(device_id, user.id, device_id)
            unknown_subjects.append(
                runtime.identity_store.get_or_create_memory_subject(
                    user.id,
                    device_id,
                    None,
                    "device_unknown",
                    "未知说话人",
                )
            )
        source, target = unknown_subjects
        runtime.identity_store.create_subject_alias(source.id, target.id, "manual_merge")

        target_path = f"/api/xiaoxin/memory-subjects/{target.id}/memory"
        neutral_response = await handler.handle_memory_subject_detail(
            _request(target_path, token, target.id)
        )
        control_response = await handler.handle_companion_memory_control(
            _control_request(
                f"{target_path}/control",
                token,
                target.id,
                {
                    "action": "purge_personal_memory",
                    "idempotency_key": "unknown-purge",
                    "payload": {"confirmation_phrase": "清空个人记忆"},
                },
            )
        )
        merged_response = await handler.handle_memory_subject_detail(
            _request(
                f"/api/xiaoxin/memory-subjects/{source.id}/memory",
                token,
                source.id,
            )
        )
        return neutral_response, control_response, merged_response

    neutral_response, control_response, merged_response = asyncio.run(scenario())
    neutral_body = json.loads(neutral_response.text)
    serialized = json.dumps(neutral_body, ensure_ascii=False)

    assert neutral_response.status == 200
    assert "evidence" not in neutral_body["payload"]
    assert "只属于已确认主人的私人边界" not in serialized
    assert control_response.status == 403
    assert merged_response.status == 404


def test_projection_fails_closed_when_mind_or_personal_pet_is_unavailable(tmp_path):
    async def scenario():
        runtime = _ControlApiRuntime(tmp_path / "with-mind")
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        _, token, subject = _create_owner(
            runtime,
            openid="wx-no-mind",
            nickname="无 Mind 用户",
            device_id="device-no-mind",
            grade="大一",
        )
        runtime.companion_mind = None
        no_mind = await handler.handle_memory_subject_detail(
            _request(
                f"/api/xiaoxin/memory-subjects/{subject.id}/memory",
                token,
                subject.id,
            )
        )

        no_pet_runtime = _ControlApiRuntime(tmp_path / "without-pet")
        no_pet_handler = XiaoxinControlHandler(
            {"xiaoxin_control": {}}, no_pet_runtime
        )
        user, no_pet_token = no_pet_runtime.auth_service.register(
            "no-pet", "secret-pass", "No Pet"
        )
        no_pet_runtime.identity_store.upsert_seen_device("device-no-pet", "No Pet")
        no_pet_runtime.identity_store.bind_device("device-no-pet", user.id, "No Pet")
        no_pet_subject = no_pet_runtime.identity_store.get_or_create_memory_subject(
            user.id,
            "device-no-pet",
            None,
            "device_unknown",
            "未知说话人",
        )
        with no_pet_runtime.identity_store._connect() as conn:
            conn.execute(
                "DELETE FROM personal_pets WHERE owner_user_id = ?", (user.id,)
            )
        no_pet = await no_pet_handler.handle_memory_subject_detail(
            _request(
                f"/api/xiaoxin/memory-subjects/{no_pet_subject.id}/memory",
                no_pet_token,
                no_pet_subject.id,
            )
        )
        return no_mind, no_pet

    no_mind, no_pet = asyncio.run(scenario())

    assert no_mind.status == 503
    assert json.loads(no_mind.text)["message"] == "companion memory unavailable"
    assert no_pet.status == 404
    assert json.loads(no_pet.text)["message"] == "personal pet not found"


def test_authenticated_miniprogram_observation_reaches_real_companion_store(tmp_path):
    async def scenario():
        runtime = _ControlApiRuntime(tmp_path)
        runtime.observation_ingress = CompanionObservationIngress(
            runtime.identity_store,
            runtime.companion_mind,
        )
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token, subject = _create_owner(
            runtime,
            openid="wx-observation-replay",
            nickname="回放用户",
            device_id="device-observation-replay",
            grade="大二",
        )
        request = make_mocked_request(
            "POST",
            "/api/miniprogram/companion/observations",
            headers={
                "Cookie": f"xiaoxin_session={token}",
                "Content-Type": "application/json",
            },
        )
        request._read_bytes = json.dumps(
            {
                "idempotencyKey": "goal-set:goal-1:v1",
                "kind": "goal_set",
                "payload": {
                    "goalId": "goal-1",
                    "title": "通过英语六级",
                    "targetAt": "2026-12-12T09:00:00+08:00",
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        response = await handler.handle_miniprogram_companion_observation(request)
        pet = runtime.identity_store.get_personal_pet_for_user(user.id)
        profile = runtime.identity_store.get_student_profile_for_user(user.id)
        context = build_companion_subject_context(
            owner_user_id=user.id,
            pet_id=pet.id,
            memory_subject_id=subject.id,
            subject_kind=subject.kind,
            raw_grade=profile["grade"],
        )
        projection = runtime.companion_mind.project(
            CompanionProjectionRequest(
                subject=context,
                surface="operator",
                now="2026-07-20T12:00:00+08:00",
            )
        )
        return response, projection

    response, projection = asyncio.run(scenario())

    assert response.status == 200
    assert json.loads(response.text)["observation"]["status"] == "recorded"
    assert projection.payload["diagnostics"]["observations"][0]["kind"] == (
        "goal_set"
    )
    assert projection.payload["diagnostics"]["evidence_timeline"][0]["kind"] == (
        "goal"
    )


def test_authenticated_control_endpoint_confirms_conversation_candidate(tmp_path):
    class CandidateModel:
        def reflect(self, request):
            source = request.turn_sources[0]
            return ReflectionProposal(
                schema_version="companion-reflection-proposal-v1",
                safe_summary="发现候选。",
                proposed_user_facts=(
                    {
                        "fact_key": "preference:quiet_study",
                        "kind": "preference",
                        "value": "喜欢安静学习",
                        "source_turn_id": source.turn_id,
                        "source_quote": "我喜欢安静学习",
                        "claim_type": "explicit_statement",
                        "sensitivity": "private",
                        "confidence": 0.95,
                    },
                ),
            )

    async def scenario():
        runtime = _ControlApiRuntime(tmp_path)
        runtime.companion_mind = CompanionMind(
            store=CompanionStore(tmp_path / "candidate-control.db"),
            reflection_model=CandidateModel(),
        )
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token, subject = _create_owner(
            runtime,
            openid="wx-candidate-control",
            nickname="候选确认用户",
            device_id="device-candidate-control",
            grade="大二",
        )
        pet = runtime.identity_store.get_personal_pet_for_user(user.id)
        profile = runtime.identity_store.get_student_profile_for_user(user.id)
        context = build_companion_subject_context(
            owner_user_id=user.id,
            pet_id=pet.id,
            memory_subject_id=subject.id,
            subject_kind=subject.kind,
            raw_grade=profile["grade"],
        )
        prepared = runtime.companion_mind.prepare_turn(
            CompanionTurnRequest(
                turn_id="turn-api-candidate",
                subject=context,
                request_digest="digest-api-candidate",
                surface="voice",
                occurred_at="2026-07-21T20:00:00+08:00",
                source_text="我喜欢安静学习。",
            )
        )
        runtime.companion_mind.commit_turn(
            prepared,
            CompanionTurnOutcome(
                visible_response="收到。",
                assistant_action="reply",
                delivery_status="generated",
            ),
        )
        await runtime.companion_mind.run_due_work(
            now="2026-07-21T20:01:00+08:00"
        )
        before = runtime.companion_mind.project(
            CompanionProjectionRequest(
                subject=context,
                surface="operator",
                now="2026-07-21T20:01:01+08:00",
            )
        )
        candidate = next(
            item
            for item in before.payload["diagnostics"]["evidence_timeline"]
            if item["source_kind"] == "conversation_candidate"
        )
        path = f"/api/xiaoxin/memory-subjects/{subject.id}/memory/control"
        response = await handler.handle_companion_memory_control(
            _control_request(
                path,
                token,
                subject.id,
                {
                    "action": "confirm_candidate",
                    "idempotency_key": "api-confirm-candidate",
                    "payload": {"evidence_id": candidate["evidence_id"]},
                },
            )
        )
        after = runtime.companion_mind.project(
            CompanionProjectionRequest(
                subject=context,
                surface="operator",
                now="2026-07-21T20:02:01+08:00",
            )
        )
        confirmed = next(
            item
            for item in after.payload["diagnostics"]["evidence_timeline"]
            if item["evidence_id"] == candidate["evidence_id"]
        )
        return response, confirmed

    response, confirmed = asyncio.run(scenario())

    assert response.status == 200
    assert json.loads(response.text)["action"] == "confirm_candidate"
    assert confirmed["status"] == "active"


def test_control_surface_and_high_risk_confirmation_gates_do_not_execute_early(
    tmp_path,
):
    async def scenario():
        runtime = _ControlApiRuntime(tmp_path)
        handler = XiaoxinControlHandler({"xiaoxin_control": {}}, runtime)
        user, token, subject = _create_owner(
            runtime,
            openid="wx-control-surface-gates",
            nickname="门禁用户",
            device_id="device-control-surface-gates",
            grade="大二",
        )
        _prepare_relationship(runtime, user, subject, turn_id="turn-surface-gates")
        path = f"/api/xiaoxin/memory-subjects/{subject.id}/memory/control"

        spoofed_voice = await handler.handle_companion_memory_control(
            _control_request(
                path,
                token,
                subject.id,
                {
                    "surface": "voice",
                    "action": "reset_relationship",
                    "idempotency_key": "voice-reset-handoff",
                    "payload": {},
                },
            )
        )
        spoofed_hardware = await handler.handle_companion_memory_control(
            _control_request(
                path,
                token,
                subject.id,
                {
                    "surface": "hardware",
                    "action": "restore_default_expression",
                    "idempotency_key": "hardware-control-denied",
                    "payload": {},
                },
            )
        )
        unconfirmed_reset = await handler.handle_companion_memory_control(
            _control_request(
                path,
                token,
                subject.id,
                {
                    "surface": "miniprogram",
                    "action": "reset_relationship",
                    "idempotency_key": "miniprogram-reset-unconfirmed",
                    "payload": {},
                },
            )
        )
        wrong_phrase = await handler.handle_companion_memory_control(
            _control_request(
                path,
                token,
                subject.id,
                {
                    "surface": "miniprogram",
                    "action": "purge_personal_memory",
                    "idempotency_key": "miniprogram-purge-wrong-phrase",
                    "payload": {"confirmation_phrase": "清空记忆"},
                },
            )
        )
        pet = runtime.identity_store.get_personal_pet_for_user(user.id)
        with runtime.companion_mind._store.connection() as connection:
            epoch_count = connection.execute(
                "SELECT COUNT(*) FROM relationship_epochs WHERE pet_id = ?",
                (pet.id,),
            ).fetchone()[0]
            control_count = connection.execute(
                """
                SELECT COUNT(*) FROM memory_controls
                WHERE idempotency_key IN (?, ?, ?, ?)
                """,
                (
                    "voice-reset-handoff",
                    "hardware-control-denied",
                    "miniprogram-reset-unconfirmed",
                    "miniprogram-purge-wrong-phrase",
                ),
            ).fetchone()[0]
        return (
            spoofed_voice,
            spoofed_hardware,
            unconfirmed_reset,
            wrong_phrase,
            epoch_count,
            control_count,
        )

    (
        spoofed_voice,
        spoofed_hardware,
        unconfirmed_reset,
        wrong_phrase,
        epoch_count,
        control_count,
    ) = asyncio.run(scenario())
    assert spoofed_voice.status == 400
    assert json.loads(spoofed_voice.text)["field"] == "surface"
    assert spoofed_hardware.status == 400
    assert unconfirmed_reset.status == 400
    assert wrong_phrase.status == 400
    assert epoch_count == 1
    assert control_count == 0
