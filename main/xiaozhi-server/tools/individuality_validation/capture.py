from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import secrets
import socket
import time
from typing import Mapping

from .hil import (
    HILAttestationMethod,
    HILCaptureAttestation,
    HILLogRecord,
    HILManifest,
    _bundle_member,
)


def collect_hardware_attestation(
    *,
    serial_port: str,
    baud_rate: int,
    server_host: str,
    server_port: int,
    output: Path,
    timeout_seconds: float = 30.0,
    attestation_method: HILAttestationMethod = "firmware_boot_observation",
    reset_device: bool = True,
) -> HILCaptureAttestation:
    """Attest a real serial device and verify the candidate server is reachable."""
    try:
        import serial  # type: ignore[import-not-found]
        from serial.tools import list_ports  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("pyserial is required for real ESP32 HIL capture") from exc

    ports = {item.device: item for item in list_ports.comports()}
    port_info = ports.get(serial_port)
    if port_info is None:
        raise RuntimeError(f"serial port is not connected: {serial_port}")
    device_instance_id = str(port_info.hwid or "").strip()
    if not device_instance_id or device_instance_id.lower() == "n/a":
        raise RuntimeError("serial device instance id is unavailable")
    if timeout_seconds <= 0:
        raise RuntimeError("HIL attestation timeout must be positive")

    if attestation_method == "firmware_boot_observation":
        attestation = _collect_firmware_boot_attestation(
            serial=serial,
            serial_port=serial_port,
            baud_rate=baud_rate,
            device_instance_id=device_instance_id,
            server_host=server_host,
            server_port=server_port,
            output=output,
            timeout_seconds=timeout_seconds,
            reset_device=reset_device,
        )
    elif attestation_method == "serial_challenge":
        attestation = _collect_serial_challenge_attestation(
            serial=serial,
            serial_port=serial_port,
            baud_rate=baud_rate,
            device_instance_id=device_instance_id,
            server_host=server_host,
            server_port=server_port,
            output=output,
            timeout_seconds=timeout_seconds,
        )
    else:
        raise RuntimeError(f"unsupported HIL attestation method: {attestation_method}")

    with socket.create_connection((server_host, server_port), timeout=timeout_seconds):
        pass
    attestation = replace(
        attestation,
        capture_completed_at=datetime.now().astimezone().isoformat(),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(asdict(attestation), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return attestation


def parse_firmware_boot_observation(raw_log: str) -> dict[str, str]:
    patterns = {
        "project_name": r"Project name:\s*(\S+)",
        "firmware_version": r"App version:\s*(\S+)",
        "firmware_elf_sha256_prefix": r"ELF file SHA256:\s*(\S+)",
        "client_id": r"Board: UUID=([0-9a-fA-F-]+)",
        "device_id": r"wifi:mode\s*:\s*sta\s*\(([0-9a-fA-F:]+)\)",
        "ota_server_endpoint": (
            r"HttpClient:\s*Established new connection to\s+([^\s]+)"
        ),
    }
    observed: dict[str, str] = {}
    for name, pattern in patterns.items():
        match = re.search(pattern, raw_log)
        if match is not None:
            observed[name] = (
                match.group(1).lower() if name == "device_id" else match.group(1)
            )
    return observed


def _collect_firmware_boot_attestation(
    *,
    serial: object,
    serial_port: str,
    baud_rate: int,
    device_instance_id: str,
    server_host: str,
    server_port: int,
    output: Path,
    timeout_seconds: float,
    reset_device: bool,
) -> HILCaptureAttestation:
    started = datetime.now().astimezone()
    chunks: list[bytes] = []
    if reset_device:
        connection = serial.Serial(  # type: ignore[attr-defined]
            serial_port,
            baudrate=baud_rate,
            timeout=min(timeout_seconds, 0.25),
            write_timeout=timeout_seconds,
        )
    else:
        connection = serial.Serial(  # type: ignore[attr-defined]
            port=None,
            baudrate=baud_rate,
            timeout=min(timeout_seconds, 0.25),
            write_timeout=timeout_seconds,
        )
        connection.port = serial_port
        connection.dtr = False
        connection.rts = False
        connection.open()
    with connection:
        if reset_device:
            connection.dtr = False
            connection.rts = True
            time.sleep(0.15)
            connection.rts = False
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            raw = connection.read(4096)
            if raw:
                chunks.append(raw)

    raw_bytes = b"".join(chunks)
    raw_log_path = output.with_suffix(".serial.log")
    raw_log_path.parent.mkdir(parents=True, exist_ok=True)
    raw_log_path.write_bytes(raw_bytes)
    observed = parse_firmware_boot_observation(
        raw_bytes.decode("utf-8", errors="replace")
    )
    required = {
        "project_name",
        "firmware_version",
        "firmware_elf_sha256_prefix",
        "client_id",
        "device_id",
        "ota_server_endpoint",
    }
    missing = sorted(required - observed.keys())
    if missing:
        raise RuntimeError(
            "ESP32 boot observation is incomplete: " + ", ".join(missing)
        )
    expected_endpoint = f"{server_host}:{server_port}"
    if observed["ota_server_endpoint"] != expected_endpoint:
        raise RuntimeError(
            "ESP32 OTA connected to an unexpected server endpoint: "
            + observed["ota_server_endpoint"]
        )
    completed = datetime.now().astimezone()
    return HILCaptureAttestation(
        attestation_id=f"hil-capture-{started:%Y%m%dT%H%M%S%z}",
        collector_version="xiaoxin-hil-collector-v2",
        capture_started_at=started.isoformat(),
        capture_completed_at=completed.isoformat(),
        serial_port=serial_port,
        serial_device_instance_id=device_instance_id,
        server_endpoint=expected_endpoint,
        hardware_challenge_nonce=None,
        hardware_challenge_response=None,
        serial_open_succeeded=True,
        server_log_stream_succeeded=False,
        network_capture_succeeded=False,
        synthetic=False,
        attestation_method="firmware_boot_observation",
        hardware_evidence_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        observed_project_name=observed["project_name"],
        observed_firmware_version=observed["firmware_version"],
        observed_firmware_elf_sha256_prefix=observed["firmware_elf_sha256_prefix"],
        observed_device_id=observed["device_id"],
        observed_client_id=observed["client_id"],
    )


def _collect_serial_challenge_attestation(
    *,
    serial: object,
    serial_port: str,
    baud_rate: int,
    device_instance_id: str,
    server_host: str,
    server_port: int,
    output: Path,
    timeout_seconds: float,
) -> HILCaptureAttestation:
    started = datetime.now().astimezone()
    nonce = secrets.token_hex(32)
    challenge = {
        "type": "xiaoxin_hil_challenge",
        "nonce": nonce,
        "issued_at": started.isoformat(),
    }
    response: Mapping[str, object] | None = None
    with serial.Serial(  # type: ignore[attr-defined]
        serial_port,
        baudrate=baud_rate,
        timeout=min(timeout_seconds, 1.0),
        write_timeout=timeout_seconds,
    ) as connection:
        connection.write((json.dumps(challenge) + "\n").encode("utf-8"))
        deadline = started + timedelta(seconds=timeout_seconds)
        while datetime.now().astimezone() < deadline:
            raw = connection.readline()
            if not raw:
                continue
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if (
                isinstance(value, dict)
                and value.get("type") == "xiaoxin_hil_challenge_response"
                and value.get("nonce") == nonce
            ):
                response = value
                break
    if response is None:
        raise RuntimeError("ESP32 did not return the HIL challenge response")
    response_fields: dict[str, str] = {}
    for name in (
        "project_name",
        "firmware_version",
        "device_id",
        "client_id",
    ):
        value = response.get(name)
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(f"ESP32 HIL challenge response is missing {name}")
        response_fields[name] = value.strip()
    elf_prefix = response.get("firmware_elf_sha256_prefix") or response.get(
        "firmware_elf_sha256"
    )
    if not isinstance(elf_prefix, str) or not elf_prefix.strip():
        raise RuntimeError(
            "ESP32 HIL challenge response is missing firmware ELF SHA-256"
        )
    response_fields["firmware_elf_sha256_prefix"] = elf_prefix.strip().lower()
    completed = datetime.now().astimezone()
    response_digest = hashlib.sha256(
        json.dumps(response, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    challenge_evidence_path = output.with_suffix(".challenge.json")
    challenge_evidence_path.parent.mkdir(parents=True, exist_ok=True)
    challenge_evidence_path.write_text(
        json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return HILCaptureAttestation(
        attestation_id=f"hil-capture-{started:%Y%m%dT%H%M%S%z}",
        collector_version="xiaoxin-hil-collector-v2",
        capture_started_at=started.isoformat(),
        capture_completed_at=completed.isoformat(),
        serial_port=serial_port,
        serial_device_instance_id=device_instance_id,
        server_endpoint=f"{server_host}:{server_port}",
        hardware_challenge_nonce=nonce,
        hardware_challenge_response=response_digest,
        serial_open_succeeded=True,
        server_log_stream_succeeded=False,
        network_capture_succeeded=False,
        synthetic=False,
        attestation_method="serial_challenge",
        hardware_evidence_sha256=response_digest,
        observed_project_name=response_fields["project_name"],
        observed_firmware_version=response_fields["firmware_version"],
        observed_firmware_elf_sha256_prefix=response_fields[
            "firmware_elf_sha256_prefix"
        ],
        observed_device_id=response_fields["device_id"].lower(),
        observed_client_id=response_fields["client_id"].lower(),
    )


def _verify_firmware_boot_evidence(
    attestation_path: Path,
    attestation: HILCaptureAttestation,
) -> None:
    raw_log_path = attestation_path.with_suffix(".serial.log")
    if not raw_log_path.is_file():
        raise ValueError(f"raw serial boot evidence is missing: {raw_log_path.name}")
    raw_bytes = raw_log_path.read_bytes()
    if hashlib.sha256(raw_bytes).hexdigest() != attestation.hardware_evidence_sha256:
        raise ValueError("raw serial boot evidence digest does not match attestation")
    observed = parse_firmware_boot_observation(
        raw_bytes.decode("utf-8", errors="replace")
    )
    expected = {
        "project_name": attestation.observed_project_name,
        "firmware_version": attestation.observed_firmware_version,
        "firmware_elf_sha256_prefix": (attestation.observed_firmware_elf_sha256_prefix),
        "device_id": attestation.observed_device_id,
        "client_id": attestation.observed_client_id,
        "ota_server_endpoint": attestation.server_endpoint,
    }
    if observed != expected:
        raise ValueError("raw serial boot evidence does not match observed identity")


def _verify_serial_challenge_evidence(
    attestation_path: Path,
    attestation: HILCaptureAttestation,
) -> None:
    evidence_path = attestation_path.with_suffix(".challenge.json")
    if not evidence_path.is_file():
        raise ValueError(f"serial challenge evidence is missing: {evidence_path.name}")
    response = json.loads(evidence_path.read_text(encoding="utf-8"))
    if not isinstance(response, dict):
        raise ValueError("serial challenge evidence must be a JSON object")
    digest = hashlib.sha256(
        json.dumps(response, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if (
        digest
        not in {
            attestation.hardware_challenge_response,
            attestation.hardware_evidence_sha256,
        }
        or attestation.hardware_challenge_response
        != attestation.hardware_evidence_sha256
    ):
        raise ValueError("serial challenge response digest does not match attestation")
    if response.get("type") != "xiaoxin_hil_challenge_response":
        raise ValueError("serial challenge response type is invalid")
    if response.get("nonce") != attestation.hardware_challenge_nonce:
        raise ValueError("serial challenge nonce does not match attestation")
    elf_prefix = response.get("firmware_elf_sha256_prefix") or response.get(
        "firmware_elf_sha256"
    )
    expected = {
        "project_name": attestation.observed_project_name,
        "firmware_version": attestation.observed_firmware_version,
        "device_id": attestation.observed_device_id,
        "client_id": attestation.observed_client_id,
        "firmware_elf_sha256_prefix": (attestation.observed_firmware_elf_sha256_prefix),
    }
    observed = {
        "project_name": response.get("project_name"),
        "firmware_version": response.get("firmware_version"),
        "device_id": (
            response.get("device_id", "").lower()
            if isinstance(response.get("device_id"), str)
            else response.get("device_id")
        ),
        "client_id": (
            response.get("client_id", "").lower()
            if isinstance(response.get("client_id"), str)
            else response.get("client_id")
        ),
        "firmware_elf_sha256_prefix": (
            elf_prefix.lower() if isinstance(elf_prefix, str) else elf_prefix
        ),
    }
    if observed != expected:
        raise ValueError("serial challenge evidence does not match observed identity")


def finalize_hardware_attestation(
    *, bundle_dir: Path
) -> tuple[HILCaptureAttestation, ...]:
    """Bind every physical-device proof to completed structured capture streams."""
    manifest_value = json.loads(
        (bundle_dir / "manifest.json").read_text(encoding="utf-8")
    )
    if not isinstance(manifest_value, dict):
        raise ValueError("HIL manifest must be a JSON object")
    manifest = HILManifest.from_dict(manifest_value)
    attestations: list[tuple[Path, HILCaptureAttestation]] = []
    for relative_path in manifest.capture_attestations:
        attestation_path = _bundle_member(bundle_dir, relative_path)
        attestation_value = json.loads(attestation_path.read_text(encoding="utf-8"))
        if not isinstance(attestation_value, dict):
            raise ValueError("HIL attestation must be a JSON object")
        attestation = HILCaptureAttestation.from_dict(attestation_value)
        if attestation.synthetic:
            raise ValueError("synthetic HIL attestation cannot be finalized")
        if attestation.attestation_method == "firmware_boot_observation":
            _verify_firmware_boot_evidence(attestation_path, attestation)
        else:
            _verify_serial_challenge_evidence(attestation_path, attestation)
        attestations.append((attestation_path, attestation))

    source_paths = {
        "serial": _bundle_member(bundle_dir, manifest.serial_log),
        "server": _bundle_member(bundle_dir, manifest.server_log),
        "network": _bundle_member(bundle_dir, manifest.network_log),
    }
    latest = max(
        datetime.fromisoformat(item.capture_completed_at) for _, item in attestations
    )
    records_by_source: dict[str, list[HILLogRecord]] = {}
    for expected_source, path in source_paths.items():
        records: list[HILLogRecord] = []
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path.name} line {line_number} must be an object")
            record = HILLogRecord.from_dict(value)
            if record.source != expected_source:
                raise ValueError(
                    f"{path.name} contains unexpected source {record.source}"
                )
            records.append(record)
            latest = max(latest, datetime.fromisoformat(record.occurred_at))
        if not records:
            raise ValueError(f"{path.name} has no structured capture records")
        records_by_source[expected_source] = records

    completed = max(latest, datetime.now().astimezone())
    finalized_attestations: list[HILCaptureAttestation] = []
    for attestation_path, attestation in attestations:
        if attestation.hardware_identity_confirmed and not any(
            record.device_id == attestation.observed_device_id
            and record.client_id == attestation.observed_client_id
            and record.firmware_version == attestation.observed_firmware_version
            and record.server_git_sha == manifest.server_git_sha
            for record in records_by_source["server"]
        ):
            raise ValueError(
                "server log does not correlate observed device MAC, UUID, and firmware"
            )
        finalized = replace(
            attestation,
            capture_completed_at=completed.isoformat(),
            serial_open_succeeded=True,
            server_log_stream_succeeded=True,
            network_capture_succeeded=True,
        )
        attestation_path.write_text(
            json.dumps(asdict(finalized), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        finalized_attestations.append(finalized)
    return tuple(finalized_attestations)
