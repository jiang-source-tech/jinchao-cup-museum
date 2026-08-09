from core.xiaoxin.doorbell_credentials import DISABLED, DoorbellCredentialStore
from core.xiaoxin.doorbell_ota import build_doorbell_mqtt_ota
from core.xiaoxin.tenant_config import load_tenant_config


def test_builds_enabled_doorbell_mqtt_payload(tmp_path):
    tenant = load_tenant_config(
        {"xiaoxin_control": {"doorbell_mqtt": {"endpoint": "mqtt.example:1883"}}}
    )
    store = DoorbellCredentialStore(tmp_path / "doorbell.db")

    payload = build_doorbell_mqtt_ota(tenant, store, "aa:bb")

    assert payload == {
        "version": 1,
        "enabled": True,
        "endpoint": "mqtt.example:1883",
        "client_id": "hzcu-iee:aa:bb",
        "username": "hzcu-iee:aa:bb",
        "password": store.get("hzcu-iee", "aa:bb").password,
        "status_topic": "device/aa:bb/status",
        "notification_topic": "device/aa:bb/notification",
        "overview_topic": "device/aa:bb/overview",
        "keepalive_seconds": 240,
        "qos": 1,
    }


def test_ota_emits_device_topics_without_tenant_protocol_field(tmp_path):
    config = load_tenant_config(
        {"xiaoxin_control": {"doorbell_mqtt": {"endpoint": "mqtt.example:1883"}}}
    )
    store = DoorbellCredentialStore(tmp_path / "doorbell.db")

    payload = build_doorbell_mqtt_ota(config, store, "aa:bb")

    assert "tenant_id" not in payload
    assert payload["overview_topic"] == "device/aa:bb/overview"


def test_returns_disabled_payload_when_endpoint_missing(tmp_path):
    tenant = load_tenant_config({"xiaoxin_control": {}})
    store = DoorbellCredentialStore(tmp_path / "doorbell.db")

    payload = build_doorbell_mqtt_ota(tenant, store, "aa:bb")

    assert payload == {
        "version": 1,
        "enabled": False,
        "reason": "doorbell_mqtt_not_configured",
    }
    assert store.get("hzcu-iee", "aa:bb") is None


def test_reuses_existing_credential_for_same_device(tmp_path):
    tenant = load_tenant_config(
        {"xiaoxin_control": {"doorbell_mqtt": {"endpoint": "mqtt.example:1883"}}}
    )
    store = DoorbellCredentialStore(tmp_path / "doorbell.db")

    first = build_doorbell_mqtt_ota(tenant, store, "aa:bb")
    second = build_doorbell_mqtt_ota(tenant, store, "aa:bb")

    assert second["client_id"] == first["client_id"]
    assert second["username"] == first["username"]
    assert second["password"] == first["password"]


def test_returns_disabled_payload_when_credential_is_disabled(tmp_path):
    tenant = load_tenant_config(
        {"xiaoxin_control": {"doorbell_mqtt": {"endpoint": "mqtt.example:1883"}}}
    )
    store = DoorbellCredentialStore(tmp_path / "doorbell.db")
    store.get_or_create("hzcu-iee", "aa:bb")
    store.disable("hzcu-iee", "aa:bb")

    payload = build_doorbell_mqtt_ota(tenant, store, "aa:bb")

    assert store.get("hzcu-iee", "aa:bb").status == DISABLED
    assert payload == {
        "version": 1,
        "enabled": False,
        "reason": "credential_disabled",
    }
