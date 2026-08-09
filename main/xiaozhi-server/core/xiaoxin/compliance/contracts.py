from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class _StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class AgeBand(_StringEnum):
    UNDER_14 = "UNDER_14"
    AGE_14_17 = "AGE_14_17"
    AGE_18_PLUS = "AGE_18_PLUS"
    UNKNOWN = "UNKNOWN"


class AgeSource(_StringEnum):
    SELF_DECLARED = "self_declared"
    GUARDIAN_CONFIRMED = "guardian_confirmed"
    ADMIN_VERIFIED = "admin_verified"


class ServiceMode(_StringEnum):
    TOOL_ONLY = "tool_only"
    MINOR_COMPANION = "minor_companion"
    ADULT_COMPANION = "adult_companion"
    BLOCKED = "blocked"


class GlobalCompanionMode(_StringEnum):
    TOOL_ONLY = "tool_only"
    ENABLED = "enabled"


class Capability(_StringEnum):
    TOOL_QUERY = "TOOL_QUERY"
    DEVICE_BIND = "DEVICE_BIND"
    VOICEPRINT_ENROLL = "VOICEPRINT_ENROLL"
    COMPANION_CHAT = "COMPANION_CHAT"
    COMPANION_INITIATIVE = "COMPANION_INITIATIVE"
    COMPANION_MEMORY_READ = "COMPANION_MEMORY_READ"
    COMPANION_MEMORY_WRITE = "COMPANION_MEMORY_WRITE"


@dataclass(frozen=True)
class ComplianceConfig:
    enabled: bool = True
    companion_service_mode: GlobalCompanionMode = GlobalCompanionMode.TOOL_ONLY
    current_service_agreement_version: str = "service-2026-08-v1"
    current_privacy_policy_version: str = "privacy-2026-08-v1"
    current_risk_notice_version: str = "risk-2026-08-v1"
    guardian_invitation_ttl_seconds: int = 600

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> "ComplianceConfig":
        raw = value or {}
        mode_text = str(
            raw.get("companion_service_mode", GlobalCompanionMode.TOOL_ONLY.value)
        ).strip()
        try:
            mode = GlobalCompanionMode(mode_text)
        except ValueError as exc:
            raise ValueError("unsupported companion service mode") from exc

        versions = {
            "current_service_agreement_version": str(
                raw.get("current_service_agreement_version", "service-2026-08-v1")
            ).strip(),
            "current_privacy_policy_version": str(
                raw.get("current_privacy_policy_version", "privacy-2026-08-v1")
            ).strip(),
            "current_risk_notice_version": str(
                raw.get("current_risk_notice_version", "risk-2026-08-v1")
            ).strip(),
        }
        if not all(versions.values()):
            raise ValueError("compliance agreement versions must not be empty")

        ttl_seconds = int(raw.get("guardian_invitation_ttl_seconds", 600))
        if ttl_seconds < 60 or ttl_seconds > 86400:
            raise ValueError(
                "guardian invitation ttl must be between 60 and 86400 seconds"
            )

        enabled_value = raw.get("enabled", True)
        enabled = (
            enabled_value
            if isinstance(enabled_value, bool)
            else str(enabled_value).strip().lower() in {"1", "true", "yes", "on"}
        )
        return cls(
            enabled=enabled,
            companion_service_mode=mode,
            guardian_invitation_ttl_seconds=ttl_seconds,
            **versions,
        )


@dataclass(frozen=True)
class ComplianceRecord:
    user_id: str
    age_band: AgeBand = AgeBand.UNKNOWN
    age_source: AgeSource | None = None
    age_confirmed_at: str | None = None
    service_agreement_version: str | None = None
    privacy_policy_version: str | None = None
    risk_notice_version: str | None = None
    agreement_accepted_at: str | None = None
    proactive_enabled: bool = False
    memory_enabled: bool = False
    mode_override: ServiceMode | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class ComplianceStatus:
    user_id: str
    age_band: AgeBand
    age_source: AgeSource | None
    companion_mode: ServiceMode
    agreement_required: bool
    guardian_required: bool
    guardian_confirmed: bool
    guardian_binding_id: str | None
    guardian_binding_status: str | None
    proactive_authorized: bool
    memory_authorized: bool
    proactive_enabled: bool
    memory_enabled: bool
    required_actions: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class ComplianceDecision:
    capability: Capability
    allowed: bool
    status: ComplianceStatus
    reason: str


@dataclass(frozen=True)
class MiniprogramAccount:
    id: str
    openid: str
    account_role: str
    linked_user_id: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class GuardianBinding:
    id: str
    student_user_id: str
    guardian_account_id: str | None
    status: str
    consent_version: str | None
    expires_at: str
    confirmed_at: str | None
    revoked_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class GuardianInvitation:
    token: str
    binding: GuardianBinding


class ComplianceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
