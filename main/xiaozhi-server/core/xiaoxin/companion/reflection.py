from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Protocol


REFLECTION_REQUEST_VERSION = "companion-reflection-request-v1"
REFLECTION_PROPOSAL_VERSION = "companion-reflection-proposal-v1"
ALLOWED_ADJUSTMENT_DIMENSIONS = frozenset(
    {
        "response_length",
        "question_frequency",
        "initiative_level",
        "memory_reference_depth",
        "emotional_posture",
        "humor_level",
        "closure_style",
        "hardware_expression_intensity",
    }
)
ALLOWED_ADJUSTMENT_VALUES = {
    "response_length": frozenset({"short", "standard", "expanded"}),
    "question_frequency": frozenset({"never", "less", "often"}),
    "initiative_level": frozenset({"disabled", "low", "medium"}),
    "memory_reference_depth": frozenset(
        {"never", "shallow", "moderate", "deep"}
    ),
    "emotional_posture": frozenset(
        {"neutral", "warm", "supportive", "attuned"}
    ),
    "humor_level": frozenset({"none", "low", "medium"}),
    "closure_style": frozenset(
        {"concise", "warm", "relational", "familiar"}
    ),
    "hardware_expression_intensity": frozenset(
        {"low", "neutral", "medium", "high"}
    ),
}
ALLOWED_ADJUSTMENT_SCOPES = frozenset(
    {
        "all",
        "voice",
        "miniprogram",
        "hardware",
        "initiative",
        "operator",
        "conversation",
        "general_qa",
        "explicit_recall",
        "reminder",
        "device_action",
    }
)
MEMORY_CANDIDATE_KEYS = frozenset(
    {
        "fact_key",
        "kind",
        "value",
        "source_turn_id",
        "source_quote",
        "claim_type",
        "sensitivity",
        "confidence",
    }
)
MEMORY_CANDIDATE_KINDS = frozenset(
    {"goal", "preference", "interest", "life_event", "relationship_context", "wellbeing"}
)
MEMORY_CANDIDATE_CLAIM_TYPES = frozenset(
    {
        "explicit_statement",
        "inference",
        "reported_speech",
        "hypothetical",
        "negated",
        "dream",
        "joke",
        "asr_uncertain",
    }
)


class ReflectionValidationError(ValueError):
    """A permanent proposal error that must not be retried."""


@dataclass(frozen=True)
class ReflectionEvidence:
    evidence_id: str
    kind: str
    ownership_scope: str
    source_summary: str
    confidence: float


@dataclass(frozen=True)
class ReflectionTurnSource:
    turn_id: str
    text: str
    occurred_at: str


@dataclass(frozen=True)
class ReflectionRequest:
    job_id: str
    job_kind: str
    pet_id: str
    relationship_epoch_id: str | None
    evidence: tuple[ReflectionEvidence, ...]
    turn_sources: tuple[ReflectionTurnSource, ...] = ()
    schema_version: str = REFLECTION_REQUEST_VERSION


@dataclass(frozen=True)
class AdjustmentProposal:
    dimension: str
    value: str
    scope: str
    evidence_ids: tuple[str, ...]
    confidence: float


@dataclass(frozen=True)
class ChapterStatementProposal:
    claim_scope: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReflectionProposal:
    schema_version: str
    safe_summary: str
    evidence_ids: tuple[str, ...] = ()
    adjustments: tuple[AdjustmentProposal, ...] = ()
    proposed_user_facts: tuple[Mapping[str, object], ...] = ()
    chapter_statements: tuple[ChapterStatementProposal, ...] = ()


class ReflectionModel(Protocol):
    def reflect(self, request: ReflectionRequest) -> ReflectionProposal:
        ...


def validate_reflection_proposal(
    request: ReflectionRequest,
    proposal: ReflectionProposal,
) -> None:
    if proposal.schema_version != REFLECTION_PROPOSAL_VERSION:
        raise ReflectionValidationError("reflection proposal schema is invalid")
    if not isinstance(proposal.safe_summary, str) or not proposal.safe_summary.strip():
        raise ReflectionValidationError("reflection safe_summary must be text")
    if any(
        not isinstance(evidence_id, str) or not evidence_id.strip()
        for evidence_id in proposal.evidence_ids
    ):
        raise ReflectionValidationError("proposal Evidence IDs are invalid")
    if len(set(proposal.evidence_ids)) != len(proposal.evidence_ids):
        raise ReflectionValidationError("proposal Evidence IDs are duplicated")
    if request.job_kind == "memory_candidate_extraction":
        _validate_memory_candidate_proposal(request, proposal)
        return
    if request.turn_sources:
        raise ReflectionValidationError(
            "turn sources are only valid for memory candidate extraction"
        )
    allowed_evidence_ids = {item.evidence_id for item in request.evidence}
    evidence_ownership = {
        item.evidence_id: item.ownership_scope for item in request.evidence
    }
    referenced_ids = set(proposal.evidence_ids)
    adjustment_keys: set[tuple[str, str]] = set()
    for adjustment in proposal.adjustments:
        if adjustment.dimension not in ALLOWED_ADJUSTMENT_DIMENSIONS:
            raise ReflectionValidationError("adjustment dimension is invalid")
        if not isinstance(adjustment.value, str) or not adjustment.value.strip():
            raise ReflectionValidationError("adjustment value is invalid")
        if adjustment.value not in ALLOWED_ADJUSTMENT_VALUES[adjustment.dimension]:
            raise ReflectionValidationError("adjustment value is invalid")
        if adjustment.scope not in ALLOWED_ADJUSTMENT_SCOPES:
            raise ReflectionValidationError("adjustment scope is invalid")
        if not adjustment.evidence_ids or any(
            not isinstance(evidence_id, str) or not evidence_id.strip()
            for evidence_id in adjustment.evidence_ids
        ):
            raise ReflectionValidationError("adjustment Evidence IDs are invalid")
        if len(set(adjustment.evidence_ids)) != len(adjustment.evidence_ids):
            raise ReflectionValidationError("adjustment Evidence IDs are duplicated")
        if not 0.0 <= adjustment.confidence <= 1.0:
            raise ReflectionValidationError("adjustment confidence is invalid")
        adjustment_key = (adjustment.dimension, adjustment.scope)
        if adjustment_key in adjustment_keys:
            raise ReflectionValidationError(
                "duplicate adjustment dimension and scope"
            )
        adjustment_keys.add(adjustment_key)
        referenced_ids.update(adjustment.evidence_ids)
    chapter_evidence_ids: set[str] = set()
    has_shared_experience = False
    for statement in proposal.chapter_statements:
        if statement.claim_scope not in {"user_fact", "shared_experience"}:
            raise ReflectionValidationError("chapter claim scope is invalid")
        if not statement.evidence_ids or any(
            not isinstance(evidence_id, str) or not evidence_id.strip()
            for evidence_id in statement.evidence_ids
        ):
            raise ReflectionValidationError("chapter statement Evidence IDs are invalid")
        if len(set(statement.evidence_ids)) != len(statement.evidence_ids):
            raise ReflectionValidationError(
                "chapter statement Evidence IDs are duplicated"
            )
        expected_ownership = (
            "user" if statement.claim_scope == "user_fact" else "relationship"
        )
        if any(
            evidence_ownership.get(evidence_id) != expected_ownership
            for evidence_id in statement.evidence_ids
        ):
            raise ReflectionValidationError(
                "chapter statement ownership does not match claim scope"
            )
        if chapter_evidence_ids.intersection(statement.evidence_ids):
            raise ReflectionValidationError(
                "chapter Evidence cannot be reused across statements"
            )
        chapter_evidence_ids.update(statement.evidence_ids)
        has_shared_experience = has_shared_experience or (
            statement.claim_scope == "shared_experience"
        )
    if request.job_kind == "academic_stage_changed":
        if not proposal.chapter_statements or not has_shared_experience:
            raise ReflectionValidationError(
                "chapter requires structured shared-experience statements"
            )
        if chapter_evidence_ids != set(proposal.evidence_ids):
            raise ReflectionValidationError(
                "chapter statements must account for all chapter Evidence"
            )
    elif proposal.chapter_statements:
        raise ReflectionValidationError(
            "chapter statements are only valid for academic stage jobs"
        )
    referenced_ids.update(chapter_evidence_ids)
    if not referenced_ids <= allowed_evidence_ids:
        raise ReflectionValidationError("proposal references unavailable Evidence")
    if proposal.proposed_user_facts:
        raise ReflectionValidationError(
            "reflection cannot create user facts without deterministic attribution"
        )


def _validate_memory_candidate_proposal(
    request: ReflectionRequest,
    proposal: ReflectionProposal,
) -> None:
    if request.evidence or proposal.evidence_ids or proposal.adjustments:
        raise ReflectionValidationError(
            "memory candidate extraction cannot consume or adjust Evidence"
        )
    if proposal.chapter_statements:
        raise ReflectionValidationError(
            "memory candidate extraction cannot create chapter statements"
        )
    sources = {item.turn_id: item.text for item in request.turn_sources}
    if not sources:
        raise ReflectionValidationError("memory candidate source is unavailable")
    if len(proposal.proposed_user_facts) > 5:
        raise ReflectionValidationError("memory candidate count exceeds limit")
    fact_keys: set[str] = set()
    for candidate in proposal.proposed_user_facts:
        if set(candidate) != MEMORY_CANDIDATE_KEYS:
            raise ReflectionValidationError("memory candidate shape is invalid")
        fact_key = candidate["fact_key"]
        kind = candidate["kind"]
        value = candidate["value"]
        source_turn_id = candidate["source_turn_id"]
        source_quote = candidate["source_quote"]
        claim_type = candidate["claim_type"]
        sensitivity = candidate["sensitivity"]
        confidence = candidate["confidence"]
        if (
            not isinstance(fact_key, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{1,31}:[a-z0-9_:-]{2,80}", fact_key)
            is None
        ):
            raise ReflectionValidationError("memory candidate fact_key is invalid")
        if fact_key in fact_keys:
            raise ReflectionValidationError("memory candidate fact_key is duplicated")
        fact_keys.add(fact_key)
        if kind not in MEMORY_CANDIDATE_KINDS:
            raise ReflectionValidationError("memory candidate kind is invalid")
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 200
        ):
            raise ReflectionValidationError("memory candidate value is invalid")
        if not isinstance(source_turn_id, str) or source_turn_id not in sources:
            raise ReflectionValidationError("memory candidate source turn is invalid")
        if (
            not isinstance(source_quote, str)
            or not source_quote.strip()
            or len(source_quote) > 400
            or source_quote not in sources[source_turn_id]
        ):
            raise ReflectionValidationError("memory candidate quote is not in source")
        if value.strip() not in source_quote:
            raise ReflectionValidationError("memory candidate value is not supported by quote")
        if claim_type not in MEMORY_CANDIDATE_CLAIM_TYPES:
            raise ReflectionValidationError("memory candidate claim type is invalid")
        if sensitivity not in {"low", "private", "sensitive"}:
            raise ReflectionValidationError("memory candidate sensitivity is invalid")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            raise ReflectionValidationError("memory candidate confidence is invalid")
