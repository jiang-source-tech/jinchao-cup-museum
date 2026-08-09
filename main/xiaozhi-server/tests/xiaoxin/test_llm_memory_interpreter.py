import json
import threading

import pytest

from core.xiaoxin.companion import (
    CompanionSubjectContext,
    MEMORY_INTERPRETATION_REQUEST_VERSION,
    MEMORY_INTERPRETATION_RESULT_VERSION,
    MemoryInterpretationError,
    MemoryInterpretationRequest,
    MemorySource,
)
from core.xiaoxin.companion.adapters import LLMMemoryInterpretationModel
from core.xiaoxin.companion.semantic_memory import (
    MEMORY_CLAIM_TYPES,
    MEMORY_KINDS,
    MEMORY_SENSITIVITIES,
    MEMORY_SUBJECT_SCOPES,
    MEMORY_TEMPORAL_SCOPES,
)


class StaticAdapter:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[tuple[list[dict], dict]] = []

    def complete_chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.response


class SequenceAdapter(StaticAdapter):
    def __init__(self, responses: list[object]) -> None:
        super().__init__(responses[0])
        self.responses = iter(responses)

    def complete_chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return next(self.responses)


def _request() -> MemoryInterpretationRequest:
    return MemoryInterpretationRequest(
        request_id="adapter-request",
        subject=CompanionSubjectContext(
            owner_user_id="owner-1",
            pet_id="pet-1",
            memory_subject_id="subject-1",
            speaker_identity="confirmed",
            academic_stage="sophomore",
            persistence_allowed=True,
        ),
        current_turn_id="turn-1",
        sources=(
            MemorySource(
                turn_id="turn-1",
                role="user",
                text="图书馆三楼待着舒服多了。",
                occurred_at="2026-07-21T10:00:00+08:00",
            ),
        ),
        schema_version=MEMORY_INTERPRETATION_REQUEST_VERSION,
    )


def _valid_payload() -> dict:
    return {
        "schema_version": MEMORY_INTERPRETATION_RESULT_VERSION,
        "proposals": [
            {
                "fact_key": "preference:study_environment",
                "kind": "preference",
                "canonical_value": "偏好安静的图书馆学习环境",
                "source_quotes": [
                    {
                        "turn_id": "turn-1",
                        "quote": "图书馆三楼待着舒服多了",
                    }
                ],
                "claim_type": "explicit_statement",
                "temporal_scope": "stable",
                "sensitivity": "low",
                "subject_scope": "self",
                "confidence": 0.94,
                "reason_code": "natural_study_preference",
                "memory_action": "create",
                "target_evidence_id": None,
                "valid_until": None,
            }
        ],
    }


def test_llm_memory_adapter_parses_strict_json_and_keeps_canonical_value() -> None:
    adapter = StaticAdapter(json.dumps(_valid_payload(), ensure_ascii=False))

    result = LLMMemoryInterpretationModel(adapter).interpret(_request())

    assert result.proposals[0].canonical_value == "偏好安静的图书馆学习环境"
    assert result.proposals[0].memory_action == "create"
    assert adapter.calls[0][1] == {
        "max_tokens": 1000,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    assert MEMORY_INTERPRETATION_RESULT_VERSION in adapter.calls[0][0][0]["content"]
    assert json.loads(adapter.calls[0][0][1]["content"])["sources"][0][
        "role"
    ] == "user"


def test_llm_memory_adapter_prompt_lists_every_strict_proposal_constraint() -> None:
    adapter = StaticAdapter(json.dumps(_valid_payload(), ensure_ascii=False))

    LLMMemoryInterpretationModel(adapter).interpret(_request())

    system_prompt = adapter.calls[0][0][0]["content"]
    for allowed_values in (
        MEMORY_CLAIM_TYPES,
        MEMORY_TEMPORAL_SCOPES,
        MEMORY_KINDS,
        MEMORY_SENSITIVITIES,
        MEMORY_SUBJECT_SCOPES,
    ):
        for value in allowed_values:
            assert value in system_prompt
    assert "[a-z][a-z0-9_]{1,63}" in system_prompt
    assert "momentary" in system_prompt and "晚于所有引用来源时间" in system_prompt
    assert "stable" in system_prompt and "valid_until=null" in system_prompt
    assert "0.0 到 1.0（含边界）的 JSON 数字" in system_prompt
    assert "不能使用字符串、百分数或中文描述" in system_prompt
    assert "preference:task_start_strategy" in system_prompt
    assert "target_evidence_id" in system_prompt


def test_llm_memory_adapter_rejects_markdown_or_non_json_output() -> None:
    adapter = StaticAdapter("```json\n{}\n```")

    with pytest.raises(MemoryInterpretationError, match="invalid JSON"):
        LLMMemoryInterpretationModel(adapter).interpret(_request())


def test_llm_memory_adapter_repairs_an_invalid_relation_target() -> None:
    invalid = _valid_payload()
    invalid["proposals"][0]["memory_action"] = "replace"
    invalid["proposals"][0]["target_evidence_id"] = "missing-evidence"
    adapter = SequenceAdapter(
        [
            json.dumps(invalid, ensure_ascii=False),
            json.dumps(_valid_payload(), ensure_ascii=False),
        ]
    )

    result = LLMMemoryInterpretationModel(adapter).interpret(_request())

    assert result.proposals[0].memory_action == "create"
    assert len(adapter.calls) == 2
    repair_request = json.loads(adapter.calls[1][0][-1]["content"])
    assert repair_request["validation_error"] == "invalid_memory_contract"


def test_llm_memory_adapter_drops_an_unrepairable_relation_target() -> None:
    invalid = _valid_payload()
    invalid["proposals"][0]["memory_action"] = "replace"
    invalid["proposals"][0]["target_evidence_id"] = "missing-evidence"
    adapter = SequenceAdapter(
        [
            json.dumps(invalid, ensure_ascii=False),
            json.dumps(invalid, ensure_ascii=False),
        ]
    )

    result = LLMMemoryInterpretationModel(adapter).interpret(_request())

    assert result.proposals == ()
    assert len(adapter.calls) == 2


def test_llm_memory_adapter_rejects_unapproved_proposal_fields() -> None:
    payload = _valid_payload()
    payload["proposals"][0]["chain_of_thought"] = "private reasoning"

    with pytest.raises(MemoryInterpretationError, match="shape is invalid"):
        LLMMemoryInterpretationModel(
            StaticAdapter(json.dumps(payload, ensure_ascii=False))
        ).interpret(_request())


def test_llm_memory_adapter_times_out_without_waiting_for_model_completion() -> None:
    blocker = threading.Event()

    class BlockingAdapter:
        def complete_chat(self, messages, **kwargs):
            blocker.wait(1)
            return json.dumps(_valid_payload(), ensure_ascii=False)

    try:
        with pytest.raises(TimeoutError, match="timed out"):
            LLMMemoryInterpretationModel(
                BlockingAdapter(), timeout_seconds=0.01
            ).interpret(_request())
    finally:
        blocker.set()
