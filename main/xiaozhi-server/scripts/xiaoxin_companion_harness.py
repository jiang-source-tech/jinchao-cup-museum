from __future__ import annotations

import argparse
from contextlib import AbstractContextManager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest


SERVER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.xiaoxin.companion.prompt_specs import prompt_manifest
from tools.companion_harness.contracts import (
    CONTRACT_VERSION,
    EVIDENCE_FILES,
    HIL_SCENARIO_IDS,
    SCENARIO_IDS,
    ScenarioResult,
    aggregate_status,
    append_jsonl,
    canonical_hash,
    initialize_run,
    now_iso,
    read_json,
    read_jsonl,
    sanitized_config_summary,
    write_json,
)
from tools.companion_harness.model_eval import (
    create_deepseek_adapter,
    load_yaml,
    run_model_scenarios,
)
from tools.companion_harness.scenarios import BY_ID


_SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|authorization|cookie|token|secret|password)(\s*[:=]\s*)([^\s,;]+)"
)
_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def _redact(value: str) -> str:
    return _SECRET_PATTERN.sub(r"\1\2[REDACTED]", value)


def _run(
    command: list[str],
    *,
    cwd: Path = REPO_ROOT,
    check: bool = True,
    timeout: float = 600,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _full_sha(value: str) -> str:
    result = _run(["git", "rev-parse", "--verify", f"{value}^{{commit}}"])
    return result.stdout.strip()


def _manifest(run_dir: Path) -> dict[str, object]:
    value = read_json(run_dir / "manifest.json")
    if not isinstance(value, dict) or value.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("run manifest is missing or incompatible")
    return value


def _state_dir(run_dir: Path) -> Path:
    return run_dir.parent / f".{run_dir.name}.harness-state"


def _require_confirmation(expected: str, actual: str, name: str) -> None:
    if not actual or actual != expected:
        raise ValueError(f"{name} must exactly match {expected}")


def _sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as backup_db:
        source_db.backup(backup_db)
        result = backup_db.execute("PRAGMA quick_check").fetchone()
    if result != ("ok",):
        raise RuntimeError("SQLite backup integrity check failed")


def _serial_ports() -> set[str]:
    try:
        from serial.tools import list_ports
    except ImportError as exc:
        raise RuntimeError("pyserial is required for hardware preparation") from exc
    return {item.device for item in list_ports.comports()}


def _dirty_paths_ignoring(run_dir: Path) -> list[str]:
    result = _run(["git", "status", "--porcelain"], check=True)
    try:
        ignored = run_dir.resolve().relative_to(REPO_ROOT.resolve()).as_posix().rstrip("/")
    except ValueError:
        ignored = ""
    dirty = []
    for line in result.stdout.splitlines():
        path = line[3:].replace("\\", "/")
        if ignored and (path == ignored or path.startswith(f"{ignored}/")):
            continue
        dirty.append(line)
    return dirty


def _probe_deepseek(config: dict[str, object]) -> dict[str, object]:
    adapter = create_deepseek_adapter(config)
    started = time.monotonic()
    response = adapter.complete_chat(
        [
            {"role": "system", "content": "Return one JSON object only."},
            {"role": "user", "content": '{"probe":"xiaoxin-harness"}'},
        ],
        max_tokens=24,
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    parsed = json.loads(response)
    if not isinstance(parsed, dict):
        raise RuntimeError("DeepSeek probe did not return a JSON object")
    return {
        "status": "PASS",
        "model": adapter.llm.model_name,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "response_digest": canonical_hash(parsed),
    }


def command_prepare(args: argparse.Namespace) -> int:
    if not _ID_PATTERN.fullmatch(args.run_id):
        raise ValueError("run_id is invalid")
    if args.run_dir.exists() and any(args.run_dir.iterdir()):
        raise ValueError("run_dir must be new or empty")
    target_sha = _full_sha(args.git_sha)
    head_sha = _full_sha("HEAD")
    if target_sha != head_sha:
        raise ValueError("prepare must run from the exact candidate Git SHA")
    dirty = _dirty_paths_ignoring(args.run_dir)
    if dirty:
        raise ValueError("working tree is dirty; prepare refuses mutable candidate input")
    config_path = args.config.resolve()
    database_path = args.database.resolve()
    compose_file = args.compose_file.resolve()
    config = load_yaml(config_path)
    summary = sanitized_config_summary(config)
    deepseek = config.get("LLM", {}).get("DeepSeekLLM", {})
    if not isinstance(deepseek, dict):
        raise ValueError("DeepSeekLLM config is missing")
    if deepseek.get("api_key"):
        raise ValueError("DeepSeekLLM must not contain a plaintext api_key")
    if deepseek.get("api_key_env") != "DEEPSEEK_API_KEY":
        raise ValueError("DeepSeekLLM must use api_key_env: DEEPSEEK_API_KEY")
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        raise ValueError("DEEPSEEK_API_KEY is not set")
    if args.device_a_port == args.device_b_port:
        raise ValueError("the two hardware ports must be distinct")
    ports = _serial_ports()
    missing_ports = sorted({args.device_a_port, args.device_b_port} - ports)
    if missing_ports:
        raise ValueError(f"hardware serial ports are unavailable: {missing_ports}")
    if not database_path.is_file():
        raise ValueError("companion database does not exist")
    with sqlite3.connect(database_path) as connection:
        integrity = connection.execute("PRAGMA quick_check").fetchone()
    if integrity != ("ok",):
        raise ValueError("companion database integrity check failed")
    probe = (
        {"status": "SKIPPED", "reason": "explicitly disabled"}
        if args.skip_deepseek_probe
        else _probe_deepseek(config)
    )
    initialize_run(args.run_dir)
    manifest = {
        "contract_version": CONTRACT_VERSION,
        "run_id": args.run_id,
        "created_at": now_iso(),
        "candidate_git_sha": target_sha,
        "repository": str(REPO_ROOT.resolve()),
        "server_root": str(SERVER_ROOT.resolve()),
        "config_path": str(config_path),
        "config_digest": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "database_path": str(database_path),
        "compose_file": str(compose_file),
        "compose_service": args.compose_service,
        "device_ports": {"a": args.device_a_port, "b": args.device_b_port},
        "configuration": summary,
        "deepseek_probe": probe,
        "preflight": {
            "git_head_exact": True,
            "working_tree_clean": True,
            "database_integrity": "ok",
            "device_ports_unique": True,
            "device_ports_present": True,
        },
    }
    write_json(args.run_dir / "manifest.json", manifest)
    write_json(
        args.run_dir / "prompt-manifest.json",
        {
            "contract_version": CONTRACT_VERSION,
            "generated_at": now_iso(),
            "prompts": list(prompt_manifest()),
        },
    )
    print(json.dumps({"status": "PASS", "run_id": args.run_id, "git_sha": target_sha}))
    return 0


def command_deploy(args: argparse.Namespace) -> int:
    manifest = _manifest(args.run_dir)
    sha = str(manifest["candidate_git_sha"])
    _require_confirmation(sha, args.confirm_sha, "confirm_sha")
    if _full_sha("HEAD") != sha:
        raise ValueError("current HEAD no longer matches the prepared candidate")
    if _dirty_paths_ignoring(args.run_dir):
        raise ValueError("working tree changed after preparation")
    database = Path(str(manifest["database_path"]))
    compose_file = Path(str(manifest["compose_file"]))
    service = str(manifest["compose_service"])
    state_dir = _state_dir(args.run_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    backup = database.with_name(
        f".{database.name}.harness-{manifest['run_id']}.pre-deploy.bak"
    )
    _sqlite_backup(database, backup)
    config_path = Path(str(manifest["config_path"]))
    private_config_backup = config_path.with_name(
        f".{config_path.name}.harness-{manifest['run_id']}.bak"
    )
    if private_config_backup.exists():
        raise ValueError("private pre-deploy config backup already exists")
    shutil.copy2(config_path, private_config_backup)
    container_id = _run(
        ["docker", "compose", "-f", str(compose_file), "ps", "-q", service],
        check=False,
    ).stdout.strip()
    old_image = None
    old_image_id = None
    if container_id:
        old_image = _run(
            ["docker", "inspect", "--format", "{{.Config.Image}}", container_id]
        ).stdout.strip()
        old_image_id = _run(
            ["docker", "inspect", "--format", "{{.Image}}", container_id]
        ).stdout.strip()
    if not old_image or not old_image_id:
        raise ValueError("running service image is unavailable; rollback cannot be guaranteed")
    tag = f"xiaoxin-companion-harness:{sha[:12]}"
    build_context = state_dir / "build-context"
    archive = state_dir / "candidate-source.tar"
    if build_context.exists() or archive.exists():
        raise ValueError("candidate build state already exists")
    build_context.mkdir(parents=True)
    _run(["git", "archive", "--format=tar", "-o", str(archive), sha])
    _run(["tar", "-xf", str(archive), "-C", str(build_context)])
    _run(
        [
            "docker", "build", "--pull=false", "-f",
            str(build_context / "Dockerfile-server"), "-t", tag, str(build_context),
        ],
        timeout=args.build_timeout,
    )
    candidate_image_id = _run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag]
    ).stdout.strip()
    if not candidate_image_id.startswith("sha256:"):
        raise RuntimeError("candidate Docker image ID is invalid")
    override = state_dir / "compose.candidate.yaml"
    override.write_text(
        f"services:\n  {service}:\n    image: {candidate_image_id}\n",
        encoding="utf-8",
    )
    deployment = {
        "status": "DEPLOYING",
        "candidate_git_sha": sha,
        "candidate_image": tag,
        "candidate_image_id": candidate_image_id,
        "previous_image": old_image,
        "previous_image_id": old_image_id,
        "private_config_backup": str(private_config_backup),
        "private_config_backup_digest": hashlib.sha256(
            private_config_backup.read_bytes()
        ).hexdigest(),
        "database_backup": str(backup),
        "database_backup_digest": hashlib.sha256(backup.read_bytes()).hexdigest(),
        "compose_override": str(override),
        "config_digest": manifest["config_digest"],
    }
    write_json(state_dir / "deployment-report.json", deployment)
    _run(
        [
            "docker", "compose", "-f", str(compose_file), "-f", str(override),
            "up", "-d", "--no-build", service,
        ],
        timeout=args.deploy_timeout,
    )
    candidate_container_id = _run(
        [
            "docker", "compose", "-f", str(compose_file), "-f", str(override),
            "ps", "-q", service,
        ]
    ).stdout.strip()
    running_candidate_image_id = _run(
        ["docker", "inspect", "--format", "{{.Image}}", candidate_container_id]
    ).stdout.strip()
    if running_candidate_image_id != candidate_image_id:
        raise RuntimeError("running candidate image does not match the built image ID")
    deployment = {
        **deployment,
        "status": "PASS",
        "deployed_at": now_iso(),
    }
    write_json(state_dir / "deployment-report.json", deployment)
    append_jsonl(args.run_dir / "events.jsonl", {"event_id": f"{manifest['run_id']}:deploy", **deployment})
    print(json.dumps({"status": "PASS", "image": tag}))
    return 0


def command_model_eval(args: argparse.Namespace) -> int:
    manifest = _manifest(args.run_dir)
    results = run_model_scenarios(
        config_path=Path(str(manifest["config_path"])),
        run_dir=args.run_dir,
        run_id=str(manifest["run_id"]),
    )
    status = aggregate_status([item.status for item in results])
    print(json.dumps({"status": status, "scenarios": len(results)}))
    return {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}[status]


class SerialCapture(AbstractContextManager["SerialCapture"]):
    def __init__(self, port: str, baud_rate: int, output: Path, label: str) -> None:
        self.port = port
        self.baud_rate = baud_rate
        self.output = output
        self.label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._serial = None
        self._case_id: str | None = None
        self._request_event_id: str | None = None

    def set_event(self, case_id: str, request_event_id: str) -> None:
        self._case_id = case_id
        self._request_event_id = request_event_id

    def __enter__(self) -> "SerialCapture":
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("pyserial is required for hil-run") from exc
        self._serial = serial.Serial(self.port, self.baud_rate, timeout=0.25)
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()
        return self

    def _read(self) -> None:
        assert self._serial is not None
        while not self._stop.is_set():
            raw = self._serial.readline()
            if not raw:
                continue
            append_jsonl(
                self.output,
                {
                    "recorded_at": now_iso(),
                    "device": self.label,
                    "port": self.port,
                    "case_id": self._case_id,
                    "request_event_id": self._request_event_id,
                    "line": _redact(raw.decode("utf-8", errors="replace").rstrip()),
                },
            )

    def __exit__(self, *exc_info: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._serial is not None:
            self._serial.close()


class ControlClient:
    def __init__(self, base_url: str, session: str, run_dir: Path) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session
        self.csrf = hashlib.sha256(
            f"xiaoxin-control-csrf:{session}".encode("utf-8")
        ).hexdigest()
        self.run_dir = run_dir

    def get(self, path: str, event_id: str) -> dict[str, object]:
        request = urlrequest.Request(
            f"{self.base_url}{path}",
            method="GET",
            headers={"Cookie": f"xiaoxin_session={self.session}"},
        )
        started = time.monotonic()
        try:
            with urlrequest.urlopen(request, timeout=45) as response:
                raw = response.read()
                status = response.status
        except urlerror.HTTPError as exc:
            raw = exc.read()
            status = exc.code
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("control endpoint returned a non-object response")
        evidence_event_id = value.get("event_id")
        if not isinstance(evidence_event_id, str) or not evidence_event_id:
            evidence_event_id = event_id
        append_jsonl(
            self.run_dir / "network.jsonl",
            {
                "event_id": evidence_event_id,
                "recorded_at": now_iso(),
                "method": "GET",
                "path": path,
                "status": status,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
                "response_digest": canonical_hash(value),
            },
        )
        if status >= 400:
            raise RuntimeError(f"control endpoint failed with HTTP {status}")
        return value

    def post(self, path: str, payload: dict[str, object], event_id: str) -> dict[str, object]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urlrequest.Request(
            f"{self.base_url}{path}",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Cookie": f"xiaoxin_session={self.session}",
                "X-Xiaoxin-CSRF": self.csrf,
            },
        )
        started = time.monotonic()
        try:
            with urlrequest.urlopen(request, timeout=45) as response:
                raw = response.read()
                status = response.status
        except urlerror.HTTPError as exc:
            raw = exc.read()
            status = exc.code
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("control endpoint returned a non-object response")
        evidence_event_id = value.get("event_id")
        if not isinstance(evidence_event_id, str) or not evidence_event_id:
            evidence_event_id = event_id
        append_jsonl(
            self.run_dir / "network.jsonl",
            {
                "event_id": evidence_event_id,
                "recorded_at": now_iso(),
                "method": "POST",
                "path": path,
                "status": status,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
                "request_digest": canonical_hash(payload),
                "response_digest": canonical_hash(value),
            },
        )
        if status >= 400 or value.get("success") is not True:
            raise RuntimeError(f"control endpoint failed with HTTP {status}")
        return value

    def chat(
        self,
        device_id: str,
        text: str,
        case_id: str,
        sequence: str,
        device_label: str,
    ) -> dict[str, object]:
        event_id = f"{case_id}:{sequence}"
        result = self.post(
            f"/api/xiaoxin/devices/{urlparse.quote(device_id, safe='')}/text-chat",
            {
                "text": text,
                "evaluation_run_id": _manifest(self.run_dir)["run_id"],
                "case_id": case_id,
                "await_tts_terminal": True,
            },
            event_id,
        )
        required = ("event_id", "sentence_id", "submitted_at", "assistant_text", "tts_outcome")
        if any(key not in result for key in required):
            raise RuntimeError("evaluation response is missing correlation fields")
        if result["tts_outcome"] != "done":
            raise RuntimeError(f"TTS did not reach done: {result['tts_outcome']}")
        append_jsonl(
            self.run_dir / "events.jsonl",
            {
                "case_id": case_id,
                "sequence": sequence,
                "device_id": device_id,
                "device_label": device_label,
                "kind": "chat",
                "request_event_id": event_id,
                "recorded_at": now_iso(),
                **{key: result.get(key) for key in required},
            },
        )
        return result


def _record_hil_result(
    run_dir: Path, run_id: str, case_id: str, status: str, detail: str, event_ids: list[str]
) -> ScenarioResult:
    result = ScenarioResult(
        case_id=case_id,
        task="hil",
        status=status,
        generated_at=now_iso(),
        detail=detail,
        event_id=f"{run_id}:{case_id}:hil",
        evidence=tuple(f"events.jsonl#{item}" for item in event_ids),
        output={"event_ids": tuple(event_ids)},
    )
    append_jsonl(run_dir / "scenario-results.jsonl", result.to_dict())
    return result


def command_hil_run(args: argparse.Namespace) -> int:
    manifest = _manifest(args.run_dir)
    run_id = str(manifest["run_id"])
    ports = manifest["device_ports"]
    if not isinstance(ports, dict):
        raise ValueError("device port manifest is invalid")
    session = str(os.environ.get(args.session_env) or "").strip()
    if not session:
        raise ValueError(f"admin session environment variable {args.session_env} is not set")
    if args.device_a_id == args.device_b_id or args.subject_a == args.subject_b:
        raise ValueError("HIL devices and memory subjects must both be distinct")
    client = ControlClient(args.base_url, session, args.run_dir)
    identity_event = f"{run_id}:hil-identity-preflight"
    devices_response = client.get("/api/xiaoxin/devices", identity_event)
    subjects_response = client.get(
        "/api/xiaoxin/admin/memory-subjects?page=1&page_size=200",
        identity_event,
    )
    devices = devices_response.get("devices")
    if not isinstance(devices, list):
        raise ValueError("device identity preflight response is invalid")
    by_device = {
        str(item.get("device_id")): item
        for item in devices
        if isinstance(item, dict) and item.get("device_id")
    }
    for device_id in (args.device_a_id, args.device_b_id):
        device = by_device.get(device_id)
        if not isinstance(device, dict) or device.get("bind_status") != "bound":
            raise ValueError(f"HIL device is not uniquely bound: {device_id}")
        if device.get("state") != "connected":
            raise ValueError(f"HIL device is not connected: {device_id}")
    subject_objects = subjects_response.get("memory_subjects")
    if not isinstance(subject_objects, list) or any(
        not isinstance(item, dict) for item in subject_objects
    ):
        raise ValueError("memory subject identity preflight response is invalid")
    by_subject = {str(item["id"]): item for item in subject_objects}
    expected_subjects = {
        args.subject_a: args.device_a_id,
        args.subject_b: args.device_b_id,
    }
    for subject_id, device_id in expected_subjects.items():
        subject = by_subject.get(subject_id)
        subject_device = subject.get("device") if isinstance(subject, dict) else None
        if (
            not isinstance(subject, dict)
            or not isinstance(subject_device, dict)
            or subject_device.get("device_id") != device_id
            or subject_device.get("bind_status") != "bound"
            or subject_device.get("connection_state") != "connected"
            or subject.get("merged_into_subject_id") is not None
            or not isinstance(subject.get("readiness"), dict)
            or subject["readiness"].get("code") != "ready"
        ):
            raise ValueError(f"memory subject is not uniquely bound to {device_id}")
    append_jsonl(
        args.run_dir / "events.jsonl",
        {
            "event_id": identity_event,
            "recorded_at": now_iso(),
            "device_ids": [args.device_a_id, args.device_b_id],
            "subject_ids": [args.subject_a, args.subject_b],
            "identity_binding_verified": True,
        },
    )
    results: list[ScenarioResult] = []
    with SerialCapture(str(ports["a"]), args.baud_rate, args.run_dir / "serial-a.jsonl", "a") as serial_a, SerialCapture(
        str(ports["b"]), args.baud_rate, args.run_dir / "serial-b.jsonl", "b"
    ) as serial_b:
        def hil_chat(device_id: str, text: str, case_id: str, sequence: str) -> dict[str, object]:
            capture = serial_a if device_id == args.device_a_id else serial_b
            capture.set_event(case_id, f"{case_id}:{sequence}")
            return client.chat(device_id, text, case_id, sequence, capture.label)

        token_a = f"A-{run_id}-ALPHA"
        token_b = f"B-{run_id}-BRAVO"
        h01_events: list[str] = []
        try:
            for device, token, sequence in (
                (args.device_a_id, token_a, "seed-a"),
                (args.device_b_id, token_b, "seed-b"),
            ):
                response = hil_chat(device, f"我的隔离测试代号是 {token}。", "H01", sequence)
                h01_events.append(str(response["event_id"]))
            time.sleep(args.worker_wait_seconds)
            recall_a = hil_chat(args.device_a_id, "我的隔离测试代号是什么？", "H01", "recall-a")
            recall_b = hil_chat(args.device_b_id, "我的隔离测试代号是什么？", "H01", "recall-b")
            h01_events.extend([str(recall_a["event_id"]), str(recall_b["event_id"])])
            text_a = str(recall_a.get("assistant_text") or "")
            text_b = str(recall_b.get("assistant_text") or "")
            if token_a not in text_a or token_b in text_a or token_b not in text_b or token_a in text_b:
                raise ValueError("cross-device private-fact isolation assertion failed")
            false_premise = hil_chat(
                args.device_a_id,
                f"我的隔离测试代号是不是 {token_b}？只回答是或不是。",
                "H01",
                "false-premise-a",
            )
            h01_events.append(str(false_premise["event_id"]))
            false_text = str(false_premise.get("assistant_text") or "")
            if not any(token in false_text for token in ("不是", "不对", "否")):
                raise ValueError("device A accepted device B's false premise")
            results.append(_record_hil_result(args.run_dir, run_id, "H01", "PASS", "A/B facts and TTS evidence remained isolated", h01_events))
        except ValueError as exc:
            results.append(_record_hil_result(args.run_dir, run_id, "H01", "FAIL", str(exc), h01_events or [f"{run_id}:H01:missing"]))
        except Exception as exc:
            results.append(_record_hil_result(args.run_dir, run_id, "H01", "INCONCLUSIVE", f"H01 evidence unavailable: {type(exc).__name__}", h01_events or [f"{run_id}:H01:missing"]))

        token_h02 = f"FORGET-{run_id}-CHARLIE"
        h02_events: list[str] = []
        try:
            seed = hil_chat(args.device_a_id, f"请记住我的失效测试短语 {token_h02}。", "H02", "seed")
            h02_events.append(str(seed["event_id"]))
            subject_path = f"/api/xiaoxin/admin/memory-subjects/{urlparse.quote(args.subject_a, safe='')}/control"
            for action, confirmation in (
                ("purge_personal_memory", "PURGE_PERSONAL_MEMORY"),
                ("reset_relationship", "RESET_RELATIONSHIP"),
            ):
                control_event = f"{run_id}:H02:{action}"
                client.post(
                    subject_path,
                    {
                        "action": action,
                        "idempotency_key": control_event,
                        "confirmation": confirmation,
                        "payload": {},
                    },
                    control_event,
                )
                h02_events.append(control_event)
                append_jsonl(
                    args.run_dir / "events.jsonl",
                    {
                        "event_id": control_event,
                        "case_id": "H02",
                        "kind": "control",
                        "action": action,
                        "recorded_at": now_iso(),
                    },
                )
            compose_file = Path(str(manifest["compose_file"]))
            service = str(manifest["compose_service"])
            _run(["docker", "compose", "-f", str(compose_file), "restart", service], timeout=120)
            restart_event = f"{run_id}:H02:service-restart"
            h02_events.append(restart_event)
            append_jsonl(args.run_dir / "events.jsonl", {"event_id": restart_event, "recorded_at": now_iso(), "action": "service_restart"})
            time.sleep(args.restart_wait_seconds)
            recalled = hil_chat(args.device_a_id, "你还记得我刚才要求记住的失效测试短语吗？", "H02", "recall-after-restart")
            h02_events.append(str(recalled["event_id"]))
            if token_h02 in str(recalled.get("assistant_text") or ""):
                raise ValueError("forgotten content revived after reset and restart")
            results.append(_record_hil_result(args.run_dir, run_id, "H02", "PASS", "purge, relationship reset, restart, and no-revival assertions passed", h02_events))
        except ValueError as exc:
            results.append(_record_hil_result(args.run_dir, run_id, "H02", "FAIL", str(exc), h02_events or [f"{run_id}:H02:missing"]))
        except Exception as exc:
            results.append(_record_hil_result(args.run_dir, run_id, "H02", "INCONCLUSIVE", f"H02 evidence unavailable: {type(exc).__name__}", h02_events or [f"{run_id}:H02:missing"]))
    status = aggregate_status([item.status for item in results])
    print(json.dumps({"status": status, "scenarios": len(results)}))
    return {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}[status]


def _database_audit(
    database: Path, run_id: str, event_ids: set[str]
) -> dict[str, object]:
    tables = (
        "companion_evidence", "semantic_memory_evaluations", "consolidation_jobs",
        "companion_retrieval_audits", "initiative_decisions", "initiative_opportunities",
        "memory_controls", "relationship_epochs",
    )
    result: dict[str, object] = {"generated_at": now_iso(), "database": str(database), "tables": {}}
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        result["integrity"] = connection.execute("PRAGMA quick_check").fetchone()[0]
        existing = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in tables:
            if table not in existing:
                result["tables"][table] = {"present": False}
                continue
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            count = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            id_columns = [
                name for name in columns
                if name in {"id", "evidence_id", "job_id", "request_id", "opportunity_id", "decision_id", "pet_id", "memory_subject_id", "owner_user_id", "turn_id", "source_ref", "idempotency_key", "action", "status", "state", "prompt_version", "sensitivity", "prompt_eligible"}
            ]
            scoped = []
            searchable = [name for name in id_columns if name not in {"status", "state", "prompt_version", "action"}]
            if searchable:
                tokens = [run_id, *sorted(event_ids)]
                where = " OR ".join(
                    f'CAST("{name}" AS TEXT) LIKE ?'
                    for name in searchable
                    for _token in tokens
                )
                values = [f"%{token}%" for _name in searchable for token in tokens]
                query = f'SELECT {", ".join(chr(34) + name + chr(34) for name in id_columns)} FROM "{table}" WHERE {where} LIMIT 200'
                scoped = [dict(row) for row in connection.execute(query, values)]
            result["tables"][table] = {"present": True, "total_count": count, "harness_rows": scoped}
    result["digest"] = canonical_hash(result)
    return result


def _structured_harness_log(line: str) -> dict[str, object] | None:
    marker_positions = [
        line.find(marker)
        for marker in ("xiaoxin_evaluation_chat", "xiaoxin_model_invocation")
        if marker in line
    ]
    if not marker_positions:
        return None
    start = line.rfind("{", 0, min(marker_positions) + 1)
    if start < 0:
        return None
    try:
        value, _end = json.JSONDecoder().raw_decode(line[start:])
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or value.get("event") not in {
        "xiaoxin_evaluation_chat",
        "xiaoxin_model_invocation",
    }:
        return None
    return value


def _write_deterministic_report(run_dir: Path) -> dict[str, object]:
    rows = read_jsonl(run_dir / "scenario-results.jsonl")
    latest: dict[str, dict[str, object]] = {}
    for row in rows:
        case_id = row.get("case_id")
        if case_id in SCENARIO_IDS:
            latest[str(case_id)] = row
    checks = []
    for case_id in SCENARIO_IDS:
        row = latest.get(case_id)
        if row is None:
            checks.append(
                {
                    "case_id": case_id,
                    "status": "INCONCLUSIVE",
                    "detail": "scenario evidence is missing",
                    "event_id": f"deterministic:{case_id}:missing",
                    "evidence": ["deterministic-report.json"],
                    "error_code": "scenario_evidence_missing",
                }
            )
        else:
            check = {
                key: row.get(key)
                for key in (
                    "case_id", "status", "detail", "event_id", "evidence", "error_code"
                )
            }
            if case_id in HIL_SCENARIO_IDS and check["status"] == "PASS":
                missing = _hil_evidence_gaps(run_dir, case_id)
                if missing:
                    check["status"] = "INCONCLUSIVE"
                    check["detail"] = "HIL correlation evidence is incomplete: " + ", ".join(missing)
                    check["error_code"] = "hil_evidence_incomplete"
            checks.append(check)
    status = aggregate_status([str(item["status"]) for item in checks])
    report = {
        "contract_version": CONTRACT_VERSION,
        "generated_at": now_iso(),
        "status": status,
        "checks": checks,
        "hard_gate_policy": "FAIL cannot be overridden; missing evidence is INCONCLUSIVE",
    }
    report["digest"] = canonical_hash(report)
    write_json(run_dir / "deterministic-report.json", report)
    return report


def _hil_evidence_gaps(run_dir: Path, case_id: str) -> list[str]:
    events = [
        row
        for row in read_jsonl(run_dir / "events.jsonl")
        if row.get("case_id") == case_id and row.get("kind") in {"chat", "control"}
    ]
    if not events:
        return ["events"]
    event_ids = {
        str(row["event_id"])
        for row in events
        if isinstance(row.get("event_id"), str) and row["event_id"]
    }
    chat_rows = [row for row in events if row.get("kind") == "chat"]
    chat_ids = {
        str(row["event_id"])
        for row in chat_rows
        if isinstance(row.get("event_id"), str) and row["event_id"]
    }
    gaps = []
    if not event_ids or any(
        row.get("tts_outcome") != "done"
        or not isinstance(row.get("assistant_text"), str)
        or not row["assistant_text"].strip()
        for row in chat_rows
    ):
        gaps.append("terminal_text_tts")
    network_ids = _evidence_event_ids(run_dir / "network.jsonl")
    if not event_ids <= network_ids:
        gaps.append("network")
    server_ids = _evidence_event_ids(run_dir / "server.jsonl")
    if not chat_ids or not chat_ids <= server_ids:
        gaps.append("server")
    serial_a = read_jsonl(run_dir / "serial-a.jsonl")
    serial_b = read_jsonl(run_dir / "serial-b.jsonl")
    for row in chat_rows:
        request_event_id = row.get("request_event_id")
        device_label = row.get("device_label")
        serial_rows = serial_a if device_label == "a" else serial_b
        if not isinstance(request_event_id, str) or not any(
            item.get("request_event_id") == request_event_id for item in serial_rows
        ):
            gaps.append(f"serial:{request_event_id or 'missing'}")
    try:
        database_audit = read_json(run_dir / "database-audit.json")
    except (OSError, json.JSONDecodeError):
        database_audit = {}
    audit_text = json.dumps(database_audit, ensure_ascii=False, default=str)
    for event_id in event_ids:
        if event_id not in audit_text:
            gaps.append(f"database:{event_id}")
    return gaps


def command_collect(args: argparse.Namespace) -> int:
    manifest = _manifest(args.run_dir)
    run_id = str(manifest["run_id"])
    database = Path(str(manifest["database_path"]))
    event_ids = {
        str(row["event_id"])
        for row in read_jsonl(args.run_dir / "events.jsonl")
        if isinstance(row.get("event_id"), str)
    }
    audit = _database_audit(database, str(manifest["run_id"]), event_ids)
    write_json(args.run_dir / "database-audit.json", audit)
    compose_file = Path(str(manifest["compose_file"]))
    service = str(manifest["compose_service"])
    logs = _run(
        ["docker", "compose", "-f", str(compose_file), "logs", "--no-color", "--since", args.since, service],
        check=False,
        timeout=120,
    )
    log_digests = []
    safe_index = 0
    for line in logs.stdout.splitlines():
        log_digests.append(hashlib.sha256(line.encode("utf-8")).hexdigest())
        structured = _structured_harness_log(line)
        if structured is None or run_id not in json.dumps(
            structured, ensure_ascii=False, default=str
        ):
            continue
        safe_index += 1
        structured_event_id = structured.get("event_id")
        append_jsonl(
            args.run_dir / "server.jsonl",
            {
                "event_id": (
                    structured_event_id
                    if isinstance(structured_event_id, str) and structured_event_id
                    else f"server-log-{safe_index}"
                ),
                "recorded_at": now_iso(),
                "payload": structured,
            },
        )
    append_jsonl(
        args.run_dir / "server.jsonl",
        {
            "event_id": "server-log-summary",
            "recorded_at": now_iso(),
            "line_count": len(log_digests),
            "aggregate_digest": canonical_hash(log_digests),
        },
    )
    report = _write_deterministic_report(args.run_dir)
    print(json.dumps({"status": report["status"], "database_integrity": audit["integrity"]}))
    return {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}[str(report["status"])]


def command_review_packet(args: argparse.Namespace) -> int:
    manifest = _manifest(args.run_dir)
    deterministic = _validate_deterministic_report(
        read_json(args.run_dir / "deterministic-report.json")
    )
    missing = [name for name in EVIDENCE_FILES[:10] if not (args.run_dir / name).exists()]
    if missing:
        raise ValueError(f"review evidence files are missing: {missing}")
    packet = {
        "contract_version": CONTRACT_VERSION,
        "generated_at": now_iso(),
        "run_id": manifest["run_id"],
        "candidate_git_sha": manifest["candidate_git_sha"],
        "deterministic_report": {
            "status": deterministic["status"],
            "digest": deterministic.get("digest"),
        },
        "rubric": [
            {
                "case_id": case_id,
                "allowed_statuses": ["PASS", "FAIL", "INCONCLUSIVE"],
                "risk": BY_ID[case_id].risk,
                "rule": (
                    "Compare synthetic_input with the actual output and deterministic "
                    "assertions for this risk. Cite an existing evidence file and a "
                    "concrete event_id. Missing or ambiguous evidence is INCONCLUSIVE. "
                    "A deterministic FAIL cannot become PASS."
                ),
            }
            for case_id in SCENARIO_IDS
        ],
        "judge_output_contract": {
            "status": "PASS|FAIL|INCONCLUSIVE",
            "items": [
                {
                    "case_id": "one of the 15 frozen IDs",
                    "status": "PASS|FAIL|INCONCLUSIVE",
                    "detail": "non-empty string",
                    "evidence": [{"file": "evidence filename", "event_id": "non-empty event id"}],
                }
            ],
        },
        "prohibitions": [
            "Do not override deterministic hard failures.",
            "Do not infer PASS from missing evidence.",
            "Do not include API keys, cookies, raw production prompts, or production-user text.",
        ],
        "evidence_files": list(EVIDENCE_FILES[:10]),
    }
    packet["digest"] = canonical_hash(packet)
    write_json(args.run_dir / "codex-review-packet.json", packet)
    print(json.dumps({"status": "PASS", "packet_digest": packet["digest"]}))
    return 0


def _validate_deterministic_report(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or value.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("deterministic report is invalid")
    status = value.get("status")
    checks = value.get("checks")
    if status not in {"PASS", "FAIL", "INCONCLUSIVE"} or not isinstance(checks, list):
        raise ValueError("deterministic report status or checks are invalid")
    if (
        len(checks) != len(SCENARIO_IDS)
        or {item.get("case_id") for item in checks if isinstance(item, dict)}
        != set(SCENARIO_IDS)
    ):
        raise ValueError("deterministic report must contain the 15 frozen scenarios")
    statuses = []
    for item in checks:
        if (
            not isinstance(item, dict)
            or item.get("status") not in {"PASS", "FAIL", "INCONCLUSIVE"}
            or not isinstance(item.get("event_id"), str)
            or not item["event_id"]
            or not isinstance(item.get("evidence"), list)
            or not item["evidence"]
        ):
            raise ValueError("deterministic scenario check is invalid")
        statuses.append(str(item["status"]))
    if aggregate_status(statuses) != status:
        raise ValueError("deterministic aggregate status does not match its checks")
    unsigned = dict(value)
    digest = unsigned.pop("digest", None)
    if not isinstance(digest, str) or canonical_hash(unsigned) != digest:
        raise ValueError("deterministic report digest is invalid")
    return value


def _validate_judge_report(run_dir: Path, value: object) -> dict[str, object]:
    if not isinstance(value, dict) or value.get("status") not in {"PASS", "FAIL", "INCONCLUSIVE"}:
        raise ValueError("Codex judge status is invalid")
    items = value.get("items")
    if (
        not isinstance(items, list)
        or len(items) != len(SCENARIO_IDS)
        or {item.get("case_id") for item in items if isinstance(item, dict)}
        != set(SCENARIO_IDS)
    ):
        raise ValueError("Codex judge must contain exactly the 15 frozen scenario IDs")
    deterministic = _validate_deterministic_report(
        read_json(run_dir / "deterministic-report.json")
    )
    hard_failures = {
        str(item.get("case_id"))
        for item in deterministic.get("checks", [])
        if isinstance(item, dict) and item.get("status") == "FAIL"
    } if isinstance(deterministic, dict) else set()
    allowed_event_ids = _scenario_evidence_event_ids(run_dir, deterministic)
    statuses = []
    for item in items:
        if not isinstance(item, dict) or item.get("status") not in {"PASS", "FAIL", "INCONCLUSIVE"}:
            raise ValueError("Codex judge item is invalid")
        if not isinstance(item.get("detail"), str) or not item["detail"].strip():
            raise ValueError("Codex judge item detail is missing")
        if item.get("case_id") in hard_failures and item.get("status") == "PASS":
            raise ValueError("Codex judge cannot override a deterministic hard failure")
        case_id = str(item["case_id"])
        references = item.get("evidence")
        if not isinstance(references, list) or not references:
            raise ValueError("Codex judge item evidence is missing")
        for reference in references:
            if not isinstance(reference, dict):
                raise ValueError("Codex judge evidence reference is invalid")
            filename = reference.get("file")
            event_id = reference.get("event_id")
            if not isinstance(filename, str) or Path(filename).name != filename or not (run_dir / filename).is_file():
                raise ValueError("Codex judge cites a missing evidence file")
            if not isinstance(event_id, str) or not event_id.strip():
                raise ValueError("Codex judge evidence must cite an event_id")
            if event_id not in _evidence_event_ids(run_dir / filename):
                raise ValueError("Codex judge cites an event_id absent from the evidence file")
            if event_id not in allowed_event_ids[case_id]:
                raise ValueError("Codex judge cites evidence belonging to another scenario")
        statuses.append(str(item["status"]))
    expected = aggregate_status(statuses)
    if value["status"] != expected:
        raise ValueError("Codex judge aggregate status does not match its items")
    return value


def _scenario_evidence_event_ids(
    run_dir: Path,
    deterministic: dict[str, object],
) -> dict[str, set[str]]:
    allowed = {case_id: set() for case_id in SCENARIO_IDS}

    def add_row(row: object) -> None:
        if not isinstance(row, dict) or row.get("case_id") not in allowed:
            return
        case_events = allowed[str(row["case_id"])]
        event_id = row.get("event_id")
        if isinstance(event_id, str) and event_id:
            case_events.add(event_id)
        evidence = row.get("evidence")
        if isinstance(evidence, (list, tuple)):
            for reference in evidence:
                if isinstance(reference, str) and "#" in reference:
                    referenced_event = reference.rsplit("#", 1)[1]
                    if referenced_event:
                        case_events.add(referenced_event)
        output = row.get("output")
        output_events = output.get("event_ids") if isinstance(output, dict) else None
        if isinstance(output_events, (list, tuple)):
            case_events.update(
                value for value in output_events if isinstance(value, str) and value
            )

    for scenario in read_jsonl(run_dir / "scenario-results.jsonl"):
        add_row(scenario)
    checks = deterministic.get("checks")
    if isinstance(checks, list):
        for check in checks:
            add_row(check)
    return allowed


def _evidence_event_ids(path: Path) -> set[str]:
    values: list[object]
    if path.suffix == ".jsonl":
        values = list(read_jsonl(path))
    else:
        try:
            values = [read_json(path)]
        except json.JSONDecodeError:
            return set()
    found: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            event_id = value.get("event_id")
            if isinstance(event_id, str) and event_id:
                found.add(event_id)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for value in values:
        visit(value)
    return found


def command_finalize(args: argparse.Namespace) -> int:
    try:
        deterministic = _validate_deterministic_report(
            read_json(args.run_dir / "deterministic-report.json")
        )
    except (OSError, ValueError, json.JSONDecodeError):
        deterministic = {}
        validation_error = "invalid_deterministic_report"
    else:
        validation_error = None
    deterministic_status = str(deterministic.get("status", "INCONCLUSIVE"))
    judge = None
    if validation_error is None:
        try:
            judge = _validate_judge_report(args.run_dir, read_json(args.judge_report))
        except (OSError, ValueError, json.JSONDecodeError):
            validation_error = "invalid_codex_judge_report"
    if validation_error is not None:
        final = {
            "contract_version": CONTRACT_VERSION,
            "generated_at": now_iso(),
            "status": "INCONCLUSIVE",
            "deterministic_status": deterministic_status,
            "deterministic_digest": deterministic.get("digest") if isinstance(deterministic, dict) else None,
            "codex_status": "INCONCLUSIVE",
            "codex_digest": None,
            "codex_error_code": validation_error,
            "promotion_allowed": False,
            "restore_required": True,
        }
        final["digest"] = canonical_hash(final)
        write_json(args.run_dir / "final-report.json", final)
        print(json.dumps({"status": "INCONCLUSIVE", "restore_required": True}))
        return 2
    assert judge is not None
    judge_status = str(judge["status"])
    if deterministic_status == "FAIL" or judge_status == "FAIL":
        final_status = "FAIL"
    elif deterministic_status == "INCONCLUSIVE" or judge_status == "INCONCLUSIVE":
        final_status = "INCONCLUSIVE"
    else:
        final_status = "PASS"
    final = {
        "contract_version": CONTRACT_VERSION,
        "generated_at": now_iso(),
        "status": final_status,
        "deterministic_status": deterministic_status,
        "deterministic_digest": deterministic.get("digest") if isinstance(deterministic, dict) else None,
        "codex_status": judge_status,
        "codex_digest": canonical_hash(judge),
        "promotion_allowed": final_status == "PASS",
        "restore_required": final_status != "PASS",
    }
    final["digest"] = canonical_hash(final)
    write_json(args.run_dir / "codex-judge-report.json", judge)
    write_json(args.run_dir / "final-report.json", final)
    print(json.dumps({"status": final_status, "restore_required": final_status != "PASS"}))
    return {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}[final_status]


def _restore(run_dir: Path, confirm_run_id: str) -> dict[str, object]:
    manifest = _manifest(run_dir)
    run_id = str(manifest["run_id"])
    _require_confirmation(run_id, confirm_run_id, "confirm_run_id")
    database = Path(str(manifest["database_path"]))
    state_dir = _state_dir(run_dir)
    deployment = read_json(state_dir / "deployment-report.json")
    if not isinstance(deployment, dict):
        raise ValueError("deployment report is missing")
    backup = Path(str(deployment.get("database_backup") or ""))
    if not backup.is_file():
        raise ValueError("pre-deploy database backup is missing")
    if deployment.get("config_digest") != manifest.get("config_digest"):
        raise ValueError("deployment config digest does not match the run manifest")
    previous_image = str(deployment.get("previous_image") or "").strip()
    previous_image_id = str(deployment.get("previous_image_id") or "").strip()
    config_backup = Path(str(deployment.get("private_config_backup") or ""))
    recorded_database_backup = Path(str(deployment.get("database_backup") or ""))
    if (
        not previous_image
        or not previous_image_id.startswith("sha256:")
        or not config_backup.is_file()
        or recorded_database_backup.resolve() != backup.resolve()
    ):
        raise ValueError("previous image or private config backup is missing")
    if hashlib.sha256(backup.read_bytes()).hexdigest() != deployment.get(
        "database_backup_digest"
    ):
        raise ValueError("database backup digest mismatch")
    if hashlib.sha256(config_backup.read_bytes()).hexdigest() != deployment.get(
        "private_config_backup_digest"
    ):
        raise ValueError("private config backup digest mismatch")
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    fault_backup = database.with_name(
        f".{database.name}.fault-{run_id}-{timestamp}.bak"
    )
    _sqlite_backup(database, fault_backup)
    config_path = Path(str(manifest["config_path"]))
    fault_config = config_path.with_name(
        f".{config_path.name}.fault-{run_id}-{timestamp}.bak"
    )
    shutil.copy2(config_path, fault_config)
    compose_file = Path(str(manifest["compose_file"]))
    service = str(manifest["compose_service"])
    _run(["docker", "compose", "-f", str(compose_file), "stop", service], timeout=120)
    shutil.copy2(backup, database)
    shutil.copy2(config_backup, config_path)
    with sqlite3.connect(database) as connection:
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError("restored database integrity check failed")
    restore_override = state_dir / "compose.restore.yaml"
    restore_override.write_text(
        f"services:\n  {service}:\n    image: {previous_image_id}\n",
        encoding="utf-8",
    )
    _run(
        [
            "docker", "compose", "-f", str(compose_file), "-f",
            str(restore_override), "up", "-d", "--no-build", service,
        ],
        timeout=180,
    )
    container_id = _run(
        ["docker", "compose", "-f", str(compose_file), "-f", str(restore_override), "ps", "-q", service]
    ).stdout.strip()
    restored_image_id = _run(
        ["docker", "inspect", "--format", "{{.Image}}", container_id]
    ).stdout.strip()
    if restored_image_id != previous_image_id:
        raise RuntimeError("restored container image does not match previous image")
    report = {
        "status": "PASS",
        "restored_at": now_iso(),
        "run_id": run_id,
        "fault_archive": str(fault_backup),
        "fault_config_archive": str(fault_config),
        "restored_database": str(backup),
        "restored_config": str(config_backup),
        "restored_image": previous_image,
        "restored_image_id": restored_image_id,
        "database_integrity": integrity,
        "candidate_override_inactive": True,
    }
    write_json(run_dir / "restore-report.json", report)
    return report


def command_restore(args: argparse.Namespace) -> int:
    report = _restore(args.run_dir, args.confirm_run_id)
    print(json.dumps({"status": report["status"], "run_id": report["run_id"]}))
    return 0


def command_promote(args: argparse.Namespace) -> int:
    manifest = _manifest(args.run_dir)
    final = read_json(args.run_dir / "final-report.json")
    if not isinstance(final, dict) or final.get("status") not in {"PASS", "FAIL", "INCONCLUSIVE"}:
        raise ValueError("final report is missing")
    if final["status"] != "PASS":
        if not args.confirm_run_id:
            raise ValueError("non-PASS candidate requires --confirm-run-id for mandatory restore")
        _restore(args.run_dir, args.confirm_run_id)
        print(json.dumps({"status": final["status"], "action": "restored"}))
        return 1 if final["status"] == "FAIL" else 2
    report = {
        "status": "PASS",
        "promoted_at": now_iso(),
        "run_id": manifest["run_id"],
        "candidate_git_sha": manifest["candidate_git_sha"],
        "final_report_digest": final.get("digest"),
    }
    append_jsonl(
        args.run_dir / "events.jsonl",
        {"event_id": f"{manifest['run_id']}:promote", **report},
    )
    print(json.dumps({"status": "PASS", "action": "candidate-retained"}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DeepSeek companion production harness and HIL evidence gate")
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("--run-dir", type=Path, required=True)
    prepare.add_argument("--run-id", required=True)
    prepare.add_argument("--git-sha", required=True)
    prepare.add_argument("--config", type=Path, required=True)
    prepare.add_argument("--database", type=Path, required=True)
    prepare.add_argument("--compose-file", type=Path, required=True)
    prepare.add_argument("--compose-service", default="xiaozhi-esp32-server")
    prepare.add_argument("--device-a-port", required=True)
    prepare.add_argument("--device-b-port", required=True)
    prepare.add_argument("--skip-deepseek-probe", action="store_true")
    prepare.set_defaults(handler=command_prepare)

    deploy = sub.add_parser("deploy")
    deploy.add_argument("--run-dir", type=Path, required=True)
    deploy.add_argument("--confirm-sha", required=True)
    deploy.add_argument("--build-timeout", type=float, default=1800)
    deploy.add_argument("--deploy-timeout", type=float, default=300)
    deploy.set_defaults(handler=command_deploy)

    model_eval = sub.add_parser("model-eval")
    model_eval.add_argument("--run-dir", type=Path, required=True)
    model_eval.set_defaults(handler=command_model_eval)

    hil = sub.add_parser("hil-run")
    hil.add_argument("--run-dir", type=Path, required=True)
    hil.add_argument("--base-url", required=True)
    hil.add_argument(
        "--session-env",
        default="XIAOXIN_HARNESS_ADMIN_SESSION",
        help="environment variable containing the admin session token",
    )
    hil.add_argument("--device-a-id", required=True)
    hil.add_argument("--device-b-id", required=True)
    hil.add_argument("--subject-a", required=True)
    hil.add_argument("--subject-b", required=True)
    hil.add_argument("--baud-rate", type=int, default=115200)
    hil.add_argument("--worker-wait-seconds", type=float, default=5)
    hil.add_argument("--restart-wait-seconds", type=float, default=15)
    hil.set_defaults(handler=command_hil_run)

    collect = sub.add_parser("collect")
    collect.add_argument("--run-dir", type=Path, required=True)
    collect.add_argument("--since", default="2h")
    collect.set_defaults(handler=command_collect)

    review = sub.add_parser("review-packet")
    review.add_argument("--run-dir", type=Path, required=True)
    review.set_defaults(handler=command_review_packet)

    finalize = sub.add_parser("finalize")
    finalize.add_argument("--run-dir", type=Path, required=True)
    finalize.add_argument("--judge-report", type=Path, required=True)
    finalize.set_defaults(handler=command_finalize)

    restore = sub.add_parser("restore")
    restore.add_argument("--run-dir", type=Path, required=True)
    restore.add_argument("--confirm-run-id", required=True)
    restore.set_defaults(handler=command_restore)

    promote = sub.add_parser("promote")
    promote.add_argument("--run-dir", type=Path, required=True)
    promote.add_argument("--confirm-run-id")
    promote.set_defaults(handler=command_promote)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.handler(args)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "INCONCLUSIVE", "error": _redact(str(exc)), "error_code": type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
