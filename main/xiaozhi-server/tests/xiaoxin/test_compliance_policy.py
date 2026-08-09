import sqlite3

from core.xiaoxin.compliance import (
    AgeBand,
    Capability,
    ComplianceConfig,
    CompliancePolicyService,
    ComplianceStore,
    GlobalCompanionMode,
    ServiceMode,
)
from core.xiaoxin.identity import XiaoxinIdentityStore


def test_compliance_mode_matrix_and_protective_switch(tmp_path):
    db_path = tmp_path / "xiaoxin_control.db"
    identity_store = XiaoxinIdentityStore(db_path)
    user, _ = identity_store.get_or_create_student_by_openid(
        "openid-student",
        "Student",
    )
    store = ComplianceStore(db_path)
    service = CompliancePolicyService(
        store,
        ComplianceConfig(companion_service_mode=GlobalCompanionMode.TOOL_ONLY),
    )

    with sqlite3.connect(db_path) as conn:
        account = conn.execute(
            "SELECT * FROM miniprogram_accounts WHERE openid = ?",
            ("openid-student",),
        ).fetchone()
    assert account is not None
    assert account[2] == "student"
    assert account[3] == user.id
    ComplianceStore(db_path)
    with sqlite3.connect(db_path) as conn:
        account_count = conn.execute(
            "SELECT COUNT(*) FROM miniprogram_accounts WHERE openid = ?",
            ("openid-student",),
        ).fetchone()[0]
    assert account_count == 1

    unknown = service.status_for_user(user.id)
    assert unknown.companion_mode is ServiceMode.TOOL_ONLY
    assert unknown.required_actions == ("declare_age_band", "accept_agreements")
    assert service.require_capability(user.id, Capability.TOOL_QUERY).allowed is True
    assert service.require_capability(user.id, Capability.COMPANION_CHAT).allowed is False

    enabled_service = CompliancePolicyService(
        store,
        ComplianceConfig(companion_service_mode=GlobalCompanionMode.ENABLED),
    )
    accepted_at = "2026-08-07T10:00:00+00:00"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE companion_compliance
            SET age_band = ?, age_source = 'self_declared', age_confirmed_at = ?,
                service_agreement_version = ?, privacy_policy_version = ?,
                risk_notice_version = ?, agreement_accepted_at = ?,
                proactive_enabled = 0, memory_enabled = 0, updated_at = ?
            WHERE user_id = ?
            """,
            (
                AgeBand.UNDER_14.value,
                accepted_at,
                service.config.current_service_agreement_version,
                service.config.current_privacy_policy_version,
                service.config.current_risk_notice_version,
                accepted_at,
                accepted_at,
                user.id,
            ),
        )

    under_14 = enabled_service.status_for_user(user.id)
    assert under_14.companion_mode is ServiceMode.TOOL_ONLY
    assert under_14.reason == "under_14_tool_only"
    assert enabled_service.require_capability(
        user.id, Capability.DEVICE_BIND
    ).allowed is True

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE companion_compliance SET age_band = ? WHERE user_id = ?",
            (AgeBand.AGE_14_17.value, user.id),
        )
    minor_without_guardian = enabled_service.status_for_user(user.id)
    assert minor_without_guardian.companion_mode is ServiceMode.TOOL_ONLY
    assert minor_without_guardian.required_actions == ("confirm_guardian",)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO miniprogram_accounts (
                id, openid, account_role, created_at, updated_at
            ) VALUES ('mpa-guardian', 'openid-guardian', 'guardian', ?, ?)
            """,
            (accepted_at, accepted_at),
        )
        conn.execute(
            """
            INSERT INTO guardian_bindings (
                id, student_user_id, guardian_account_id,
                invitation_token_hash, status, consent_version,
                expires_at, confirmed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'confirmed', ?, ?, ?, ?, ?)
            """,
            (
                "gdn-confirmed",
                user.id,
                "mpa-guardian",
                "token-hash",
                service.config.current_service_agreement_version,
                "2026-08-07T10:10:00+00:00",
                accepted_at,
                accepted_at,
                accepted_at,
            ),
        )
    minor = enabled_service.status_for_user(user.id)
    assert minor.companion_mode is ServiceMode.MINOR_COMPANION
    assert minor.proactive_enabled is False
    assert minor.memory_enabled is False

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE companion_compliance
            SET age_band = ?
            WHERE user_id = ?
            """,
            (AgeBand.AGE_18_PLUS.value, user.id),
        )
    protected = service.update_settings(
        user.id,
        proactive_enabled=True,
        memory_enabled=True,
    )
    assert protected.companion_mode is ServiceMode.TOOL_ONLY
    assert protected.reason == "service_tool_only"
    assert protected.proactive_authorized is True
    assert protected.memory_authorized is True
    assert protected.proactive_enabled is False
    assert protected.memory_enabled is False

    adult = enabled_service.status_for_user(user.id)
    assert adult.companion_mode is ServiceMode.ADULT_COMPANION
    assert adult.proactive_enabled is True
    assert adult.memory_enabled is True
    assert enabled_service.require_capability(
        user.id, Capability.COMPANION_CHAT
    ).allowed is True
