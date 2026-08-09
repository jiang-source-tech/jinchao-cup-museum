import pytest

from core.xiaoxin.identity.resolver import XiaoxinIdentityResolver
from core.xiaoxin.identity.store import XiaoxinIdentityStore


def _bind(store: XiaoxinIdentityStore, device_id: str, user_id: str) -> None:
    store.upsert_seen_device(device_id)
    store.bind_device(device_id, user_id, device_id)


def _speaker_subject(
    store: XiaoxinIdentityStore,
    *,
    user_id: str,
    device_id: str,
    speaker_name: str,
):
    profile = store.get_or_create_speaker_profile(
        owner_user_id=user_id,
        device_id=device_id,
        speaker_key=f"key-{device_id}-{speaker_name}",
        display_name=speaker_name,
    )
    return store.get_or_create_memory_subject(
        owner_user_id=user_id,
        device_id=device_id,
        speaker_profile_id=profile.id,
        kind="user_speaker",
        display_name=speaker_name,
    )


def test_subject_alias_rejects_cross_owner_mapping(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user_a = store.create_user("user-a", "hash-a", "A")
    user_b = store.create_user("user-b", "hash-b", "B")
    _bind(store, "device-a", user_a.id)
    _bind(store, "device-b", user_b.id)
    subject_a = _speaker_subject(
        store,
        user_id=user_a.id,
        device_id="device-a",
        speaker_name="小林",
    )
    subject_b = _speaker_subject(
        store,
        user_id=user_b.id,
        device_id="device-b",
        speaker_name="小林",
    )

    with pytest.raises(ValueError, match="owner"):
        store.create_subject_alias(subject_a.id, subject_b.id, "cross-owner")


def test_subject_alias_rejects_incompatible_kind_direction(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user = store.create_user("user", "hash", "User")
    _bind(store, "device-1", user.id)
    speaker = _speaker_subject(
        store,
        user_id=user.id,
        device_id="device-1",
        speaker_name="小林",
    )
    unknown = store.get_or_create_memory_subject(
        owner_user_id=user.id,
        device_id="device-1",
        speaker_profile_id=None,
        kind="device_unknown",
        display_name="未知说话人",
    )

    with pytest.raises(ValueError, match="kind"):
        store.create_subject_alias(speaker.id, unknown.id, "downgrade-kind")


@pytest.mark.parametrize(
    ("from_subject_id", "to_subject_id", "message"),
    (
        ("missing-source", None, "from subject does not exist"),
        (None, "missing-target", "to subject does not exist"),
    ),
)
def test_subject_alias_validates_both_entities(
    tmp_path,
    from_subject_id,
    to_subject_id,
    message,
):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    fallback = store.get_or_create_memory_subject(
        owner_user_id=None,
        device_id="device-1",
        speaker_profile_id=None,
        kind="device_fallback",
        display_name="fallback",
    )

    with pytest.raises(ValueError, match=message):
        store.create_subject_alias(
            from_subject_id or fallback.id,
            to_subject_id or fallback.id,
            "missing-entity",
        )


def test_unknown_alias_to_confirmed_is_rejected_instead_of_upgrading_trust(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user = store.create_user("user", "hash", "User")
    _bind(store, "device-1", user.id)
    resolver = XiaoxinIdentityResolver(store)
    unknown_identity = resolver.resolve_turn_subject(
        "device-1",
        "未知说话人",
        "session-unknown",
    )
    confirmed_identity = resolver.resolve_turn_subject(
        "device-1",
        "小林",
        "session-confirmed",
    )

    with pytest.raises(ValueError, match="kind"):
        store.create_subject_alias(
            unknown_identity.memory_subject_id,
            confirmed_identity.memory_subject_id,
            "unsafe-trust-upgrade",
        )


def test_same_owner_device_switch_alias_uses_one_canonical_subject(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user = store.create_user("user", "hash", "User")
    _bind(store, "device-a", user.id)
    _bind(store, "device-b", user.id)
    resolver = XiaoxinIdentityResolver(store)
    canonical = resolver.resolve_turn_subject("device-a", "小林", "session-a")
    moved_device = resolver.resolve_turn_subject("device-b", "小林", "session-b")

    store.create_subject_alias(
        moved_device.memory_subject_id,
        canonical.memory_subject_id,
        "same-owner-device-switch",
    )
    resolved = resolver.resolve_turn_subject("device-b", "小林", "session-b2")

    assert resolved.memory_subject_id == canonical.memory_subject_id
    assert resolved.owner_user_id == user.id
    assert resolved.subject_kind == "user_speaker"
    assert resolved.device_id == "device-b"
