from __future__ import annotations

from .ids import stable_hash
from .models import (
    SPEAKER_CONFIRMED,
    SUBJECT_DEVICE_FALLBACK,
    SUBJECT_DEVICE_UNKNOWN,
    SUBJECT_USER_SPEAKER,
    TurnIdentity,
)
from .store import XiaoxinIdentityStore

UNKNOWN_SPEAKERS = {"", "未知说话人", "unknown", "unknown speaker", "none"}


def _normalize_speaker(speaker: str | None) -> str:
    return str(speaker or "").strip()


def _is_unknown_speaker(speaker_text: str) -> bool:
    return speaker_text.lower() in UNKNOWN_SPEAKERS


class XiaoxinIdentityResolver:
    def __init__(self, store: XiaoxinIdentityStore):
        self.store = store

    def resolve_turn_subject(
        self,
        device_id: str,
        speaker: str | None,
        session_id: str,
    ) -> TurnIdentity:
        safe_device_id = str(device_id or "").strip() or f"session:{session_id}"
        device = self.store.get_device_by_device_id(safe_device_id)
        if device is None:
            device = self.store.upsert_seen_device(safe_device_id)

        owner_user_id = device.owner_user_id
        speaker_text = _normalize_speaker(speaker)

        if owner_user_id is None:
            subject = self.store.get_or_create_memory_subject(
                owner_user_id=None,
                device_id=safe_device_id,
                speaker_profile_id=None,
                kind=SUBJECT_DEVICE_FALLBACK,
                display_name=f"未绑定设备 {safe_device_id}",
            )
            return self._canonical_turn_identity(
                subject,
                safe_device_id=safe_device_id,
                is_authenticated_device=False,
            )

        if _is_unknown_speaker(speaker_text):
            subject = self.store.get_or_create_memory_subject(
                owner_user_id=owner_user_id,
                device_id=safe_device_id,
                speaker_profile_id=None,
                kind=SUBJECT_DEVICE_UNKNOWN,
                display_name="未知说话人",
            )
            return self._canonical_turn_identity(
                subject,
                safe_device_id=safe_device_id,
                is_authenticated_device=True,
            )

        if speaker_text.startswith("voiceprint:"):
            provider_speaker_id = speaker_text.removeprefix("voiceprint:").strip()
            if not provider_speaker_id:
                return self.resolve_turn_subject(
                    safe_device_id, "未知说话人", session_id
                )
            speaker_key = provider_speaker_id
        else:
            speaker_key = stable_hash(owner_user_id, safe_device_id, speaker_text)
        profile = self.store.get_or_create_speaker_profile(
            owner_user_id=owner_user_id,
            device_id=safe_device_id,
            speaker_key=speaker_key,
            display_name=speaker_text,
            status=SPEAKER_CONFIRMED,
        )
        resolved_display_name = profile.display_name or speaker_text
        subject = self.store.get_or_create_memory_subject(
            owner_user_id=owner_user_id,
            device_id=safe_device_id,
            speaker_profile_id=profile.id,
            kind=SUBJECT_USER_SPEAKER,
            display_name=resolved_display_name,
        )
        return self._canonical_turn_identity(
            subject,
            safe_device_id=safe_device_id,
            is_authenticated_device=True,
        )

    def _canonical_turn_identity(
        self,
        subject,
        *,
        safe_device_id: str,
        is_authenticated_device: bool,
    ) -> TurnIdentity:
        resolved_subject_id = self.store.resolve_subject_alias(subject.id) or subject.id
        canonical = self.store.get_memory_subject(resolved_subject_id)
        if canonical is None:
            raise ValueError("canonical memory subject does not exist")
        confidence = {
            SUBJECT_USER_SPEAKER: "speaker_confirmed",
            SUBJECT_DEVICE_UNKNOWN: "unknown_speaker",
            SUBJECT_DEVICE_FALLBACK: "device_fallback",
        }.get(canonical.kind)
        if confidence is None:
            raise ValueError("canonical memory subject kind is unsupported")
        return TurnIdentity(
            canonical.id,
            canonical.owner_user_id,
            safe_device_id,
            canonical.speaker_profile_id,
            canonical.kind,
            is_authenticated_device,
            confidence,
        )
