"""PROTOTYPE ONLY: deterministic short-lived Valence-Arousal model."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
import math
from typing import Literal, Mapping


Age = Literal[1, 2, 3, 4] | None
RelationshipStage = Literal[
    "first_meeting",
    "familiar",
    "attuned",
    "long_term_companion",
]
Surface = Literal["voice", "text", "hardware"]

SCALE = 1000
MIN_VALUE = -SCALE
MAX_VALUE = SCALE
BASELINE_VALENCE = 150
BASELINE_AROUSAL = 0
SNAPSHOT_TTL_SECONDS = 6 * 60 * 60
BASE_VALENCE_HALF_LIFE_SECONDS = 90 * 60
BASE_AROUSAL_HALF_LIFE_SECONDS = 35 * 60
MODEL_VERSION = "companion-va-v1"


@dataclass(frozen=True)
class AffectPoint:
    valence: int
    arousal: int

    def __post_init__(self) -> None:
        for name, value in (("valence", self.valence), ("arousal", self.arousal)):
            if not isinstance(value, int) or not MIN_VALUE <= value <= MAX_VALUE:
                raise ValueError(f"{name} must be an integer in [-1000, 1000]")

    def public(self) -> dict[str, float]:
        return {
            "valence": self.valence / SCALE,
            "arousal": self.arousal / SCALE,
        }


BASELINE = AffectPoint(BASELINE_VALENCE, BASELINE_AROUSAL)


@dataclass(frozen=True)
class Dynamics:
    reactivity: float
    recovery_rate: float
    expression_gain: float
    inertia: float


AGE_DYNAMICS: Mapping[Age, Dynamics] = {
    None: Dynamics(1.00, 1.00, 1.00, 1.00),
    1: Dynamics(1.10, 1.10, 1.00, 0.92),
    2: Dynamics(1.00, 1.00, 1.00, 1.00),
    3: Dynamics(0.92, 0.95, 1.00, 1.08),
    4: Dynamics(0.85, 0.90, 1.00, 1.15),
}

RELATIONSHIP_DYNAMICS: Mapping[RelationshipStage, Dynamics] = {
    "first_meeting": Dynamics(0.85, 1.15, 0.75, 1.15),
    "familiar": Dynamics(0.95, 1.05, 0.90, 1.05),
    "attuned": Dynamics(1.00, 1.00, 1.00, 1.00),
    "long_term_companion": Dynamics(0.95, 0.90, 1.00, 1.10),
}


@dataclass(frozen=True)
class EventSpec:
    target: AffectPoint
    strength: float
    context: str


EVENT_SPECS: Mapping[str, EventSpec] = {
    "shared_success": EventSpec(AffectPoint(750, 600), 0.70, "celebration"),
    "helpful_resolution": EventSpec(AffectPoint(420, 200), 0.40, "ordinary"),
    "ordinary_chat": EventSpec(AffectPoint(220, 50), 0.18, "ordinary"),
    "user_distress": EventSpec(AffectPoint(80, -260), 0.80, "user_distress"),
    "negative_feedback": EventSpec(AffectPoint(100, -350), 0.90, "negative_feedback"),
}


@dataclass(frozen=True)
class AffectEvent:
    event_id: str
    occurred_at: str
    kind: str

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id is required")
        _aware(self.occurred_at)
        if self.kind not in EVENT_SPECS:
            raise ValueError(f"unsupported affect event: {self.kind}")


@dataclass(frozen=True)
class AffectSnapshot:
    pet_id: str
    memory_subject_id: str
    relationship_epoch_id: str
    point: AffectPoint
    observed_at: str
    expires_at: str
    dynamics_age: Age
    dynamics_relationship_stage: RelationshipStage
    processed_event_ids: tuple[str, ...] = ()
    version: str = MODEL_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("pet_id", self.pet_id),
            ("memory_subject_id", self.memory_subject_id),
            ("relationship_epoch_id", self.relationship_epoch_id),
        ):
            if not value.strip():
                raise ValueError(f"{name} is required")
        observed = _aware(self.observed_at)
        expires = _aware(self.expires_at)
        if expires <= observed:
            raise ValueError("expires_at must be later than observed_at")
        if self.dynamics_age not in AGE_DYNAMICS:
            raise ValueError("dynamics_age is invalid")
        if self.dynamics_relationship_stage not in RELATIONSHIP_DYNAMICS:
            raise ValueError("dynamics_relationship_stage is invalid")
        if self.version != MODEL_VERSION:
            raise ValueError("snapshot version is invalid")

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["point"] = asdict(self.point)
        data["processed_event_ids"] = list(self.processed_event_ids)
        return data

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AffectSnapshot":
        point = value.get("point")
        if not isinstance(point, Mapping):
            raise ValueError("snapshot point is invalid")
        ids = value.get("processed_event_ids", [])
        if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
            raise ValueError("processed_event_ids is invalid")
        return cls(
            pet_id=str(value.get("pet_id", "")),
            memory_subject_id=str(value.get("memory_subject_id", "")),
            relationship_epoch_id=str(value.get("relationship_epoch_id", "")),
            point=AffectPoint(int(point["valence"]), int(point["arousal"])),
            observed_at=str(value.get("observed_at", "")),
            expires_at=str(value.get("expires_at", "")),
            dynamics_age=value.get("dynamics_age"),  # type: ignore[arg-type]
            dynamics_relationship_stage=str(
                value.get("dynamics_relationship_stage", "")
            ),  # type: ignore[arg-type]
            processed_event_ids=tuple(ids),
            version=str(value.get("version", "")),
        )


@dataclass(frozen=True)
class EventResult:
    status: str
    snapshot: AffectSnapshot
    event_kind: str
    context: str


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must include a timezone offset")
    return parsed


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _clamp(value: int) -> int:
    return max(MIN_VALUE, min(MAX_VALUE, value))


def _round_half_away(value: float) -> int:
    if value >= 0:
        return math.floor(value + 0.5)
    return math.ceil(value - 0.5)


def dynamics_for(age: Age, relationship_stage: RelationshipStage) -> Dynamics:
    age_part = AGE_DYNAMICS[age]
    relationship_part = RELATIONSHIP_DYNAMICS[relationship_stage]
    return Dynamics(
        reactivity=age_part.reactivity * relationship_part.reactivity,
        recovery_rate=age_part.recovery_rate * relationship_part.recovery_rate,
        expression_gain=age_part.expression_gain * relationship_part.expression_gain,
        inertia=age_part.inertia * relationship_part.inertia,
    )


def baseline_snapshot(
    *,
    pet_id: str = "pet-prototype",
    memory_subject_id: str = "subject-prototype",
    relationship_epoch_id: str = "epoch-prototype",
    observed_at: str,
    age: Age = 2,
    relationship_stage: RelationshipStage = "familiar",
    processed_event_ids: tuple[str, ...] = (),
) -> AffectSnapshot:
    now = _aware(observed_at)
    return AffectSnapshot(
        pet_id=pet_id,
        memory_subject_id=memory_subject_id,
        relationship_epoch_id=relationship_epoch_id,
        point=BASELINE,
        observed_at=_iso(now),
        expires_at=_iso(now + timedelta(seconds=SNAPSHOT_TTL_SECONDS)),
        dynamics_age=age,
        dynamics_relationship_stage=relationship_stage,
        processed_event_ids=processed_event_ids,
    )


def _decay_axis(value: int, baseline: int, elapsed_seconds: float, half_life: float) -> int:
    if elapsed_seconds <= 0:
        return value
    remaining = math.exp2(-elapsed_seconds / half_life)
    return _clamp(baseline + _round_half_away((value - baseline) * remaining))


def decay_point(
    point: AffectPoint,
    *,
    elapsed_seconds: float,
    dynamics: Dynamics,
) -> AffectPoint:
    valence_half_life = BASE_VALENCE_HALF_LIFE_SECONDS / dynamics.recovery_rate
    arousal_half_life = BASE_AROUSAL_HALF_LIFE_SECONDS / dynamics.recovery_rate
    return AffectPoint(
        _decay_axis(
            point.valence,
            BASELINE.valence,
            elapsed_seconds,
            valence_half_life,
        ),
        _decay_axis(
            point.arousal,
            BASELINE.arousal,
            elapsed_seconds,
            arousal_half_life,
        ),
    )


def read_snapshot(snapshot: AffectSnapshot, *, now: str) -> tuple[AffectSnapshot, str]:
    current = _aware(now)
    observed = _aware(snapshot.observed_at)
    expires = _aware(snapshot.expires_at)
    if current < observed:
        return (
            baseline_snapshot(
                pet_id=snapshot.pet_id,
                memory_subject_id=snapshot.memory_subject_id,
                relationship_epoch_id=snapshot.relationship_epoch_id,
                observed_at=now,
                age=snapshot.dynamics_age,
                relationship_stage=snapshot.dynamics_relationship_stage,
                processed_event_ids=snapshot.processed_event_ids,
            ),
            "future_snapshot_rejected",
        )
    if current >= expires:
        return (
            baseline_snapshot(
                pet_id=snapshot.pet_id,
                memory_subject_id=snapshot.memory_subject_id,
                relationship_epoch_id=snapshot.relationship_epoch_id,
                observed_at=now,
                age=snapshot.dynamics_age,
                relationship_stage=snapshot.dynamics_relationship_stage,
                processed_event_ids=snapshot.processed_event_ids,
            ),
            "expired_to_baseline",
        )
    dynamics = dynamics_for(
        snapshot.dynamics_age,
        snapshot.dynamics_relationship_stage,
    )
    point = decay_point(
        snapshot.point,
        elapsed_seconds=(current - observed).total_seconds(),
        dynamics=dynamics,
    )
    return replace(snapshot, point=point, observed_at=_iso(current)), "restored"


def restore_snapshot(
    snapshot: AffectSnapshot,
    *,
    now: str,
    pet_id: str,
    memory_subject_id: str,
    relationship_epoch_id: str,
    current_age: Age,
    current_relationship_stage: RelationshipStage,
) -> tuple[AffectSnapshot, str]:
    if (
        snapshot.pet_id != pet_id
        or snapshot.memory_subject_id != memory_subject_id
        or snapshot.relationship_epoch_id != relationship_epoch_id
    ):
        return (
            baseline_snapshot(
                pet_id=pet_id,
                memory_subject_id=memory_subject_id,
                relationship_epoch_id=relationship_epoch_id,
                observed_at=now,
                age=current_age,
                relationship_stage=current_relationship_stage,
            ),
            "identity_or_epoch_mismatch",
        )
    return read_snapshot(snapshot, now=now)


def restore_payload(
    payload: Mapping[str, object],
    *,
    now: str,
    pet_id: str,
    memory_subject_id: str,
    relationship_epoch_id: str,
    current_age: Age,
    current_relationship_stage: RelationshipStage,
) -> tuple[AffectSnapshot, str]:
    try:
        snapshot = AffectSnapshot.from_dict(payload)
    except (KeyError, TypeError, ValueError):
        return (
            baseline_snapshot(
                pet_id=pet_id,
                memory_subject_id=memory_subject_id,
                relationship_epoch_id=relationship_epoch_id,
                observed_at=now,
                age=current_age,
                relationship_stage=current_relationship_stage,
            ),
            "invalid_snapshot",
        )
    return restore_snapshot(
        snapshot,
        now=now,
        pet_id=pet_id,
        memory_subject_id=memory_subject_id,
        relationship_epoch_id=relationship_epoch_id,
        current_age=current_age,
        current_relationship_stage=current_relationship_stage,
    )


def apply_event(
    snapshot: AffectSnapshot,
    event: AffectEvent,
    *,
    age: Age,
    relationship_stage: RelationshipStage,
) -> EventResult:
    spec = EVENT_SPECS[event.kind]
    if event.event_id in snapshot.processed_event_ids:
        return EventResult("duplicate_ignored", snapshot, event.kind, spec.context)

    occurred = _aware(event.occurred_at)
    observed = _aware(snapshot.observed_at)
    if occurred < observed:
        return EventResult("out_of_order_ignored", snapshot, event.kind, spec.context)

    current, _ = read_snapshot(snapshot, now=event.occurred_at)
    dynamics = dynamics_for(age, relationship_stage)
    blend = min(1.0, spec.strength * dynamics.reactivity / dynamics.inertia)
    point = AffectPoint(
        _clamp(
            current.point.valence
            + _round_half_away((spec.target.valence - current.point.valence) * blend)
        ),
        _clamp(
            current.point.arousal
            + _round_half_away((spec.target.arousal - current.point.arousal) * blend)
        ),
    )

    if spec.context == "user_distress":
        point = AffectPoint(
            max(0, min(250, point.valence)),
            max(-400, min(0, point.arousal)),
        )
    elif spec.context == "negative_feedback":
        point = AffectPoint(
            max(0, min(200, point.valence)),
            max(-500, min(-150, point.arousal)),
        )

    processed = (*current.processed_event_ids, event.event_id)[-64:]
    updated = replace(
        current,
        point=point,
        observed_at=_iso(occurred),
        expires_at=_iso(occurred + timedelta(seconds=SNAPSHOT_TTL_SECONDS)),
        dynamics_age=age,
        dynamics_relationship_stage=relationship_stage,
        processed_event_ids=processed,
    )
    return EventResult("applied", updated, event.kind, spec.context)


def reset_to_baseline(
    snapshot: AffectSnapshot,
    *,
    now: str,
    relationship_epoch_id: str,
    age: Age,
    relationship_stage: RelationshipStage = "first_meeting",
) -> AffectSnapshot:
    return baseline_snapshot(
        pet_id=snapshot.pet_id,
        memory_subject_id=snapshot.memory_subject_id,
        relationship_epoch_id=relationship_epoch_id,
        observed_at=now,
        age=age,
        relationship_stage=relationship_stage,
        processed_event_ids=snapshot.processed_event_ids,
    )


def project_affect(
    point: AffectPoint,
    *,
    age: Age,
    relationship_stage: RelationshipStage,
    context: str,
    surface: Surface,
    device_state: str = "normal",
) -> dict[str, object]:
    dynamics = dynamics_for(age, relationship_stage)
    visible = AffectPoint(
        _clamp(
            BASELINE.valence
            + _round_half_away(
                (point.valence - BASELINE.valence) * dynamics.expression_gain
            )
        ),
        _clamp(_round_half_away(point.arousal * dynamics.expression_gain)),
    )

    if context == "user_distress":
        posture = "supportive_settled"
        cadence = "gentle_settled"
        expression = "quiet_warm"
        constraints = ("no_humor", "no_celebration", "no_initiative_from_va")
    elif context == "negative_feedback":
        posture = "receptive_brief"
        cadence = "brief_settled"
        expression = "attentive_still"
        constraints = (
            "no_self_pity",
            "no_comfort_seeking",
            "no_initiative_from_va",
        )
    elif visible.valence >= 450 and visible.arousal >= 250:
        posture = "bright_warm"
        cadence = "bright_but_bounded"
        expression = "bright_pulse"
        constraints = ("no_initiative_from_va",)
    elif visible.arousal <= -150:
        posture = "gentle_warm"
        cadence = "settled"
        expression = "quiet_warm"
        constraints = ("no_initiative_from_va",)
    else:
        posture = "warm_neutral"
        cadence = "natural"
        expression = "warm_idle"
        constraints = ("no_initiative_from_va",)

    intensity = "medium" if expression == "bright_pulse" else "low"
    if relationship_stage == "first_meeting":
        intensity = "low"
    if surface == "hardware" and device_state == "low_battery":
        expression = "low_power"
        intensity = "low"

    return {
        "emotional_posture": posture,
        "voice_cadence": cadence,
        "hardware_expression": {
            "kind": expression,
            "intensity": intensity,
        },
        "hard_constraints": list(constraints),
        "may_create_initiative": False,
        "requires_existing_opportunity": True,
    }
