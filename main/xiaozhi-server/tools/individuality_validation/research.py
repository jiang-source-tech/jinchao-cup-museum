from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
from typing import Literal, Mapping, Sequence

from core.xiaoxin.companion.reflection import ALLOWED_ADJUSTMENT_VALUES
from core.xiaoxin.companion.temperament import TEMPERAMENT_AXIS_LEVELS

from .checkpoint import normalize_research_version_binding
from .contracts import (
    GateCheck,
    GateReport,
    canonical_hash,
    canonical_json,
    make_report,
    require_aware_datetime,
    require_text,
)


QuestionMode = Literal["style_only", "whole_companion"]
TruthStatus = Literal[
    "confirmed",
    "corrected",
    "forgotten",
    "revoked",
    "prohibited",
]


@dataclass(frozen=True)
class CounterfactualCandidate:
    candidate_id: str
    participant_key: str
    memory_subject_id: str
    relationship_epoch_id: str
    probe_key: str
    mode: QuestionMode
    response_text: str
    fact_ids: tuple[str, ...]
    model_name: str
    model_version: str
    capability_hash: str
    safety_hash: str
    voice_id: str
    policy_hash: str
    display_cues_removed: bool = True

    def __post_init__(self) -> None:
        for name in (
            "candidate_id",
            "participant_key",
            "memory_subject_id",
            "relationship_epoch_id",
            "probe_key",
            "response_text",
            "model_name",
            "model_version",
            "capability_hash",
            "safety_hash",
            "voice_id",
            "policy_hash",
        ):
            require_text(name, getattr(self, name))
        if self.mode not in {"style_only", "whole_companion"}:
            raise ValueError("counterfactual candidate mode is invalid")
        if self.mode == "style_only" and self.fact_ids:
            raise ValueError("style_only candidates cannot contain personal fact ids")
        if len(set(self.fact_ids)) != len(self.fact_ids):
            raise ValueError("counterfactual fact ids must be unique")


@dataclass(frozen=True)
class CounterfactualPolicyVariant:
    kind: Literal["temperament", "adjustment"]
    values: Mapping[str, str]
    policy_hash: str

    def __post_init__(self) -> None:
        require_text("policy_hash", self.policy_hash)
        if self.kind == "temperament":
            if set(self.values) != set(TEMPERAMENT_AXIS_LEVELS):
                raise ValueError("counterfactual temperament must define all five axes")
            if any(
                value not in TEMPERAMENT_AXIS_LEVELS[axis]
                for axis, value in self.values.items()
            ):
                raise ValueError("counterfactual temperament contains an invalid level")
        elif self.kind == "adjustment":
            if len(self.values) != 1:
                raise ValueError("counterfactual adjustment must change one behavior")
            dimension, value = next(iter(self.values.items()))
            if value not in ALLOWED_ADJUSTMENT_VALUES.get(dimension, frozenset()):
                raise ValueError("counterfactual adjustment is invalid")
        else:
            raise ValueError("counterfactual policy variant kind is invalid")


@dataclass(frozen=True)
class CounterfactualPair:
    pair_id: str
    actual: CounterfactualCandidate
    counterfactual: CounterfactualCandidate
    counterfactual_variant: CounterfactualPolicyVariant | None = None
    max_length_delta_ratio: float = 0.15

    def __post_init__(self) -> None:
        require_text("pair_id", self.pair_id)
        if not 0 <= self.max_length_delta_ratio <= 0.5:
            raise ValueError("counterfactual length tolerance is invalid")


def generate_counterfactual_pair(
    *,
    pair_id: str,
    actual: CounterfactualCandidate,
    counterfactual_candidate_id: str,
    counterfactual_response_text: str,
    counterfactual_policy_variant: CounterfactualPolicyVariant,
    max_length_delta_ratio: float = 0.15,
) -> CounterfactualPair:
    """Generate a matched alternative while preserving every frozen condition."""
    for name, value in (
        ("counterfactual_candidate_id", counterfactual_candidate_id),
        ("counterfactual_response_text", counterfactual_response_text),
        ("counterfactual_policy_hash", counterfactual_policy_variant.policy_hash),
    ):
        require_text(name, value)
    counterfactual = replace(
        actual,
        candidate_id=counterfactual_candidate_id,
        response_text=counterfactual_response_text,
        policy_hash=counterfactual_policy_variant.policy_hash,
    )
    pair = CounterfactualPair(
        pair_id=pair_id,
        actual=actual,
        counterfactual=counterfactual,
        counterfactual_variant=counterfactual_policy_variant,
        max_length_delta_ratio=max_length_delta_ratio,
    )
    check = validate_counterfactual_pair(pair)
    if check.status != "PASS":
        raise ValueError(
            "generated counterfactual violates frozen conditions: "
            + ",".join(check.evidence)
        )
    return pair


def validate_counterfactual_pair(pair: CounterfactualPair) -> GateCheck:
    left = pair.actual
    right = pair.counterfactual
    mismatches: list[str] = []
    fields = (
        "participant_key",
        "memory_subject_id",
        "relationship_epoch_id",
        "probe_key",
        "mode",
        "fact_ids",
        "model_name",
        "model_version",
        "capability_hash",
        "safety_hash",
        "voice_id",
    )
    for field_name in fields:
        if getattr(left, field_name) != getattr(right, field_name):
            mismatches.append(field_name)
    if left.candidate_id == right.candidate_id:
        mismatches.append("candidate_id_not_counterfactual")
    if left.policy_hash == right.policy_hash:
        mismatches.append("policy_hash_not_counterfactual")
    if pair.counterfactual_variant is None:
        mismatches.append("counterfactual_generation_proof_missing")
    elif pair.counterfactual_variant.policy_hash != right.policy_hash:
        mismatches.append("counterfactual_generation_proof_mismatch")
    if not left.display_cues_removed or not right.display_cues_removed:
        mismatches.append("display_cues_present")
    longest = max(len(left.response_text), len(right.response_text), 1)
    length_delta = abs(len(left.response_text) - len(right.response_text)) / longest
    if length_delta > pair.max_length_delta_ratio:
        mismatches.append("response_length")
    return GateCheck(
        check_id=f"counterfactual:{pair.pair_id}",
        status="PASS" if not mismatches else "FAIL",
        detail=(
            "counterfactual conditions are matched"
            if not mismatches
            else "counterfactual mismatch"
        ),
        evidence=tuple(mismatches),
    )


@dataclass(frozen=True)
class MemoryTruthItem:
    fact_id: str
    memory_subject_id: str
    relationship_epoch_id: str
    status: TruthStatus
    reference_eligible: bool
    relevance_tags: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("fact_id", "memory_subject_id", "relationship_epoch_id"):
            require_text(name, getattr(self, name))
        if self.status not in {
            "confirmed",
            "corrected",
            "forgotten",
            "revoked",
            "prohibited",
        }:
            raise ValueError("memory truth status is invalid")
        if self.status != "confirmed" and self.reference_eligible:
            raise ValueError("only confirmed truth items may be reference eligible")


@dataclass(frozen=True)
class MemoryReference:
    reference_id: str
    participant_key: str
    memory_subject_id: str
    relationship_epoch_id: str
    probe_key: str
    fact_ids: tuple[str, ...]
    explicit_recall: bool = False

    def __post_init__(self) -> None:
        for name in (
            "reference_id",
            "participant_key",
            "memory_subject_id",
            "relationship_epoch_id",
            "probe_key",
        ):
            require_text(name, getattr(self, name))


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise ValueError("percentile requires values")
    ordered = sorted(values)
    index = int((len(ordered) - 1) * quantile)
    return ordered[index]


def _cluster_bootstrap_lower(
    participant_counts: Mapping[str, tuple[int, int]],
    *,
    iterations: int,
    seed: str,
) -> float | None:
    usable = {
        key: counts for key, counts in participant_counts.items() if counts[1] > 0
    }
    if not usable:
        return None
    keys = tuple(sorted(usable))
    generator = random.Random(int(_digest(seed), 16))
    estimates: list[float] = []
    for _ in range(iterations):
        numerator = 0
        denominator = 0
        for _sample in keys:
            sampled_key = keys[generator.randrange(len(keys))]
            counts = usable[sampled_key]
            numerator += counts[0]
            denominator += counts[1]
        estimates.append(numerator / denominator)
    return _percentile(estimates, 0.025)


def validate_memory_references(
    *,
    truth: Sequence[MemoryTruthItem],
    references: Sequence[MemoryReference],
    generated_at: str,
    bootstrap_iterations: int = 10_000,
    preregistration: Mapping[str, object] | None = None,
) -> GateReport:
    if bootstrap_iterations < 10_000:
        raise ValueError("memory validation requires at least 10,000 bootstraps")
    frozen = dict(preregistration or load_preregistration())
    analysis_contract = dict(frozen["analysis_contract"])  # type: ignore[arg-type]
    memory_contract = dict(analysis_contract["memory"])  # type: ignore[arg-type]
    by_id = {item.fact_id: item for item in truth}
    duplicate_truth = len(by_id) != len(truth)
    duplicate_references = len({item.reference_id for item in references}) != len(
        references
    )
    cited = 0
    eligible = 0
    relevant = 0
    recall_expected = 0
    recall_observed = 0
    participant_precision: dict[str, list[int]] = {}
    participant_relevance: dict[str, list[int]] = {}
    participant_recall: dict[str, list[int]] = {}
    p0: list[str] = []
    for reference in references:
        precision_counts = participant_precision.setdefault(
            reference.participant_key, [0, 0]
        )
        relevance_counts = participant_relevance.setdefault(
            reference.participant_key, [0, 0]
        )
        recall_counts = participant_recall.setdefault(reference.participant_key, [0, 0])
        valid_fact_ids: set[str] = set()
        for fact_id in reference.fact_ids:
            cited += 1
            precision_counts[1] += 1
            relevance_counts[1] += 1
            item = by_id.get(fact_id)
            if item is None:
                p0.append(f"{reference.reference_id}:unknown:{fact_id}")
                continue
            if item.memory_subject_id != reference.memory_subject_id:
                p0.append(f"{reference.reference_id}:cross_subject:{fact_id}")
                continue
            if item.relationship_epoch_id != reference.relationship_epoch_id:
                p0.append(f"{reference.reference_id}:old_epoch:{fact_id}")
                continue
            if not item.reference_eligible or item.status != "confirmed":
                p0.append(f"{reference.reference_id}:ineligible:{fact_id}")
                continue
            eligible += 1
            precision_counts[0] += 1
            valid_fact_ids.add(fact_id)
            if reference.probe_key in item.relevance_tags:
                relevant += 1
                relevance_counts[0] += 1
        if reference.explicit_recall:
            expected_fact_ids = {
                item.fact_id
                for item in truth
                if item.memory_subject_id == reference.memory_subject_id
                and item.relationship_epoch_id == reference.relationship_epoch_id
                and item.status == "confirmed"
                and item.reference_eligible
                and reference.probe_key in item.relevance_tags
            }
            recall_expected += len(expected_fact_ids)
            recalled = len(valid_fact_ids.intersection(expected_fact_ids))
            recall_observed += recalled
            recall_counts[0] += recalled
            recall_counts[1] += len(expected_fact_ids)
    precision = eligible / cited if cited else None
    relevance = relevant / cited if cited else None
    recall = recall_observed / recall_expected if recall_expected else None
    precision_lower = _cluster_bootstrap_lower(
        {key: tuple(value) for key, value in participant_precision.items()},
        iterations=bootstrap_iterations,
        seed="memory-precision",
    )
    relevance_lower = _cluster_bootstrap_lower(
        {key: tuple(value) for key, value in participant_relevance.items()},
        iterations=bootstrap_iterations,
        seed="memory-relevance",
    )
    recall_lower = _cluster_bootstrap_lower(
        {key: tuple(value) for key, value in participant_recall.items()},
        iterations=bootstrap_iterations,
        seed="memory-recall",
    )
    checks = (
        GateCheck(
            "truth-set-unique",
            "FAIL" if duplicate_truth or duplicate_references else "PASS",
            "memory truth fact ids and reference ids are unique",
        ),
        GateCheck(
            "memory-reference-p0",
            "FAIL" if p0 else "PASS",
            "no false, cross-subject, old-epoch, forgotten, or revoked facts were cited",
            tuple(p0[:50]),
        ),
        GateCheck(
            "memory-reference-precision",
            (
                "INCONCLUSIVE"
                if precision is None or precision_lower is None
                else (
                    "PASS"
                    if precision >= float(memory_contract["precision_min"])
                    and precision_lower
                    >= float(memory_contract["precision_cluster_ci_lower_min"])
                    else "FAIL"
                )
            ),
            (
                "no references available"
                if precision is None
                else f"precision={precision:.4f}; cluster_ci_lower={precision_lower:.4f}"
            ),
        ),
        GateCheck(
            "memory-explicit-recall",
            (
                "INCONCLUSIVE"
                if recall is None or recall_lower is None
                else (
                    "PASS"
                    if recall >= float(memory_contract["explicit_recall_min"])
                    and recall_lower
                    >= float(memory_contract["explicit_recall_cluster_ci_lower_min"])
                    else "FAIL"
                )
            ),
            (
                "no explicit recall requests available"
                if recall is None
                else f"recall={recall:.4f}; cluster_ci_lower={recall_lower:.4f}"
            ),
        ),
        GateCheck(
            "memory-reference-relevance",
            (
                "INCONCLUSIVE"
                if relevance is None or relevance_lower is None
                else (
                    "PASS"
                    if relevance >= float(memory_contract["relevance_min"])
                    and relevance_lower
                    >= float(memory_contract["relevance_cluster_ci_lower_min"])
                    else "FAIL"
                )
            ),
            (
                "no references available"
                if relevance is None
                else f"relevance={relevance:.4f}; cluster_ci_lower={relevance_lower:.4f}"
            ),
        ),
    )
    return make_report(
        gate_id="slice13-memory-truth",
        generated_at=generated_at,
        checks=checks,
        metadata={
            "truth_count": len(truth),
            "reference_count": len(references),
            "participant_count": len(participant_precision),
            "bootstrap_iterations": bootstrap_iterations,
        },
    )


@dataclass(frozen=True)
class FrozenPolicySnapshot:
    snapshot_id: str
    participant_key: str
    checkpoint: str
    captured_at: str
    policy_json: str
    policy_hash: str
    server_git_sha: str
    prompt_hash: str
    temperament_generator_version: str
    va_config_hash: str

    def __post_init__(self) -> None:
        for name in (
            "snapshot_id",
            "participant_key",
            "checkpoint",
            "server_git_sha",
            "prompt_hash",
            "temperament_generator_version",
            "va_config_hash",
        ):
            require_text(name, getattr(self, name))
        require_aware_datetime("captured_at", self.captured_at)
        require_text("policy_json", self.policy_json)
        policy = json.loads(self.policy_json)
        if not isinstance(policy, dict) or canonical_hash(policy) != self.policy_hash:
            raise ValueError("frozen policy snapshot hash mismatch")


def freeze_policy_snapshot(
    *,
    snapshot_id: str,
    participant_key: str,
    checkpoint: str,
    captured_at: str,
    policy: Mapping[str, object],
    server_git_sha: str,
    prompt_hash: str,
    temperament_generator_version: str,
    va_config_hash: str,
) -> FrozenPolicySnapshot:
    frozen_policy = dict(policy)
    policy_json = canonical_json(frozen_policy)
    return FrozenPolicySnapshot(
        snapshot_id=snapshot_id,
        participant_key=participant_key,
        checkpoint=checkpoint,
        captured_at=captured_at,
        policy_json=policy_json,
        policy_hash=canonical_hash(frozen_policy),
        server_git_sha=server_git_sha,
        prompt_hash=prompt_hash,
        temperament_generator_version=temperament_generator_version,
        va_config_hash=va_config_hash,
    )


@dataclass(frozen=True)
class TwoAfcTask:
    task_id: str
    participant_key: str
    checkpoint: str
    question_index: int
    mode: QuestionMode
    probe_key: str
    pair_id: str
    left_candidate_id: str
    right_candidate_id: str
    randomization_digest: str


@dataclass(frozen=True)
class TwoAfcAnswer:
    task_id: str
    correct_position: Literal["A", "B"]


@dataclass(frozen=True)
class TwoAfcDesign:
    participant_tasks: tuple[TwoAfcTask, ...]
    answer_key: tuple[TwoAfcAnswer, ...]


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def build_2afc_tasks(
    *,
    participant_key: str,
    checkpoint: str,
    pairs: Sequence[CounterfactualPair],
    frozen_seed: str,
) -> TwoAfcDesign:
    for name, value in (
        ("participant_key", participant_key),
        ("checkpoint", checkpoint),
        ("frozen_seed", frozen_seed),
    ):
        require_text(name, value)
    eligible: dict[str, list[CounterfactualPair]] = {
        "style_only": [],
        "whole_companion": [],
    }
    pair_ids: set[str] = set()
    candidate_ids: set[str] = set()
    for pair in pairs:
        if pair.pair_id in pair_ids:
            raise ValueError(f"duplicate counterfactual pair id: {pair.pair_id}")
        pair_ids.add(pair.pair_id)
        for candidate in (pair.actual, pair.counterfactual):
            if candidate.candidate_id in candidate_ids:
                raise ValueError(
                    f"duplicate counterfactual candidate id: {candidate.candidate_id}"
                )
            candidate_ids.add(candidate.candidate_id)
        check = validate_counterfactual_pair(pair)
        if check.status != "PASS":
            raise ValueError(f"counterfactual pair {pair.pair_id} is not matched")
        if pair.actual.participant_key != participant_key:
            raise ValueError("counterfactual pair belongs to another participant")
        eligible[pair.actual.mode].append(pair)
    if any(len(items) < 6 for items in eligible.values()):
        raise ValueError("2AFC requires at least six matched pairs for each mode")

    selected: list[CounterfactualPair] = []
    for mode in ("style_only", "whole_companion"):
        ordered = sorted(
            eligible[mode],
            key=lambda pair: _digest(
                frozen_seed,
                participant_key,
                checkpoint,
                mode,
                pair.pair_id,
            ),
        )
        selected.extend(ordered[:6])
    selected.sort(
        key=lambda pair: _digest(
            frozen_seed,
            participant_key,
            checkpoint,
            "question-order",
            pair.pair_id,
        )
    )
    position_order = sorted(
        range(len(selected)),
        key=lambda index: _digest(
            frozen_seed,
            participant_key,
            checkpoint,
            "position-balance",
            selected[index].pair_id,
        ),
    )
    actual_left_indices = set(position_order[: len(selected) // 2])

    tasks: list[TwoAfcTask] = []
    answer_key: list[TwoAfcAnswer] = []
    for zero_based_index, pair in enumerate(selected):
        index = zero_based_index + 1
        digest = _digest(
            frozen_seed,
            participant_key,
            checkpoint,
            str(index),
            pair.pair_id,
        )
        actual_left = zero_based_index in actual_left_indices
        task_id = f"{participant_key}:{checkpoint}:{index:02d}"
        tasks.append(
            TwoAfcTask(
                task_id=task_id,
                participant_key=participant_key,
                checkpoint=checkpoint,
                question_index=index,
                mode=pair.actual.mode,
                probe_key=pair.actual.probe_key,
                pair_id=pair.pair_id,
                left_candidate_id=(
                    pair.actual.candidate_id
                    if actual_left
                    else pair.counterfactual.candidate_id
                ),
                right_candidate_id=(
                    pair.counterfactual.candidate_id
                    if actual_left
                    else pair.actual.candidate_id
                ),
                randomization_digest=digest,
            )
        )
        answer_key.append(
            TwoAfcAnswer(
                task_id=task_id,
                correct_position="A" if actual_left else "B",
            )
        )
    return TwoAfcDesign(tuple(tasks), tuple(answer_key))


def balanced_group_assignments(
    participant_keys: Sequence[str],
    *,
    frozen_seed: str,
) -> dict[str, str]:
    if len(set(participant_keys)) != len(participant_keys):
        raise ValueError("participant keys must be unique")
    ordered = sorted(
        participant_keys,
        key=lambda key: _digest(frozen_seed, "group", key),
    )
    return {
        key: "normal_adaptation" if index % 2 == 0 else "delayed_adaptation"
        for index, key in enumerate(ordered)
    }


STUDY_DATA_DICTIONARY = (
    ("study_id", "string", "preregistered study identifier"),
    ("participant_key", "string", "pseudonymous participant identifier"),
    ("checkpoint", "enum", "D0, D7, D30, or D90"),
    ("group", "enum", "normal_adaptation or delayed_adaptation"),
    ("task_id", "string", "frozen 2AFC task identifier"),
    ("occurred_at", "datetime", "timezone-aware response timestamp"),
    ("probe_key", "enum", "one of the seven behavior probes"),
    ("mode", "enum", "style_only or whole_companion"),
    ("axis_level", "string", "preregistered temperament axis-level stratum"),
    ("basis_category", "enum", "temperament, adjustment, memory, or control"),
    ("fact_ids", "array[string]", "structured eligible fact identifiers only"),
    ("policy_hash", "sha256", "canonical frozen policy digest"),
    ("reason_codes", "array[string]", "ordered policy decision reasons"),
    ("model_version", "string", "generation model version"),
    ("prompt_hash", "sha256", "frozen prompt digest"),
    ("choice", "enum", "A or B"),
    ("confidence", "integer", "participant confidence from 1 to 4"),
    ("exclusion_reason", "string|null", "preregistered exclusion reason"),
)


DEFAULT_PREREGISTRATION_PATH = (
    Path(__file__).resolve().parents[4]
    / "docs"
    / "verification"
    / "xiaoxin-individuality"
    / "research"
    / "preregistration-v1.json"
)


def load_preregistration(
    path: Path = DEFAULT_PREREGISTRATION_PATH,
) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("research preregistration must be a JSON object")
    return value


def research_contract_payload(
    preregistration: Mapping[str, object] | None = None,
) -> dict[str, object]:
    frozen = dict(preregistration or load_preregistration())
    return {
        "contract_version": frozen.get("contract_version"),
        "preregistration": frozen,
        "data_dictionary": [
            {"field": field, "type": field_type, "description": description}
            for field, field_type, description in STUDY_DATA_DICTIONARY
        ],
    }


def research_contract_hash(
    preregistration: Mapping[str, object] | None = None,
) -> str:
    return canonical_hash(research_contract_payload(preregistration))


StudyCheckpoint = Literal["D7", "D30", "D90"]
StudyGroup = Literal["normal_adaptation", "delayed_adaptation"]


@dataclass(frozen=True)
class StudyResponse:
    participant_key: str
    checkpoint: StudyCheckpoint
    group: StudyGroup
    task_id: str
    mode: QuestionMode
    axis_level: str
    correct: bool
    exclusion_reason: str | None = None

    def __post_init__(self) -> None:
        for name in ("participant_key", "task_id", "axis_level"):
            require_text(name, getattr(self, name))
        if self.checkpoint not in {"D7", "D30", "D90"}:
            raise ValueError("study checkpoint is invalid")
        if self.group not in {"normal_adaptation", "delayed_adaptation"}:
            raise ValueError("study group is invalid")
        if self.mode not in {"style_only", "whole_companion"}:
            raise ValueError("study response mode is invalid")
        if not isinstance(self.correct, bool):
            raise ValueError("study response correctness must be boolean")


def _response_counts(
    responses: Sequence[StudyResponse],
) -> dict[str, tuple[int, int]]:
    counts: dict[str, list[int]] = {}
    for response in responses:
        participant = counts.setdefault(response.participant_key, [0, 0])
        participant[0] += int(response.correct)
        participant[1] += 1
    return {key: tuple(value) for key, value in counts.items()}


def _rate(responses: Sequence[StudyResponse]) -> float | None:
    if not responses:
        return None
    return sum(response.correct for response in responses) / len(responses)


def _cluster_difference_lower(
    left: Sequence[StudyResponse],
    right: Sequence[StudyResponse],
    *,
    iterations: int,
    seed: str,
) -> float | None:
    left_counts = _response_counts(left)
    right_counts = _response_counts(right)
    if not left_counts or not right_counts:
        return None
    left_keys = tuple(sorted(left_counts))
    right_keys = tuple(sorted(right_counts))
    generator = random.Random(int(_digest(seed), 16))
    estimates: list[float] = []
    for _ in range(iterations):
        left_samples = [
            left_counts[left_keys[generator.randrange(len(left_keys))]]
            for _key in left_keys
        ]
        right_samples = [
            right_counts[right_keys[generator.randrange(len(right_keys))]]
            for _key in right_keys
        ]
        left_rate = sum(item[0] for item in left_samples) / sum(
            item[1] for item in left_samples
        )
        right_rate = sum(item[0] for item in right_samples) / sum(
            item[1] for item in right_samples
        )
        estimates.append(left_rate - right_rate)
    return _percentile(estimates, 0.025)


def evaluate_research_results(
    *,
    responses: Sequence[StudyResponse],
    assignments: Mapping[str, StudyGroup],
    generated_at: str,
    collection_complete: bool,
    bootstrap_iterations: int = 10_000,
    preregistration: Mapping[str, object] | None = None,
    version_binding: Mapping[str, object] | None = None,
) -> GateReport:
    if bootstrap_iterations < 10_000:
        raise ValueError("research gate requires at least 10,000 bootstraps")
    if any(
        group not in {"normal_adaptation", "delayed_adaptation"}
        for group in assignments.values()
    ):
        raise ValueError("research assignment group is invalid")
    frozen = dict(preregistration or load_preregistration())
    normalized_version_binding = (
        normalize_research_version_binding(version_binding)
        if version_binding is not None
        else None
    )
    sample_contract = dict(frozen["sample_contract"])  # type: ignore[arg-type]
    analysis_contract = dict(frozen["analysis_contract"])  # type: ignore[arg-type]
    valid = tuple(
        response for response in responses if response.exclusion_reason is None
    )
    duplicate_keys = len(
        {
            (response.participant_key, response.checkpoint, response.task_id)
            for response in responses
        }
    ) != len(responses)
    assignment_failures = tuple(
        response.participant_key
        for response in valid
        if assignments.get(response.participant_key) != response.group
    )
    response_counts: dict[tuple[str, str], int] = {}
    for response in valid:
        key = (response.participant_key, response.checkpoint)
        response_counts[key] = response_counts.get(key, 0) + 1
    incomplete_sets = tuple(
        f"{participant}:{checkpoint}:{count}/12"
        for (participant, checkpoint), count in response_counts.items()
        if count != 12
    )

    missing_status = "FAIL" if collection_complete else "INCONCLUSIVE"
    checks: list[GateCheck] = [
        GateCheck(
            "research-record-contract",
            (
                "FAIL"
                if duplicate_keys or assignment_failures or incomplete_sets
                else "PASS"
            ),
            "responses have unique tasks, frozen assignments, and 12 questions",
            tuple((list(assignment_failures) + list(incomplete_sets))[:50]),
        )
    ]

    d90_participants = {
        response.participant_key for response in valid if response.checkpoint == "D90"
    }
    required_axis_levels = tuple(sample_contract["d90_axis_levels_required"])
    axis_counts = {
        axis_level: len(
            {
                response.participant_key
                for response in valid
                if response.checkpoint == "D90" and response.axis_level == axis_level
            }
        )
        for axis_level in required_axis_levels
    }
    assigned_counts = {
        group: sum(value == group for value in assignments.values())
        for group in ("normal_adaptation", "delayed_adaptation")
    }
    d90_group_counts = {
        group: len(
            {
                response.participant_key
                for response in valid
                if response.checkpoint == "D90" and response.group == group
            }
        )
        for group in assigned_counts
    }
    attrition_rates = {
        group: (1 - d90_group_counts[group] / assigned if assigned else 1.0)
        for group, assigned in assigned_counts.items()
    }
    sample_complete = (
        len(assignments) >= int(sample_contract["confirmatory_recruits_min"])
        and len(d90_participants) >= int(sample_contract["d90_valid_completers_min"])
        and axis_counts
        and min(axis_counts.values()) >= int(sample_contract["d90_axis_level_min"])
        and abs(
            attrition_rates["normal_adaptation"] - attrition_rates["delayed_adaptation"]
        )
        <= float(sample_contract["max_group_attrition_gap_percentage_points"]) / 100.0
    )
    checks.append(
        GateCheck(
            "research-sample-attrition-axis",
            "PASS" if sample_complete else missing_status,
            (
                f"recruits={len(assignments)}; d90={len(d90_participants)}; "
                f"minimum_axis_level={min(axis_counts.values()) if axis_counts else 0}; "
                f"attrition_gap={abs(attrition_rates['normal_adaptation'] - attrition_rates['delayed_adaptation']):.4f}"
            ),
        )
    )

    d7_contract = dict(analysis_contract["D7"])  # type: ignore[arg-type]
    d30_contract = dict(analysis_contract["D30_normal_adaptation"])  # type: ignore[arg-type]
    d90_contract = dict(analysis_contract["D90"])  # type: ignore[arg-type]
    threshold_specs = (
        (
            "D7",
            None,
            float(d7_contract["overall_rate_min"]),
            float(d7_contract["cluster_ci_lower_strictly_above"]),
            float(d7_contract["style_only_rate_min"]),
        ),
        (
            "D30",
            "normal_adaptation",
            float(d30_contract["overall_rate_min"]),
            float(d30_contract["cluster_ci_lower_strictly_above"]),
            float(d30_contract["style_only_rate_min"]),
        ),
        (
            "D90",
            None,
            float(d90_contract["overall_rate_min"]),
            float(d90_contract["cluster_ci_lower_strictly_above"]),
            float(d90_contract["style_only_rate_min"]),
        ),
    )
    checkpoint_rates: dict[str, float | None] = {}
    for checkpoint, group, minimum, ci_minimum, style_minimum in threshold_specs:
        selected = tuple(
            response
            for response in valid
            if response.checkpoint == checkpoint
            and (group is None or response.group == group)
        )
        style = tuple(
            response for response in selected if response.mode == "style_only"
        )
        rate = _rate(selected)
        style_rate = _rate(style)
        lower = _cluster_bootstrap_lower(
            _response_counts(selected),
            iterations=bootstrap_iterations,
            seed=f"research:{checkpoint}:{group or 'all'}",
        )
        checkpoint_rates[f"{checkpoint}:{group or 'all'}"] = rate
        if rate is None or style_rate is None or lower is None:
            status = missing_status
        else:
            status = (
                "PASS"
                if rate >= minimum
                and lower > ci_minimum
                and style_rate >= style_minimum
                else "FAIL"
            )
        checks.append(
            GateCheck(
                f"research-identification-{checkpoint.lower()}-{group or 'all'}",
                status,
                (
                    "checkpoint evidence unavailable"
                    if rate is None
                    else f"rate={rate:.4f}; cluster_ci_lower={lower:.4f}; style_only={style_rate:.4f}"
                ),
            )
        )

    d30_normal = tuple(
        response
        for response in valid
        if response.checkpoint == "D30" and response.group == "normal_adaptation"
    )
    d30_delayed = tuple(
        response
        for response in valid
        if response.checkpoint == "D30" and response.group == "delayed_adaptation"
    )
    difference = (
        None
        if _rate(d30_normal) is None or _rate(d30_delayed) is None
        else _rate(d30_normal) - _rate(d30_delayed)  # type: ignore[operator]
    )
    difference_lower = _cluster_difference_lower(
        d30_normal,
        d30_delayed,
        iterations=bootstrap_iterations,
        seed="research:d30-group-difference",
    )
    checks.append(
        GateCheck(
            "research-d30-adaptation-difference",
            (
                missing_status
                if difference is None or difference_lower is None
                else (
                    "PASS"
                    if difference >= float(d30_contract["delayed_group_difference_min"])
                    and difference_lower
                    > float(d30_contract["difference_cluster_ci_lower_strictly_above"])
                    else "FAIL"
                )
            ),
            (
                "D30 group evidence unavailable"
                if difference is None
                else f"difference={difference:.4f}; cluster_ci_lower={difference_lower:.4f}"
            ),
        )
    )

    d30_all_rate = _rate(
        tuple(response for response in valid if response.checkpoint == "D30")
    )
    d90_all_rate = _rate(
        tuple(response for response in valid if response.checkpoint == "D90")
    )
    checks.append(
        GateCheck(
            "research-d90-longitudinal-retention",
            (
                missing_status
                if d30_all_rate is None or d90_all_rate is None
                else (
                    "PASS"
                    if d90_all_rate
                    >= d30_all_rate - float(d90_contract["max_rate_drop_from_D30"])
                    else "FAIL"
                )
            ),
            (
                "D30 or D90 evidence unavailable"
                if d30_all_rate is None or d90_all_rate is None
                else f"d30={d30_all_rate:.4f}; d90={d90_all_rate:.4f}"
            ),
        )
    )

    d90_counts = _response_counts(
        tuple(response for response in valid if response.checkpoint == "D90")
    )
    high_performer_rate = (
        sum(correct >= 9 and total == 12 for correct, total in d90_counts.values())
        / len(d90_counts)
        if d90_counts
        else None
    )
    checks.append(
        GateCheck(
            "research-d90-nine-of-twelve",
            (
                missing_status
                if high_performer_rate is None
                else (
                    "PASS"
                    if high_performer_rate
                    >= float(d90_contract["participants_with_9_of_12_min"])
                    else "FAIL"
                )
            ),
            (
                "D90 participant evidence unavailable"
                if high_performer_rate is None
                else f"nine_of_twelve_rate={high_performer_rate:.4f}"
            ),
        )
    )
    return make_report(
        gate_id="slice13-longitudinal-research",
        generated_at=generated_at,
        checks=tuple(checks),
        metadata={
            "response_count": len(responses),
            "valid_response_count": len(valid),
            "assigned_participant_count": len(assignments),
            "d90_participant_count": len(d90_participants),
            "bootstrap_iterations": bootstrap_iterations,
            "collection_complete": collection_complete,
            "checkpoint_rates": checkpoint_rates,
            "version_binding": normalized_version_binding,
        },
    )
