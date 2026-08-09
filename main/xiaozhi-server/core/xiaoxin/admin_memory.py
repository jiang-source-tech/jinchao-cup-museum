from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import datetime
from typing import Any, Mapping

from core.xiaoxin.companion import (
    CompanionProjectionRequest,
    CompanionSubjectContext,
    CompanionUnavailableError,
    build_companion_subject_context,
)
from core.xiaoxin.identity.models import (
    DEVICE_BOUND,
    PET_ACTIVE,
    SPEAKER_CONFIRMED,
    SUBJECT_USER_SPEAKER,
)


class AdminMemoryQueryService:
    def __init__(
        self,
        identity_store: Any,
        registry: Any = None,
        count_provider: Any = None,
    ):
        self.identity_store = identity_store
        self.registry = registry
        self.count_provider = count_provider

    def list_subjects(
        self,
        *,
        search: str = "",
        owner_user_id: str = "",
        device_id: str = "",
        subject_kind: str = "",
        readiness_code: str = "",
        include_merged: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, object]:
        items = [self._subject_item(subject) for subject in self.identity_store.list_all_memory_subjects()]
        counts_by_subject: Mapping[str, Mapping[str, object]] = {}
        load_counts = getattr(self.count_provider, "_admin_subject_counts", None)
        if callable(load_counts) and items:
            try:
                counts_by_subject = load_counts(tuple(item["id"] for item in items))
            except CompanionUnavailableError:
                counts_by_subject = {}
        for item in items:
            if item["id"] in counts_by_subject:
                item["counts"] = dict(counts_by_subject[item["id"]])
        if not include_merged:
            items = [item for item in items if not item["subject"]["merged_into_subject_id"]]
        search_value = search.strip().casefold()
        if search_value:
            items = [item for item in items if search_value in self._search_text(item)]
        if owner_user_id:
            items = [item for item in items if item["owner"]["id"] == owner_user_id]
        if device_id:
            items = [item for item in items if item["device"]["device_id"] == device_id]
        if subject_kind:
            items = [item for item in items if item["kind"] == subject_kind]
        if readiness_code:
            items = [item for item in items if item["readiness"]["code"] == readiness_code]

        items.sort(key=self._sort_key, reverse=True)
        recommended = next(
            (item["id"] for item in items if item["readiness"]["code"] == "ready"),
            items[0]["id"] if items else None,
        )
        safe_page = max(1, int(page))
        safe_page_size = max(1, min(int(page_size), 200))
        start = (safe_page - 1) * safe_page_size
        page_items = items[start : start + safe_page_size]
        for item in page_items:
            item["recommended"] = item["id"] == recommended
        return {
            "memory_subjects": page_items,
            "recommended_subject_id": recommended,
            "total": len(items),
            "page": safe_page,
            "page_size": safe_page_size,
        }

    def get_subject(self, subject_id: str) -> dict[str, object] | None:
        subject = self.identity_store.get_memory_subject(subject_id)
        if subject is None:
            return None
        return self._subject_item(subject)

    def project_subject(self, subject_item: Mapping[str, Any], companion_mind: Any) -> dict[str, object]:
        readiness = subject_item["readiness"]
        if readiness["code"] != "ready":
            return {
                "surface": "operator",
                "xiaoxin_age": None,
                "relationship_stage": "first_meeting",
                "payload": {
                    "policy": {"memory_reference_budget": 0},
                    "evidence": [],
                    "diagnostics": {"health": {}},
                },
            }
        context = self.subject_context(subject_item)
        projection = companion_mind.project(
            CompanionProjectionRequest(
                subject=context,
                surface="operator",
                now=datetime.now().astimezone().isoformat(),
            )
        )
        return asdict(projection)

    def subject_context(
        self, subject_item: Mapping[str, Any]
    ) -> CompanionSubjectContext:
        owner = subject_item["owner"]
        pet = subject_item["pet"]
        subject = subject_item["subject"]
        if subject_item["readiness"]["code"] != "ready":
            raise PermissionError("memory subject is not ready")
        profile = self.identity_store.get_student_profile_for_user(owner["id"])
        return build_companion_subject_context(
            owner_user_id=owner["id"],
            pet_id=pet["id"],
            memory_subject_id=subject["id"],
            subject_kind=subject["kind"],
            raw_grade=profile.get("grade") if profile is not None else None,
        )

    def _subject_item(self, subject: Any) -> dict[str, object]:
        owner = (
            self.identity_store.get_user_by_id(subject.owner_user_id)
            if subject.owner_user_id
            else None
        )
        device = self.identity_store.get_device_by_device_id(subject.device_id)
        speaker = (
            self.identity_store.get_speaker_profile(subject.speaker_profile_id)
            if subject.speaker_profile_id
            else None
        )
        pet = (
            self.identity_store.get_personal_pet_for_user(subject.owner_user_id)
            if subject.owner_user_id
            else None
        )
        runtime_state = self._runtime_state(subject.device_id)
        readiness = self._readiness(subject, owner, device, speaker, pet)
        owner_payload = {
            "id": owner.id if owner else None,
            "username": (
                owner.username
                if owner and not owner.username.startswith("mp:")
                else ""
            ),
            "display_name": owner.display_name if owner else "",
            "account_source": (
                "miniprogram" if owner and owner.username.startswith("mp:") else "local"
            ) if owner else "unknown",
            "status": "active" if owner else "missing",
        }
        device_payload = {
            "device_id": subject.device_id,
            "display_name": device.display_name if device else subject.device_id,
            "bind_status": device.bind_status if device else "missing",
            "owner_user_id": device.owner_user_id if device else None,
            "last_seen_at": device.last_seen_at if device else None,
            "connection_state": runtime_state,
        }
        speaker_payload = {
            "id": speaker.id if speaker else None,
            "display_name": speaker.display_name if speaker else subject.display_name,
            "status": speaker.status if speaker else "unknown",
            "last_seen_at": speaker.last_seen_at if speaker else None,
            "speaker_key_digest": self._speaker_key_digest(speaker.speaker_key) if speaker else None,
        }
        pet_payload = {
            "id": pet.id if pet else None,
            "status": pet.status if pet else "missing",
            "companion_started_at": pet.companion_started_at if pet else None,
        }
        subject_payload = {
            "id": subject.id,
            "kind": subject.kind,
            "display_name": subject.display_name,
            "created_at": subject.created_at,
            "merged_into_subject_id": subject.merged_into_subject_id,
        }
        return {
            **subject_payload,
            "subject": subject_payload,
            "owner": owner_payload,
            "device": device_payload,
            "speaker": speaker_payload,
            "pet": pet_payload,
            "readiness": readiness,
            "counts": {
                "available": False,
                "evidence": None,
                "candidate_facts": None,
                "jobs": None,
                "errors": None,
            },
        }

    @staticmethod
    def _readiness(subject: Any, owner: Any, device: Any, speaker: Any, pet: Any) -> dict[str, object]:
        if subject.merged_into_subject_id:
            code = "subject_merged"
        elif owner is None:
            code = "owner_missing"
        elif (
            device is None
            or device.bind_status != DEVICE_BOUND
            or device.owner_user_id != owner.id
        ):
            code = "device_unbound"
        elif (
            subject.kind != SUBJECT_USER_SPEAKER
            or speaker is None
            or speaker.status != SPEAKER_CONFIRMED
            or speaker.owner_user_id != owner.id
        ):
            code = "speaker_unconfirmed"
        elif pet is None:
            code = "pet_missing"
        elif pet.status != PET_ACTIVE:
            code = "pet_pending"
        else:
            code = "ready"
        return {
            "code": code,
            "persistence_allowed": code == "ready",
            "reasons": [] if code == "ready" else [code],
        }

    def _runtime_state(self, device_id: str) -> str:
        if self.registry is None:
            return "unknown"
        for item in self.registry.list_devices():
            if item.get("device_id") != device_id:
                continue
            if item.get("state") in {"connected", "online"} or item.get("doorbell_state") == "online":
                return "online"
            return str(item.get("state") or item.get("doorbell_state") or "offline")
        return "offline"

    @staticmethod
    def _speaker_key_digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _search_text(item: Mapping[str, Any]) -> str:
        values = (
            item["display_name"],
            item["id"],
            item["owner"]["username"],
            item["owner"]["display_name"],
            item["device"]["device_id"],
            item["device"]["display_name"],
            item["speaker"]["display_name"],
        )
        return " ".join(str(value or "") for value in values).casefold()

    @staticmethod
    def _sort_key(item: Mapping[str, Any]) -> tuple[object, ...]:
        return (
            not bool(item["subject"]["merged_into_subject_id"]),
            item["device"]["connection_state"] == "online",
            item["device"]["bind_status"] == DEVICE_BOUND,
            item["readiness"]["code"] == "ready",
            item["kind"] == SUBJECT_USER_SPEAKER,
            item["pet"]["status"] == PET_ACTIVE,
            item["speaker"]["last_seen_at"] or item["device"]["last_seen_at"] or item["created_at"],
            item["id"],
        )


__all__ = ["AdminMemoryQueryService", "CompanionUnavailableError"]
