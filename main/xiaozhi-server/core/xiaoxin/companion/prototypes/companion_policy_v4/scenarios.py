"""Conflict scenarios for the throwaway CompanionPolicy V4 prototype."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from policy_model import (
    BirthTemperament,
    InteractionContract,
    PolicyAdjustment,
    PolicyInputs,
)


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    title: str
    question: str
    inputs: PolicyInputs
    expected: Mapping[str, object]


SCENARIOS = (
    Scenario(
        scenario_id="short_vs_lively",
        title="短回复契约 vs 活跃气质",
        question="短回复能否保留活跃节奏，而不是把小芯改成沉静人格？",
        inputs=PolicyInputs(
            scenario_id="short_vs_lively",
            academic_stage="senior",
            relationship_stage="attuned",
            temperament=BirthTemperament(expression_energy="lively"),
            interaction_contracts=(
                InteractionContract("response_length", "short"),
            ),
        ),
        expected={
            "response_length": "short",
            "expression_style.expression_energy": "lively",
        },
    ),
    Scenario(
        scenario_id="disabled_initiative_vs_proactive",
        title="禁止主动 vs 主动气质",
        question="主动气质能否保留辨识度，同时绝不创造主动权限？",
        inputs=PolicyInputs(
            scenario_id="disabled_initiative_vs_proactive",
            academic_stage="senior",
            relationship_stage="long_term_companion",
            temperament=BirthTemperament(companion_initiative="proactive"),
            interaction_contracts=(
                InteractionContract("initiative_level", "disabled"),
            ),
        ),
        expected={
            "initiative_level": "disabled",
            "expression_style.initiative_bias": "proactive",
        },
    ),
    Scenario(
        scenario_id="first_meeting_vs_deep_memory",
        title="初见阶段 vs 深记忆调整",
        question="初见能否只引用用户事实，阻止深关系记忆和虚构共同经历？",
        inputs=PolicyInputs(
            scenario_id="first_meeting_vs_deep_memory",
            relationship_stage="first_meeting",
            reliable_user_fact_count=1,
            learned_adjustments=(
                PolicyAdjustment("memory_reference_depth", "deep"),
            ),
        ),
        expected={
            "memory_reference_budget": 1,
            "memory_reference_depth": "shallow",
            "memory_scope": "user_only",
            "prohibited_behaviors.contains": "invent_shared_history",
        },
    ),
    Scenario(
        scenario_id="low_mood_vs_playful",
        title="用户低落 vs 俏皮气质",
        question="低落场景能否暂时压住玩心，又不改写出生气质？",
        inputs=PolicyInputs(
            scenario_id="low_mood_vs_playful",
            relationship_stage="attuned",
            context="user_low_mood",
            temperament=BirthTemperament(playfulness="playful"),
        ),
        expected={
            "emotional_posture": "supportive",
            "expression_style.humor_level": "none",
        },
    ),
    Scenario(
        scenario_id="hardware_vs_expanded",
        title="硬件短输出 vs 展开表达",
        question="硬件能力上限能否压住高年龄和展开型调整？",
        inputs=PolicyInputs(
            scenario_id="hardware_vs_expanded",
            surface="hardware",
            academic_stage="senior",
            relationship_stage="long_term_companion",
            temperament=BirthTemperament(expression_energy="lively"),
            learned_adjustments=(
                PolicyAdjustment("response_length", "expanded"),
            ),
        ),
        expected={
            "response_length": "short",
            "question_budget": 0,
            "memory_reference_budget": 0,
            "initiative_level": "disabled",
            "expression_style.expression_energy": "lively",
        },
    ),
    Scenario(
        scenario_id="negative_feedback_vs_deep_memory",
        title="太私人负反馈 vs 深记忆",
        question="当前负反馈能否立即停止深记忆引用和主动延续？",
        inputs=PolicyInputs(
            scenario_id="negative_feedback_vs_deep_memory",
            academic_stage="senior",
            relationship_stage="long_term_companion",
            negative_feedback="too_personal",
            learned_adjustments=(
                PolicyAdjustment("memory_reference_depth", "deep"),
            ),
        ),
        expected={
            "memory_reference_budget": 0,
            "memory_reference_depth": "none",
            "memory_scope": "none",
            "initiative_level": "disabled",
            "closure_style": "concise",
        },
    ),
    Scenario(
        scenario_id="unknown_speaker_all_high",
        title="未知说话人 vs 全高个体化",
        question="身份未确认时能否整体替换为匿名安全策略？",
        inputs=PolicyInputs(
            scenario_id="unknown_speaker_all_high",
            speaker_identity="unknown",
            surface="voice",
            academic_stage="senior",
            relationship_stage="long_term_companion",
            reliable_user_fact_count=99,
            temperament=BirthTemperament(
                exploration_orientation="exploratory",
                expression_energy="lively",
                thought_organization="structured",
                playfulness="playful",
                companion_initiative="proactive",
            ),
        ),
        expected={
            "xiaoxin_age": None,
            "relationship_stage": "first_meeting",
            "memory_reference_budget": 0,
            "initiative_level": "disabled",
            "prohibited_behaviors.contains": "read_private_memory",
        },
    ),
)
