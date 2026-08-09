from __future__ import annotations

import pytest

from core.xiaoxin.companion import build_companion_subject_context


def test_confirmed_identity_and_student_grade_build_companion_subject_context():
    context = build_companion_subject_context(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        subject_kind="user_speaker",
        raw_grade="大二",
    )

    assert context.owner_user_id == "owner-1"
    assert context.pet_id == "pet-1"
    assert context.memory_subject_id == "subject-1"
    assert context.speaker_identity == "confirmed"
    assert context.academic_stage == "sophomore"
    assert context.persistence_allowed is True


@pytest.mark.parametrize(
    ("subject_kind", "speaker_identity"),
    [
        ("device_unknown", "unknown"),
        ("device_fallback", "invalid"),
        ("unsupported", "invalid"),
    ],
)
def test_unconfirmed_identity_cannot_persist_private_companion_memory(
    subject_kind,
    speaker_identity,
):
    context = build_companion_subject_context(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id=f"subject-{subject_kind}",
        subject_kind=subject_kind,
        raw_grade="大三",
    )

    assert context.speaker_identity == speaker_identity
    assert context.persistence_allowed is False


@pytest.mark.parametrize("missing_field", ["owner_user_id", "pet_id"])
def test_subject_context_requires_resolved_owner_and_personal_pet(missing_field):
    facts = {
        "owner_user_id": "owner-1",
        "pet_id": "pet-1",
        "memory_subject_id": "subject-1",
        "subject_kind": "user_speaker",
        "raw_grade": "大一",
    }
    facts[missing_field] = None

    with pytest.raises(ValueError):
        build_companion_subject_context(**facts)
