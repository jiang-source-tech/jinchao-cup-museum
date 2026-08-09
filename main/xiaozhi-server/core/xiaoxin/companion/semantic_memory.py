from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import hashlib
import re
from typing import Literal, Protocol

from .contracts import (
    CompanionSubjectContext,
    InteractionKind,
    ProjectionSurface,
)


MEMORY_INTERPRETATION_REQUEST_VERSION = "companion-memory-interpretation-request-v1"
MEMORY_INTERPRETATION_RESULT_VERSION = "companion-memory-interpretation-result-v2"
MEMORY_INTERPRETATION_MAX_EXISTING_FACTS = 32
MEMORY_RECALL_REQUEST_VERSION = "companion-memory-recall-request-v1"
MEMORY_RECALL_PLAN_VERSION = "companion-memory-recall-plan-v1"
MEMORY_CLAIM_TYPES = frozenset(
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
MEMORY_TEMPORAL_SCOPES = frozenset({"momentary", "episode", "stable"})
MEMORY_KINDS = frozenset(
    {
        "profile",
        "goal",
        "preference",
        "interest",
        "life_event",
        "relationship_context",
        "wellbeing",
    }
)
MEMORY_SENSITIVITIES = frozenset({"low", "private", "sensitive"})
MEMORY_SUBJECT_SCOPES = frozenset({"self", "third_party", "unknown"})
MEMORY_ACTIONS = frozenset(
    {"create", "reinforce", "replace", "coexist", "temporary_override"}
)
MEMORY_RECALL_KINDS = MEMORY_KINDS | frozenset(
    {
        "profile_fact",
        "explicit_preference",
        "boundary",
        "explicit_boundary",
        "user_life_event",
        "goal_completed",
        "future_event",
        "recent_conversation",
    }
)
_FACT_KEY_PATTERN = re.compile(r"[a-z][a-z0-9_]{1,31}:[a-z0-9_:-]{2,80}")
_REASON_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]{1,63}")
_LEGACY_FACT_KEY_CANONICAL = {
    "origin": "profile:origin",
    "preferred_name": "profile:preferred_name",
    "academic_stage": "profile:academic_stage",
    "preference:planning_habit": "preference:task_start_strategy",
    "preference:focus_on_key_step": "preference:task_start_strategy",
    "preference:working_style": "preference:task_start_strategy",
}
_AUTHORIZED_CROSS_KEY_REPLACEMENTS = frozenset(
    {
        (
            "preference:task_start_strategy",
            "preference:emotional_support_style",
        ),
    }
)


def memory_fact_replacement_is_authorized(
    source_fact_key: str,
    target_fact_key: str,
) -> bool:
    return (source_fact_key, target_fact_key) in _AUTHORIZED_CROSS_KEY_REPLACEMENTS


def canonical_memory_fact_key(fact_key: str, *, kind: str) -> str:
    canonical = _LEGACY_FACT_KEY_CANONICAL.get(fact_key, fact_key)
    if _FACT_KEY_PATTERN.fullmatch(canonical) is not None:
        return canonical
    digest = hashlib.sha256(f"{kind}\x1f{fact_key}".encode("utf-8")).hexdigest()[:16]
    return f"legacy:{digest}"


def memory_fact_key_storage_aliases(fact_key: str) -> tuple[str, ...]:
    aliases = [fact_key]
    aliases.extend(
        legacy
        for legacy, canonical in _LEGACY_FACT_KEY_CANONICAL.items()
        if canonical == fact_key
    )
    return tuple(aliases)


@dataclass(frozen=True)
class MemorySource:
    turn_id: str
    role: Literal["user", "assistant"]
    text: str
    occurred_at: str
    asr_reliability: Literal["reliable", "uncertain", "unknown"] = "unknown"


@dataclass(frozen=True)
class MemorySourceQuote:
    turn_id: str
    quote: str


@dataclass(frozen=True)
class MemoryProposal:
    fact_key: str
    kind: str
    canonical_value: str
    source_quotes: tuple[MemorySourceQuote, ...]
    claim_type: str
    temporal_scope: str
    sensitivity: str
    subject_scope: Literal["self", "third_party", "unknown"]
    confidence: float
    reason_code: str
    memory_action: Literal[
        "create", "reinforce", "replace", "coexist", "temporary_override"
    ] = "create"
    target_evidence_id: str | None = None
    valid_until: str | None = None


@dataclass(frozen=True)
class MemoryExistingFact:
    evidence_id: str
    fact_key: str
    kind: str
    canonical_value: str
    sensitivity: str
    occurred_at: str


@dataclass(frozen=True)
class MemoryInterpretationRequest:
    request_id: str
    subject: CompanionSubjectContext
    current_turn_id: str
    sources: tuple[MemorySource, ...]
    existing_facts: tuple[MemoryExistingFact, ...] = ()
    schema_version: str = MEMORY_INTERPRETATION_REQUEST_VERSION


@dataclass(frozen=True)
class MemoryInterpretationResult:
    schema_version: str
    proposals: tuple[MemoryProposal, ...] = ()


@dataclass(frozen=True)
class MemoryRecallRequest:
    request_id: str
    subject: CompanionSubjectContext
    interaction_kind: InteractionKind
    surface: ProjectionSurface
    query: str
    memory_reference_budget: int
    requested_fact_keys: tuple[str, ...] = ()
    requested_kinds: tuple[str, ...] = ()
    exclude_sensitivities: tuple[str, ...] = ()
    occurred_after: str | None = None
    occurred_before: str | None = None
    schema_version: str = MEMORY_RECALL_REQUEST_VERSION


@dataclass(frozen=True)
class MemoryRecallPlan:
    schema_version: str
    should_recall: bool
    reason_code: str
    query: str
    fact_keys: tuple[str, ...]
    kinds: tuple[str, ...]
    exclude_sensitivities: tuple[str, ...]
    occurred_after: str | None
    occurred_before: str | None
    limit: int


@dataclass(frozen=True)
class MemoryWriteDecision:
    action: Literal["drop", "shadow", "candidate", "active", "reinforce"]
    reason_code: str


class MemoryWritePolicy:
    """Deterministically decide whether a validated proposal may persist."""

    def decide(
        self,
        proposal: MemoryProposal,
        *,
        mode: str,
        existing_facts: tuple[MemoryExistingFact, ...] = (),
        explicit_correction: bool = False,
        explicit_memory_request: bool = False,
    ) -> MemoryWriteDecision:
        if mode == "shadow":
            return MemoryWriteDecision("shadow", "shadow_mode")
        if proposal.subject_scope != "self":
            return MemoryWriteDecision("drop", "non_self_claim")
        if proposal.claim_type in {
            "reported_speech",
            "hypothetical",
            "negated",
            "dream",
            "joke",
        }:
            return MemoryWriteDecision("drop", f"unsafe_{proposal.claim_type}")
        if proposal.memory_action == "reinforce":
            if (
                mode != "active_explicit"
                or proposal.claim_type != "explicit_statement"
                or proposal.sensitivity == "sensitive"
            ):
                return MemoryWriteDecision("drop", "reinforcement_not_active")
            return MemoryWriteDecision("reinforce", "semantic_reinforcement")
        if memory_proposal_is_naturally_persistent(proposal):
            if mode != "active_explicit":
                return MemoryWriteDecision("candidate", "candidate_mode")
            return MemoryWriteDecision(
                "active",
                "explicit_current_primary_focus",
            )
        if proposal.memory_action == "replace":
            if mode != "active_explicit":
                return MemoryWriteDecision("candidate", "candidate_mode")
            if (
                proposal.claim_type == "explicit_statement"
                and proposal.sensitivity != "sensitive"
                and proposal.temporal_scope == "stable"
            ):
                return MemoryWriteDecision("active", "semantic_replacement")
            return MemoryWriteDecision(
                "candidate", "replacement_confirmation_required"
            )
        if proposal.memory_action == "temporary_override":
            if mode != "active_explicit":
                return MemoryWriteDecision("candidate", "candidate_mode")
            if (
                proposal.claim_type == "explicit_statement"
                and proposal.sensitivity != "sensitive"
                and proposal.temporal_scope == "momentary"
                and proposal.valid_until is not None
            ):
                return MemoryWriteDecision("active", "semantic_temporary_override")
            return MemoryWriteDecision(
                "candidate", "temporary_override_confirmation_required"
            )
        conflicts = any(
            item.fact_key == proposal.fact_key
            and item.canonical_value != proposal.canonical_value
            for item in existing_facts
        ) and proposal.memory_action != "coexist"
        if (
            conflicts
            and explicit_correction
            and proposal.claim_type == "explicit_statement"
            and proposal.sensitivity != "sensitive"
            and proposal.temporal_scope == "stable"
        ):
            return MemoryWriteDecision("active", "explicit_fact_correction")
        if mode != "active_explicit":
            return MemoryWriteDecision("candidate", "candidate_mode")
        if conflicts:
            return MemoryWriteDecision("candidate", "conflicting_fact")
        if "infer" in proposal.reason_code:
            return MemoryWriteDecision(
                "candidate",
                "inference_confirmation_required",
            )
        if (
            explicit_memory_request
            and proposal.claim_type == "explicit_statement"
            and proposal.sensitivity != "sensitive"
            and (
                proposal.temporal_scope in {"episode", "stable"}
                or (
                    proposal.temporal_scope == "momentary"
                    and proposal.valid_until is not None
                )
            )
        ):
            return MemoryWriteDecision("active", "explicit_memory_request")
        if (
            proposal.claim_type == "explicit_statement"
            and proposal.sensitivity == "low"
            and proposal.temporal_scope == "stable"
        ):
            return MemoryWriteDecision("active", "explicit_low_risk_fact")
        return MemoryWriteDecision("candidate", "confirmation_required")


def memory_proposal_is_naturally_persistent(proposal: MemoryProposal) -> bool:
    return (
        proposal.fact_key == "goal:current_primary_focus"
        and proposal.kind == "goal"
        and proposal.claim_type == "explicit_statement"
        and proposal.subject_scope == "self"
        and proposal.sensitivity != "sensitive"
        and proposal.temporal_scope in {"episode", "stable"}
    )


class MemoryInterpretationModel(Protocol):
    def interpret(
        self, request: MemoryInterpretationRequest
    ) -> MemoryInterpretationResult: ...


class MemoryInterpretationError(ValueError):
    """The interpretation request or model result violates the memory contract."""


class MemoryRecallPlanningError(ValueError):
    """An untrusted memory-tool request violates the recall contract."""


class MemoryInterpreter:
    """Validate semantic memory proposals without performing persistence."""

    def __init__(self, model: MemoryInterpretationModel) -> None:
        self._model = model

    @property
    def model_name(self) -> str:
        value = getattr(self._model, "model_name", None)
        return value if isinstance(value, str) and value else type(self._model).__name__

    @property
    def prompt_version(self) -> str | None:
        value = getattr(self._model, "prompt_version", None)
        return value if isinstance(value, str) and value else None

    def interpret(
        self, request: MemoryInterpretationRequest
    ) -> MemoryInterpretationResult:
        _validate_request(request)
        result = self._model.interpret(request)
        result = _normalize_proposal_validity(request, result)
        _validate_interpretation(request, result)
        return result


def _drop_unresolvable_relation_proposals(
    request: MemoryInterpretationRequest,
    result: MemoryInterpretationResult,
) -> MemoryInterpretationResult:
    """Discard relation proposals whose target cannot be proven from the request."""

    existing_ids = {item.evidence_id for item in request.existing_facts}
    proposals = tuple(
        proposal
        for proposal in result.proposals
        if proposal.memory_action == "create"
        or (
            isinstance(proposal.target_evidence_id, str)
            and proposal.target_evidence_id in existing_ids
        )
    )
    return replace(result, proposals=proposals)


def _normalize_proposal_validity(
    request: MemoryInterpretationRequest,
    result: MemoryInterpretationResult,
) -> MemoryInterpretationResult:
    """Canonicalize semantic slots and make temporal retention deterministic."""

    sources = {source.turn_id: source for source in request.sources}
    existing_by_id = {item.evidence_id: item for item in request.existing_facts}
    normalized: list[MemoryProposal] = []
    for proposal in result.proposals:
        fact_key = _LEGACY_FACT_KEY_CANONICAL.get(
            proposal.fact_key,
            proposal.fact_key,
        )
        target = (
            existing_by_id.get(proposal.target_evidence_id)
            if proposal.target_evidence_id is not None
            else None
        )
        if target is not None and proposal.memory_action in {
            "reinforce",
            "replace",
            "temporary_override",
        }:
            target_fact_key = canonical_memory_fact_key(
                target.fact_key,
                kind=target.kind,
            )
            if (
                proposal.memory_action != "replace"
                or not memory_fact_replacement_is_authorized(
                    target_fact_key,
                    fact_key,
                )
            ):
                fact_key = target_fact_key
        proposal = replace(proposal, fact_key=fact_key)
        if proposal.temporal_scope in {"episode", "stable"}:
            normalized.append(replace(proposal, valid_until=None))
            continue
        if proposal.temporal_scope != "momentary" or not proposal.source_quotes:
            normalized.append(proposal)
            continue
        quoted_sources = [sources.get(item.turn_id) for item in proposal.source_quotes]
        if any(source is None for source in quoted_sources):
            normalized.append(proposal)
            continue
        latest_source = max(
            _aware_datetime(source.occurred_at)
            for source in quoted_sources
            if source is not None
        )
        normalized.append(
            replace(
                proposal,
                valid_until=(latest_source + timedelta(days=1)).isoformat(),
            )
        )
    return replace(result, proposals=tuple(normalized))


class MemoryRecallPlanner:
    """Turn an LLM memory-tool request into a bounded deterministic plan."""

    def plan(self, request: MemoryRecallRequest) -> MemoryRecallPlan:
        _validate_recall_request(request)
        if (
            request.subject.speaker_identity != "confirmed"
            or not request.subject.persistence_allowed
        ):
            return _disabled_recall_plan(request, "subject_not_eligible")
        if request.interaction_kind == "general_qa":
            return _disabled_recall_plan(request, "interaction_not_eligible")
        if request.memory_reference_budget <= 0:
            return _disabled_recall_plan(request, "memory_budget_exhausted")
        excluded = request.exclude_sensitivities
        if request.surface == "initiative" and "sensitive" not in excluded:
            excluded = (*excluded, "sensitive")
        return MemoryRecallPlan(
            schema_version=MEMORY_RECALL_PLAN_VERSION,
            should_recall=True,
            reason_code="semantic_tool_request",
            query=request.query,
            fact_keys=request.requested_fact_keys,
            kinds=request.requested_kinds,
            exclude_sensitivities=excluded,
            occurred_after=request.occurred_after,
            occurred_before=request.occurred_before,
            limit=min(request.memory_reference_budget, 3),
        )


def _validate_recall_request(request: MemoryRecallRequest) -> None:
    if request.schema_version != MEMORY_RECALL_REQUEST_VERSION:
        raise MemoryRecallPlanningError("memory recall request schema is invalid")
    if (
        not isinstance(request.query, str)
        or not request.query.strip()
        or len(request.query) > 500
    ):
        raise MemoryRecallPlanningError("memory recall query is invalid")
    if isinstance(request.memory_reference_budget, bool) or not isinstance(
        request.memory_reference_budget, int
    ):
        raise MemoryRecallPlanningError("memory recall budget is invalid")
    if len(request.requested_fact_keys) > 8:
        raise MemoryRecallPlanningError("memory recall allows at most eight fact keys")
    if len(request.requested_kinds) > 8:
        raise MemoryRecallPlanningError("memory recall allows at most eight kinds")
    if len(request.exclude_sensitivities) > 3:
        raise MemoryRecallPlanningError(
            "memory recall allows at most three excluded sensitivities"
        )
    for fact_key in request.requested_fact_keys:
        if (
            not isinstance(fact_key, str)
            or _FACT_KEY_PATTERN.fullmatch(fact_key) is None
        ):
            raise MemoryRecallPlanningError("memory recall fact key is invalid")
    for kind in request.requested_kinds:
        if kind not in MEMORY_RECALL_KINDS:
            raise MemoryRecallPlanningError("memory recall kind is invalid")
    for sensitivity in request.exclude_sensitivities:
        if sensitivity not in MEMORY_SENSITIVITIES:
            raise MemoryRecallPlanningError("memory recall sensitivity is invalid")
    occurred_after = (
        _recall_datetime(request.occurred_after)
        if request.occurred_after is not None
        else None
    )
    occurred_before = (
        _recall_datetime(request.occurred_before)
        if request.occurred_before is not None
        else None
    )
    if (
        occurred_after is not None
        and occurred_before is not None
        and occurred_after > occurred_before
    ):
        raise MemoryRecallPlanningError("memory recall time range is invalid")


def _recall_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise MemoryRecallPlanningError("memory recall time is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MemoryRecallPlanningError("memory recall time must include a timezone")
    return parsed


def _disabled_recall_plan(
    request: MemoryRecallRequest,
    reason_code: str,
) -> MemoryRecallPlan:
    return MemoryRecallPlan(
        schema_version=MEMORY_RECALL_PLAN_VERSION,
        should_recall=False,
        reason_code=reason_code,
        query=request.query,
        fact_keys=(),
        kinds=(),
        exclude_sensitivities=(),
        occurred_after=None,
        occurred_before=None,
        limit=0,
    )


def _validate_request(request: MemoryInterpretationRequest) -> None:
    if request.schema_version != MEMORY_INTERPRETATION_REQUEST_VERSION:
        raise MemoryInterpretationError(
            "memory interpretation request schema is invalid"
        )
    if not isinstance(request.request_id, str) or not request.request_id.strip():
        raise MemoryInterpretationError("memory interpretation request ID is invalid")
    if (
        not isinstance(request.current_turn_id, str)
        or not request.current_turn_id.strip()
    ):
        raise MemoryInterpretationError(
            "memory interpretation current turn ID is invalid"
        )
    if (
        request.subject.speaker_identity != "confirmed"
        or not request.subject.persistence_allowed
    ):
        raise MemoryInterpretationError(
            "memory interpretation requires a confirmed persistent subject"
        )
    if len(request.sources) > 6:
        raise MemoryInterpretationError(
            "memory interpretation context allows at most six messages"
        )
    if len(request.existing_facts) > MEMORY_INTERPRETATION_MAX_EXISTING_FACTS:
        raise MemoryInterpretationError(
            "memory interpretation allows at most thirty-two existing facts"
        )
    existing_ids: set[str] = set()
    for existing in request.existing_facts:
        if (
            not isinstance(existing.evidence_id, str)
            or not existing.evidence_id.strip()
            or len(existing.evidence_id) > 128
        ):
            raise MemoryInterpretationError("memory existing evidence ID is invalid")
        if existing.evidence_id in existing_ids:
            raise MemoryInterpretationError(
                "memory existing evidence IDs are duplicated"
            )
        existing_ids.add(existing.evidence_id)
        if (
            not isinstance(existing.fact_key, str)
            or _FACT_KEY_PATTERN.fullmatch(existing.fact_key) is None
        ):
            raise MemoryInterpretationError("memory existing fact key is invalid")
        if existing.kind not in MEMORY_RECALL_KINDS:
            raise MemoryInterpretationError("memory existing fact kind is invalid")
        if (
            not isinstance(existing.canonical_value, str)
            or not existing.canonical_value.strip()
            or len(existing.canonical_value) > 200
        ):
            raise MemoryInterpretationError(
                "memory existing canonical value is invalid"
            )
        if existing.sensitivity not in MEMORY_SENSITIVITIES:
            raise MemoryInterpretationError("memory existing sensitivity is invalid")
        _aware_datetime(existing.occurred_at)
    for source in request.sources:
        if not isinstance(source.turn_id, str) or not source.turn_id.strip():
            raise MemoryInterpretationError("memory source turn ID is invalid")
        if source.role not in {"user", "assistant"}:
            raise MemoryInterpretationError("memory source role is invalid")
        if not isinstance(source.text, str) or not source.text.strip():
            raise MemoryInterpretationError("memory source text is invalid")
    if sum(len(source.text) for source in request.sources) > 3000:
        raise MemoryInterpretationError(
            "memory interpretation context allows at most 3000 characters"
        )
    turn_ids = [source.turn_id for source in request.sources]
    if len(set(turn_ids)) != len(turn_ids):
        raise MemoryInterpretationError(
            "memory interpretation source turn IDs are duplicated"
        )
    current_source = next(
        (
            source
            for source in request.sources
            if source.turn_id == request.current_turn_id
        ),
        None,
    )
    if current_source is None or current_source.role != "user":
        raise MemoryInterpretationError(
            "memory interpretation current turn must be a user source"
        )
    occurred_at = tuple(
        _aware_datetime(source.occurred_at) for source in request.sources
    )
    if any(
        source.asr_reliability not in {"reliable", "uncertain", "unknown"}
        for source in request.sources
    ):
        raise MemoryInterpretationError("memory source ASR reliability is invalid")
    if occurred_at and max(occurred_at) - min(occurred_at) > timedelta(minutes=30):
        raise MemoryInterpretationError(
            "memory interpretation context must stay within thirty minutes"
        )


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise MemoryInterpretationError(
            "memory source occurred_at must be an ISO-8601 datetime"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MemoryInterpretationError(
            "memory source occurred_at must include a timezone"
        )
    return parsed


def _validate_interpretation(
    request: MemoryInterpretationRequest,
    result: MemoryInterpretationResult,
) -> None:
    if result.schema_version != MEMORY_INTERPRETATION_RESULT_VERSION:
        raise MemoryInterpretationError("memory interpretation schema is invalid")
    if len(result.proposals) > 5:
        raise MemoryInterpretationError(
            "memory interpretation allows at most five proposals"
        )
    fact_keys = [proposal.fact_key for proposal in result.proposals]
    if len(set(fact_keys)) != len(fact_keys):
        raise MemoryInterpretationError("memory proposal fact keys are duplicated")
    sources = {source.turn_id: source for source in request.sources}
    existing_by_id = {item.evidence_id: item for item in request.existing_facts}
    for proposal in result.proposals:
        if (
            not isinstance(proposal.fact_key, str)
            or _FACT_KEY_PATTERN.fullmatch(proposal.fact_key) is None
        ):
            raise MemoryInterpretationError("memory proposal fact key is invalid")
        if proposal.kind not in MEMORY_KINDS:
            raise MemoryInterpretationError("memory proposal kind is invalid")
        if (
            not isinstance(proposal.canonical_value, str)
            or not proposal.canonical_value.strip()
            or len(proposal.canonical_value) > 200
        ):
            raise MemoryInterpretationError(
                "memory proposal canonical value is invalid"
            )
        if proposal.claim_type not in MEMORY_CLAIM_TYPES:
            raise MemoryInterpretationError("memory proposal claim type is invalid")
        if proposal.temporal_scope not in MEMORY_TEMPORAL_SCOPES:
            raise MemoryInterpretationError("memory proposal temporal scope is invalid")
        if proposal.temporal_scope == "momentary" and proposal.valid_until is None:
            raise MemoryInterpretationError(
                "memory momentary proposal requires valid_until"
            )
        if proposal.sensitivity not in MEMORY_SENSITIVITIES:
            raise MemoryInterpretationError("memory proposal sensitivity is invalid")
        if proposal.subject_scope not in MEMORY_SUBJECT_SCOPES:
            raise MemoryInterpretationError("memory proposal subject scope is invalid")
        if (
            not isinstance(proposal.memory_action, str)
            or proposal.memory_action not in MEMORY_ACTIONS
        ):
            raise MemoryInterpretationError("memory proposal action is invalid")
        if proposal.memory_action == "create":
            if proposal.target_evidence_id is not None:
                raise MemoryInterpretationError(
                    "memory create proposal cannot target existing evidence"
                )
        else:
            if (
                not isinstance(proposal.target_evidence_id, str)
                or proposal.target_evidence_id not in existing_by_id
            ):
                raise MemoryInterpretationError(
                    "memory relation proposal target is invalid"
                )
            target = existing_by_id[proposal.target_evidence_id]
            if target.kind != proposal.kind:
                raise MemoryInterpretationError(
                    "memory relation proposal target kind does not match"
                )
        if proposal.memory_action == "replace" and not (
            (
                proposal.claim_type == "explicit_statement"
                and proposal.subject_scope == "self"
                and proposal.temporal_scope == "stable"
            )
            or memory_proposal_is_naturally_persistent(proposal)
        ):
            raise MemoryInterpretationError(
                "memory replacement requires an explicit stable self statement"
            )
        if proposal.memory_action == "temporary_override" and (
            proposal.temporal_scope != "momentary" or proposal.valid_until is None
        ):
            raise MemoryInterpretationError(
                "memory temporary override requires momentary validity"
            )
        if (
            isinstance(proposal.confidence, bool)
            or not isinstance(proposal.confidence, (int, float))
            or not 0.0 <= float(proposal.confidence) <= 1.0
        ):
            raise MemoryInterpretationError("memory proposal confidence is invalid")
        if (
            not isinstance(proposal.reason_code, str)
            or _REASON_CODE_PATTERN.fullmatch(proposal.reason_code) is None
        ):
            raise MemoryInterpretationError("memory proposal reason code is invalid")
        if not proposal.source_quotes:
            raise MemoryInterpretationError("memory proposal requires a user quote")
        unique_quotes = {
            (source_quote.turn_id, source_quote.quote)
            for source_quote in proposal.source_quotes
        }
        if len(proposal.source_quotes) > 3 or len(unique_quotes) != len(
            proposal.source_quotes
        ):
            raise MemoryInterpretationError(
                "memory proposal allows at most three unique user quotes"
            )
        for source_quote in proposal.source_quotes:
            source = sources.get(source_quote.turn_id)
            if source is None or source.role != "user":
                raise MemoryInterpretationError(
                    "memory proposal quote must reference a user source"
                )
            if not source_quote.quote.strip() or source_quote.quote not in source.text:
                raise MemoryInterpretationError(
                    "memory proposal quote is not present in its user source"
                )
        if (
            any(
                sources[item.turn_id].asr_reliability == "uncertain"
                for item in proposal.source_quotes
            )
            and proposal.claim_type != "asr_uncertain"
        ):
            raise MemoryInterpretationError(
                "memory proposal from uncertain ASR must keep asr_uncertain claim type"
            )
        if proposal.valid_until is not None:
            valid_until = _aware_datetime(proposal.valid_until)
            latest_source = max(
                _aware_datetime(sources[item.turn_id].occurred_at)
                for item in proposal.source_quotes
            )
            if valid_until <= latest_source:
                raise MemoryInterpretationError(
                    "memory proposal valid_until must be after its source"
                )
