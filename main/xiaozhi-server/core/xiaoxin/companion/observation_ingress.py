from __future__ import annotations

import logging
from typing import Any, Mapping

from .contracts import (
    CompanionObservation,
    CompanionObserveResult,
    ObservationKind,
    build_companion_subject_context,
)
from .mind import CompanionMind


LOGGER = logging.getLogger(__name__)


class CompanionObservationIngress:
    """Resolve authenticated business events into the CompanionMind seam."""

    def __init__(self, identity_store: Any, companion_mind: CompanionMind) -> None:
        self._identity_store = identity_store
        self._companion_mind = companion_mind

    def observe_user_event(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        kind: ObservationKind,
        source_kind: str,
        source_ref: str,
        occurred_at: str,
        payload: Mapping[str, object],
        safe_summary: str,
    ) -> CompanionObserveResult | None:
        pet = self._identity_store.get_personal_pet_for_user(user_id)
        if pet is None:
            LOGGER.warning(
                "Companion observation skipped because personal pet is unavailable",
                extra={"companion_owner_user_id": user_id},
            )
            return None
        subjects = tuple(
            item
            for item in self._identity_store.list_memory_subjects_for_user(user_id)
            if item.kind == "user_speaker"
            and item.merged_into_subject_id is None
        )
        if len(subjects) != 1:
            LOGGER.warning(
                "Companion observation deferred because confirmed subject is not unique",
                extra={
                    "companion_owner_user_id": user_id,
                    "companion_pet_id": pet.id,
                    "companion_confirmed_subject_count": len(subjects),
                },
            )
            return self._companion_mind._defer_observation(
                owner_user_id=user_id,
                pet_id=pet.id,
                idempotency_key=idempotency_key,
                kind=kind,
                source_kind=source_kind,
                source_ref=source_ref,
                occurred_at=occurred_at,
                payload=payload,
                safe_summary=safe_summary,
                queued_reason=(
                    "missing_subject" if not subjects else "ambiguous_subject"
                ),
            )
        subject = subjects[0]
        profile = self._identity_store.get_student_profile_for_user(user_id)
        context = build_companion_subject_context(
            owner_user_id=user_id,
            pet_id=pet.id,
            memory_subject_id=subject.id,
            subject_kind=subject.kind,
            raw_grade=profile.get("grade") if profile is not None else None,
        )
        self._companion_mind._flush_deferred_observations(context)
        return self._companion_mind.observe(
            CompanionObservation(
                idempotency_key=idempotency_key,
                subject=context,
                kind=kind,
                source_kind=source_kind,
                source_ref=source_ref,
                occurred_at=occurred_at,
                payload=payload,
                safe_summary=safe_summary,
            )
        )

    def flush_pending_for_user(
        self,
        user_id: str,
    ) -> tuple[CompanionObserveResult, ...]:
        pet = self._identity_store.get_personal_pet_for_user(user_id)
        if pet is None:
            return ()
        subjects = tuple(
            item
            for item in self._identity_store.list_memory_subjects_for_user(user_id)
            if item.kind == "user_speaker"
            and item.merged_into_subject_id is None
        )
        if len(subjects) != 1:
            return ()
        profile = self._identity_store.get_student_profile_for_user(user_id)
        subject = subjects[0]
        context = build_companion_subject_context(
            owner_user_id=user_id,
            pet_id=pet.id,
            memory_subject_id=subject.id,
            subject_kind=subject.kind,
            raw_grade=profile.get("grade") if profile is not None else None,
        )
        return self._companion_mind._flush_deferred_observations(context)
