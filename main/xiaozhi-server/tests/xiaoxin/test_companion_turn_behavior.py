from __future__ import annotations

from dataclasses import asdict

from core.xiaoxin.companion import CompanionExpressionStyle, CompanionPolicy
from core.xiaoxin.companion.turn_behavior import (
    TurnBehaviorPlanningInputs,
    plan_turn_behavior,
)


def test_turn_behavior_plan_is_distinct_varied_and_policy_bounded():
    focused_policy = CompanionPolicy(
        xiaoxin_age=4,
        relationship_stage="attuned",
        response_length="short",
        question_budget=0,
        memory_reference_budget=1,
        initiative_level="disabled",
        emotional_posture="warm",
        closure_style="concise",
        expression_style=CompanionExpressionStyle(
            exploration_orientation="focused",
            expression_energy="calm",
            thought_organization="structured",
            humor_level="none",
            initiative_bias="reserved",
        ),
    )
    exploratory_policy = CompanionPolicy(
        xiaoxin_age=4,
        relationship_stage="attuned",
        response_length="expanded",
        question_budget=2,
        memory_reference_budget=2,
        initiative_level="medium",
        emotional_posture="supportive",
        closure_style="relational",
        expression_style=CompanionExpressionStyle(
            exploration_orientation="exploratory",
            expression_energy="lively",
            thought_organization="intuitive",
            humor_level="medium",
            initiative_bias="proactive",
        ),
    )

    focused = plan_turn_behavior(
        TurnBehaviorPlanningInputs(
            policy=focused_policy,
            pet_id="pet-focused",
            turn_id="turn-shared",
            turn_count=18,
            context="ordinary",
            interaction_kind="conversation",
        )
    )
    exploratory = plan_turn_behavior(
        TurnBehaviorPlanningInputs(
            policy=exploratory_policy,
            pet_id="pet-exploratory",
            turn_id="turn-shared",
            turn_count=18,
            context="ordinary",
            interaction_kind="conversation",
        )
    )
    replay_variants = {
        tuple(
            asdict(
                plan_turn_behavior(
                    TurnBehaviorPlanningInputs(
                        policy=exploratory_policy,
                        pet_id="pet-exploratory",
                        turn_id=f"turn-{index}",
                        turn_count=18 + index,
                        context="ordinary",
                        interaction_kind="conversation",
                    )
                )
            ).items()
        )
        for index in range(8)
    }

    assert focused != exploratory
    assert focused.question_mode == "none"
    assert focused.initiative_hook == "none"
    assert len(replay_variants) > 1
    for plan in (focused, exploratory):
        assert len(plan.salient_traits) <= 2
        for value in asdict(plan).values():
            if isinstance(value, str):
                assert "。" not in value and "！" not in value and "？" not in value
