from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Literal, Mapping, Sequence

from .contracts import (
    GateCheck,
    GateReport,
    make_report,
    require_aware_datetime,
    require_text,
)


HILOutcome = Literal["PASS", "FAIL"]
HILAttestationMethod = Literal[
    "serial_challenge",
    "firmware_boot_observation",
]
HIL_CONTRACT_VERSION = "xiaoxin-esp32-hil-v2"
REQUIRED_HIL_PATHS = (
    "cold_start",
    "normal_conversation",
    "success",
    "low_mood",
    "negative_feedback",
    "ordinary_recovery",
    "wifi_reconnect",
    "websocket_reconnect",
    "network_recovery",
    "server_restart",
    "device_restart",
    "power_restore",
    "outbox_replay",
    "duplicate_delivery",
    "delayed_delivery",
    "low_battery",
    "hardware_semantic_alignment",
    "reset_relationship",
    "purge_personal_memory",
    "initiative_disabled",
    "growth_review_disabled",
    "subject_isolation",
    "device_isolation",
    "ota_success",
    "ota_rollback",
)
NETWORK_EVIDENCE_PATHS = frozenset(
    {
        "wifi_reconnect",
        "websocket_reconnect",
        "network_recovery",
        "server_restart",
        "outbox_replay",
        "duplicate_delivery",
        "delayed_delivery",
        "ota_success",
        "ota_rollback",
    }
)


@dataclass(frozen=True)
class HILIdentityBinding:
    device_id: str
    memory_subject_id: str
    pet_id: str
    relationship_epoch_id: str

    def __post_init__(self) -> None:
        for name in (
            "device_id",
            "memory_subject_id",
            "pet_id",
            "relationship_epoch_id",
        ):
            require_text(name, getattr(self, name))

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "HILIdentityBinding":
        return cls(**dict(value))  # type: ignore[arg-type]


@dataclass(frozen=True)
class HILCaptureAttestation:
    attestation_id: str
    collector_version: str
    capture_started_at: str
    capture_completed_at: str
    serial_port: str
    serial_device_instance_id: str
    server_endpoint: str
    hardware_challenge_nonce: str | None
    hardware_challenge_response: str | None
    serial_open_succeeded: bool
    server_log_stream_succeeded: bool
    network_capture_succeeded: bool
    synthetic: bool
    attestation_method: HILAttestationMethod = "serial_challenge"
    hardware_evidence_sha256: str | None = None
    observed_project_name: str | None = None
    observed_firmware_version: str | None = None
    observed_firmware_elf_sha256_prefix: str | None = None
    observed_device_id: str | None = None
    observed_client_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "attestation_id",
            "collector_version",
            "serial_port",
            "serial_device_instance_id",
            "server_endpoint",
        ):
            require_text(name, getattr(self, name))
        require_aware_datetime("capture_started_at", self.capture_started_at)
        require_aware_datetime("capture_completed_at", self.capture_completed_at)
        if self.attestation_method not in {
            "serial_challenge",
            "firmware_boot_observation",
        }:
            raise ValueError("HIL attestation method is unsupported")
        if self.attestation_method == "serial_challenge":
            require_text("hardware_challenge_nonce", self.hardware_challenge_nonce)  # type: ignore[arg-type]
            require_text(
                "hardware_challenge_response",
                self.hardware_challenge_response,  # type: ignore[arg-type]
            )
        identity_values = (
            self.hardware_evidence_sha256,
            self.observed_project_name,
            self.observed_firmware_version,
            self.observed_firmware_elf_sha256_prefix,
            self.observed_device_id,
            self.observed_client_id,
        )
        if self.attestation_method == "firmware_boot_observation" or any(
            value is not None for value in identity_values
        ):
            for name in (
                "hardware_evidence_sha256",
                "observed_project_name",
                "observed_firmware_version",
                "observed_firmware_elf_sha256_prefix",
                "observed_device_id",
                "observed_client_id",
            ):
                require_text(name, getattr(self, name))  # type: ignore[arg-type]
            if (
                re.fullmatch(r"[0-9a-f]{64}", self.hardware_evidence_sha256 or "")
                is None
            ):
                raise ValueError("hardware_evidence_sha256 must be a SHA-256 digest")
            if (
                re.fullmatch(
                    r"[0-9a-f]{8,64}(?:\.\.\.)?",
                    self.observed_firmware_elf_sha256_prefix or "",
                )
                is None
            ):
                raise ValueError("observed firmware ELF SHA-256 prefix is invalid")
            if (
                re.fullmatch(
                    r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}",
                    self.observed_device_id or "",
                )
                is None
            ):
                raise ValueError("observed_device_id must be a MAC address")
            if (
                re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    self.observed_client_id or "",
                )
                is None
            ):
                raise ValueError("observed_client_id must be a UUID")
            instance_id = re.sub(
                r"[^0-9a-f]", "", self.serial_device_instance_id.lower()
            )
            device_id = re.sub(r"[^0-9a-f]", "", self.observed_device_id or "")
            if device_id not in instance_id:
                raise ValueError(
                    "serial USB instance is not bound to observed device MAC"
                )

    @property
    def hardware_identity_confirmed(self) -> bool:
        observed_identity = bool(
            self.hardware_evidence_sha256
            and self.observed_project_name
            and self.observed_firmware_version
            and self.observed_firmware_elf_sha256_prefix
            and self.observed_device_id
            and self.observed_client_id
        )
        if self.attestation_method == "serial_challenge":
            return bool(
                observed_identity
                and self.hardware_challenge_nonce
                and self.hardware_challenge_response
            )
        return observed_identity

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "HILCaptureAttestation":
        return cls(**dict(value))  # type: ignore[arg-type]


@dataclass(frozen=True)
class HILManifest:
    run_id: str
    started_at: str
    completed_at: str
    server_git_sha: str
    firmware_version: str
    identity_bindings: tuple[HILIdentityBinding, ...]
    policy_hash: str
    temperament_generator_version: str
    serial_port: str
    serial_log: str
    server_log: str
    network_log: str
    capture_attestation: str
    evidence_origin: Literal["real_hardware", "synthetic"]
    slo_thresholds_ms: Mapping[str, float]
    contract_version: str = HIL_CONTRACT_VERSION
    additional_serial_ports: tuple[str, ...] = ()
    additional_capture_attestations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "run_id",
            "server_git_sha",
            "firmware_version",
            "policy_hash",
            "temperament_generator_version",
            "serial_port",
            "serial_log",
            "server_log",
            "network_log",
            "capture_attestation",
        ):
            require_text(name, getattr(self, name))
        require_aware_datetime("started_at", self.started_at)
        require_aware_datetime("completed_at", self.completed_at)
        if len(self.identity_bindings) < 2:
            raise ValueError("HIL requires at least two identity bindings")
        if len(self.additional_serial_ports) != len(
            self.additional_capture_attestations
        ):
            raise ValueError("HIL serial ports and capture attestations must align")
        for name, values in (
            ("additional_serial_ports", self.additional_serial_ports),
            (
                "additional_capture_attestations",
                self.additional_capture_attestations,
            ),
        ):
            for value in values:
                require_text(name, value)
        if len(set(self.serial_ports)) != len(self.serial_ports):
            raise ValueError("HIL serial ports must be unique")
        if len(set(self.capture_attestations)) != len(self.capture_attestations):
            raise ValueError("HIL capture attestation paths must be unique")
        for field_name in (
            "device_id",
            "memory_subject_id",
            "pet_id",
            "relationship_epoch_id",
        ):
            values = [
                getattr(binding, field_name) for binding in self.identity_bindings
            ]
            if len(set(values)) != len(values):
                raise ValueError(f"HIL {field_name} values must be unique")
        if self.evidence_origin not in {"real_hardware", "synthetic"}:
            raise ValueError("HIL evidence origin is invalid")
        if any(value <= 0 for value in self.slo_thresholds_ms.values()):
            raise ValueError("HIL SLO thresholds must be positive")
        if re.fullmatch(r"[0-9a-f]{40}", self.server_git_sha) is None:
            raise ValueError("server_git_sha must be a full lowercase Git SHA")
        if re.fullmatch(r"[0-9a-f]{64}", self.policy_hash) is None:
            raise ValueError("policy_hash must be a lowercase SHA-256 digest")
        if self.contract_version != HIL_CONTRACT_VERSION:
            raise ValueError("HIL contract version is unsupported")
        values = (
            self.firmware_version,
            *self.serial_ports,
            *self.capture_attestations,
            *(
                getattr(binding, field)
                for binding in self.identity_bindings
                for field in (
                    "device_id",
                    "memory_subject_id",
                    "pet_id",
                    "relationship_epoch_id",
                )
            ),
        )
        if any("REPLACE_WITH" in value for value in values):
            raise ValueError("HIL manifest still contains template placeholders")

    @property
    def device_ids(self) -> tuple[str, ...]:
        return tuple(binding.device_id for binding in self.identity_bindings)

    @property
    def memory_subject_ids(self) -> tuple[str, ...]:
        return tuple(binding.memory_subject_id for binding in self.identity_bindings)

    @property
    def serial_ports(self) -> tuple[str, ...]:
        return (self.serial_port, *self.additional_serial_ports)

    @property
    def capture_attestations(self) -> tuple[str, ...]:
        return (self.capture_attestation, *self.additional_capture_attestations)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "HILManifest":
        normalized = dict(value)
        raw_bindings = normalized.get("identity_bindings") or ()
        normalized["identity_bindings"] = tuple(
            HILIdentityBinding.from_dict(item)
            for item in raw_bindings  # type: ignore[union-attr]
        )
        normalized["slo_thresholds_ms"] = dict(
            normalized.get("slo_thresholds_ms") or {}
        )
        normalized["additional_serial_ports"] = tuple(
            normalized.get("additional_serial_ports") or ()
        )
        normalized["additional_capture_attestations"] = tuple(
            normalized.get("additional_capture_attestations") or ()
        )
        return cls(**normalized)  # type: ignore[arg-type]


@dataclass(frozen=True)
class HILEvent:
    event_id: str
    path: str
    iteration: int
    occurred_at: str
    device_id: str
    memory_subject_id: str
    pet_id: str
    relationship_epoch_id: str
    policy_hash: str
    expected: str
    observed: str
    outcome: HILOutcome
    state_before_hash: str
    state_after_hash: str
    identity_leak: bool = False
    duplicate_expression: bool = False
    old_state_revived: bool = False
    latency_ms: float | None = None
    slo_key: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "event_id",
            "path",
            "device_id",
            "memory_subject_id",
            "pet_id",
            "relationship_epoch_id",
            "policy_hash",
            "expected",
            "observed",
            "state_before_hash",
            "state_after_hash",
        ):
            require_text(name, getattr(self, name))
        require_aware_datetime("occurred_at", self.occurred_at)
        if self.path not in REQUIRED_HIL_PATHS and self.path != "stability_24h":
            raise ValueError("HIL event path is unknown")
        if self.iteration < 1:
            raise ValueError("HIL event iteration must be positive")
        if self.outcome not in {"PASS", "FAIL"}:
            raise ValueError("HIL event outcome is invalid")
        if (self.latency_ms is None) != (self.slo_key is None):
            raise ValueError("HIL latency and slo_key must be recorded together")
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("HIL latency cannot be negative")

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "HILEvent":
        return cls(**dict(value))  # type: ignore[arg-type]


@dataclass(frozen=True)
class HILLogRecord:
    event_id: str
    source: Literal["serial", "server", "network"]
    occurred_at: str
    device_id: str
    memory_subject_id: str
    pet_id: str
    relationship_epoch_id: str
    client_id: str | None = None
    firmware_version: str | None = None
    server_git_sha: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "event_id",
            "device_id",
            "memory_subject_id",
            "pet_id",
            "relationship_epoch_id",
        ):
            require_text(name, getattr(self, name))
        require_aware_datetime("occurred_at", self.occurred_at)
        if self.source not in {"serial", "server", "network"}:
            raise ValueError("HIL log source is invalid")
        for name in ("client_id", "firmware_version", "server_git_sha"):
            value = getattr(self, name)
            if value is not None:
                require_text(name, value)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "HILLogRecord":
        return cls(**dict(value))  # type: ignore[arg-type]


def _load_json(path: Path) -> Mapping[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _load_jsonl(path: Path, factory: object) -> tuple[object, ...]:
    records: list[object] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name} line {line_number} must be an object")
        records.append(factory.from_dict(value))  # type: ignore[attr-defined]
    return tuple(records)


def _binding_key(value: HILEvent | HILLogRecord) -> tuple[str, str, str, str]:
    return (
        value.device_id,
        value.memory_subject_id,
        value.pet_id,
        value.relationship_epoch_id,
    )


def evaluate_hil_evidence(
    *,
    manifest: HILManifest,
    events: Sequence[HILEvent],
    log_records: Sequence[HILLogRecord],
    attestation: HILCaptureAttestation | None,
    additional_attestations: Sequence[HILCaptureAttestation] = (),
    generated_at: str | None = None,
) -> GateReport:
    generated_at = generated_at or datetime.now().astimezone().isoformat()
    failures: list[str] = []
    p0_failures: list[str] = []
    inconclusive: list[str] = []
    measured_slo_keys: set[str] = set()
    started = datetime.fromisoformat(manifest.started_at)
    completed = datetime.fromisoformat(manifest.completed_at)
    report_time = datetime.fromisoformat(generated_at)
    if completed > report_time:
        failures.append("manifest_completed_in_future")
    if completed <= started:
        failures.append("manifest_window_not_positive")
    valid_bindings = {
        (
            binding.device_id,
            binding.memory_subject_id,
            binding.pet_id,
            binding.relationship_epoch_id,
        )
        for binding in manifest.identity_bindings
    }
    all_attestations = (
        *((attestation,) if attestation is not None else ()),
        *tuple(additional_attestations),
    )
    attested_device_ids: set[str] = set()
    server_correlated_device_ids: set[str] = set()
    server_records = tuple(
        record for record in log_records if record.source == "server"
    )

    if manifest.evidence_origin != "real_hardware":
        inconclusive.append("evidence_origin_not_real_hardware")
    if not all_attestations:
        inconclusive.append("capture_attestation_missing")
    if len(all_attestations) < len(manifest.capture_attestations):
        inconclusive.append("capture_attestation_file_missing")
    for item in all_attestations:
        if item.synthetic:
            inconclusive.append("synthetic_capture_cannot_pass")
        if item.serial_port not in manifest.serial_ports:
            failures.append("attestation_serial_port_mismatch")
        if not item.serial_open_succeeded:
            inconclusive.append("serial_capture_not_confirmed")
        if not item.server_log_stream_succeeded:
            inconclusive.append("server_log_stream_not_confirmed")
        if not item.network_capture_succeeded:
            inconclusive.append("network_capture_not_confirmed")
        if not item.hardware_identity_confirmed:
            inconclusive.append("hardware_identity_not_confirmed")
        if item.hardware_identity_confirmed:
            if item.observed_device_id not in manifest.device_ids:
                failures.append("attestation_device_id_mismatch")
            else:
                attested_device_ids.add(item.observed_device_id)
            if item.observed_firmware_version != manifest.firmware_version:
                failures.append("attestation_firmware_version_mismatch")
            if any(
                record.device_id == item.observed_device_id
                and record.client_id == item.observed_client_id
                and record.firmware_version == item.observed_firmware_version
                and record.server_git_sha == manifest.server_git_sha
                for record in server_records
            ):
                server_correlated_device_ids.add(item.observed_device_id)
            else:
                inconclusive.append(
                    f"server_identity_correlation_missing:{item.observed_device_id}"
                )
        capture_started = datetime.fromisoformat(item.capture_started_at)
        capture_completed = datetime.fromisoformat(item.capture_completed_at)
        if capture_started > started or capture_completed < completed:
            inconclusive.append("attestation_does_not_cover_manifest_window")
    missing_hardware_devices = set(manifest.device_ids) - attested_device_ids
    inconclusive.extend(
        f"hardware_device_coverage_missing:{device_id}"
        for device_id in sorted(missing_hardware_devices)
    )
    if len({item.serial_device_instance_id for item in all_attestations}) < len(
        manifest.device_ids
    ):
        inconclusive.append("physical_device_instance_coverage_missing")
    if not manifest.slo_thresholds_ms:
        inconclusive.append("device_slo_not_frozen")

    records_by_event: dict[str, dict[str, list[HILLogRecord]]] = {}
    for record in log_records:
        sources = records_by_event.setdefault(record.event_id, {})
        sources.setdefault(record.source, []).append(record)
        occurred = datetime.fromisoformat(record.occurred_at)
        if not started <= occurred <= completed:
            failures.append(f"log_outside_window:{record.event_id}:{record.source}")
        if _binding_key(record) not in valid_bindings:
            p0_failures.append(f"log_identity_binding_mismatch:{record.event_id}")

    path_counts = {path: 0 for path in REQUIRED_HIL_PATHS}
    event_ids: set[str] = set()
    path_iterations: set[tuple[str, int]] = set()
    stability_times: list[datetime] = []
    participating_device_ids: set[str] = set()
    isolation_device_ids: set[str] = set()
    for event in events:
        if event.event_id in event_ids:
            failures.append(f"duplicate_evidence_event_id:{event.event_id}")
        event_ids.add(event.event_id)
        if event.path in path_counts:
            path_counts[event.path] += 1
            path_iteration = (event.path, event.iteration)
            if path_iteration in path_iterations:
                failures.append(
                    f"duplicate_path_iteration:{event.path}:{event.iteration}"
                )
            path_iterations.add(path_iteration)
        occurred = datetime.fromisoformat(event.occurred_at)
        if not started <= occurred <= completed:
            failures.append(f"event_outside_window:{event.event_id}")
        if _binding_key(event) not in valid_bindings:
            p0_failures.append(f"event_identity_binding_mismatch:{event.event_id}")
        else:
            participating_device_ids.add(event.device_id)
            if event.path in {"subject_isolation", "device_isolation"}:
                isolation_device_ids.add(event.device_id)
        if event.policy_hash != manifest.policy_hash:
            failures.append(f"policy_hash_mismatch:{event.event_id}")
        if event.outcome == "FAIL" or event.expected != event.observed:
            failures.append(f"path_failed:{event.event_id}")
        if event.identity_leak:
            p0_failures.append(f"identity_leak:{event.event_id}")
        if event.duplicate_expression:
            p0_failures.append(f"duplicate_expression:{event.event_id}")
        if event.old_state_revived:
            p0_failures.append(f"old_state_revived:{event.event_id}")

        correlated = records_by_event.get(event.event_id, {})
        required_sources = {"serial", "server"}
        if event.path in NETWORK_EVIDENCE_PATHS:
            required_sources.add("network")
        for source in required_sources:
            records = correlated.get(source, [])
            if not records:
                inconclusive.append(f"event_not_correlated:{event.event_id}:{source}")
            elif any(_binding_key(record) != _binding_key(event) for record in records):
                p0_failures.append(
                    f"correlated_identity_mismatch:{event.event_id}:{source}"
                )
            elif source == "server":
                recorded_shas = {
                    record.server_git_sha
                    for record in records
                    if record.server_git_sha is not None
                }
                if not recorded_shas:
                    inconclusive.append(f"server_git_sha_missing:{event.event_id}")
                elif manifest.server_git_sha not in recorded_shas:
                    failures.append(f"server_git_sha_mismatch:{event.event_id}")
        if event.slo_key is not None:
            measured_slo_keys.add(event.slo_key)
            threshold = manifest.slo_thresholds_ms.get(event.slo_key)
            if threshold is None:
                inconclusive.append(f"slo_not_frozen:{event.slo_key}")
            elif event.latency_ms is not None and event.latency_ms > threshold:
                failures.append(f"slo_exceeded:{event.event_id}")
        if event.path == "stability_24h":
            stability_times.append(occurred)

    failures.extend(p0_failures)
    for slo_key in manifest.slo_thresholds_ms:
        if slo_key not in measured_slo_keys:
            inconclusive.append(f"slo_measurement_missing:{slo_key}")
    missing_repetitions = tuple(
        f"{path}:{count}/30" for path, count in path_counts.items() if count < 30
    )
    inconclusive.extend(missing_repetitions)
    inconclusive.extend(
        f"device_participation_missing:{device_id}"
        for device_id in sorted(set(manifest.device_ids) - participating_device_ids)
    )
    inconclusive.extend(
        f"device_isolation_evidence_missing:{device_id}"
        for device_id in sorted(set(manifest.device_ids) - isolation_device_ids)
    )

    stability_times.sort()
    stability_duration = (
        (stability_times[-1] - stability_times[0]).total_seconds()
        if len(stability_times) >= 2
        else 0.0
    )
    maximum_gap = max(
        (
            (later - earlier).total_seconds()
            for earlier, later in zip(stability_times, stability_times[1:])
        ),
        default=float("inf"),
    )
    stability_complete = (
        len(stability_times) >= 49
        and stability_duration >= 24 * 60 * 60
        and maximum_gap <= 30 * 60
    )
    if not stability_complete:
        inconclusive.append("24h_stability_event_coverage_missing")

    evidence_status = (
        "FAIL" if failures else ("INCONCLUSIVE" if inconclusive else "PASS")
    )
    real_capture_complete = (
        manifest.evidence_origin == "real_hardware"
        and len(all_attestations) >= len(manifest.device_ids)
        and not missing_hardware_devices
        and server_correlated_device_ids == set(manifest.device_ids)
        and len({item.serial_device_instance_id for item in all_attestations})
        >= len(manifest.device_ids)
        and all(
            not item.synthetic
            and item.hardware_identity_confirmed
            and item.serial_open_succeeded
            and item.server_log_stream_succeeded
            and item.network_capture_succeeded
            for item in all_attestations
        )
    )
    checks = (
        GateCheck(
            "hil-evidence-contract",
            evidence_status,
            (
                "HIL evidence is complete"
                if evidence_status == "PASS"
                else "HIL evidence is incomplete or failed"
            ),
            tuple(dict.fromkeys(failures + inconclusive))[:100],
        ),
        GateCheck(
            "hil-real-capture-attestation",
            ("PASS" if real_capture_complete else "INCONCLUSIVE"),
            "real serial, server, and network capture is attested",
        ),
        GateCheck(
            "hil-p0-zero",
            "FAIL" if p0_failures else "PASS",
            "identity leaks, binding mismatches, duplicate expressions, and old-state revival are zero",
            tuple(dict.fromkeys(p0_failures))[:100],
        ),
        GateCheck(
            "hil-critical-path-repetitions",
            "PASS" if not missing_repetitions else "INCONCLUSIVE",
            "every critical path has at least 30 structured correlated runs",
            missing_repetitions,
        ),
        GateCheck(
            "hil-24h-stability",
            "PASS" if stability_complete else "INCONCLUSIVE",
            f"event-covered stability seconds={stability_duration:.0f}; maximum_gap={maximum_gap:.0f}",
        ),
        GateCheck(
            "hil-device-slo",
            (
                "PASS"
                if manifest.slo_thresholds_ms
                and not any(item.startswith("slo_") for item in failures + inconclusive)
                else (
                    "FAIL"
                    if any(item.startswith("slo_exceeded") for item in failures)
                    else "INCONCLUSIVE"
                )
            ),
            "latency measurements use frozen real-device SLO thresholds",
        ),
    )
    return make_report(
        gate_id="slice13-real-esp32-hil",
        generated_at=generated_at,
        checks=checks,
        metadata={
            "run_id": manifest.run_id,
            "server_git_sha": manifest.server_git_sha,
            "firmware_version": manifest.firmware_version,
            "identity_bindings": tuple(
                {
                    "device_id": binding.device_id,
                    "memory_subject_id": binding.memory_subject_id,
                    "pet_id": binding.pet_id,
                    "relationship_epoch_id": binding.relationship_epoch_id,
                }
                for binding in manifest.identity_bindings
            ),
            "policy_hash": manifest.policy_hash,
            "event_count": len(events),
            "structured_log_record_count": len(log_records),
            "path_counts": path_counts,
        },
    )


def _bundle_member(bundle_dir: Path, relative_name: str) -> Path:
    candidate = (bundle_dir / relative_name).resolve()
    root = bundle_dir.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"HIL evidence path escapes bundle: {relative_name}") from exc
    return candidate


def evaluate_hil_bundle(
    bundle_dir: Path,
    *,
    generated_at: str | None = None,
) -> GateReport:
    generated_at = generated_at or datetime.now().astimezone().isoformat()
    manifest_path = bundle_dir / "manifest.json"
    events_path = bundle_dir / "events.jsonl"
    missing = tuple(
        name
        for name, path in (("manifest", manifest_path), ("events", events_path))
        if not path.is_file()
    )
    if missing:
        return make_report(
            gate_id="slice13-real-esp32-hil",
            generated_at=generated_at,
            checks=(
                GateCheck(
                    "hil-bundle-files",
                    "INCONCLUSIVE",
                    "required HIL bundle files are missing",
                    missing,
                ),
            ),
            metadata={"bundle_dir": str(bundle_dir)},
        )
    try:
        manifest = HILManifest.from_dict(_load_json(manifest_path))
        events = _load_jsonl(events_path, HILEvent)
        log_paths = (
            ("serial", _bundle_member(bundle_dir, manifest.serial_log)),
            ("server", _bundle_member(bundle_dir, manifest.server_log)),
            ("network", _bundle_member(bundle_dir, manifest.network_log)),
        )
        records: list[HILLogRecord] = []
        for expected_source, path in log_paths:
            if path.is_file():
                loaded = _load_jsonl(path, HILLogRecord)
                if any(record.source != expected_source for record in loaded):  # type: ignore[attr-defined]
                    raise ValueError(
                        f"{path.name} contains a record for another log source"
                    )
                records.extend(loaded)  # type: ignore[arg-type]
        attestations: list[HILCaptureAttestation] = []
        for relative_path in manifest.capture_attestations:
            attestation_path = _bundle_member(bundle_dir, relative_path)
            if not attestation_path.is_file():
                continue
            attestation_value = _load_json(attestation_path)
            if attestation_value.get("status") != "INCONCLUSIVE":
                attestations.append(HILCaptureAttestation.from_dict(attestation_value))
    except (TypeError, ValueError, json.JSONDecodeError, OSError) as exc:
        return make_report(
            gate_id="slice13-real-esp32-hil",
            generated_at=generated_at,
            checks=(
                GateCheck(
                    "hil-bundle-parse",
                    "FAIL",
                    "HIL bundle violates the evidence contract",
                    (str(exc),),
                ),
            ),
            metadata={"bundle_dir": str(bundle_dir)},
        )
    return evaluate_hil_evidence(
        manifest=manifest,
        events=events,  # type: ignore[arg-type]
        log_records=records,
        attestation=attestations[0] if attestations else None,
        additional_attestations=attestations[1:],
        generated_at=generated_at,
    )
