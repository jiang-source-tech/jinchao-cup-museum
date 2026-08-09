from __future__ import annotations

import re
from dataclasses import dataclass


FIRST_TENANT_ID = "hzcu-iee"
FIRST_TENANT_NAME = "信息与电气工程学院"
MQTT_TOPIC_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def validate_mqtt_topic_segment(value: str, field_name: str) -> str:
    text = str(value or "")
    if text != text.strip() or not MQTT_TOPIC_SEGMENT_PATTERN.fullmatch(text):
        raise ValueError(f"{field_name} must be a safe MQTT topic segment")
    return text


@dataclass(frozen=True)
class DoorbellTenantSettings:
    endpoint: str = ""
    host: str = ""
    port: int = 1883
    username: str | None = None
    password: str | None = None
    keepalive_seconds: int = 240
    qos: int = 1

    @property
    def enabled(self) -> bool:
        return bool(self.endpoint.strip())


@dataclass(frozen=True)
class TenantConfig:
    tenant_id: str
    display_name: str
    doorbell: DoorbellTenantSettings

    def status_topic(self, device_id: str) -> str:
        safe_device_id = validate_mqtt_topic_segment(device_id, "device_id")
        return f"device/{safe_device_id}/status"

    def notification_topic(self, device_id: str) -> str:
        safe_device_id = validate_mqtt_topic_segment(device_id, "device_id")
        return f"device/{safe_device_id}/notification"

    def overview_topic(self, device_id: str) -> str:
        safe_device_id = validate_mqtt_topic_segment(device_id, "device_id")
        return f"device/{safe_device_id}/overview"

    def telemetry_topic(self, device_id: str) -> str:
        safe_device_id = validate_mqtt_topic_segment(device_id, "device_id")
        return f"device/{safe_device_id}/telemetry"


def load_tenant_config(config: dict) -> TenantConfig:
    control = config.get("xiaoxin_control", {}) or {}
    tenant_section = control.get("tenant", {}) or {}
    doorbell_section = control.get("doorbell_mqtt", {}) or {}

    tenant_id = validate_mqtt_topic_segment(
        str(tenant_section.get("id") or FIRST_TENANT_ID).strip(),
        "tenant_id",
    )
    display_name = str(tenant_section.get("display_name") or FIRST_TENANT_NAME).strip()
    endpoint = str(doorbell_section.get("endpoint") or "").strip()
    host, port = _parse_endpoint(endpoint)

    return TenantConfig(
        tenant_id=tenant_id,
        display_name=display_name,
        doorbell=DoorbellTenantSettings(
            endpoint=endpoint,
            host=host,
            port=port,
            username=doorbell_section.get("username"),
            password=doorbell_section.get("password"),
            keepalive_seconds=int(doorbell_section.get("keepalive_seconds", 240)),
            qos=int(doorbell_section.get("qos", 1)),
        ),
    )


def _parse_endpoint(endpoint: str | None) -> tuple[str, int]:
    endpoint_text = str(endpoint or "").strip()
    if not endpoint_text:
        return "", 1883
    if endpoint_text.startswith("mqtt://"):
        endpoint_text = endpoint_text[len("mqtt://") :]
    if ":" not in endpoint_text:
        return endpoint_text, 1883

    host, port_text = endpoint_text.rsplit(":", 1)
    try:
        port = int(port_text)
    except ValueError:
        port = 1883
    return host, port
