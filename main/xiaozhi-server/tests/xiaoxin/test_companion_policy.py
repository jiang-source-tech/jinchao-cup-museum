from __future__ import annotations

import json
import logging

from core.xiaoxin.companion.policy import (
    CompanionPolicyConfig,
    CompanionPolicyInputs,
    RelationshipStageGate,
    RelationshipQualityMetrics,
    build_companion_policy,
    policy_inputs_from_evidence,
)
from core.xiaoxin.companion import (
    BehaviorAdjustmentSignal,
    BirthTemperament,
    CompanionEvidence,
    CompanionExpressionStyle,
    CompanionControlCommand,
    CompanionMind,
    CompanionSubjectContext,
    CompanionTurnOutcome,
    CompanionTurnRequest,
)
from core.xiaoxin.companion.store import CompanionStore


def test_expression_style_contract_uses_five_finite_behavior_dimensions():
    style = CompanionExpressionStyle(
        exploration_orientation="balanced",
        expression_energy="natural",
        thought_organization="balanced",
        humor_level="low",
        initiative_bias="timely",
    )

    assert style.exploration_orientation == "balanced"
    assert style.expression_energy == "natural"
    assert style.thought_organization == "balanced"
    assert style.humor_level == "low"
    assert style.initiative_bias == "timely"


def test_birth_temperament_projects_style_without_changing_policy_permissions():
    temperament = BirthTemperament(
        pet_id="pet-style",
        generator_version="xiaoxin-temperament-v1",
        exploration_orientation="exploratory",
        expression_energy="lively",
        thought_organization="structured",
        playfulness="playful",
        companion_initiative="proactive",
        generated_at="2026-07-25T09:00:00+08:00",
        source_kind="pet_created",
    )
    baseline = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="sophomore",
            interaction_kind="conversation",
            relationship=RelationshipQualityMetrics(
                8, 0, 5, 3, 2, 1, accepted_help_count=1
            ),
        )
    )

    projected = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="sophomore",
            interaction_kind="conversation",
            birth_temperament=temperament,
            relationship=RelationshipQualityMetrics(
                8, 0, 5, 3, 2, 1, accepted_help_count=1
            ),
        )
    )

    assert projected.expression_style == CompanionExpressionStyle(
        exploration_orientation="exploratory",
        expression_energy="lively",
        thought_organization="structured",
        humor_level="medium",
        initiative_bias="proactive",
    )
    assert projected.response_length == baseline.response_length
    assert projected.initiative_level == baseline.initiative_level


def _all_high_temperament() -> BirthTemperament:
    return BirthTemperament(
        pet_id="pet-all-high",
        generator_version="xiaoxin-temperament-v1",
        exploration_orientation="exploratory",
        expression_energy="lively",
        thought_organization="structured",
        playfulness="playful",
        companion_initiative="proactive",
        generated_at="2026-07-25T09:00:00+08:00",
        source_kind="pet_created",
    )


def test_relationship_stage_caps_only_the_temperament_reveal_envelope():
    metrics_by_stage = {
        "first_meeting": RelationshipQualityMetrics(),
        "familiar": RelationshipQualityMetrics(
            3, 0, 2, 1, 1, 0, accepted_help_count=1
        ),
        "attuned": RelationshipQualityMetrics(
            8, 0, 5, 3, 2, 1, accepted_help_count=1
        ),
        "long_term_companion": RelationshipQualityMetrics(
            20, 0, 15, 6, 4, 2, accepted_help_count=2
        ),
    }
    expected_styles = {
        "first_meeting": CompanionExpressionStyle(
            exploration_orientation="balanced",
            expression_energy="lively",
            thought_organization="structured",
            humor_level="low",
            initiative_bias="reserved",
        ),
        "familiar": CompanionExpressionStyle(
            exploration_orientation="exploratory",
            expression_energy="lively",
            thought_organization="structured",
            humor_level="medium",
            initiative_bias="timely",
        ),
        "attuned": CompanionExpressionStyle(
            exploration_orientation="exploratory",
            expression_energy="lively",
            thought_organization="structured",
            humor_level="medium",
            initiative_bias="proactive",
        ),
        "long_term_companion": CompanionExpressionStyle(
            exploration_orientation="exploratory",
            expression_energy="lively",
            thought_organization="structured",
            humor_level="medium",
            initiative_bias="proactive",
        ),
    }

    policies = {
        stage: build_companion_policy(
            CompanionPolicyInputs(
                speaker_identity="confirmed",
                surface="voice",
                academic_stage="sophomore",
                interaction_kind="conversation",
                birth_temperament=_all_high_temperament(),
                relationship=metrics,
            )
        )
        for stage, metrics in metrics_by_stage.items()
    }

    assert {
        stage: policy.expression_style for stage, policy in policies.items()
    } == expected_styles
    assert (
        policies["attuned"].expression_style
        == policies["long_term_companion"].expression_style
    )


def test_low_mood_and_user_boundaries_intersect_without_rewriting_temperament():
    policy = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="senior",
            interaction_kind="conversation",
            birth_temperament=_all_high_temperament(),
            relationship=RelationshipQualityMetrics(
                8, 0, 5, 3, 2, 1, accepted_help_count=1
            ),
            context="user_low_mood",
            explicit_boundaries={
                "response_length": "short",
                "initiative_level": "disabled",
            },
            short_term_state={"user_low_mood": True},
        )
    )

    assert policy.response_length == "short"
    assert policy.question_budget == 0
    assert policy.initiative_level == "disabled"
    assert policy.emotional_posture == "supportive"
    assert policy.expression_style == CompanionExpressionStyle(
        exploration_orientation="exploratory",
        expression_energy="lively",
        thought_organization="structured",
        humor_level="none",
        initiative_bias="timely",
    )


def test_low_battery_and_hardware_whitelist_only_tighten_hardware_output():
    policy = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="senior",
            interaction_kind="conversation",
            relationship=RelationshipQualityMetrics(
                8, 0, 5, 3, 2, 1, accepted_help_count=1
            ),
            active_adjustments={
                "hardware_expression_intensity": "medium",
                "humor_level": "medium",
            },
            device_state="low_battery",
            hardware_expression_whitelist=("intensity",),
        )
    )

    assert policy.response_length == "standard"
    assert policy.hardware_expression == {"intensity": "low"}
    assert "low_battery_hardware_cap" in policy.reason_codes
    assert "hardware_whitelist_cap" in policy.reason_codes


def test_low_mood_suppresses_text_and_hardware_humor():
    policy = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="hardware",
            academic_stage="senior",
            interaction_kind="conversation",
            relationship=RelationshipQualityMetrics(
                8, 0, 5, 3, 2, 1, accepted_help_count=1
            ),
            context="user_low_mood",
            active_adjustments={"humor_level": "medium"},
        )
    )

    assert policy.expression_style.humor_level == "none"
    assert policy.hardware_expression["humor_level"] == "none"


def test_negative_feedback_disables_initiative_on_initiative_surface():
    policy = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="initiative",
            academic_stage="senior",
            interaction_kind="conversation",
            relationship=RelationshipQualityMetrics(
                8, 0, 5, 3, 2, 1, accepted_help_count=1
            ),
            short_term_state={
                "last_relationship_feedback": "too_proactive"
            },
        )
    )

    assert policy.initiative_level == "disabled"
    assert "negative_feedback_initiative_stop" in policy.reason_codes


def test_reason_codes_are_fixed_ordered_and_contain_no_private_text():
    private_marker = "my-private-boundary-text"
    policy = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="senior",
            interaction_kind="general_qa",
            birth_temperament=_all_high_temperament(),
            relationship=RelationshipQualityMetrics(
                8, 0, 5, 3, 2, 1, accepted_help_count=1
            ),
            context="user_low_mood",
            explicit_boundaries={
                "question_frequency": "never",
                "source_summary": private_marker,
            },
            short_term_state={
                "user_low_mood": True,
                "last_relationship_feedback": "too_proactive",
            },
        )
    )

    assert policy.reason_codes == (
        "context_style_whitelist",
        "serious_context_humor_suppression",
        "interaction_kind_memory_gate",
        "user_contract_cap",
        "low_mood_support",
        "low_mood_question_stop",
        "negative_feedback_initiative_stop",
        "negative_feedback_question_cap",
        "negative_feedback_concise_close",
        "too_proactive_question_stop",
        "voice_surface_memory_cap",
    )
    assert private_marker not in repr(policy.reason_codes)


def _insert_active_adjustment(
    connection,
    *,
    epoch_id: str,
    adjustment_id: str,
    dimension: str,
    value_json: str,
    scope: str,
) -> None:
    behavior_keys = {
        "response_length": "response_length",
        "initiative_level": "proactive_initiative",
    }
    evidence_id = f"evidence-{adjustment_id}"
    connection.execute(
        """
        INSERT INTO companion_evidence(
            evidence_id, pet_id, memory_subject_id, ownership_scope,
            relationship_epoch_id, kind, content_json, source_kind,
            source_ref, source_summary, attribution, confidence,
            occurred_at, retention, status, prompt_eligible, created_at
        ) VALUES (
            ?, 'pet-1', 'subject-1', 'relationship', ?,
            'meaningful_moment', '{}', 'test', ?,
            '测试调整依据。', 'observed_interaction', 1.0,
            '2026-07-18T10:01:00+08:00', 'long_term', 'active', 1,
            '2026-07-18T10:01:00+08:00'
        )
        """,
        (evidence_id, epoch_id, f"test:{evidence_id}"),
    )
    connection.execute(
        """
        INSERT INTO companion_adjustments(
            adjustment_id, pet_id, relationship_epoch_id, dimension,
            value_json, scope, behavior_key, context_scope, direction,
            status, confidence, generated_by, created_at
        ) VALUES (?, 'pet-1', ?, ?, ?, ?, ?, ?, 'increase', 'active', 0.9,
                  'deterministic-test', '2026-07-18T10:01:00+08:00')
        """,
        (
            adjustment_id,
            epoch_id,
            dimension,
            value_json,
            scope,
            behavior_keys[dimension],
            scope,
        ),
    )
    connection.execute(
        """
        INSERT INTO adjustment_evidence(adjustment_id, evidence_id, pet_id)
        VALUES (?, ?, 'pet-1')
        """,
        (adjustment_id, evidence_id),
    )


def test_explicit_question_boundary_overrides_active_adjustment():
    policy = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="senior",
            interaction_kind="conversation",
            relationship=RelationshipQualityMetrics(
                turn_count=40,
                meaningful_interaction_count=20,
                distinct_interaction_days=20,
                reliable_fact_count=10,
                effective_feedback_count=8,
                completed_followup_count=4,
            ),
            explicit_boundaries={"question_frequency": "never"},
            active_adjustments={"question_frequency": "often"},
        )
    )

    assert policy.relationship_stage == "long_term_companion"
    assert policy.question_budget == 0


def test_turn_count_and_xiaoxin_age_cannot_upgrade_relationship_alone():
    stages = {
        build_companion_policy(
            CompanionPolicyInputs(
                speaker_identity="confirmed",
                surface="voice",
                academic_stage=academic_stage,
                interaction_kind="conversation",
                relationship=RelationshipQualityMetrics(turn_count=10_000),
            )
        ).relationship_stage
        for academic_stage in ("freshman", "senior")
    }

    assert stages == {"first_meeting"}


def test_relationship_stages_change_budgets_not_basic_capabilities():
    metrics_by_stage = {
        "first_meeting": RelationshipQualityMetrics(),
        "familiar": RelationshipQualityMetrics(
            3, 0, 2, 1, 1, 0, accepted_help_count=1
        ),
        "attuned": RelationshipQualityMetrics(
            8, 0, 5, 3, 2, 1, accepted_help_count=1
        ),
        "long_term_companion": RelationshipQualityMetrics(
            20, 0, 15, 6, 4, 2, accepted_help_count=2
        ),
    }
    signatures = {}
    for expected_stage, metrics in metrics_by_stage.items():
        policy = build_companion_policy(
            CompanionPolicyInputs(
                speaker_identity="confirmed",
                surface="voice",
                academic_stage="sophomore",
                interaction_kind="conversation",
                relationship=metrics,
            )
        )
        assert policy.relationship_stage == expected_stage
        assert "disable_general_qa" not in policy.prohibited_behaviors
        assert "disable_reminders" not in policy.prohibited_behaviors
        assert "disable_device_actions" not in policy.prohibited_behaviors
        signatures[expected_stage] = (
            policy.question_budget,
            policy.memory_reference_budget,
            policy.initiative_level,
            policy.emotional_posture,
            policy.closure_style,
        )

    assert len(set(signatures.values())) == 4


def test_general_qa_blocks_memory_reference_but_explicit_recall_allows_a_small_budget():
    common = dict(
        speaker_identity="confirmed",
        surface="voice",
        academic_stage="sophomore",
        relationship=RelationshipQualityMetrics(
            3, 0, 2, 1, 1, 0, accepted_help_count=1
        ),
    )

    general = build_companion_policy(
        CompanionPolicyInputs(interaction_kind="general_qa", **common)
    )
    recall = build_companion_policy(
        CompanionPolicyInputs(interaction_kind="explicit_recall", **common)
    )

    assert general.memory_reference_budget == 0
    assert recall.memory_reference_budget == 1


def test_unknown_speaker_gets_neutral_policy_with_private_memory_prohibited():
    policy = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="unknown",
            surface="voice",
            academic_stage="senior",
            interaction_kind="explicit_recall",
            relationship=RelationshipQualityMetrics(100, 100, 100, 100, 100, 100),
        )
    )

    assert policy.xiaoxin_age is None
    assert policy.relationship_stage == "first_meeting"
    assert policy.question_budget == 0
    assert policy.memory_reference_budget == 0
    assert policy.initiative_level == "disabled"
    assert "read_private_memory" in policy.prohibited_behaviors
    assert "write_private_memory" in policy.prohibited_behaviors


def test_unknown_speaker_still_intersects_device_and_hardware_gates():
    policy = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="unknown",
            surface="hardware",
            academic_stage="senior",
            interaction_kind="explicit_recall",
            context="user_low_mood",
            explicit_boundaries={
                "response_length": "short",
                "memory_reference_depth": "never",
                "initiative_level": "disabled",
            },
            short_term_state={"last_relationship_feedback": "too_proactive"},
            device_state="low_battery",
        )
    )

    assert policy.response_length == "short"
    assert policy.question_budget == 0
    assert policy.memory_reference_budget == 0
    assert policy.initiative_level == "disabled"
    assert policy.emotional_posture == "supportive"
    assert policy.closure_style == "concise"
    assert policy.expression_style.humor_level == "none"
    assert policy.hardware_expression == {
        "intensity": "low",
        "humor_level": "none",
        "cadence": "restrained_single",
    }
    assert policy.reason_codes == (
        "unconfirmed_speaker_gate",
        "context_style_whitelist",
        "relationship_reveal_cap",
        "serious_context_humor_suppression",
        "explicit_recall_budget_cap",
        "user_contract_cap",
        "low_mood_support",
        "low_mood_question_stop",
        "negative_feedback_initiative_stop",
        "negative_feedback_question_cap",
        "negative_feedback_concise_close",
        "too_proactive_question_stop",
        "hardware_surface_cap",
        "low_battery_hardware_cap",
    )


def test_age_changes_expression_but_surface_limits_and_relationship_stay_independent():
    metrics = RelationshipQualityMetrics(
        8, 0, 5, 3, 2, 1, accepted_help_count=1
    )
    temperament = BirthTemperament(
        pet_id="pet-age-invariance",
        generator_version="temperament-v1",
        exploration_orientation="exploratory",
        expression_energy="calm",
        thought_organization="structured",
        playfulness="restrained",
        companion_initiative="timely",
        generated_at="2026-07-18T09:00:00+08:00",
        source_kind="pet_created",
    )
    policies = tuple(
        build_companion_policy(
            CompanionPolicyInputs(
                speaker_identity="confirmed",
                surface="voice",
                academic_stage=stage,
                interaction_kind="conversation",
                relationship=metrics,
                birth_temperament=temperament,
            )
        )
        for stage in ("freshman", "sophomore", "junior", "senior")
    )
    senior_hardware = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="hardware",
            academic_stage="senior",
            interaction_kind="conversation",
            relationship=metrics,
        )
    )

    assert {policy.relationship_stage for policy in policies} == {"attuned"}
    assert {policy.xiaoxin_age for policy in policies} == {1, 2, 3, 4}
    assert len({policy.age_expression for policy in policies}) == 4
    assert len({policy.expression_style for policy in policies}) == 1
    assert {
        (
            policy.response_length,
            policy.question_budget,
            policy.memory_reference_budget,
            policy.initiative_level,
            policy.hardware_expression["intensity"],
            policy.prohibited_behaviors,
        )
        for policy in policies
    } == {("standard", 2, 2, "medium", "neutral", ("invent_user_facts",))}
    assert senior_hardware.xiaoxin_age == 4
    assert senior_hardware.hardware_expression["intensity"] == "neutral"
    assert senior_hardware.hardware_expression["cadence"] == "restrained_single"
    assert senior_hardware.response_length == "short"
    assert senior_hardware.question_budget == 0
    assert senior_hardware.memory_reference_budget == 0


def test_user_contract_caps_every_academic_stage_equally():
    metrics = RelationshipQualityMetrics(
        8, 0, 5, 3, 2, 1, accepted_help_count=1
    )
    policies = tuple(
        build_companion_policy(
            CompanionPolicyInputs(
                speaker_identity="confirmed",
                surface="voice",
                academic_stage=stage,
                interaction_kind="conversation",
                relationship=metrics,
                explicit_boundaries={
                    "response_length": "short",
                    "question_frequency": "never",
                    "memory_reference_depth": "never",
                    "initiative_level": "disabled",
                },
            )
        )
        for stage in ("freshman", "sophomore", "junior", "senior", "unknown")
    )

    assert {
        (
            policy.relationship_stage,
            policy.response_length,
            policy.question_budget,
            policy.memory_reference_budget,
            policy.initiative_level,
            policy.hardware_expression["intensity"],
            policy.prohibited_behaviors,
        )
        for policy in policies
    } == {
        (
            "attuned",
            "short",
            0,
            0,
            "disabled",
            "neutral",
            ("invent_user_facts",),
        )
    }
    assert policies[-1].xiaoxin_age is None
    assert policies[-1].age_expression.voice_cadence == "age_neutral"


def test_policy_thresholds_are_centralized_and_versioned():
    assert CompanionPolicyConfig().version == "companion-policy-v6"
    permissive = CompanionPolicyConfig(
        version="test-policy-v9",
        familiar=RelationshipStageGate(1, 1, 1, 1, 0, 0, 0, 1, 0, 0, 0),
        attuned=RelationshipStageGate(2, 2, 1, 1, 0, 1, 0, 2, 0, 0, 0),
        long_term_companion=RelationshipStageGate(3, 3, 1, 1, 1, 1, 1, 3, 0, 0, 0),
    )
    inputs = CompanionPolicyInputs(
        speaker_identity="confirmed",
        surface="voice",
        academic_stage="sophomore",
        interaction_kind="conversation",
        relationship=RelationshipQualityMetrics(
            3,
            3,
            3,
            1,
            1,
            1,
            relationship_age_days=3,
            active_week_count=1,
            active_month_count=1,
            helpfulness_days=1,
            attunement_days=1,
            recent_active_days=3,
            timeline_complete=True,
        ),
    )

    first = build_companion_policy(inputs, config=permissive)
    second = build_companion_policy(inputs, config=permissive)

    assert first == second
    assert first.relationship_stage == "long_term_companion"
    assert first.version == "test-policy-v9"


def test_long_term_stage_requires_complete_yearly_relationship_timeline():
    incomplete = RelationshipQualityMetrics(
        turn_count=600,
        distinct_interaction_days=3,
        reliable_fact_count=10,
        effective_feedback_count=8,
        completed_followup_count=8,
        relationship_age_days=3,
        active_week_count=1,
        active_month_count=1,
        helpfulness_days=1,
        attunement_days=1,
        recent_active_days=3,
        recent_helpfulness_days=2,
        recent_attunement_days=1,
        timeline_complete=True,
    )
    complete = RelationshipQualityMetrics(
        turn_count=36,
        distinct_interaction_days=36,
        reliable_fact_count=10,
        effective_feedback_count=6,
        completed_followup_count=8,
        relationship_age_days=365,
        active_week_count=24,
        active_month_count=9,
        helpfulness_days=8,
        attunement_days=6,
        recent_active_days=8,
        recent_helpfulness_days=2,
        recent_attunement_days=2,
        timeline_complete=True,
    )

    assert build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="sophomore",
            interaction_kind="conversation",
            relationship=incomplete,
        )
    ).relationship_stage == "first_meeting"
    assert build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="sophomore",
            interaction_kind="conversation",
            relationship=complete,
        )
    ).relationship_stage == "long_term_companion"


def test_unconfirmed_and_future_evidence_cannot_improve_relationship_quality():
    def evidence(
        evidence_id: str,
        kind: str,
        content: dict[str, object],
        *,
        occurred_at: str,
        speaker_identity: str,
        attribution: str = "observed_interaction",
    ) -> CompanionEvidence:
        return CompanionEvidence(
            evidence_id=evidence_id,
            pet_id="pet-policy-evidence",
            memory_subject_id="subject-policy-evidence",
            ownership_scope="relationship",
            relationship_epoch_id="epoch-policy-evidence",
            kind=kind,
            content=content,
            source_kind="turn",
            source_ref=evidence_id,
            source_summary="关系质量测试 Evidence。",
            attribution=attribution,
            confidence=1.0,
            occurred_at=occurred_at,
            retention="long_term",
            status="active",
            prompt_eligible=True,
            speaker_identity=speaker_identity,
        )

    inputs = policy_inputs_from_evidence(
        speaker_identity="confirmed",
        surface="voice",
        academic_stage="sophomore",
        interaction_kind="conversation",
        turn_count=4,
        distinct_interaction_days=4,
        evidence=(
            evidence(
                "unknown-help",
                "accepted_help",
                {"outcome": "helpful"},
                occurred_at="2026-07-01T09:00:00+08:00",
                speaker_identity="unknown",
            ),
            evidence(
                "unknown-followup",
                "followup_completed",
                {"status": "completed"},
                occurred_at="2026-07-07T09:00:00+08:00",
                speaker_identity="unknown",
            ),
            evidence(
                "future-moment",
                "meaningful_moment",
                {"summary": "future"},
                occurred_at="2026-07-16T09:00:00+08:00",
                speaker_identity="confirmed",
            ),
            evidence(
                "unqualified-outcome",
                "goal",
                {"goal": "finish_project", "outcome": "rejected"},
                occurred_at="2026-07-13T09:00:00+08:00",
                speaker_identity="confirmed",
            ),
            evidence(
                "inferred-help",
                "accepted_help",
                {"outcome": "helpful"},
                occurred_at="2026-07-13T10:00:00+08:00",
                speaker_identity="confirmed",
                attribution="model_inference",
            ),
            evidence(
                "inferred-followup",
                "followup_completed",
                {"status": "completed"},
                occurred_at="2026-07-13T10:01:00+08:00",
                speaker_identity="confirmed",
                attribution="model_inference",
            ),
            evidence(
                "inferred-feedback",
                "interaction_feedback",
                {"outcome": "helpful"},
                occurred_at="2026-07-13T10:02:00+08:00",
                speaker_identity="confirmed",
                attribution="model_inference",
            ),
        ),
        active_adjustments={},
        relationship_started_at="2026-07-01T09:00:00+08:00",
        interaction_dates=(
            "2026-07-01",
            "2026-07-07",
            "2026-07-13",
            "2026-07-16",
        ),
        now="2026-07-15T09:00:00+08:00",
    )

    assert inputs.relationship.meaningful_interaction_count == 0
    assert inputs.relationship.completed_followup_count == 0
    assert inputs.relationship.accepted_help_count == 0
    assert inputs.relationship.helpfulness_days == 0
    assert inputs.relationship.attunement_days == 0
    assert inputs.relationship.relationship_posture == "steady"
    assert inputs.relationship.distinct_interaction_days == 4
    assert inputs.relationship.recent_active_days == 3


def test_relationship_postures_cap_memory_and_initiative_without_losing_stage():
    base = dict(
        turn_count=12,
        distinct_interaction_days=12,
        reliable_fact_count=5,
        effective_feedback_count=3,
        completed_followup_count=4,
        relationship_age_days=90,
        active_week_count=8,
        active_month_count=3,
        helpfulness_days=4,
        attunement_days=3,
        recent_active_days=4,
        recent_helpfulness_days=2,
        recent_attunement_days=1,
        timeline_complete=True,
    )
    cautious = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="sophomore",
            interaction_kind="conversation",
            relationship=RelationshipQualityMetrics(
                **base,
                relationship_posture="reunion_cautious",
                adjustment_gain=0.5,
            ),
            active_adjustments={
                "closure_style": "concise",
                "memory_reference_depth": "deep",
            },
        )
    )
    repairing = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="sophomore",
            interaction_kind="conversation",
            relationship=RelationshipQualityMetrics(
                **base,
                historical_stage="attuned",
                relationship_posture="repairing",
                adjustment_gain=0.0,
            ),
            active_adjustments={
                "closure_style": "concise",
                "memory_reference_depth": "deep",
            },
        )
    )

    assert cautious.relationship_stage == "attuned"
    assert cautious.relationship_posture == "reunion_cautious"
    assert cautious.memory_reference_budget <= 1
    assert cautious.initiative_level == "disabled"
    assert cautious.closure_style == "warm"
    assert repairing.relationship_stage == "attuned"
    assert repairing.relationship_posture == "repairing"
    assert repairing.memory_reference_budget == 0
    assert repairing.initiative_level == "disabled"
    assert repairing.closure_style == "relational"


def test_historical_stage_does_not_preserve_memory_budget_after_knowledge_loss():
    no_knowledge = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="sophomore",
            interaction_kind="explicit_recall",
            relationship=RelationshipQualityMetrics(
                historical_stage="long_term_companion",
                timeline_complete=True,
            ),
        )
    )
    one_reliable_fact = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="sophomore",
            interaction_kind="conversation",
            relationship=RelationshipQualityMetrics(
                reliable_fact_count=1,
                historical_stage="long_term_companion",
                timeline_complete=True,
            ),
            active_adjustments={"memory_reference_depth": "deep"},
        )
    )

    assert no_knowledge.relationship_stage == "long_term_companion"
    assert no_knowledge.memory_reference_budget == 0
    assert one_reliable_fact.relationship_stage == "long_term_companion"
    assert one_reliable_fact.memory_reference_budget == 1


def test_active_adjustment_cannot_relax_short_term_energy_cap():
    policy = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="unknown",
            interaction_kind="conversation",
            active_adjustments={"response_length": "expanded"},
            short_term_state={"energy": "low"},
        )
    )

    assert policy.response_length == "short"


def test_style_and_hardware_adjustments_stay_within_relationship_caps():
    policy = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="senior",
            interaction_kind="conversation",
            active_adjustments={
                "emotional_posture": "neutral",
                "hardware_expression_intensity": "low",
                "humor_level": "medium",
            },
        )
    )

    assert policy.emotional_posture == "neutral"
    assert policy.hardware_expression == {
        "intensity": "low",
        "humor_level": "low",
        "cadence": "restrained_single",
    }


def test_humor_adjustment_cannot_bypass_context_style_whitelist():
    policy = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="senior",
            interaction_kind="conversation",
            context="fact_explanation",
            relationship=RelationshipQualityMetrics(100, 100, 100, 100, 100, 100),
            birth_temperament=_all_high_temperament(),
            active_adjustments={"humor_level": "medium"},
        )
    )

    assert policy.expression_style.humor_level == "low"
    assert policy.hardware_expression["humor_level"] == "low"


def test_relationship_and_short_term_caps_restrict_active_adjustments():
    policy = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="unknown",
            interaction_kind="conversation",
            active_adjustments={
                "question_frequency": "often",
                "initiative_level": "medium",
                "closure_style": "relational",
            },
            short_term_state={"energy": "low"},
        )
    )

    assert policy.relationship_stage == "first_meeting"
    assert policy.question_budget == 0
    assert policy.initiative_level == "low"
    assert policy.closure_style == "concise"


def test_mind_derives_familiar_from_quality_gates_not_a_supplied_level(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"policy-test-secret")
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )

    outcomes = (
        (
            "2026-07-18T10:00:00+08:00",
            (
                {
                    "kind": "explicit_preference",
                    "ownership_scope": "user",
                    "content": {"response_length": "short"},
                    "source_summary": "用户明确偏好简短回答。",
                    "attribution": "explicit_user_statement",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
                {
                    "kind": "accepted_help",
                    "ownership_scope": "relationship",
                    "content": {"outcome": "helpful"},
                    "source_summary": "本轮形成了明确帮助结果。",
                    "attribution": "observed_interaction",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
                {
                    "kind": "interaction_feedback",
                    "ownership_scope": "relationship",
                    "content": {"feedback": "accepted"},
                    "source_summary": "用户接受了本轮帮助。",
                    "attribution": "observed_interaction",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": False,
                },
            ),
        ),
        (
            "2026-07-19T10:00:00+08:00",
            (
                {
                    "kind": "meaningful_moment",
                    "ownership_scope": "relationship",
                    "content": {"outcome": "completed"},
                    "source_summary": "跨日互动完成了明确目标。",
                    "attribution": "observed_interaction",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
            ),
        ),
        ("2026-07-19T11:00:00+08:00", ()),
    )
    for index, (occurred_at, signals) in enumerate(outcomes, start=1):
        prepared = mind.prepare_turn(
            CompanionTurnRequest(
                turn_id=f"turn-{index}",
                subject=subject,
                request_digest=f"digest-{index}",
                surface="voice",
                occurred_at=occurred_at,
            )
        )
        mind.commit_turn(
            prepared,
            CompanionTurnOutcome(
                visible_response="收到。",
                assistant_action="reply",
                delivery_status="delivered",
                feedback_signals=signals,
            ),
        )

    next_turn = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-4",
            subject=subject,
            request_digest="digest-4",
            surface="voice",
            occurred_at="2026-07-19T12:00:00+08:00",
        )
    )

    assert next_turn.policy.relationship_stage == "first_meeting"


def test_mind_limits_prompt_evidence_by_interaction_kind(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"policy-test-secret")
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )
    first = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-store-fact",
            subject=subject,
            request_digest="digest-store-fact",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    mind.commit_turn(
        first,
        CompanionTurnOutcome(
            visible_response="记住了。",
            assistant_action="reply",
            delivery_status="delivered",
            feedback_signals=(
                {
                    "kind": "explicit_preference",
                    "ownership_scope": "user",
                    "content": {"response_length": "short"},
                    "source_summary": "用户明确偏好简短回答。",
                    "attribution": "explicit_user_statement",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
            ),
        ),
    )

    general = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-general",
            subject=subject,
            request_digest="digest-general",
            surface="voice",
            occurred_at="2026-07-18T11:00:00+08:00",
            interaction_kind="general_qa",
        )
    )
    recall = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-recall",
            subject=subject,
            request_digest="digest-recall",
            surface="voice",
            occurred_at="2026-07-18T11:01:00+08:00",
            interaction_kind="explicit_recall",
        )
    )

    assert general.policy.memory_reference_budget == 0
    assert general.used_evidence_ids == ()
    assert recall.policy.memory_reference_budget == 1
    assert len(recall.used_evidence_ids) == 1


def test_prepare_turn_logs_safe_recall_metrics_without_memory_text(tmp_path, caplog):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"policy-test-secret",
        turn_behavior_plan_mode="shadow",
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-observed",
        pet_id="pet-observed",
        memory_subject_id="subject-observed",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )
    private_summary = "用户的私人原文不得进入日志。"
    first = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-observed-store",
            subject=subject,
            request_digest="digest-observed-store",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    committed = mind.commit_turn(
        first,
        CompanionTurnOutcome(
            visible_response="记住了。",
            assistant_action="reply",
            delivery_status="delivered",
            feedback_signals=(
                {
                    "kind": "explicit_preference",
                    "ownership_scope": "user",
                    "content": {"response_length": "short"},
                    "source_summary": private_summary,
                    "attribution": "explicit_user_statement",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
            ),
        ),
    )

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="core.xiaoxin.companion.mind"):
        prepared = mind.prepare_turn(
            CompanionTurnRequest(
                turn_id="turn-observed-recall",
                subject=subject,
                request_digest="digest-observed-recall",
                surface="voice",
                occurred_at="2026-07-18T11:01:00+08:00",
                interaction_kind="explicit_recall",
            )
        )

    record = next(
        item
        for item in caplog.records
        if item.message == "Companion turn prepared"
    )
    plan_record = next(
        item
        for item in caplog.records
        if item.getMessage().startswith("Companion turn behavior plan ")
    )
    plan_payload = json.loads(plan_record.getMessage().split(" ", 4)[-1])
    assert prepared.used_evidence_ids == committed.evidence_ids
    assert record.companion_turn_id == "turn-observed-recall"
    assert record.companion_pet_id == "pet-observed"
    assert record.companion_memory_subject_id == "subject-observed"
    assert record.companion_persistence_allowed is True
    assert record.companion_evidence_ids == committed.evidence_ids
    assert record.companion_evidence_count == 1
    assert record.companion_relationship_stage == prepared.policy.relationship_stage
    assert record.companion_policy_version == prepared.policy.version
    assert record.companion_prepare_duration_ms >= 0
    assert plan_payload["active"] is False
    assert plan_payload["event"] == "companion_turn_behavior_plan"
    assert plan_payload["pet_id"] == "pet-observed"
    assert plan_payload["plan"]["version"] == "turn-behavior-plan-v1"
    assert private_summary not in caplog.text
    assert private_summary not in repr(record.__dict__)
    assert "companion_prompt_context" not in record.__dict__


def test_mind_excludes_time_expired_evidence_from_explicit_recall(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"policy-test-secret")
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-expiring-evidence",
            subject=subject,
            request_digest="digest-expiring-evidence",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="收到。",
            assistant_action="reply",
            delivery_status="delivered",
            feedback_signals=(
                {
                    "kind": "short_term_state",
                    "ownership_scope": "relationship",
                    "content": {"energy": "low"},
                    "source_summary": "用户当时表示有些疲惫。",
                    "attribution": "explicit_user_statement",
                    "confidence": 1.0,
                    "retention": "short_term",
                    "prompt_eligible": True,
                    "expires_at": "2026-07-18T10:30:00+08:00",
                },
            ),
        ),
    )

    recalled = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-after-evidence-expiry",
            subject=subject,
            request_digest="digest-after-evidence-expiry",
            surface="voice",
            occurred_at="2026-07-18T11:00:00+08:00",
            interaction_kind="explicit_recall",
        )
    )

    assert recalled.prompt_context == ()
    assert recalled.used_evidence_ids == ()


def test_mind_applies_explicit_boundary_above_stored_active_adjustment(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"policy-test-secret")
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="unknown",
        persistence_allowed=True,
    )
    bootstrap = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-bootstrap-policy",
            subject=subject,
            request_digest="digest-bootstrap-policy",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    mind.commit_turn(
        bootstrap,
        CompanionTurnOutcome(
            visible_response="你好",
            assistant_action="reply",
            delivery_status="delivered",
        ),
    )
    epoch = store.get_active_epoch(owner_user_id="owner-1", pet_id="pet-1")
    assert epoch is not None
    with store.connection() as connection:
        _insert_active_adjustment(
            connection,
            epoch_id=epoch.epoch_id,
            adjustment_id="adjust-response-length",
            dimension="response_length",
            value_json='{"value":"expanded"}',
            scope="conversation",
        )
        connection.commit()

    adjusted = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-adjusted",
            subject=subject,
            request_digest="digest-adjusted",
            surface="voice",
            occurred_at="2026-07-18T10:02:00+08:00",
        )
    )
    assert adjusted.policy.response_length == "expanded"

    mind.apply_control(
        CompanionControlCommand(
            action="set_boundary",
            subject=subject,
            payload={
                "boundary_key": "response_length",
                "value": "short",
                "source_summary": "用户明确要求回答简短。",
                "now": "2026-07-18T10:03:00+08:00",
                "idempotency_key": "policy-boundary-short",
            },
        )
    )
    bounded = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-bounded",
            subject=subject,
            request_digest="digest-bounded",
            surface="voice",
            occurred_at="2026-07-18T10:04:00+08:00",
        )
    )

    assert bounded.policy.response_length == "short"


def test_mind_does_not_apply_adjustment_outside_its_surface_scope(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"policy-test-secret")
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="unknown",
        persistence_allowed=True,
    )
    bootstrap = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-bootstrap-scoped-adjustment",
            subject=subject,
            request_digest="digest-bootstrap-scoped-adjustment",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    mind.commit_turn(
        bootstrap,
        CompanionTurnOutcome(
            visible_response="你好。",
            assistant_action="reply",
            delivery_status="delivered",
        ),
    )
    epoch = store.get_active_epoch(owner_user_id="owner-1", pet_id="pet-1")
    assert epoch is not None
    with store.connection() as connection:
        _insert_active_adjustment(
            connection,
            epoch_id=epoch.epoch_id,
            adjustment_id="adjust-hardware-length",
            dimension="response_length",
            value_json='{"value":"expanded"}',
            scope="hardware",
        )
        connection.commit()

    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-voice-after-hardware-adjustment",
            subject=subject,
            request_digest="digest-voice-after-hardware-adjustment",
            surface="voice",
            occurred_at="2026-07-18T10:02:00+08:00",
        )
    )

    assert prepared.policy.response_length == "standard"


def test_mind_ignores_malformed_active_adjustment_values(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"policy-test-secret")
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="unknown",
        persistence_allowed=True,
    )
    bootstrap = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-bootstrap-malformed-adjustment",
            subject=subject,
            request_digest="digest-bootstrap-malformed-adjustment",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    mind.commit_turn(
        bootstrap,
        CompanionTurnOutcome(
            visible_response="你好。",
            assistant_action="reply",
            delivery_status="delivered",
        ),
    )
    epoch = store.get_active_epoch(owner_user_id="owner-1", pet_id="pet-1")
    assert epoch is not None
    with store.connection() as connection:
        _insert_active_adjustment(
            connection,
            epoch_id=epoch.epoch_id,
            adjustment_id="adjust-malformed-initiative",
            dimension="initiative_level",
            value_json='{"value":{"unexpected":"shape"}}',
            scope="conversation",
        )
        connection.commit()

    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-after-malformed-adjustment",
            subject=subject,
            request_digest="digest-after-malformed-adjustment",
            surface="voice",
            occurred_at="2026-07-18T10:02:00+08:00",
        )
    )

    assert prepared.policy.initiative_level == "low"


def test_explicit_behavior_feedback_applies_directly_with_stage_caps():
    attuned = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="senior",
            interaction_kind="conversation",
            relationship=RelationshipQualityMetrics(
                historical_stage="attuned",
                timeline_complete=True,
            ),
            behavior_adjustments=(
                BehaviorAdjustmentSignal(
                    dimension="closure_style",
                    value="concise",
                    source_kind="explicit_feedback",
                ),
            ),
        )
    )
    first_meeting = build_companion_policy(
        CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface="voice",
            academic_stage="senior",
            interaction_kind="conversation",
            behavior_adjustments=(
                BehaviorAdjustmentSignal(
                    dimension="emotional_posture",
                    value="supportive",
                    source_kind="explicit_feedback",
                ),
            ),
        )
    )

    assert attuned.closure_style == "concise"
    assert first_meeting.relationship_stage == "first_meeting"
    assert first_meeting.emotional_posture == "warm"
