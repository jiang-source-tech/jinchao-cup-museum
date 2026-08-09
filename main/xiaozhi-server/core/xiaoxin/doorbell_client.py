from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable

from config.logger import setup_logging
from core.xiaoxin.registry import XiaoxinDeviceRegistry
from core.xiaoxin.tenant_config import (
    TenantConfig,
    load_tenant_config,
)

TAG = __name__


@dataclass(frozen=True)
class DoorbellMqttSettings:
    host: str = ""
    port: int = 1883
    username: str | None = None
    password: str | None = None
    keepalive_seconds: int = 240

    @classmethod
    def from_config(cls, config: dict) -> "DoorbellMqttSettings":
        control = config.get("xiaoxin_control", {}) or {}
        doorbell = control.get("doorbell_mqtt", {}) or {}
        endpoint = (
            doorbell.get("server_endpoint")
            or doorbell.get("endpoint")
            or config.get("server", {}).get("mqtt_gateway")
        )
        host, port = _parse_endpoint(endpoint)
        return cls(
            host=host,
            port=port,
            username=doorbell.get("username"),
            password=doorbell.get("password"),
            keepalive_seconds=int(doorbell.get("keepalive_seconds", 240)),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.host)


class XiaoxinDoorbellClient:
    def __init__(
        self,
        settings: DoorbellMqttSettings,
        registry: XiaoxinDeviceRegistry,
        tenant: TenantConfig | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.tenant = tenant or load_tenant_config({})
        self._client_factory = client_factory
        self._client: Any | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connect_listeners: list[Callable[[], None]] = []
        self._publish_ack_listeners: list[Callable[[int, int], None]] = []
        self._listener_lock = Lock()
        self._session_generation = 0
        self._active_session_generation: int | None = None
        self._last_error_state: str | None = None
        self.logger = setup_logging()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._start_client()

    def start_without_loop_for_test(self) -> None:
        self._start_client()

    def stop(self) -> None:
        if self._client is None:
            return
        self._client.loop_stop()
        self._client.disconnect()
        self._client = None
        self._active_session_generation = None

    @property
    def publish_session_generation(self) -> int | None:
        return self._active_session_generation

    def publish_wake(self, device_id: str, tenant_id: str | None = None) -> bool:
        if self._client is None and self.settings.enabled:
            self._start_client()
        if self._client is None:
            return False
        topic = self.tenant.notification_topic(device_id)
        payload = json.dumps({"type": "wake"}, separators=(",", ":"))
        result = self._client.publish(
            topic, payload, qos=self.tenant.doorbell.qos, retain=False
        )
        return getattr(result, "rc", 1) == 0

    def publish_overview(
        self, device_id: str, payload: dict[str, object]
    ) -> int | None:
        generation = self.ensure_publish_session()
        if generation is None:
            return None
        return self.publish_overview_in_session(
            generation,
            device_id,
            payload,
        )

    def ensure_publish_session(self) -> int | None:
        if self._client is None and self.settings.enabled:
            self._start_client()
        return self._active_session_generation

    def publish_overview_in_session(
        self,
        expected_generation: int,
        device_id: str,
        payload: dict[str, object],
    ) -> int | None:
        client = self._client
        if (
            client is None
            or self._active_session_generation != expected_generation
        ):
            return None
        result = client.publish(
            self.tenant.overview_topic(device_id),
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            qos=self.tenant.doorbell.qos,
            retain=True,
        )
        if (
            self._client is not client
            or self._active_session_generation != expected_generation
        ):
            return None
        return int(result.mid) if getattr(result, "rc", 1) == 0 else None

    def add_connect_listener(self, listener: Callable[[], None]) -> None:
        with self._listener_lock:
            self._connect_listeners.append(listener)

    def add_publish_ack_listener(
        self, listener: Callable[[int, int], None]
    ) -> None:
        with self._listener_lock:
            self._publish_ack_listeners.append(listener)

    def can_attempt_wake(self) -> bool:
        return self.settings.enabled

    def diagnostic_state(self) -> str:
        if not self.settings.enabled:
            return "doorbell_mqtt_disabled"
        if self._last_error_state is not None:
            return self._last_error_state
        if self._client is None:
            return "doorbell_client_not_started"
        return "ok"

    def _start_client(self) -> None:
        if not self.settings.enabled:
            self.logger.bind(tag=TAG).warning("Xiaoxin doorbell MQTT is disabled")
            return
        if self._client is not None:
            return
        client = self._new_client()
        self._session_generation += 1
        generation = self._session_generation
        setattr(client, "_xiaoxin_session_generation", generation)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_publish = self._on_publish
        if self.settings.username or self.settings.password:
            client.username_pw_set(
                username=self.settings.username,
                password=self.settings.password,
            )
        try:
            client.connect(
                self.settings.host,
                self.settings.port,
                self.settings.keepalive_seconds,
            )
            self._client = client
            self._active_session_generation = generation
            client.loop_start()
        except Exception as exc:
            if self._client is client:
                self._client = None
                self._active_session_generation = None
            self._last_error_state = "doorbell_connect_failed"
            self.logger.bind(tag=TAG).error(
                f"Xiaoxin doorbell MQTT connect failed: {exc}"
            )
            return
        self._last_error_state = None

    def _new_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        import paho.mqtt.client as mqtt

        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        client.subscribe("device/+/status", qos=self.tenant.doorbell.qos)
        client.subscribe("device/+/telemetry", qos=self.tenant.doorbell.qos)
        if getattr(reason_code, "is_failure", False):
            return
        self._notify_listeners(self._connect_listeners)

    def _on_publish(
        self, client, userdata, mid, reason_code, properties=None
    ) -> None:
        generation = int(getattr(client, "_xiaoxin_session_generation"))
        self._notify_listeners(
            self._publish_ack_listeners,
            int(mid),
            generation,
        )

    def _notify_listeners(self, listeners, *args) -> None:
        with self._listener_lock:
            snapshot = tuple(listeners)
        loop = self._loop
        for listener in snapshot:
            if loop is not None and loop.is_running():
                try:
                    loop.call_soon_threadsafe(
                        self._invoke_listener, listener, *args
                    )
                except RuntimeError:
                    self.logger.bind(tag=TAG).error(
                        "Xiaoxin doorbell MQTT listener scheduling failed"
                    )
                continue
            self._invoke_listener(listener, *args)

    def _invoke_listener(self, listener, *args) -> None:
        try:
            listener(*args)
        except Exception:
            self.logger.bind(tag=TAG).error(
                "Xiaoxin doorbell MQTT listener failed"
            )

    def _on_message(self, client, userdata, message):
        topic = getattr(message, "topic", "")
        parts = topic.split("/")
        if len(parts) != 3 or parts[0] != "device":
            return
        payload = getattr(message, "payload", b"")
        if parts[2] == "telemetry":
            self._on_telemetry(parts[1], payload)
            return
        if parts[2] != "status":
            return
        status = payload.decode("utf-8", errors="ignore").strip()
        if status not in {"online", "offline"}:
            return
        device_id = parts[1]
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(
                self.registry.update_doorbell_status,
                device_id,
                status,
                self.tenant.tenant_id,
            )
            return
        self.registry.update_doorbell_status(device_id, status, self.tenant.tenant_id)

    def _on_telemetry(self, device_id: str, payload: bytes) -> None:
        try:
            telemetry = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(telemetry, dict):
            return
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(
                self._store_telemetry,
                device_id,
                telemetry,
            )
            return
        self._store_telemetry(device_id, telemetry)

    def _store_telemetry(
        self, device_id: str, telemetry: dict[str, object]
    ) -> None:
        self.registry.update_device_telemetry(
            device_id,
            battery_level=telemetry.get("battery_level"),
            battery_percent=telemetry.get("battery_percent"),
            firmware_version=telemetry.get("firmware_version"),
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
        return host, 1883
    return host, port
