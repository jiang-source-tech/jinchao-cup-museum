from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Literal, Mapping


GateStatus = Literal["PASS", "FAIL", "INCONCLUSIVE"]
GATE_CONTRACT_VERSION = "xiaoxin-individuality-gate-v1"


def canonical_json(value: object) -> str:
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def require_aware_datetime(name: str, value: str) -> datetime:
    require_text(name, value)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone offset")
    return parsed


@dataclass(frozen=True)
class GateCheck:
    check_id: str
    status: GateStatus
    detail: str
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_text("check_id", self.check_id)
        require_text("detail", self.detail)
        if self.status not in {"PASS", "FAIL", "INCONCLUSIVE"}:
            raise ValueError("gate check status is invalid")
        if any(not isinstance(item, str) or not item.strip() for item in self.evidence):
            raise ValueError("gate check evidence must contain non-empty strings")


@dataclass(frozen=True)
class GateReport:
    gate_id: str
    status: GateStatus
    generated_at: str
    checks: tuple[GateCheck, ...]
    metadata: Mapping[str, object] = field(default_factory=dict)
    contract_version: str = GATE_CONTRACT_VERSION

    def __post_init__(self) -> None:
        require_text("gate_id", self.gate_id)
        require_aware_datetime("generated_at", self.generated_at)
        if self.status not in {"PASS", "FAIL", "INCONCLUSIVE"}:
            raise ValueError("gate report status is invalid")
        if not self.checks:
            raise ValueError("gate report must contain at least one check")
        if self.status != aggregate_status(self.checks):
            raise ValueError("gate report status does not match its checks")

    @property
    def digest(self) -> str:
        return canonical_hash(self)

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["digest"] = self.digest
        return value

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def aggregate_status(checks: tuple[GateCheck, ...]) -> GateStatus:
    if any(check.status == "FAIL" for check in checks):
        return "FAIL"
    if any(check.status == "INCONCLUSIVE" for check in checks):
        return "INCONCLUSIVE"
    return "PASS"


def make_report(
    *,
    gate_id: str,
    generated_at: str,
    checks: tuple[GateCheck, ...],
    metadata: Mapping[str, object] | None = None,
) -> GateReport:
    return GateReport(
        gate_id=gate_id,
        status=aggregate_status(checks),
        generated_at=generated_at,
        checks=checks,
        metadata=dict(metadata or {}),
    )
