from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Literal, Mapping


Status = Literal["PASS", "FAIL", "INCONCLUSIVE"]
CONTRACT_VERSION = "xiaoxin-companion-harness-v1"
SCENARIO_IDS = (
    "M01", "M02", "M03", "M04", "M05", "M06", "M07", "M08",
    "M09", "M10", "R01", "R02", "I01", "H01", "H02",
)
MODEL_SCENARIO_IDS = SCENARIO_IDS[:13]
HIL_SCENARIO_IDS = SCENARIO_IDS[13:]
EVIDENCE_FILES = (
    "manifest.json",
    "prompt-manifest.json",
    "scenario-results.jsonl",
    "model-invocations.jsonl",
    "events.jsonl",
    "serial-a.jsonl",
    "serial-b.jsonl",
    "server.jsonl",
    "network.jsonl",
    "database-audit.json",
    "deterministic-report.json",
    "codex-review-packet.json",
    "codex-judge-report.json",
    "final-report.json",
    "restore-report.json",
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def canonical_json(value: object) -> str:
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def canonical_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def append_jsonl(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(canonical_json(value) + "\n")


def read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number} must be a JSON object")
        rows.append(value)
    return rows


def initialize_run(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in EVIDENCE_FILES:
        path = run_dir / name
        if path.exists():
            continue
        if name.endswith(".jsonl"):
            path.write_text("", encoding="utf-8")
        else:
            write_json(path, {})


def aggregate_status(statuses: list[str]) -> Status:
    if any(status == "FAIL" for status in statuses):
        return "FAIL"
    if any(status == "INCONCLUSIVE" for status in statuses):
        return "INCONCLUSIVE"
    return "PASS"


@dataclass(frozen=True)
class ScenarioResult:
    case_id: str
    task: str
    status: Status
    generated_at: str
    detail: str
    event_id: str
    evidence: tuple[str, ...]
    output_digest: str | None = None
    output: object | None = None
    error_code: str | None = None
    contract_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.case_id not in SCENARIO_IDS:
            raise ValueError("unknown harness scenario")
        if self.status not in {"PASS", "FAIL", "INCONCLUSIVE"}:
            raise ValueError("invalid scenario status")
        if not self.event_id or not self.detail or not self.evidence:
            raise ValueError("scenario evidence is incomplete")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def sanitized_config_summary(config: Mapping[str, object]) -> dict[str, object]:
    selected = config.get("selected_module")
    runtime = config.get("xiaoxin_runtime")
    llms = config.get("LLM")
    deepseek = llms.get("DeepSeekLLM", {}) if isinstance(llms, Mapping) else {}
    env_name = deepseek.get("api_key_env") if isinstance(deepseek, Mapping) else None
    return {
        "selected_module": dict(selected) if isinstance(selected, Mapping) else {},
        "companion_worker_llm": (
            runtime.get("companion_worker_llm") if isinstance(runtime, Mapping) else None
        ),
        "deepseek": {
            "base_url": deepseek.get("base_url") or deepseek.get("url"),
            "model_name": deepseek.get("model_name"),
            "credential_source": f"env:{env_name}" if env_name else "invalid",
        },
    }
