from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Literal, Mapping


SpeakerIdentity = Literal["confirmed", "unknown", "invalid"]
AcademicStage = Literal["freshman", "sophomore", "junior", "senior", "unknown"]
OwnershipScope = Literal["user", "relationship"]
ProjectionSurface = Literal[
    "voice", "miniprogram", "hardware", "initiative", "operator"
]
InteractionKind = Literal[
    "conversation",
    "general_qa",
    "explicit_recall",
    "reminder",
    "device_action",
]
ExplorationOrientation = Literal["focused", "balanced", "exploratory"]
ExpressionEnergy = Literal["calm", "natural", "lively"]
ThoughtOrganization = Literal["intuitive", "balanced", "structured"]
Playfulness = Literal["restrained", "lighthearted", "playful"]
CompanionInitiative = Literal["reserved", "timely", "proactive"]
HumorLevel = Literal["none", "low", "medium"]
InitiativeBias = Literal["reserved", "timely", "proactive"]
TemperamentSourceKind = Literal["pet_created", "legacy_backfill"]
CompanionContext = Literal[
    "ordinary",
    "fact_explanation",
    "open_learning_difficulty",
    "multi_task_choice",
    "success",
    "user_low_mood",
    "future_event",
    "explicit_boundary",
    "serious",
]
CompanionDeviceState = Literal["normal", "low_battery"]
HardwareExpressionField = Literal["kind", "intensity", "humor_level", "cadence"]
AgeVoiceCadence = Literal[
    "age_neutral",
    "start_then_explore",
    "receive_then_next_step",
    "conclusion_then_tradeoffs",
    "judgment_risk_then_close",
]
AgeQuestionPreference = Literal[
    "age_neutral",
    "exploratory",
    "clarifying",
    "tradeoff",
    "judgment_check",
]
AgeProblemOrganization = Literal[
    "age_neutral",
    "action_seed",
    "bounded_plan",
    "option_tradeoff",
    "principle_risk",
]
AgeMemoryUse = Literal[
    "age_neutral",
    "concrete_cue",
    "progress_continuity",
    "evidence_comparison",
    "revisable_long_view",
]
AgeInitiativePosture = Literal[
    "age_neutral",
    "light_invitation",
    "contextual_followup",
    "decision_point",
    "restrained_acknowledgement",
]
AgeHardwareCadence = Literal[
    "age_neutral",
    "quick_single",
    "steady_sequence",
    "deliberate_sequence",
    "restrained_single",
]
CompanionPolicyReasonCode = Literal[
    "unconfirmed_speaker_gate",
    "context_style_whitelist",
    "relationship_reveal_cap",
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
    "reunion_cautious_cap",
    "repairing_cap",
]
ObservationKind = Literal[
    "todo_created",
    "todo_updated",
    "todo_deleted",
    "todo_completed",
    "reminder_delivered",
    "reminder_tts_completed",
    "reminder_delivery_failed",
    "initiative_feedback",
    "course_created",
    "course_updated",
    "course_deleted",
    "goal_set",
    "goal_completed",
    "future_event_set",
    "future_event_cancelled",
    "boundary_set",
    "companion_feedback",
    "memory_corrected",
    "memory_candidate_confirmed",
    "memory_candidate_rejected",
]
AdjustmentDimension = Literal[
    "response_length",
    "question_frequency",
    "initiative_level",
    "memory_reference_depth",
    "emotional_posture",
    "humor_level",
    "closure_style",
    "hardware_expression_intensity",
]
AdjustmentStatus = Literal[
    "candidate",
    "trial",
    "active",
    "superseded",
    "expired",
    "revoked",
]
CompanionVAEventKind = Literal[
    "shared_success",
    "helpful_resolution",
    "ordinary_chat",
    "user_distress",
    "negative_feedback",
]
AdjustmentDirection = Literal["increase", "decrease"]
AdjustmentEvidenceQualification = Literal["eligible", "clue_only", "rejected"]
BehaviorAdjustmentSource = Literal[
    "inferred_adjustment", "explicit_feedback", "explicit_contract"
]
_ADJUSTMENT_DIMENSIONS = {
    "response_length",
    "question_frequency",
    "initiative_level",
    "memory_reference_depth",
    "emotional_posture",
    "humor_level",
    "closure_style",
    "hardware_expression_intensity",
}
_ADJUSTMENT_STATUSES = {
    "candidate",
    "trial",
    "active",
    "superseded",
    "expired",
    "revoked",
}

_TEMPERAMENT_LEVELS = {
    "exploration_orientation": {"focused", "balanced", "exploratory"},
    "expression_energy": {"calm", "natural", "lively"},
    "thought_organization": {"intuitive", "balanced", "structured"},
    "playfulness": {"restrained", "lighthearted", "playful"},
    "companion_initiative": {"reserved", "timely", "proactive"},
}
_COMPANION_POLICY_REASON_CODES = {
    "unconfirmed_speaker_gate",
    "context_style_whitelist",
    "relationship_reveal_cap",
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
    "reunion_cautious_cap",
    "repairing_cap",
}

_ACADEMIC_STAGE_BY_GRADE = {
    "大一": "freshman",
    "大二": "sophomore",
    "大三": "junior",
    "大四": "senior",
    "freshman": "freshman",
    "sophomore": "sophomore",
    "junior": "junior",
    "senior": "senior",
}
_XIAOXIN_AGE_BY_STAGE = {
    "freshman": 1,
    "sophomore": 2,
    "junior": 3,
    "senior": 4,
    "unknown": None,
}


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _require_aware_iso_datetime(name: str, value: str) -> None:
    _require_text(name, value)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone offset")


def _require_surface(value: str) -> None:
    if value not in {"voice", "miniprogram", "hardware", "initiative", "operator"}:
        raise ValueError("surface is invalid")


def normalize_academic_stage(raw_grade: object) -> AcademicStage:
    if raw_grade is None:
        return "unknown"
    normalized = str(raw_grade).strip().lower()
    if not normalized:
        return "unknown"
    direct = _ACADEMIC_STAGE_BY_GRADE.get(normalized)
    if direct is not None:
        return direct
    match = re.fullmatch(r"大([一二三四])(?:年级|本科)?", normalized)
    if match is None:
        return "unknown"
    return _ACADEMIC_STAGE_BY_GRADE[f"大{match.group(1)}"]


def xiaoxin_age_for_stage(stage: AcademicStage) -> int | None:
    try:
        return _XIAOXIN_AGE_BY_STAGE[stage]
    except KeyError as exc:
        raise ValueError("academic_stage is invalid") from exc


@dataclass(frozen=True)
class BirthTemperament:
    pet_id: str
    generator_version: str
    exploration_orientation: ExplorationOrientation
    expression_energy: ExpressionEnergy
    thought_organization: ThoughtOrganization
    playfulness: Playfulness
    companion_initiative: CompanionInitiative
    generated_at: str
    source_kind: TemperamentSourceKind

    def __post_init__(self) -> None:
        _require_text("pet_id", self.pet_id)
        _require_text("generator_version", self.generator_version)
        for field_name, allowed in _TEMPERAMENT_LEVELS.items():
            if getattr(self, field_name) not in allowed:
                raise ValueError(f"{field_name} is invalid")
        _require_aware_iso_datetime("generated_at", self.generated_at)
        if self.source_kind not in {"pet_created", "legacy_backfill"}:
            raise ValueError("source_kind is invalid")


@dataclass(frozen=True)
class CompanionExpressionStyle:
    exploration_orientation: ExplorationOrientation
    expression_energy: ExpressionEnergy
    thought_organization: ThoughtOrganization
    humor_level: HumorLevel
    initiative_bias: InitiativeBias

    def __post_init__(self) -> None:
        allowed_values = {
            "exploration_orientation": _TEMPERAMENT_LEVELS["exploration_orientation"],
            "expression_energy": _TEMPERAMENT_LEVELS["expression_energy"],
            "thought_organization": _TEMPERAMENT_LEVELS["thought_organization"],
            "humor_level": {"none", "low", "medium"},
            "initiative_bias": _TEMPERAMENT_LEVELS["companion_initiative"],
        }
        for field_name, allowed in allowed_values.items():
            if getattr(self, field_name) not in allowed:
                raise ValueError(f"{field_name} is invalid")


@dataclass(frozen=True)
class BehaviorAdjustmentSignal:
    dimension: str
    value: str
    source_kind: BehaviorAdjustmentSource
    confidence: float = 1.0
    direction: AdjustmentDirection | None = None

    def __post_init__(self) -> None:
        if self.dimension not in _ADJUSTMENT_DIMENSIONS:
            raise ValueError("behavior adjustment dimension is invalid")
        _require_text("behavior adjustment value", self.value)
        if self.source_kind not in {
            "inferred_adjustment",
            "explicit_feedback",
            "explicit_contract",
        }:
            raise ValueError("behavior adjustment source is invalid")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("behavior adjustment confidence is invalid")
        if self.direction not in {None, "increase", "decrease"}:
            raise ValueError("behavior adjustment direction is invalid")


@dataclass(frozen=True)
class TurnBehaviorPlan:
    primary_move: str
    information_order: str
    question_mode: str
    support_move: str
    closure_intent: str
    initiative_hook: str
    salient_traits: tuple[str, ...] = ()
    version: str = "turn-behavior-plan-v1"

    def __post_init__(self) -> None:
        allowed = {
            "primary_move": {
                "acknowledge",
                "direct_answer",
                "clarify",
                "co_explore",
                "practical_support",
                "emotional_support",
                "celebrate",
            },
            "information_order": {
                "key_point_first",
                "context_first",
                "stepwise",
                "collaborative",
            },
            "question_mode": {
                "none",
                "needed_only",
                "one_key_question",
                "light_invitation",
            },
            "support_move": {
                "none",
                "reflect",
                "validate",
                "ground",
                "next_step",
            },
            "closure_intent": {
                "concise",
                "leave_space",
                "warm",
                "next_step",
            },
            "initiative_hook": {"none", "context_followup"},
        }
        for field_name, choices in allowed.items():
            if getattr(self, field_name) not in choices:
                raise ValueError(f"turn behavior {field_name} is invalid")
        if len(self.salient_traits) > 2 or len(set(self.salient_traits)) != len(
            self.salient_traits
        ):
            raise ValueError("turn behavior salient traits are invalid")
        if any(
            trait
            not in {
                "exploration_orientation",
                "expression_energy",
                "thought_organization",
                "humor_level",
                "initiative_bias",
            }
            for trait in self.salient_traits
        ):
            raise ValueError("turn behavior salient trait is invalid")
        _require_text("turn behavior version", self.version)


@dataclass(frozen=True)
class CompanionAgeExpression:
    voice_cadence: AgeVoiceCadence
    question_preference: AgeQuestionPreference
    problem_organization: AgeProblemOrganization
    memory_use: AgeMemoryUse
    initiative_posture: AgeInitiativePosture
    hardware_cadence: AgeHardwareCadence

    def __post_init__(self) -> None:
        allowed_values = {
            "voice_cadence": {
                "age_neutral",
                "start_then_explore",
                "receive_then_next_step",
                "conclusion_then_tradeoffs",
                "judgment_risk_then_close",
            },
            "question_preference": {
                "age_neutral",
                "exploratory",
                "clarifying",
                "tradeoff",
                "judgment_check",
            },
            "problem_organization": {
                "age_neutral",
                "action_seed",
                "bounded_plan",
                "option_tradeoff",
                "principle_risk",
            },
            "memory_use": {
                "age_neutral",
                "concrete_cue",
                "progress_continuity",
                "evidence_comparison",
                "revisable_long_view",
            },
            "initiative_posture": {
                "age_neutral",
                "light_invitation",
                "contextual_followup",
                "decision_point",
                "restrained_acknowledgement",
            },
            "hardware_cadence": {
                "age_neutral",
                "quick_single",
                "steady_sequence",
                "deliberate_sequence",
                "restrained_single",
            },
        }
        for field_name, allowed in allowed_values.items():
            if getattr(self, field_name) not in allowed:
                raise ValueError(f"{field_name} is invalid")


_AGE_EXPRESSION_BY_STAGE = {
    "unknown": CompanionAgeExpression(
        voice_cadence="age_neutral",
        question_preference="age_neutral",
        problem_organization="age_neutral",
        memory_use="age_neutral",
        initiative_posture="age_neutral",
        hardware_cadence="age_neutral",
    ),
    "freshman": CompanionAgeExpression(
        voice_cadence="start_then_explore",
        question_preference="exploratory",
        problem_organization="action_seed",
        memory_use="concrete_cue",
        initiative_posture="light_invitation",
        hardware_cadence="quick_single",
    ),
    "sophomore": CompanionAgeExpression(
        voice_cadence="receive_then_next_step",
        question_preference="clarifying",
        problem_organization="bounded_plan",
        memory_use="progress_continuity",
        initiative_posture="contextual_followup",
        hardware_cadence="steady_sequence",
    ),
    "junior": CompanionAgeExpression(
        voice_cadence="conclusion_then_tradeoffs",
        question_preference="tradeoff",
        problem_organization="option_tradeoff",
        memory_use="evidence_comparison",
        initiative_posture="decision_point",
        hardware_cadence="deliberate_sequence",
    ),
    "senior": CompanionAgeExpression(
        voice_cadence="judgment_risk_then_close",
        question_preference="judgment_check",
        problem_organization="principle_risk",
        memory_use="revisable_long_view",
        initiative_posture="restrained_acknowledgement",
        hardware_cadence="restrained_single",
    ),
}


def age_expression_for_stage(stage: AcademicStage) -> CompanionAgeExpression:
    try:
        return _AGE_EXPRESSION_BY_STAGE[stage]
    except KeyError as exc:
        raise ValueError("academic_stage is invalid") from exc


def _default_expression_style() -> CompanionExpressionStyle:
    return CompanionExpressionStyle(
        exploration_orientation="balanced",
        expression_energy="natural",
        thought_organization="balanced",
        humor_level="low",
        initiative_bias="timely",
    )


def build_companion_subject_context(
    *,
    owner_user_id: str | None,
    pet_id: str | None,
    memory_subject_id: str,
    subject_kind: str,
    raw_grade: object,
) -> "CompanionSubjectContext":
    _require_text("owner_user_id", owner_user_id or "")
    _require_text("pet_id", pet_id or "")
    speaker_identity: SpeakerIdentity
    if subject_kind == "user_speaker":
        speaker_identity = "confirmed"
    elif subject_kind == "device_unknown":
        speaker_identity = "unknown"
    else:
        speaker_identity = "invalid"
    return CompanionSubjectContext(
        owner_user_id=owner_user_id,
        pet_id=pet_id,
        memory_subject_id=memory_subject_id,
        speaker_identity=speaker_identity,
        academic_stage=normalize_academic_stage(raw_grade),
        persistence_allowed=speaker_identity == "confirmed",
    )


@dataclass(frozen=True)
class CompanionSubjectContext:
    owner_user_id: str
    pet_id: str
    memory_subject_id: str
    speaker_identity: SpeakerIdentity
    academic_stage: AcademicStage
    persistence_allowed: bool

    def __post_init__(self) -> None:
        _require_text("owner_user_id", self.owner_user_id)
        _require_text("pet_id", self.pet_id)
        _require_text("memory_subject_id", self.memory_subject_id)
        if self.speaker_identity not in {"confirmed", "unknown", "invalid"}:
            raise ValueError("speaker_identity is invalid")
        if self.academic_stage not in {
            "freshman",
            "sophomore",
            "junior",
            "senior",
            "unknown",
        }:
            raise ValueError("academic_stage is invalid")
        if self.speaker_identity != "confirmed" and self.persistence_allowed:
            raise ValueError("unconfirmed speakers cannot persist private memory")


@dataclass(frozen=True)
class CompanionObservation:
    idempotency_key: str
    subject: CompanionSubjectContext
    kind: ObservationKind
    source_kind: str
    source_ref: str
    occurred_at: str
    payload: Mapping[str, object]
    safe_summary: str

    def __post_init__(self) -> None:
        for name, value in (
            ("idempotency_key", self.idempotency_key),
            ("source_kind", self.source_kind),
            ("source_ref", self.source_ref),
            ("safe_summary", self.safe_summary),
        ):
            _require_text(name, value)
        if self.kind not in {
            "todo_created",
            "todo_updated",
            "todo_deleted",
            "todo_completed",
            "reminder_delivered",
            "reminder_tts_completed",
            "reminder_delivery_failed",
            "initiative_feedback",
            "course_created",
            "course_updated",
            "course_deleted",
            "goal_set",
            "goal_completed",
            "future_event_set",
            "future_event_cancelled",
            "boundary_set",
            "companion_feedback",
            "memory_corrected",
            "memory_candidate_confirmed",
            "memory_candidate_rejected",
        }:
            raise ValueError("observation kind is invalid")
        _require_aware_iso_datetime("occurred_at", self.occurred_at)
        if not isinstance(self.payload, Mapping):
            raise ValueError("observation payload must be a mapping")


@dataclass(frozen=True)
class CompanionVAEvent:
    event_id: str
    subject: CompanionSubjectContext
    relationship_epoch_id: str
    kind: CompanionVAEventKind
    occurred_at: str
    received_at: str
    source_kind: Literal["turn_analysis", "delivery_outcome", "companion_feedback"]
    source_ref: str

    def __post_init__(self) -> None:
        _require_text("event_id", self.event_id)
        _require_text("relationship_epoch_id", self.relationship_epoch_id)
        _require_text("source_ref", self.source_ref)
        if self.subject.speaker_identity != "confirmed":
            raise ValueError("VA events require a confirmed speaker")
        if not self.subject.persistence_allowed:
            raise ValueError("VA events require persistence permission")
        if self.kind not in {
            "shared_success",
            "helpful_resolution",
            "ordinary_chat",
            "user_distress",
            "negative_feedback",
        }:
            raise ValueError("VA event kind is invalid")
        if self.source_kind not in {
            "turn_analysis",
            "delivery_outcome",
            "companion_feedback",
        }:
            raise ValueError("VA event source kind is invalid")
        _require_aware_iso_datetime("occurred_at", self.occurred_at)
        _require_aware_iso_datetime("received_at", self.received_at)
        if datetime.fromisoformat(self.occurred_at) > datetime.fromisoformat(
            self.received_at
        ):
            raise ValueError("VA event cannot be received before it occurred")


@dataclass(frozen=True)
class CompanionVAEventResult:
    event_id: str
    status: str

    def __post_init__(self) -> None:
        _require_text("event_id", self.event_id)
        if self.status not in {
            "applied",
            "duplicate",
            "ignored_out_of_order",
            "ignored_stale_epoch",
        }:
            raise ValueError("VA event result status is invalid")


@dataclass(frozen=True)
class CompanionObserveResult:
    observation_id: str
    status: str
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text("observation_id", self.observation_id)
        if self.status not in {
            "recorded",
            "duplicate",
            "deferred",
            "not_persisted",
        }:
            raise ValueError("observation result status is invalid")


@dataclass(frozen=True)
class CompanionPolicy:
    xiaoxin_age: int | None
    relationship_stage: str
    response_length: str
    question_budget: int
    memory_reference_budget: int
    initiative_level: str
    emotional_posture: str
    closure_style: str
    relationship_posture: str = "steady"
    relationship_adjustment_gain: float = 1.0
    expression_style: CompanionExpressionStyle = field(
        default_factory=_default_expression_style
    )
    prohibited_behaviors: tuple[str, ...] = ()
    hardware_expression: Mapping[str, object] = field(default_factory=dict)
    age_expression: CompanionAgeExpression = field(
        default_factory=lambda: age_expression_for_stage("unknown")
    )
    version: str = "companion-policy-v6"
    reason_codes: tuple[CompanionPolicyReasonCode, ...] = ()

    def __post_init__(self) -> None:
        if self.xiaoxin_age not in {None, 1, 2, 3, 4}:
            raise ValueError("xiaoxin_age must be null or between 1 and 4")
        if self.relationship_stage not in {
            "first_meeting",
            "familiar",
            "attuned",
            "long_term_companion",
        }:
            raise ValueError("relationship_stage is invalid")
        if self.relationship_posture not in {
            "steady",
            "reunion_cautious",
            "repairing",
        }:
            raise ValueError("relationship_posture is invalid")
        if self.relationship_adjustment_gain not in {0.0, 0.5, 0.75, 1.0}:
            raise ValueError("relationship_adjustment_gain is invalid")
        if self.question_budget < 0 or self.memory_reference_budget < 0:
            raise ValueError("policy budgets must be non-negative")
        if any(
            code not in _COMPANION_POLICY_REASON_CODES for code in self.reason_codes
        ):
            raise ValueError("policy reason code is invalid")
        for name, value in (
            ("response_length", self.response_length),
            ("initiative_level", self.initiative_level),
            ("emotional_posture", self.emotional_posture),
            ("closure_style", self.closure_style),
            ("version", self.version),
        ):
            _require_text(name, value)


@dataclass(frozen=True)
class RelationshipEpoch:
    epoch_id: str
    pet_id: str
    started_at: str
    ended_at: str | None
    start_reason: str
    end_reason: str | None

    def __post_init__(self) -> None:
        _require_text("epoch_id", self.epoch_id)
        _require_text("pet_id", self.pet_id)
        _require_aware_iso_datetime("started_at", self.started_at)
        _require_text("start_reason", self.start_reason)
        if self.ended_at is not None:
            _require_aware_iso_datetime("ended_at", self.ended_at)
        if (self.ended_at is None) != (self.end_reason is None):
            raise ValueError("ended_at and end_reason must be set together")


@dataclass(frozen=True)
class CompanionEvidence:
    evidence_id: str
    pet_id: str
    memory_subject_id: str
    ownership_scope: OwnershipScope
    relationship_epoch_id: str | None
    kind: str
    content: Mapping[str, object]
    source_kind: str
    source_ref: str
    source_summary: str
    attribution: str
    confidence: float
    occurred_at: str
    retention: str
    status: str
    prompt_eligible: bool
    expires_at: str | None = None
    fact_key: str | None = None
    importance: float = 0.5
    sensitivity: str = "private"
    valid_from: str | None = None
    valid_until: str | None = None
    speaker_identity: SpeakerIdentity = "confirmed"

    def __post_init__(self) -> None:
        for name, value in (
            ("evidence_id", self.evidence_id),
            ("pet_id", self.pet_id),
            ("memory_subject_id", self.memory_subject_id),
            ("kind", self.kind),
            ("source_kind", self.source_kind),
            ("source_ref", self.source_ref),
            ("source_summary", self.source_summary),
            ("occurred_at", self.occurred_at),
            ("retention", self.retention),
            ("status", self.status),
        ):
            _require_text(name, value)
        if self.ownership_scope not in {"user", "relationship"}:
            raise ValueError("ownership_scope is invalid")
        if self.ownership_scope == "relationship" and not self.relationship_epoch_id:
            raise ValueError(
                "relationship_epoch_id is required for relationship Evidence"
            )
        if self.status not in {
            "candidate",
            "active",
            "superseded",
            "forgotten",
            "expired",
        }:
            raise ValueError("status is invalid")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not 0.0 <= self.importance <= 1.0:
            raise ValueError("importance must be between 0 and 1")
        if self.sensitivity not in {"low", "private", "sensitive"}:
            raise ValueError("sensitivity is invalid")
        if self.speaker_identity not in {"confirmed", "unknown", "invalid"}:
            raise ValueError("speaker_identity is invalid")
        if self.fact_key is not None:
            _require_text("fact_key", self.fact_key)
        _require_aware_iso_datetime("occurred_at", self.occurred_at)
        if self.expires_at is not None:
            _require_aware_iso_datetime("expires_at", self.expires_at)
        if self.valid_from is not None:
            _require_aware_iso_datetime("valid_from", self.valid_from)
        if self.valid_until is not None:
            _require_aware_iso_datetime("valid_until", self.valid_until)
        if (
            self.status in {"forgotten", "superseded", "expired"}
            and self.prompt_eligible
        ):
            raise ValueError(
                f"prompt_eligible must be false when status is {self.status}"
            )


@dataclass(frozen=True)
class SessionCapsule:
    capsule_id: str
    pet_id: str
    relationship_epoch_id: str
    evidence_ids: tuple[str, ...]
    safe_summary: str
    interaction_outcome: str
    adjustment_signals: tuple[str, ...]
    status: str
    created_at: str
    expires_at: str | None

    def __post_init__(self) -> None:
        for name, value in (
            ("capsule_id", self.capsule_id),
            ("pet_id", self.pet_id),
            ("relationship_epoch_id", self.relationship_epoch_id),
            ("safe_summary", self.safe_summary),
            ("interaction_outcome", self.interaction_outcome),
            ("status", self.status),
        ):
            _require_text(name, value)
        if not self.evidence_ids or any(
            not evidence_id.strip() for evidence_id in self.evidence_ids
        ):
            raise ValueError("SessionCapsule requires Evidence IDs")
        if any(
            signal not in _ADJUSTMENT_DIMENSIONS for signal in self.adjustment_signals
        ):
            raise ValueError("SessionCapsule adjustment signal is invalid")
        if self.status not in {"active", "inactive", "invalidated", "expired"}:
            raise ValueError("SessionCapsule status is invalid")
        _require_aware_iso_datetime("created_at", self.created_at)
        if self.expires_at is not None:
            _require_aware_iso_datetime("expires_at", self.expires_at)


@dataclass(frozen=True)
class AdjustmentQualificationLineage:
    evidence_id: str
    qualification: AdjustmentEvidenceQualification
    reason_code: str
    qualifying_local_date: str | None
    contributes_date: bool

    def __post_init__(self) -> None:
        _require_text("evidence_id", self.evidence_id)
        _require_text("reason_code", self.reason_code)
        if self.qualification not in {"eligible", "clue_only", "rejected"}:
            raise ValueError("adjustment Evidence qualification is invalid")
        if self.qualification == "eligible":
            if self.qualifying_local_date is None:
                raise ValueError("eligible adjustment Evidence requires a local date")
            try:
                datetime.fromisoformat(self.qualifying_local_date)
            except ValueError as exc:
                raise ValueError("qualifying_local_date is invalid") from exc
        elif self.qualifying_local_date is not None or self.contributes_date:
            raise ValueError(
                "non-eligible adjustment Evidence cannot contribute a date"
            )


@dataclass(frozen=True)
class CompanionAdjustment:
    adjustment_id: str
    pet_id: str
    relationship_epoch_id: str
    dimension: AdjustmentDimension
    value: str
    scope: str
    status: AdjustmentStatus
    evidence_ids: tuple[str, ...]
    confidence: float
    generated_by: str
    created_at: str
    valid_until: str | None
    behavior_key: str | None = None
    context_scope: str | None = None
    direction: AdjustmentDirection | None = None
    qualification_lineage: tuple[AdjustmentQualificationLineage, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("adjustment_id", self.adjustment_id),
            ("pet_id", self.pet_id),
            ("relationship_epoch_id", self.relationship_epoch_id),
            ("dimension", self.dimension),
            ("value", self.value),
            ("scope", self.scope),
            ("status", self.status),
            ("generated_by", self.generated_by),
        ):
            _require_text(name, value)
        if not self.evidence_ids or any(
            not evidence_id.strip() for evidence_id in self.evidence_ids
        ):
            raise ValueError("CompanionAdjustment requires Evidence IDs")
        if self.dimension not in _ADJUSTMENT_DIMENSIONS:
            raise ValueError("CompanionAdjustment dimension is invalid")
        if self.status not in _ADJUSTMENT_STATUSES:
            raise ValueError("CompanionAdjustment status is invalid")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.behavior_key is not None:
            _require_text("behavior_key", self.behavior_key)
        if self.context_scope is not None:
            _require_text("context_scope", self.context_scope)
        if self.direction not in {None, "increase", "decrease"}:
            raise ValueError("CompanionAdjustment direction is invalid")
        structured_key_parts = (
            self.behavior_key,
            self.context_scope,
            self.direction,
        )
        if any(part is None for part in structured_key_parts) and any(
            part is not None for part in structured_key_parts
        ):
            raise ValueError("CompanionAdjustment structured key is incomplete")
        _require_aware_iso_datetime("created_at", self.created_at)
        if self.valid_until is not None:
            _require_aware_iso_datetime("valid_until", self.valid_until)


@dataclass(frozen=True)
class CompanionChapter:
    chapter_id: str
    pet_id: str
    relationship_epoch_id: str
    academic_stage: str
    xiaoxin_age: int | None
    period_start: str
    period_end: str | None
    evidence_ids: tuple[str, ...]
    shared_moment_ids: tuple[str, ...]
    adjustment_ids: tuple[str, ...]
    safe_narrative: str
    status: Literal["draft", "active", "superseded", "invalidated"]
    version: int

    def __post_init__(self) -> None:
        for name, value in (
            ("chapter_id", self.chapter_id),
            ("pet_id", self.pet_id),
            ("relationship_epoch_id", self.relationship_epoch_id),
            ("academic_stage", self.academic_stage),
            ("safe_narrative", self.safe_narrative),
            ("status", self.status),
        ):
            _require_text(name, value)
        if self.xiaoxin_age not in {None, 1, 2, 3, 4}:
            raise ValueError("xiaoxin_age must be null or between 1 and 4")
        if self.version < 1:
            raise ValueError("chapter version must be positive")
        if not self.evidence_ids:
            raise ValueError("CompanionChapter requires Evidence IDs")
        if not set(self.shared_moment_ids) <= set(self.evidence_ids):
            raise ValueError("shared moments must be chapter Evidence")
        _require_aware_iso_datetime("period_start", self.period_start)
        if self.period_end is not None:
            _require_aware_iso_datetime("period_end", self.period_end)


@dataclass(frozen=True)
class CompanionTurnRequest:
    turn_id: str
    subject: CompanionSubjectContext
    request_digest: str
    surface: ProjectionSurface
    occurred_at: str
    interaction_kind: InteractionKind = "conversation"
    source_text: str | None = None
    conversation_digest: str | None = None
    retrieval_query: str | None = None
    retrieval_hints: Mapping[str, object] = field(default_factory=dict)
    current_turn_corrections: tuple[str, ...] = ()
    context: CompanionContext = "ordinary"

    def __post_init__(self) -> None:
        _require_text("turn_id", self.turn_id)
        _require_text("request_digest", self.request_digest)
        _require_surface(self.surface)
        _require_aware_iso_datetime("occurred_at", self.occurred_at)
        if self.interaction_kind not in {
            "conversation",
            "general_qa",
            "explicit_recall",
            "reminder",
            "device_action",
        }:
            raise ValueError("interaction_kind is invalid")
        if self.source_text is not None:
            _require_text("source_text", self.source_text)
            if len(self.source_text) > 2000:
                raise ValueError("source_text exceeds the short-term source limit")
        if self.conversation_digest is not None:
            _require_text("conversation_digest", self.conversation_digest)
            if len(self.conversation_digest) > 128:
                raise ValueError("conversation_digest exceeds the short-term limit")
        if self.retrieval_query is not None:
            _require_text("retrieval_query", self.retrieval_query)
            if len(self.retrieval_query) > 500:
                raise ValueError("retrieval_query exceeds the current-turn limit")
        if not isinstance(self.retrieval_hints, Mapping):
            raise ValueError("retrieval_hints must be a mapping")
        allowed_hint_keys = {
            "fact_keys",
            "kinds",
            "time_from",
            "time_to",
            "exclude_sensitivities",
        }
        if not set(self.retrieval_hints) <= allowed_hint_keys:
            raise ValueError("retrieval_hints contains unsupported fields")
        for name in ("fact_keys", "kinds", "exclude_sensitivities"):
            values = self.retrieval_hints.get(name, ())
            if (
                not isinstance(values, (tuple, list))
                or len(values) > 8
                or any(not isinstance(item, str) or not item.strip() for item in values)
            ):
                raise ValueError(f"retrieval_hints {name} must be a short text list")
        excluded = set(self.retrieval_hints.get("exclude_sensitivities", ()))
        if not excluded <= {"low", "private", "sensitive"}:
            raise ValueError("retrieval_hints exclude_sensitivities is invalid")
        for name in ("time_from", "time_to"):
            value = self.retrieval_hints.get(name)
            if value is not None:
                _require_aware_iso_datetime(f"retrieval_hints {name}", value)
        if not set(self.current_turn_corrections) <= {
            "no_follow_up",
            "concise",
            "no_humor",
            "no_memory_reference",
            "settle_hardware",
        }:
            raise ValueError("current turn corrections are invalid")
        if self.context not in {
            "ordinary",
            "fact_explanation",
            "open_learning_difficulty",
            "multi_task_choice",
            "success",
            "user_low_mood",
            "future_event",
            "explicit_boundary",
            "serious",
        }:
            raise ValueError("companion context is invalid")


@dataclass(frozen=True)
class PreparedCompanionTurn:
    turn_id: str
    owner_user_id: str
    pet_id: str
    memory_subject_id: str
    relationship_epoch_id: str | None
    request_digest: str
    occurred_at: str
    prepared_token: str
    policy: CompanionPolicy
    persistence_allowed: bool
    behavior_plan: TurnBehaviorPlan | None = None
    behavior_plan_active: bool = False
    prompt_context: tuple[str, ...] = ()
    used_evidence_ids: tuple[str, ...] = ()
    academic_stage: AcademicStage = "unknown"
    surface: ProjectionSurface = "voice"
    interaction_kind: InteractionKind = "conversation"
    source_text: str | None = None
    conversation_digest: str | None = None
    growth_moment: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("turn_id", self.turn_id),
            ("owner_user_id", self.owner_user_id),
            ("pet_id", self.pet_id),
            ("memory_subject_id", self.memory_subject_id),
            ("request_digest", self.request_digest),
            ("prepared_token", self.prepared_token),
        ):
            _require_text(name, value)
        _require_aware_iso_datetime("occurred_at", self.occurred_at)
        if self.source_text is not None:
            _require_text("source_text", self.source_text)
        if self.conversation_digest is not None:
            _require_text("conversation_digest", self.conversation_digest)
        if self.academic_stage not in {
            "freshman",
            "sophomore",
            "junior",
            "senior",
            "unknown",
        }:
            raise ValueError("academic_stage is invalid")
        _require_surface(self.surface)
        if self.interaction_kind not in {
            "conversation",
            "general_qa",
            "explicit_recall",
            "reminder",
            "device_action",
        }:
            raise ValueError("interaction_kind is invalid")
        if any(not evidence_id.strip() for evidence_id in self.used_evidence_ids):
            raise ValueError("used_evidence_ids cannot contain blank IDs")
        if self.growth_moment is not None:
            required_growth_fields = {
                "moment_id",
                "from_stage",
                "to_stage",
                "xiaoxin_age",
                "safe_summary",
                "occurred_at",
                "relationship_epoch_id",
                "evidence_id",
            }
            if not required_growth_fields <= set(self.growth_moment):
                raise ValueError("growth_moment is missing required fields")


@dataclass(frozen=True)
class CompanionTurnOutcome:
    visible_response: str
    assistant_action: str
    delivery_status: str
    feedback_signals: tuple[Mapping[str, object], ...] = ()
    legacy_memory_fact_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text("visible_response", self.visible_response)
        _require_text("assistant_action", self.assistant_action)
        _require_text("delivery_status", self.delivery_status)
        if any(
            not isinstance(item, str) or not item.strip()
            for item in self.legacy_memory_fact_keys
        ):
            raise ValueError("legacy_memory_fact_keys must contain text")


@dataclass(frozen=True)
class CompanionCommitResult:
    turn_id: str
    status: str
    evidence_ids: tuple[str, ...] = ()
    job_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text("turn_id", self.turn_id)
        _require_text("status", self.status)


@dataclass(frozen=True)
class CompanionControlCommand:
    action: Literal[
        "reset_relationship",
        "forget_evidence",
        "forget_theme",
        "correct_evidence",
        "set_boundary",
        "revoke_boundary",
        "purge_personal_memory",
        "record_initiative_feedback",
        "sync_academic_stage",
        "set_growth_moments_enabled",
        "set_initiative_quiet_hours",
        "revoke_adjustment",
        "set_interaction_contract",
        "revoke_interaction_contract",
        "restore_default_expression",
        "confirm_candidate",
        "reject_candidate",
    ]
    subject: CompanionSubjectContext
    payload: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action not in {
            "reset_relationship",
            "forget_evidence",
            "forget_theme",
            "correct_evidence",
            "set_boundary",
            "revoke_boundary",
            "purge_personal_memory",
            "record_initiative_feedback",
            "sync_academic_stage",
            "set_growth_moments_enabled",
            "set_initiative_quiet_hours",
            "revoke_adjustment",
            "set_interaction_contract",
            "revoke_interaction_contract",
            "restore_default_expression",
            "confirm_candidate",
            "reject_candidate",
        }:
            raise ValueError("action is invalid")


@dataclass(frozen=True)
class CompanionControlResult:
    action: str
    status: str
    retained: int = 0
    deactivated: int = 0
    forgotten: int = 0
    requeued: int = 0

    def __post_init__(self) -> None:
        _require_text("action", self.action)
        _require_text("status", self.status)
        if min(self.retained, self.deactivated, self.forgotten, self.requeued) < 0:
            raise ValueError("control result counts must be non-negative")


@dataclass(frozen=True)
class CompanionProjectionRequest:
    subject: CompanionSubjectContext
    surface: ProjectionSurface
    now: str
    initiative_enabled: bool = True
    quiet_hours_active: bool = False
    device_available: bool = True
    higher_priority_pending: bool = False
    initiative_decision_id: str | None = None
    device_state: CompanionDeviceState = "normal"

    def __post_init__(self) -> None:
        _require_surface(self.surface)
        _require_aware_iso_datetime("now", self.now)
        if self.device_state not in {"normal", "low_battery"}:
            raise ValueError("companion device state is invalid")
        if self.initiative_decision_id is not None:
            _require_text("initiative_decision_id", self.initiative_decision_id)
            if self.surface != "initiative":
                raise ValueError(
                    "initiative_decision_id is only valid for initiative projection"
                )


@dataclass(frozen=True)
class CompanionProjection:
    surface: ProjectionSurface
    xiaoxin_age: int | None
    relationship_stage: str
    payload: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_surface(self.surface)
        if self.xiaoxin_age not in {None, 1, 2, 3, 4}:
            raise ValueError("xiaoxin_age must be null or between 1 and 4")
        if self.relationship_stage not in {
            "first_meeting",
            "familiar",
            "attuned",
            "long_term_companion",
        }:
            raise ValueError("relationship_stage is invalid")


@dataclass(frozen=True)
class CompanionWorkResult:
    claimed: int = 0
    succeeded: int = 0
    retried: int = 0
    failed: int = 0

    def __post_init__(self) -> None:
        if min(self.claimed, self.succeeded, self.retried, self.failed) < 0:
            raise ValueError("work result counts must be non-negative")


class CompanionContractError(ValueError):
    pass


class CompanionIdempotencyConflict(RuntimeError):
    pass


class CompanionUnavailableError(RuntimeError):
    pass
