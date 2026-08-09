from __future__ import annotations

from core.xiaoxin.doorbell_credentials import DoorbellCredentialStore
from core.xiaoxin.tenant_config import TenantConfig


def build_doorbell_mqtt_ota(
    tenant: TenantConfig,
    credentials: DoorbellCredentialStore,
    device_id: str,
) -> dict:
    if not tenant.doorbell.enabled:
        return {
            "version": 1,
            "enabled": False,
            "reason": "doorbell_mqtt_not_configured",
        }

    credential = credentials.get_or_create(tenant.tenant_id, device_id)
    if credential.status != "active":
        return {
            "version": 1,
            "enabled": False,
            "reason": "credential_disabled",
        }

    return {
        "version": 1,
        "enabled": True,
        "endpoint": tenant.doorbell.endpoint,
        "client_id": credential.client_id,
        "username": credential.username,
        "password": credential.password,
        "status_topic": tenant.status_topic(device_id),
        "notification_topic": tenant.notification_topic(device_id),
        "overview_topic": tenant.overview_topic(device_id),
        "keepalive_seconds": tenant.doorbell.keepalive_seconds,
        "qos": tenant.doorbell.qos,
    }
