from __future__ import annotations

from dataclasses import dataclass
import hashlib

from .contracts import CompanionContext, CompanionPolicy, TurnBehaviorPlan


_MIDDLE_STYLE = {
    "exploration_orientation": "balanced",
    "expression_energy": "natural",
    "thought_organization": "balanced",
    "humor_level": "low",
    "initiative_bias": "timely",
}


@dataclass(frozen=True)
class TurnBehaviorPlanningInputs:
    policy: CompanionPolicy
    pet_id: str
    turn_id: str
    turn_count: int
    context: CompanionContext
    interaction_kind: str

    def __post_init__(self) -> None:
        if not self.pet_id.strip() or not self.turn_id.strip():
            raise ValueError("turn behavior identity must be non-empty")
        if self.turn_count < 0:
            raise ValueError("turn behavior turn count must be non-negative")


def _choice(inputs: TurnBehaviorPlanningInputs, key: str, values: tuple[str, ...]) -> str:
    digest = hashlib.sha256(
        f"{inputs.pet_id}\0{inputs.turn_id}\0{inputs.turn_count}\0{key}".encode("utf-8")
    ).digest()
    return values[digest[0] % len(values)]


def _primary_move(inputs: TurnBehaviorPlanningInputs) -> str:
    if inputs.context == "user_low_mood":
        return "emotional_support"
    if inputs.context == "success":
        energy = inputs.policy.expression_style.expression_energy
        if energy == "calm":
            return "acknowledge"
        if energy == "lively":
            return "celebrate"
        return _choice(inputs, "success", ("acknowledge", "celebrate"))
    if inputs.context in {"fact_explanation", "serious"}:
        return "direct_answer"
    if inputs.context == "open_learning_difficulty":
        return "practical_support"
    if inputs.context == "multi_task_choice":
        return "clarify"
    if inputs.context == "future_event":
        return "acknowledge"
    if inputs.interaction_kind in {"general_qa", "explicit_recall"}:
        return "direct_answer"
    orientation = inputs.policy.expression_style.exploration_orientation
    if orientation == "exploratory":
        return _choice(inputs, "primary", ("co_explore", "acknowledge"))
    if orientation == "focused":
        return _choice(inputs, "primary", ("direct_answer", "practical_support"))
    return _choice(inputs, "primary", ("acknowledge", "practical_support"))


def _information_order(inputs: TurnBehaviorPlanningInputs) -> str:
    organization = inputs.policy.expression_style.thought_organization
    if organization == "structured":
        return _choice(inputs, "order", ("key_point_first", "stepwise"))
    if organization == "intuitive":
        return _choice(inputs, "order", ("context_first", "collaborative"))
    return _choice(inputs, "order", ("key_point_first", "context_first"))


def _question_mode(inputs: TurnBehaviorPlanningInputs) -> str:
    if inputs.policy.question_budget <= 0:
        return "none"
    if inputs.context in {"user_low_mood", "serious", "explicit_boundary"}:
        return "needed_only"
    style = inputs.policy.expression_style
    if style.exploration_orientation == "exploratory":
        return _choice(inputs, "question", ("one_key_question", "light_invitation"))
    if style.initiative_bias == "proactive":
        return "one_key_question"
    return "needed_only"


def _support_move(inputs: TurnBehaviorPlanningInputs) -> str:
    if inputs.context == "user_low_mood":
        return _choice(inputs, "support", ("reflect", "validate", "ground"))
    if inputs.policy.emotional_posture in {"supportive", "attuned"}:
        return _choice(inputs, "support", ("validate", "next_step"))
    if inputs.context == "open_learning_difficulty":
        return "next_step"
    return "none"


def _closure_intent(inputs: TurnBehaviorPlanningInputs) -> str:
    choices = {
        "concise": ("concise",),
        "warm": ("warm", "leave_space"),
        "relational": ("leave_space", "warm"),
        "familiar": ("warm", "next_step"),
    }
    return _choice(inputs, "closure", choices[inputs.policy.closure_style])


def _initiative_hook(inputs: TurnBehaviorPlanningInputs) -> str:
    if inputs.policy.initiative_level == "disabled":
        return "none"
    if inputs.context not in {"future_event", "open_learning_difficulty", "ordinary"}:
        return "none"
    if inputs.policy.expression_style.initiative_bias == "reserved":
        return "none"
    return _choice(inputs, "initiative", ("none", "context_followup"))


def _salient_traits(inputs: TurnBehaviorPlanningInputs) -> tuple[str, ...]:
    style = inputs.policy.expression_style
    candidates = [
        dimension
        for dimension, middle in _MIDDLE_STYLE.items()
        if getattr(style, dimension) != middle
    ]
    if not candidates:
        candidates = ["thought_organization", "expression_energy"]
    offset = int.from_bytes(
        hashlib.sha256(
            f"{inputs.pet_id}\0{inputs.turn_count}\0traits".encode("utf-8")
        ).digest()[:2],
        "big",
    ) % len(candidates)
    rotated = candidates[offset:] + candidates[:offset]
    return tuple(rotated[:2])


def plan_turn_behavior(inputs: TurnBehaviorPlanningInputs) -> TurnBehaviorPlan:
    plan = TurnBehaviorPlan(
        primary_move=_primary_move(inputs),
        information_order=_information_order(inputs),
        question_mode=_question_mode(inputs),
        support_move=_support_move(inputs),
        closure_intent=_closure_intent(inputs),
        initiative_hook=_initiative_hook(inputs),
        salient_traits=_salient_traits(inputs),
    )
    if plan.question_mode != "none" and inputs.policy.question_budget <= 0:
        raise ValueError("turn behavior plan exceeds question budget")
    if plan.initiative_hook != "none" and inputs.policy.initiative_level == "disabled":
        raise ValueError("turn behavior plan exceeds initiative policy")
    return plan
