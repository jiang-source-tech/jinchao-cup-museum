import asyncio
from dataclasses import replace
import json

import pytest

from core.xiaoxin.companion import (
    CompanionMind,
    CompanionControlCommand,
    CompanionProjectionRequest,
    CompanionSubjectContext,
    CompanionTurnOutcome,
    CompanionTurnRequest,
    MEMORY_INTERPRETATION_RESULT_VERSION,
    MemoryExistingFact,
    MemoryInterpretationRequest,
    MemoryInterpretationResult,
    MemoryInterpretationError,
    MemoryInterpreter,
    MemoryProposal,
    MemoryRecallPlanner,
    MemoryRecallPlanningError,
    MemoryRecallRequest,
    MemorySource,
    MemorySourceQuote,
    MemoryWritePolicy,
)
from core.xiaoxin.companion.adapters import LLMMemoryInterpretationModel
from core.xiaoxin.companion.store import CompanionStore


class StaticInterpretationModel:
    def __init__(self, result: MemoryInterpretationResult) -> None:
        self.result = result
        self.requests: list[MemoryInterpretationRequest] = []

    def interpret(
        self, request: MemoryInterpretationRequest
    ) -> MemoryInterpretationResult:
        self.requests.append(request)
        return self.result


class InvalidJsonMemoryAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def complete_chat(self, messages, **kwargs):
        self.calls += 1
        return "not-json"


class MultiTurnCandidateModel:
    def __init__(self) -> None:
        self.requests: list[MemoryInterpretationRequest] = []

    def interpret(
        self, request: MemoryInterpretationRequest
    ) -> MemoryInterpretationResult:
        self.requests.append(request)
        if request.current_turn_id != "turn-semantic-2":
            return MemoryInterpretationResult(
                schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
            )
        return MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
            proposals=(
                MemoryProposal(
                    fact_key="goal:english_cet6",
                    kind="goal",
                    canonical_value="正在准备英语六级，当前主要困难是听力",
                    source_quotes=(
                        MemorySourceQuote(
                            turn_id="turn-semantic-1",
                            quote="我最近一直在准备六级",
                        ),
                        MemorySourceQuote(
                            turn_id="turn-semantic-2",
                            quote="还是听力最让我头疼",
                        ),
                    ),
                    claim_type="explicit_statement",
                    temporal_scope="episode",
                    sensitivity="private",
                    subject_scope="self",
                    confidence=0.95,
                    reason_code="explicit_goal_with_followup_detail",
                ),
            ),
        )


def _confirmed_subject() -> CompanionSubjectContext:
    return CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )


def _valid_contract_request() -> MemoryInterpretationRequest:
    return MemoryInterpretationRequest(
        request_id="interpret-contract",
        subject=_confirmed_subject(),
        current_turn_id="turn-1",
        sources=(
            MemorySource(
                turn_id="turn-1",
                role="user",
                text="我最近在准备六级。",
                occurred_at="2026-07-21T10:00:00+08:00",
            ),
        ),
    )


def _valid_contract_proposal() -> MemoryProposal:
    return MemoryProposal(
        fact_key="goal:english_cet6",
        kind="goal",
        canonical_value="正在准备英语六级",
        source_quotes=(
            MemorySourceQuote(
                turn_id="turn-1",
                quote="我最近在准备六级",
            ),
        ),
        claim_type="explicit_statement",
        temporal_scope="episode",
        sensitivity="private",
        subject_scope="self",
        confidence=0.93,
        reason_code="explicit_goal",
    )


def _valid_recall_request() -> MemoryRecallRequest:
    return MemoryRecallRequest(
        request_id="recall-contract",
        subject=_confirmed_subject(),
        interaction_kind="explicit_recall",
        surface="voice",
        query="上次让我紧张的那个考试是什么？",
        memory_reference_budget=2,
    )


@pytest.mark.parametrize(
    ("changes", "mode", "expected_action", "expected_reason"),
    (
        ({"subject_scope": "third_party"}, "active_explicit", "drop", "non_self_claim"),
        (
            {"claim_type": "hypothetical"},
            "active_explicit",
            "drop",
            "unsafe_hypothetical",
        ),
        ({"claim_type": "negated"}, "active_explicit", "drop", "unsafe_negated"),
        ({"claim_type": "joke"}, "active_explicit", "drop", "unsafe_joke"),
        ({"claim_type": "dream"}, "active_explicit", "drop", "unsafe_dream"),
        (
            {"sensitivity": "sensitive"},
            "active_explicit",
            "candidate",
            "confirmation_required",
        ),
        ({}, "candidate", "candidate", "candidate_mode"),
        ({}, "shadow", "shadow", "shadow_mode"),
        ({}, "active_explicit", "active", "explicit_low_risk_fact"),
    ),
)
def test_memory_write_policy_enforces_release_and_attribution_gates(
    changes: dict[str, object],
    mode: str,
    expected_action: str,
    expected_reason: str,
) -> None:
    base_proposal = replace(
        _valid_contract_proposal(),
        temporal_scope="stable",
        sensitivity="low",
    )
    proposal = replace(base_proposal, **changes)

    decision = MemoryWritePolicy().decide(proposal, mode=mode)

    assert (decision.action, decision.reason_code) == (
        expected_action,
        expected_reason,
    )


def test_memory_write_policy_keeps_conflicting_explicit_fact_as_candidate() -> None:
    proposal = replace(
        _valid_contract_proposal(),
        temporal_scope="stable",
        sensitivity="low",
    )
    existing = MemoryExistingFact(
        evidence_id="existing-goal",
        fact_key=proposal.fact_key,
        kind=proposal.kind,
        canonical_value="已经放弃英语六级",
        sensitivity="low",
        occurred_at="2026-07-20T10:00:00+08:00",
    )

    decision = MemoryWritePolicy().decide(
        proposal,
        mode="active_explicit",
        existing_facts=(existing,),
    )

    assert (decision.action, decision.reason_code) == (
        "candidate",
        "conflicting_fact",
    )


def test_memory_write_policy_does_not_activate_a_stable_fact_inferred_from_one_event() -> (
    None
):
    proposal = replace(
        _valid_contract_proposal(),
        fact_key="preference:response_to_setbacks",
        kind="preference",
        canonical_value="遇到考试失利会选择重做错题而不是逃避",
        temporal_scope="stable",
        sensitivity="low",
        reason_code="inferred_from_statement",
    )

    decision = MemoryWritePolicy().decide(
        proposal,
        mode="active_explicit",
    )

    assert (decision.action, decision.reason_code) == (
        "candidate",
        "inference_confirmation_required",
    )


def test_memory_write_policy_activates_confirmed_explicit_correction() -> None:
    proposal = replace(
        _valid_contract_proposal(),
        temporal_scope="stable",
        sensitivity="private",
        reason_code="explicit_correction",
    )
    existing = MemoryExistingFact(
        evidence_id="existing-goal",
        fact_key=proposal.fact_key,
        kind=proposal.kind,
        canonical_value="已经放弃英语六级",
        sensitivity="private",
        occurred_at="2026-07-20T10:00:00+08:00",
    )

    decision = MemoryWritePolicy().decide(
        proposal,
        mode="candidate",
        existing_facts=(existing,),
        explicit_correction=True,
    )

    assert (decision.action, decision.reason_code) == (
        "active",
        "explicit_fact_correction",
    )


def test_explicit_memory_request_activates_only_safe_current_episode() -> None:
    proposal = replace(
        _valid_contract_proposal(),
        temporal_scope="episode",
        sensitivity="private",
    )

    allowed = MemoryWritePolicy().decide(
        proposal,
        mode="active_explicit",
        explicit_memory_request=True,
    )
    sensitive = MemoryWritePolicy().decide(
        replace(proposal, sensitivity="sensitive"),
        mode="active_explicit",
        explicit_memory_request=True,
    )
    expiring_plan = MemoryWritePolicy().decide(
        replace(
            proposal,
            temporal_scope="momentary",
            valid_until="2026-07-28T23:59:59+08:00",
        ),
        mode="active_explicit",
        explicit_memory_request=True,
    )

    assert (allowed.action, allowed.reason_code) == (
        "active",
        "explicit_memory_request",
    )
    assert sensitive.action == "candidate"
    assert (expiring_plan.action, expiring_plan.reason_code) == (
        "active",
        "explicit_memory_request",
    )


def test_natural_expression_can_propose_a_normalized_user_fact() -> None:
    request = MemoryInterpretationRequest(
        request_id="interpret-natural-preference",
        subject=_confirmed_subject(),
        current_turn_id="turn-1",
        sources=(
            MemorySource(
                turn_id="turn-1",
                role="user",
                text="图书馆三楼待着舒服多了，宿舍总有人讲话。",
                occurred_at="2026-07-21T10:00:00+08:00",
            ),
        ),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
            proposals=(
                MemoryProposal(
                    fact_key="preference:study_environment",
                    kind="preference",
                    canonical_value="偏好安静的学习环境",
                    source_quotes=(
                        MemorySourceQuote(
                            turn_id="turn-1",
                            quote="图书馆三楼待着舒服多了",
                        ),
                    ),
                    claim_type="explicit_statement",
                    temporal_scope="stable",
                    sensitivity="private",
                    subject_scope="self",
                    confidence=0.93,
                    reason_code="explicit_environment_preference",
                ),
            ),
        )
    )

    result = MemoryInterpreter(model).interpret(request)

    assert result.proposals[0].canonical_value == "偏好安静的学习环境"
    assert result.proposals[0].canonical_value not in request.sources[0].text
    assert model.requests == [request]


def test_explicit_correction_can_target_an_existing_fact_key() -> None:
    request = MemoryInterpretationRequest(
        request_id="interpret-explicit-correction",
        subject=_confirmed_subject(),
        current_turn_id="turn-2",
        sources=(
            MemorySource(
                turn_id="turn-2",
                role="user",
                text="我现在不喜欢咖啡了。",
                occurred_at="2026-07-21T10:00:00+08:00",
            ),
        ),
        existing_facts=(
            MemoryExistingFact(
                evidence_id="evidence-coffee-like",
                fact_key="preference:coffee",
                kind="preference",
                canonical_value="喜欢咖啡",
                sensitivity="private",
                occurred_at="2026-07-01T10:00:00+08:00",
            ),
        ),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
            proposals=(
                MemoryProposal(
                    fact_key="preference:coffee",
                    kind="preference",
                    canonical_value="不喜欢咖啡",
                    source_quotes=(
                        MemorySourceQuote(
                            turn_id="turn-2",
                            quote="我现在不喜欢咖啡了",
                        ),
                    ),
                    claim_type="explicit_statement",
                    temporal_scope="stable",
                    sensitivity="private",
                    subject_scope="self",
                    confidence=0.97,
                    reason_code="explicit_preference_correction",
                ),
            ),
        )
    )

    result = MemoryInterpreter(model).interpret(request)

    assert result.proposals[0].fact_key == request.existing_facts[0].fact_key
    assert result.proposals[0].canonical_value == "不喜欢咖啡"


def test_interpreter_accepts_at_most_thirty_two_current_conflict_facts() -> None:
    existing = MemoryExistingFact(
        evidence_id="evidence-0",
        fact_key="preference:item_0",
        kind="preference",
        canonical_value="偏好项目0",
        sensitivity="private",
        occurred_at="2026-07-01T10:00:00+08:00",
    )
    request = replace(
        _valid_contract_request(),
        existing_facts=tuple(
            replace(
                existing,
                evidence_id=f"evidence-{index}",
                fact_key=f"preference:item_{index}",
            )
            for index in range(32)
        ),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
        )
    )

    MemoryInterpreter(model).interpret(request)

    assert model.requests == [request]

    too_many_request = replace(
        request,
        existing_facts=request.existing_facts
        + (
            replace(
                existing,
                evidence_id="evidence-32",
                fact_key="preference:item_32",
            ),
        ),
    )
    with pytest.raises(
        MemoryInterpretationError,
        match="at most thirty-two existing facts",
    ):
        MemoryInterpreter(model).interpret(too_many_request)

    assert model.requests == [request]


def test_existing_conflict_facts_are_validated_before_the_model_call() -> None:
    existing = MemoryExistingFact(
        evidence_id="evidence-invalid",
        fact_key="Not a key",
        kind="preference",
        canonical_value="喜欢咖啡",
        sensitivity="private",
        occurred_at="2026-07-01T10:00:00+08:00",
    )
    request = replace(
        _valid_contract_request(),
        existing_facts=(existing,),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
        )
    )

    with pytest.raises(
        MemoryInterpretationError,
        match="existing fact key is invalid",
    ):
        MemoryInterpreter(model).interpret(request)

    assert model.requests == []


def test_interpreter_accepts_legacy_boundary_existing_fact() -> None:
    request = replace(
        _valid_contract_request(),
        existing_facts=(
            MemoryExistingFact(
                evidence_id="legacy-boundary",
                fact_key="boundary:initiative_frequency",
                kind="boundary",
                canonical_value="once a day",
                sensitivity="private",
                occurred_at="2026-07-21T09:00:00+08:00",
            ),
        ),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
        )
    )

    result = MemoryInterpreter(model).interpret(request)

    assert result.proposals == ()
    assert model.requests == [request]


def test_followup_can_reference_an_earlier_user_turn_without_using_assistant_as_fact() -> (
    None
):
    request = MemoryInterpretationRequest(
        request_id="interpret-multi-turn-goal",
        subject=_confirmed_subject(),
        current_turn_id="turn-3",
        sources=(
            MemorySource(
                turn_id="turn-1",
                role="user",
                text="我最近一直在准备六级。",
                occurred_at="2026-07-21T10:00:00+08:00",
            ),
            MemorySource(
                turn_id="turn-2",
                role="assistant",
                text="准备得怎么样？",
                occurred_at="2026-07-21T10:00:03+08:00",
            ),
            MemorySource(
                turn_id="turn-3",
                role="user",
                text="还是听力最让我头疼。",
                occurred_at="2026-07-21T10:00:06+08:00",
            ),
        ),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
            proposals=(
                MemoryProposal(
                    fact_key="goal:english_cet6",
                    kind="goal",
                    canonical_value="正在准备英语六级，当前主要困难是听力",
                    source_quotes=(
                        MemorySourceQuote(
                            turn_id="turn-1",
                            quote="我最近一直在准备六级",
                        ),
                        MemorySourceQuote(
                            turn_id="turn-3",
                            quote="还是听力最让我头疼",
                        ),
                    ),
                    claim_type="explicit_statement",
                    temporal_scope="episode",
                    sensitivity="private",
                    subject_scope="self",
                    confidence=0.94,
                    reason_code="explicit_goal_with_followup_detail",
                ),
            ),
        )
    )

    result = MemoryInterpreter(model).interpret(request)

    assert tuple(
        source_quote.turn_id for source_quote in result.proposals[0].source_quotes
    ) == ("turn-1", "turn-3")


def test_assistant_words_cannot_be_used_as_user_fact_evidence() -> None:
    request = MemoryInterpretationRequest(
        request_id="interpret-assistant-attribution",
        subject=_confirmed_subject(),
        current_turn_id="turn-2",
        sources=(
            MemorySource(
                turn_id="turn-1",
                role="assistant",
                text="你是不是更喜欢一个人学习？",
                occurred_at="2026-07-21T10:00:00+08:00",
            ),
            MemorySource(
                turn_id="turn-2",
                role="user",
                text="也许吧。",
                occurred_at="2026-07-21T10:00:03+08:00",
            ),
        ),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
            proposals=(
                MemoryProposal(
                    fact_key="preference:study_alone",
                    kind="preference",
                    canonical_value="喜欢独自学习",
                    source_quotes=(
                        MemorySourceQuote(
                            turn_id="turn-1",
                            quote="更喜欢一个人学习",
                        ),
                    ),
                    claim_type="inference",
                    temporal_scope="stable",
                    sensitivity="private",
                    subject_scope="self",
                    confidence=0.61,
                    reason_code="assistant_led_inference",
                ),
            ),
        )
    )

    with pytest.raises(
        MemoryInterpretationError,
        match="quote must reference a user source",
    ):
        MemoryInterpreter(model).interpret(request)


def test_context_over_six_messages_is_rejected_before_calling_the_model() -> None:
    request = MemoryInterpretationRequest(
        request_id="interpret-context-limit",
        subject=_confirmed_subject(),
        current_turn_id="turn-7",
        sources=tuple(
            MemorySource(
                turn_id=f"turn-{index}",
                role="user" if index % 2 else "assistant",
                text=f"message-{index}",
                occurred_at=f"2026-07-21T10:00:0{index}+08:00",
            )
            for index in range(1, 8)
        ),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
        )
    )

    with pytest.raises(
        MemoryInterpretationError,
        match="at most six messages",
    ):
        MemoryInterpreter(model).interpret(request)

    assert model.requests == []


def test_context_turn_ids_must_be_unique() -> None:
    source = _valid_contract_request().sources[0]
    request = replace(
        _valid_contract_request(),
        sources=(source, replace(source, text="还是听力最让我头疼。")),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
        )
    )

    with pytest.raises(
        MemoryInterpretationError,
        match="turn IDs are duplicated",
    ):
        MemoryInterpreter(model).interpret(request)

    assert model.requests == []


def test_context_rejects_roles_other_than_user_and_assistant() -> None:
    request = replace(
        _valid_contract_request(),
        sources=(
            MemorySource(
                turn_id="turn-0",
                role="system",
                text="用户喜欢安静学习。",
                occurred_at="2026-07-21T09:59:59+08:00",
            ),
            _valid_contract_request().sources[0],
        ),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
        )
    )

    with pytest.raises(
        MemoryInterpretationError,
        match="source role is invalid",
    ):
        MemoryInterpreter(model).interpret(request)

    assert model.requests == []


def test_unknown_speaker_never_reaches_the_interpretation_model() -> None:
    request = replace(
        _valid_contract_request(),
        subject=replace(
            _confirmed_subject(),
            speaker_identity="unknown",
            persistence_allowed=False,
        ),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
        )
    )

    with pytest.raises(
        MemoryInterpretationError,
        match="requires a confirmed persistent subject",
    ):
        MemoryInterpreter(model).interpret(request)

    assert model.requests == []


def test_current_turn_must_be_a_user_source() -> None:
    request = MemoryInterpretationRequest(
        request_id="interpret-current-user",
        subject=_confirmed_subject(),
        current_turn_id="turn-2",
        sources=(
            MemorySource(
                turn_id="turn-1",
                role="user",
                text="我最近在准备六级。",
                occurred_at="2026-07-21T10:00:00+08:00",
            ),
            MemorySource(
                turn_id="turn-2",
                role="assistant",
                text="准备得怎么样？",
                occurred_at="2026-07-21T10:00:03+08:00",
            ),
        ),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
        )
    )

    with pytest.raises(
        MemoryInterpretationError,
        match="current turn must be a user source",
    ):
        MemoryInterpreter(model).interpret(request)

    assert model.requests == []


def test_context_over_three_thousand_characters_is_rejected() -> None:
    request = MemoryInterpretationRequest(
        request_id="interpret-context-text-limit",
        subject=_confirmed_subject(),
        current_turn_id="turn-1",
        sources=(
            MemorySource(
                turn_id="turn-1",
                role="user",
                text="想" * 3001,
                occurred_at="2026-07-21T10:00:00+08:00",
            ),
        ),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
        )
    )

    with pytest.raises(
        MemoryInterpretationError,
        match="at most 3000 characters",
    ):
        MemoryInterpreter(model).interpret(request)

    assert model.requests == []


def test_context_older_than_thirty_minutes_is_rejected() -> None:
    request = MemoryInterpretationRequest(
        request_id="interpret-context-time-limit",
        subject=_confirmed_subject(),
        current_turn_id="turn-2",
        sources=(
            MemorySource(
                turn_id="turn-1",
                role="user",
                text="我最近一直在准备六级。",
                occurred_at="2026-07-21T09:29:59+08:00",
            ),
            MemorySource(
                turn_id="turn-2",
                role="user",
                text="还是听力最让我头疼。",
                occurred_at="2026-07-21T10:00:00+08:00",
            ),
        ),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
        )
    )

    with pytest.raises(
        MemoryInterpretationError,
        match="within thirty minutes",
    ):
        MemoryInterpreter(model).interpret(request)

    assert model.requests == []


def test_model_cannot_return_more_than_five_memory_proposals() -> None:
    request = MemoryInterpretationRequest(
        request_id="interpret-proposal-limit",
        subject=_confirmed_subject(),
        current_turn_id="turn-1",
        sources=(
            MemorySource(
                turn_id="turn-1",
                role="user",
                text="我最近在准备六级。",
                occurred_at="2026-07-21T10:00:00+08:00",
            ),
        ),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
            proposals=tuple(
                MemoryProposal(
                    fact_key=f"goal:english_cet6_{index}",
                    kind="goal",
                    canonical_value="正在准备英语六级",
                    source_quotes=(
                        MemorySourceQuote(
                            turn_id="turn-1",
                            quote="我最近在准备六级",
                        ),
                    ),
                    claim_type="explicit_statement",
                    temporal_scope="episode",
                    sensitivity="private",
                    subject_scope="self",
                    confidence=0.9,
                    reason_code="explicit_goal",
                )
                for index in range(6)
            ),
        )
    )

    with pytest.raises(
        MemoryInterpretationError,
        match="at most five proposals",
    ):
        MemoryInterpreter(model).interpret(request)


def test_model_cannot_invent_an_unknown_claim_type() -> None:
    request = MemoryInterpretationRequest(
        request_id="interpret-claim-type",
        subject=_confirmed_subject(),
        current_turn_id="turn-1",
        sources=(
            MemorySource(
                turn_id="turn-1",
                role="user",
                text="假如我去考研，也许会选计算机。",
                occurred_at="2026-07-21T10:00:00+08:00",
            ),
        ),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
            proposals=(
                MemoryProposal(
                    fact_key="goal:graduate_computer_science",
                    kind="goal",
                    canonical_value="计划报考计算机研究生",
                    source_quotes=(
                        MemorySourceQuote(
                            turn_id="turn-1",
                            quote="假如我去考研，也许会选计算机",
                        ),
                    ),
                    claim_type="certain_fact",
                    temporal_scope="episode",
                    sensitivity="private",
                    subject_scope="self",
                    confidence=0.7,
                    reason_code="unsupported_claim_type",
                ),
            ),
        )
    )

    with pytest.raises(
        MemoryInterpretationError,
        match="claim type is invalid",
    ):
        MemoryInterpreter(model).interpret(request)


def test_momentary_state_gets_a_server_controlled_expiration_time() -> None:
    request = MemoryInterpretationRequest(
        request_id="interpret-momentary-expiry",
        subject=_confirmed_subject(),
        current_turn_id="turn-1",
        sources=(
            MemorySource(
                turn_id="turn-1",
                role="user",
                text="我今天有点累。",
                occurred_at="2026-07-21T10:00:00+08:00",
            ),
        ),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
            proposals=(
                MemoryProposal(
                    fact_key="wellbeing:temporary_tiredness",
                    kind="wellbeing",
                    canonical_value="今天感到疲惫",
                    source_quotes=(
                        MemorySourceQuote(
                            turn_id="turn-1",
                            quote="我今天有点累",
                        ),
                    ),
                    claim_type="explicit_statement",
                    temporal_scope="momentary",
                    sensitivity="sensitive",
                    subject_scope="self",
                    confidence=0.92,
                    reason_code="explicit_temporary_state",
                ),
            ),
        )
    )

    result = MemoryInterpreter(model).interpret(request)

    assert result.proposals[0].valid_until == "2026-07-22T10:00:00+08:00"


def test_model_expiration_is_replaced_by_server_controlled_expiration() -> None:
    proposal = replace(
        _valid_contract_proposal(),
        fact_key="wellbeing:temporary_tiredness",
        kind="wellbeing",
        canonical_value="今天感到疲惫",
        temporal_scope="momentary",
        sensitivity="sensitive",
        valid_until="2026-07-21T09:59:59+08:00",
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
            proposals=(proposal,),
        )
    )

    result = MemoryInterpreter(model).interpret(_valid_contract_request())

    assert result.proposals[0].valid_until == "2026-07-22T10:00:00+08:00"


def test_episode_model_expiration_is_removed_by_server_policy() -> None:
    proposal = replace(
        _valid_contract_proposal(),
        valid_until="2026-07-21T09:59:59+08:00",
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
            proposals=(proposal,),
        )
    )

    result = MemoryInterpreter(model).interpret(_valid_contract_request())

    assert result.proposals[0].valid_until is None


@pytest.mark.parametrize(
    ("proposal", "message"),
    (
        (
            replace(_valid_contract_proposal(), fact_key="Not a key"),
            "fact key is invalid",
        ),
        (
            replace(_valid_contract_proposal(), kind="personality_label"),
            "kind is invalid",
        ),
        (
            replace(_valid_contract_proposal(), canonical_value=""),
            "canonical value is invalid",
        ),
        (
            replace(_valid_contract_proposal(), sensitivity="public"),
            "sensitivity is invalid",
        ),
        (
            replace(_valid_contract_proposal(), confidence=True),
            "confidence is invalid",
        ),
        (
            replace(_valid_contract_proposal(), reason_code="Bad reason"),
            "reason code is invalid",
        ),
    ),
)
def test_untrusted_model_cannot_escape_the_proposal_contract(
    proposal: MemoryProposal,
    message: str,
) -> None:
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
            proposals=(proposal,),
        )
    )

    with pytest.raises(MemoryInterpretationError, match=message):
        MemoryInterpreter(model).interpret(_valid_contract_request())


def test_model_cannot_return_two_proposals_for_the_same_fact_key() -> None:
    proposal = _valid_contract_proposal()
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
            proposals=(proposal, replace(proposal, canonical_value="准备大学英语六级")),
        )
    )

    with pytest.raises(
        MemoryInterpretationError,
        match="fact keys are duplicated",
    ):
        MemoryInterpreter(model).interpret(_valid_contract_request())


def test_model_cannot_attach_unbounded_or_duplicate_source_quotes() -> None:
    quote = _valid_contract_proposal().source_quotes[0]
    proposal = replace(
        _valid_contract_proposal(),
        source_quotes=(quote, quote, quote, quote),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
            proposals=(proposal,),
        )
    )

    with pytest.raises(
        MemoryInterpretationError,
        match="at most three unique user quotes",
    ):
        MemoryInterpreter(model).interpret(_valid_contract_request())


def test_natural_recall_query_does_not_require_a_keyword_hint() -> None:
    request = MemoryRecallRequest(
        request_id="recall-natural-reference",
        subject=_confirmed_subject(),
        interaction_kind="explicit_recall",
        surface="voice",
        query="上次让我紧张的那个考试是什么？",
        memory_reference_budget=3,
        requested_kinds=("goal", "life_event", "wellbeing"),
    )

    plan = MemoryRecallPlanner().plan(request)

    assert plan.should_recall is True
    assert plan.reason_code == "semantic_tool_request"
    assert plan.query == request.query
    assert plan.kinds == ("goal", "life_event", "wellbeing")
    assert plan.limit == 3


def test_general_qa_cannot_recall_private_memory_even_if_the_model_requests_it() -> (
    None
):
    request = MemoryRecallRequest(
        request_id="recall-general-qa",
        subject=_confirmed_subject(),
        interaction_kind="general_qa",
        surface="voice",
        query="解释一下什么是英语六级。",
        memory_reference_budget=2,
        requested_kinds=("goal",),
    )

    plan = MemoryRecallPlanner().plan(request)

    assert plan.should_recall is False
    assert plan.reason_code == "interaction_not_eligible"
    assert plan.limit == 0


def test_unknown_speaker_cannot_receive_a_private_recall_plan() -> None:
    request = MemoryRecallRequest(
        request_id="recall-unknown-speaker",
        subject=replace(
            _confirmed_subject(),
            speaker_identity="unknown",
            persistence_allowed=False,
        ),
        interaction_kind="explicit_recall",
        surface="voice",
        query="你记得我最近在忙什么吗？",
        memory_reference_budget=2,
    )

    plan = MemoryRecallPlanner().plan(request)

    assert plan.should_recall is False
    assert plan.reason_code == "subject_not_eligible"
    assert plan.limit == 0


def test_zero_policy_budget_overrides_a_semantic_recall_request() -> None:
    request = MemoryRecallRequest(
        request_id="recall-zero-budget",
        subject=_confirmed_subject(),
        interaction_kind="explicit_recall",
        surface="voice",
        query="上次让我紧张的那个考试是什么？",
        memory_reference_budget=0,
    )

    plan = MemoryRecallPlanner().plan(request)

    assert plan.should_recall is False
    assert plan.reason_code == "memory_budget_exhausted"
    assert plan.limit == 0


def test_initiative_surface_always_excludes_sensitive_memory() -> None:
    request = MemoryRecallRequest(
        request_id="recall-initiative-sensitive",
        subject=_confirmed_subject(),
        interaction_kind="conversation",
        surface="initiative",
        query="最近有什么值得主动跟进的事情？",
        memory_reference_budget=2,
        exclude_sensitivities=("low",),
    )

    plan = MemoryRecallPlanner().plan(request)

    assert plan.should_recall is True
    assert plan.exclude_sensitivities == ("low", "sensitive")


@pytest.mark.parametrize(
    ("recall_request", "message"),
    (
        (replace(_valid_recall_request(), query=""), "query is invalid"),
        (
            replace(
                _valid_recall_request(),
                requested_fact_keys=("Not a key",),
            ),
            "fact key is invalid",
        ),
        (
            replace(
                _valid_recall_request(),
                requested_kinds=("assistant_action",),
            ),
            "kind is invalid",
        ),
        (
            replace(
                _valid_recall_request(),
                exclude_sensitivities=("public",),
            ),
            "sensitivity is invalid",
        ),
        (
            replace(
                _valid_recall_request(),
                occurred_after="2026-07-22T10:00:00+08:00",
                occurred_before="2026-07-21T10:00:00+08:00",
            ),
            "time range is invalid",
        ),
        (
            replace(
                _valid_recall_request(),
                requested_fact_keys=tuple(f"goal:goal_{index}" for index in range(9)),
            ),
            "at most eight fact keys",
        ),
    ),
)
def test_untrusted_memory_tool_arguments_are_rejected(
    recall_request: MemoryRecallRequest,
    message: str,
) -> None:
    with pytest.raises(MemoryRecallPlanningError, match=message):
        MemoryRecallPlanner().plan(recall_request)


def test_recall_time_filters_require_timezone_aware_values() -> None:
    request = replace(
        _valid_recall_request(),
        occurred_after="2026-07-21T10:00:00",
    )

    with pytest.raises(
        MemoryRecallPlanningError,
        match="time must include a timezone",
    ):
        MemoryRecallPlanner().plan(request)


def test_reported_speech_keeps_third_party_attribution_for_the_write_policy() -> None:
    request = replace(
        _valid_contract_request(),
        sources=(
            MemorySource(
                turn_id="turn-1",
                role="user",
                text="室友说他准备考研。",
                occurred_at="2026-07-21T10:00:00+08:00",
            ),
        ),
    )
    proposal = replace(
        _valid_contract_proposal(),
        fact_key="goal:graduate_exam",
        canonical_value="准备研究生考试",
        source_quotes=(
            MemorySourceQuote(
                turn_id="turn-1",
                quote="室友说他准备考研",
            ),
        ),
        claim_type="reported_speech",
        subject_scope="third_party",
        reason_code="reported_third_party_goal",
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
            proposals=(proposal,),
        )
    )

    result = MemoryInterpreter(model).interpret(request)

    assert result.proposals[0].subject_scope == "third_party"


@pytest.mark.parametrize(
    ("text", "claim_type", "reason_code"),
    (
        ("如果我去考研，也许会选计算机。", "hypothetical", "hypothetical_goal"),
        ("我没有在准备考研。", "negated", "negated_goal"),
        ("我梦到自己考上研究生了。", "dream", "dreamed_goal"),
        ("开玩笑的，我才没有一天背完六级词汇。", "joke", "joking_goal"),
        ("我可能在准备六级。", "asr_uncertain", "uncertain_asr_goal"),
    ),
)
def test_risky_claim_attribution_is_preserved_for_later_write_policy(
    text: str,
    claim_type: str,
    reason_code: str,
) -> None:
    request = replace(
        _valid_contract_request(),
        sources=(
            MemorySource(
                turn_id="turn-1",
                role="user",
                text=text,
                occurred_at="2026-07-21T10:00:00+08:00",
                asr_reliability=(
                    "uncertain" if claim_type == "asr_uncertain" else "reliable"
                ),
            ),
        ),
    )
    proposal = replace(
        _valid_contract_proposal(),
        canonical_value="准备研究生或英语六级考试",
        source_quotes=(
            MemorySourceQuote(
                turn_id="turn-1",
                quote=text.rstrip("。"),
            ),
        ),
        claim_type=claim_type,
        reason_code=reason_code,
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
            proposals=(proposal,),
        )
    )

    result = MemoryInterpreter(model).interpret(request)

    assert result.proposals[0].claim_type == claim_type


def test_model_cannot_upgrade_uncertain_asr_to_an_explicit_fact() -> None:
    request = replace(
        _valid_contract_request(),
        sources=(
            replace(
                _valid_contract_request().sources[0],
                asr_reliability="uncertain",
            ),
        ),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
            proposals=(_valid_contract_proposal(),),
        )
    )

    with pytest.raises(
        MemoryInterpretationError,
        match="uncertain ASR must keep asr_uncertain",
    ):
        MemoryInterpreter(model).interpret(request)


def test_candidate_mode_interprets_bounded_multi_turn_context_without_blocking_commit(
    tmp_path,
) -> None:
    model = StaticInterpretationModel(
        MemoryInterpretationResult(
            schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
        )
    )
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "companion.db"),
        token_secret=b"semantic-context",
        memory_interpreter=MemoryInterpreter(model),
        memory_interpreter_mode="candidate",
    )

    for turn_id, occurred_at, user_text, reply in (
        (
            "turn-context-1",
            "2026-07-21T10:00:00+08:00",
            "我最近一直在准备六级。",
            "准备得怎么样？",
        ),
        (
            "turn-context-2",
            "2026-07-21T10:00:06+08:00",
            "还是听力最让我头疼。",
            "听力确实需要多花一点时间。",
        ),
    ):
        prepared = mind.prepare_turn(
            CompanionTurnRequest(
                turn_id=turn_id,
                subject=_confirmed_subject(),
                request_digest=f"digest-{turn_id}",
                surface="voice",
                occurred_at=occurred_at,
                source_text=user_text,
                conversation_digest="conversation-a",
            )
        )
        committed = mind.commit_turn(
            prepared,
            CompanionTurnOutcome(
                visible_response=reply,
                assistant_action="reply",
                delivery_status="generated",
            ),
        )
        assert committed.status == "committed"

    result = asyncio.run(mind.run_due_work(now="2026-07-21T10:00:10+08:00", limit=10))

    assert result.succeeded == 2
    second_request = next(
        request
        for request in model.requests
        if request.current_turn_id == "turn-context-2"
    )
    assert [(source.turn_id, source.role) for source in second_request.sources] == [
        ("turn-context-1", "user"),
        ("turn-context-1:assistant", "assistant"),
        ("turn-context-2", "user"),
    ]


def test_candidate_mode_persists_a_normalized_multi_turn_candidate(tmp_path) -> None:
    model = MultiTurnCandidateModel()
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "companion.db"),
        token_secret=b"semantic-candidate",
        memory_interpreter=MemoryInterpreter(model),
        memory_interpreter_mode="candidate",
    )
    for index, text in enumerate(
        ("我最近一直在准备六级。", "还是听力最让我头疼。"),
        start=1,
    ):
        turn_id = f"turn-semantic-{index}"
        prepared = mind.prepare_turn(
            CompanionTurnRequest(
                turn_id=turn_id,
                subject=_confirmed_subject(),
                request_digest=f"digest-{turn_id}",
                surface="voice",
                occurred_at=f"2026-07-21T10:00:0{index}+08:00",
                source_text=text,
                conversation_digest="conversation-semantic",
            )
        )
        mind.commit_turn(
            prepared,
            CompanionTurnOutcome(
                visible_response="我在听。",
                assistant_action="reply",
                delivery_status="generated",
            ),
        )

    work = asyncio.run(mind.run_due_work(now="2026-07-21T10:00:10+08:00", limit=10))
    projection = mind.project(
        CompanionProjectionRequest(
            subject=_confirmed_subject(),
            surface="operator",
            now="2026-07-21T10:00:11+08:00",
        )
    )
    student_projection = mind.project(
        CompanionProjectionRequest(
            subject=_confirmed_subject(),
            surface="miniprogram",
            now="2026-07-21T10:00:11+08:00",
        )
    )

    assert work.succeeded == 2
    candidate = next(
        item
        for item in projection.payload["diagnostics"]["evidence_timeline"]
        if item["source_kind"] == "conversation_candidate"
    )
    assert candidate["fact_key"] == "goal:english_cet6"
    assert candidate["status"] == "candidate"
    assert candidate["prompt_eligible"] is False
    assert candidate["source_summary"] == ("正在准备英语六级，当前主要困难是听力")
    assert "pending_memory_candidates" not in student_projection.payload
    assert "evidence" not in student_projection.payload
    assert "diagnostics" not in student_projection.payload
    evaluations = projection.payload["diagnostics"]["semantic_memory_evaluations"]
    assert len(evaluations) == 2
    proposal_evaluation = next(
        item for item in evaluations if item["proposal_count"] == 1
    )
    assert proposal_evaluation["action_counts"] == {"candidate": 1}
    assert proposal_evaluation["claim_type_counts"] == {"explicit_statement": 1}
    assert (
        projection.payload["diagnostics"]["health"]["semantic_memory_evaluations"] == 2
    )


def test_semantic_recall_tool_uses_natural_query_and_returns_at_most_two_summaries(
    tmp_path,
) -> None:
    store = CompanionStore(tmp_path / "semantic-recall.db")
    seed_mind = CompanionMind(store=store, token_secret=b"semantic-recall-seed")
    seed = seed_mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-recall-seed",
            subject=_confirmed_subject(),
            request_digest="digest-recall-seed",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
        )
    )
    seed_mind.commit_turn(
        seed,
        CompanionTurnOutcome(
            visible_response="我记得了。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=(
                {
                    "kind": "goal",
                    "ownership_scope": "user",
                    "content": {
                        "fact_key": "goal:english_cet6",
                        "canonical_value": "六级考试让我紧张",
                    },
                    "source_summary": "用户明确表示六级考试会让自己紧张。",
                    "attribution": "explicit_user_statement",
                    "confidence": 1.0,
                    "retention": "persistent",
                    "prompt_eligible": True,
                },
                {
                    "kind": "goal",
                    "ownership_scope": "user",
                    "content": {
                        "fact_key": "goal:robot_competition",
                        "canonical_value": "正在推进机器人竞赛",
                    },
                    "source_summary": "用户正在推进机器人竞赛。",
                    "attribution": "explicit_user_statement",
                    "confidence": 1.0,
                    "retention": "persistent",
                    "prompt_eligible": True,
                },
                {
                    "kind": "preference",
                    "ownership_scope": "user",
                    "content": {
                        "fact_key": "preference:working_style",
                        "canonical_value": "通常喜欢先列计划再行动",
                    },
                    "source_summary": "用户通常喜欢先列计划再行动。",
                    "attribution": "explicit_user_statement",
                    "confidence": 1.0,
                    "retention": "persistent",
                    "prompt_eligible": True,
                },
            ),
        ),
    )
    semantic_mind = CompanionMind(
        store=store,
        token_secret=b"semantic-recall-tool",
        memory_interpreter=MemoryInterpreter(
            StaticInterpretationModel(
                MemoryInterpretationResult(
                    schema_version=MEMORY_INTERPRETATION_RESULT_VERSION
                )
            )
        ),
        memory_interpreter_mode="candidate",
    )
    prepared = semantic_mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-natural-recall",
            subject=_confirmed_subject(),
            request_digest="digest-natural-recall",
            surface="voice",
            occurred_at="2026-07-21T10:00:00+08:00",
            interaction_kind="explicit_recall",
            retrieval_query=(
                "按对我的了解，提醒我最近在推进什么，以及我通常喜欢怎么开始。"
            ),
        )
    )

    assert prepared.prompt_context == ()
    recalled, tool_result = semantic_mind._recall_companion_memory(
        prepared,
        query="按对我的了解，提醒我最近在推进什么，以及我通常喜欢怎么开始。",
        kinds=("preference", "interest", "goal"),
        minimum_memory_reference_budget=2,
    )

    assert len(recalled.prompt_context) == 2
    assert any('"kind":"goal"' in item for item in recalled.prompt_context)
    assert any('"kind":"preference"' in item for item in recalled.prompt_context)
    assert len(recalled.used_evidence_ids) == 2
    assert tool_result == {
        "memories": recalled.prompt_context,
        "reason_code": "semantic_tool_request",
    }


def test_semantic_recall_tool_fails_closed_for_general_qa(tmp_path) -> None:
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "semantic-general-qa.db"),
        token_secret=b"semantic-general-qa",
        memory_interpreter=MemoryInterpreter(
            StaticInterpretationModel(
                MemoryInterpretationResult(
                    schema_version=MEMORY_INTERPRETATION_RESULT_VERSION
                )
            )
        ),
        memory_interpreter_mode="candidate",
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-general-qa-tool",
            subject=_confirmed_subject(),
            request_digest="digest-general-qa-tool",
            surface="voice",
            occurred_at="2026-07-21T10:00:00+08:00",
            interaction_kind="general_qa",
        )
    )

    recalled, result = mind._recall_companion_memory(
        prepared,
        query="图书馆今天几点关门？",
    )

    assert recalled == prepared
    assert result == {
        "memories": (),
        "reason_code": "interaction_not_eligible",
    }


def test_context_job_pins_release_on_terminal_semantic_work(tmp_path) -> None:
    database_path = tmp_path / "semantic-context-pins.db"
    store = CompanionStore(database_path)
    mind = CompanionMind(
        store=store,
        token_secret=b"semantic-context-pins",
        memory_interpreter=MemoryInterpreter(
            StaticInterpretationModel(
                MemoryInterpretationResult(
                    schema_version=MEMORY_INTERPRETATION_RESULT_VERSION
                )
            )
        ),
        memory_interpreter_mode="candidate",
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-context-pin",
            subject=_confirmed_subject(),
            request_digest="digest-context-pin",
            surface="voice",
            occurred_at="2026-07-21T10:00:00+08:00",
            source_text="图书馆三楼待着舒服多了。",
            conversation_digest="conversation-context-pin",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="我听到了。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM companion_context_messages"
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM companion_context_job_pins"
            ).fetchone()[0]
            == 2
        )

    work = asyncio.run(mind.run_due_work(now="2026-07-21T10:00:10+08:00", limit=10))

    assert work.succeeded == 1
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM companion_context_job_pins"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM companion_context_messages"
            ).fetchone()[0]
            == 2
        )

    store.expire_derived_objects(now="2026-07-21T10:31:00+08:00")
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM companion_context_messages"
            ).fetchone()[0]
            == 0
        )


def test_active_explicit_persists_only_low_risk_stable_fact_and_scrubs_raw_quote(
    tmp_path,
) -> None:
    proposal = MemoryProposal(
        fact_key="preference:study_environment",
        kind="preference",
        canonical_value="偏好在安静的图书馆学习",
        source_quotes=(
            MemorySourceQuote(
                turn_id="turn-active-explicit",
                quote="我更喜欢在安静的图书馆学习",
            ),
        ),
        claim_type="explicit_statement",
        temporal_scope="stable",
        sensitivity="low",
        subject_scope="self",
        confidence=0.97,
        reason_code="explicit_stable_preference",
    )
    store = CompanionStore(tmp_path / "semantic-active.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"semantic-active-explicit",
        memory_interpreter=MemoryInterpreter(
            StaticInterpretationModel(
                MemoryInterpretationResult(
                    schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
                    proposals=(proposal,),
                )
            )
        ),
        memory_interpreter_mode="active_explicit",
        memory_active_explicit_release_enabled=True,
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-active-explicit",
            subject=_confirmed_subject(),
            request_digest="digest-active-explicit",
            surface="voice",
            occurred_at="2026-07-21T11:00:00+08:00",
            source_text="我更喜欢在安静的图书馆学习。",
            conversation_digest="conversation-active-explicit",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="好，我记住了。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )

    work = asyncio.run(mind.run_due_work(now="2026-07-21T11:00:05+08:00", limit=10))

    assert work.succeeded == 1
    with store.connection() as connection:
        row = connection.execute(
            """
            SELECT status, prompt_eligible, content_json
            FROM companion_evidence
            WHERE fact_key = 'preference:study_environment'
            """
        ).fetchone()
        evaluation = connection.execute(
            """
            SELECT action_counts_json, reason_counts_json
            FROM semantic_memory_evaluations
            """
        ).fetchone()
    assert (row["status"], row["prompt_eligible"]) == ("active", 1)
    content = json.loads(row["content_json"])
    assert "source_quote" not in content
    assert "source_quotes" not in content
    assert content["source_quote_digests"]
    assert json.loads(evaluation["action_counts_json"]) == {"active": 1}
    assert json.loads(evaluation["reason_counts_json"]) == {"explicit_low_risk_fact": 1}


def _run_semantic_preference_relation(
    tmp_path,
    *,
    database_name: str,
    source_text: str,
    proposal: MemoryProposal,
    existing_fact_key: str = "preference:planning_habit",
) -> tuple[CompanionStore, object]:
    store = CompanionStore(tmp_path / database_name)
    mind = CompanionMind(
        store=store,
        token_secret=b"semantic-preference-relation",
        memory_interpreter=MemoryInterpreter(
            StaticInterpretationModel(
                MemoryInterpretationResult(
                    schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
                    proposals=(proposal,),
                )
            )
        ),
        memory_interpreter_mode="active_explicit",
        memory_active_explicit_release_enabled=True,
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-preference-relation",
            subject=_confirmed_subject(),
            request_digest=f"digest-{database_name}",
            surface="voice",
            occurred_at="2026-07-21T11:00:00+08:00",
            source_text=source_text,
            conversation_digest=f"conversation-{database_name}",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="我会按你现在更舒服的方式陪你。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    with store.connection() as connection:
        connection.execute(
            """
            INSERT INTO companion_evidence(
                evidence_id, pet_id, memory_subject_id, ownership_scope,
                kind, content_json, fact_key, sensitivity, source_kind,
                source_ref, source_summary, attribution, confidence,
                occurred_at, retention, status, prompt_eligible, created_at
            ) VALUES (
                'old-task-start-strategy', ?, ?, 'user', 'preference', ?,
                ?, 'low', 'control',
                'control:old-task-start-strategy', '做事前习惯先列计划',
                'explicit_statement', 1.0, '2026-07-20T10:00:00+08:00',
                'persistent', 'active', 1, '2026-07-20T10:00:00+08:00'
            )
            """,
            (
                prepared.pet_id,
                prepared.memory_subject_id,
                json.dumps(
                    {"canonical_value": "做事前习惯先列计划"},
                    ensure_ascii=False,
                ),
                existing_fact_key,
            ),
        )
        connection.commit()
    work = asyncio.run(mind.run_due_work(now="2026-07-21T11:00:05+08:00", limit=10))
    return store, work


def test_semantic_replacement_without_magic_words_supersedes_target(tmp_path) -> None:
    proposal = MemoryProposal(
        fact_key="preference:focus_on_key_step",
        kind="preference",
        canonical_value="做任务时倾向先抓关键路径",
        source_quotes=(
            MemorySourceQuote(
                turn_id="turn-preference-relation",
                quote="最近我发现先抓关键路径更适合我",
            ),
        ),
        claim_type="explicit_statement",
        temporal_scope="stable",
        sensitivity="low",
        subject_scope="self",
        confidence=0.96,
        reason_code="stable_preference_changed",
        memory_action="replace",
        target_evidence_id="old-task-start-strategy",
    )
    store, work = _run_semantic_preference_relation(
        tmp_path,
        database_name="semantic-replace.db",
        source_text="最近我发现先抓关键路径更适合我。",
        proposal=proposal,
    )

    assert work.succeeded == 1
    with store.connection() as connection:
        rows = connection.execute(
            """
            SELECT evidence_id, fact_key, status, prompt_eligible, content_json
            FROM companion_evidence
            WHERE evidence_id = 'old-task-start-strategy'
               OR fact_key = 'preference:task_start_strategy'
            ORDER BY created_at
            """
        ).fetchall()
        relation = connection.execute(
            """
            SELECT relation_kind, source_evidence_id, target_evidence_id
            FROM evidence_relations
            WHERE relation_kind = 'superseded_by'
            """
        ).fetchone()
    assert [(row["status"], row["prompt_eligible"]) for row in rows] == [
        ("superseded", 0),
        ("active", 1),
    ]
    assert rows[1]["fact_key"] == "preference:task_start_strategy"
    assert json.loads(rows[1]["content_json"])["write_reason_code"] == (
        "semantic_replacement"
    )
    assert tuple(relation) == (
        "superseded_by",
        "old-task-start-strategy",
        rows[1]["evidence_id"],
    )


def test_emotional_support_replacement_retires_a_misclassified_task_strategy(
    tmp_path,
) -> None:
    proposal = MemoryProposal(
        fact_key="preference:emotional_support_style",
        kind="preference",
        canonical_value="压力大时先慢慢理清感受，再温和地一起想办法",
        source_quotes=(
            MemorySourceQuote(
                turn_id="turn-preference-relation",
                quote="压力大时我希望你先陪我理清感受，再一起想办法",
            ),
        ),
        claim_type="explicit_statement",
        temporal_scope="stable",
        sensitivity="low",
        subject_scope="self",
        confidence=0.97,
        reason_code="explicit_emotional_support_update",
        memory_action="replace",
        target_evidence_id="old-task-start-strategy",
    )
    store, work = _run_semantic_preference_relation(
        tmp_path,
        database_name="semantic-emotional-support-migration.db",
        source_text="压力大时我希望你先陪我理清感受，再一起想办法。",
        proposal=proposal,
        existing_fact_key="preference:task_start_strategy",
    )

    assert work.succeeded == 1
    with store.connection() as connection:
        replacement = connection.execute(
            """
            SELECT evidence_id
            FROM companion_evidence
            WHERE fact_key = 'preference:emotional_support_style'
              AND status = 'active'
            """
        ).fetchone()
        epoch_id = connection.execute(
            """
            SELECT epoch_id FROM relationship_epochs
            WHERE pet_id = ? AND ended_at IS NULL
            """,
            (_confirmed_subject().pet_id,),
        ).fetchone()["epoch_id"]
        connection.execute(
            """
            INSERT INTO companion_evidence(
                evidence_id, pet_id, memory_subject_id, ownership_scope,
                kind, content_json, fact_key, sensitivity, source_kind,
                source_ref, source_summary, attribution, confidence,
                occurred_at, retention, status, prompt_eligible, created_at
            ) VALUES (
                'polluted-task-strategy-for-repair', ?, ?, 'user', 'preference', ?,
                'preference:task_start_strategy', 'low', 'control',
                'control:polluted-task-strategy-for-repair',
                '压力大时不要立刻给方案', 'explicit_statement', 1.0,
                '2026-07-19T10:00:00+08:00', 'persistent', 'active', 1,
                '2026-07-19T10:00:00+08:00'
            )
            """,
            (
                _confirmed_subject().pet_id,
                _confirmed_subject().memory_subject_id,
                json.dumps(
                    {"canonical_value": "压力大时不要立刻给方案"},
                    ensure_ascii=False,
                ),
            ),
        )
        connection.execute(
            """
            INSERT INTO companion_adjustments(
                adjustment_id, pet_id, relationship_epoch_id, dimension,
                value_json, scope, behavior_key, context_scope, direction,
                status, confidence, generated_by, created_at
            ) VALUES (
                'adjustment-from-polluted-memory', ?, ?, 'emotional_posture',
                '{"value":"neutral"}', 'user_low_mood', 'emotional_posture',
                'user_low_mood', 'decrease', 'active', 0.9,
                'deterministic-test', '2026-07-19T10:00:00+08:00'
            )
            """,
            (_confirmed_subject().pet_id, epoch_id),
        )
        connection.execute(
            """
            INSERT INTO adjustment_evidence(adjustment_id, evidence_id, pet_id)
            VALUES ('adjustment-from-polluted-memory',
                    'polluted-task-strategy-for-repair', ?)
            """,
            (_confirmed_subject().pet_id,),
        )
        connection.commit()

    repair = store.repair_misclassified_semantic_evidence(
        owner_user_id=_confirmed_subject().owner_user_id,
        pet_id=_confirmed_subject().pet_id,
        memory_subject_id=_confirmed_subject().memory_subject_id,
        obsolete_evidence_id="polluted-task-strategy-for-repair",
        replacement_evidence_id=replacement["evidence_id"],
        obsolete_fact_key="preference:task_start_strategy",
        replacement_fact_key="preference:emotional_support_style",
        now="2026-07-21T11:00:06+08:00",
    )

    assert repair["status"] == "applied"
    with store.connection() as connection:
        rows = connection.execute(
            """
            SELECT evidence_id, fact_key, status, prompt_eligible
            FROM companion_evidence
            WHERE evidence_id = 'old-task-start-strategy'
               OR evidence_id = 'polluted-task-strategy-for-repair'
               OR fact_key = 'preference:emotional_support_style'
            ORDER BY created_at
            """
        ).fetchall()
        repaired_relation = connection.execute(
            """
            SELECT relation_kind, source_evidence_id, target_evidence_id
            FROM evidence_relations
            WHERE source_evidence_id = 'polluted-task-strategy-for-repair'
            """
        ).fetchone()
        adjustment = connection.execute(
            """
            SELECT status FROM companion_adjustments
            WHERE adjustment_id = 'adjustment-from-polluted-memory'
            """
        ).fetchone()
    states = {
        row["evidence_id"]: (row["fact_key"], row["status"], row["prompt_eligible"])
        for row in rows
    }
    assert states["old-task-start-strategy"] == (
        "preference:task_start_strategy",
        "superseded",
        0,
    )
    assert states["polluted-task-strategy-for-repair"] == (
        "preference:task_start_strategy",
        "superseded",
        0,
    )
    assert states[replacement["evidence_id"]] == (
        "preference:emotional_support_style",
        "active",
        1,
    )
    assert tuple(repaired_relation) == (
        "superseded_by",
        "polluted-task-strategy-for-repair",
        replacement["evidence_id"],
    )
    assert adjustment["status"] == "revoked"


def test_current_primary_focus_promotes_an_existing_candidate(tmp_path) -> None:
    proposal = MemoryProposal(
        fact_key="goal:current_primary_focus",
        kind="goal",
        canonical_value="这学期主要准备数字钢琴考级，目前卡在模拟上台紧张",
        source_quotes=(
            MemorySourceQuote(
                turn_id="turn-current-primary-focus",
                quote="这学期我主要准备数字钢琴考级，最近卡在模拟上台紧张",
            ),
        ),
        claim_type="explicit_statement",
        temporal_scope="episode",
        sensitivity="private",
        subject_scope="self",
        confidence=0.95,
        reason_code="explicit_current_focus",
        memory_action="coexist",
        target_evidence_id="active-earlier-primary-focus",
    )
    store = CompanionStore(tmp_path / "semantic-current-primary-focus.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"semantic-current-primary-focus",
        memory_interpreter=MemoryInterpreter(
            StaticInterpretationModel(
                MemoryInterpretationResult(
                    schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
                    proposals=(proposal,),
                )
            )
        ),
        memory_interpreter_mode="active_explicit",
        memory_active_explicit_release_enabled=True,
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-current-primary-focus",
            subject=_confirmed_subject(),
            request_digest="digest-current-primary-focus",
            surface="voice",
            occurred_at="2026-07-21T11:00:00+08:00",
            source_text="这学期我主要准备数字钢琴考级，最近卡在模拟上台紧张。",
            conversation_digest="conversation-current-primary-focus",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="我会陪你继续准备。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    with store.connection() as connection:
        connection.execute(
            """
            INSERT INTO companion_evidence(
                evidence_id, pet_id, memory_subject_id, ownership_scope,
                kind, content_json, fact_key, sensitivity, source_kind,
                source_ref, source_summary, attribution, confidence,
                occurred_at, retention, status, prompt_eligible, expires_at,
                created_at
            ) VALUES (
                'candidate-current-primary-focus', ?, ?, 'user', 'goal', ?,
                'goal:current_primary_focus', 'private', 'conversation_candidate',
                'turn-earlier-focus', ?, 'explicit_statement', 0.9,
                '2026-07-20T10:00:00+08:00', 'until_confirmed', 'candidate', 0,
                '2026-08-19T10:00:00+08:00', '2026-07-20T10:00:00+08:00'
            )
            """,
            (
                prepared.pet_id,
                prepared.memory_subject_id,
                json.dumps(
                    {"canonical_value": proposal.canonical_value},
                    ensure_ascii=False,
                ),
                proposal.canonical_value,
            ),
        )
        connection.execute(
            """
            INSERT INTO companion_evidence(
                evidence_id, pet_id, memory_subject_id, ownership_scope,
                kind, content_json, fact_key, sensitivity, source_kind,
                source_ref, source_summary, attribution, confidence,
                occurred_at, retention, status, prompt_eligible, created_at
            ) VALUES (
                'active-earlier-primary-focus', ?, ?, 'user', 'goal', ?,
                'goal:current_primary_focus', 'private', 'conversation_candidate',
                'turn-active-earlier-focus', ?, 'explicit_statement', 0.95,
                '2026-07-20T11:00:00+08:00', 'persistent', 'active', 1,
                '2026-07-20T11:00:00+08:00'
            )
            """,
            (
                prepared.pet_id,
                prepared.memory_subject_id,
                json.dumps(
                    {"canonical_value": "这学期主要准备英语六级"},
                    ensure_ascii=False,
                ),
                "这学期主要准备英语六级",
            ),
        )
        connection.commit()

    work = asyncio.run(mind.run_due_work(now="2026-07-21T11:00:05+08:00", limit=10))

    assert work.succeeded == 1
    with store.connection() as connection:
        rows = connection.execute(
            """
            SELECT evidence_id, status, prompt_eligible, content_json
            FROM companion_evidence
            WHERE fact_key = 'goal:current_primary_focus'
            ORDER BY created_at
            """
        ).fetchall()
        relation_kinds = tuple(
            row["relation_kind"]
            for row in connection.execute(
                """
                SELECT relation_kind FROM evidence_relations
                WHERE source_evidence_id IN (
                    'candidate-current-primary-focus',
                    'active-earlier-primary-focus'
                )
                ORDER BY relation_kind, source_evidence_id
                """
            )
        )
    assert [(row["status"], row["prompt_eligible"]) for row in rows] == [
        ("superseded", 0),
        ("superseded", 0),
        ("active", 1),
    ]
    assert json.loads(rows[2]["content_json"])["write_reason_code"] == (
        "explicit_current_primary_focus"
    )
    assert relation_kinds == ("superseded_by", "superseded_by")


def test_structured_memory_output_failure_retries_instead_of_failing_permanently(
    tmp_path,
) -> None:
    database_path = tmp_path / "companion.db"
    store = CompanionStore(database_path)
    adapter = InvalidJsonMemoryAdapter()
    mind = CompanionMind(
        store=store,
        token_secret=b"semantic-structured-output-retry",
        memory_interpreter=MemoryInterpreter(LLMMemoryInterpretationModel(adapter)),
        memory_interpreter_mode="candidate",
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-structured-output-retry",
            subject=_confirmed_subject(),
            request_digest="digest-structured-output-retry",
            surface="voice",
            occurred_at="2026-07-21T10:00:00+08:00",
            source_text="我最近更喜欢先把任务拆成一个最小步骤。",
            conversation_digest="conversation-structured-output-retry",
        )
    )
    committed = mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="我记下了。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )

    result = asyncio.run(mind.run_due_work(now="2026-07-21T10:00:10+08:00", limit=10))

    assert adapter.calls == 2
    assert result.retried == 1
    assert result.failed == 0
    with store.connection() as connection:
        row = connection.execute(
            """
            SELECT status, attempt, next_attempt_at
            FROM consolidation_jobs WHERE job_id = ?
            """,
            (committed.job_ids[0],),
        ).fetchone()
    assert row["status"] == "retry"
    assert row["attempt"] == 1
    assert row["next_attempt_at"] == "2026-07-21T10:00:40+08:00"


def test_semantic_replacement_canonicalizes_legacy_working_style(tmp_path) -> None:
    proposal = MemoryProposal(
        fact_key="preference:task_start_strategy",
        kind="preference",
        canonical_value="面对陌生任务时先试一小段，再根据结果调整",
        source_quotes=(
            MemorySourceQuote(
                turn_id="turn-preference-relation",
                quote="我现在遇到陌生任务，会先试一小段，再根据结果调整",
            ),
        ),
        claim_type="explicit_statement",
        temporal_scope="stable",
        sensitivity="low",
        subject_scope="self",
        confidence=0.97,
        reason_code="explicit_stable_preference",
        memory_action="replace",
        target_evidence_id="old-task-start-strategy",
    )
    store, work = _run_semantic_preference_relation(
        tmp_path,
        database_name="semantic-working-style-replace.db",
        source_text="我现在遇到陌生任务，会先试一小段，再根据结果调整。",
        proposal=proposal,
        existing_fact_key="preference:working_style",
    )

    assert work.succeeded == 1
    with store.connection() as connection:
        old = connection.execute(
            """
            SELECT status, prompt_eligible FROM companion_evidence
            WHERE evidence_id = 'old-task-start-strategy'
            """
        ).fetchone()
        active = connection.execute(
            """
            SELECT fact_key, status, prompt_eligible FROM companion_evidence
            WHERE fact_key = 'preference:task_start_strategy'
            """
        ).fetchone()
    assert tuple(old) == ("superseded", 0)
    assert tuple(active) == ("preference:task_start_strategy", "active", 1)


def test_semantic_coexistence_keeps_both_stable_preferences_active(tmp_path) -> None:
    proposal = MemoryProposal(
        fact_key="preference:task_start_strategy",
        kind="preference",
        canonical_value="赶时间时倾向先抓关键路径",
        source_quotes=(
            MemorySourceQuote(
                turn_id="turn-preference-relation",
                quote="我平时会列计划，赶时间时也会先抓关键路径",
            ),
        ),
        claim_type="explicit_statement",
        temporal_scope="stable",
        sensitivity="low",
        subject_scope="self",
        confidence=0.94,
        reason_code="contextual_preference_coexists",
        memory_action="coexist",
        target_evidence_id="old-task-start-strategy",
    )
    store, work = _run_semantic_preference_relation(
        tmp_path,
        database_name="semantic-coexist.db",
        source_text="我平时会列计划，赶时间时也会先抓关键路径。",
        proposal=proposal,
    )

    assert work.succeeded == 1
    with store.connection() as connection:
        statuses = connection.execute(
            """
            SELECT status, prompt_eligible
            FROM companion_evidence
            WHERE evidence_id = 'old-task-start-strategy'
               OR fact_key = 'preference:task_start_strategy'
            ORDER BY created_at
            """
        ).fetchall()
        relation_kind = connection.execute(
            "SELECT relation_kind FROM evidence_relations"
        ).fetchone()[0]
    assert [tuple(row) for row in statuses] == [("active", 1), ("active", 1)]
    assert relation_kind == "coexists_with"


def test_semantic_temporary_override_expires_without_replacing_stable_preference(
    tmp_path,
) -> None:
    proposal = MemoryProposal(
        fact_key="preference:task_start_strategy",
        kind="preference",
        canonical_value="今天赶时间时先抓关键路径",
        source_quotes=(
            MemorySourceQuote(
                turn_id="turn-preference-relation",
                quote="今天赶时间，先抓关键路径处理",
            ),
        ),
        claim_type="explicit_statement",
        temporal_scope="momentary",
        sensitivity="low",
        subject_scope="self",
        confidence=0.97,
        reason_code="temporary_task_strategy",
        memory_action="temporary_override",
        target_evidence_id="old-task-start-strategy",
        valid_until="2026-07-21T18:00:00+08:00",
    )
    store, work = _run_semantic_preference_relation(
        tmp_path,
        database_name="semantic-temporary.db",
        source_text="今天赶时间，先抓关键路径处理。",
        proposal=proposal,
    )

    assert work.succeeded == 1
    with store.connection() as connection:
        before_expiry = connection.execute(
            """
            SELECT evidence_id, status, prompt_eligible, valid_until, expires_at
            FROM companion_evidence
            WHERE evidence_id = 'old-task-start-strategy'
               OR fact_key = 'preference:task_start_strategy'
            ORDER BY created_at
            """
        ).fetchall()
        relationship_epoch_id = connection.execute(
            """
            SELECT epoch_id FROM relationship_epochs
            WHERE pet_id = 'pet-1' AND ended_at IS NULL
            """
        ).fetchone()[0]
    assert [
        (row["status"], row["prompt_eligible"]) for row in before_expiry
    ] == [("active", 1), ("active", 1)]
    assert before_expiry[1]["valid_until"] == before_expiry[1]["expires_at"]
    active_override = store.recall_evidence(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        relationship_epoch_id=relationship_epoch_id,
        turn_id="recall-temporary-before-expiry",
        interaction_kind="explicit_recall",
        now="2026-07-21T11:00:06+08:00",
        retrieval_hints={"kinds": ("preference",)},
        limit=8,
    )
    assert [item.evidence_id for item in active_override] == [
        before_expiry[1]["evidence_id"]
    ]

    store.expire_derived_objects(now="2026-07-22T11:00:01+08:00")
    with store.connection() as connection:
        after_expiry = connection.execute(
            """
            SELECT evidence_id, status, prompt_eligible
            FROM companion_evidence
            WHERE evidence_id = 'old-task-start-strategy'
               OR fact_key = 'preference:task_start_strategy'
            ORDER BY created_at
            """
        ).fetchall()
    assert [
        (row["status"], row["prompt_eligible"]) for row in after_expiry
    ] == [("active", 1), ("expired", 0)]
    restored_preference = store.recall_evidence(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        relationship_epoch_id=relationship_epoch_id,
        turn_id="recall-temporary-after-expiry",
        interaction_kind="explicit_recall",
        now="2026-07-22T11:00:02+08:00",
        retrieval_hints={"kinds": ("preference",)},
        limit=8,
    )
    assert [item.evidence_id for item in restored_preference] == [
        "old-task-start-strategy"
    ]


def test_active_explicit_correction_supersedes_old_private_fact(tmp_path) -> None:
    proposal = MemoryProposal(
        fact_key="preference:debugging_habit",
        kind="preference",
        canonical_value="编程调试时先自己尝试10分钟再求助",
        source_quotes=(
            MemorySourceQuote(
                turn_id="turn-explicit-correction",
                quote="我现在习惯先自己调试10分钟，再来找你一起看",
            ),
        ),
        claim_type="explicit_statement",
        temporal_scope="stable",
        sensitivity="private",
        subject_scope="self",
        confidence=0.99,
        reason_code="explicit_correction",
    )
    store = CompanionStore(tmp_path / "semantic-explicit-correction.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"semantic-explicit-correction",
        memory_interpreter=MemoryInterpreter(
            StaticInterpretationModel(
                MemoryInterpretationResult(
                    schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
                    proposals=(proposal,),
                )
            )
        ),
        memory_interpreter_mode="active_explicit",
        memory_active_explicit_release_enabled=True,
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-explicit-correction",
            subject=_confirmed_subject(),
            request_digest="digest-explicit-correction",
            surface="voice",
            occurred_at="2026-07-21T11:00:00+08:00",
            source_text=(
                "纠正一下：我现在习惯先自己调试10分钟，再来找你一起看。"
                "这个新习惯请替代旧的。"
            ),
            conversation_digest="conversation-explicit-correction",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="好，我更新了。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    with store.connection() as connection:
        connection.execute(
            """
            INSERT INTO companion_evidence(
                evidence_id, pet_id, memory_subject_id, ownership_scope,
                kind, content_json, fact_key, sensitivity, source_kind,
                source_ref, source_summary, attribution, confidence,
                occurred_at, retention, status, prompt_eligible, created_at
            ) VALUES (
                'old-debugging-habit', ?, ?, 'user', 'preference', ?,
                'preference:debugging_habit', 'private', 'control',
                'control:old-debugging-habit', '编程调试时先自己尝试20分钟再求助',
                'explicit_statement', 1.0, '2026-07-20T10:00:00+08:00',
                'persistent', 'active', 1, '2026-07-20T10:00:00+08:00'
            )
            """,
            (
                prepared.pet_id,
                prepared.memory_subject_id,
                json.dumps(
                    {"canonical_value": "编程调试时先自己尝试20分钟再求助"},
                    ensure_ascii=False,
                ),
            ),
        )
        connection.commit()

    work = asyncio.run(mind.run_due_work(now="2026-07-21T11:00:05+08:00", limit=10))

    assert work.succeeded == 1
    with store.connection() as connection:
        rows = connection.execute(
            """
            SELECT evidence_id, status, prompt_eligible, content_json
            FROM companion_evidence
            WHERE fact_key = 'preference:debugging_habit'
            ORDER BY created_at
            """
        ).fetchall()
        relation = connection.execute(
            """
            SELECT relation_kind, source_evidence_id, target_evidence_id
            FROM evidence_relations
            """
        ).fetchone()
        evaluation = connection.execute(
            "SELECT reason_counts_json FROM semantic_memory_evaluations"
        ).fetchone()
    assert [(row["status"], row["prompt_eligible"]) for row in rows] == [
        ("superseded", 0),
        ("active", 1),
    ]
    assert json.loads(rows[1]["content_json"])["canonical_value"] == (
        "编程调试时先自己尝试10分钟再求助"
    )
    assert tuple(relation) == (
        "superseded_by",
        "old-debugging-habit",
        rows[1]["evidence_id"],
    )
    assert json.loads(evaluation["reason_counts_json"]) == {
        "explicit_fact_correction": 1
    }


@pytest.mark.parametrize(
    (
        "source_text",
        "correction_release_enabled",
        "expected_new_status",
        "expected_old_status",
    ),
    (
        (
            "更新一下：考研已经结束。"
            "我接下来准备嵌入式课程设计，请记住。",
            True,
            ("active", 1),
            ("superseded", 0),
        ),
        (
            "更新一下：考研已经结束。"
            "我接下来准备嵌入式课程设计。",
            False,
            ("candidate", 0),
            ("active", 1),
        ),
    ),
)
def test_explicit_goal_transition_supersedes_only_the_completed_goal(
    tmp_path,
    source_text: str,
    correction_release_enabled: bool,
    expected_new_status: tuple[str, int],
    expected_old_status: tuple[str, int],
) -> None:
    proposal = MemoryProposal(
        fact_key="goal:embedded_course_design",
        kind="goal",
        canonical_value="正在准备嵌入式课程设计",
        source_quotes=(
            MemorySourceQuote(
                turn_id="turn-goal-transition",
                quote="我接下来准备嵌入式课程设计",
            ),
        ),
        claim_type="explicit_statement",
        temporal_scope="episode",
        sensitivity="private",
        subject_scope="self",
        confidence=0.99,
        reason_code="explicit_goal_transition",
    )
    store = CompanionStore(tmp_path / "semantic-goal-transition.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"semantic-goal-transition",
        memory_interpreter=MemoryInterpreter(
            StaticInterpretationModel(
                MemoryInterpretationResult(
                    schema_version=MEMORY_INTERPRETATION_RESULT_VERSION,
                    proposals=(proposal,),
                )
            )
        ),
        memory_interpreter_mode="active_explicit",
        memory_active_explicit_release_enabled=correction_release_enabled,
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-goal-transition",
            subject=_confirmed_subject(),
            request_digest="digest-goal-transition",
            surface="voice",
            occurred_at="2026-07-21T11:00:00+08:00",
            source_text=source_text,
            conversation_digest="conversation-goal-transition",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="好，我会按新目标陪你推进。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    with store.connection() as connection:
        for evidence_id, fact_key, value in (
            ("old-graduate-goal", "goal:graduate_exam", "正在准备考研"),
            ("parallel-robot-goal", "goal:robot_competition", "正在准备机器人竞赛"),
        ):
            connection.execute(
                """
                INSERT INTO companion_evidence(
                    evidence_id, pet_id, memory_subject_id, ownership_scope,
                    kind, content_json, fact_key, sensitivity, source_kind,
                    source_ref, source_summary, attribution, confidence,
                    occurred_at, retention, status, prompt_eligible, created_at
                ) VALUES (?, ?, ?, 'user', 'goal', ?, ?, 'private', 'control',
                          ?, ?, 'explicit_statement', 1.0,
                          '2026-07-20T10:00:00+08:00', 'persistent', 'active', 1,
                          '2026-07-20T10:00:00+08:00')
                """,
                (
                    evidence_id,
                    prepared.pet_id,
                    prepared.memory_subject_id,
                    json.dumps({"canonical_value": value}, ensure_ascii=False),
                    fact_key,
                    f"control:{evidence_id}",
                    value,
                ),
            )
        connection.commit()

    work = asyncio.run(mind.run_due_work(now="2026-07-21T11:00:05+08:00", limit=10))

    assert work.succeeded == 1
    with store.connection() as connection:
        rows = connection.execute(
            """
            SELECT fact_key, status, prompt_eligible
            FROM companion_evidence
            WHERE kind = 'goal'
            ORDER BY fact_key
            """
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("goal:embedded_course_design", *expected_new_status),
        ("goal:graduate_exam", *expected_old_status),
        ("goal:robot_competition", "active", 1),
    ]


@pytest.mark.parametrize("action", ("reset_relationship", "purge_personal_memory"))
def test_relationship_reset_and_purge_delete_pinned_semantic_context(
    tmp_path,
    action: str,
) -> None:
    store = CompanionStore(tmp_path / f"semantic-{action}.db")
    mind = CompanionMind(
        store=store,
        token_secret=f"semantic-{action}".encode(),
        memory_interpreter=MemoryInterpreter(
            StaticInterpretationModel(
                MemoryInterpretationResult(
                    schema_version=MEMORY_INTERPRETATION_RESULT_VERSION
                )
            )
        ),
        memory_interpreter_mode="candidate",
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id=f"turn-{action}",
            subject=_confirmed_subject(),
            request_digest=f"digest-{action}",
            surface="voice",
            occurred_at="2026-07-21T12:00:00+08:00",
            source_text="今天在图书馆学习。",
            conversation_digest=f"conversation-{action}",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="我在听。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )

    mind.apply_control(
        CompanionControlCommand(
            action=action,
            subject=_confirmed_subject(),
            payload={
                "now": "2026-07-21T12:00:05+08:00",
                "idempotency_key": f"control-{action}",
            },
        )
    )

    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM companion_context_messages"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM companion_context_job_pins"
            ).fetchone()[0]
            == 0
        )


def test_enabled_mode_without_model_fails_closed_instead_of_restoring_regex_writes(
    tmp_path,
) -> None:
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "semantic-model-unavailable.db"),
        token_secret=b"semantic-model-unavailable",
        memory_interpreter_mode="candidate",
    )

    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-semantic-model-unavailable",
            subject=_confirmed_subject(),
            request_digest="digest-semantic-model-unavailable",
            surface="voice",
            occurred_at="2026-07-21T12:30:00+08:00",
            source_text="我来自杭州。",
            conversation_digest="conversation-model-unavailable",
        )
    )

    assert mind.uses_semantic_user_facts is True
    assert prepared.source_text is None
    committed = mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="我听到了。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    assert committed.job_ids == ()
    assert committed.evidence_ids == ()


def test_active_explicit_mode_is_held_at_candidate_until_release_gate_opens(
    tmp_path,
) -> None:
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "semantic-active-gate.db"),
        token_secret=b"semantic-active-gate",
        memory_interpreter=MemoryInterpreter(
            StaticInterpretationModel(
                MemoryInterpretationResult(
                    schema_version=MEMORY_INTERPRETATION_RESULT_VERSION
                )
            )
        ),
        memory_interpreter_mode="active_explicit",
    )

    assert mind.uses_semantic_user_facts is True
    assert mind.semantic_memory_mode == "candidate"


def _seed_memory_candidate_job_statuses(
    store: CompanionStore,
    statuses: tuple[str, ...],
) -> None:
    with store.connection() as connection:
        for index, status in enumerate(statuses):
            timestamp = f"2026-07-21T10:{index:02d}:00+08:00"
            connection.execute(
                """
                INSERT INTO consolidation_jobs(
                    job_id, pet_id, relationship_epoch_id, job_kind,
                    idempotency_key, payload_json, status, attempt, due_at,
                    lease_until, next_attempt_at, model, prompt_version,
                    schema_version, failure_reason, created_at, updated_at
                ) VALUES (?, ?, NULL, ?, ?, '{}', ?, 1, ?, NULL, NULL,
                          NULL, NULL, ?, ?, ?, ?)
                """,
                (
                    f"memory-health-{index}",
                    "pet-1",
                    "memory_candidate_extraction",
                    f"memory-health-{index}",
                    status,
                    timestamp,
                    MEMORY_INTERPRETATION_RESULT_VERSION,
                    "model output invalid" if status == "failed" else None,
                    timestamp,
                    timestamp,
                ),
            )
        connection.commit()


def test_release_guard_recovers_after_ten_consecutive_successes(tmp_path) -> None:
    store = CompanionStore(tmp_path / "semantic-release-recovery.db")
    _seed_memory_candidate_job_statuses(
        store,
        ("failed",) * 8 + ("succeeded",) * 10,
    )

    assert store.semantic_memory_effective_mode(requested_mode="active_explicit") == (
        "active_explicit",
        "configured_mode",
    )


def test_release_guard_downgrades_again_after_a_new_failure(tmp_path) -> None:
    store = CompanionStore(tmp_path / "semantic-release-regression.db")
    _seed_memory_candidate_job_statuses(
        store,
        ("failed",) * 8 + ("succeeded",) * 10 + ("failed",),
    )

    assert store.semantic_memory_effective_mode(requested_mode="active_explicit") == (
        "candidate",
        "active_release_guard_downgrade",
    )


def test_semantic_interpreter_canonicalizes_legacy_profile_fact_keys(tmp_path) -> None:
    store = CompanionStore(tmp_path / "semantic-legacy-profile.db")
    seed_mind = CompanionMind(store=store, token_secret=b"legacy-profile-seed")
    seed = seed_mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-legacy-origin",
            subject=_confirmed_subject(),
            request_digest="digest-legacy-origin",
            surface="voice",
            occurred_at="2026-07-21T08:00:00+08:00",
        )
    )
    seed_mind.commit_turn(
        seed,
        CompanionTurnOutcome(
            visible_response="记住了。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=(
                {
                    "kind": "profile_fact",
                    "ownership_scope": "user",
                    "content": {"fact_key": "origin", "value": "杭州"},
                    "source_summary": "用户明确表示自己来自杭州。",
                    "attribution": "explicit_user_statement",
                    "confidence": 1.0,
                    "retention": "persistent",
                    "prompt_eligible": True,
                },
            ),
        ),
    )
    model = StaticInterpretationModel(
        MemoryInterpretationResult(schema_version=MEMORY_INTERPRETATION_RESULT_VERSION)
    )
    semantic_mind = CompanionMind(
        store=store,
        token_secret=b"legacy-profile-semantic",
        memory_interpreter=MemoryInterpreter(model),
        memory_interpreter_mode="candidate",
    )
    prepared = semantic_mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-after-legacy-origin",
            subject=_confirmed_subject(),
            request_digest="digest-after-legacy-origin",
            surface="voice",
            occurred_at="2026-07-21T08:01:00+08:00",
            source_text="最近还是更想在图书馆学习。",
            conversation_digest="conversation-legacy-origin",
        )
    )
    semantic_mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="我听到了。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )

    work = asyncio.run(
        semantic_mind.run_due_work(
            now="2026-07-21T08:01:05+08:00",
            limit=10,
        )
    )

    assert work.succeeded == 2
    assert model.requests[0].existing_facts[0].fact_key == "profile:origin"
