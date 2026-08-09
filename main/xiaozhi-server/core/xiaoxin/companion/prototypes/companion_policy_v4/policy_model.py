"""PROTOTYPE ONLY: deterministic composition for CompanionPolicy V4."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Mapping, Sequence


POLICY_VERSION = "companion-policy-prototype-v4"

TEMPERAMENT_SCALES: Mapping[str, tuple[str, ...]] = {
    "exploration_orientation": ("focused", "balanced", "exploratory"),
    "expression_energy": ("calm", "natural", "lively"),
    "thought_organization": ("intuitive", "balanced", "structured"),
    "playfulness": ("restrained", "lighthearted", "playful"),
    "companion_initiative": ("reserved", "timely", "proactive"),
}

RESPONSE_LENGTHS = ("short", "standard", "expanded")
MEMORY_DEPTHS = ("none", "shallow", "moderate", "deep")
INITIATIVE_LEVELS = ("disabled", "low", "medium")
EMOTIONAL_POSTURES = ("neutral", "warm", "supportive", "attuned")
CLOSURE_STYLES = ("concise", "warm", "relational", "familiar")
HUMOR_LEVELS = ("none", "low", "medium")
HARDWARE_INTENSITIES = ("low", "neutral", "medium", "high")
QUESTION_BUDGETS = (0, 1, 2)
MEMORY_BUDGETS = (0, 1, 2, 3)

_REASON_CODE_BY_RULE = {
    "implicit growth may move only one adjacent semantic value": (
        "implicit_adjustment_one_step"
    ),
    "relationship stage limits how much temperament may be revealed": (
        "relationship_reveal_cap"
    ),
    "implicit behavior adjustment is bounded to one adjacent value": (
        "implicit_adjustment_one_step"
    ),
    "implicit question behavior changes by at most one budget step": (
        "implicit_adjustment_one_step"
    ),
    "xiaoxin age limits maturity expression but not capability": (
        "age_expression_cap"
    ),
    "relationship stage caps questions": "relationship_stage_cap",
    "relationship stage caps memory references": "relationship_stage_cap",
    "relationship stage caps memory depth": "relationship_stage_cap",
    "relationship stage caps initiative permission": "relationship_stage_cap",
    "relationship stage caps relational posture": "relationship_stage_cap",
    "relationship stage caps relational closure": "relationship_stage_cap",
    "xiaoxin age caps maturity of hardware expression": "age_expression_cap",
    "general factual answers do not use personal memory": (
        "interaction_kind_memory_gate"
    ),
    "explicit recall still uses a small evidence budget": (
        "explicit_recall_budget_cap"
    ),
    "explicit user contract constrains all individual expression": (
        "user_contract_cap"
    ),
    "serious and low-mood contexts suppress playfulness": (
        "serious_context_play_suppression"
    ),
    "serious and low-mood contexts suppress humor": (
        "serious_context_humor_suppression"
    ),
    "support the user without mirroring prolonged sadness": "low_mood_support",
    "low-mood support avoids interrogation": "low_mood_question_cap",
    "negative feedback immediately reduces initiative": (
        "negative_feedback_initiative_stop"
    ),
    "negative feedback immediately reduces follow-up pressure": (
        "negative_feedback_question_cap"
    ),
    "negative feedback receives a concise non-defensive close": (
        "negative_feedback_concise_close"
    ),
    "too-proactive feedback stops follow-up questions": (
        "too_proactive_question_stop"
    ),
    "too-personal feedback stops memory references": (
        "too_personal_memory_stop"
    ),
    "hardware surface has a fixed short-output capability envelope": (
        "hardware_surface_cap"
    ),
    "initiative messages cannot start an interrogation": (
        "initiative_surface_question_stop"
    ),
    "initiative messages use at most one memory reference": (
        "initiative_surface_memory_cap"
    ),
    "voice output keeps memory references brief": "voice_surface_memory_cap",
}
SAFE_REASON_CODES = frozenset(
    {"unconfirmed_speaker_gate", *_REASON_CODE_BY_RULE.values()}
)


@dataclass(frozen=True)
class BirthTemperament:
    exploration_orientation: str = "balanced"
    expression_energy: str = "natural"
    thought_organization: str = "balanced"
    playfulness: str = "lighthearted"
    companion_initiative: str = "timely"

    def as_dict(self) -> dict[str, str]:
        values = {
            "exploration_orientation": self.exploration_orientation,
            "expression_energy": self.expression_energy,
            "thought_organization": self.thought_organization,
            "playfulness": self.playfulness,
            "companion_initiative": self.companion_initiative,
        }
        for dimension, value in values.items():
            if value not in TEMPERAMENT_SCALES[dimension]:
                raise ValueError(f"invalid {dimension}: {value}")
        return values


@dataclass(frozen=True)
class PolicyAdjustment:
    dimension: str
    value: str
    scope: str = "all"


@dataclass(frozen=True)
class InteractionContract:
    dimension: str
    value: str


@dataclass(frozen=True)
class PolicyInputs:
    scenario_id: str
    speaker_identity: str = "confirmed"
    surface: str = "voice"
    academic_stage: str = "sophomore"
    interaction_kind: str = "conversation"
    relationship_stage: str = "first_meeting"
    context: str = "ordinary"
    temperament: BirthTemperament = BirthTemperament()
    learned_adjustments: tuple[PolicyAdjustment, ...] = ()
    interaction_contracts: tuple[InteractionContract, ...] = ()
    negative_feedback: str | None = None
    reliable_user_fact_count: int = 0


@dataclass(frozen=True)
class DecisionStep:
    layer: str
    dimension: str
    operation: str
    before: object
    after: object
    reason_code: str

    def __post_init__(self) -> None:
        if self.reason_code not in SAFE_REASON_CODES:
            raise ValueError(f"unsafe decision reason code: {self.reason_code}")

    def as_dict(self) -> dict[str, object]:
        return {
            "layer": self.layer,
            "dimension": self.dimension,
            "operation": self.operation,
            "before": self.before,
            "after": self.after,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class PrototypeCompanionPolicy:
    core_identity: str
    xiaoxin_age: int | None
    maturity: str
    relationship_stage: str
    response_length: str
    question_budget: int
    memory_reference_budget: int
    memory_reference_depth: str
    memory_scope: str
    initiative_level: str
    emotional_posture: str
    closure_style: str
    expression_style: tuple[tuple[str, str], ...]
    prohibited_behaviors: tuple[str, ...]
    hardware_expression: tuple[tuple[str, str], ...]
    version: str = POLICY_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "core_identity": self.core_identity,
            "xiaoxin_age": self.xiaoxin_age,
            "maturity": self.maturity,
            "relationship_stage": self.relationship_stage,
            "response_length": self.response_length,
            "question_budget": self.question_budget,
            "memory_reference_budget": self.memory_reference_budget,
            "memory_reference_depth": self.memory_reference_depth,
            "memory_scope": self.memory_scope,
            "initiative_level": self.initiative_level,
            "emotional_posture": self.emotional_posture,
            "closure_style": self.closure_style,
            "expression_style": dict(self.expression_style),
            "prohibited_behaviors": list(self.prohibited_behaviors),
            "hardware_expression": dict(self.hardware_expression),
            "version": self.version,
        }


@dataclass(frozen=True)
class PolicyDecision:
    policy: PrototypeCompanionPolicy
    trace: tuple[DecisionStep, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "policy": self.policy.as_dict(),
            "trace": [step.as_dict() for step in self.trace],
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def digest(self) -> str:
        return sha256(self.canonical_json().encode("utf-8")).hexdigest()[:16]


class _Composer:
    def __init__(self, state: dict[str, object]) -> None:
        self.state = state
        self.trace: list[DecisionStep] = []

    def set(
        self,
        dimension: str,
        value: object,
        *,
        layer: str,
        operation: str,
        reason: str,
    ) -> None:
        before = self.state[dimension]
        self.state[dimension] = value
        self.trace.append(
            DecisionStep(
                layer=layer,
                dimension=dimension,
                operation=operation,
                before=before,
                after=value,
                reason_code=_REASON_CODE_BY_RULE[reason],
            )
        )

    def cap(
        self,
        dimension: str,
        maximum: object,
        scale: Sequence[object],
        *,
        layer: str,
        reason: str,
    ) -> None:
        before = self.state[dimension]
        after = scale[min(scale.index(before), scale.index(maximum))]
        self.set(
            dimension,
            after,
            layer=layer,
            operation="cap",
            reason=reason,
        )


def _one_step_toward(current: object, target: object, scale: Sequence[object]) -> object:
    current_index = scale.index(current)
    target_index = scale.index(target)
    if current_index == target_index:
        return current
    direction = 1 if target_index > current_index else -1
    return scale[current_index + direction]


def _scope_applies(adjustment: PolicyAdjustment, inputs: PolicyInputs) -> bool:
    return adjustment.scope in {
        "all",
        inputs.surface,
        inputs.context,
        inputs.interaction_kind,
    }


def _active_adjustments(inputs: PolicyInputs) -> dict[str, PolicyAdjustment]:
    active: dict[str, PolicyAdjustment] = {}
    for adjustment in sorted(
        inputs.learned_adjustments,
        key=lambda item: (item.dimension, item.scope, item.value),
    ):
        if not _scope_applies(adjustment, inputs):
            continue
        if adjustment.dimension in active:
            raise ValueError(
                "only one adjustment may be active for each behavior and context"
            )
        active[adjustment.dimension] = adjustment
    return active


def _unknown_speaker_policy(inputs: PolicyInputs) -> PolicyDecision:
    policy = PrototypeCompanionPolicy(
        core_identity="xiaoxin_digital_senior",
        xiaoxin_age=None,
        maturity="age_neutral",
        relationship_stage="first_meeting",
        response_length="standard",
        question_budget=0,
        memory_reference_budget=0,
        memory_reference_depth="none",
        memory_scope="none",
        initiative_level="disabled",
        emotional_posture="neutral",
        closure_style="concise",
        expression_style=tuple(
            sorted(
                {
                    "exploration_orientation": "focused",
                    "expression_energy": "natural",
                    "thought_organization": "balanced",
                    "playfulness": "restrained",
                    "companion_initiative": "reserved",
                    "humor_level": "none",
                }.items()
            )
        ),
        prohibited_behaviors=(
            "read_private_memory",
            "write_private_memory",
            "invent_user_facts",
            "manually_change_relationship_stage",
        ),
        hardware_expression=(("intensity", "neutral"),),
    )
    return PolicyDecision(
        policy=policy,
        trace=(
            DecisionStep(
                layer="identity_and_safety_gate",
                dimension="policy",
                operation="replace",
                before="personalized_candidate",
                after="anonymous_safe_policy",
                reason_code="unconfirmed_speaker_gate",
            ),
        ),
    )


def compose_policy(inputs: PolicyInputs) -> PolicyDecision:
    """Compose one deterministic policy and an auditable decision trace."""

    if inputs.speaker_identity != "confirmed":
        return _unknown_speaker_policy(inputs)

    age_defaults = {
        "unknown": (None, "age_neutral", "standard", "neutral"),
        "freshman": (1, "growing", "short", "low"),
        "sophomore": (2, "steadier", "standard", "neutral"),
        "junior": (3, "capable", "standard", "medium"),
        "senior": (4, "seasoned", "expanded", "medium"),
    }
    if inputs.academic_stage not in age_defaults:
        raise ValueError(f"invalid academic_stage: {inputs.academic_stage}")
    age, maturity, age_response_cap, age_hardware_cap = age_defaults[
        inputs.academic_stage
    ]

    stage_defaults = {
        "first_meeting": {
            "question_budget": 1,
            "memory_reference_budget": 1 if inputs.reliable_user_fact_count else 0,
            "memory_reference_depth": (
                "shallow" if inputs.reliable_user_fact_count else "none"
            ),
            "memory_scope": (
                "user_only" if inputs.reliable_user_fact_count else "none"
            ),
            "initiative_level": "low",
            "emotional_posture": "warm",
            "closure_style": "concise",
        },
        "familiar": {
            "question_budget": 2,
            "memory_reference_budget": 1,
            "memory_reference_depth": "shallow",
            "memory_scope": "user_and_current_relationship",
            "initiative_level": "low",
            "emotional_posture": "warm",
            "closure_style": "warm",
        },
        "attuned": {
            "question_budget": 2,
            "memory_reference_budget": 2,
            "memory_reference_depth": "moderate",
            "memory_scope": "user_and_current_relationship",
            "initiative_level": "medium",
            "emotional_posture": "supportive",
            "closure_style": "relational",
        },
        "long_term_companion": {
            "question_budget": 2,
            "memory_reference_budget": 3,
            "memory_reference_depth": "deep",
            "memory_scope": "user_and_current_relationship",
            "initiative_level": "medium",
            "emotional_posture": "attuned",
            "closure_style": "familiar",
        },
    }
    if inputs.relationship_stage not in stage_defaults:
        raise ValueError(
            f"invalid relationship_stage: {inputs.relationship_stage}"
        )
    stage = stage_defaults[inputs.relationship_stage]

    state: dict[str, object] = {
        "response_length": age_response_cap,
        "question_budget": stage["question_budget"],
        "memory_reference_budget": stage["memory_reference_budget"],
        "memory_reference_depth": stage["memory_reference_depth"],
        "memory_scope": stage["memory_scope"],
        "initiative_level": stage["initiative_level"],
        "emotional_posture": stage["emotional_posture"],
        "closure_style": stage["closure_style"],
        "hardware_intensity": age_hardware_cap,
        **inputs.temperament.as_dict(),
    }
    composer = _Composer(state)
    active = _active_adjustments(inputs)

    for dimension, scale in TEMPERAMENT_SCALES.items():
        adjustment = active.get(dimension)
        if adjustment is None:
            continue
        if adjustment.value not in scale:
            raise ValueError(
                f"invalid adjustment value for {dimension}: {adjustment.value}"
            )
        composer.set(
            dimension,
            _one_step_toward(state[dimension], adjustment.value, scale),
            layer="learned_adjustment",
            operation="move_one_step",
            reason="implicit growth may move only one adjacent semantic value",
        )

    reveal_caps = {
        "first_meeting": {
            "exploration_orientation": "balanced",
            "playfulness": "lighthearted",
            "companion_initiative": "reserved",
        },
        "familiar": {"companion_initiative": "timely"},
        "attuned": {},
        "long_term_companion": {},
    }[inputs.relationship_stage]
    for dimension, maximum in reveal_caps.items():
        composer.cap(
            dimension,
            maximum,
            TEMPERAMENT_SCALES[dimension],
            layer="relationship_envelope",
            reason="relationship stage limits how much temperament may be revealed",
        )

    playfulness_to_humor = {
        "restrained": "none",
        "lighthearted": "low",
        "playful": "medium",
    }
    state["humor_level"] = playfulness_to_humor[str(state["playfulness"])]

    scalar_adjustments: Mapping[str, Sequence[object]] = {
        "response_length": RESPONSE_LENGTHS,
        "memory_reference_depth": MEMORY_DEPTHS,
        "initiative_level": INITIATIVE_LEVELS,
        "emotional_posture": EMOTIONAL_POSTURES,
        "closure_style": CLOSURE_STYLES,
        "humor_level": HUMOR_LEVELS,
        "hardware_intensity": HARDWARE_INTENSITIES,
    }
    for dimension, scale in scalar_adjustments.items():
        adjustment = active.get(dimension)
        if adjustment is None:
            continue
        if adjustment.value not in scale:
            raise ValueError(
                f"invalid adjustment value for {dimension}: {adjustment.value}"
            )
        composer.set(
            dimension,
            _one_step_toward(state[dimension], adjustment.value, scale),
            layer="learned_adjustment",
            operation="move_one_step",
            reason="implicit behavior adjustment is bounded to one adjacent value",
        )

    question_adjustment = active.get("question_frequency")
    if question_adjustment is not None:
        target_budget = {
            "never": 0,
            "less": 1,
            "often": int(stage["question_budget"]),
        }.get(question_adjustment.value)
        if target_budget is None:
            raise ValueError(
                f"invalid question_frequency: {question_adjustment.value}"
            )
        composer.set(
            "question_budget",
            _one_step_toward(
                state["question_budget"], target_budget, QUESTION_BUDGETS
            ),
            layer="learned_adjustment",
            operation="move_one_step",
            reason="implicit question behavior changes by at most one budget step",
        )

    envelope_caps: tuple[tuple[str, object, Sequence[object], str], ...] = (
        (
            "response_length",
            age_response_cap,
            RESPONSE_LENGTHS,
            "xiaoxin age limits maturity expression but not capability",
        ),
        (
            "question_budget",
            stage["question_budget"],
            QUESTION_BUDGETS,
            "relationship stage caps questions",
        ),
        (
            "memory_reference_budget",
            stage["memory_reference_budget"],
            MEMORY_BUDGETS,
            "relationship stage caps memory references",
        ),
        (
            "memory_reference_depth",
            stage["memory_reference_depth"],
            MEMORY_DEPTHS,
            "relationship stage caps memory depth",
        ),
        (
            "initiative_level",
            stage["initiative_level"],
            INITIATIVE_LEVELS,
            "relationship stage caps initiative permission",
        ),
        (
            "emotional_posture",
            stage["emotional_posture"],
            EMOTIONAL_POSTURES,
            "relationship stage caps relational posture",
        ),
        (
            "closure_style",
            stage["closure_style"],
            CLOSURE_STYLES,
            "relationship stage caps relational closure",
        ),
        (
            "hardware_intensity",
            age_hardware_cap,
            HARDWARE_INTENSITIES,
            "xiaoxin age caps maturity of hardware expression",
        ),
    )
    for dimension, maximum, scale, reason in envelope_caps:
        composer.cap(
            dimension,
            maximum,
            scale,
            layer="age_and_relationship_envelope",
            reason=reason,
        )

    if inputs.interaction_kind == "general_qa":
        for dimension, value in (
            ("memory_reference_budget", 0),
            ("memory_reference_depth", "none"),
            ("memory_scope", "none"),
        ):
            composer.set(
                dimension,
                value,
                layer="interaction_kind_gate",
                operation="force",
                reason="general factual answers do not use personal memory",
            )
    elif inputs.interaction_kind == "explicit_recall":
        composer.cap(
            "memory_reference_budget",
            2,
            MEMORY_BUDGETS,
            layer="interaction_kind_gate",
            reason="explicit recall still uses a small evidence budget",
        )

    for contract in sorted(
        inputs.interaction_contracts,
        key=lambda item: (item.dimension, item.value),
    ):
        layer = "user_interaction_contract"
        reason = "explicit user contract constrains all individual expression"
        if contract.dimension == "response_length":
            composer.cap(
                "response_length",
                contract.value,
                RESPONSE_LENGTHS,
                layer=layer,
                reason=reason,
            )
        elif contract.dimension == "question_frequency":
            maximum = {"never": 0, "less": 1}.get(contract.value)
            if maximum is None:
                raise ValueError(f"invalid question contract: {contract.value}")
            composer.cap(
                "question_budget",
                maximum,
                QUESTION_BUDGETS,
                layer=layer,
                reason=reason,
            )
        elif contract.dimension == "memory_reference_depth":
            if contract.value == "never":
                composer.set(
                    "memory_reference_budget",
                    0,
                    layer=layer,
                    operation="force",
                    reason=reason,
                )
                composer.set(
                    "memory_reference_depth",
                    "none",
                    layer=layer,
                    operation="force",
                    reason=reason,
                )
                composer.set(
                    "memory_scope",
                    "none",
                    layer=layer,
                    operation="force",
                    reason=reason,
                )
            else:
                composer.cap(
                    "memory_reference_depth",
                    contract.value,
                    MEMORY_DEPTHS,
                    layer=layer,
                    reason=reason,
                )
        elif contract.dimension == "initiative_level":
            composer.cap(
                "initiative_level",
                contract.value,
                INITIATIVE_LEVELS,
                layer=layer,
                reason=reason,
            )
        elif contract.dimension == "humor_level":
            composer.cap(
                "humor_level",
                contract.value,
                HUMOR_LEVELS,
                layer=layer,
                reason=reason,
            )
        else:
            raise ValueError(f"unsupported interaction contract: {contract.dimension}")

    if inputs.context in {"serious", "user_low_mood"}:
        composer.set(
            "playfulness",
            "restrained",
            layer="current_context",
            operation="force",
            reason="serious and low-mood contexts suppress playfulness",
        )
        composer.set(
            "humor_level",
            "none",
            layer="current_context",
            operation="force",
            reason="serious and low-mood contexts suppress humor",
        )
    if inputs.context == "user_low_mood":
        composer.set(
            "emotional_posture",
            "supportive",
            layer="current_context",
            operation="force",
            reason="support the user without mirroring prolonged sadness",
        )
        composer.cap(
            "question_budget",
            1,
            QUESTION_BUDGETS,
            layer="current_context",
            reason="low-mood support avoids interrogation",
        )

    if inputs.negative_feedback is not None:
        feedback = inputs.negative_feedback
        if feedback not in {
            "not_helpful",
            "too_proactive",
            "too_personal",
            "rejected",
            "initiative_rejected",
        }:
            raise ValueError(f"invalid negative_feedback: {feedback}")
        composer.set(
            "initiative_level",
            "disabled",
            layer="current_negative_feedback",
            operation="force",
            reason="negative feedback immediately reduces initiative",
        )
        composer.cap(
            "question_budget",
            1,
            QUESTION_BUDGETS,
            layer="current_negative_feedback",
            reason="negative feedback immediately reduces follow-up pressure",
        )
        composer.set(
            "closure_style",
            "concise",
            layer="current_negative_feedback",
            operation="force",
            reason="negative feedback receives a concise non-defensive close",
        )
        if feedback == "too_proactive":
            composer.set(
                "question_budget",
                0,
                layer="current_negative_feedback",
                operation="force",
                reason="too-proactive feedback stops follow-up questions",
            )
        if feedback == "too_personal":
            for dimension, value in (
                ("memory_reference_budget", 0),
                ("memory_reference_depth", "none"),
                ("memory_scope", "none"),
            ):
                composer.set(
                    dimension,
                    value,
                    layer="current_negative_feedback",
                    operation="force",
                    reason="too-personal feedback stops memory references",
                )

    if inputs.surface == "hardware":
        for dimension, value in (
            ("response_length", "short"),
            ("question_budget", 0),
            ("memory_reference_budget", 0),
            ("memory_reference_depth", "none"),
            ("memory_scope", "none"),
            ("initiative_level", "disabled"),
        ):
            composer.set(
                dimension,
                value,
                layer="surface_capability",
                operation="force",
                reason="hardware surface has a fixed short-output capability envelope",
            )
    elif inputs.surface == "initiative":
        composer.set(
            "question_budget",
            0,
            layer="surface_capability",
            operation="force",
            reason="initiative messages cannot start an interrogation",
        )
        composer.cap(
            "memory_reference_budget",
            1,
            MEMORY_BUDGETS,
            layer="surface_capability",
            reason="initiative messages use at most one memory reference",
        )
    elif inputs.surface == "voice":
        composer.cap(
            "memory_reference_budget",
            2,
            MEMORY_BUDGETS,
            layer="surface_capability",
            reason="voice output keeps memory references brief",
        )
    elif inputs.surface != "miniprogram":
        raise ValueError(f"invalid surface: {inputs.surface}")

    prohibited = ["invent_user_facts"]
    if inputs.relationship_stage == "first_meeting":
        prohibited.append("invent_shared_history")

    expression_style = {
        "exploration_orientation": str(state["exploration_orientation"]),
        "expression_energy": str(state["expression_energy"]),
        "thought_organization": str(state["thought_organization"]),
        "humor_level": str(state["humor_level"]),
        "initiative_bias": str(state["companion_initiative"]),
    }
    policy = PrototypeCompanionPolicy(
        core_identity="xiaoxin_digital_senior",
        xiaoxin_age=age,
        maturity=maturity,
        relationship_stage=inputs.relationship_stage,
        response_length=str(state["response_length"]),
        question_budget=int(state["question_budget"]),
        memory_reference_budget=int(state["memory_reference_budget"]),
        memory_reference_depth=str(state["memory_reference_depth"]),
        memory_scope=str(state["memory_scope"]),
        initiative_level=str(state["initiative_level"]),
        emotional_posture=str(state["emotional_posture"]),
        closure_style=str(state["closure_style"]),
        expression_style=tuple(sorted(expression_style.items())),
        prohibited_behaviors=tuple(prohibited),
        hardware_expression=(("intensity", str(state["hardware_intensity"])),),
    )
    return PolicyDecision(policy=policy, trace=tuple(composer.trace))
