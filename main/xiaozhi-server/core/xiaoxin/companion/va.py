from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Mapping


MODEL_VERSION = "companion-va-v1"
BASELINE_VALENCE = 150
BASELINE_AROUSAL = 0
SNAPSHOT_TTL_SECONDS = 6 * 60 * 60
VALENCE_HALF_LIFE_SECONDS = 90 * 60
AROUSAL_HALF_LIFE_SECONDS = 35 * 60

EVENT_SPECS: Mapping[str, tuple[int, int, int, str]] = {
    "shared_success": (750, 600, 700, "celebration"),
    "helpful_resolution": (420, 200, 400, "ordinary"),
    "ordinary_chat": (220, 50, 180, "ordinary"),
    "user_distress": (80, -260, 800, "supportive_settled"),
    "negative_feedback": (100, -350, 900, "receptive_brief"),
}

AGE_DYNAMICS = {
    None: (1000, 1000, 1000, 1000),
    1: (1100, 1100, 1000, 920),
    2: (1000, 1000, 1000, 1000),
    3: (920, 950, 1000, 1080),
    4: (850, 900, 1000, 1150),
}
RELATIONSHIP_DYNAMICS = {
    "first_meeting": (850, 1150, 750, 1150),
    "familiar": (950, 1050, 900, 1050),
    "attuned": (1000, 1000, 1000, 1000),
    "long_term_companion": (950, 900, 1000, 1100),
}


@dataclass(frozen=True)
class VAState:
    valence: int
    arousal: int
    observed_at: str
    expires_at: str
    age: int | None
    relationship_stage: str
    context: str = "ordinary"


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("VA timestamps require a timezone")
    return parsed


def _round(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _clamp(value: int) -> int:
    return max(-1000, min(1000, value))


def _dynamics(age: int | None, stage: str) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    age_reactivity, age_recovery, age_expression, age_inertia = AGE_DYNAMICS.get(
        age, AGE_DYNAMICS[None]
    )
    relation_reactivity, relation_recovery, relation_expression, relation_inertia = (
        RELATIONSHIP_DYNAMICS.get(stage, RELATIONSHIP_DYNAMICS["first_meeting"])
    )
    scale = Decimal(1000)
    return (
        Decimal(age_reactivity * relation_reactivity) / (scale * scale),
        Decimal(age_recovery * relation_recovery) / (scale * scale),
        Decimal(age_expression * relation_expression) / (scale * scale),
        Decimal(age_inertia * relation_inertia) / (scale * scale),
    )


def baseline(*, now: str, age: int | None, relationship_stage: str) -> VAState:
    observed = _aware(now)
    return VAState(
        valence=BASELINE_VALENCE,
        arousal=BASELINE_AROUSAL,
        observed_at=observed.isoformat(),
        expires_at=(observed + timedelta(seconds=SNAPSHOT_TTL_SECONDS)).isoformat(),
        age=age,
        relationship_stage=relationship_stage,
    )


def decay(state: VAState, *, now: str) -> VAState:
    current = _aware(now)
    observed = _aware(state.observed_at)
    if current < observed or current >= _aware(state.expires_at):
        return baseline(
            now=now, age=state.age, relationship_stage=state.relationship_stage
        )
    elapsed = Decimal(str((current - observed).total_seconds()))
    _, recovery, _, _ = _dynamics(state.age, state.relationship_stage)

    def axis(value: int, base: int, half_life: int) -> int:
        with localcontext() as context:
            context.prec = 28
            remaining = Decimal(2) ** (-(elapsed * recovery) / Decimal(half_life))
            return _clamp(base + _round(Decimal(value - base) * remaining))

    return VAState(
        valence=axis(state.valence, BASELINE_VALENCE, VALENCE_HALF_LIFE_SECONDS),
        arousal=axis(state.arousal, BASELINE_AROUSAL, AROUSAL_HALF_LIFE_SECONDS),
        observed_at=current.isoformat(),
        expires_at=state.expires_at,
        age=state.age,
        relationship_stage=state.relationship_stage,
        context=state.context,
    )


def apply_event(
    state: VAState,
    *,
    kind: str,
    occurred_at: str,
    age: int | None,
    relationship_stage: str,
) -> VAState:
    target_v, target_a, strength, event_context = EVENT_SPECS[kind]
    current = decay(state, now=occurred_at)
    reactivity, _, _, inertia = _dynamics(age, relationship_stage)
    blend = min(Decimal(1), Decimal(strength) * reactivity / (Decimal(1000) * inertia))
    valence = _clamp(
        current.valence + _round(Decimal(target_v - current.valence) * blend)
    )
    arousal = _clamp(
        current.arousal + _round(Decimal(target_a - current.arousal) * blend)
    )
    if event_context == "supportive_settled":
        valence, arousal = max(0, min(250, valence)), max(-400, min(0, arousal))
    elif event_context == "receptive_brief":
        valence, arousal = max(0, min(200, valence)), max(-500, min(-150, arousal))
    occurred = _aware(occurred_at)
    return VAState(
        valence=valence,
        arousal=arousal,
        observed_at=occurred.isoformat(),
        expires_at=(occurred + timedelta(seconds=SNAPSHOT_TTL_SECONDS)).isoformat(),
        age=age,
        relationship_stage=relationship_stage,
        context=event_context,
    )


def semantic_projection(state: VAState) -> Mapping[str, object]:
    _, _, expression_gain, _ = _dynamics(state.age, state.relationship_stage)
    projected_valence = BASELINE_VALENCE + _round(
        Decimal(state.valence - BASELINE_VALENCE) * expression_gain
    )
    projected_arousal = _round(Decimal(state.arousal) * expression_gain)
    if state.context == "supportive_settled":
        posture, cadence, expression = (
            "supportive_settled",
            "gentle_settled",
            "quiet_warm",
        )
        constraints = ("no_humor", "no_celebration", "no_initiative_from_va")
    elif state.context == "receptive_brief":
        posture, cadence, expression = (
            "receptive_brief",
            "brief_settled",
            "attentive_still",
        )
        constraints = ("no_self_pity", "no_comfort_seeking", "no_initiative_from_va")
    elif projected_valence >= 450 and projected_arousal >= 250:
        posture, cadence, expression = (
            "bright_warm",
            "bright_but_bounded",
            "bright_pulse",
        )
        constraints = ("no_initiative_from_va",)
    elif projected_arousal <= -150:
        posture, cadence, expression = "gentle_warm", "settled", "quiet_warm"
        constraints = ("no_initiative_from_va",)
    else:
        posture, cadence, expression = "warm_neutral", "natural", "warm_idle"
        constraints = ("no_initiative_from_va",)
    if state.relationship_stage == "first_meeting":
        hardware_expression = {"kind": expression, "intensity": "low"}
    else:
        hardware_expression = {
            "kind": expression,
            "intensity": "medium" if expression == "bright_pulse" else "low",
        }
    return {
        "emotional_posture": posture,
        "voice_cadence": cadence,
        "hardware_expression": hardware_expression,
        "hard_constraints": constraints,
        "may_create_initiative": False,
    }
