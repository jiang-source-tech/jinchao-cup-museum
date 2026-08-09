from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Mapping

from core.xiaoxin.companion.store import SCHEMA_VERSION

from .checkpoint import evaluate_checkpoint_outcomes
from .contracts import (
    GATE_CONTRACT_VERSION,
    GateCheck,
    GateReport,
    canonical_hash,
    make_report,
    require_aware_datetime,
)


ROLLOUT_STAGES = (
    "not_started",
    "schema_backfill_shadow",
    "expression_style_diagnostic",
    "temperament_limited_cohort",
    "adjustment_candidate_only",
    "adjustment_active_limited_cohort",
    "relationship_v2_shadow_compare",
    "relationship_v2_active",
    "narrative_va_limited_cohort",
    "cp06_controls",
    "hil_pass",
    "d7_pilot",
    "d30_controlled_study",
    "d90_confirmation",
)

_SHA1_RE = re.compile(r"[0-9a-f]{40}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_RESEARCH_CHECKS = {
    "d7_pilot": ("research-identification-d7-all",),
    "d30_controlled_study": (
        "research-identification-d30-normal_adaptation",
        "research-d30-adaptation-difference",
    ),
    "d90_confirmation": (
        "research-identification-d90-all",
        "research-d90-longitudinal-retention",
        "research-d90-nine-of-twelve",
        "research-sample-attrition-axis",
    ),
}
_CHECKPOINT_NAMES = {
    "d7_pilot": "D7",
    "d30_controlled_study": "D30",
    "d90_confirmation": "D90",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(
    path: Path | None,
    evidence_name: str,
) -> tuple[Mapping[str, object] | None, GateCheck]:
    if path is None:
        return None, GateCheck(
            f"rollout-{evidence_name}-present",
            "INCONCLUSIVE",
            f"{evidence_name} evidence was not supplied",
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, GateCheck(
            f"rollout-{evidence_name}-present",
            "INCONCLUSIVE",
            f"{evidence_name} evidence could not be read",
            (f"{type(exc).__name__}:{exc}",),
        )
    if not isinstance(value, dict):
        return None, GateCheck(
            f"rollout-{evidence_name}-present",
            "INCONCLUSIVE",
            f"{evidence_name} evidence must be a JSON object",
        )
    return value, GateCheck(
        f"rollout-{evidence_name}-present",
        "PASS",
        f"{evidence_name} evidence was loaded",
        (str(path),),
    )


def _report_check(
    payload: Mapping[str, object] | None,
    *,
    evidence_name: str,
    gate_id: str,
    allow_inconclusive: bool = False,
) -> GateCheck:
    if payload is None:
        return GateCheck(
            f"rollout-{evidence_name}-valid",
            "INCONCLUSIVE",
            f"{evidence_name} report is unavailable",
        )
    if payload.get("gate_id") != gate_id:
        return GateCheck(
            f"rollout-{evidence_name}-valid",
            "FAIL",
            f"{evidence_name} report has the wrong gate id",
            (f"expected:{gate_id}", f"observed:{payload.get('gate_id')}",),
        )
    supplied_digest = payload.get("digest")
    report_body = dict(payload)
    report_body.pop("digest", None)
    calculated_digest = canonical_hash(report_body)
    if supplied_digest != calculated_digest:
        return GateCheck(
            f"rollout-{evidence_name}-valid",
            "FAIL",
            f"{evidence_name} report digest does not match its contents",
            (f"calculated:{calculated_digest}",),
        )
    status = payload.get("status")
    if status not in {"PASS", "FAIL", "INCONCLUSIVE"}:
        return GateCheck(
            f"rollout-{evidence_name}-valid",
            "FAIL",
            f"{evidence_name} report status is invalid",
        )
    raw_checks = payload.get("checks")
    metadata = payload.get("metadata")
    if not isinstance(raw_checks, list) or not isinstance(metadata, dict):
        return GateCheck(
            f"rollout-{evidence_name}-valid",
            "FAIL",
            f"{evidence_name} report checks or metadata are invalid",
        )
    try:
        parsed_checks = []
        for item in raw_checks:
            if not isinstance(item, dict) or not isinstance(
                item.get("evidence", []), list
            ):
                raise ValueError("report check structure is invalid")
            parsed_checks.append(
                GateCheck(
                    check_id=item.get("check_id"),  # type: ignore[arg-type]
                    status=item.get("status"),  # type: ignore[arg-type]
                    detail=item.get("detail"),  # type: ignore[arg-type]
                    evidence=tuple(item.get("evidence", [])),
                )
            )
        reconstructed = GateReport(
            gate_id=payload.get("gate_id"),  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            generated_at=payload.get("generated_at"),  # type: ignore[arg-type]
            checks=tuple(parsed_checks),
            metadata=metadata,
            contract_version=payload.get("contract_version"),  # type: ignore[arg-type]
        )
    except (TypeError, ValueError) as exc:
        return GateCheck(
            f"rollout-{evidence_name}-valid",
            "FAIL",
            f"{evidence_name} report violates the gate contract",
            (f"{type(exc).__name__}:{exc}",),
        )
    if (
        reconstructed.contract_version != GATE_CONTRACT_VERSION
        or reconstructed.digest != supplied_digest
    ):
        return GateCheck(
            f"rollout-{evidence_name}-valid",
            "FAIL",
            f"{evidence_name} report contract version or digest is invalid",
        )
    effective_status = (
        "PASS" if status == "INCONCLUSIVE" and allow_inconclusive else status
    )
    return GateCheck(
        f"rollout-{evidence_name}-valid",
        effective_status,  # type: ignore[arg-type]
        (
            f"{evidence_name} report is valid; checkpoint-specific checks apply"
            if status == "INCONCLUSIVE" and allow_inconclusive
            else f"{evidence_name} report status is {status}"
        ),
        (f"digest:{supplied_digest}",),
    )


def _aware_interval(
    payload: Mapping[str, object],
    start_key: str,
    end_key: str,
) -> bool:
    try:
        start = require_aware_datetime(start_key, payload.get(start_key))  # type: ignore[arg-type]
        end = require_aware_datetime(end_key, payload.get(end_key))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return end >= start


def _chain_check(
    payload: Mapping[str, object] | None,
    *,
    current_stage: str,
    server_git_sha: str,
) -> GateCheck:
    if current_stage == "not_started":
        return GateCheck(
            "rollout-state-chain",
            "PASS" if payload is None else "FAIL",
            "rollout chain starts from the frozen genesis state",
        )
    if payload is None:
        return GateCheck(
            "rollout-state-chain",
            "INCONCLUSIVE",
            "previous successful rollout report is required",
        )
    metadata = payload.get("metadata")
    valid = (
        payload.get("gate_id") == "slice14-controlled-rollout"
        and payload.get("status") == "PASS"
        and isinstance(metadata, dict)
        and metadata.get("target_stage") == current_stage
        and metadata.get("server_git_sha") == server_git_sha
    )
    return GateCheck(
        "rollout-state-chain",
        "PASS" if valid else "FAIL",
        "previous PASS report establishes this revision's current rollout stage"
        if valid
        else "previous report does not establish this revision and current stage",
        (f"current:{current_stage}", f"server_git_sha:{server_git_sha}"),
    )


def _stage_checks(current_stage: str, target_stage: str) -> tuple[GateCheck, ...]:
    if current_stage not in ROLLOUT_STAGES or target_stage not in ROLLOUT_STAGES:
        return (
            GateCheck(
                "rollout-stage-known",
                "FAIL",
                "current and target stages must use the frozen rollout vocabulary",
                (f"current:{current_stage}", f"target:{target_stage}"),
            ),
        )
    current_index = ROLLOUT_STAGES.index(current_stage)
    expected = (
        ROLLOUT_STAGES[current_index + 1]
        if current_index + 1 < len(ROLLOUT_STAGES)
        else None
    )
    return (
        GateCheck(
            "rollout-stage-known",
            "PASS",
            "current and target stages use the frozen rollout vocabulary",
        ),
        GateCheck(
            "rollout-stage-sequence",
            "PASS" if target_stage == expected else "FAIL",
            (
                "target is the single permitted next stage"
                if target_stage == expected
                else "target skips, repeats, or reverses the frozen rollout sequence"
            ),
            (
                f"current:{current_stage}",
                f"target:{target_stage}",
                f"expected:{expected}",
            ),
        ),
    )


def _database_check(database: Path | None) -> GateCheck:
    if database is None:
        return GateCheck(
            "rollout-database-integrity",
            "INCONCLUSIVE",
            "companion SQLite database was not supplied",
        )
    try:
        uri = database.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5.0) as connection:
            schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        return GateCheck(
            "rollout-database-integrity",
            "INCONCLUSIVE",
            "companion SQLite database could not be inspected",
            (f"{type(exc).__name__}:{exc}",),
        )
    valid = (
        schema_version == SCHEMA_VERSION
        and integrity.lower() == "ok"
        and not foreign_keys
    )
    return GateCheck(
        "rollout-database-integrity",
        "PASS" if valid else "FAIL",
        "schema version, integrity, and foreign keys were checked read-only",
        (
            f"schema_version:{schema_version}",
            f"expected_schema_version:{SCHEMA_VERSION}",
            f"integrity:{integrity}",
            f"foreign_key_violations:{len(foreign_keys)}",
        ),
    )


def _backup_restore_check(
    backup: Path | None,
    restore_payload: Mapping[str, object] | None,
) -> GateCheck:
    if backup is None or restore_payload is None:
        return GateCheck(
            "rollout-backup-restorable",
            "INCONCLUSIVE",
            "backup file and restore evidence are both required",
        )
    try:
        digest = _sha256(backup)
    except OSError as exc:
        return GateCheck(
            "rollout-backup-restorable",
            "INCONCLUSIVE",
            "backup file could not be hashed",
            (f"{type(exc).__name__}:{exc}",),
        )
    supplied_digest = restore_payload.get("backup_sha256")
    valid = (
        restore_payload.get("status") == "PASS"
        and supplied_digest == digest
        and _SHA256_RE.fullmatch(str(supplied_digest or "")) is not None
        and restore_payload.get("restored_schema_version") == SCHEMA_VERSION
        and str(restore_payload.get("integrity_check", "")).lower() == "ok"
        and restore_payload.get("foreign_key_violations") == 0
        and _aware_interval(
            restore_payload,
            "restore_started_at",
            "restore_completed_at",
        )
    )
    return GateCheck(
        "rollout-backup-restorable",
        "PASS" if valid else "FAIL",
        "backup digest and isolated restore result were verified",
        (
            f"backup_sha256:{digest}",
            f"restored_schema_version:{restore_payload.get('restored_schema_version')}",
            f"integrity:{restore_payload.get('integrity_check')}",
            f"foreign_key_violations:{restore_payload.get('foreign_key_violations')}",
        ),
    )


def _observation_checks(
    payload: Mapping[str, object] | None,
    *,
    current_stage: str,
    server_git_sha: str,
    backup_digest: str | None,
    matrix_digest: object,
    previous_rollout_digest: object,
) -> tuple[GateCheck, ...]:
    if payload is None:
        return (
            GateCheck(
                "rollout-observation-contract",
                "INCONCLUSIVE",
                "current-stage observation report is unavailable",
            ),
            GateCheck(
                "rollout-p0-zero",
                "INCONCLUSIVE",
                "P0 stop events cannot be evaluated without an observation report",
            ),
        )
    p0_events = payload.get("p0_events")
    p0_valid = isinstance(p0_events, list)
    p0_present = p0_valid and bool(p0_events)
    contract_valid = (
        payload.get("status") == "PASS"
        and payload.get("stage") == current_stage
        and payload.get("server_git_sha") == server_git_sha
        and payload.get("schema_version") == SCHEMA_VERSION
        and payload.get("backup_sha256") == backup_digest
        and payload.get("matrix_report_digest") == matrix_digest
        and payload.get("previous_rollout_report_digest")
        == previous_rollout_digest
        and p0_valid
        and _aware_interval(payload, "started_at", "completed_at")
    )
    observation_status = payload.get("status")
    contract_status = (
        "FAIL"
        if observation_status == "FAIL"
        else ("PASS" if contract_valid else "INCONCLUSIVE")
    )
    return (
        GateCheck(
            "rollout-observation-contract",
            contract_status,  # type: ignore[arg-type]
            "observation report is bound to the current release evidence"
            if contract_valid
            else "observation report is incomplete or not bound to the current evidence",
        ),
        GateCheck(
            "rollout-p0-zero",
            "FAIL" if p0_present else ("PASS" if p0_valid else "INCONCLUSIVE"),
            "P0 stop condition detected"
            if p0_present
            else "no P0 stop event was reported",
            tuple(str(item) for item in p0_events) if p0_valid else (),
        ),
    )


def _research_checkpoint_check(
    payload: Mapping[str, object] | None,
    target_stage: str,
) -> GateCheck:
    required = _RESEARCH_CHECKS[target_stage]
    if payload is None:
        return GateCheck(
            f"rollout-{target_stage}-research",
            "INCONCLUSIVE",
            "the required longitudinal research report is unavailable",
            required,
        )
    raw_checks = payload.get("checks")
    if not isinstance(raw_checks, list):
        return GateCheck(
            f"rollout-{target_stage}-research",
            "INCONCLUSIVE",
            "research report checks are unavailable",
            required,
        )
    statuses = {
        str(item.get("check_id")): item.get("status")
        for item in raw_checks
        if isinstance(item, dict)
    }
    missing = tuple(check_id for check_id in required if check_id not in statuses)
    failed = tuple(
        check_id for check_id in required if statuses.get(check_id) == "FAIL"
    )
    incomplete = tuple(
        check_id
        for check_id in required
        if statuses.get(check_id) not in {"PASS", "FAIL"}
    )
    if failed:
        status = "FAIL"
    elif missing or incomplete:
        status = "INCONCLUSIVE"
    else:
        status = "PASS"
    return GateCheck(
        f"rollout-{target_stage}-research",
        status,  # type: ignore[arg-type]
        f"required {target_stage} preregistered checks were evaluated",
        tuple(
            f"{check_id}:{statuses.get(check_id, 'missing')}"
            for check_id in required
        ),
    )


def _checkpoint_outcome_check(
    payload: Mapping[str, object] | None,
    *,
    target_stage: str,
    server_git_sha: str,
) -> GateCheck:
    if payload is None:
        return GateCheck(
            f"rollout-{target_stage}-outcomes",
            "INCONCLUSIVE",
            "checkpoint outcome report is unavailable",
        )
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return GateCheck(
            f"rollout-{target_stage}-outcomes",
            "INCONCLUSIVE",
            "checkpoint outcome report metadata is unavailable",
        )
    expected_checkpoint = _CHECKPOINT_NAMES[target_stage]
    if (
        metadata.get("checkpoint") != expected_checkpoint
        or metadata.get("server_git_sha") != server_git_sha
    ):
        return GateCheck(
            f"rollout-{target_stage}-outcomes",
            "FAIL",
            "checkpoint outcome report belongs to another milestone or revision",
            (
                f"expected_checkpoint:{expected_checkpoint}",
                f"observed_checkpoint:{metadata.get('checkpoint')}",
                f"expected_sha:{server_git_sha}",
                f"observed_sha:{metadata.get('server_git_sha')}",
            ),
        )
    metrics = metadata.get("metrics")
    if not isinstance(metrics, dict):
        return GateCheck(
            f"rollout-{target_stage}-outcomes",
            "INCONCLUSIVE",
            "checkpoint raw counts, estimates, and confidence intervals are unavailable",
        )
    try:
        expected = evaluate_checkpoint_outcomes(
            checkpoint=expected_checkpoint,
            server_git_sha=server_git_sha,
            participant_count=metadata.get("participant_count"),
            recruited_count=metadata.get("recruited_count"),
            group_assignment_counts=metadata.get("group_assignment_counts"),
            metrics=metrics,
            research_version_binding=metadata.get("research_version_binding"),  # type: ignore[arg-type]
            generated_at=payload.get("generated_at"),  # type: ignore[arg-type]
        )
    except (TypeError, ValueError) as exc:
        return GateCheck(
            f"rollout-{target_stage}-outcomes",
            "FAIL",
            "checkpoint report cannot be reproduced from its raw metrics",
            (f"{type(exc).__name__}:{exc}",),
        )
    if expected.to_dict() != dict(payload):
        return GateCheck(
            f"rollout-{target_stage}-outcomes",
            "FAIL",
            "checkpoint statuses do not match deterministic threshold evaluation",
            (f"expected_digest:{expected.digest}",),
        )
    return GateCheck(
        f"rollout-{target_stage}-outcomes",
        expected.status,
        "checkpoint report exactly matches deterministic threshold evaluation",
        (f"digest:{expected.digest}",),
    )


def _research_version_binding_check(
    research_payload: Mapping[str, object] | None,
    checkpoint_payload: Mapping[str, object] | None,
    *,
    server_git_sha: str,
) -> GateCheck:
    research_metadata = research_payload.get("metadata") if research_payload else None
    checkpoint_metadata = (
        checkpoint_payload.get("metadata") if checkpoint_payload else None
    )
    if not isinstance(research_metadata, dict) or not isinstance(
        checkpoint_metadata, dict
    ):
        return GateCheck(
            "rollout-research-version-binding",
            "INCONCLUSIVE",
            "research and checkpoint version bindings are unavailable",
        )
    research_binding = research_metadata.get("version_binding")
    checkpoint_binding = checkpoint_metadata.get("research_version_binding")
    if not isinstance(research_binding, dict) or not isinstance(
        checkpoint_binding, dict
    ):
        return GateCheck(
            "rollout-research-version-binding",
            "INCONCLUSIVE",
            "research version binding is incomplete",
        )
    valid = (
        research_binding == checkpoint_binding
        and research_binding.get("server_git_sha") == server_git_sha
    )
    return GateCheck(
        "rollout-research-version-binding",
        "PASS" if valid else "FAIL",
        "2AFC and checkpoint evidence use the same frozen release versions"
        if valid
        else "research evidence mixes another server or frozen policy version",
    )


def _server_binding_check(
    payload: Mapping[str, object] | None,
    *,
    evidence_name: str,
    server_git_sha: str,
) -> GateCheck:
    metadata = payload.get("metadata") if payload else None
    if not isinstance(metadata, dict):
        return GateCheck(
            f"rollout-{evidence_name}-server-binding",
            "INCONCLUSIVE",
            f"{evidence_name} report server metadata is unavailable",
        )
    observed_sha = metadata.get("server_git_sha")
    return GateCheck(
        f"rollout-{evidence_name}-server-binding",
        "PASS" if observed_sha == server_git_sha else "FAIL",
        f"{evidence_name} report is bound to the candidate server Git SHA"
        if observed_sha == server_git_sha
        else f"{evidence_name} report belongs to another server revision",
        (f"expected:{server_git_sha}", f"observed:{observed_sha}"),
    )


def evaluate_rollout_transition(
    *,
    current_stage: str,
    target_stage: str,
    server_git_sha: str,
    database: Path | None = None,
    backup: Path | None = None,
    restore_report: Path | None = None,
    matrix_report: Path | None = None,
    observation_report: Path | None = None,
    previous_rollout_report: Path | None = None,
    checkpoint_report: Path | None = None,
    hil_report: Path | None = None,
    research_report: Path | None = None,
    generated_at: str | None = None,
) -> GateReport:
    checks = list(_stage_checks(current_stage, target_stage))
    checks.append(
        GateCheck(
            "rollout-server-git-sha",
            "PASS" if _SHA1_RE.fullmatch(server_git_sha) else "FAIL",
            "candidate server Git SHA is immutable"
            if _SHA1_RE.fullmatch(server_git_sha)
            else "candidate server Git SHA is invalid",
            (f"server_git_sha:{server_git_sha}",),
        )
    )
    checks.append(_database_check(database))

    previous_payload, previous_present = _load_json(
        previous_rollout_report, "previous-rollout"
    )
    if current_stage != "not_started":
        checks.append(previous_present)
        checks.append(
            _report_check(
                previous_payload,
                evidence_name="previous-rollout",
                gate_id="slice14-controlled-rollout",
            )
        )
    checks.append(
        _chain_check(
            previous_payload,
            current_stage=current_stage,
            server_git_sha=server_git_sha,
        )
    )

    restore_payload, restore_present = _load_json(restore_report, "restore")
    checks.append(restore_present)
    checks.append(_backup_restore_check(backup, restore_payload))
    backup_digest = None
    if backup is not None:
        try:
            backup_digest = _sha256(backup)
        except OSError:
            pass

    matrix_payload, matrix_present = _load_json(matrix_report, "matrix")
    checks.append(matrix_present)
    checks.append(
        _report_check(
            matrix_payload,
            evidence_name="matrix",
            gate_id="slice13-policy-matrix",
        )
    )

    observation_payload, observation_present = _load_json(
        observation_report, "observation"
    )
    checks.append(observation_present)
    checks.extend(
        _observation_checks(
            observation_payload,
            current_stage=current_stage,
            server_git_sha=server_git_sha,
            backup_digest=backup_digest,
            matrix_digest=matrix_payload.get("digest") if matrix_payload else None,
            previous_rollout_digest=(
                previous_payload.get("digest") if previous_payload else None
            ),
        )
    )

    if target_stage == "hil_pass":
        hil_payload, hil_present = _load_json(hil_report, "hil")
        checks.append(hil_present)
        checks.append(
            _report_check(
                hil_payload,
                evidence_name="hil",
                gate_id="slice13-real-esp32-hil",
            )
        )
        checks.append(
            _server_binding_check(
                hil_payload,
                evidence_name="hil",
                server_git_sha=server_git_sha,
            )
        )
    if target_stage in _RESEARCH_CHECKS:
        research_payload, research_present = _load_json(research_report, "research")
        checks.append(research_present)
        checks.append(
            _report_check(
                research_payload,
                evidence_name="research",
                gate_id="slice13-longitudinal-research",
                allow_inconclusive=True,
            )
        )
        checks.append(_research_checkpoint_check(research_payload, target_stage))
        checkpoint_payload, checkpoint_present = _load_json(
            checkpoint_report, "checkpoint"
        )
        checks.append(checkpoint_present)
        checks.append(
            _report_check(
                checkpoint_payload,
                evidence_name="checkpoint",
                gate_id="slice14-longitudinal-checkpoint",
            )
        )
        checks.append(
            _checkpoint_outcome_check(
                checkpoint_payload,
                target_stage=target_stage,
                server_git_sha=server_git_sha,
            )
        )
        checks.append(
            _research_version_binding_check(
                research_payload,
                checkpoint_payload,
                server_git_sha=server_git_sha,
            )
        )

    return make_report(
        gate_id="slice14-controlled-rollout",
        generated_at=generated_at or _now(),
        checks=tuple(checks),
        metadata={
            "current_stage": current_stage,
            "target_stage": target_stage,
            "server_git_sha": server_git_sha,
            "schema_version": SCHEMA_VERSION,
            "stage_order": ROLLOUT_STAGES,
        },
    )


def _rollback_window_check(
    payload: Mapping[str, object] | None,
    *,
    server_git_sha: str,
) -> GateCheck:
    if payload is None:
        return GateCheck(
            "rollout-cleanup-rollback-window",
            "INCONCLUSIVE",
            "rollback-window evidence is unavailable",
        )
    incidents = payload.get("p0_events")
    valid = (
        payload.get("status") == "PASS"
        and payload.get("server_git_sha") == server_git_sha
        and payload.get("window_complete") is True
        and incidents == []
        and _aware_interval(payload, "window_started_at", "window_completed_at")
    )
    return GateCheck(
        "rollout-cleanup-rollback-window",
        "PASS" if valid else "FAIL",
        "rollback window completed without P0 events"
        if valid
        else "rollback window is incomplete, unbound, or contains P0 events",
    )


def evaluate_legacy_cleanup(
    *,
    current_stage: str,
    server_git_sha: str,
    database: Path | None = None,
    backup: Path | None = None,
    restore_report: Path | None = None,
    matrix_report: Path | None = None,
    observation_report: Path | None = None,
    previous_rollout_report: Path | None = None,
    checkpoint_report: Path | None = None,
    hil_report: Path | None = None,
    research_report: Path | None = None,
    rollback_report: Path | None = None,
    generated_at: str | None = None,
) -> GateReport:
    checks = [
        GateCheck(
            "rollout-cleanup-stage",
            "PASS" if current_stage == "d90_confirmation" else "FAIL",
            "legacy cleanup is allowed only after D90 confirmation",
            (f"current:{current_stage}",),
        ),
        GateCheck(
            "rollout-server-git-sha",
            "PASS" if _SHA1_RE.fullmatch(server_git_sha) else "FAIL",
            "candidate server Git SHA is immutable"
            if _SHA1_RE.fullmatch(server_git_sha)
            else "candidate server Git SHA is invalid",
            (f"server_git_sha:{server_git_sha}",),
        ),
        _database_check(database),
    ]

    previous_payload, previous_present = _load_json(
        previous_rollout_report, "previous-rollout"
    )
    checks.append(previous_present)
    checks.append(
        _report_check(
            previous_payload,
            evidence_name="previous-rollout",
            gate_id="slice14-controlled-rollout",
        )
    )
    checks.append(
        _chain_check(
            previous_payload,
            current_stage="d90_confirmation",
            server_git_sha=server_git_sha,
        )
    )

    restore_payload, restore_present = _load_json(restore_report, "restore")
    checks.append(restore_present)
    checks.append(_backup_restore_check(backup, restore_payload))
    backup_digest = None
    if backup is not None:
        try:
            backup_digest = _sha256(backup)
        except OSError:
            pass

    matrix_payload, matrix_present = _load_json(matrix_report, "matrix")
    checks.append(matrix_present)
    checks.append(
        _report_check(
            matrix_payload,
            evidence_name="matrix",
            gate_id="slice13-policy-matrix",
        )
    )

    observation_payload, observation_present = _load_json(
        observation_report, "observation"
    )
    checks.append(observation_present)
    checks.extend(
        _observation_checks(
            observation_payload,
            current_stage="d90_confirmation",
            server_git_sha=server_git_sha,
            backup_digest=backup_digest,
            matrix_digest=matrix_payload.get("digest") if matrix_payload else None,
            previous_rollout_digest=(
                previous_payload.get("digest") if previous_payload else None
            ),
        )
    )

    hil_payload, hil_present = _load_json(hil_report, "hil")
    checks.append(hil_present)
    checks.append(
        _report_check(
            hil_payload,
            evidence_name="hil",
            gate_id="slice13-real-esp32-hil",
        )
    )
    checks.append(
        _server_binding_check(
            hil_payload,
            evidence_name="hil",
            server_git_sha=server_git_sha,
        )
    )

    research_payload, research_present = _load_json(research_report, "research")
    checks.append(research_present)
    checks.append(
        _report_check(
            research_payload,
            evidence_name="research",
            gate_id="slice13-longitudinal-research",
            allow_inconclusive=True,
        )
    )
    checks.append(
        _research_checkpoint_check(research_payload, "d90_confirmation")
    )

    checkpoint_payload, checkpoint_present = _load_json(
        checkpoint_report, "checkpoint"
    )
    checks.append(checkpoint_present)
    checks.append(
        _report_check(
            checkpoint_payload,
            evidence_name="checkpoint",
            gate_id="slice14-longitudinal-checkpoint",
        )
    )
    checks.append(
        _checkpoint_outcome_check(
            checkpoint_payload,
            target_stage="d90_confirmation",
            server_git_sha=server_git_sha,
        )
    )
    checks.append(
        _research_version_binding_check(
            research_payload,
            checkpoint_payload,
            server_git_sha=server_git_sha,
        )
    )

    rollback_payload, rollback_present = _load_json(rollback_report, "rollback")
    checks.append(rollback_present)
    checks.append(
        _rollback_window_check(rollback_payload, server_git_sha=server_git_sha)
    )
    return make_report(
        gate_id="slice14-legacy-cleanup",
        generated_at=generated_at or _now(),
        checks=tuple(checks),
        metadata={
            "current_stage": current_stage,
            "server_git_sha": server_git_sha,
            "schema_version": SCHEMA_VERSION,
            "authorization_only": True,
            "protected_records": (
                "birth_temperament",
                "migration_audit",
            ),
        },
    )
