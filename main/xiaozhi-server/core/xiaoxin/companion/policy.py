from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Mapping

from .contracts import (
    BehaviorAdjustmentSignal,
    BirthTemperament,
    CompanionAgeExpression,
    CompanionContext,
    CompanionDeviceState,
    CompanionEvidence,
    CompanionExpressionStyle,
    CompanionPolicy,
    CompanionPolicyReasonCode,
    HardwareExpressionField,
    age_expression_for_stage,
    xiaoxin_age_for_stage,
)


@dataclass(frozen=True)
class RelationshipQualityMetrics:
    turn_count: int = 0
    meaningful_interaction_count: int = 0
    distinct_interaction_days: int = 0
    reliable_fact_count: int = 0
    effective_feedback_count: int = 0
    completed_followup_count: int = 0
    accepted_help_count: int = 0
    negative_feedback_count: int = 0
    relationship_age_days: int = 0
    active_week_count: int = 0
    active_month_count: int = 0
    helpfulness_days: int = 0
    attunement_days: int = 0
    recent_active_days: int = 0
    recent_helpfulness_days: int = 0
    recent_attunement_days: int = 0
    historical_stage: str | None = None
    relationship_posture: str = "steady"
    adjustment_gain: float = 1.0
    timeline_complete: bool = False
    recent_window_counts: Mapping[int, tuple[int, int, int]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        values = (
            self.turn_count,
            self.meaningful_interaction_count,
            self.distinct_interaction_days,
            self.reliable_fact_count,
            self.effective_feedback_count,
            self.completed_followup_count,
            self.accepted_help_count,
            self.negative_feedback_count,
            self.relationship_age_days,
            self.active_week_count,
            self.active_month_count,
            self.helpfulness_days,
            self.attunement_days,
            self.recent_active_days,
            self.recent_helpfulness_days,
            self.recent_attunement_days,
        )
        if min(values) < 0:
            raise ValueError("relationship quality metrics must be non-negative")
        if self.historical_stage not in {
            None,
            "first_meeting",
            "familiar",
            "attuned",
            "long_term_companion",
        }:
            raise ValueError("historical relationship stage is invalid")
        if self.relationship_posture not in {
            "steady",
            "reunion_cautious",
            "repairing",
        }:
            raise ValueError("relationship posture is invalid")
        if self.adjustment_gain not in {0.0, 0.5, 0.75, 1.0}:
            raise ValueError("relationship adjustment gain is invalid")
        expected_gains = {
            "steady": {1.0},
            "reunion_cautious": {0.5, 0.75},
            "repairing": {0.0},
        }
        if self.adjustment_gain not in expected_gains[self.relationship_posture]:
            raise ValueError("relationship posture and adjustment gain disagree")
        if any(
            window not in {60, 180, 365} or len(counts) != 3 or min(counts) < 0
            for window, counts in self.recent_window_counts.items()
        ):
            raise ValueError("relationship recent window counts are invalid")

    @property
    def continuity(self) -> int:
        return self.distinct_interaction_days

    @property
    def knowledge(self) -> int:
        return self.reliable_fact_count

    @property
    def helpfulness(self) -> int:
        return self.completed_followup_count + self.accepted_help_count

    @property
    def attunement(self) -> int:
        return self.effective_feedback_count


@dataclass(frozen=True)
class RelationshipStageGate:
    minimum_span_days: int
    minimum_active_days: int
    minimum_active_weeks: int
    minimum_active_months: int
    minimum_knowledge: int
    minimum_helpfulness_days: int
    minimum_attunement_days: int
    recent_window_days: int
    minimum_recent_active_days: int
    minimum_recent_helpfulness_days: int
    minimum_recent_attunement_days: int

    def __post_init__(self) -> None:
        if (
            min(
                self.minimum_span_days,
                self.minimum_active_days,
                self.minimum_active_weeks,
                self.minimum_active_months,
                self.minimum_knowledge,
                self.minimum_helpfulness_days,
                self.minimum_attunement_days,
                self.recent_window_days,
                self.minimum_recent_active_days,
                self.minimum_recent_helpfulness_days,
                self.minimum_recent_attunement_days,
            )
            < 0
        ):
            raise ValueError("relationship stage thresholds must be non-negative")

    def accepts(self, metrics: RelationshipQualityMetrics) -> bool:
        recent = metrics.recent_window_counts.get(
            self.recent_window_days,
            (
                metrics.recent_active_days,
                metrics.recent_helpfulness_days,
                metrics.recent_attunement_days,
            ),
        )
        return (
            metrics.relationship_age_days >= self.minimum_span_days
            and metrics.continuity >= self.minimum_active_days
            and metrics.active_week_count >= self.minimum_active_weeks
            and metrics.active_month_count >= self.minimum_active_months
            and metrics.knowledge >= self.minimum_knowledge
            and metrics.helpfulness_days >= self.minimum_helpfulness_days
            and metrics.attunement_days >= self.minimum_attunement_days
            and recent[0] >= self.minimum_recent_active_days
            and recent[1] >= self.minimum_recent_helpfulness_days
            and recent[2] >= self.minimum_recent_attunement_days
        )


@dataclass(frozen=True)
class CompanionPolicyConfig:
    version: str = "companion-policy-v6"
    reliable_confidence: float = 0.8
    familiar: RelationshipStageGate = RelationshipStageGate(
        14, 4, 2, 1, 2, 1, 1, 60, 2, 1, 1
    )
    attuned: RelationshipStageGate = RelationshipStageGate(
        90, 12, 8, 3, 5, 4, 3, 180, 4, 2, 1
    )
    long_term_companion: RelationshipStageGate = RelationshipStageGate(
        365, 36, 24, 9, 10, 8, 6, 365, 8, 2, 2
    )

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("policy version must be non-empty")
        if not 0.0 <= self.reliable_confidence <= 1.0:
            raise ValueError("reliable_confidence must be between 0 and 1")


DEFAULT_COMPANION_POLICY_CONFIG = CompanionPolicyConfig()

_STYLE_DIMENSIONS = (
    "exploration_orientation",
    "expression_energy",
    "thought_organization",
    "humor_level",
    "initiative_bias",
)
_MIDDLE_STYLE = {
    "exploration_orientation": "balanced",
    "expression_energy": "natural",
    "thought_organization": "balanced",
    "humor_level": "low",
    "initiative_bias": "timely",
}
_CONTEXT_STYLE_DIMENSIONS = {
    "ordinary": _STYLE_DIMENSIONS,
    "fact_explanation": (
        "exploration_orientation",
        "expression_energy",
        "thought_organization",
    ),
    "open_learning_difficulty": (
        "exploration_orientation",
        "expression_energy",
        "thought_organization",
    ),
    "multi_task_choice": (
        "exploration_orientation",
        "thought_organization",
        "initiative_bias",
    ),
    "success": ("expression_energy", "humor_level"),
    "user_low_mood": (
        "exploration_orientation",
        "expression_energy",
        "thought_organization",
    ),
    "future_event": (
        "exploration_orientation",
        "thought_organization",
        "humor_level",
        "initiative_bias",
    ),
    "explicit_boundary": ("thought_organization",),
    "serious": _STYLE_DIMENSIONS,
}
_STYLE_ORDERS = {
    "exploration_orientation": ("focused", "balanced", "exploratory"),
    "expression_energy": ("calm", "natural", "lively"),
    "thought_organization": ("intuitive", "balanced", "structured"),
    "humor_level": ("none", "low", "medium"),
    "initiative_bias": ("reserved", "timely", "proactive"),
}
_RELATIONSHIP_REVEAL_CAPS = {
    "first_meeting": {
        "exploration_orientation": "balanced",
        "humor_level": "low",
        "initiative_bias": "reserved",
    },
    "familiar": {"initiative_bias": "timely"},
    "attuned": {},
    "long_term_companion": {},
}
_HARDWARE_INTENSITIES = ("low", "neutral", "medium", "high")
_RESPONSE_LENGTHS = ("short", "standard", "expanded")
_QUESTION_BUDGETS = (0, 1, 2)
_MEMORY_BUDGETS = (0, 1, 2, 3)
_INITIATIVE_LEVELS = ("disabled", "low", "medium")
_EMOTIONAL_POSTURES = ("neutral", "warm", "supportive", "attuned")
_CLOSURE_STYLES = ("concise", "warm", "relational", "familiar")
_HUMOR_LEVELS = ("none", "low", "medium")
_REASON_CODE_ORDER = (
    "unconfirmed_speaker_gate",
    "context_style_whitelist",
    "relationship_reveal_cap",
    "reunion_cautious_cap",
    "repairing_cap",
    "serious_context_humor_suppression",
    "interaction_kind_memory_gate",
    "explicit_recall_budget_cap",
    "user_contract_cap",
    "low_mood_support",
    "low_mood_question_stop",
    "negative_feedback_initiative_stop",
    "negative_feedback_question_cap",
    "negative_feedback_concise_close",
    "too_proactive_question_stop",
    "too_personal_memory_stop",
    "hardware_surface_cap",
    "initiative_surface_question_stop",
    "initiative_surface_memory_cap",
    "voice_surface_memory_cap",
    "low_battery_hardware_cap",
    "hardware_whitelist_cap",
)


@dataclass(frozen=True)
class CompanionPolicyInputs:
    speaker_identity: str
    surface: str
    academic_stage: str
    interaction_kind: str
    birth_temperament: BirthTemperament | None = None
    relationship: RelationshipQualityMetrics = RelationshipQualityMetrics()
    explicit_boundaries: Mapping[str, object] = field(default_factory=dict)
    active_adjustments: Mapping[str, object] = field(default_factory=dict)
    behavior_adjustments: tuple[BehaviorAdjustmentSignal, ...] = ()
    short_term_state: Mapping[str, object] = field(default_factory=dict)
    context: CompanionContext = "ordinary"
    device_state: CompanionDeviceState = "normal"
    hardware_expression_whitelist: tuple[HardwareExpressionField, ...] = (
        "kind",
        "intensity",
        "humor_level",
        "cadence",
    )

    def __post_init__(self) -> None:
        if self.context not in _CONTEXT_STYLE_DIMENSIONS:
            raise ValueError("companion context is invalid")
        if self.device_state not in {"normal", "low_battery"}:
            raise ValueError("companion device state is invalid")
        if any(
            field_name not in {"kind", "intensity", "humor_level", "cadence"}
            for field_name in self.hardware_expression_whitelist
        ):
            raise ValueError("hardware expression whitelist is invalid")


def _relationship_feedback_value(item: CompanionEvidence) -> object | None:
    if item.kind == "initiative_feedback":
        if item.attribution != "observed_user_feedback":
            return None
    elif item.attribution not in {
        "explicit_user_feedback",
        "observed_interaction",
    }:
        return None
    if item.kind == "interaction_feedback":
        return (
            item.content.get("outcome")
            or item.content.get("feedback")
            or item.content.get("signal")
        )
    if item.kind == "initiative_feedback":
        outcome = item.content.get("outcome")
        if outcome == "accepted":
            return "initiative_accepted"
        if outcome == "rejected":
            return "initiative_rejected"
    if item.kind == "accepted_help" and item.attribution == "explicit_user_feedback":
        return "helpful"
    return None


def _is_qualified_relationship_outcome(item: CompanionEvidence) -> bool:
    return item.attribution in {
        "explicit_user_feedback",
        "observed_interaction",
    }


def policy_inputs_from_evidence(
    *,
    speaker_identity: str,
    surface: str,
    academic_stage: str,
    interaction_kind: str,
    turn_count: int,
    distinct_interaction_days: int,
    evidence: tuple[CompanionEvidence, ...],
    active_adjustments: Mapping[str, object],
    behavior_adjustments: tuple[BehaviorAdjustmentSignal, ...] = (),
    context: CompanionContext = "ordinary",
    birth_temperament: BirthTemperament | None = None,
    relationship_started_at: str | None = None,
    interaction_dates: tuple[str, ...] = (),
    historical_stage: str | None = None,
    relationship_stage_history: tuple[tuple[str, str], ...] = (),
    now: str | None = None,
    config: CompanionPolicyConfig = DEFAULT_COMPANION_POLICY_CONFIG,
) -> CompanionPolicyInputs:
    reliable_kinds = {
        "profile_fact",
        "explicit_preference",
        "preference",
        "interest",
        "explicit_boundary",
        "boundary",
        "user_life_event",
        "life_event",
        "relationship_context",
        "wellbeing",
        "goal",
    }
    current_moment = datetime.fromisoformat(now) if now is not None else None
    qualified_evidence = tuple(
        item
        for item in evidence
        if item.speaker_identity == "confirmed"
        and (
            current_moment is None
            or datetime.fromisoformat(item.occurred_at) <= current_moment
        )
    )
    meaningful = tuple(
        item for item in qualified_evidence if item.kind == "meaningful_moment"
    )
    reliable_fact_count = sum(
        item.kind in reliable_kinds and item.confidence >= config.reliable_confidence
        for item in qualified_evidence
    )
    positive_feedback_values = {
        "accepted",
        "helpful",
        "effective",
        "short_reply_worked",
        "initiative_accepted",
    }
    negative_feedback_values = {
        "not_helpful",
        "too_proactive",
        "too_personal",
        "rejected",
        "initiative_rejected",
    }
    accepted_help_count = sum(
        item.kind == "accepted_help" and _is_qualified_relationship_outcome(item)
        for item in qualified_evidence
    )
    effective_feedback_count = 0
    negative_feedback_count = 0
    last_relationship_feedback: str | None = None
    for item in sorted(
        qualified_evidence,
        key=lambda value: (value.occurred_at, value.evidence_id),
    ):
        feedback_value = _relationship_feedback_value(item)
        if feedback_value in positive_feedback_values:
            effective_feedback_count += 1
            last_relationship_feedback = str(feedback_value)
        elif feedback_value in negative_feedback_values:
            negative_feedback_count += 1
            last_relationship_feedback = str(feedback_value)
    completed_followup_count = sum(
        _is_qualified_relationship_outcome(item)
        and (
            item.kind == "followup_completed"
            or (item.kind == "followup" and item.content.get("status") == "completed")
        )
        for item in qualified_evidence
    )
    explicit_boundaries: dict[str, object] = {}
    short_term_state: dict[str, object] = {}
    for item in sorted(
        qualified_evidence,
        key=lambda value: (value.occurred_at, value.evidence_id),
    ):
        if item.kind in {"explicit_boundary", "boundary"}:
            boundary_key = item.content.get("boundary_key")
            if isinstance(boundary_key, str) and boundary_key.strip():
                explicit_boundaries[boundary_key] = item.content.get("value")
        elif item.kind == "short_term_state":
            short_term_state.update(item.content)
    if last_relationship_feedback is not None:
        short_term_state["last_relationship_feedback"] = last_relationship_feedback
    quality = _relationship_quality_from_timeline(
        turn_count=turn_count,
        distinct_interaction_days=distinct_interaction_days,
        reliable_fact_count=reliable_fact_count,
        effective_feedback_count=effective_feedback_count,
        completed_followup_count=completed_followup_count,
        accepted_help_count=accepted_help_count,
        negative_feedback_count=negative_feedback_count,
        evidence=qualified_evidence,
        relationship_started_at=relationship_started_at,
        interaction_dates=interaction_dates,
        historical_stage=historical_stage,
        relationship_stage_history=relationship_stage_history,
        now=now,
    )
    return CompanionPolicyInputs(
        speaker_identity=speaker_identity,
        surface=surface,
        academic_stage=academic_stage,
        interaction_kind=interaction_kind,
        birth_temperament=birth_temperament,
        relationship=replace(
            quality,
            meaningful_interaction_count=len(meaningful),
        ),
        explicit_boundaries=explicit_boundaries,
        active_adjustments=dict(active_adjustments),
        behavior_adjustments=behavior_adjustments,
        short_term_state=short_term_state,
        context=context,
    )


def derive_relationship_stage(
    metrics: RelationshipQualityMetrics,
    *,
    config: CompanionPolicyConfig = DEFAULT_COMPANION_POLICY_CONFIG,
) -> str:
    if not metrics.timeline_complete:
        if (
            metrics.turn_count >= 20
            and metrics.continuity >= 15
            and metrics.knowledge >= 6
            and metrics.helpfulness >= 4
            and metrics.attunement >= 4
        ):
            return "long_term_companion"
        if (
            metrics.turn_count >= 8
            and metrics.continuity >= 5
            and metrics.knowledge >= 3
            and metrics.helpfulness >= 2
            and metrics.attunement >= 2
        ):
            return "attuned"
        if (
            metrics.turn_count >= 3
            and metrics.continuity >= 2
            and metrics.knowledge >= 1
            and metrics.helpfulness >= 1
            and metrics.attunement >= 1
        ):
            return "familiar"
        return "first_meeting"
    if config.long_term_companion.accepts(metrics):
        return "long_term_companion"
    if config.attuned.accepts(metrics):
        return "attuned"
    if config.familiar.accepts(metrics):
        return "familiar"
    return "first_meeting"


_STAGE_ORDER = {
    "first_meeting": 0,
    "familiar": 1,
    "attuned": 2,
    "long_term_companion": 3,
}


def _shanghai_date(value: str) -> date:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("relationship timestamps must include a timezone")
    return parsed.astimezone(ZoneInfo("Asia/Shanghai")).date()


def _relationship_quality_from_timeline(
    *,
    turn_count: int,
    distinct_interaction_days: int,
    reliable_fact_count: int,
    effective_feedback_count: int,
    completed_followup_count: int,
    accepted_help_count: int,
    negative_feedback_count: int,
    evidence: tuple[CompanionEvidence, ...],
    relationship_started_at: str | None,
    interaction_dates: tuple[str, ...],
    historical_stage: str | None,
    relationship_stage_history: tuple[tuple[str, str], ...],
    now: str | None,
) -> RelationshipQualityMetrics:
    current_date = _shanghai_date(now) if now is not None else None
    dates = tuple(
        sorted(
            value
            for value in {date.fromisoformat(item) for item in interaction_dates}
            if current_date is None or value <= current_date
        )
    )
    start_date = (
        _shanghai_date(relationship_started_at)
        if relationship_started_at is not None
        else current_date
    )
    age_days = (
        max((current_date - start_date).days, 0)
        if current_date is not None and start_date is not None
        else 0
    )
    help_dates: set[date] = set()
    attunement_dates: set[date] = set()
    negative_moments: list[datetime] = []
    positive_moments: list[datetime] = []
    for item in evidence:
        item_date = _shanghai_date(item.occurred_at)
        if _is_qualified_relationship_outcome(item) and (
            item.kind in {"accepted_help", "followup_completed"}
            or (item.kind == "followup" and item.content.get("status") == "completed")
        ):
            help_dates.add(item_date)
        feedback_value = _relationship_feedback_value(item)
        if feedback_value in {
            "accepted",
            "helpful",
            "effective",
            "short_reply_worked",
            "initiative_accepted",
        }:
            attunement_dates.add(item_date)
            positive_moments.append(datetime.fromisoformat(item.occurred_at))
        if feedback_value in {
            "not_helpful",
            "too_proactive",
            "too_personal",
            "rejected",
            "initiative_rejected",
        }:
            negative_moments.append(datetime.fromisoformat(item.occurred_at))

    def recent_count(values: set[date], window_days: int) -> int:
        if current_date is None:
            return 0
        return sum(0 <= (current_date - value).days <= window_days for value in values)

    historical = historical_stage or "first_meeting"
    posture = "steady"
    adjustment_gain = 1.0
    last_negative = max(negative_moments, default=None)
    if last_negative is not None:
        has_positive_repair = any(value > last_negative for value in positive_moments)
        last_negative_date = _shanghai_date(last_negative.isoformat())
        healthy_days = sum(value > last_negative_date for value in dates)
        if not has_positive_repair and healthy_days < 3:
            posture = "repairing"
            adjustment_gain = 0.0
    if posture == "steady" and current_date is not None and dates:
        stage_history = tuple(
            sorted(
                (
                    (_shanghai_date(occurred_at), stage)
                    for occurred_at, stage in relationship_stage_history
                ),
                key=lambda item: (item[0], _STAGE_ORDER[item[1]]),
            )
        )

        def stage_on(value: date) -> str:
            if not stage_history:
                return historical
            stages = (
                stage for occurred_at, stage in stage_history if occurred_at <= value
            )
            return max(stages, key=_STAGE_ORDER.__getitem__, default="first_meeting")

        def absence_days_for(value: date) -> int | None:
            return {
                "familiar": 30,
                "attuned": 60,
                "long_term_companion": 120,
            }.get(stage_on(value))

        last_date = dates[-1]
        current_absence_days = absence_days_for(last_date)
        if (
            current_absence_days is not None
            and (current_date - last_date).days >= current_absence_days
        ):
            posture = "reunion_cautious"
            adjustment_gain = 0.5
        else:
            latest_return_index: int | None = None
            for index in range(1, len(dates)):
                absence_days = absence_days_for(dates[index - 1])
                if (
                    absence_days is not None
                    and (dates[index] - dates[index - 1]).days >= absence_days
                ):
                    latest_return_index = index
            if latest_return_index is not None:
                return_day_count = len(dates) - latest_return_index
                if return_day_count < 3:
                    posture = "reunion_cautious"
                    adjustment_gain = (0.5, 0.75)[return_day_count - 1]
    return RelationshipQualityMetrics(
        turn_count=turn_count,
        distinct_interaction_days=distinct_interaction_days,
        reliable_fact_count=reliable_fact_count,
        effective_feedback_count=effective_feedback_count,
        completed_followup_count=completed_followup_count,
        accepted_help_count=accepted_help_count,
        negative_feedback_count=negative_feedback_count,
        relationship_age_days=age_days,
        active_week_count=len({value.isocalendar()[:2] for value in dates}),
        active_month_count=len({(value.year, value.month) for value in dates}),
        helpfulness_days=len(help_dates),
        attunement_days=len(attunement_dates),
        recent_active_days=recent_count(set(dates), 365),
        recent_helpfulness_days=recent_count(help_dates, 365),
        recent_attunement_days=recent_count(attunement_dates, 365),
        historical_stage=historical_stage,
        relationship_posture=posture,
        adjustment_gain=adjustment_gain,
        timeline_complete=True,
        recent_window_counts={
            window: (
                recent_count(set(dates), window),
                recent_count(help_dates, window),
                recent_count(attunement_dates, window),
            )
            for window in (60, 180, 365)
        },
    )


def relationship_quality_snapshot(
    metrics: RelationshipQualityMetrics,
) -> dict[str, int]:
    return {
        "continuity": metrics.continuity,
        "knowledge": metrics.knowledge,
        "helpfulness": metrics.helpfulness,
        "attunement": metrics.attunement,
    }


def relationship_stage_reason_codes(
    metrics: RelationshipQualityMetrics,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if metrics.continuity > 0:
        reasons.append("multi_day_continuity")
    if metrics.knowledge > 0:
        reasons.append("reliable_user_knowledge")
    if metrics.helpfulness > 0:
        reasons.append("confirmed_helpfulness")
    if metrics.attunement > 0:
        reasons.append("positive_attunement")
    return tuple(reasons)


def relationship_stage_progress(
    metrics: RelationshipQualityMetrics,
    *,
    config: CompanionPolicyConfig = DEFAULT_COMPANION_POLICY_CONFIG,
) -> Mapping[str, object]:
    candidate_stage = derive_relationship_stage(metrics, config=config)
    historical_stage = metrics.historical_stage or "first_meeting"
    current_stage = max(
        (candidate_stage, historical_stage),
        key=lambda value: _STAGE_ORDER[value],
    )
    if metrics.relationship_posture == "repairing":
        current_stage = historical_stage
    next_stage = {
        "first_meeting": "familiar",
        "familiar": "attuned",
        "attuned": "long_term_companion",
        "long_term_companion": None,
    }[current_stage]
    if next_stage is None:
        return {
            "policy_version": config.version,
            "current_stage": current_stage,
            "next_stage": None,
            "gap_reason_codes": (),
        }

    gate = getattr(config, next_stage)
    recent = metrics.recent_window_counts.get(
        gate.recent_window_days,
        (
            metrics.recent_active_days,
            metrics.recent_helpfulness_days,
            metrics.recent_attunement_days,
        ),
    )
    checks = (
        (
            "minimum_relationship_span",
            metrics.relationship_age_days,
            gate.minimum_span_days,
        ),
        ("minimum_active_days", metrics.continuity, gate.minimum_active_days),
        (
            "minimum_active_weeks",
            metrics.active_week_count,
            gate.minimum_active_weeks,
        ),
        (
            "minimum_active_months",
            metrics.active_month_count,
            gate.minimum_active_months,
        ),
        (
            "minimum_reliable_knowledge",
            metrics.knowledge,
            gate.minimum_knowledge,
        ),
        (
            "minimum_helpfulness_days",
            metrics.helpfulness_days,
            gate.minimum_helpfulness_days,
        ),
        (
            "minimum_attunement_days",
            metrics.attunement_days,
            gate.minimum_attunement_days,
        ),
        (
            "minimum_recent_active_days",
            recent[0],
            gate.minimum_recent_active_days,
        ),
        (
            "minimum_recent_helpfulness_days",
            recent[1],
            gate.minimum_recent_helpfulness_days,
        ),
        (
            "minimum_recent_attunement_days",
            recent[2],
            gate.minimum_recent_attunement_days,
        ),
    )
    gap_reason_codes = tuple(
        reason_code for reason_code, actual, minimum in checks if actual < minimum
    )
    if metrics.relationship_posture == "repairing":
        gap_reason_codes = ("repairing_posture_pause", *gap_reason_codes)
    return {
        "policy_version": config.version,
        "current_stage": current_stage,
        "next_stage": next_stage,
        "gap_reason_codes": gap_reason_codes,
    }


def _style_values(style: CompanionExpressionStyle) -> dict[str, str]:
    return {
        dimension: str(getattr(style, dimension)) for dimension in _STYLE_DIMENSIONS
    }


def _expression_style_from_values(
    values: Mapping[str, str],
) -> CompanionExpressionStyle:
    return CompanionExpressionStyle(
        exploration_orientation=values["exploration_orientation"],
        expression_energy=values["expression_energy"],
        thought_organization=values["thought_organization"],
        humor_level=values["humor_level"],
        initiative_bias=values["initiative_bias"],
    )


def _apply_context_style_whitelist(
    style: CompanionExpressionStyle,
    context: CompanionContext,
) -> CompanionExpressionStyle:
    allowed_dimensions = set(_CONTEXT_STYLE_DIMENSIONS[context])
    values = _style_values(style)
    for dimension in _STYLE_DIMENSIONS:
        if (
            dimension not in allowed_dimensions
            and values[dimension] != _MIDDLE_STYLE[dimension]
        ):
            values[dimension] = _MIDDLE_STYLE[dimension]
    return _expression_style_from_values(values)


def _apply_relationship_reveal_cap(
    style: CompanionExpressionStyle,
    stage: str,
) -> CompanionExpressionStyle:
    values = _style_values(style)
    for dimension, cap in _RELATIONSHIP_REVEAL_CAPS[stage].items():
        order = _STYLE_ORDERS[dimension]
        if order.index(values[dimension]) > order.index(cap):
            values[dimension] = cap
    return _expression_style_from_values(values)


def _suppress_serious_humor(
    style: CompanionExpressionStyle,
) -> CompanionExpressionStyle:
    values = _style_values(style)
    values["humor_level"] = "none"
    return _expression_style_from_values(values)


def _one_step_toward(current: object, target: object, scale: tuple) -> object:
    current_index = scale.index(current)
    target_index = scale.index(target)
    if current_index == target_index:
        return current
    direction = 1 if target_index > current_index else -1
    return scale[current_index + direction]


def _apply_adjustment(
    current: object,
    target: object,
    scale: tuple,
    *,
    explicit: bool,
) -> object:
    return target if explicit else _one_step_toward(current, target, scale)


def _ordered_reason_codes(
    active_codes: set[str],
) -> tuple[CompanionPolicyReasonCode, ...]:
    return tuple(code for code in _REASON_CODE_ORDER if code in active_codes)


def _finalize_policy(
    *,
    inputs: CompanionPolicyInputs,
    config: CompanionPolicyConfig,
    effective_context: CompanionContext,
    stage: str,
    relationship_posture: str,
    relationship_adjustment_gain: float,
    age: int | None,
    response_length: str,
    question_budget: int,
    memory_budget: int,
    initiative: str,
    posture: str,
    closure: str,
    expression_style: CompanionExpressionStyle,
    hardware_intensity: str,
    humor_level: object | None,
    age_expression: CompanionAgeExpression,
    prohibited_behaviors: tuple[str, ...],
    explicit_recall_can_reference_memory: bool,
    reason_codes: set[str],
) -> CompanionPolicy:
    if inputs.interaction_kind == "general_qa":
        memory_budget = 0
        reason_codes.add("interaction_kind_memory_gate")
    elif inputs.interaction_kind == "explicit_recall":
        if (
            explicit_recall_can_reference_memory
            and inputs.relationship.reliable_fact_count > 0
        ):
            memory_budget = min(max(memory_budget, 1), 2)
        else:
            memory_budget = 0
        reason_codes.add("explicit_recall_budget_cap")

    if inputs.surface == "hardware":
        response_length = "short"
        question_budget = 0
        memory_budget = 0
        initiative = "disabled"
        reason_codes.add("hardware_surface_cap")
    elif inputs.surface == "initiative":
        question_budget = 0
        memory_budget = min(memory_budget, 1)
        reason_codes.add("initiative_surface_question_stop")
        reason_codes.add("initiative_surface_memory_cap")
    elif inputs.surface == "voice":
        memory_budget = min(memory_budget, 2)
        reason_codes.add("voice_surface_memory_cap")

    boundaries = inputs.explicit_boundaries
    supported_boundary_fields = {
        "question_frequency",
        "memory_reference_depth",
        "initiative_level",
        "response_length",
    }
    if supported_boundary_fields.intersection(boundaries):
        reason_codes.add("user_contract_cap")
    if boundaries.get("question_frequency") in {"never", "less"}:
        question_budget = 0
    if boundaries.get("memory_reference_depth") == "never":
        memory_budget = 0
    if boundaries.get("initiative_level") == "disabled":
        initiative = "disabled"
    if boundaries.get("response_length") == "short":
        response_length = "short"

    if inputs.short_term_state.get("energy") == "low":
        response_length = "short"
        question_budget = 0

    if effective_context == "user_low_mood":
        response_length = "short"
        posture = "supportive"
        question_budget = 0
        reason_codes.add("low_mood_support")
        reason_codes.add("low_mood_question_stop")

    last_relationship_feedback = inputs.short_term_state.get(
        "last_relationship_feedback"
    )
    if last_relationship_feedback in {
        "not_helpful",
        "too_proactive",
        "too_personal",
        "rejected",
        "initiative_rejected",
    }:
        initiative = "disabled"
        reason_codes.add("negative_feedback_initiative_stop")
        question_budget = min(question_budget, 1)
        reason_codes.add("negative_feedback_question_cap")
        closure = "concise"
        reason_codes.add("negative_feedback_concise_close")
        if last_relationship_feedback == "too_proactive":
            question_budget = 0
            reason_codes.add("too_proactive_question_stop")
        if last_relationship_feedback == "too_personal":
            memory_budget = 0
            reason_codes.add("too_personal_memory_stop")

    if inputs.device_state == "low_battery":
        hardware_intensity = "low"
        reason_codes.add("low_battery_hardware_cap")

    hardware_cadence = age_expression.hardware_cadence
    if effective_context in {"serious", "user_low_mood"}:
        humor_level = "none"
        hardware_cadence = "restrained_single"
    if inputs.device_state == "low_battery":
        hardware_cadence = "restrained_single"

    hardware_expression = {
        "intensity": hardware_intensity,
        **({"humor_level": humor_level} if humor_level is not None else {}),
        "cadence": hardware_cadence,
    }
    allowed_hardware_fields = set(inputs.hardware_expression_whitelist)
    filtered_hardware_expression = {
        field_name: value
        for field_name, value in hardware_expression.items()
        if field_name in allowed_hardware_fields
    }
    if filtered_hardware_expression != hardware_expression:
        reason_codes.add("hardware_whitelist_cap")

    return CompanionPolicy(
        xiaoxin_age=age,
        relationship_stage=stage,
        relationship_posture=relationship_posture,
        relationship_adjustment_gain=relationship_adjustment_gain,
        response_length=response_length,
        question_budget=question_budget,
        memory_reference_budget=memory_budget,
        initiative_level=initiative,
        emotional_posture=posture,
        closure_style=closure,
        expression_style=expression_style,
        prohibited_behaviors=prohibited_behaviors,
        hardware_expression=filtered_hardware_expression,
        age_expression=age_expression,
        reason_codes=_ordered_reason_codes(reason_codes),
        version=config.version,
    )


def build_companion_policy(
    inputs: CompanionPolicyInputs,
    *,
    config: CompanionPolicyConfig = DEFAULT_COMPANION_POLICY_CONFIG,
) -> CompanionPolicy:
    reason_codes: set[str] = set()
    effective_context: CompanionContext = inputs.context
    if inputs.short_term_state.get("user_low_mood") is True:
        effective_context = "user_low_mood"

    expression_style = _expression_style_from_temperament(
        inputs.birth_temperament if inputs.speaker_identity == "confirmed" else None
    )
    if inputs.speaker_identity != "confirmed":
        reason_codes.add("unconfirmed_speaker_gate")
        expression_style = _apply_context_style_whitelist(
            expression_style,
            effective_context,
        )
        if effective_context != "ordinary":
            reason_codes.add("context_style_whitelist")
        expression_style = _apply_relationship_reveal_cap(
            expression_style,
            "first_meeting",
        )
        reason_codes.add("relationship_reveal_cap")
        if effective_context in {"serious", "user_low_mood"}:
            expression_style = _suppress_serious_humor(expression_style)
            reason_codes.add("serious_context_humor_suppression")
        return _finalize_policy(
            inputs=inputs,
            config=config,
            effective_context=effective_context,
            stage="first_meeting",
            relationship_posture="steady",
            relationship_adjustment_gain=1.0,
            age=None,
            response_length="standard",
            question_budget=0,
            memory_budget=0,
            initiative="disabled",
            posture="neutral",
            closure="concise",
            expression_style=expression_style,
            hardware_intensity="neutral",
            humor_level=None,
            age_expression=age_expression_for_stage("unknown"),
            prohibited_behaviors=(
                "read_private_memory",
                "write_private_memory",
                "manually_change_relationship_stage",
            ),
            explicit_recall_can_reference_memory=False,
            reason_codes=reason_codes,
        )

    candidate_stage = derive_relationship_stage(inputs.relationship, config=config)
    historical_stage = inputs.relationship.historical_stage or "first_meeting"
    stage = max(
        (candidate_stage, historical_stage),
        key=lambda value: _STAGE_ORDER[value],
    )
    relationship_posture = inputs.relationship.relationship_posture
    if relationship_posture == "repairing":
        stage = historical_stage
    expression_style = _apply_context_style_whitelist(
        expression_style,
        effective_context,
    )
    if effective_context != "ordinary":
        reason_codes.add("context_style_whitelist")
    expression_style = _apply_relationship_reveal_cap(
        expression_style,
        stage,
    )
    if _RELATIONSHIP_REVEAL_CAPS[stage]:
        reason_codes.add("relationship_reveal_cap")
    if effective_context in {"serious", "user_low_mood"}:
        expression_style = _suppress_serious_humor(expression_style)
        reason_codes.add("serious_context_humor_suppression")

    stage_defaults = {
        "first_meeting": (
            1,
            1 if inputs.relationship.reliable_fact_count else 0,
            "low",
            "warm",
            "concise",
        ),
        "familiar": (2, 1, "low", "warm", "warm"),
        "attuned": (2, 2, "medium", "supportive", "relational"),
        "long_term_companion": (2, 3, "medium", "attuned", "familiar"),
    }
    (
        stage_question_budget,
        stage_memory_budget,
        stage_initiative,
        posture,
        stage_closure,
    ) = stage_defaults[stage]
    question_budget = stage_question_budget
    memory_budget = min(stage_memory_budget, inputs.relationship.reliable_fact_count)
    initiative = stage_initiative
    closure = stage_closure
    response_length = "standard"

    adjustment: dict[str, object] = {}
    explicit_adjustment_dimensions: set[str] = set()
    if inputs.relationship.adjustment_gain > 0.0:
        adjustment.update(inputs.active_adjustments)
        for signal in inputs.behavior_adjustments:
            adjustment[signal.dimension] = signal.value
            if signal.source_kind in {"explicit_feedback", "explicit_contract"}:
                explicit_adjustment_dimensions.add(signal.dimension)
    question_target = {
        "never": 0,
        "less": 1,
        "often": stage_question_budget,
    }.get(adjustment.get("question_frequency"))
    if question_target is not None:
        question_budget = int(
            _apply_adjustment(
                question_budget,
                question_target,
                _QUESTION_BUDGETS,
                explicit="question_frequency" in explicit_adjustment_dimensions,
            )
        )
    memory_target = {
        "never": 0,
        "shallow": 1,
        "moderate": min(stage_memory_budget, 2),
        "deep": stage_memory_budget,
    }.get(adjustment.get("memory_reference_depth"))
    if memory_target is not None:
        memory_budget = int(
            _apply_adjustment(
                memory_budget,
                memory_target,
                _MEMORY_BUDGETS,
                explicit="memory_reference_depth"
                in explicit_adjustment_dimensions,
            )
        )
    adjustment_initiative = adjustment.get("initiative_level")
    if adjustment_initiative in _INITIATIVE_LEVELS:
        initiative = str(
            _apply_adjustment(
                initiative,
                adjustment_initiative,
                _INITIATIVE_LEVELS,
                explicit="initiative_level" in explicit_adjustment_dimensions,
            )
        )
        initiative = _INITIATIVE_LEVELS[
            min(
                _INITIATIVE_LEVELS.index(initiative),
                _INITIATIVE_LEVELS.index(stage_initiative),
            )
        ]
    adjustment_closure = adjustment.get("closure_style")
    if adjustment_closure in _CLOSURE_STYLES:
        closure = str(
            _apply_adjustment(
                closure,
                adjustment_closure,
                _CLOSURE_STYLES,
                explicit="closure_style" in explicit_adjustment_dimensions,
            )
        )
        closure = _CLOSURE_STYLES[
            min(
                _CLOSURE_STYLES.index(closure),
                _CLOSURE_STYLES.index(stage_closure),
            )
        ]

    age = xiaoxin_age_for_stage(inputs.academic_stage)
    age_expression = age_expression_for_stage(inputs.academic_stage)
    hardware_intensity = "neutral"
    adjustment_response_length = adjustment.get("response_length")
    if adjustment_response_length in _RESPONSE_LENGTHS:
        response_length = str(
            _apply_adjustment(
                response_length,
                adjustment_response_length,
                _RESPONSE_LENGTHS,
                explicit="response_length" in explicit_adjustment_dimensions,
            )
        )
    adjustment_posture = adjustment.get("emotional_posture")
    if adjustment_posture in _EMOTIONAL_POSTURES:
        posture = str(
            _apply_adjustment(
                posture,
                adjustment_posture,
                _EMOTIONAL_POSTURES,
                explicit="emotional_posture" in explicit_adjustment_dimensions,
            )
        )
        posture = _EMOTIONAL_POSTURES[
            min(
                _EMOTIONAL_POSTURES.index(posture),
                _EMOTIONAL_POSTURES.index(stage_defaults[stage][3]),
            )
        ]
    adjustment_hardware_intensity = adjustment.get("hardware_expression_intensity")
    if adjustment_hardware_intensity in _HARDWARE_INTENSITIES:
        hardware_intensity = str(
            _apply_adjustment(
                hardware_intensity,
                adjustment_hardware_intensity,
                _HARDWARE_INTENSITIES,
                explicit="hardware_expression_intensity"
                in explicit_adjustment_dimensions,
            )
        )
    humor_level = adjustment.get("humor_level")
    if humor_level in _HUMOR_LEVELS:
        humor_level = str(
            _apply_adjustment(
                expression_style.humor_level,
                humor_level,
                _HUMOR_LEVELS,
                explicit="humor_level" in explicit_adjustment_dimensions,
            )
        )
        style_values = _style_values(expression_style)
        style_values["humor_level"] = humor_level
        expression_style = _apply_context_style_whitelist(
            _expression_style_from_values(style_values),
            effective_context,
        )
        expression_style = _apply_relationship_reveal_cap(expression_style, stage)
        humor_level = expression_style.humor_level
    else:
        humor_level = None
    if effective_context in {"serious", "user_low_mood"}:
        expression_style = _suppress_serious_humor(expression_style)
        humor_level = "none"

    memory_budget = min(
        memory_budget,
        inputs.relationship.reliable_fact_count,
    )

    if relationship_posture == "reunion_cautious":
        question_budget = min(question_budget, 1)
        memory_budget = min(memory_budget, 1)
        initiative = "disabled"
        reason_codes.add("reunion_cautious_cap")
    elif relationship_posture == "repairing":
        question_budget = min(question_budget, 1)
        memory_budget = 0
        initiative = "disabled"
        reason_codes.add("repairing_cap")

    prohibited = ["invent_user_facts"]
    if stage == "first_meeting":
        prohibited.append("invent_shared_history")

    return _finalize_policy(
        inputs=inputs,
        config=config,
        effective_context=effective_context,
        stage=stage,
        relationship_posture=relationship_posture,
        relationship_adjustment_gain=inputs.relationship.adjustment_gain,
        age=age,
        response_length=response_length,
        question_budget=question_budget,
        memory_budget=memory_budget,
        initiative=initiative,
        posture=posture,
        closure=closure,
        expression_style=expression_style,
        hardware_intensity=hardware_intensity,
        humor_level=humor_level,
        age_expression=age_expression,
        prohibited_behaviors=tuple(prohibited),
        explicit_recall_can_reference_memory=True,
        reason_codes=reason_codes,
    )


def _expression_style_from_temperament(
    temperament: BirthTemperament | None,
) -> CompanionExpressionStyle:
    if temperament is None:
        return CompanionExpressionStyle(
            exploration_orientation="balanced",
            expression_energy="natural",
            thought_organization="balanced",
            humor_level="low",
            initiative_bias="timely",
        )
    humor_by_playfulness = {
        "restrained": "none",
        "lighthearted": "low",
        "playful": "medium",
    }
    return CompanionExpressionStyle(
        exploration_orientation=temperament.exploration_orientation,
        expression_energy=temperament.expression_energy,
        thought_organization=temperament.thought_organization,
        humor_level=humor_by_playfulness[temperament.playfulness],
        initiative_bias=temperament.companion_initiative,
    )
