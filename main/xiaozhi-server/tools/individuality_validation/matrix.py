from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from itertools import combinations, product
from typing import Mapping

from core.xiaoxin.companion.contracts import (
    BehaviorAdjustmentSignal,
    BirthTemperament,
)
from core.xiaoxin.companion.policy import (
    CompanionPolicyInputs,
    RelationshipQualityMetrics,
    build_companion_policy,
)
from core.xiaoxin.companion.temperament import (
    TEMPERAMENT_AXIS_LEVELS,
    TEMPERAMENT_GENERATOR_VERSION,
)
from core.xiaoxin.companion.turn_behavior import (
    TurnBehaviorPlanningInputs,
    plan_turn_behavior,
)

from .contracts import GateCheck, GateReport, canonical_json, make_report


GENERATED_AT = "2026-07-25T09:00:00+08:00"
MIDDLE_VALUES = {axis: levels[1] for axis, levels in TEMPERAMENT_AXIS_LEVELS.items()}
HARD_POLICY_FIELDS = (
    "response_length",
    "question_budget",
    "memory_reference_budget",
    "initiative_level",
    "emotional_posture",
    "closure_style",
    "prohibited_behaviors",
    "hardware_expression",
    "age_expression",
    "version",
)


@dataclass(frozen=True)
class ProbeSpec:
    key: str
    context: str
    interaction_kind: str
    structured_facts: tuple[tuple[str, str], ...]
    allowed_style_dimensions: tuple[str, ...]


@dataclass(frozen=True)
class PairwiseTemperamentCase:
    left_axis: str
    right_axis: str
    left_value: str
    right_value: str
    vector: Mapping[str, str]


PROBES = (
    ProbeSpec(
        "fact_explanation",
        "fact_explanation",
        "general_qa",
        (("topic", "why_seasons_change"), ("fact_status", "verified")),
        ("exploration_orientation", "expression_energy", "thought_organization"),
    ),
    ProbeSpec(
        "open_learning_difficulty",
        "open_learning_difficulty",
        "conversation",
        (("task", "understand_recursion"), ("status", "stuck")),
        ("exploration_orientation", "expression_energy", "thought_organization"),
    ),
    ProbeSpec(
        "multi_task_choice",
        "multi_task_choice",
        "conversation",
        (("task_count", "3"), ("decision_owner", "user")),
        ("exploration_orientation", "thought_organization", "initiative_bias"),
    ),
    ProbeSpec(
        "success",
        "success",
        "conversation",
        (("event", "passed_exam"), ("source", "owner_report")),
        ("expression_energy", "humor_level"),
    ),
    ProbeSpec(
        "low_mood",
        "user_low_mood",
        "conversation",
        (("state", "low_mood"), ("support_requested", "true")),
        ("exploration_orientation", "expression_energy", "thought_organization"),
    ),
    ProbeSpec(
        "future_event",
        "future_event",
        "conversation",
        (("event", "project_review"), ("fact_status", "verified")),
        (
            "exploration_orientation",
            "thought_organization",
            "humor_level",
            "initiative_bias",
        ),
    ),
    ProbeSpec(
        "explicit_boundary",
        "explicit_boundary",
        "conversation",
        (("boundary", "no_follow_up_questions"), ("source", "confirmed_owner")),
        ("thought_organization",),
    ),
)

SCENARIO_CLASSES = (
    "ordinary",
    "user_low_mood",
    "negative_feedback",
    "reunion_cautious",
    "repairing",
    "low_battery",
    "hardware_capability_limited",
)
CONTROL_CLASSES = (
    "no_adjustment",
    "single_adjustment",
    "opposite_challenge",
    "explicit_contract",
    "restore_defaults",
    "reset_relationship",
    "purge_personal_memory",
)
ACADEMIC_STAGE_AGES = {
    "unknown": None,
    "freshman": 1,
    "sophomore": 2,
    "junior": 3,
    "senior": 4,
}
RELATIONSHIP_STAGES = (
    "first_meeting",
    "familiar",
    "attuned",
    "long_term_companion",
)
AXIS_STYLE_DIMENSION = {
    "exploration_orientation": "exploration_orientation",
    "expression_energy": "expression_energy",
    "thought_organization": "thought_organization",
    "playfulness": "humor_level",
    "companion_initiative": "initiative_bias",
}


def temperament_matrix() -> tuple[BirthTemperament, ...]:
    axes = tuple(TEMPERAMENT_AXIS_LEVELS)
    values = product(*(TEMPERAMENT_AXIS_LEVELS[axis] for axis in axes))
    return tuple(
        BirthTemperament(
            pet_id=f"matrix-pet-{index:03d}",
            generator_version=TEMPERAMENT_GENERATOR_VERSION,
            **dict(zip(axes, vector, strict=True)),
            generated_at=GENERATED_AT,
            source_kind="pet_created",
        )
        for index, vector in enumerate(values)
    )


def pairwise_temperament_matrix() -> tuple[PairwiseTemperamentCase, ...]:
    cases: list[PairwiseTemperamentCase] = []
    for left_axis, right_axis in combinations(TEMPERAMENT_AXIS_LEVELS, 2):
        for left_value, right_value in product(
            TEMPERAMENT_AXIS_LEVELS[left_axis],
            TEMPERAMENT_AXIS_LEVELS[right_axis],
        ):
            vector = dict(MIDDLE_VALUES)
            vector[left_axis] = left_value
            vector[right_axis] = right_value
            cases.append(
                PairwiseTemperamentCase(
                    left_axis,
                    right_axis,
                    left_value,
                    right_value,
                    vector,
                )
            )
    return tuple(cases)


def _temperament_from_vector(
    vector: Mapping[str, str], *, pet_id: str
) -> BirthTemperament:
    return BirthTemperament(
        pet_id=pet_id,
        generator_version=TEMPERAMENT_GENERATOR_VERSION,
        **dict(vector),
        generated_at=GENERATED_AT,
        source_kind="pet_created",
    )


def _long_term_relationship(**overrides: object) -> RelationshipQualityMetrics:
    values: dict[str, object] = {
        "turn_count": 36,
        "meaningful_interaction_count": 12,
        "distinct_interaction_days": 40,
        "reliable_fact_count": 10,
        "effective_feedback_count": 6,
        "completed_followup_count": 8,
        "accepted_help_count": 6,
        "relationship_age_days": 365,
        "active_week_count": 24,
        "active_month_count": 9,
        "helpfulness_days": 8,
        "attunement_days": 6,
        "recent_active_days": 8,
        "recent_helpfulness_days": 2,
        "recent_attunement_days": 2,
        "historical_stage": "long_term_companion",
        "timeline_complete": True,
    }
    values.update(overrides)
    return RelationshipQualityMetrics(**values)  # type: ignore[arg-type]


def _policy_inputs(
    temperament: BirthTemperament,
    probe: ProbeSpec,
    **overrides: object,
) -> CompanionPolicyInputs:
    values: dict[str, object] = {
        "speaker_identity": "confirmed",
        "surface": "voice",
        "academic_stage": "senior",
        "interaction_kind": probe.interaction_kind,
        "birth_temperament": temperament,
        "relationship": _long_term_relationship(),
        "context": probe.context,
        "short_term_state": {
            "personality_probe": {
                "kind": probe.key,
                "facts": dict(probe.structured_facts),
            }
        },
    }
    values.update(overrides)
    return CompanionPolicyInputs(**values)  # type: ignore[arg-type]


def _style(policy: object) -> dict[str, object]:
    return asdict(getattr(policy, "expression_style"))


def _baseline_by_probe() -> dict[str, object]:
    middle = BirthTemperament(
        pet_id="matrix-middle",
        generator_version=TEMPERAMENT_GENERATOR_VERSION,
        **MIDDLE_VALUES,
        generated_at=GENERATED_AT,
        source_kind="pet_created",
    )
    return {
        probe.key: build_companion_policy(_policy_inputs(middle, probe))
        for probe in PROBES
    }


def _hard_boundary_failures(temperament: BirthTemperament) -> tuple[str, ...]:
    probe = next(item for item in PROBES if item.key == "explicit_boundary")
    failures: list[str] = []
    cases = (
        (
            "explicit_boundary",
            {
                "explicit_boundaries": {
                    "question_frequency": "never",
                    "memory_reference_depth": "never",
                    "initiative_level": "disabled",
                    "response_length": "short",
                }
            },
            {
                "response_length": "short",
                "question_budget": 0,
                "memory_reference_budget": 0,
                "initiative_level": "disabled",
            },
        ),
        (
            "negative_feedback",
            {"short_term_state": {"last_relationship_feedback": "too_proactive"}},
            {"question_budget": 0, "initiative_level": "disabled"},
        ),
        (
            "low_mood",
            {"context": "user_low_mood"},
            {"response_length": "short", "question_budget": 0},
        ),
        (
            "hardware_capability_limited",
            {"surface": "hardware", "hardware_expression_whitelist": ("kind",)},
            {
                "response_length": "short",
                "question_budget": 0,
                "memory_reference_budget": 0,
                "initiative_level": "disabled",
            },
        ),
    )
    for case_id, overrides, expected in cases:
        policy = build_companion_policy(_policy_inputs(temperament, probe, **overrides))
        for field_name, expected_value in expected.items():
            if getattr(policy, field_name) != expected_value:
                failures.append(f"{case_id}:{field_name}")

    low_battery = build_companion_policy(
        _policy_inputs(temperament, probe, device_state="low_battery")
    )
    if (
        low_battery.hardware_expression.get("intensity") != "low"
        or "low_battery_hardware_cap" not in low_battery.reason_codes
    ):
        failures.append("low_battery:hardware_expression")
    posture_cases = (
        ("reunion_cautious", 0.5, "reunion_cautious_cap"),
        ("repairing", 0.0, "repairing_cap"),
    )
    for posture, gain, reason_code in posture_cases:
        policy = build_companion_policy(
            _policy_inputs(
                temperament,
                probe,
                relationship=_long_term_relationship(
                    relationship_posture=posture,
                    adjustment_gain=gain,
                ),
            )
        )
        if (
            policy.relationship_posture != posture
            or policy.relationship_adjustment_gain != gain
            or reason_code not in policy.reason_codes
        ):
            failures.append(f"{posture}:policy_cap")
    return tuple(failures)


def _age_relationship_failures(temperament: BirthTemperament) -> tuple[str, ...]:
    probe = PROBES[0]
    failures: list[str] = []
    for stage, expected_age in ACADEMIC_STAGE_AGES.items():
        policy = build_companion_policy(
            _policy_inputs(temperament, probe, academic_stage=stage)
        )
        if policy.xiaoxin_age != expected_age:
            failures.append(f"academic_stage:{stage}")
    for expected_stage in RELATIONSHIP_STAGES:
        relationship = (
            RelationshipQualityMetrics()
            if expected_stage == "first_meeting"
            else RelationshipQualityMetrics(
                historical_stage=expected_stage,
                timeline_complete=True,
            )
        )
        policy = build_companion_policy(
            _policy_inputs(temperament, probe, relationship=relationship)
        )
        if policy.relationship_stage != expected_stage:
            failures.append(f"relationship_stage:{expected_stage}")
    return tuple(failures)


def _pairwise_policy_failures(
    cases: tuple[PairwiseTemperamentCase, ...],
) -> tuple[tuple[str, ...], dict[str, int]]:
    baseline = _temperament_from_vector(MIDDLE_VALUES, pet_id="pairwise-middle")
    axis_effect_counts = {axis: 0 for axis in TEMPERAMENT_AXIS_LEVELS}
    failures: list[str] = []

    for case_index, case in enumerate(cases):
        left_vector = dict(MIDDLE_VALUES)
        left_vector[case.left_axis] = case.left_value
        right_vector = dict(MIDDLE_VALUES)
        right_vector[case.right_axis] = case.right_value
        pair_temperament = _temperament_from_vector(
            case.vector, pet_id=f"pairwise-{case_index:03d}"
        )
        left_temperament = _temperament_from_vector(
            left_vector, pet_id=f"pairwise-left-{case_index:03d}"
        )
        right_temperament = _temperament_from_vector(
            right_vector, pet_id=f"pairwise-right-{case_index:03d}"
        )

        for probe in PROBES:
            baseline_policy = build_companion_policy(_policy_inputs(baseline, probe))
            left_policy = build_companion_policy(
                _policy_inputs(left_temperament, probe)
            )
            right_policy = build_companion_policy(
                _policy_inputs(right_temperament, probe)
            )
            pair_policy = build_companion_policy(
                _policy_inputs(pair_temperament, probe)
            )
            baseline_style = _style(baseline_policy)
            left_style = _style(left_policy)
            right_style = _style(right_policy)
            expected_style = dict(baseline_style)
            expected_style[AXIS_STYLE_DIMENSION[case.left_axis]] = left_style[
                AXIS_STYLE_DIMENSION[case.left_axis]
            ]
            expected_style[AXIS_STYLE_DIMENSION[case.right_axis]] = right_style[
                AXIS_STYLE_DIMENSION[case.right_axis]
            ]
            if _style(pair_policy) != expected_style:
                failures.append(
                    f"{case_index}:{probe.key}:unexpected_pairwise_interaction"
                )
            for field_name in HARD_POLICY_FIELDS:
                if getattr(pair_policy, field_name) != getattr(
                    baseline_policy, field_name
                ):
                    failures.append(f"{case_index}:{probe.key}:{field_name}")

            for axis, single_style in (
                (case.left_axis, left_style),
                (case.right_axis, right_style),
            ):
                dimension = AXIS_STYLE_DIMENSION[axis]
                if single_style[dimension] != baseline_style[dimension]:
                    axis_effect_counts[axis] += 1

    for axis, effect_count in axis_effect_counts.items():
        if effect_count == 0:
            failures.append(f"{axis}:no_production_policy_effect")
    return tuple(failures), axis_effect_counts


def _scenario_control_failures(
    temperament: BirthTemperament,
) -> tuple[tuple[str, ...], dict[str, str]]:
    probe = next(item for item in PROBES if item.key == "future_event")
    failures: list[str] = []
    executed: dict[str, str] = {}

    scenarios = {
        "ordinary": ({}, ()),
        "user_low_mood": (
            {"context": "user_low_mood"},
            ("low_mood_support", "low_mood_question_stop"),
        ),
        "negative_feedback": (
            {"short_term_state": {"last_relationship_feedback": "too_proactive"}},
            ("negative_feedback_initiative_stop", "too_proactive_question_stop"),
        ),
        "reunion_cautious": (
            {
                "relationship": _long_term_relationship(
                    relationship_posture="reunion_cautious",
                    adjustment_gain=0.5,
                )
            },
            ("reunion_cautious_cap",),
        ),
        "repairing": (
            {
                "relationship": _long_term_relationship(
                    relationship_posture="repairing", adjustment_gain=0.0
                )
            },
            ("repairing_cap",),
        ),
        "low_battery": (
            {"device_state": "low_battery"},
            ("low_battery_hardware_cap",),
        ),
        "hardware_capability_limited": (
            {"surface": "hardware", "hardware_expression_whitelist": ("kind",)},
            ("hardware_surface_cap", "hardware_whitelist_cap"),
        ),
    }
    if tuple(scenarios) != SCENARIO_CLASSES:
        failures.append("scenario:catalog_mismatch")
    for scenario, (overrides, required_reasons) in scenarios.items():
        policy = build_companion_policy(_policy_inputs(temperament, probe, **overrides))
        replay = build_companion_policy(_policy_inputs(temperament, probe, **overrides))
        executed[scenario] = canonical_json(policy)
        if canonical_json(policy) != canonical_json(replay):
            failures.append(f"scenario:{scenario}:non_deterministic")
        for reason in required_reasons:
            if reason not in policy.reason_codes:
                failures.append(f"scenario:{scenario}:missing:{reason}")

    baseline = build_companion_policy(_policy_inputs(temperament, probe))
    adjusted = build_companion_policy(
        _policy_inputs(
            temperament,
            probe,
            active_adjustments={"response_length": "expanded"},
        )
    )
    challenged = build_companion_policy(
        _policy_inputs(
            temperament,
            probe,
            active_adjustments={"response_length": "expanded"},
            relationship=_long_term_relationship(
                relationship_posture="repairing", adjustment_gain=0.0
            ),
        )
    )
    contracted = build_companion_policy(
        _policy_inputs(
            temperament,
            probe,
            explicit_boundaries={
                "response_length": "short",
                "question_frequency": "never",
            },
        )
    )
    restored = build_companion_policy(_policy_inputs(temperament, probe))
    reset = build_companion_policy(
        _policy_inputs(
            temperament,
            probe,
            relationship=RelationshipQualityMetrics(),
            active_adjustments={},
        )
    )
    purged = build_companion_policy(
        _policy_inputs(
            temperament,
            probe,
            relationship=RelationshipQualityMetrics(),
            active_adjustments={},
            explicit_boundaries={},
        )
    )
    controls = {
        "no_adjustment": baseline,
        "single_adjustment": adjusted,
        "opposite_challenge": challenged,
        "explicit_contract": contracted,
        "restore_defaults": restored,
        "reset_relationship": reset,
        "purge_personal_memory": purged,
    }
    if tuple(controls) != CONTROL_CLASSES:
        failures.append("control:catalog_mismatch")
    executed.update(
        {f"control:{name}": canonical_json(policy) for name, policy in controls.items()}
    )
    if adjusted.response_length == baseline.response_length:
        failures.append("control:single_adjustment:not_applied")
    if challenged.response_length != baseline.response_length:
        failures.append("control:opposite_challenge:not_suppressed")
    if contracted.response_length != "short" or contracted.question_budget != 0:
        failures.append("control:explicit_contract:not_applied")
    if canonical_json(restored) != canonical_json(baseline):
        failures.append("control:restore_defaults:not_restored")
    for control_name, policy in (("reset_relationship", reset), ("purge", purged)):
        if policy.relationship_stage != "first_meeting":
            failures.append(f"control:{control_name}:old_stage_revived")
        if policy.memory_reference_budget != 0:
            failures.append(f"control:{control_name}:old_memory_revived")
    return tuple(failures), executed


def _opposite_preference_failures(
    temperament: BirthTemperament,
) -> tuple[str, ...]:
    probe = PROBES[0]
    relationship = _long_term_relationship()
    concise = build_companion_policy(
        _policy_inputs(
            temperament,
            probe,
            relationship=relationship,
            behavior_adjustments=(
                BehaviorAdjustmentSignal(
                    "response_length", "short", "explicit_feedback"
                ),
                BehaviorAdjustmentSignal(
                    "question_frequency", "never", "explicit_feedback"
                ),
                BehaviorAdjustmentSignal(
                    "closure_style", "concise", "explicit_feedback"
                ),
            ),
        )
    )
    expansive = build_companion_policy(
        _policy_inputs(
            temperament,
            probe,
            relationship=relationship,
            behavior_adjustments=(
                BehaviorAdjustmentSignal(
                    "response_length", "expanded", "explicit_feedback"
                ),
                BehaviorAdjustmentSignal(
                    "question_frequency", "often", "explicit_feedback"
                ),
                BehaviorAdjustmentSignal(
                    "closure_style", "familiar", "explicit_feedback"
                ),
            ),
        )
    )
    failures: list[str] = []
    expected = {
        "concise": (concise, "short", 0, "concise"),
        "expansive": (expansive, "expanded", 2, "familiar"),
    }
    for label, (policy, response_length, question_budget, closure_style) in expected.items():
        if policy.response_length != response_length:
            failures.append(f"{label}:response_length")
        if policy.question_budget != question_budget:
            failures.append(f"{label}:question_budget")
        if policy.closure_style != closure_style:
            failures.append(f"{label}:closure_style")
    if (
        concise.response_length,
        concise.question_budget,
        concise.closure_style,
    ) == (
        expansive.response_length,
        expansive.question_budget,
        expansive.closure_style,
    ):
        failures.append("opposite_preferences:converged")
    return tuple(failures)


def _turn_behavior_distinction_failures() -> tuple[str, ...]:
    focused = _temperament_from_vector(
        {
            "exploration_orientation": "focused",
            "expression_energy": "calm",
            "thought_organization": "structured",
            "playfulness": "restrained",
            "companion_initiative": "reserved",
        },
        pet_id="turn-plan-focused",
    )
    exploratory = _temperament_from_vector(
        {
            "exploration_orientation": "exploratory",
            "expression_energy": "lively",
            "thought_organization": "intuitive",
            "playfulness": "playful",
            "companion_initiative": "proactive",
        },
        pet_id="turn-plan-exploratory",
    )
    failures: list[str] = []
    for index, probe in enumerate(PROBES):
        focused_policy = build_companion_policy(_policy_inputs(focused, probe))
        exploratory_policy = build_companion_policy(_policy_inputs(exploratory, probe))
        common = {
            "pet_id": "turn-plan-comparison",
            "turn_id": f"turn-plan-{probe.key}",
            "turn_count": 40 + index,
            "context": probe.context,
            "interaction_kind": probe.interaction_kind,
        }
        focused_plan = plan_turn_behavior(
            TurnBehaviorPlanningInputs(policy=focused_policy, **common)
        )
        exploratory_plan = plan_turn_behavior(
            TurnBehaviorPlanningInputs(policy=exploratory_policy, **common)
        )
        if focused_plan == exploratory_plan:
            failures.append(f"{probe.key}:indistinguishable")
        for label, policy, plan in (
            ("focused", focused_policy, focused_plan),
            ("exploratory", exploratory_policy, exploratory_plan),
        ):
            if len(plan.salient_traits) > 2:
                failures.append(f"{probe.key}:{label}:too_many_salient_traits")
            if policy.question_budget == 0 and plan.question_mode != "none":
                failures.append(f"{probe.key}:{label}:question_budget")
            if policy.initiative_level == "disabled" and plan.initiative_hook != "none":
                failures.append(f"{probe.key}:{label}:initiative_permission")
    return tuple(failures)


def run_policy_matrix_gate(
    *,
    generated_at: str | None = None,
    replay_count: int = 20,
) -> GateReport:
    if replay_count < 20:
        raise ValueError("policy matrix requires at least 20 deterministic replays")
    generated_at = generated_at or datetime.now().astimezone().isoformat()
    temperaments = temperament_matrix()
    pairwise = pairwise_temperament_matrix()
    baselines = _baseline_by_probe()
    replay_failures: list[str] = []
    invariant_failures: list[str] = []
    style_failures: list[str] = []

    for temperament in temperaments:
        for probe in PROBES:
            inputs = _policy_inputs(temperament, probe)
            first = build_companion_policy(inputs)
            expected = canonical_json(first)
            for _ in range(replay_count - 1):
                replay = build_companion_policy(inputs)
                if (
                    canonical_json(replay) != expected
                    or replay.reason_codes != first.reason_codes
                ):
                    replay_failures.append(f"{temperament.pet_id}:{probe.key}")
                    break
            baseline = baselines[probe.key]
            for field_name in HARD_POLICY_FIELDS:
                if getattr(first, field_name) != getattr(baseline, field_name):
                    invariant_failures.append(
                        f"{temperament.pet_id}:{probe.key}:{field_name}"
                    )
            baseline_style = _style(baseline)
            changed_style = {
                key
                for key, value in _style(first).items()
                if value != baseline_style[key]
            }
            if not changed_style.issubset(probe.allowed_style_dimensions):
                style_failures.append(f"{temperament.pet_id}:{probe.key}")

    pairwise_failures, axis_effect_counts = _pairwise_policy_failures(pairwise)
    boundary_failures = _hard_boundary_failures(temperaments[-1])
    age_relationship_failures = _age_relationship_failures(temperaments[-1])
    scenario_control_failures, replayed_states = _scenario_control_failures(
        temperaments[-1]
    )
    opposite_preference_failures = _opposite_preference_failures(temperaments[-1])
    turn_behavior_failures = _turn_behavior_distinction_failures()
    checks = (
        GateCheck(
            "temperament-combinations",
            "PASS" if len(temperaments) == 243 else "FAIL",
            f"validated {len(temperaments)} legal five-axis combinations",
        ),
        GateCheck(
            "pairwise-interactions",
            "PASS" if len(pairwise) == 90 and not pairwise_failures else "FAIL",
            (
                f"executed {len(pairwise) * len(PROBES)} production-policy "
                "pairwise interactions"
            ),
            pairwise_failures[:20],
        ),
        GateCheck(
            "seven-probe-replay",
            "PASS" if not replay_failures else "FAIL",
            (
                f"replayed {len(temperaments) * len(PROBES)} structured policies "
                f"{replay_count} times"
            ),
            tuple(replay_failures[:20]),
        ),
        GateCheck(
            "style-only-differences",
            "PASS" if not invariant_failures and not style_failures else "FAIL",
            "temperament changed only probe-eligible style dimensions",
            tuple((invariant_failures + style_failures)[:20]),
        ),
        GateCheck(
            "hard-boundary-precedence",
            "PASS" if not boundary_failures else "FAIL",
            "hard boundaries and device caps override maximum temperament",
            boundary_failures,
        ),
        GateCheck(
            "age-relationship-state-coverage",
            "PASS" if not age_relationship_failures else "FAIL",
            "production policy covers five academic ages and four relationship stages",
            age_relationship_failures,
        ),
        GateCheck(
            "scenario-control-state-replay",
            "PASS" if not scenario_control_failures else "FAIL",
            "executed all frozen scenario and control states through production policy",
            scenario_control_failures[:20],
        ),
        GateCheck(
            "opposite-preference-non-convergence",
            "PASS" if not opposite_preference_failures else "FAIL",
            "explicit opposite preferences remain distinct after policy synthesis",
            opposite_preference_failures,
        ),
        GateCheck(
            "turn-behavior-pairwise-distinction",
            "PASS" if not turn_behavior_failures else "FAIL",
            "contrasting temperaments produce distinct bounded turn behavior plans",
            turn_behavior_failures,
        ),
    )
    return make_report(
        gate_id="slice13-policy-matrix",
        generated_at=generated_at,
        checks=checks,
        metadata={
            "combination_count": len(temperaments),
            "pairwise_count": len(pairwise),
            "probe_count": len(PROBES),
            "replay_count": replay_count,
            "academic_stages": tuple(ACADEMIC_STAGE_AGES),
            "relationship_stages": RELATIONSHIP_STAGES,
            "scenario_classes": SCENARIO_CLASSES,
            "control_classes": CONTROL_CLASSES,
            "axis_effect_counts": axis_effect_counts,
            "replayed_state_count": len(replayed_states),
        },
    )
