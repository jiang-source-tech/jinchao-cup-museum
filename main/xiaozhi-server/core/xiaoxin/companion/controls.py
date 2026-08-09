from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, time

from .contracts import (
    CompanionControlCommand,
    CompanionControlResult,
    CompanionUnavailableError,
)
from .store import CompanionStore
from .reflection import ALLOWED_ADJUSTMENT_VALUES


def _require_control_time(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("control now must be an ISO-8601 datetime with timezone")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            "control now must be an ISO-8601 datetime with timezone"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("control now must be an ISO-8601 datetime with timezone")
    return value


def _require_quiet_hour(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be HH:MM")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be HH:MM") from exc
    if parsed.second or parsed.microsecond:
        raise ValueError(f"{name} must be HH:MM")
    return parsed.strftime("%H:%M")


class CompanionControls:
    def __init__(self, store: CompanionStore) -> None:
        self._store = store

    def apply(self, command: CompanionControlCommand) -> CompanionControlResult:
        if command.subject.speaker_identity != "confirmed":
            raise PermissionError(
                "only the confirmed owner can control personal memory"
            )
        if command.action == "sync_academic_stage":
            now = _require_control_time(command.payload.get("now"))
            effective_at = _require_control_time(
                command.payload.get("effective_at", now)
            )
            academic_status = command.payload.get("academic_status", "active")
            transition_kind = command.payload.get("transition_kind")
            source_revision = command.payload.get("source_revision")
            clear_stage = command.payload.get("clear_stage", False)
            if not isinstance(academic_status, str):
                raise ValueError("academic_status is invalid")
            if transition_kind is not None and not isinstance(transition_kind, str):
                raise ValueError("transition_kind is invalid")
            if (
                isinstance(source_revision, bool)
                or not isinstance(source_revision, int)
                or source_revision < 1
            ):
                raise ValueError("source_revision must be a positive integer")
            if not isinstance(clear_stage, bool):
                raise ValueError("clear_stage must be boolean")
            epoch = self._store.get_active_epoch(
                owner_user_id=command.subject.owner_user_id,
                pet_id=command.subject.pet_id,
            )
            if epoch is None:
                return CompanionControlResult(
                    action=command.action,
                    status="not_applied",
                )
            evidence_id, job_id = self._store.sync_academic_stage(
                owner_user_id=command.subject.owner_user_id,
                pet_id=command.subject.pet_id,
                memory_subject_id=command.subject.memory_subject_id,
                relationship_epoch_id=epoch.epoch_id,
                academic_stage=command.subject.academic_stage,
                now=now,
                academic_status=academic_status,
                transition_kind=transition_kind,
                effective_at=effective_at,
                source_revision=source_revision,
                clear_stage=clear_stage,
            )
            return CompanionControlResult(
                action=command.action,
                status="applied" if evidence_id is not None else "already_applied",
                retained=int(evidence_id is not None),
                requeued=int(job_id is not None),
            )
        if command.action == "reset_relationship":
            now = command.payload.get("now")
            idempotency_key = command.payload.get("idempotency_key")
            now = _require_control_time(now)
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise ValueError("reset_relationship requires idempotency_key")
            return self._store.reset_relationship(
                owner_user_id=command.subject.owner_user_id,
                pet_id=command.subject.pet_id,
                memory_subject_id=command.subject.memory_subject_id,
                now=now,
                idempotency_key=idempotency_key,
            )
        if command.action == "set_growth_moments_enabled":
            enabled = command.payload.get("enabled")
            now = _require_control_time(command.payload.get("now"))
            idempotency_key = command.payload.get("idempotency_key")
            if not isinstance(enabled, bool):
                raise ValueError("growth moment enabled state must be boolean")
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise ValueError("growth moment control requires idempotency_key")
            return self._store.set_growth_moments_enabled(
                owner_user_id=command.subject.owner_user_id,
                pet_id=command.subject.pet_id,
                memory_subject_id=command.subject.memory_subject_id,
                enabled=enabled,
                now=now,
                idempotency_key=idempotency_key,
            )
        if command.action in {
            "revoke_adjustment",
            "set_interaction_contract",
            "revoke_interaction_contract",
            "set_initiative_quiet_hours",
            "restore_default_expression",
        }:
            now = _require_control_time(command.payload.get("now"))
            idempotency_key = command.payload.get("idempotency_key")
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise ValueError(f"{command.action} requires idempotency_key")
            payload: dict[str, object] = {}
            if command.action == "revoke_adjustment":
                adjustment_id = command.payload.get("adjustment_id")
                if not isinstance(adjustment_id, str) or not adjustment_id.strip():
                    raise ValueError("revoke_adjustment requires adjustment_id")
                payload["adjustment_id"] = adjustment_id
            elif command.action == "revoke_interaction_contract":
                contract_id = command.payload.get("contract_id")
                if not isinstance(contract_id, str) or not contract_id.strip():
                    raise ValueError("revoke_interaction_contract requires contract_id")
                payload["contract_id"] = contract_id
            elif command.action == "set_interaction_contract":
                dimension = command.payload.get("dimension")
                value = command.payload.get("value")
                scope = command.payload.get("scope")
                safe_label = command.payload.get("safe_label")
                safe_scope = command.payload.get("safe_scope")
                if not isinstance(
                    dimension, str
                ) or value not in ALLOWED_ADJUSTMENT_VALUES.get(dimension, frozenset()):
                    raise ValueError("interaction contract value is invalid")
                if scope not in {
                    "all",
                    "voice",
                    "miniprogram",
                    "hardware",
                    "initiative",
                    "conversation",
                    "general_qa",
                    "explicit_recall",
                    "user_low_mood",
                }:
                    raise ValueError("interaction contract scope is invalid")
                if (
                    not isinstance(safe_label, str)
                    or not safe_label.strip()
                    or len(safe_label) > 80
                    or not isinstance(safe_scope, str)
                    or not safe_scope.strip()
                    or len(safe_scope) > 80
                ):
                    raise ValueError("interaction contract safe summary is invalid")
                payload = {
                    "dimension": dimension,
                    "value": value,
                    "scope": scope,
                    "safe_label": safe_label,
                    "safe_scope": safe_scope,
                }
            elif command.action == "set_initiative_quiet_hours":
                enabled = command.payload.get("enabled")
                if not isinstance(enabled, bool):
                    raise ValueError("initiative quiet hours enabled state must be boolean")
                start = _require_quiet_hour(
                    "initiative quiet hours start",
                    command.payload.get("start"),
                )
                end = _require_quiet_hour(
                    "initiative quiet hours end",
                    command.payload.get("end"),
                )
                payload = {
                    "enabled": enabled,
                    "start": start,
                    "end": end,
                    "scope": "initiative",
                    "safe_label": (
                        f"每天 {start}—{end} 不主动打扰"
                        if enabled
                        else "全天允许主动陪伴"
                    ),
                    "safe_scope": "主动陪伴",
                }
            return self._store.apply_expression_control(
                action=command.action,
                owner_user_id=command.subject.owner_user_id,
                pet_id=command.subject.pet_id,
                memory_subject_id=command.subject.memory_subject_id,
                payload=payload,
                now=now,
                idempotency_key=idempotency_key,
            )
        if command.action == "forget_evidence":
            evidence_id = command.payload.get("evidence_id")
            now = command.payload.get("now")
            idempotency_key = command.payload.get("idempotency_key")
            if not isinstance(evidence_id, str) or not evidence_id.strip():
                raise ValueError("forget_evidence requires evidence_id")
            now = _require_control_time(now)
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise ValueError("forget_evidence requires idempotency_key")
            return self._store.forget_evidence(
                owner_user_id=command.subject.owner_user_id,
                pet_id=command.subject.pet_id,
                memory_subject_id=command.subject.memory_subject_id,
                evidence_id=evidence_id,
                now=now,
                idempotency_key=idempotency_key,
            )
        if command.action in {"confirm_candidate", "reject_candidate"}:
            evidence_id = command.payload.get("evidence_id")
            now = _require_control_time(command.payload.get("now"))
            idempotency_key = command.payload.get("idempotency_key")
            if not isinstance(evidence_id, str) or not evidence_id.strip():
                raise ValueError(f"{command.action} requires evidence_id")
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise ValueError(f"{command.action} requires idempotency_key")
            return self._store.resolve_memory_candidate(
                owner_user_id=command.subject.owner_user_id,
                pet_id=command.subject.pet_id,
                memory_subject_id=command.subject.memory_subject_id,
                evidence_id=evidence_id,
                resolution=(
                    "confirmed" if command.action == "confirm_candidate" else "rejected"
                ),
                now=now,
                idempotency_key=idempotency_key,
            )
        if command.action == "forget_theme":
            theme = command.payload.get("theme")
            now = command.payload.get("now")
            idempotency_key = command.payload.get("idempotency_key")
            if not isinstance(theme, str) or not theme.strip():
                raise ValueError("forget_theme requires theme")
            now = _require_control_time(now)
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise ValueError("forget_theme requires idempotency_key")
            return self._store.forget_theme(
                owner_user_id=command.subject.owner_user_id,
                pet_id=command.subject.pet_id,
                memory_subject_id=command.subject.memory_subject_id,
                theme=theme,
                now=now,
                idempotency_key=idempotency_key,
            )
        if command.action == "correct_evidence":
            evidence_id = command.payload.get("evidence_id")
            replacement_content = command.payload.get("replacement_content")
            source_summary = command.payload.get("source_summary")
            now = command.payload.get("now")
            idempotency_key = command.payload.get("idempotency_key")
            if not isinstance(evidence_id, str) or not evidence_id.strip():
                raise ValueError("correct_evidence requires evidence_id")
            if not isinstance(replacement_content, Mapping):
                raise ValueError("correct_evidence requires replacement_content")
            if not isinstance(source_summary, str) or not source_summary.strip():
                raise ValueError("correct_evidence requires source_summary")
            now = _require_control_time(now)
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise ValueError("correct_evidence requires idempotency_key")
            return self._store.correct_evidence_control(
                owner_user_id=command.subject.owner_user_id,
                pet_id=command.subject.pet_id,
                memory_subject_id=command.subject.memory_subject_id,
                evidence_id=evidence_id,
                replacement_content=dict(replacement_content),
                source_summary=source_summary,
                now=now,
                idempotency_key=idempotency_key,
            )
        if command.action == "set_boundary":
            boundary_key = command.payload.get("boundary_key")
            value = command.payload.get("value")
            source_summary = command.payload.get("source_summary")
            now = command.payload.get("now")
            idempotency_key = command.payload.get("idempotency_key")
            if not isinstance(boundary_key, str) or not boundary_key.strip():
                raise ValueError("set_boundary requires boundary_key")
            if value is None:
                raise ValueError("set_boundary requires value")
            if not isinstance(source_summary, str) or not source_summary.strip():
                raise ValueError("set_boundary requires source_summary")
            now = _require_control_time(now)
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise ValueError("set_boundary requires idempotency_key")
            return self._store.set_boundary(
                owner_user_id=command.subject.owner_user_id,
                pet_id=command.subject.pet_id,
                memory_subject_id=command.subject.memory_subject_id,
                boundary_key=boundary_key,
                value=value,
                source_summary=source_summary,
                now=now,
                idempotency_key=idempotency_key,
            )
        if command.action == "revoke_boundary":
            evidence_id = command.payload.get("evidence_id")
            now = command.payload.get("now")
            idempotency_key = command.payload.get("idempotency_key")
            if not isinstance(evidence_id, str) or not evidence_id.strip():
                raise ValueError("revoke_boundary requires evidence_id")
            now = _require_control_time(now)
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise ValueError("revoke_boundary requires idempotency_key")
            return self._store.revoke_boundary(
                owner_user_id=command.subject.owner_user_id,
                pet_id=command.subject.pet_id,
                memory_subject_id=command.subject.memory_subject_id,
                evidence_id=evidence_id,
                now=now,
                idempotency_key=idempotency_key,
            )
        if command.action == "purge_personal_memory":
            now = command.payload.get("now")
            idempotency_key = command.payload.get("idempotency_key")
            now = _require_control_time(now)
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise ValueError("purge_personal_memory requires idempotency_key")
            return self._store.purge_personal_memory(
                owner_user_id=command.subject.owner_user_id,
                pet_id=command.subject.pet_id,
                memory_subject_id=command.subject.memory_subject_id,
                now=now,
                idempotency_key=idempotency_key,
            )
        if command.action == "record_initiative_feedback":
            decision_id = command.payload.get("decision_id")
            outcome = command.payload.get("outcome")
            now = _require_control_time(command.payload.get("now"))
            idempotency_key = command.payload.get("idempotency_key")
            if not isinstance(decision_id, str) or not decision_id.strip():
                raise ValueError("initiative feedback requires decision_id")
            if outcome not in {
                "ignored",
                "accepted",
                "rejected",
                "delivery_failed",
            }:
                raise ValueError("initiative feedback outcome is invalid")
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise ValueError("initiative feedback requires idempotency_key")
            return self._store.record_initiative_feedback(
                owner_user_id=command.subject.owner_user_id,
                pet_id=command.subject.pet_id,
                memory_subject_id=command.subject.memory_subject_id,
                decision_id=decision_id,
                outcome=outcome,
                now=now,
                idempotency_key=idempotency_key,
            )
        raise CompanionUnavailableError(
            f"control action {command.action!r} is not implemented yet"
        )
