from core.xiaoxin.doorbell_credentials import DoorbellCredentialStore


def test_get_or_create_returns_stable_per_device_credential(tmp_path):
    store = DoorbellCredentialStore(tmp_path / "doorbell.db")

    first = store.get_or_create("hzcu-iee", "aa:bb")
    second = store.get_or_create("hzcu-iee", "aa:bb")

    assert first.id == second.id
    assert first.tenant_id == "hzcu-iee"
    assert first.device_id == "aa:bb"
    assert first.client_id == "hzcu-iee:aa:bb"
    assert first.username == "hzcu-iee:aa:bb"
    assert first.password == second.password
    assert len(first.password) >= 43
    assert first.status == "active"


def test_credentials_are_tenant_scoped(tmp_path):
    store = DoorbellCredentialStore(tmp_path / "doorbell.db")

    first = store.get_or_create("hzcu-iee", "device-1")
    other = store.get_or_create("other-tenant", "device-1")

    assert first.id != other.id
    assert first.username == "hzcu-iee:device-1"
    assert other.username == "other-tenant:device-1"


def test_rejects_unsafe_tenant_or_device_ids(tmp_path):
    store = DoorbellCredentialStore(tmp_path / "doorbell.db")

    for unsafe in ("device/1", "device+1", "device#1", "device 1"):
        try:
            store.get_or_create("hzcu-iee", unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted unsafe device id {unsafe!r}")

    try:
        store.get_or_create("hzcu/iee", "device-1")
    except ValueError:
        pass
    else:
        raise AssertionError("accepted unsafe tenant id")


def test_rotate_replaces_password_and_marks_generation(tmp_path):
    store = DoorbellCredentialStore(tmp_path / "doorbell.db")
    first = store.get_or_create("hzcu-iee", "device-1")

    rotated = store.rotate("hzcu-iee", "device-1")

    assert rotated.id == first.id
    assert rotated.password != first.password
    assert rotated.generation == first.generation + 1
    assert rotated.status == "active"


def test_disable_marks_credential_unusable(tmp_path):
    store = DoorbellCredentialStore(tmp_path / "doorbell.db")
    store.get_or_create("hzcu-iee", "device-1")

    store.disable("hzcu-iee", "device-1")
    credential = store.get("hzcu-iee", "device-1")

    assert credential is not None
    assert credential.status == "disabled"


def test_list_active_returns_only_active_credentials_in_sorted_order(tmp_path):
    store = DoorbellCredentialStore(tmp_path / "doorbell.db")

    active_late = store.get_or_create("tenant-b", "device-2")
    disabled = store.get_or_create("tenant-a", "device-3")
    active_early = store.get_or_create("tenant-a", "device-1")

    store.disable(disabled.tenant_id, disabled.device_id)

    active_credentials = store.list_active()

    assert [credential.tenant_id for credential in active_credentials] == [
        active_early.tenant_id,
        active_late.tenant_id,
    ]
    assert [credential.device_id for credential in active_credentials] == [
        active_early.device_id,
        active_late.device_id,
    ]
    assert disabled.id not in {credential.id for credential in active_credentials}
    assert all(credential.status == "active" for credential in active_credentials)


def test_verify_password_uses_constant_time_comparison(tmp_path, monkeypatch):
    store = DoorbellCredentialStore(tmp_path / "doorbell.db")
    credential = store.get_or_create("default", "device-1")
    compared = []

    def compare_digest(stored, supplied):
        compared.append((stored, supplied))
        return stored == supplied

    monkeypatch.setattr(
        "core.xiaoxin.doorbell_credentials.secrets.compare_digest",
        compare_digest,
    )

    assert (
        store.verify_password(
            credential.username,
            "device-1",
            credential.password,
        )
        is True
    )
    assert store.verify_password(credential.username, "device-1", "wrong") is False
    assert (
        store.verify_password(
            credential.username,
            "device-2",
            credential.password,
        )
        is False
    )
    assert compared == [
        (credential.password, credential.password),
        (credential.password, "wrong"),
    ]


def test_verify_password_rejects_disabled_credential(tmp_path):
    store = DoorbellCredentialStore(tmp_path / "doorbell.db")
    credential = store.get_or_create("default", "device-1")

    store.disable("default", "device-1")

    assert (
        store.verify_password(
            credential.username,
            credential.device_id,
            credential.password,
        )
        is False
    )
