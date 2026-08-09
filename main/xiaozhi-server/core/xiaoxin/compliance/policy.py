from __future__ import annotations

from .contracts import (
    AgeBand,
    Capability,
    ComplianceConfig,
    ComplianceDecision,
    ComplianceRecord,
    ComplianceStatus,
    GlobalCompanionMode,
    ServiceMode,
)


def evaluate_status(
    record: ComplianceRecord,
    *,
    guardian_confirmed: bool,
    guardian_binding_id: str | None = None,
    guardian_binding_status: str | None = None,
    config: ComplianceConfig,
) -> ComplianceStatus:
    agreement_required = not _agreements_current(record, config)
    guardian_required = record.age_band is AgeBand.AGE_14_17 and not guardian_confirmed
    required_actions: list[str] = []
    if record.age_band is AgeBand.UNKNOWN:
        required_actions.append("declare_age_band")
    if agreement_required:
        required_actions.append("accept_agreements")
    if guardian_required:
        required_actions.append("confirm_guardian")

    mode, reason = _eligible_mode(
        record,
        agreement_required=agreement_required,
        guardian_confirmed=guardian_confirmed,
    )
    if record.mode_override in {ServiceMode.BLOCKED, ServiceMode.TOOL_ONLY}:
        mode = record.mode_override
        reason = "mode_override"
    elif config.enabled and config.companion_service_mode is GlobalCompanionMode.TOOL_ONLY:
        mode = ServiceMode.TOOL_ONLY
        reason = "service_tool_only"

    proactive_enabled = (
        mode is ServiceMode.ADULT_COMPANION and record.proactive_enabled
    )
    memory_enabled = mode is ServiceMode.ADULT_COMPANION and record.memory_enabled
    return ComplianceStatus(
        user_id=record.user_id,
        age_band=record.age_band,
        age_source=record.age_source,
        companion_mode=mode,
        agreement_required=agreement_required,
        guardian_required=guardian_required,
        guardian_confirmed=guardian_confirmed,
        guardian_binding_id=guardian_binding_id,
        guardian_binding_status=guardian_binding_status,
        proactive_authorized=record.proactive_enabled,
        memory_authorized=record.memory_enabled,
        proactive_enabled=proactive_enabled,
        memory_enabled=memory_enabled,
        required_actions=tuple(required_actions),
        reason=reason,
    )


def decide_capability(
    status: ComplianceStatus,
    capability: Capability,
) -> ComplianceDecision:
    if status.companion_mode is ServiceMode.BLOCKED:
        return ComplianceDecision(capability, False, status, "account_blocked")
    if capability is Capability.TOOL_QUERY:
        return ComplianceDecision(capability, True, status, "tool_query_allowed")

    prerequisites_complete = not status.required_actions
    if capability is Capability.DEVICE_BIND:
        allowed = prerequisites_complete
        return ComplianceDecision(
            capability,
            allowed,
            status,
            "device_bind_allowed" if allowed else "compliance_setup_required",
        )
    if capability is Capability.VOICEPRINT_ENROLL:
        allowed = prerequisites_complete and status.age_band is not AgeBand.UNDER_14
        return ComplianceDecision(
            capability,
            allowed,
            status,
            "voiceprint_allowed" if allowed else "voiceprint_not_available",
        )
    if capability is Capability.COMPANION_CHAT:
        allowed = status.companion_mode in {
            ServiceMode.MINOR_COMPANION,
            ServiceMode.ADULT_COMPANION,
        }
        return ComplianceDecision(
            capability,
            allowed,
            status,
            "companion_chat_allowed" if allowed else status.reason,
        )
    if capability is Capability.COMPANION_INITIATIVE:
        allowed = (
            status.companion_mode is ServiceMode.ADULT_COMPANION
            and status.proactive_enabled
        )
        return ComplianceDecision(
            capability,
            allowed,
            status,
            "initiative_allowed" if allowed else "initiative_disabled",
        )
    if capability in {
        Capability.COMPANION_MEMORY_READ,
        Capability.COMPANION_MEMORY_WRITE,
    }:
        allowed = (
            status.companion_mode is ServiceMode.ADULT_COMPANION
            and status.memory_enabled
        )
        return ComplianceDecision(
            capability,
            allowed,
            status,
            "memory_allowed" if allowed else "memory_disabled",
        )
    raise ValueError(f"unsupported compliance capability: {capability}")


def _agreements_current(record: ComplianceRecord, config: ComplianceConfig) -> bool:
    return bool(
        record.agreement_accepted_at
        and record.service_agreement_version
        == config.current_service_agreement_version
        and record.privacy_policy_version == config.current_privacy_policy_version
        and record.risk_notice_version == config.current_risk_notice_version
    )


def _eligible_mode(
    record: ComplianceRecord,
    *,
    agreement_required: bool,
    guardian_confirmed: bool,
) -> tuple[ServiceMode, str]:
    if record.age_band is AgeBand.UNKNOWN:
        return ServiceMode.TOOL_ONLY, "age_band_required"
    if agreement_required:
        return ServiceMode.TOOL_ONLY, "agreements_required"
    if record.age_band is AgeBand.UNDER_14:
        return ServiceMode.TOOL_ONLY, "under_14_tool_only"
    if record.age_band is AgeBand.AGE_14_17:
        if not guardian_confirmed:
            return ServiceMode.TOOL_ONLY, "guardian_confirmation_required"
        return ServiceMode.MINOR_COMPANION, "minor_companion_ready"
    return ServiceMode.ADULT_COMPANION, "adult_companion_ready"
