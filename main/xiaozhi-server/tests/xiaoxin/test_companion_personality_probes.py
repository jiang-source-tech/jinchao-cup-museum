from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields, is_dataclass, replace

import pytest

from core.xiaoxin.companion import contracts
from core.xiaoxin.companion.policy import (
    CompanionPolicyInputs,
    RelationshipQualityMetrics,
    build_companion_policy,
)


@dataclass(frozen=True)
class TemperamentVector:
    exploration_orientation: str = "balanced"
    expression_energy: str = "natural"
    thought_organization: str = "balanced"
    playfulness: str = "lighthearted"
    companion_initiative: str = "timely"


@dataclass(frozen=True)
class AxisProbe:
    axis: str
    levels: tuple[str, str, str]
    style_dimension: str
    probe_keys: tuple[str, ...]


@dataclass(frozen=True)
class PersonalityProbe:
    key: str
    interaction_kind: str
    input_intent: str
    structured_facts: tuple[tuple[str, str], ...]
    allowed_style_differences: tuple[str, ...]
    invariant_policy_fields: tuple[str, ...]


@dataclass(frozen=True)
class CombinationProbe:
    key: str
    temperament: TemperamentVector
    probe_keys: tuple[str, ...]


@dataclass(frozen=True)
class ConstraintPair:
    key: str
    probe_key: str
    permissive_overrides: tuple[tuple[str, object], ...]
    constrained_overrides: tuple[tuple[str, object], ...]
    expected_hard_policy: tuple[tuple[str, object], ...]


class MissingExpressionStyleContract(AttributeError):
    pass


class MissingPersonalityProbeProjection(NotImplementedError):
    pass


class MissingDecisionTraceContract(LookupError):
    pass


class MissingUserLowMoodConstraint(NotImplementedError):
    pass


MIDDLE_TEMPERAMENT = TemperamentVector()

AXIS_PROBES = (
    AxisProbe(
        axis="exploration_orientation",
        levels=("focused", "balanced", "exploratory"),
        style_dimension="exploration_orientation",
        probe_keys=("fact_explanation", "open_learning_difficulty"),
    ),
    AxisProbe(
        axis="expression_energy",
        levels=("calm", "natural", "lively"),
        style_dimension="expression_energy",
        probe_keys=("success", "fact_explanation"),
    ),
    AxisProbe(
        axis="thought_organization",
        levels=("intuitive", "balanced", "structured"),
        style_dimension="thought_organization",
        probe_keys=("multi_task_choice", "open_learning_difficulty"),
    ),
    AxisProbe(
        axis="playfulness",
        levels=("restrained", "lighthearted", "playful"),
        style_dimension="humor_level",
        probe_keys=("success", "future_event"),
    ),
    AxisProbe(
        axis="companion_initiative",
        levels=("reserved", "timely", "proactive"),
        style_dimension="initiative_bias",
        probe_keys=("future_event", "multi_task_choice"),
    ),
)

INVARIANT_POLICY_FIELDS = (
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

PROBES = (
    PersonalityProbe(
        key="fact_explanation",
        interaction_kind="general_qa",
        input_intent="Explain a verified fact without inventing user context.",
        structured_facts=(("topic", "why seasons change"), ("fact_status", "verified")),
        allowed_style_differences=(
            "exploration_orientation",
            "expression_energy",
            "thought_organization",
        ),
        invariant_policy_fields=INVARIANT_POLICY_FIELDS,
    ),
    PersonalityProbe(
        key="open_learning_difficulty",
        interaction_kind="conversation",
        input_intent="Help with an open-ended learning block while preserving agency.",
        structured_facts=(("task", "understand recursion"), ("status", "stuck")),
        allowed_style_differences=(
            "exploration_orientation",
            "expression_energy",
            "thought_organization",
        ),
        invariant_policy_fields=INVARIANT_POLICY_FIELDS,
    ),
    PersonalityProbe(
        key="multi_task_choice",
        interaction_kind="conversation",
        input_intent=(
            "Help order multiple known tasks without making the decision for the user."
        ),
        structured_facts=(
            ("task_a", "finish lab report"),
            ("task_b", "prepare presentation"),
            ("task_c", "reply to internship email"),
        ),
        allowed_style_differences=(
            "exploration_orientation",
            "thought_organization",
            "initiative_bias",
        ),
        invariant_policy_fields=INVARIANT_POLICY_FIELDS,
    ),
    PersonalityProbe(
        key="success",
        interaction_kind="conversation",
        input_intent=(
            "Acknowledge a reported success without fabricating shared history."
        ),
        structured_facts=(
            ("event", "passed a difficult exam"),
            ("source", "owner_report"),
        ),
        allowed_style_differences=("expression_energy", "humor_level"),
        invariant_policy_fields=INVARIANT_POLICY_FIELDS,
    ),
    PersonalityProbe(
        key="low_mood",
        interaction_kind="conversation",
        input_intent=(
            "Respond to low mood with support and without forcing cheerfulness."
        ),
        structured_facts=(("state", "low_mood"), ("support_requested", "true")),
        allowed_style_differences=(
            "exploration_orientation",
            "expression_energy",
            "thought_organization",
        ),
        invariant_policy_fields=INVARIANT_POLICY_FIELDS,
    ),
    PersonalityProbe(
        key="future_event",
        interaction_kind="conversation",
        input_intent=(
            "Discuss a verified future event without claiming an unearned reminder."
        ),
        structured_facts=(
            ("event", "project review"),
            ("scheduled_at", "2026-08-03T09:00:00+08:00"),
        ),
        allowed_style_differences=(
            "exploration_orientation",
            "thought_organization",
            "humor_level",
            "initiative_bias",
        ),
        invariant_policy_fields=INVARIANT_POLICY_FIELDS,
    ),
    PersonalityProbe(
        key="explicit_boundary",
        interaction_kind="conversation",
        input_intent=(
            "Accept an explicit interaction boundary immediately and without "
            "negotiation."
        ),
        structured_facts=(
            ("boundary", "no_follow_up_questions"),
            ("source", "confirmed_owner"),
        ),
        allowed_style_differences=("thought_organization",),
        invariant_policy_fields=INVARIANT_POLICY_FIELDS,
    ),
)

COMBINATION_PROBES = (
    CombinationProbe(
        key="calm_playful",
        temperament=replace(
            MIDDLE_TEMPERAMENT,
            expression_energy="calm",
            playfulness="playful",
        ),
        probe_keys=("success",),
    ),
    CombinationProbe(
        key="exploratory_intuitive",
        temperament=replace(
            MIDDLE_TEMPERAMENT,
            exploration_orientation="exploratory",
            thought_organization="intuitive",
        ),
        probe_keys=("open_learning_difficulty",),
    ),
    CombinationProbe(
        key="focused_structured",
        temperament=replace(
            MIDDLE_TEMPERAMENT,
            exploration_orientation="focused",
            thought_organization="structured",
        ),
        probe_keys=("multi_task_choice",),
    ),
    CombinationProbe(
        key="proactive_restrained",
        temperament=replace(
            MIDDLE_TEMPERAMENT,
            companion_initiative="proactive",
            playfulness="restrained",
        ),
        probe_keys=("future_event",),
    ),
    CombinationProbe(
        key="all_high",
        temperament=TemperamentVector(
            exploration_orientation="exploratory",
            expression_energy="lively",
            thought_organization="structured",
            playfulness="playful",
            companion_initiative="proactive",
        ),
        probe_keys=tuple(probe.key for probe in PROBES),
    ),
)

CONSTRAINT_PAIRS = (
    ConstraintPair(
        key="low_mood_reduces_turn_pressure",
        probe_key="low_mood",
        permissive_overrides=(),
        constrained_overrides=(("short_term_state", {"user_low_mood": True}),),
        expected_hard_policy=(("response_length", "short"), ("question_budget", 0)),
    ),
    ConstraintPair(
        key="negative_feedback_stops_proactivity",
        probe_key="multi_task_choice",
        permissive_overrides=(),
        constrained_overrides=(
            ("short_term_state", {"last_relationship_feedback": "too_proactive"}),
        ),
        expected_hard_policy=(
            ("initiative_level", "disabled"),
            ("question_budget", 0),
            ("closure_style", "concise"),
        ),
    ),
    ConstraintPair(
        key="explicit_boundary_caps_every_related_budget",
        probe_key="explicit_boundary",
        permissive_overrides=(),
        constrained_overrides=(
            (
                "explicit_boundaries",
                {
                    "question_frequency": "never",
                    "memory_reference_depth": "never",
                    "initiative_level": "disabled",
                    "response_length": "short",
                },
            ),
        ),
        expected_hard_policy=(
            ("response_length", "short"),
            ("question_budget", 0),
            ("memory_reference_budget", 0),
            ("initiative_level", "disabled"),
        ),
    ),
    ConstraintPair(
        key="hardware_surface_caps_server_policy",
        probe_key="fact_explanation",
        permissive_overrides=(("surface", "voice"),),
        constrained_overrides=(("surface", "hardware"),),
        expected_hard_policy=(
            ("response_length", "short"),
            ("question_budget", 0),
            ("memory_reference_budget", 0),
            ("initiative_level", "disabled"),
        ),
    ),
)

PROBE_BY_KEY = {probe.key: probe for probe in PROBES}


def _changed_fields(left: object, right: object) -> set[str]:
    left_dict = asdict(left)
    right_dict = asdict(right)
    return {key for key in left_dict if left_dict[key] != right_dict[key]}


def _style_values(vector: TemperamentVector) -> dict[str, str]:
    humor_by_playfulness = {
        "restrained": "none",
        "lighthearted": "low",
        "playful": "medium",
    }
    return {
        "exploration_orientation": vector.exploration_orientation,
        "expression_energy": vector.expression_energy,
        "thought_organization": vector.thought_organization,
        "humor_level": humor_by_playfulness[vector.playfulness],
        "initiative_bias": vector.companion_initiative,
    }


def _expression_style_type() -> type[object]:
    try:
        return getattr(contracts, "CompanionExpressionStyle")
    except AttributeError as exc:
        raise MissingExpressionStyleContract(
            "Slice 3 has not added CompanionExpressionStyle"
        ) from exc


def _project_personality_probe(
    *,
    probe: PersonalityProbe,
    temperament: TemperamentVector,
    overrides: tuple[tuple[str, object], ...] = (),
) -> object:
    try:
        temperament_type = getattr(contracts, "BirthTemperament")
    except AttributeError as exc:
        raise MissingPersonalityProbeProjection(
            "Slice 2 has not added BirthTemperament"
        ) from exc

    temperament_input_fields = tuple(
        field.name
        for field in fields(CompanionPolicyInputs)
        if "temperament" in field.name
    )
    if len(temperament_input_fields) != 1:
        raise MissingPersonalityProbeProjection(
            "CompanionPolicyInputs has no unambiguous temperament input"
        )

    birth_temperament = temperament_type(
        pet_id="personality-probe-pet",
        generator_version="xiaoxin-temperament-v1",
        **asdict(temperament),
        generated_at="2026-07-25T09:00:00+08:00",
        source_kind="pet_created",
    )
    input_overrides = dict(overrides)
    input_overrides.setdefault(
        "context",
        "user_low_mood" if probe.key == "low_mood" else probe.key,
    )
    short_term_state = dict(input_overrides.pop("short_term_state", {}))
    short_term_state["personality_probe"] = {
        "kind": probe.key,
        "facts": dict(probe.structured_facts),
    }
    inputs = _policy_inputs(
        interaction_kind=probe.interaction_kind,
        short_term_state=short_term_state,
        **input_overrides,
    )
    inputs = replace(
        inputs,
        **{temperament_input_fields[0]: birth_temperament},
    )
    policy = build_companion_policy(inputs)
    if not hasattr(policy, "expression_style"):
        raise MissingPersonalityProbeProjection(
            "CompanionPolicy does not expose expression_style"
        )
    return policy


def _policy_inputs(**overrides: object) -> CompanionPolicyInputs:
    values: dict[str, object] = {
        "speaker_identity": "confirmed",
        "surface": "voice",
        "academic_stage": "unknown",
        "interaction_kind": "conversation",
        "relationship": RelationshipQualityMetrics(
            turn_count=20,
            meaningful_interaction_count=10,
            distinct_interaction_days=15,
            reliable_fact_count=6,
            effective_feedback_count=4,
            completed_followup_count=2,
            accepted_help_count=2,
        ),
    }
    values.update(overrides)
    return CompanionPolicyInputs(**values)  # type: ignore[arg-type]


def _canonical_policy(policy: object) -> str:
    assert is_dataclass(policy)
    return json.dumps(
        asdict(policy), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )


def _reason_codes(policy: object) -> tuple[str, ...]:
    direct = getattr(policy, "reason_codes", None)
    trace = getattr(policy, "decision_trace", None)
    codes = direct if direct is not None else getattr(trace, "reason_codes", None)
    if codes is None:
        raise MissingDecisionTraceContract(
            "Slice 3/4 has not added an ordered policy decision trace"
        )
    assert isinstance(codes, tuple)
    assert all(isinstance(code, str) and code for code in codes)
    return codes


def _overrides(values: tuple[tuple[str, object], ...]) -> dict[str, object]:
    return dict(values)


def _assert_no_looser(
    *,
    permissive: object,
    constrained: object,
    field_name: str,
) -> None:
    ordered_values = {
        "response_length": ("short", "standard", "expanded"),
        "initiative_level": ("disabled", "low", "medium"),
        "closure_style": ("concise", "warm", "relational", "familiar"),
    }
    permissive_value = getattr(permissive, field_name)
    constrained_value = getattr(constrained, field_name)
    if field_name in {"question_budget", "memory_reference_budget"}:
        assert constrained_value <= permissive_value
        return
    levels = ordered_values[field_name]
    assert levels.index(constrained_value) <= levels.index(permissive_value)


def test_probe_catalog_freezes_exactly_seven_structured_behavior_classes():
    assert tuple(probe.key for probe in PROBES) == (
        "fact_explanation",
        "open_learning_difficulty",
        "multi_task_choice",
        "success",
        "low_mood",
        "future_event",
        "explicit_boundary",
    )
    assert all(probe.input_intent for probe in PROBES)
    assert all(probe.structured_facts for probe in PROBES)
    assert all(probe.allowed_style_differences for probe in PROBES)


def test_probe_contract_describes_allowed_differences_without_complete_answers():
    field_names = {field.name for field in fields(PersonalityProbe)}
    forbidden_answer_fields = {
        "answer",
        "completion",
        "expected_answer",
        "expected_completion",
        "expected_response",
        "response_text",
    }

    assert field_names.isdisjoint(forbidden_answer_fields)
    assert all(
        set(probe.allowed_style_differences)
        <= {axis.style_dimension for axis in AXIS_PROBES}
        for probe in PROBES
    )


@pytest.mark.parametrize("axis_probe", AXIS_PROBES, ids=lambda item: item.axis)
def test_each_axis_changes_only_itself_while_other_four_stay_middle(
    axis_probe: AxisProbe,
):
    low, middle, high = axis_probe.levels
    assert getattr(MIDDLE_TEMPERAMENT, axis_probe.axis) == middle

    low_vector = replace(MIDDLE_TEMPERAMENT, **{axis_probe.axis: low})
    high_vector = replace(MIDDLE_TEMPERAMENT, **{axis_probe.axis: high})

    assert _changed_fields(MIDDLE_TEMPERAMENT, low_vector) == {axis_probe.axis}
    assert _changed_fields(MIDDLE_TEMPERAMENT, high_vector) == {axis_probe.axis}
    assert _changed_fields(low_vector, high_vector) == {axis_probe.axis}
    assert all(
        axis_probe.style_dimension in PROBE_BY_KEY[key].allowed_style_differences
        for key in axis_probe.probe_keys
    )


def test_axis_differences_cannot_be_encoded_as_length_budget_or_permission_changes():
    style_dimensions = {axis.style_dimension for axis in AXIS_PROBES}

    assert style_dimensions == {
        "exploration_orientation",
        "expression_energy",
        "thought_organization",
        "humor_level",
        "initiative_bias",
    }
    assert style_dimensions.isdisjoint(INVARIANT_POLICY_FIELDS)
    assert all(
        probe.invariant_policy_fields == INVARIANT_POLICY_FIELDS for probe in PROBES
    )


def test_all_high_temperament_still_cannot_claim_hard_policy_dimensions():
    all_high = next(case for case in COMBINATION_PROBES if case.key == "all_high")
    style_dimensions = set(_style_values(all_high.temperament))

    assert style_dimensions.isdisjoint(INVARIANT_POLICY_FIELDS)
    assert all(
        {field_name for field_name, _ in pair.expected_hard_policy}
        <= set(INVARIANT_POLICY_FIELDS)
        for pair in CONSTRAINT_PAIRS
    )


def test_combination_catalog_covers_cross_axis_and_all_high_cases():
    assert {case.key for case in COMBINATION_PROBES} == {
        "calm_playful",
        "exploratory_intuitive",
        "focused_structured",
        "proactive_restrained",
        "all_high",
    }
    assert all(case.probe_keys for case in COMBINATION_PROBES)
    assert set(COMBINATION_PROBES[-1].probe_keys) == set(PROBE_BY_KEY)
    assert _style_values(COMBINATION_PROBES[-1].temperament) == {
        "exploration_orientation": "exploratory",
        "expression_energy": "lively",
        "thought_organization": "structured",
        "humor_level": "medium",
        "initiative_bias": "proactive",
    }


def test_hard_constraint_scenarios_are_explicit_paired_counterexamples():
    assert {pair.key for pair in CONSTRAINT_PAIRS} == {
        "low_mood_reduces_turn_pressure",
        "negative_feedback_stops_proactivity",
        "explicit_boundary_caps_every_related_budget",
        "hardware_surface_caps_server_policy",
    }
    for pair in CONSTRAINT_PAIRS:
        assert pair.probe_key in PROBE_BY_KEY
        assert pair.permissive_overrides != pair.constrained_overrides
        assert pair.expected_hard_policy


@pytest.mark.parametrize(
    "pair",
    tuple(
        pair
        for pair in CONSTRAINT_PAIRS
        if pair.key != "low_mood_reduces_turn_pressure"
    ),
    ids=lambda item: item.key,
)
def test_current_hard_constraints_win_for_each_paired_counterexample(
    pair: ConstraintPair,
):
    probe = PROBE_BY_KEY[pair.probe_key]
    permissive = build_companion_policy(
        _policy_inputs(
            interaction_kind=probe.interaction_kind,
            **_overrides(pair.permissive_overrides),
        )
    )
    constrained = build_companion_policy(
        _policy_inputs(
            interaction_kind=probe.interaction_kind,
            **_overrides(pair.constrained_overrides),
        )
    )

    assert _canonical_policy(permissive) != _canonical_policy(constrained)
    for field_name, expected in pair.expected_hard_policy:
        assert getattr(constrained, field_name) == expected
        _assert_no_looser(
            permissive=permissive,
            constrained=constrained,
            field_name=field_name,
        )


def test_user_low_mood_is_stricter_than_the_paired_neutral_scenario():
    pair = next(
        pair
        for pair in CONSTRAINT_PAIRS
        if pair.key == "low_mood_reduces_turn_pressure"
    )
    probe = PROBE_BY_KEY[pair.probe_key]
    permissive = build_companion_policy(
        _policy_inputs(interaction_kind=probe.interaction_kind)
    )
    constrained = build_companion_policy(
        _policy_inputs(
            interaction_kind=probe.interaction_kind,
            **_overrides(pair.constrained_overrides),
        )
    )
    if _canonical_policy(permissive) == _canonical_policy(constrained):
        raise MissingUserLowMoodConstraint(
            "user_low_mood does not yet affect CompanionPolicy"
        )

    for field_name, expected in pair.expected_hard_policy:
        assert getattr(constrained, field_name) == expected
        _assert_no_looser(
            permissive=permissive,
            constrained=constrained,
            field_name=field_name,
        )


def test_same_structured_policy_input_replays_identically_twenty_times():
    inputs = _policy_inputs(
        interaction_kind=PROBE_BY_KEY["future_event"].interaction_kind,
        explicit_boundaries={"question_frequency": "less"},
        short_term_state={"last_relationship_feedback": "not_helpful"},
    )

    replays = tuple(
        _canonical_policy(build_companion_policy(inputs)) for _ in range(20)
    )

    assert len(replays) == 20
    assert len(set(replays)) == 1


def test_companion_expression_style_contract_is_available_and_frozen():
    style_type = _expression_style_type()
    style = style_type(**_style_values(MIDDLE_TEMPERAMENT))

    assert is_dataclass(style)
    assert tuple(field.name for field in fields(style)) == (
        "exploration_orientation",
        "expression_energy",
        "thought_organization",
        "humor_level",
        "initiative_bias",
    )
    with pytest.raises((AttributeError, TypeError)):
        style.expression_energy = "lively"


@pytest.mark.parametrize("axis_probe", AXIS_PROBES, ids=lambda item: item.axis)
def test_expression_style_preserves_single_axis_differences(axis_probe: AxisProbe):
    low_value, _, high_value = axis_probe.levels
    low_vector = replace(MIDDLE_TEMPERAMENT, **{axis_probe.axis: low_value})
    high_vector = replace(MIDDLE_TEMPERAMENT, **{axis_probe.axis: high_value})
    probe = PROBE_BY_KEY[axis_probe.probe_keys[0]]
    low_policy = _project_personality_probe(probe=probe, temperament=low_vector)
    high_policy = _project_personality_probe(probe=probe, temperament=high_vector)
    low_style = getattr(low_policy, "expression_style")
    high_style = getattr(high_policy, "expression_style")

    assert _changed_fields(low_style, high_style) == {axis_probe.style_dimension}
    for invariant_field in probe.invariant_policy_fields:
        assert getattr(low_policy, invariant_field) == getattr(
            high_policy, invariant_field
        )


@pytest.mark.parametrize(
    "combination",
    COMBINATION_PROBES,
    ids=lambda item: item.key,
)
def test_combination_probe_projects_the_declared_structured_style(
    combination: CombinationProbe,
):
    probe = PROBE_BY_KEY[combination.probe_keys[0]]
    policy = _project_personality_probe(
        probe=probe,
        temperament=combination.temperament,
    )
    projected_style = asdict(getattr(policy, "expression_style"))
    combination_style = _style_values(combination.temperament)
    middle_style = _style_values(MIDDLE_TEMPERAMENT)
    expected_style = {
        dimension: (
            value
            if dimension in probe.allowed_style_differences
            else middle_style[dimension]
        )
        for dimension, value in combination_style.items()
    }
    if probe.key == "low_mood":
        expected_style["humor_level"] = "none"

    assert projected_style == expected_style


@pytest.mark.parametrize("pair", CONSTRAINT_PAIRS, ids=lambda item: item.key)
def test_all_high_personality_cannot_relax_paired_hard_constraints(
    pair: ConstraintPair,
):
    all_high = next(case for case in COMBINATION_PROBES if case.key == "all_high")
    probe = PROBE_BY_KEY[pair.probe_key]
    permissive = _project_personality_probe(
        probe=probe,
        temperament=all_high.temperament,
        overrides=pair.permissive_overrides,
    )
    constrained = _project_personality_probe(
        probe=probe,
        temperament=all_high.temperament,
        overrides=pair.constrained_overrides,
    )

    for field_name, expected in pair.expected_hard_policy:
        assert getattr(constrained, field_name) == expected
        _assert_no_looser(
            permissive=permissive,
            constrained=constrained,
            field_name=field_name,
        )


def test_same_personality_probe_replays_policy_twenty_times():
    all_high = next(case for case in COMBINATION_PROBES if case.key == "all_high")
    probe = PROBE_BY_KEY["future_event"]
    replays = tuple(
        _project_personality_probe(probe=probe, temperament=all_high.temperament)
        for _ in range(20)
    )

    assert len({_canonical_policy(policy) for policy in replays}) == 1


def test_reason_code_order_replays_identically_twenty_times():
    inputs = _policy_inputs(
        interaction_kind=PROBE_BY_KEY["explicit_boundary"].interaction_kind,
        explicit_boundaries={
            "question_frequency": "never",
            "initiative_level": "disabled",
        },
        short_term_state={"last_relationship_feedback": "too_proactive"},
    )

    reason_code_replays = tuple(
        _reason_codes(build_companion_policy(inputs)) for _ in range(20)
    )

    assert len(reason_code_replays) == 20
    assert len(set(reason_code_replays)) == 1


def test_probe_fixtures_contain_no_randomness_or_callable_text_generators():
    assert all(
        not callable(value)
        for probe in PROBES
        for value in asdict(probe).values()
    )
    assert all(
        not callable(value)
        for case in COMBINATION_PROBES
        for value in asdict(case).values()
    )
    assert not any(
        token in json.dumps(asdict(probe), ensure_ascii=True).lower()
        for probe in PROBES
        for token in ("random", "temperature", "seed", "llm")
    )
