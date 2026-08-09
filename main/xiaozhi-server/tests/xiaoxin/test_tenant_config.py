import pytest

from core.xiaoxin.tenant_config import load_tenant_config, validate_mqtt_topic_segment


def test_loads_first_release_tenant_defaults():
    tenant = load_tenant_config({"xiaoxin_control": {}})

    assert tenant.tenant_id == "hzcu-iee"
    assert tenant.display_name == "信息与电气工程学院"
    assert tenant.doorbell.endpoint == ""
    assert tenant.doorbell.keepalive_seconds == 240
    assert tenant.doorbell.qos == 1


def test_device_topics_remain_unscoped_and_include_overview_and_telemetry():
    config = load_tenant_config({"xiaoxin_control": {}})

    assert config.status_topic("aa:bb") == "device/aa:bb/status"
    assert config.notification_topic("aa:bb") == "device/aa:bb/notification"
    assert config.overview_topic("aa:bb") == "device/aa:bb/overview"
    assert config.telemetry_topic("aa:bb") == "device/aa:bb/telemetry"


def test_rejects_mqtt_wildcards_and_path_separators_in_topic_segments():
    tenant = load_tenant_config({"xiaoxin_control": {}})

    for unsafe in ("device/1", "device+1", "device#1", " device-1", "", "device 1"):
        with pytest.raises(ValueError):
            validate_mqtt_topic_segment(unsafe, "device_id")
        with pytest.raises(ValueError):
            tenant.status_topic(unsafe)


def test_reads_explicit_endpoint_and_server_credential():
    tenant = load_tenant_config(
        {
            "xiaoxin_control": {
                "tenant": {"id": "hzcu-iee", "display_name": "信息与电气工程学院"},
                "doorbell_mqtt": {
                    "endpoint": "mqtt.example:1884",
                    "username": "hzcu-iee:server",
                    "password": "service-secret",
                    "keepalive_seconds": 120,
                    "qos": 1,
                },
            }
        }
    )

    assert tenant.doorbell.endpoint == "mqtt.example:1884"
    assert tenant.doorbell.host == "mqtt.example"
    assert tenant.doorbell.port == 1884
    assert tenant.doorbell.username == "hzcu-iee:server"
    assert tenant.doorbell.password == "service-secret"
    assert tenant.doorbell.enabled is True
