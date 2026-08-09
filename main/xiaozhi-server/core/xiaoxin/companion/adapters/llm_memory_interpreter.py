from __future__ import annotations

import json

from ..model_harness import StructuredJsonHarness, StructuredOutputError
from ..prompt_specs import memory_interpretation_prompt

from ..semantic_memory import (
    MEMORY_CLAIM_TYPES,
    MEMORY_INTERPRETATION_RESULT_VERSION,
    MEMORY_KINDS,
    MEMORY_SENSITIVITIES,
    MEMORY_SUBJECT_SCOPES,
    MEMORY_TEMPORAL_SCOPES,
    MemoryInterpretationError,
    MemoryInterpretationRequest,
    MemoryInterpretationResult,
    MemoryProposal,
    MemorySourceQuote,
    _drop_unresolvable_relation_proposals,
    _normalize_proposal_validity,
    _validate_interpretation,
)


_PROPOSAL_KEYS = {
    "fact_key",
    "kind",
    "canonical_value",
    "source_quotes",
    "claim_type",
    "temporal_scope",
    "sensitivity",
    "subject_scope",
    "confidence",
    "reason_code",
    "memory_action",
    "target_evidence_id",
    "valid_until",
}


class LLMMemoryInterpretationModel:
    """Strict structured adapter for semantic user-fact interpretation."""

    def __init__(
        self,
        adapter: object,
        *,
        timeout_seconds: float = 20.0,
        audit_sink=None,
    ) -> None:
        self._harness = StructuredJsonHarness(adapter, audit_sink=audit_sink)
        self._spec = memory_interpretation_prompt(
            timeout_seconds=max(float(timeout_seconds), 0.001)
        )

    @property
    def model_name(self) -> str:
        return self._harness.model_name

    @property
    def prompt_version(self) -> str:
        return self._spec.prompt_version

    @property
    def prompt_hash(self) -> str:
        return self._spec.prompt_hash

    def interpret(
        self, request: MemoryInterpretationRequest
    ) -> MemoryInterpretationResult:
        payload = {
            "schema_version": request.schema_version,
            "request_id": request.request_id,
            "current_turn_id": request.current_turn_id,
            "sources": [
                {
                    "turn_id": item.turn_id,
                    "role": item.role,
                    "text": item.text,
                    "occurred_at": item.occurred_at,
                    "asr_reliability": item.asr_reliability,
                }
                for item in request.sources
            ],
            "existing_facts": [
                {
                    "evidence_id": item.evidence_id,
                    "fact_key": item.fact_key,
                    "kind": item.kind,
                    "canonical_value": item.canonical_value,
                    "sensitivity": item.sensitivity,
                    "occurred_at": item.occurred_at,
                }
                for item in request.existing_facts
            ],
        }
        try:
            validation_attempts = 0

            def validate(result: MemoryInterpretationResult) -> MemoryInterpretationResult:
                nonlocal validation_attempts
                validation_attempts += 1
                try:
                    normalized = _normalize_proposal_validity(request, result)
                    _validate_interpretation(request, normalized)
                    return normalized
                except MemoryInterpretationError as exc:
                    if (
                        validation_attempts >= 2
                        and str(exc) == "memory relation proposal target is invalid"
                    ):
                        normalized = _drop_unresolvable_relation_proposals(
                            request,
                            result,
                        )
                        normalized = _normalize_proposal_validity(
                            request,
                            normalized,
                        )
                        _validate_interpretation(request, normalized)
                        return normalized
                    raise StructuredOutputError(
                        "invalid_memory_contract",
                        str(exc),
                    ) from exc

            return self._harness.complete(
                spec=self._spec,
                user_payload=payload,
                parser=_parse_result,
                validator=validate,
                correlation={"request_id": request.request_id},
            ).value
        except StructuredOutputError as exc:
            raise MemoryInterpretationError(str(exc)) from exc


def _parse_result(raw: object) -> MemoryInterpretationResult:
    if not isinstance(raw, str):
        raise StructuredOutputError(
            "response_not_text", "memory interpretation output must be JSON"
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(
            "invalid_json", "memory interpretation output is invalid JSON"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "proposals",
    }:
        raise StructuredOutputError(
            "invalid_top_level_shape", "memory interpretation shape is invalid"
        )
    if not isinstance(payload["schema_version"], str):
        raise StructuredOutputError(
            "invalid_schema_version_type", "memory schema_version must be text"
        )
    proposals = payload["proposals"]
    if not isinstance(proposals, list):
        raise StructuredOutputError("invalid_proposals_type", "memory proposals must be a list")
    parsed = []
    for item in proposals:
        if not isinstance(item, dict) or set(item) != _PROPOSAL_KEYS:
            raise StructuredOutputError(
                "invalid_proposal_shape", "memory proposal shape is invalid"
            )
        for field_name in (
            "fact_key",
            "kind",
            "canonical_value",
            "claim_type",
            "temporal_scope",
            "sensitivity",
            "subject_scope",
            "reason_code",
            "memory_action",
        ):
            if not isinstance(item[field_name], str):
                raise StructuredOutputError(
                    "invalid_proposal_field_type",
                    f"memory proposal {field_name} must be text",
                )
        confidence = item["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise StructuredOutputError(
                "invalid_confidence_type", "memory confidence must be numeric"
            )
        if item["valid_until"] is not None and not isinstance(
            item["valid_until"], str
        ):
            raise StructuredOutputError(
                "invalid_valid_until_type", "memory valid_until must be text or null"
            )
        if item["target_evidence_id"] is not None and not isinstance(
            item["target_evidence_id"], str
        ):
            raise StructuredOutputError(
                "invalid_target_evidence_id_type",
                "memory target evidence id must be text or null",
            )
        quotes = item["source_quotes"]
        if not isinstance(quotes, list) or any(
            not isinstance(quote, dict) or set(quote) != {"turn_id", "quote"}
            for quote in quotes
        ):
            raise StructuredOutputError(
                "invalid_source_quote_shape", "memory source quote shape is invalid"
            )
        if any(
            not isinstance(quote["turn_id"], str)
            or not isinstance(quote["quote"], str)
            for quote in quotes
        ):
            raise StructuredOutputError(
                "invalid_source_quote_type", "memory source quote fields must be text"
            )
        parsed.append(
            MemoryProposal(
                fact_key=item["fact_key"],
                kind=item["kind"],
                canonical_value=item["canonical_value"],
                source_quotes=tuple(
                    MemorySourceQuote(
                        turn_id=quote["turn_id"],
                        quote=quote["quote"],
                    )
                    for quote in quotes
                ),
                claim_type=item["claim_type"],
                temporal_scope=item["temporal_scope"],
                sensitivity=item["sensitivity"],
                subject_scope=item["subject_scope"],
                confidence=item["confidence"],
                reason_code=item["reason_code"],
                memory_action=item["memory_action"],
                target_evidence_id=item["target_evidence_id"],
                valid_until=item["valid_until"],
            )
        )
    return MemoryInterpretationResult(
        schema_version=payload["schema_version"],
        proposals=tuple(parsed),
    )
