import asyncio

import json

import pytest

from core.xiaoxin.companion import (
    CompanionControlCommand,
    CompanionMind,
    CompanionObservation,
    CompanionProjectionRequest,
    CompanionSubjectContext,
    CompanionTurnOutcome,
    CompanionTurnRequest,
)
from core.xiaoxin.companion.reflection import ReflectionProposal
from core.xiaoxin.companion.reflection import ReflectionRequest, ReflectionTurnSource
from core.xiaoxin.companion.adapters import LLMReflectionModel
from core.xiaoxin.companion.store import CompanionStore


def _run_due_work(mind, **kwargs):
    return asyncio.run(mind.run_due_work(**kwargs))


def _subject() -> CompanionSubjectContext:
    return CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )


class ExactQuoteCandidateModel:
    def __init__(self) -> None:
        self.requests = []

    def reflect(self, request):
        self.requests.append(request)
        source = request.turn_sources[0]
        return ReflectionProposal(
            schema_version="companion-reflection-proposal-v1",
            safe_summary="发现一条等待确认的目标候选。",
            proposed_user_facts=(
                {
                    "fact_key": "goal:english_cet6",
                    "kind": "goal",
                    "value": "准备英语六级",
                    "source_turn_id": source.turn_id,
                    "source_quote": "我最近在准备英语六级",
                    "claim_type": "explicit_statement",
                    "sensitivity": "private",
                    "confidence": 0.96,
                },
            ),
        )


class JsonCandidateAdapter:
    def __init__(self) -> None:
        self.messages = []

    def complete_chat(
        self, messages, max_tokens=None, temperature=None, response_format=None
    ):
        self.messages.append(messages)
        return """
        {
          "schema_version": "companion-reflection-proposal-v1",
          "safe_summary": "发现一条等待确认的目标候选。",
          "evidence_ids": [],
          "adjustments": [],
          "proposed_user_facts": [{
            "fact_key": "goal:english_cet6",
            "kind": "goal",
            "value": "准备英语六级",
            "source_turn_id": "turn-adapter-source",
            "source_quote": "我最近在准备英语六级",
            "claim_type": "explicit_statement",
            "sensitivity": "private",
            "confidence": 0.96
          }],
          "chapter_statements": []
        }
        """


class FabricatedQuoteCandidateModel:
    def reflect(self, request):
        return ReflectionProposal(
            schema_version="companion-reflection-proposal-v1",
            safe_summary="发现候选。",
            proposed_user_facts=(
                {
                    "fact_key": "goal:graduate_exam",
                    "kind": "goal",
                    "value": "准备研究生考试",
                    "source_turn_id": request.turn_sources[0].turn_id,
                    "source_quote": "我正在准备研究生考试",
                    "claim_type": "explicit_statement",
                    "sensitivity": "private",
                    "confidence": 0.99,
                },
            ),
        )


class TooManyCandidatesModel:
    def reflect(self, request):
        source = request.turn_sources[0]
        return ReflectionProposal(
            schema_version="companion-reflection-proposal-v1",
            safe_summary="\u5019\u9009\u8fc7\u591a\u3002",
            proposed_user_facts=tuple(
                {
                    "fact_key": f"interest:english_{index}",
                    "kind": "interest",
                    "value": "\u82f1\u8bed\u516d\u7ea7",
                    "source_turn_id": source.turn_id,
                    "source_quote": "\u6211\u6700\u8fd1\u5728\u51c6\u5907\u82f1\u8bed\u516d\u7ea7",
                    "claim_type": "explicit_statement",
                    "sensitivity": "private",
                    "confidence": 0.8,
                }
                for index in range(6)
            ),
        )


class RetryThenCandidateModel(ExactQuoteCandidateModel):
    def reflect(self, request):
        if not self.requests:
            self.requests.append(request)
            raise TimeoutError("temporary reflection timeout")
        return super().reflect(request)


class RiskyClaimCandidateModel:
    def __init__(self, *, value, quote, claim_type):
        self.value = value
        self.quote = quote
        self.claim_type = claim_type

    def reflect(self, request):
        return ReflectionProposal(
            schema_version="companion-reflection-proposal-v1",
            safe_summary="发现一条需要用户确认的候选。",
            proposed_user_facts=(
                {
                    "fact_key": f"life_event:risky_{self.claim_type}",
                    "kind": "life_event",
                    "value": self.value,
                    "source_turn_id": request.turn_sources[0].turn_id,
                    "source_quote": self.quote,
                    "claim_type": self.claim_type,
                    "sensitivity": "private",
                    "confidence": 0.7,
                },
            ),
        )


def test_confirmed_turn_extracts_exact_quote_candidate_without_prompting_it(tmp_path):
    model = ExactQuoteCandidateModel()
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"candidate-extraction",
        reflection_model=model,
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-candidate-1",
            subject=_subject(),
            request_digest="digest-candidate-1",
            surface="voice",
            occurred_at="2026-07-21T10:00:00+08:00",
            source_text="我最近在准备英语六级。",
        )
    )

    committed = mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="听起来你已经开始认真准备了。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )

    assert committed.status == "committed"
    assert len(committed.job_ids) == 1
    before_worker = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T10:00:10+08:00",
        )
    )
    assert before_worker.payload["diagnostics"]["health"][
        "temporary_turn_sources"
    ] == 1

    work = _run_due_work(mind, now="2026-07-21T10:01:00+08:00")
    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T10:01:01+08:00",
        )
    )
    next_turn = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-after-candidate",
            subject=_subject(),
            request_digest="digest-after-candidate",
            surface="voice",
            occurred_at="2026-07-21T10:01:01+08:00",
        )
    )

    assert work.succeeded == 1
    assert len(model.requests) == 1
    assert model.requests[0].turn_sources[0].text == "我最近在准备英语六级。"
    candidate = next(
        item
        for item in operator.payload["diagnostics"]["evidence_timeline"]
        if item["source_kind"] == "conversation_candidate"
    )
    assert candidate["kind"] == "goal"
    assert candidate["fact_key"] == "goal:english_cet6"
    assert candidate["status"] == "candidate"
    assert candidate["prompt_eligible"] is False
    assert candidate["source_ref"] == "turn-candidate-1"
    assert operator.payload["diagnostics"]["health"][
        "temporary_turn_sources"
    ] == 0
    assert candidate["evidence_id"] not in next_turn.used_evidence_ids


def test_user_confirmation_activates_candidate_for_eligible_recall(tmp_path):
    model = ExactQuoteCandidateModel()
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"candidate-confirmation",
        reflection_model=model,
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-confirm-candidate",
            subject=_subject(),
            request_digest="digest-confirm-candidate",
            surface="voice",
            occurred_at="2026-07-21T11:00:00+08:00",
            source_text="我最近在准备英语六级。",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="我记下这件事之前会先让你确认。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    _run_due_work(mind, now="2026-07-21T11:01:00+08:00")
    before = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T11:01:01+08:00",
        )
    )
    candidate = next(
        item
        for item in before.payload["diagnostics"]["evidence_timeline"]
        if item["source_kind"] == "conversation_candidate"
    )

    result = mind.apply_control(
        CompanionControlCommand(
            action="confirm_candidate",
            subject=_subject(),
            payload={
                "evidence_id": candidate["evidence_id"],
                "now": "2026-07-21T11:02:00+08:00",
                "idempotency_key": "confirm-candidate-1",
            },
        )
    )
    after = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T11:02:01+08:00",
        )
    )
    confirmed = next(
        item
        for item in after.payload["diagnostics"]["evidence_timeline"]
        if item["evidence_id"] == candidate["evidence_id"]
    )
    with store.connection() as connection:
        retained_source = connection.execute(
            """
            SELECT json_extract(content_json, '$.source_quote') AS source_quote,
                   json_extract(content_json, '$.source_quote_digest') AS quote_digest
            FROM companion_evidence WHERE evidence_id = ?
            """,
            (candidate["evidence_id"],),
        ).fetchone()
    assert result.action == "confirm_candidate"
    assert result.status == "applied"
    assert result.retained == 1
    assert confirmed["status"] == "active"
    assert confirmed["prompt_eligible"] is True
    assert confirmed["attribution"] == "user_confirmed_candidate"
    assert retained_source["source_quote"] is None
    assert len(retained_source["quote_digest"]) == 64

    recall = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-recall-confirmed-candidate",
            subject=_subject(),
            request_digest="digest-recall-confirmed-candidate",
            surface="voice",
            occurred_at="2026-07-21T11:03:00+08:00",
            interaction_kind="explicit_recall",
        )
    )
    recalled, tool_result = mind._recall_companion_memory(
        recall,
        query="英语六级",
        kinds=("goal",),
    )

    recalled_item = json.loads(recalled.prompt_context[0])
    assert recalled_item["fact"] == "准备英语六级"
    assert recalled_item["kind"] == "goal"
    assert tool_result["memories"] == recalled.prompt_context


def test_llm_adapter_receives_only_the_short_term_turn_source_for_candidates():
    adapter = JsonCandidateAdapter()
    model = LLMReflectionModel(adapter, timeout_seconds=1)
    request = ReflectionRequest(
        job_id="job-adapter-candidate",
        job_kind="memory_candidate_extraction",
        pet_id="pet-1",
        relationship_epoch_id="epoch-1",
        evidence=(),
        turn_sources=(
            ReflectionTurnSource(
                turn_id="turn-adapter-source",
                text="我最近在准备英语六级。",
                occurred_at="2026-07-21T12:00:00+08:00",
            ),
        ),
    )

    proposal = model.reflect(request)

    system_prompt = adapter.messages[0][0]["content"]
    serialized_request = adapter.messages[0][1]["content"]
    assert "最多提议 5 条" in system_prompt
    assert "fact_key" in system_prompt
    assert "explicit_statement" in system_prompt
    assert "我最近在准备英语六级。" in serialized_request
    assert '"turn_sources"' in serialized_request
    assert "payload_json" not in serialized_request
    assert proposal.proposed_user_facts[0]["source_quote"] == (
        "我最近在准备英语六级"
    )


def test_user_rejection_forgets_candidate_and_audits_the_decision(tmp_path):
    model = ExactQuoteCandidateModel()
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"candidate-rejection",
        reflection_model=model,
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-reject-candidate",
            subject=_subject(),
            request_digest="digest-reject-candidate",
            surface="voice",
            occurred_at="2026-07-21T13:00:00+08:00",
            source_text="我最近在准备英语六级。",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="我会先让你确认。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    _run_due_work(mind, now="2026-07-21T13:01:00+08:00")
    before = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T13:01:01+08:00",
        )
    )
    candidate = next(
        item
        for item in before.payload["diagnostics"]["evidence_timeline"]
        if item["source_kind"] == "conversation_candidate"
    )

    result = mind.apply_control(
        CompanionControlCommand(
            action="reject_candidate",
            subject=_subject(),
            payload={
                "evidence_id": candidate["evidence_id"],
                "now": "2026-07-21T13:02:00+08:00",
                "idempotency_key": "reject-candidate-1",
            },
        )
    )
    after = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T13:02:01+08:00",
        )
    )
    rejected = next(
        item
        for item in after.payload["diagnostics"]["evidence_timeline"]
        if item["evidence_id"] == candidate["evidence_id"]
    )

    assert result.forgotten == 1
    assert rejected["status"] == "forgotten"
    assert rejected["prompt_eligible"] is False
    assert rejected["attribution"] == "user_rejected_candidate"
    assert after.payload["diagnostics"]["observations"][0]["kind"] == (
        "memory_candidate_rejected"
    )
    assert after.payload["diagnostics"]["observations"][0]["evidence_ids"] == (
        candidate["evidence_id"],
    )


def test_fabricated_quote_is_rejected_and_short_term_source_is_deleted(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"candidate-fabricated-quote",
        reflection_model=FabricatedQuoteCandidateModel(),
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-fabricated-quote",
            subject=_subject(),
            request_digest="digest-fabricated-quote",
            surface="voice",
            occurred_at="2026-07-21T14:00:00+08:00",
            source_text="我今天只是去图书馆还书。",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="知道了。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )

    result = _run_due_work(mind, now="2026-07-21T14:01:00+08:00")
    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T14:01:01+08:00",
        )
    )

    assert result.failed == 1
    assert all(
        item["source_kind"] != "conversation_candidate"
        for item in operator.payload["diagnostics"]["evidence_timeline"]
    )
    assert operator.payload["diagnostics"]["health"][
        "temporary_turn_sources"
    ] == 0


def test_more_than_five_candidates_rejects_entire_job_without_writes(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"candidate-count-limit",
        reflection_model=TooManyCandidatesModel(),
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-too-many-candidates",
            subject=_subject(),
            request_digest="digest-too-many-candidates",
            surface="voice",
            occurred_at="2026-07-21T14:10:00+08:00",
            source_text="\u6211\u6700\u8fd1\u5728\u51c6\u5907\u82f1\u8bed\u516d\u7ea7\u3002",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="\u6536\u5230\u3002",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )

    result = _run_due_work(mind, now="2026-07-21T14:11:00+08:00")
    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T14:11:01+08:00",
        )
    )

    assert result.failed == 1
    assert all(
        item["source_kind"] != "conversation_candidate"
        for item in operator.payload["diagnostics"]["evidence_timeline"]
    )
    assert operator.payload["diagnostics"]["health"]["temporary_turn_sources"] == 0


def test_confirmed_candidate_supersedes_conflicting_active_fact(tmp_path):
    model = ExactQuoteCandidateModel()
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"candidate-conflict",
        reflection_model=model,
    )
    old = mind.observe(
        CompanionObservation(
            idempotency_key="goal-set:old-cet4",
            subject=_subject(),
            kind="goal_set",
            source_kind="miniprogram_companion",
            source_ref="english_cet6",
            occurred_at="2026-07-20T09:00:00+08:00",
            payload={
                "goal_id": "english_cet6",
                "title": "暂不准备英语六级",
                "status": "active",
            },
            safe_summary="用户明确设定了一项目标。",
        )
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-candidate-conflict",
            subject=_subject(),
            request_digest="digest-candidate-conflict",
            surface="voice",
            occurred_at="2026-07-21T15:00:00+08:00",
            source_text="我最近在准备英语六级。",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="这个变化需要你确认后才会替换旧记录。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    _run_due_work(mind, now="2026-07-21T15:01:00+08:00")
    before = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T15:01:01+08:00",
        )
    )
    candidate = next(
        item
        for item in before.payload["diagnostics"]["evidence_timeline"]
        if item["source_kind"] == "conversation_candidate"
    )

    mind.apply_control(
        CompanionControlCommand(
            action="confirm_candidate",
            subject=_subject(),
            payload={
                "evidence_id": candidate["evidence_id"],
                "now": "2026-07-21T15:02:00+08:00",
                "idempotency_key": "confirm-candidate-conflict",
            },
        )
    )
    after = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T15:02:01+08:00",
        )
    )
    timeline = {
        item["evidence_id"]: item
        for item in after.payload["diagnostics"]["evidence_timeline"]
    }

    assert timeline[old.evidence_ids[0]]["status"] == "superseded"
    assert timeline[candidate["evidence_id"]]["status"] == "active"
    assert any(
        item["source_evidence_id"] == old.evidence_ids[0]
        and item["target_evidence_id"] == candidate["evidence_id"]
        and item["relation_kind"] == "superseded_by"
        for item in after.payload["diagnostics"]["relations"]
    )


def test_deterministic_fact_superseding_candidate_scrubs_candidate_quote(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"candidate-deterministic-supersession",
        reflection_model=ExactQuoteCandidateModel(),
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-before-deterministic-fact",
            subject=_subject(),
            request_digest="digest-before-deterministic-fact",
            surface="voice",
            occurred_at="2026-07-21T15:05:00+08:00",
            source_text="\u6211\u6700\u8fd1\u5728\u51c6\u5907\u82f1\u8bed\u516d\u7ea7\u3002",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="\u6211\u4f1a\u5148\u8ba9\u4f60\u786e\u8ba4\u3002",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    _run_due_work(mind, now="2026-07-21T15:05:30+08:00")
    before = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T15:05:31+08:00",
        )
    )
    candidate = next(
        item
        for item in before.payload["diagnostics"]["evidence_timeline"]
        if item["source_kind"] == "conversation_candidate"
    )

    mind.observe(
        CompanionObservation(
            idempotency_key="goal-set:deterministic-cet6",
            subject=_subject(),
            kind="goal_set",
            source_kind="miniprogram_companion",
            source_ref="english_cet6",
            occurred_at="2026-07-21T15:06:00+08:00",
            payload={
                "goal_id": "english_cet6",
                "title": "\u590d\u4e60\u82f1\u8bed\u516d\u7ea7",
                "status": "active",
            },
            safe_summary="\u7528\u6237\u660e\u786e\u8bbe\u5b9a\u4e86\u82f1\u8bed\u516d\u7ea7\u76ee\u6807\u3002",
        )
    )

    with store.connection() as connection:
        row = connection.execute(
            """
            SELECT status,
                   json_extract(content_json, '$.source_quote') AS source_quote,
                   json_extract(content_json, '$.source_quote_digest') AS quote_digest
            FROM companion_evidence WHERE evidence_id = ?
            """,
            (candidate["evidence_id"],),
        ).fetchone()
    assert row["status"] == "superseded"
    assert row["source_quote"] is None
    assert len(row["quote_digest"]) == 64


def test_confirming_new_candidate_scrubs_quote_from_superseded_candidate(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"candidate-superseded-quote",
        reflection_model=ExactQuoteCandidateModel(),
    )
    for suffix, occurred_at, worker_at in (
        ("old", "2026-07-21T15:10:00+08:00", "2026-07-21T15:10:30+08:00"),
        ("new", "2026-07-21T15:11:00+08:00", "2026-07-21T15:11:30+08:00"),
    ):
        prepared = mind.prepare_turn(
            CompanionTurnRequest(
                turn_id=f"turn-candidate-{suffix}",
                subject=_subject(),
                request_digest=f"digest-candidate-{suffix}",
                surface="voice",
                occurred_at=occurred_at,
                source_text="\u6211\u6700\u8fd1\u5728\u51c6\u5907\u82f1\u8bed\u516d\u7ea7\u3002",
            )
        )
        mind.commit_turn(
            prepared,
            CompanionTurnOutcome(
                visible_response="\u6211\u4f1a\u5148\u8ba9\u4f60\u786e\u8ba4\u3002",
                assistant_action="reply",
                delivery_status="generated",
            ),
        )
        _run_due_work(mind, now=worker_at)

    before = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T15:11:31+08:00",
        )
    )
    candidates = {
        item["source_ref"]: item
        for item in before.payload["diagnostics"]["evidence_timeline"]
        if item["source_kind"] == "conversation_candidate"
    }
    old_candidate = candidates["turn-candidate-old"]
    new_candidate = candidates["turn-candidate-new"]

    mind.apply_control(
        CompanionControlCommand(
            action="confirm_candidate",
            subject=_subject(),
            payload={
                "evidence_id": new_candidate["evidence_id"],
                "now": "2026-07-21T15:12:00+08:00",
                "idempotency_key": "confirm-new-candidate-scrubs-old",
            },
        )
    )

    with store.connection() as connection:
        rows = connection.execute(
            """
            SELECT evidence_id, status,
                   json_extract(content_json, '$.source_quote') AS source_quote,
                   json_extract(content_json, '$.source_quote_digest') AS quote_digest
            FROM companion_evidence
            WHERE evidence_id IN (?, ?)
            """,
            (old_candidate["evidence_id"], new_candidate["evidence_id"]),
        ).fetchall()
    by_id = {row["evidence_id"]: row for row in rows}
    assert by_id[old_candidate["evidence_id"]]["status"] == "superseded"
    assert by_id[old_candidate["evidence_id"]]["source_quote"] is None
    assert len(by_id[old_candidate["evidence_id"]]["quote_digest"]) == 64
    assert by_id[new_candidate["evidence_id"]]["source_quote"] is None


def test_generic_forget_scrubs_quote_from_conversation_candidate(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"candidate-generic-forget",
        reflection_model=ExactQuoteCandidateModel(),
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-candidate-generic-forget",
            subject=_subject(),
            request_digest="digest-candidate-generic-forget",
            surface="voice",
            occurred_at="2026-07-21T15:20:00+08:00",
            source_text="\u6211\u6700\u8fd1\u5728\u51c6\u5907\u82f1\u8bed\u516d\u7ea7\u3002",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="\u6211\u4f1a\u5148\u8ba9\u4f60\u786e\u8ba4\u3002",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    _run_due_work(mind, now="2026-07-21T15:20:30+08:00")
    before = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T15:20:31+08:00",
        )
    )
    candidate = next(
        item
        for item in before.payload["diagnostics"]["evidence_timeline"]
        if item["source_kind"] == "conversation_candidate"
    )

    mind.apply_control(
        CompanionControlCommand(
            action="forget_evidence",
            subject=_subject(),
            payload={
                "evidence_id": candidate["evidence_id"],
                "now": "2026-07-21T15:21:00+08:00",
                "idempotency_key": "forget-conversation-candidate",
            },
        )
    )

    with store.connection() as connection:
        row = connection.execute(
            """
            SELECT status,
                   json_extract(content_json, '$.source_quote') AS source_quote,
                   json_extract(content_json, '$.source_quote_digest') AS quote_digest
            FROM companion_evidence WHERE evidence_id = ?
            """,
            (candidate["evidence_id"],),
        ).fetchone()
    assert row["status"] == "forgotten"
    assert row["source_quote"] is None
    assert len(row["quote_digest"]) == 64


def test_correcting_conversation_candidate_scrubs_quote_from_old_evidence(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"candidate-correction",
        reflection_model=ExactQuoteCandidateModel(),
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-candidate-correction",
            subject=_subject(),
            request_digest="digest-candidate-correction",
            surface="voice",
            occurred_at="2026-07-21T15:25:00+08:00",
            source_text="\u6211\u6700\u8fd1\u5728\u51c6\u5907\u82f1\u8bed\u516d\u7ea7\u3002",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="\u6211\u4f1a\u5148\u8ba9\u4f60\u786e\u8ba4\u3002",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    _run_due_work(mind, now="2026-07-21T15:25:30+08:00")
    before = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T15:25:31+08:00",
        )
    )
    candidate = next(
        item
        for item in before.payload["diagnostics"]["evidence_timeline"]
        if item["source_kind"] == "conversation_candidate"
    )

    result = mind.apply_control(
        CompanionControlCommand(
            action="correct_evidence",
            subject=_subject(),
            payload={
                "evidence_id": candidate["evidence_id"],
                "replacement_content": {"value": "\u590d\u4e60\u82f1\u8bed\u516d\u7ea7"},
                "source_summary": "\u7528\u6237\u66f4\u6b63\u4e86\u5019\u9009\u5185\u5bb9\u3002",
                "now": "2026-07-21T15:26:00+08:00",
                "idempotency_key": "correct-conversation-candidate",
            },
        )
    )

    with store.connection() as connection:
        old = connection.execute(
            """
            SELECT status,
                   json_extract(content_json, '$.source_quote') AS source_quote,
                   json_extract(content_json, '$.source_quote_digest') AS quote_digest
            FROM companion_evidence WHERE evidence_id = ?
            """,
            (candidate["evidence_id"],),
        ).fetchone()
        replacement = connection.execute(
            """
            SELECT status, prompt_eligible
            FROM companion_evidence
            WHERE source_ref = 'control:correct-conversation-candidate'
            """
        ).fetchone()
    assert result.deactivated == 1
    assert old["status"] == "superseded"
    assert old["source_quote"] is None
    assert len(old["quote_digest"]) == 64
    assert replacement["status"] == "active"
    assert replacement["prompt_eligible"] == 1


def test_personal_memory_purge_deletes_pending_short_term_turn_source(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"candidate-source-purge",
        reflection_model=ExactQuoteCandidateModel(),
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-source-before-purge",
            subject=_subject(),
            request_digest="digest-source-before-purge",
            surface="voice",
            occurred_at="2026-07-21T15:30:00+08:00",
            source_text="\u6211\u6700\u8fd1\u5728\u51c6\u5907\u82f1\u8bed\u516d\u7ea7\u3002",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="\u6536\u5230\u3002",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    before = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T15:30:01+08:00",
        )
    )
    assert before.payload["diagnostics"]["health"]["temporary_turn_sources"] == 1

    mind.apply_control(
        CompanionControlCommand(
            action="purge_personal_memory",
            subject=_subject(),
            payload={
                "now": "2026-07-21T15:31:00+08:00",
                "idempotency_key": "purge-pending-turn-source",
            },
        )
    )
    after = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T15:31:01+08:00",
        )
    )

    assert after.payload["diagnostics"]["health"]["temporary_turn_sources"] == 0


def test_relationship_reset_deletes_sources_for_cancelled_candidate_jobs(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"candidate-source-reset",
        reflection_model=ExactQuoteCandidateModel(),
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-source-before-reset",
            subject=_subject(),
            request_digest="digest-source-before-reset",
            surface="voice",
            occurred_at="2026-07-21T15:40:00+08:00",
            source_text="\u6211\u6700\u8fd1\u5728\u51c6\u5907\u82f1\u8bed\u516d\u7ea7\u3002",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="\u6536\u5230\u3002",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )

    mind.apply_control(
        CompanionControlCommand(
            action="reset_relationship",
            subject=_subject(),
            payload={
                "now": "2026-07-21T15:41:00+08:00",
                "idempotency_key": "reset-pending-turn-source",
            },
        )
    )
    after = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T15:41:01+08:00",
        )
    )

    assert after.payload["diagnostics"]["health"]["temporary_turn_sources"] == 0


def test_temporary_model_failure_retries_without_losing_short_term_source(tmp_path):
    model = RetryThenCandidateModel()
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"candidate-retry",
        reflection_model=model,
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-candidate-retry",
            subject=_subject(),
            request_digest="digest-candidate-retry",
            surface="voice",
            occurred_at="2026-07-21T16:00:00+08:00",
            source_text="我最近在准备英语六级。",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="收到。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )

    first = _run_due_work(mind, now="2026-07-21T16:01:00+08:00")
    during_retry = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T16:01:01+08:00",
        )
    )
    early = _run_due_work(mind, now="2026-07-21T16:01:29+08:00")
    due = _run_due_work(mind, now="2026-07-21T16:01:30+08:00")
    after = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T16:01:31+08:00",
        )
    )

    assert first.retried == 1
    assert during_retry.payload["diagnostics"]["health"][
        "temporary_turn_sources"
    ] == 1
    assert early.claimed == 0
    assert due.succeeded == 1
    assert after.payload["diagnostics"]["health"][
        "temporary_turn_sources"
    ] == 0
    assert any(
        item["source_kind"] == "conversation_candidate"
        for item in after.payload["diagnostics"]["evidence_timeline"]
    )


def test_expired_turn_source_is_cleaned_without_calling_remote_model(tmp_path):
    model = ExactQuoteCandidateModel()
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"candidate-source-expiry",
        reflection_model=model,
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-source-expiry",
            subject=_subject(),
            request_digest="digest-source-expiry",
            surface="voice",
            occurred_at="2026-07-21T17:00:00+08:00",
            source_text="我最近在准备英语六级。",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="收到。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )

    result = _run_due_work(mind, now="2026-07-22T17:00:01+08:00")
    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-22T17:00:02+08:00",
        )
    )

    assert result.succeeded == 1
    assert model.requests == []
    assert operator.payload["diagnostics"]["health"][
        "temporary_turn_sources"
    ] == 0
    assert all(
        item["source_kind"] != "conversation_candidate"
        for item in operator.payload["diagnostics"]["evidence_timeline"]
    )


def test_disabled_candidate_worker_never_persists_raw_turn_source(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"candidate-worker-disabled",
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-worker-disabled",
            subject=_subject(),
            request_digest="digest-worker-disabled",
            surface="voice",
            occurred_at="2026-07-21T18:00:00+08:00",
            source_text="这是不应该被暂存的原文。",
        )
    )

    committed = mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="收到。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T18:00:01+08:00",
        )
    )

    assert prepared.source_text is None
    assert committed.job_ids == ()
    assert operator.payload["diagnostics"]["health"][
        "temporary_turn_sources"
    ] == 0


@pytest.mark.parametrize(
    ("source_text", "quote", "value", "claim_type"),
    (
        ("室友说他下周要比赛。", "室友说他下周要比赛", "下周要比赛", "reported_speech"),
        ("如果明天下雨我就不去跑步。", "如果明天下雨我就不去跑步", "不去跑步", "hypothetical"),
        ("我没有打算考研。", "我没有打算考研", "打算考研", "negated"),
        ("我昨晚梦到自己去了北京。", "我昨晚梦到自己去了北京", "去了北京", "dream"),
        ("开玩笑，我要当宇航员。", "开玩笑，我要当宇航员", "当宇航员", "joke"),
        ("刚才识别错了，我不是要退学。", "刚才识别错了，我不是要退学", "要退学", "asr_uncertain"),
        ("最近每天都在图书馆。", "最近每天都在图书馆", "每天都在图书馆", "inference"),
    ),
)
def test_risky_or_nonliteral_claims_remain_non_prompting_candidates(
    tmp_path,
    source_text,
    quote,
    value,
    claim_type,
):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / f"{claim_type}.db"),
        token_secret=f"candidate-{claim_type}".encode(),
        reflection_model=RiskyClaimCandidateModel(
            value=value,
            quote=quote,
            claim_type=claim_type,
        ),
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id=f"turn-{claim_type}",
            subject=_subject(),
            request_digest=f"digest-{claim_type}",
            surface="voice",
            occurred_at="2026-07-21T19:00:00+08:00",
            source_text=source_text,
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="我不会直接把这句话当成事实。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    result = _run_due_work(mind, now="2026-07-21T19:01:00+08:00")
    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T19:01:01+08:00",
        )
    )
    candidate = next(
        item
        for item in operator.payload["diagnostics"]["evidence_timeline"]
        if item["source_kind"] == "conversation_candidate"
    )

    assert result.succeeded == 1
    assert candidate["status"] == "candidate"
    assert candidate["prompt_eligible"] is False
    assert candidate["attribution"] == f"candidate_{claim_type}"


def test_unresolved_candidate_expires_and_scrubs_its_source_quote(tmp_path):
    store = CompanionStore(tmp_path / "candidate-expiry.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"candidate-expiry",
        reflection_model=ExactQuoteCandidateModel(),
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-candidate-expiry",
            subject=_subject(),
            request_digest="digest-candidate-expiry",
            surface="voice",
            occurred_at="2026-07-21T20:00:00+08:00",
            source_text="我最近在准备英语六级。",
        )
    )
    mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="收到。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    _run_due_work(mind, now="2026-07-21T20:01:00+08:00")

    _run_due_work(mind, now="2026-08-21T20:01:01+08:00")
    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-08-21T20:01:02+08:00",
        )
    )
    candidate = next(
        item
        for item in operator.payload["diagnostics"]["evidence_timeline"]
        if item["source_kind"] == "conversation_candidate"
    )
    with store.connection() as connection:
        source_quote = connection.execute(
            """
            SELECT json_extract(content_json, '$.source_quote')
            FROM companion_evidence WHERE evidence_id = ?
            """,
            (candidate["evidence_id"],),
        ).fetchone()[0]

    assert candidate["status"] == "expired"
    assert source_quote is None
