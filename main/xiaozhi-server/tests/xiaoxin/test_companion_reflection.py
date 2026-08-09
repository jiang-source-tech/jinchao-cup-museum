from __future__ import annotations

import asyncio
import time

import pytest

from core.xiaoxin.companion.adapters import LLMReflectionModel
from core.xiaoxin.companion import (
    CompanionMind,
    CompanionSubjectContext,
    CompanionTurnOutcome,
    CompanionTurnRequest,
)
from core.xiaoxin.companion.reflection import (
    AdjustmentProposal,
    ChapterStatementProposal,
    ReflectionEvidence,
    ReflectionProposal,
    ReflectionRequest,
    ReflectionValidationError,
    validate_reflection_proposal,
)
from core.xiaoxin.companion.store import CompanionStore


def _run_due_work(mind, **kwargs):
    return asyncio.run(mind.run_due_work(**kwargs))


def _request() -> ReflectionRequest:
    return ReflectionRequest(
        job_id="job-1",
        job_kind="session_consolidation",
        pet_id="pet-1",
        relationship_epoch_id="epoch-1",
        evidence=(
            ReflectionEvidence(
                evidence_id="evidence-1",
                kind="meaningful_moment",
                ownership_scope="relationship",
                source_summary="本轮形成了明确帮助结果。",
                confidence=1.0,
            ),
        ),
    )


@pytest.mark.parametrize(
    ("proposal", "message"),
    (
        (
            ReflectionProposal(
                schema_version="wrong-schema",
                safe_summary="无。",
            ),
            "schema",
        ),
        (
            ReflectionProposal(
                schema_version="companion-reflection-proposal-v1",
                safe_summary="   ",
            ),
            "safe_summary",
        ),
        (
            ReflectionProposal(
                schema_version="companion-reflection-proposal-v1",
                safe_summary="无。",
                evidence_ids=("foreign-evidence",),
            ),
            "unavailable Evidence",
        ),
        (
            ReflectionProposal(
                schema_version="companion-reflection-proposal-v1",
                safe_summary="无。",
                adjustments=(
                    AdjustmentProposal(
                        dimension="personality_type",
                        value="fixed",
                        scope="conversation",
                        evidence_ids=("evidence-1",),
                        confidence=0.9,
                    ),
                ),
            ),
            "dimension",
        ),
        (
            ReflectionProposal(
                schema_version="companion-reflection-proposal-v1",
                safe_summary="无。",
                adjustments=(
                    AdjustmentProposal(
                        dimension="response_length",
                        value="extremely_verbose",
                        scope="conversation",
                        evidence_ids=("evidence-1",),
                        confidence=0.9,
                    ),
                ),
            ),
            "value",
        ),
        (
            ReflectionProposal(
                schema_version="companion-reflection-proposal-v1",
                safe_summary="无。",
                adjustments=(
                    AdjustmentProposal(
                        dimension="response_length",
                        value="short",
                        scope="private_database",
                        evidence_ids=("evidence-1",),
                        confidence=0.9,
                    ),
                ),
            ),
            "scope",
        ),
        (
            ReflectionProposal(
                schema_version="companion-reflection-proposal-v1",
                safe_summary="无。",
                adjustments=(
                    AdjustmentProposal(
                        dimension="response_length",
                        value="short",
                        scope="conversation",
                        evidence_ids=(),
                        confidence=0.9,
                    ),
                ),
            ),
            "Evidence IDs",
        ),
        (
            ReflectionProposal(
                schema_version="companion-reflection-proposal-v1",
                safe_summary="无。",
                adjustments=(
                    AdjustmentProposal(
                        dimension="response_length",
                        value="short",
                        scope="conversation",
                        evidence_ids=("evidence-1",),
                        confidence=0.9,
                    ),
                    AdjustmentProposal(
                        dimension="response_length",
                        value="standard",
                        scope="conversation",
                        evidence_ids=("evidence-1",),
                        confidence=0.9,
                    ),
                ),
            ),
            "duplicate",
        ),
        (
            ReflectionProposal(
                schema_version="companion-reflection-proposal-v1",
                safe_summary="无。",
                proposed_user_facts=({"fact": "用户一定讨厌集体活动"},),
            ),
            "user facts",
        ),
    ),
)
def test_reflection_validator_rejects_entire_invalid_proposal(proposal, message):
    with pytest.raises(ReflectionValidationError, match=message):
        validate_reflection_proposal(_request(), proposal)


def test_chapter_statement_scope_must_match_cited_evidence_ownership():
    request = ReflectionRequest(
        job_id="job-chapter-attribution",
        job_kind="academic_stage_changed",
        pet_id="pet-1",
        relationship_epoch_id="epoch-1",
        evidence=(
            ReflectionEvidence(
                evidence_id="user-evidence",
                kind="user_life_event",
                ownership_scope="user",
                source_summary="用户完成了自己设定的目标。",
                confidence=1.0,
            ),
            ReflectionEvidence(
                evidence_id="relationship-evidence",
                kind="meaningful_moment",
                ownership_scope="relationship",
                source_summary="用户确认本轮互动有帮助。",
                confidence=1.0,
            ),
        ),
    )
    proposal = ReflectionProposal(
        schema_version="companion-reflection-proposal-v1",
        safe_summary="小芯和用户共同完成了用户自己的目标。",
        evidence_ids=("user-evidence", "relationship-evidence"),
        chapter_statements=(
            ChapterStatementProposal(
                claim_scope="shared_experience",
                evidence_ids=("user-evidence", "relationship-evidence"),
            ),
        ),
    )

    with pytest.raises(ReflectionValidationError, match="ownership"):
        validate_reflection_proposal(request, proposal)


class PartiallyInvalidReflectionModel:
    def reflect(self, request):
        evidence_id = request.evidence[0].evidence_id
        return ReflectionProposal(
            schema_version="companion-reflection-proposal-v1",
            safe_summary="建议调整。",
            adjustments=(
                AdjustmentProposal(
                    dimension="response_length",
                    value="short",
                    scope="conversation",
                    evidence_ids=(evidence_id,),
                    confidence=0.9,
                ),
                AdjustmentProposal(
                    dimension="personality_type",
                    value="fixed",
                    scope="conversation",
                    evidence_ids=(evidence_id,),
                    confidence=0.9,
                ),
            ),
        )


class StaleEvidenceReflectionModel:
    def __init__(self, evidence_id: str) -> None:
        self.evidence_id = evidence_id
        self.calls = []

    def reflect(self, request):
        self.calls.append(request)
        return ReflectionProposal(
            schema_version="companion-reflection-proposal-v1",
            safe_summary="建议调整。",
            adjustments=(
                AdjustmentProposal(
                    dimension="response_length",
                    value="short",
                    scope="conversation",
                    evidence_ids=(self.evidence_id,),
                    confidence=0.9,
                ),
            ),
        )


def test_worker_rejects_invalid_proposal_without_partial_domain_writes(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"reflection-test-secret",
        reflection_model=PartiallyInvalidReflectionModel(),
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-invalid-proposal",
            subject=subject,
            request_digest="digest-invalid-proposal",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    committed = mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="收到。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=(
                {
                    "kind": "meaningful_moment",
                    "ownership_scope": "relationship",
                    "content": {"outcome": "helpful"},
                    "source_summary": "本轮形成了明确帮助结果。",
                    "attribution": "observed_interaction",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
            ),
        ),
    )

    result = _run_due_work(mind, now="2026-07-18T10:01:00+08:00", limit=20)

    assert result.failed == 1
    assert result.retried == 0
    with store.connection() as connection:
        job = connection.execute(
            "SELECT status FROM consolidation_jobs WHERE job_id = ?",
            (committed.job_ids[0],),
        ).fetchone()
        adjustment_count = connection.execute(
            "SELECT COUNT(*) FROM companion_adjustments"
        ).fetchone()[0]
        chapter_count = connection.execute(
            "SELECT COUNT(*) FROM companion_chapters"
        ).fetchone()[0]
    assert job["status"] == "failed"
    assert adjustment_count == 0
    assert chapter_count == 0


def test_forgotten_evidence_cannot_be_reactivated_by_reflection(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = StaleEvidenceReflectionModel("forgotten-evidence")
    mind = CompanionMind(
        store=store,
        token_secret=b"reflection-test-secret",
        reflection_model=model,
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-forgotten-reflection",
            subject=subject,
            request_digest="digest-forgotten-reflection",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    committed = mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="收到。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=(
                {
                    "kind": "meaningful_moment",
                    "ownership_scope": "relationship",
                    "content": {"outcome": "helpful"},
                    "source_summary": "本轮形成了明确帮助结果。",
                    "attribution": "observed_interaction",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
            ),
        ),
    )
    forgotten_evidence_id = committed.evidence_ids[0]
    model.evidence_id = forgotten_evidence_id
    with store.connection() as connection:
        connection.execute(
            """
            UPDATE companion_evidence
            SET status = 'forgotten', prompt_eligible = 0
            WHERE evidence_id = ?
            """,
            (forgotten_evidence_id,),
        )
        connection.commit()

    result = _run_due_work(mind, now="2026-07-18T10:01:00+08:00", limit=20)

    assert result.failed == 1
    assert model.calls == []
    with store.connection() as connection:
        adjustment_count = connection.execute(
            "SELECT COUNT(*) FROM companion_adjustments"
        ).fetchone()[0]
    assert adjustment_count == 0


class JsonAdapter:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = []

    def complete_chat(self, messages, max_tokens=None, temperature=None, **kwargs):
        self.calls.append(messages)
        return self.response


class SlowJsonAdapter(JsonAdapter):
    def complete_chat(self, messages, max_tokens=None, temperature=None, **kwargs):
        self.calls.append(messages)
        time.sleep(0.05)
        return self.response


def test_llm_reflection_adapter_uses_safe_input_and_rejects_extra_reasoning_fields():
    adapter = JsonAdapter(
        """
        {
          "schema_version": "companion-reflection-proposal-v1",
          "safe_summary": "无长期变化。",
          "evidence_ids": [],
          "adjustments": [],
          "proposed_user_facts": [],
          "chapter_statements": [],
          "chain_of_thought": "不应被接受"
        }
        """
    )
    model = LLMReflectionModel(adapter, timeout_seconds=1)

    with pytest.raises(ReflectionValidationError, match="unexpected fields"):
        model.reflect(_request())

    serialized_request = adapter.calls[0][1]["content"]
    assert "本轮形成了明确帮助结果。" in serialized_request
    assert "chain_of_thought" not in adapter.calls[0][0]["content"]
    assert "content_json" not in serialized_request
    assert '"response_length"' in adapter.calls[0][0]["content"]
    assert '"proposed_user_facts":[]' in adapter.calls[0][0]["content"]


def test_llm_reflection_adapter_enforces_its_own_timeout():
    adapter = SlowJsonAdapter(
        """
        {
          "schema_version": "companion-reflection-proposal-v1",
          "safe_summary": "无长期变化。",
          "evidence_ids": [],
          "adjustments": [],
          "proposed_user_facts": [],
          "chapter_statements": []
        }
        """
    )
    model = LLMReflectionModel(adapter, timeout_seconds=0.001)

    with pytest.raises(TimeoutError, match="timed out"):
        model.reflect(_request())
