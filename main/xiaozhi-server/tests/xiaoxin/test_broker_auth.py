from core.xiaoxin.broker_auth import (
    export_mosquitto_auth,
    password_entries,
    render_acl,
    write_atomic,
    write_mosquitto_password_file,
)
from core.xiaoxin.doorbell_credentials import DoorbellCredentialStore
from core.xiaoxin.tenant_config import load_tenant_config


def test_render_acl_limits_device_to_own_topics(tmp_path):
    tenant = load_tenant_config({"xiaoxin_control": {}})
    store = DoorbellCredentialStore(tmp_path / "doorbell.db")
    credential = store.get_or_create("hzcu-iee", "device-1")
    store.get_or_create("hzcu-iee", "device-2")
    store.disable("hzcu-iee", "device-2")

    acl = render_acl(tenant, store.list_active() + [store.get("hzcu-iee", "device-2")])

    assert "user hzcu-iee:server" in acl
    assert "topic read device/+/status" in acl
    assert "topic read device/+/telemetry" in acl
    assert "topic write device/+/notification" in acl
    assert "topic write device/+/overview" in acl
    assert f"user {credential.username}" in acl
    assert "topic write device/device-1/status" in acl
    assert "topic write device/device-1/telemetry" in acl
    assert "topic read device/device-1/notification" in acl
    assert "topic read device/device-1/overview" in acl
    assert "user hzcu-iee:device-2" not in acl
    assert "topic read device/+/notification" not in acl


def test_password_entries_include_service_and_only_active_devices(tmp_path):
    store = DoorbellCredentialStore(tmp_path / "doorbell.db")
    active = store.get_or_create("hzcu-iee", "device-1")
    store.get_or_create("hzcu-iee", "device-2")
    store.disable("hzcu-iee", "device-2")

    entries = password_entries(
        store.list_active() + [store.get("hzcu-iee", "device-2")],
        service_username="hzcu-iee:server",
        service_password="server-secret",
    )

    assert entries == [
        ("hzcu-iee:server", "server-secret"),
        (active.username, active.password),
    ]


def test_write_mosquitto_password_file_uses_mosquitto_passwd(tmp_path):
    calls = []

    def fake_runner(args, check):
        calls.append((args, check))
        if "-c" in args:
            target = args[args.index("-c") + 1]
        else:
            target = args[2]
        from pathlib import Path

        Path(target).write_text("hashed\n", encoding="utf-8")

    target = tmp_path / "password_file"

    write_mosquitto_password_file(
        target,
        [("hzcu-iee:server", "server-secret"), ("hzcu-iee:device-1", "device-secret")],
        runner=fake_runner,
    )

    assert calls[0][0][:3] == ["mosquitto_passwd", "-b", "-c"]
    assert calls[0][0][-2:] == ["hzcu-iee:server", "server-secret"]
    assert calls[0][1] is True
    assert calls[1][0][:2] == ["mosquitto_passwd", "-b"]
    assert calls[1][0][-2:] == ["hzcu-iee:device-1", "device-secret"]
    assert calls[1][1] is True
    assert target.read_text(encoding="utf-8") == "hashed\n"
    assert not (tmp_path / "password_file.tmp").exists()


def test_write_atomic_replaces_file(tmp_path):
    target = tmp_path / "aclfile"
    target.write_text("old", encoding="utf-8")

    write_atomic(target, "new\n")

    assert target.read_text(encoding="utf-8") == "new\n"
    assert not (tmp_path / "aclfile.tmp").exists()


def test_export_mosquitto_auth_writes_password_and_acl_files(tmp_path):
    tenant = load_tenant_config(
        {
            "xiaoxin_control": {
                "doorbell_mqtt": {
                    "endpoint": "mqtt.example:1883",
                    "username": "hzcu-iee:server",
                    "password": "server-secret",
                }
            }
        }
    )
    store = DoorbellCredentialStore(tmp_path / "doorbell.db")
    credential = store.get_or_create("hzcu-iee", "device-1")
    calls = []

    def fake_password_writer(path, entries, runner=None):
        calls.append((path, entries))
        path.write_text("hashed\n", encoding="utf-8")

    result = export_mosquitto_auth(
        tenant,
        store,
        tmp_path / "auth",
        password_writer=fake_password_writer,
    )

    assert result.password_file == tmp_path / "auth" / "password_file"
    assert result.acl_file == tmp_path / "auth" / "acl_file"
    assert result.password_file.read_text(encoding="utf-8") == "hashed\n"
    assert "topic read device/device-1/notification" in result.acl_file.read_text(encoding="utf-8")
    assert calls == [
        (
            tmp_path / "auth" / "password_file",
            [("hzcu-iee:server", "server-secret"), (credential.username, credential.password)],
        )
    ]


def test_mosquitto_development_deployment_allows_anonymous_clients():
    from pathlib import Path

    project_dir = Path(__file__).resolve().parents[2]
    mosquitto_conf = (
        project_dir / "mosquitto" / "config" / "mosquitto.conf"
    ).read_text(encoding="utf-8")
    assert "allow_anonymous true" in mosquitto_conf
    assert "password_file /mosquitto/auth/password_file" not in mosquitto_conf
    assert "acl_file /mosquitto/auth/acl_file" not in mosquitto_conf

    for compose_name in ("docker-compose.yml",):
        compose = (project_dir / compose_name).read_text(encoding="utf-8")
        assert "./mosquitto/auth:/mosquitto/auth:ro" not in compose
