from __future__ import annotations

import json

from ..model_harness import StructuredJsonHarness, StructuredOutputError
from ..prompt_specs import reflection_prompt

from ..reflection import (
    ALLOWED_ADJUSTMENT_SCOPES,
    ALLOWED_ADJUSTMENT_VALUES,
    MEMORY_CANDIDATE_CLAIM_TYPES,
    MEMORY_CANDIDATE_KEYS,
    MEMORY_CANDIDATE_KINDS,
    AdjustmentProposal,
    ChapterStatementProposal,
    ReflectionProposal,
    ReflectionRequest,
    ReflectionValidationError,
    validate_reflection_proposal,
)


_ALLOWED_PROPOSAL_KEYS = {
    "schema_version",
    "safe_summary",
    "evidence_ids",
    "adjustments",
    "proposed_user_facts",
    "chapter_statements",
}
_REQUIRED_PROPOSAL_KEYS = _ALLOWED_PROPOSAL_KEYS
_ALLOWED_ADJUSTMENT_KEYS = {
    "dimension",
    "value",
    "scope",
    "evidence_ids",
    "confidence",
}
_ALLOWED_CHAPTER_STATEMENT_KEYS = {"claim_scope", "evidence_ids"}


class LLMReflectionModel:
    """Strict remote-model adapter; it never owns or writes a Store."""

    def __init__(
        self,
        adapter: object,
        *,
        timeout_seconds: float = 20.0,
        audit_sink=None,
    ) -> None:
        self._timeout_seconds = max(float(timeout_seconds), 0.001)
        self._harness = StructuredJsonHarness(adapter, audit_sink=audit_sink)

    @property
    def model_name(self) -> str:
        return self._harness.model_name

    def prompt_version_for(self, request: ReflectionRequest) -> str:
        return reflection_prompt(timeout_seconds=self._timeout_seconds).prompt_version

    def reflect(self, request: ReflectionRequest) -> ReflectionProposal:
        spec = reflection_prompt(timeout_seconds=self._timeout_seconds)
        payload = {
            "schema_version": request.schema_version,
            "job_id": request.job_id,
            "job_kind": request.job_kind,
            "pet_id": request.pet_id,
            "relationship_epoch_id": request.relationship_epoch_id,
            "evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "kind": item.kind,
                    "ownership_scope": item.ownership_scope,
                    "source_summary": item.source_summary,
                    "confidence": item.confidence,
                }
                for item in request.evidence
            ],
            "turn_sources": [
                {
                    "turn_id": item.turn_id,
                    "text": item.text,
                    "occurred_at": item.occurred_at,
                }
                for item in request.turn_sources
            ],
        }
        try:
            return self._harness.complete(
                spec=spec,
                user_payload=payload,
                parser=_parse_proposal,
                validator=lambda proposal: _validate_proposal(request, proposal),
                correlation={"job_id": request.job_id, "pet_id": request.pet_id},
            ).value
        except StructuredOutputError as exc:
            raise ReflectionValidationError(str(exc)) from exc


def _validate_proposal(
    request: ReflectionRequest, proposal: ReflectionProposal
) -> ReflectionProposal:
    validate_reflection_proposal(request, proposal)
    return proposal


def _parse_proposal(raw: object) -> ReflectionProposal:
    if not isinstance(raw, str):
        raise StructuredOutputError("response_not_text", "reflection output must be JSON text")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError("invalid_json", "reflection output is not valid JSON") from exc
    if not isinstance(payload, dict) or not set(payload) <= _ALLOWED_PROPOSAL_KEYS:
        raise StructuredOutputError("unexpected_fields", "reflection output has unexpected fields")
    if not _REQUIRED_PROPOSAL_KEYS <= set(payload):
        raise StructuredOutputError("missing_fields", "reflection output is missing required fields")
    evidence_ids = _text_tuple(payload["evidence_ids"], "evidence_ids")
    raw_adjustments = payload["adjustments"]
    if not isinstance(raw_adjustments, list):
        raise StructuredOutputError("invalid_adjustments_type", "adjustments must be a list")
    adjustments = []
    for item in raw_adjustments:
        if not isinstance(item, dict) or set(item) != _ALLOWED_ADJUSTMENT_KEYS:
            raise StructuredOutputError("invalid_adjustment_shape", "adjustment shape is invalid")
        confidence = item["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise StructuredOutputError("invalid_confidence_type", "adjustment confidence is invalid")
        adjustments.append(
            AdjustmentProposal(
                dimension=_text(item["dimension"], "dimension"),
                value=_text(item["value"], "value"),
                scope=_text(item["scope"], "scope"),
                evidence_ids=_text_tuple(item["evidence_ids"], "evidence_ids"),
                confidence=float(confidence),
            )
        )
    proposed_user_facts = payload["proposed_user_facts"]
    if not isinstance(proposed_user_facts, list) or any(
        not isinstance(item, dict) for item in proposed_user_facts
    ):
        raise StructuredOutputError("invalid_facts_type", "proposed_user_facts must be object list")
    for item in proposed_user_facts:
        if set(item) != MEMORY_CANDIDATE_KEYS:
            raise StructuredOutputError("invalid_candidate_shape", "memory candidate shape is invalid")
        for field_name in MEMORY_CANDIDATE_KEYS - {"confidence"}:
            if not isinstance(item[field_name], str):
                raise StructuredOutputError(
                    "invalid_candidate_field_type",
                    f"memory candidate {field_name} must be text",
                )
        confidence = item["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise StructuredOutputError("invalid_confidence_type", "memory candidate confidence is invalid")
    raw_chapter_statements = payload["chapter_statements"]
    if not isinstance(raw_chapter_statements, list):
        raise StructuredOutputError("invalid_chapters_type", "chapter_statements must be a list")
    chapter_statements = []
    for item in raw_chapter_statements:
        if (
            not isinstance(item, dict)
            or set(item) != _ALLOWED_CHAPTER_STATEMENT_KEYS
        ):
            raise StructuredOutputError("invalid_chapter_shape", "chapter statement shape is invalid")
        chapter_statements.append(
            ChapterStatementProposal(
                claim_scope=_text(item["claim_scope"], "claim_scope"),
                evidence_ids=_text_tuple(item["evidence_ids"], "evidence_ids"),
            )
        )
    return ReflectionProposal(
        schema_version=_text(payload["schema_version"], "schema_version"),
        safe_summary=_text(payload["safe_summary"], "safe_summary", allow_empty=True),
        evidence_ids=evidence_ids,
        adjustments=tuple(adjustments),
        proposed_user_facts=tuple(proposed_user_facts),
        chapter_statements=tuple(chapter_statements),
    )


def _text(value: object, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise StructuredOutputError("invalid_text_type", f"{name} must be text")
    return value


def _text_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise StructuredOutputError("invalid_list_type", f"{name} must be a list")
    result = tuple(_text(item, name) for item in value)
    return result
