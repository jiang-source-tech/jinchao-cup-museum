from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import json

import pytest

from tools.individuality_validation.checkpoint import evaluate_checkpoint_outcomes
from tools.individuality_validation.contracts import GateCheck, make_report
from tools.individuality_validation.capture import parse_firmware_boot_observation
from tools.individuality_validation.hil import (
    NETWORK_EVIDENCE_PATHS,
    REQUIRED_HIL_PATHS,
    HILCaptureAttestation,
    HILEvent,
    HILIdentityBinding,
    HILLogRecord,
    HILManifest,
    _bundle_member,
    evaluate_hil_bundle,
    evaluate_hil_evidence,
)
from tools.individuality_validation.matrix import (
    CONTROL_CLASSES,
    PROBES,
    SCENARIO_CLASSES,
    pairwise_temperament_matrix,
    run_policy_matrix_gate,
    temperament_matrix,
)
from tools.individuality_validation.research import (
    CounterfactualCandidate,
    CounterfactualPair,
    CounterfactualPolicyVariant,
    MemoryReference,
    MemoryTruthItem,
    StudyResponse,
    balanced_group_assignments,
    build_2afc_tasks,
    evaluate_research_results,
    freeze_policy_snapshot,
    generate_counterfactual_pair,
    load_preregistration,
    research_contract_hash,
    research_contract_payload,
    validate_counterfactual_pair,
    validate_memory_references,
)
from tools.individuality_validation.rollout import evaluate_rollout_transition


NOW = "2026-07-26T20:00:00+08:00"
VERSION_BINDING = {
    "server_git_sha": "b" * 40,
    "policy_hash": "c" * 64,
    "prompt_hash": "d" * 64,
    "temperament_generator_version": "xiaoxin-temperament-v1",
    "va_config_hash": "e" * 64,
}


def _candidate(
    *,
    index: int,
    mode: str,
    kind: str,
    policy_hash: str,
    memory_subject_id: str = "subject-research",
) -> CounterfactualCandidate:
    return CounterfactualCandidate(
        candidate_id=f"{kind}-{mode}-{index}",
        participant_key="participant-1",
        memory_subject_id=memory_subject_id,
        relationship_epoch_id="epoch-research",
        probe_key=PROBES[index % len(PROBES)].key,
        mode=mode,  # type: ignore[arg-type]
        response_text=("我们先看重点。" if kind == "actual" else "我们先看全貌。"),
        fact_ids=() if mode == "style_only" else ("fact-eligible",),
        model_name="qwen",
        model_version="model-v1",
        capability_hash="capability-hash",
        safety_hash="safety-hash",
        voice_id="same-voice",
        policy_hash=policy_hash,
    )


def _matched_pairs() -> tuple[CounterfactualPair, ...]:
    return tuple(
        generate_counterfactual_pair(
            pair_id=f"pair-{mode}-{index}",
            actual=_candidate(
                index=index,
                mode=mode,
                kind="actual",
                policy_hash=f"actual-policy-{index}",
            ),
            counterfactual_candidate_id=f"counterfactual-{mode}-{index}",
            counterfactual_response_text="我们先看全貌。",
            counterfactual_policy_variant=CounterfactualPolicyVariant(
                kind="temperament",
                values={
                    "exploration_orientation": "focused",
                    "expression_energy": "calm",
                    "thought_organization": "intuitive",
                    "playfulness": "restrained",
                    "companion_initiative": "reserved",
                },
                policy_hash=f"counterfactual-policy-{index}",
            ),
        )
        for mode in ("style_only", "whole_companion")
        for index in range(6)
    )


def _complete_hil_evidence() -> tuple[
    HILManifest,
    tuple[HILEvent, ...],
    tuple[HILLogRecord, ...],
    HILCaptureAttestation,
]:
    start = datetime.fromisoformat("2026-07-25T09:00:00+08:00")
    end = start + timedelta(hours=24)
    policy_hash = "a" * 64
    manifest = HILManifest(
        run_id="hil-complete",
        started_at=start.isoformat(),
        completed_at=end.isoformat(),
        server_git_sha="b" * 40,
        firmware_version="firmware-v1",
        identity_bindings=(
            HILIdentityBinding("device-a", "subject-a", "pet-a", "epoch-a"),
            HILIdentityBinding("device-b", "subject-b", "pet-b", "epoch-b"),
        ),
        policy_hash=policy_hash,
        temperament_generator_version="xiaoxin-temperament-v1",
        serial_port="COM7",
        serial_log="serial.log",
        server_log="server.log",
        network_log="network.log",
        capture_attestation="capture-attestation.json",
        evidence_origin="synthetic",
        slo_thresholds_ms={"voice_end_to_end": 1200.0},
    )
    events: list[HILEvent] = []
    for path_index, path in enumerate(REQUIRED_HIL_PATHS):
        for iteration in range(1, 31):
            subject_index = (path_index + iteration) % 2
            event_id = f"{path}-{iteration:02d}"
            events.append(
                HILEvent(
                    event_id=event_id,
                    path=path,
                    iteration=iteration,
                    occurred_at=(start + timedelta(minutes=len(events))).isoformat(),
                    device_id=("device-a", "device-b")[subject_index],
                    memory_subject_id=("subject-a", "subject-b")[subject_index],
                    pet_id=("pet-a", "pet-b")[subject_index],
                    relationship_epoch_id=("epoch-a", "epoch-b")[subject_index],
                    policy_hash=policy_hash,
                    expected="state-ok",
                    observed="state-ok",
                    outcome="PASS",
                    state_before_hash=f"before-{event_id}",
                    state_after_hash=f"after-{event_id}",
                    latency_ms=500.0 if path == "normal_conversation" else None,
                    slo_key=(
                        "voice_end_to_end" if path == "normal_conversation" else None
                    ),
                )
            )
    for stability_index in range(49):
        events.append(
            HILEvent(
                event_id=f"stability-24h-{stability_index:02d}",
                path="stability_24h",
                iteration=stability_index + 1,
                occurred_at=(
                    start + timedelta(minutes=30 * stability_index)
                ).isoformat(),
                device_id="device-a",
                memory_subject_id="subject-a",
                pet_id="pet-a",
                relationship_epoch_id="epoch-a",
                policy_hash=policy_hash,
                expected="stable",
                observed="stable",
                outcome="PASS",
                state_before_hash="stability-before",
                state_after_hash="stability-after",
            )
        )
    records = tuple(
        HILLogRecord(
            event_id=event.event_id,
            source=source,  # type: ignore[arg-type]
            occurred_at=event.occurred_at,
            device_id=event.device_id,
            memory_subject_id=event.memory_subject_id,
            pet_id=event.pet_id,
            relationship_epoch_id=event.relationship_epoch_id,
        )
        for event in events
        for source in (
            ("serial", "server", "network")
            if event.path
            in {
                "wifi_reconnect",
                "websocket_reconnect",
                "network_recovery",
                "server_restart",
                "outbox_replay",
                "duplicate_delivery",
                "delayed_delivery",
            }
            else ("serial", "server")
        )
    )
    attestation = HILCaptureAttestation(
        attestation_id="synthetic-attestation",
        collector_version="unit-test",
        capture_started_at=start.isoformat(),
        capture_completed_at=end.isoformat(),
        serial_port="COM7",
        serial_device_instance_id="synthetic-device",
        server_endpoint="127.0.0.1:8000",
        hardware_challenge_nonce="synthetic-nonce",
        hardware_challenge_response="synthetic-response",
        serial_open_succeeded=True,
        server_log_stream_succeeded=True,
        network_capture_succeeded=True,
        synthetic=True,
    )
    return manifest, tuple(events), records, attestation


def test_slice13_full_matrix_is_deterministic_and_covers_interactions():
    report = run_policy_matrix_gate(generated_at=NOW, replay_count=20)

    assert report.status == "PASS"
    assert len(temperament_matrix()) == 243
    assert len(pairwise_temperament_matrix()) == 90
    assert len(PROBES) == 7
    assert len(SCENARIO_CLASSES) == 7
    assert len(CONTROL_CLASSES) == 7
    assert report.metadata["replay_count"] == 20
    assert all(report.metadata["axis_effect_counts"].values())
    assert report.metadata["replayed_state_count"] == 14


def test_slice13_research_contract_matches_counterfactual_memory_and_2afc_rules():
    pairs = _matched_pairs()
    assert all(validate_counterfactual_pair(pair).status == "PASS" for pair in pairs)
    leaked = replace(
        pairs[0],
        counterfactual=replace(
            pairs[0].counterfactual,
            memory_subject_id="subject-other",
        ),
    )
    assert validate_counterfactual_pair(leaked).status == "FAIL"

    truth = (
        MemoryTruthItem(
            fact_id="fact-eligible",
            memory_subject_id="subject-research",
            relationship_epoch_id="epoch-research",
            status="confirmed",
            reference_eligible=True,
            relevance_tags=("future_event",),
        ),
        MemoryTruthItem(
            fact_id="fact-forgotten",
            memory_subject_id="subject-research",
            relationship_epoch_id="epoch-research",
            status="forgotten",
            reference_eligible=False,
            relevance_tags=("future_event",),
        ),
    )
    valid_reference = MemoryReference(
        reference_id="reference-valid",
        participant_key="participant-1",
        memory_subject_id="subject-research",
        relationship_epoch_id="epoch-research",
        probe_key="future_event",
        fact_ids=("fact-eligible",),
        explicit_recall=True,
    )
    valid_memory = validate_memory_references(
        truth=truth,
        references=(valid_reference,),
        generated_at=NOW,
    )
    invalid_memory = validate_memory_references(
        truth=truth,
        references=(replace(valid_reference, fact_ids=("fact-forgotten",)),),
        generated_at=NOW,
    )
    assert valid_memory.status == "PASS"
    assert invalid_memory.status == "FAIL"

    snapshot = freeze_policy_snapshot(
        snapshot_id="snapshot-d7",
        participant_key="participant-1",
        checkpoint="D7",
        captured_at=NOW,
        policy={"version": "companion-policy-v6", "reason_codes": ["stable"]},
        server_git_sha="b" * 40,
        prompt_hash="c" * 64,
        temperament_generator_version="xiaoxin-temperament-v1",
        va_config_hash="d" * 64,
    )
    design = build_2afc_tasks(
        participant_key="participant-1",
        checkpoint="D7",
        pairs=pairs,
        frozen_seed="preregistered-seed-v1",
    )
    replayed_design = build_2afc_tasks(
        participant_key="participant-1",
        checkpoint="D7",
        pairs=tuple(reversed(pairs)),
        frozen_seed="preregistered-seed-v1",
    )
    assignments = balanced_group_assignments(
        tuple(f"participant-{index}" for index in range(12)),
        frozen_seed="preregistered-seed-v1",
    )
    assert snapshot.policy_hash
    assert design == replayed_design
    assert len(design.participant_tasks) == 12
    assert sum(task.mode == "style_only" for task in design.participant_tasks) == 6
    assert not any(
        "correct_position" in task.__dict__ for task in design.participant_tasks
    )
    assert sum(item.correct_position == "A" for item in design.answer_key) == 6
    assert sum(value == "normal_adaptation" for value in assignments.values()) == 6
    assert (
        research_contract_payload()["preregistration"]["sample_contract"][
            "d90_valid_completers_min"
        ]
        == 60
    )

    generated = generate_counterfactual_pair(
        pair_id="generated-pair",
        actual=pairs[0].actual,
        counterfactual_candidate_id="generated-counterfactual",
        counterfactual_response_text=pairs[0].counterfactual.response_text,
        counterfactual_policy_variant=CounterfactualPolicyVariant(
            kind="adjustment",
            values={"response_length": "short"},
            policy_hash="generated-policy",
        ),
    )
    assert validate_counterfactual_pair(generated).status == "PASS"

    preregistration = load_preregistration()
    modified = deepcopy(preregistration)
    modified["sample_contract"]["d90_valid_completers_min"] = 61
    assert research_contract_hash(preregistration) != research_contract_hash(modified)

    duplicate_pair = replace(pairs[1], pair_id=pairs[0].pair_id)
    with pytest.raises(ValueError, match="duplicate counterfactual pair id"):
        build_2afc_tasks(
            participant_key="participant-1",
            checkpoint="D7",
            pairs=(pairs[0], duplicate_pair, *pairs[2:]),
            frozen_seed="preregistered-seed-v1",
        )

    research = evaluate_research_results(
        responses=(),
        assignments={},
        generated_at=NOW,
        collection_complete=False,
        version_binding=VERSION_BINDING,
    )
    assert research.status == "INCONCLUSIVE"
    assert research.metadata["bootstrap_iterations"] == 10_000
    assert research.metadata["version_binding"] == VERSION_BINDING


def test_slice13_hil_report_refuses_missing_or_failed_real_device_evidence(tmp_path):
    boot = parse_firmware_boot_observation(
        "Project name: ai_pet\n"
        "App version: 0.1.3\n"
        "ELF file SHA256: b285668ab...\n"
        "Board: UUID=26e34b0b-d878-4889-b794-70400225cba9\n"
        "wifi:mode : sta (1c:db:d4:48:d1:50)\n"
        "HttpClient: Established new connection to 121.43.33.0:8003\n"
    )
    assert boot == {
        "project_name": "ai_pet",
        "firmware_version": "0.1.3",
        "firmware_elf_sha256_prefix": "b285668ab...",
        "client_id": "26e34b0b-d878-4889-b794-70400225cba9",
        "device_id": "1c:db:d4:48:d1:50",
        "ota_server_endpoint": "121.43.33.0:8003",
    }

    with pytest.raises(ValueError, match="escapes bundle"):
        _bundle_member(tmp_path, "../outside.jsonl")

    missing = evaluate_hil_bundle(tmp_path, generated_at=NOW)
    assert missing.status == "INCONCLUSIVE"

    manifest, events, records, attestation = _complete_hil_evidence()
    complete = evaluate_hil_evidence(
        manifest=manifest,
        events=events,
        log_records=records,
        attestation=attestation,
        generated_at=NOW,
    )
    failed = evaluate_hil_evidence(
        manifest=manifest,
        events=(replace(events[0], identity_leak=True), *events[1:]),
        log_records=records,
        attestation=attestation,
        generated_at=NOW,
    )

    assert complete.status == "INCONCLUSIVE"
    assert {"ota_success", "ota_rollback"} <= NETWORK_EVIDENCE_PATHS
    assert any(
        evidence.startswith("hardware_device_coverage_missing:")
        for check in complete.checks
        for evidence in check.evidence
    )
    assert any(
        evidence.startswith("server_git_sha_missing:")
        for check in complete.checks
        for evidence in check.evidence
    )
    assert complete.metadata["event_count"] == len(REQUIRED_HIL_PATHS) * 30 + 49
    assert failed.status == "FAIL"
    assert any(
        check.check_id == "hil-p0-zero" and check.status == "FAIL"
        for check in failed.checks
    )


def test_slice14_rollout_refuses_stage_skips_wrong_revision_and_p0(tmp_path):
    skipped = evaluate_rollout_transition(
        current_stage="not_started",
        target_stage="temperament_limited_cohort",
        server_git_sha="b" * 40,
        generated_at=NOW,
    )
    assert skipped.status == "FAIL"
    assert any(
        check.check_id == "rollout-stage-sequence" and check.status == "FAIL"
        for check in skipped.checks
    )

    previous_path = tmp_path / "previous-rollout.json"
    make_report(
        gate_id="slice14-controlled-rollout",
        generated_at=NOW,
        checks=(GateCheck("prior-stage", "PASS", "prior stage passed"),),
        metadata={
            "current_stage": "not_started",
            "target_stage": "schema_backfill_shadow",
            "server_git_sha": "a" * 40,
        },
    ).write_json(previous_path)
    wrong_revision = evaluate_rollout_transition(
        current_stage="schema_backfill_shadow",
        target_stage="expression_style_diagnostic",
        server_git_sha="b" * 40,
        previous_rollout_report=previous_path,
        generated_at=NOW,
    )
    assert wrong_revision.status == "FAIL"
    assert any(
        check.check_id == "rollout-state-chain" and check.status == "FAIL"
        for check in wrong_revision.checks
    )

    observation = tmp_path / "observation.json"
    observation.write_text(
        json.dumps(
            {
                "status": "PASS",
                "stage": "not_started",
                "server_git_sha": "b" * 40,
                "schema_version": 19,
                "backup_sha256": None,
                "matrix_report_digest": None,
                "p0_events": ["subject_identity_leak"],
            }
        ),
        encoding="utf-8",
    )
    stopped = evaluate_rollout_transition(
        current_stage="not_started",
        target_stage="schema_backfill_shadow",
        server_git_sha="b" * 40,
        observation_report=observation,
        generated_at=NOW,
    )
    assert stopped.status == "FAIL"
    assert any(
        check.check_id == "rollout-p0-zero" and check.status == "FAIL"
        for check in stopped.checks
    )


def test_slice14_rollout_requires_complete_hil_and_checkpoint_reports():
    report = evaluate_rollout_transition(
        current_stage="cp06_controls",
        target_stage="hil_pass",
        server_git_sha="b" * 40,
        generated_at=NOW,
    )

    assert report.status == "INCONCLUSIVE"
    assert any(
        check.check_id == "rollout-hil-present"
        and check.status == "INCONCLUSIVE"
        for check in report.checks
    )

    checkpoint = evaluate_rollout_transition(
        current_stage="hil_pass",
        target_stage="d7_pilot",
        server_git_sha="b" * 40,
        generated_at=NOW,
    )
    assert checkpoint.status == "INCONCLUSIVE"
    assert any(
        check.check_id == "rollout-checkpoint-present"
        and check.status == "INCONCLUSIVE"
        for check in checkpoint.checks
    )

    undersized = evaluate_checkpoint_outcomes(
        checkpoint="D7",
        server_git_sha="b" * 40,
        participant_count=11,
        recruited_count=12,
        group_assignment_counts={},
        metrics={},
        research_version_binding=VERSION_BINDING,
        generated_at=NOW,
    )
    assert any(
        check.check_id == "checkpoint-sample"
        and check.status == "INCONCLUSIVE"
        for check in undersized.checks
    )

    inconsistent_groups = evaluate_checkpoint_outcomes(
        checkpoint="D30",
        server_git_sha="b" * 40,
        participant_count=2,
        recruited_count=72,
        group_assignment_counts={
            "normal_adaptation": 1,
            "delayed_adaptation": 1,
        },
        metrics={},
        research_version_binding=VERSION_BINDING,
        generated_at=NOW,
    )
    assert inconsistent_groups.status == "FAIL"
    assert any(
        check.check_id == "checkpoint-sample" and check.status == "FAIL"
        for check in inconsistent_groups.checks
    )

    impossible_interval = evaluate_checkpoint_outcomes(
        checkpoint="D7",
        server_git_sha="b" * 40,
        participant_count=12,
        recruited_count=12,
        group_assignment_counts={},
        metrics={
            "memory_reference_count": 12,
            "memory_precision": 0.98,
            "memory_precision_ci_lower": 1.0,
        },
        research_version_binding=VERSION_BINDING,
        generated_at=NOW,
    )
    assert any(
        check.check_id == "checkpoint-memory-reference-precision"
        and check.status == "FAIL"
        for check in impossible_interval.checks
    )
