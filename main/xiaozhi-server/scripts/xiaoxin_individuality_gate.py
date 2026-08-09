from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from tools.individuality_validation.hil import evaluate_hil_bundle
from tools.individuality_validation.checkpoint import evaluate_checkpoint_outcomes
from tools.individuality_validation.capture import (
    collect_hardware_attestation,
    finalize_hardware_attestation,
)
from tools.individuality_validation.matrix import run_policy_matrix_gate
from tools.individuality_validation.research import (
    StudyResponse,
    evaluate_research_results,
    research_contract_hash,
    research_contract_payload,
)
from tools.individuality_validation.rollout import (
    ROLLOUT_STAGES,
    evaluate_legacy_cleanup,
    evaluate_rollout_transition,
)


def _write(value: object, output: Path | None) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if output is None:
        print(payload, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload, encoding="utf-8")


def _exit_code(status: str) -> int:
    return {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}[status]


def _matrix(args: argparse.Namespace) -> int:
    report = run_policy_matrix_gate(replay_count=args.replays)
    _write(report.to_dict(), args.output)
    return _exit_code(report.status)


def _hil(args: argparse.Namespace) -> int:
    report = evaluate_hil_bundle(args.bundle)
    _write(report.to_dict(), args.output)
    return _exit_code(report.status)


def _research_contract(args: argparse.Namespace) -> int:
    payload = research_contract_payload()
    payload["contract_hash"] = research_contract_hash()
    payload["generated_at"] = datetime.now().astimezone().isoformat()
    _write(payload, args.output)
    return 0


def _research_results(args: argparse.Namespace) -> int:
    responses = tuple(
        StudyResponse(**json.loads(line))
        for line in args.responses.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    assignments_value = json.loads(args.assignments.read_text(encoding="utf-8"))
    if not isinstance(assignments_value, dict):
        raise ValueError("research assignments must be a JSON object")
    report = evaluate_research_results(
        responses=responses,
        assignments=assignments_value,
        generated_at=datetime.now().astimezone().isoformat(),
        collection_complete=args.collection_complete,
        bootstrap_iterations=args.bootstrap_iterations,
        version_binding={
            "server_git_sha": args.server_git_sha,
            "policy_hash": args.policy_hash,
            "prompt_hash": args.prompt_hash,
            "temperament_generator_version": args.temperament_generator_version,
            "va_config_hash": args.va_config_hash,
        },
    )
    _write(report.to_dict(), args.output)
    return _exit_code(report.status)


def _checkpoint_results(args: argparse.Namespace) -> int:
    value = json.loads(args.input.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("metrics"), dict)
        or not isinstance(value.get("research_version_binding"), dict)
    ):
        raise ValueError(
            "checkpoint input must contain metrics and research_version_binding objects"
        )
    report = evaluate_checkpoint_outcomes(
        checkpoint=value.get("checkpoint"),
        server_git_sha=value.get("server_git_sha"),
        participant_count=value.get("participant_count"),
        recruited_count=value.get("recruited_count"),
        group_assignment_counts=value.get("group_assignment_counts"),
        metrics=value["metrics"],
        research_version_binding=value.get("research_version_binding"),
    )
    _write(report.to_dict(), args.output)
    return _exit_code(report.status)


def _hil_attest(args: argparse.Namespace) -> int:
    try:
        attestation = collect_hardware_attestation(
            serial_port=args.serial_port,
            baud_rate=args.baud_rate,
            server_host=args.server_host,
            server_port=args.server_port,
            output=args.output,
            timeout_seconds=args.timeout,
            attestation_method=args.attestation_method,
            reset_device=not args.no_reset_device,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        _write(
            {
                "status": "INCONCLUSIVE",
                "generated_at": datetime.now().astimezone().isoformat(),
                "check_id": "hil-real-device-attestation",
                "detail": str(exc),
                "serial_port": args.serial_port,
                "server_endpoint": f"{args.server_host}:{args.server_port}",
            },
            args.output,
        )
        return _exit_code("INCONCLUSIVE")
    _write({"status": "PASS", "attestation": attestation.__dict__}, None)
    return 0


def _hil_finalize_attestation(args: argparse.Namespace) -> int:
    attestations = finalize_hardware_attestation(bundle_dir=args.bundle)
    _write(
        {
            "status": "PASS",
            "attestations": [item.__dict__ for item in attestations],
        },
        None,
    )
    return 0


def _rollout(args: argparse.Namespace) -> int:
    report = evaluate_rollout_transition(
        current_stage=args.current_stage,
        target_stage=args.target_stage,
        server_git_sha=args.server_git_sha,
        database=args.database,
        backup=args.backup,
        restore_report=args.restore_report,
        matrix_report=args.matrix_report,
        observation_report=args.observation_report,
        previous_rollout_report=args.previous_rollout_report,
        checkpoint_report=args.checkpoint_report,
        hil_report=args.hil_report,
        research_report=args.research_report,
    )
    _write(report.to_dict(), args.output)
    return _exit_code(report.status)


def _rollout_cleanup(args: argparse.Namespace) -> int:
    report = evaluate_legacy_cleanup(
        current_stage=args.current_stage,
        server_git_sha=args.server_git_sha,
        database=args.database,
        backup=args.backup,
        restore_report=args.restore_report,
        matrix_report=args.matrix_report,
        observation_report=args.observation_report,
        previous_rollout_report=args.previous_rollout_report,
        checkpoint_report=args.checkpoint_report,
        hil_report=args.hil_report,
        research_report=args.research_report,
        rollback_report=args.rollback_report,
    )
    _write(report.to_dict(), args.output)
    return _exit_code(report.status)


def _add_rollout_evidence_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--server-git-sha", required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--restore-report", type=Path)
    parser.add_argument("--matrix-report", type=Path)
    parser.add_argument("--observation-report", type=Path)
    parser.add_argument("--previous-rollout-report", type=Path)
    parser.add_argument("--checkpoint-report", type=Path)
    parser.add_argument("--hil-report", type=Path)
    parser.add_argument("--research-report", type=Path)
    parser.add_argument("--output", type=Path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run offline Xiaoxin individuality and real-device evidence gates."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    matrix = subparsers.add_parser("matrix", help="run the 243 temperament matrix")
    matrix.add_argument("--replays", type=int, default=20)
    matrix.add_argument("--output", type=Path)
    matrix.set_defaults(handler=_matrix)

    hil = subparsers.add_parser("hil", help="validate a real ESP32 evidence bundle")
    hil.add_argument("--bundle", type=Path, required=True)
    hil.add_argument("--output", type=Path)
    hil.set_defaults(handler=_hil)

    attest = subparsers.add_parser(
        "hil-attest",
        help="observe a connected ESP32 and attest candidate-server reachability",
    )
    attest.add_argument("--serial-port", required=True)
    attest.add_argument("--baud-rate", type=int, default=115200)
    attest.add_argument("--server-host", required=True)
    attest.add_argument("--server-port", type=int, required=True)
    attest.add_argument("--timeout", type=float, default=30.0)
    attest.add_argument(
        "--attestation-method",
        choices=("firmware_boot_observation", "serial_challenge"),
        default="firmware_boot_observation",
    )
    attest.add_argument("--no-reset-device", action="store_true")
    attest.add_argument("--output", type=Path, required=True)
    attest.set_defaults(handler=_hil_attest)

    finalize = subparsers.add_parser(
        "hil-finalize-attestation",
        help="bind real device proofs to completed structured capture streams",
    )
    finalize.add_argument("--bundle", type=Path, required=True)
    finalize.set_defaults(handler=_hil_finalize_attestation)

    research = subparsers.add_parser(
        "research-contract",
        help="emit the frozen 2AFC study contract and data dictionary",
    )
    research.add_argument("--output", type=Path)
    research.set_defaults(handler=_research_contract)

    results = subparsers.add_parser(
        "research-results",
        help="evaluate preregistered D7/D30/D90 results",
    )
    results.add_argument("--responses", type=Path, required=True)
    results.add_argument("--assignments", type=Path, required=True)
    results.add_argument("--bootstrap-iterations", type=int, default=10_000)
    results.add_argument("--collection-complete", action="store_true")
    results.add_argument("--server-git-sha", required=True)
    results.add_argument("--policy-hash", required=True)
    results.add_argument("--prompt-hash", required=True)
    results.add_argument("--temperament-generator-version", required=True)
    results.add_argument("--va-config-hash", required=True)
    results.add_argument("--output", type=Path)
    results.set_defaults(handler=_research_results)

    checkpoint = subparsers.add_parser(
        "checkpoint-results",
        help="evaluate raw D7/D30/D90 safety and longitudinal metrics",
    )
    checkpoint.add_argument("--input", type=Path, required=True)
    checkpoint.add_argument("--output", type=Path)
    checkpoint.set_defaults(handler=_checkpoint_results)

    rollout = subparsers.add_parser(
        "rollout",
        help="evaluate one non-skippable controlled-rollout transition",
    )
    rollout.add_argument("--current-stage", choices=ROLLOUT_STAGES, required=True)
    rollout.add_argument("--target-stage", choices=ROLLOUT_STAGES, required=True)
    _add_rollout_evidence_arguments(rollout)
    rollout.set_defaults(handler=_rollout)

    cleanup = subparsers.add_parser(
        "rollout-cleanup",
        help="authorize legacy cleanup only after D90 and the rollback window",
    )
    cleanup.add_argument("--current-stage", choices=ROLLOUT_STAGES, required=True)
    cleanup.add_argument("--rollback-report", type=Path)
    _add_rollout_evidence_arguments(cleanup)
    cleanup.set_defaults(handler=_rollout_cleanup)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
