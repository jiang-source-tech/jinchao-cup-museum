from core.xiaoxin.identity.models import (
    SUBJECT_DEVICE_FALLBACK,
    SUBJECT_DEVICE_UNKNOWN,
    SUBJECT_USER_SPEAKER,
)
from core.xiaoxin.identity.resolver import XiaoxinIdentityResolver
from core.xiaoxin.identity.store import XiaoxinIdentityStore


def _bind_seen_device(store: XiaoxinIdentityStore, device_id: str, user_id: str, display_name: str) -> None:
    store.upsert_seen_device(device_id)
    store.bind_device(device_id, user_id, display_name)


def test_bound_device_confirmed_speaker_resolves_to_user_speaker(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user = store.create_user("liu", "hash", "刘昊江")
    _bind_seen_device(store, "device-1", user.id, "桌面小新")
    resolver = XiaoxinIdentityResolver(store)

    identity = resolver.resolve_turn_subject("device-1", "刘昊江", "session-1")

    assert identity.owner_user_id == user.id
    assert identity.subject_kind == SUBJECT_USER_SPEAKER
    assert identity.memory_subject_id.startswith("ms_")


def test_bound_device_unknown_speaker_resolves_to_device_unknown(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user = store.create_user("liu", "hash", "刘昊江")
    _bind_seen_device(store, "device-1", user.id, "桌面小新")
    resolver = XiaoxinIdentityResolver(store)

    identity = resolver.resolve_turn_subject("device-1", "未知说话人", "session-1")

    assert identity.owner_user_id == user.id
    assert identity.subject_kind == SUBJECT_DEVICE_UNKNOWN


def test_unbound_device_resolves_to_device_fallback(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    resolver = XiaoxinIdentityResolver(store)

    identity = resolver.resolve_turn_subject("device-1", "刘昊江", "session-1")

    assert identity.owner_user_id is None
    assert identity.subject_kind == SUBJECT_DEVICE_FALLBACK


def test_same_speaker_name_on_two_devices_does_not_share_subject(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user = store.create_user("liu", "hash", "刘昊江")
    _bind_seen_device(store, "device-1", user.id, "桌面小新")
    _bind_seen_device(store, "device-2", user.id, "客厅小新")
    resolver = XiaoxinIdentityResolver(store)

    first = resolver.resolve_turn_subject("device-1", "刘昊江", "session-1")
    second = resolver.resolve_turn_subject("device-2", "刘昊江", "session-2")

    assert first.memory_subject_id != second.memory_subject_id


def test_voiceprint_provider_id_reuses_enrolled_speaker_profile(tmp_path):
    store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
    user = store.create_user("liu", "hash", "刘昊江")
    _bind_seen_device(store, "device-1", user.id, "桌面小新")
    enrolled = store.get_or_create_speaker_profile(
        owner_user_id=user.id,
        device_id="device-1",
        speaker_key="xiaoxin_voiceprint_1",
        display_name="刘昊江",
    )
    resolver = XiaoxinIdentityResolver(store)

    identity = resolver.resolve_turn_subject(
        "device-1", "voiceprint:xiaoxin_voiceprint_1", "session-1"
    )

    assert identity.speaker_profile_id == enrolled.id
    assert identity.subject_kind == SUBJECT_USER_SPEAKER
    assert len(store.list_speakers_for_device(user.id, "device-1")) == 1
