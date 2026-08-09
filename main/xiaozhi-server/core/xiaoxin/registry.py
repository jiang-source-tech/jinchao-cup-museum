from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from core.xiaoxin.control_types import XiaoxinDeviceState, utc_now_iso


def _valid_battery_percent(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= 100 else None


def _valid_battery_level(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= 4 else None


def _valid_firmware_version(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:64] if normalized else None


@dataclass
class _DeviceEntry:
    device_id: str
    tenant_id: str = "hzcu-iee"
    connection: Any | None = None
    transport: str | None = None
    last_seen_at: str | None = None
    doorbell_state: str = "offline"
    doorbell_updated_at: str | None = None
    battery_level: int | None = None
    battery_percent: int | None = None
    firmware_version: str | None = None

    def state(self) -> XiaoxinDeviceState:
        if self.connection is not None:
            return XiaoxinDeviceState.CONNECTED
        if self.doorbell_state == "online":
            return XiaoxinDeviceState.WAKEABLE
        return XiaoxinDeviceState.OFFLINE

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "device_id": self.device_id,
            "tenant_id": self.tenant_id,
            "state": self.state().value,
            "transport": self.transport,
            "last_seen_at": self.last_seen_at,
            "doorbell_state": self.doorbell_state,
            "doorbell_updated_at": self.doorbell_updated_at,
            "battery_level": self.battery_level,
            "battery_percent": self.battery_percent,
            "firmware_version": self.firmware_version,
        }


class XiaoxinDeviceRegistry:
    def __init__(self) -> None:
        self._devices: dict[str, _DeviceEntry] = {}
        self._condition = asyncio.Condition()

    def register_connection(self, device_id: str, handler: Any, transport: str) -> None:
        entry = self._entry(device_id)
        entry.connection = handler
        entry.transport = transport
        entry.last_seen_at = utc_now_iso()
        self._notify()

    def unregister_connection(self, device_id: str, handler: Any) -> None:
        entry = self._devices.get(device_id)
        if entry is None or entry.connection is not handler:
            return
        entry.connection = None
        entry.transport = None
        entry.last_seen_at = utc_now_iso()
        self._notify()

    def update_doorbell_status(
        self, device_id: str, status: str, tenant_id: str = "hzcu-iee"
    ) -> None:
        entry = self._entry(device_id)
        entry.tenant_id = tenant_id
        entry.doorbell_state = "online" if status == "online" else "offline"
        entry.doorbell_updated_at = utc_now_iso()
        self._notify()

    def update_device_telemetry(
        self,
        device_id: str,
        *,
        battery_level: object = None,
        battery_percent: object = None,
        firmware_version: object = None,
    ) -> None:
        entry = self._entry(device_id)
        normalized_level = _valid_battery_level(battery_level)
        normalized_battery = _valid_battery_percent(battery_percent)
        normalized_version = _valid_firmware_version(firmware_version)
        if normalized_level is not None:
            entry.battery_level = normalized_level
        if normalized_battery is not None:
            entry.battery_percent = normalized_battery
        if normalized_version is not None:
            entry.firmware_version = normalized_version
        entry.last_seen_at = utc_now_iso()
        self._notify()

    def get_connection(self, device_id: str) -> Any | None:
        entry = self._devices.get(device_id)
        return entry.connection if entry else None

    def get_device_state(self, device_id: str) -> XiaoxinDeviceState:
        return self._entry(device_id).state()

    def list_devices(self) -> list[dict[str, str | int | None]]:
        return [
            entry.to_dict()
            for entry in sorted(self._devices.values(), key=lambda item: item.device_id)
        ]

    async def wait_for_connected(self, device_id: str, timeout_seconds: float) -> Any | None:
        async with self._condition:
            current = self.get_connection(device_id)
            if current is not None:
                return current
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(
                        lambda: self.get_connection(device_id) is not None
                    ),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                return None
            return self.get_connection(device_id)

    def _entry(self, device_id: str) -> _DeviceEntry:
        if device_id not in self._devices:
            self._devices[device_id] = _DeviceEntry(device_id=device_id)
        return self._devices[device_id]

    def _notify(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._notify_async())

    async def _notify_async(self) -> None:
        async with self._condition:
            self._condition.notify_all()
