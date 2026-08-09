from __future__ import annotations

from dataclasses import dataclass


SUBJECT_USER_SPEAKER = "user_speaker"
SUBJECT_DEVICE_UNKNOWN = "device_unknown"
SUBJECT_DEVICE_FALLBACK = "device_fallback"

SPEAKER_CONFIRMED = "confirmed"
SPEAKER_UNKNOWN = "unknown"
SPEAKER_ARCHIVED = "archived"

DEVICE_BOUND = "bound"
DEVICE_SEEN = "seen"

PET_PENDING = "pending"
PET_ACTIVE = "active"
PET_ARCHIVED = "archived"

USER_ROLE_ADMIN = "admin"
USER_ROLE_USER = "user"


@dataclass(frozen=True)
class IdentityUser:
    id: str
    username: str
    password_hash: str
    display_name: str
    role: str
    created_at: str
    last_login_at: str | None = None


@dataclass(frozen=True)
class IdentitySession:
    id: str
    user_id: str
    token_hash: str
    expires_at: str
    created_at: str
    last_seen_at: str | None = None


@dataclass(frozen=True)
class IdentityDevice:
    id: str
    owner_user_id: str | None
    device_id: str
    display_name: str
    bind_status: str
    tenant_id: str
    created_at: str
    last_seen_at: str | None = None
    bound_at: str | None = None


@dataclass(frozen=True)
class PersonalPet:
    id: str
    owner_user_id: str
    status: str
    created_at: str
    companion_started_at: str | None
    started_at_source: str
    updated_at: str


@dataclass(frozen=True)
class SpeakerProfile:
    id: str
    owner_user_id: str | None
    device_id: str
    speaker_key: str
    display_name: str
    status: str
    created_at: str
    last_seen_at: str | None = None


@dataclass(frozen=True)
class MemorySubject:
    id: str
    owner_user_id: str | None
    device_id: str
    speaker_profile_id: str | None
    kind: str
    display_name: str
    created_at: str
    merged_into_subject_id: str | None = None


@dataclass(frozen=True)
class SubjectAlias:
    from_subject_id: str
    to_subject_id: str
    reason: str
    created_at: str


@dataclass(frozen=True)
class TurnIdentity:
    memory_subject_id: str
    owner_user_id: str | None
    device_id: str
    speaker_profile_id: str | None
    subject_kind: str
    is_authenticated_device: bool
    confidence: str
