from __future__ import annotations

import hashlib
import secrets

from .contracts import (
    AgeBand,
    AgeSource,
    Capability,
    ComplianceConfig,
    ComplianceDecision,
    ComplianceError,
    ComplianceStatus,
    GuardianBinding,
    GuardianInvitation,
    MiniprogramAccount,
)
from .policy import decide_capability, evaluate_status
from .store import ComplianceStore


class CompliancePolicyService:
    def __init__(self, store: ComplianceStore, config: ComplianceConfig):
        self.store = store
        self.config = config

    def status_for_user(self, user_id: str) -> ComplianceStatus:
        record = self.store.ensure_user_record(user_id)
        guardian_binding = self.store.latest_guardian_binding(user_id)
        return evaluate_status(
            record,
            guardian_confirmed=self.store.has_confirmed_guardian(
                user_id,
                consent_version=self.config.current_service_agreement_version,
            ),
            guardian_binding_id=(guardian_binding.id if guardian_binding else None),
            guardian_binding_status=(
                guardian_binding.status if guardian_binding else None
            ),
            config=self.config,
        )

    def require_capability(
        self,
        user_id: str,
        capability: Capability,
    ) -> ComplianceDecision:
        return decide_capability(self.status_for_user(user_id), capability)

    def ensure_miniprogram_account(
        self,
        openid: str,
        *,
        account_role: str,
        linked_user_id: str | None,
    ) -> MiniprogramAccount:
        return self.store.ensure_miniprogram_account(
            openid,
            account_role=account_role,
            linked_user_id=linked_user_id,
        )

    def declare_age_band(self, user_id: str, age_band: AgeBand) -> ComplianceStatus:
        if age_band is AgeBand.UNKNOWN:
            raise ComplianceError(
                "age_band_invalid",
                "an explicit age band is required",
            )
        self.store.declare_age_band(user_id, age_band, AgeSource.SELF_DECLARED)
        return self.status_for_user(user_id)

    def accept_current_agreements(self, user_id: str) -> ComplianceStatus:
        self.store.accept_agreements(
            user_id,
            service_agreement_version=self.config.current_service_agreement_version,
            privacy_policy_version=self.config.current_privacy_policy_version,
            risk_notice_version=self.config.current_risk_notice_version,
        )
        return self.status_for_user(user_id)

    def update_settings(
        self,
        user_id: str,
        *,
        proactive_enabled: bool,
        memory_enabled: bool,
    ) -> ComplianceStatus:
        status = self.status_for_user(user_id)
        if (proactive_enabled or memory_enabled) and (
            status.age_band is not AgeBand.AGE_18_PLUS
            or status.agreement_required
        ):
            raise ComplianceError(
                "companion_settings_unavailable",
                "these settings are available only to adults with current agreements",
            )
        self.store.update_settings(
            user_id,
            proactive_enabled=proactive_enabled,
            memory_enabled=memory_enabled,
        )
        return self.status_for_user(user_id)

    def create_guardian_invitation(self, user_id: str) -> GuardianInvitation:
        status = self.status_for_user(user_id)
        if status.age_band is not AgeBand.AGE_14_17:
            raise ComplianceError(
                "guardian_invitation_not_required",
                "guardian confirmation is available only for users aged 14 to 17",
            )
        if status.guardian_confirmed:
            raise ComplianceError(
                "guardian_already_confirmed",
                "guardian confirmation is already complete",
            )
        if status.agreement_required:
            raise ComplianceError(
                "agreements_required",
                "current agreements must be accepted before guardian confirmation",
            )
        token = secrets.token_urlsafe(32)
        binding = self.store.create_guardian_invitation(
            user_id,
            token_hash=self._token_hash(token),
            ttl_seconds=self.config.guardian_invitation_ttl_seconds,
        )
        return GuardianInvitation(token=token, binding=binding)

    def guardian_invitation(self, token: str) -> GuardianBinding:
        binding = self.store.get_guardian_binding_by_token_hash(
            self._token_hash(token)
        )
        if binding is None:
            raise ComplianceError(
                "guardian_invitation_not_found",
                "invitation not found",
            )
        if binding.status == "expired":
            raise ComplianceError(
                "guardian_invitation_expired",
                "invitation has expired",
            )
        if binding.status != "pending":
            raise ComplianceError(
                "guardian_invitation_unavailable",
                "invitation is no longer available",
            )
        return binding

    def confirm_guardian_invitation(
        self,
        *,
        token: str,
        guardian_account: MiniprogramAccount,
    ) -> ComplianceStatus:
        if guardian_account.account_role != "guardian":
            raise ComplianceError(
                "guardian_account_required",
                "a guardian account is required",
            )
        binding = self.guardian_invitation(token)
        self.store.confirm_guardian_binding(
            binding.id,
            guardian_account_id=guardian_account.id,
            consent_version=self.config.current_service_agreement_version,
        )
        return self.status_for_user(binding.student_user_id)

    def revoke_guardian_binding(
        self,
        user_id: str,
        binding_id: str,
    ) -> ComplianceStatus:
        self.store.revoke_guardian_binding(user_id, binding_id)
        return self.status_for_user(user_id)

    @staticmethod
    def _token_hash(token: str) -> str:
        token_value = str(token or "").strip()
        if not token_value:
            raise ComplianceError(
                "guardian_token_required",
                "guardian token is required",
            )
        return hashlib.sha256(token_value.encode("utf-8")).hexdigest()
