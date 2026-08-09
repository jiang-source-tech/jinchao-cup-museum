import asyncio

import json

import pytest

from core.xiaoxin.companion import (
    CompanionControlCommand,
    CompanionEvidence,
    CompanionMind,
    CompanionObservation,
    CompanionProjectionRequest,
    CompanionSubjectContext,
    CompanionTurnOutcome,
    CompanionTurnRequest,
)
from core.xiaoxin.companion.store import SCHEMA_VERSION, CompanionStore
from core.xiaoxin.companion.reflection import ReflectionProposal


def _run_due_work(mind, **kwargs):
    return asyncio.run(mind.run_due_work(**kwargs))


def _subject() -> CompanionSubjectContext:
    return CompanionSubjectContext(
        owner_user_id="owner-retrieval",
        pet_id="pet-retrieval",
        memory_subject_id="subject-retrieval",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )


def _other_subject() -> CompanionSubjectContext:
    return CompanionSubjectContext(
        owner_user_id="owner-retrieval-other",
        pet_id="pet-retrieval-other",
        memory_subject_id="subject-retrieval-other",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )


def _commit_fact(
    mind: CompanionMind,
    *,
    turn_id: str,
    occurred_at: str,
    fact_key: str,
    value: str,
    summary: str,
    subject: CompanionSubjectContext | None = None,
) -> str:
    subject = subject or _subject()
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id=turn_id,
            subject=subject,
            request_digest=f"digest-{turn_id}",
            surface="voice",
            occurred_at=occurred_at,
        )
    )
    committed = mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="\u6536\u5230\u3002",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=(
                {
                    "kind": "profile_fact",
                    "ownership_scope": "user",
                    "content": {"fact_key": fact_key, "value": value},
                    "source_summary": summary,
                    "attribution": "explicit_user_statement",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
            ),
        ),
    )
    return committed.evidence_ids[0]


def test_explicit_fact_hint_outranks_newer_unrelated_evidence(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"retrieval-exact-fact",
    )
    origin_id = _commit_fact(
        mind,
        turn_id="turn-origin",
        occurred_at="2026-07-01T09:00:00+08:00",
        fact_key="origin",
        value="\u6b66\u6c49",
        summary="\u7528\u6237\u660e\u786e\u8868\u793a\u81ea\u5df1\u6765\u81ea\u6b66\u6c49\u3002",
    )
    _commit_fact(
        mind,
        turn_id="turn-newer-preference",
        occurred_at="2026-07-20T09:00:00+08:00",
        fact_key="response_length",
        value="short",
        summary="\u7528\u6237\u660e\u786e\u504f\u597d\u7b80\u77ed\u56de\u7b54\u3002",
    )

    recalled = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-recall-origin",
            subject=_subject(),
            request_digest="digest-recall-origin",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
            interaction_kind="explicit_recall",
            retrieval_query="\u6211\u6765\u81ea\u54ea\u91cc",
            retrieval_hints={"fact_keys": ("origin",)},
        )
    )

    assert recalled.used_evidence_ids == (origin_id,)
    recalled_item = json.loads(recalled.prompt_context[0])
    assert recalled_item == {
        "fact": "\u7528\u6237\u660e\u786e\u8868\u793a\u81ea\u5df1\u6765\u81ea\u6b66\u6c49\u3002",
        "fact_key": "origin",
        "kind": "profile_fact",
    }


def test_chinese_fts_query_outranks_newer_unrelated_evidence(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"retrieval-chinese-fts",
    )
    library_id = _commit_fact(
        mind,
        turn_id="turn-library-preference",
        occurred_at="2026-07-01T09:00:00+08:00",
        fact_key="study_place",
        value="\u5b89\u9759\u7684\u56fe\u4e66\u9986",
        summary="\u7528\u6237\u559c\u6b22\u5728\u5b89\u9759\u7684\u56fe\u4e66\u9986\u5b66\u4e60\u3002",
    )
    _commit_fact(
        mind,
        turn_id="turn-newer-breakfast",
        occurred_at="2026-07-20T09:00:00+08:00",
        fact_key="breakfast",
        value="\u8c46\u6d46",
        summary="\u7528\u6237\u65e9\u9910\u559c\u6b22\u559d\u8c46\u6d46\u3002",
    )

    recalled = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-recall-library",
            subject=_subject(),
            request_digest="digest-recall-library",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
            interaction_kind="explicit_recall",
            retrieval_query="\u6211\u559c\u6b22\u7684\u5b89\u9759\u7684\u56fe\u4e66\u9986",
        )
    )

    assert recalled.used_evidence_ids == (library_id,)


def test_operator_retrieval_audit_contains_only_digests_ids_and_scores(tmp_path):
    secret_query = "\u6211\u559c\u6b22\u54ea\u4e2a\u5b89\u9759\u7684\u56fe\u4e66\u9986"
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"retrieval-audit",
    )
    evidence_id = _commit_fact(
        mind,
        turn_id="turn-audited-fact",
        occurred_at="2026-07-01T09:00:00+08:00",
        fact_key="study_place",
        value="\u5b89\u9759\u7684\u56fe\u4e66\u9986",
        summary="\u7528\u6237\u559c\u6b22\u5728\u5b89\u9759\u7684\u56fe\u4e66\u9986\u5b66\u4e60\u3002",
    )
    mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-audited-recall",
            subject=_subject(),
            request_digest="digest-audited-recall",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
            interaction_kind="explicit_recall",
            retrieval_query=secret_query,
            retrieval_hints={"fact_keys": ("study_place",)},
        )
    )

    projection = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T09:00:01+08:00",
        )
    )
    audit = projection.payload["diagnostics"]["retrieval_audits"][0]
    serialized = repr(audit)

    assert audit["turn_id"] == "turn-audited-recall"
    assert audit["selected_evidence_ids"] == (evidence_id,)
    assert len(audit["query_digest"]) == 64
    assert len(audit["hints_digest"]) == 64
    assert audit["candidate_count"] >= 1
    assert audit["duration_ms"] >= 0
    assert secret_query not in serialized
    assert "\u5b89\u9759\u7684\u56fe\u4e66\u9986" not in serialized


def test_explicit_recall_does_not_rotate_equally_relevant_evidence(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"retrieval-recent-reference",
    )
    evidence_ids = []
    for goal_id, occurred_at, title in (
        ("older", "2026-07-19T09:00:00+08:00", "\u5b66\u4e60\u6570\u636e\u5e93"),
        ("newer", "2026-07-20T09:00:00+08:00", "\u5b66\u4e60\u7f51\u7edc\u5b89\u5168"),
    ):
        result = mind.observe(
            CompanionObservation(
                idempotency_key=f"goal-{goal_id}",
                subject=_subject(),
                kind="goal_set",
                source_kind="miniprogram_companion",
                source_ref=goal_id,
                occurred_at=occurred_at,
                payload={
                    "goal_id": goal_id,
                    "title": title,
                    "status": "active",
                },
                safe_summary=f"\u7528\u6237\u8bbe\u5b9a\u4e86{title}\u7684\u5b66\u4e60\u76ee\u6807\u3002",
            )
        )
        evidence_ids.append(result.evidence_ids[0])

    first = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-goal-recall-first",
            subject=_subject(),
            request_digest="digest-goal-recall-first",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
            interaction_kind="explicit_recall",
            retrieval_query="\u6211\u7684\u5b66\u4e60\u76ee\u6807",
            retrieval_hints={"kinds": ("goal",)},
        )
    )
    second = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-goal-recall-second",
            subject=_subject(),
            request_digest="digest-goal-recall-second",
            surface="voice",
            occurred_at="2026-07-21T09:01:00+08:00",
            interaction_kind="explicit_recall",
            retrieval_query="\u6211\u7684\u5b66\u4e60\u76ee\u6807",
            retrieval_hints={"kinds": ("goal",)},
        )
    )

    assert first.used_evidence_ids == (evidence_ids[1],)
    assert second.used_evidence_ids == (evidence_ids[1],)


def test_time_range_hint_excludes_older_matching_evidence(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"retrieval-time-range",
    )
    old_id = _commit_fact(
        mind,
        turn_id="turn-old-project",
        occurred_at="2026-06-01T09:00:00+08:00",
        fact_key="project:old",
        value="\u65e7\u9879\u76ee",
        summary="\u7528\u6237\u5728\u516d\u6708\u5b8c\u6210\u4e86\u65e7\u9879\u76ee\u3002",
    )
    new_id = _commit_fact(
        mind,
        turn_id="turn-new-project",
        occurred_at="2026-07-20T09:00:00+08:00",
        fact_key="project:new",
        value="\u65b0\u9879\u76ee",
        summary="\u7528\u6237\u5728\u4e03\u6708\u5b8c\u6210\u4e86\u65b0\u9879\u76ee\u3002",
    )

    recalled = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-recall-july-project",
            subject=_subject(),
            request_digest="digest-recall-july-project",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
            interaction_kind="explicit_recall",
            retrieval_query="\u4e03\u6708\u7684\u9879\u76ee",
            retrieval_hints={"time_from": "2026-07-01T00:00:00+08:00"},
        )
    )

    assert old_id not in recalled.used_evidence_ids
    assert recalled.used_evidence_ids == (new_id,)


def test_general_qa_does_not_retrieve_or_create_private_retrieval_audit(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"retrieval-general-qa",
    )
    _commit_fact(
        mind,
        turn_id="turn-private-origin",
        occurred_at="2026-07-20T09:00:00+08:00",
        fact_key="origin",
        value="\u6b66\u6c49",
        summary="\u7528\u6237\u660e\u786e\u8868\u793a\u81ea\u5df1\u6765\u81ea\u6b66\u6c49\u3002",
    )

    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-general-weather",
            subject=_subject(),
            request_digest="digest-general-weather",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
            interaction_kind="general_qa",
            retrieval_query="\u6b66\u6c49\u4eca\u5929\u5929\u6c14\u600e\u4e48\u6837",
        )
    )
    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T09:00:01+08:00",
        )
    )

    assert prepared.used_evidence_ids == ()
    assert operator.payload["diagnostics"]["retrieval_audits"] == ()


def test_recent_conversation_requires_an_explicit_continuity_kind(tmp_path):
    database_path = tmp_path / "recent-conversation-boundary.db"
    mind = CompanionMind(
        store=CompanionStore(database_path),
        token_secret=b"recent-conversation-boundary",
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-recent-context",
            subject=_subject(),
            request_digest="digest-recent-context",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
        )
    )
    committed = mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="我在听。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=(
                {
                    "kind": "recent_conversation",
                    "ownership_scope": "user",
                    "content": {
                        "canonical_value": "用户此前说：准备重做电路分析错题。"
                    },
                    "source_summary": "用户此前说：准备重做电路分析错题。",
                    "attribution": "explicit_user_statement",
                    "confidence": 1.0,
                    "retention": "short_term",
                    "prompt_eligible": True,
                    "expires_at": "2026-07-28T09:00:00+08:00",
                },
            ),
        ),
    )
    evidence_id = committed.evidence_ids[0]

    ordinary = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-ordinary-recall",
            subject=_subject(),
            request_digest="digest-ordinary-recall",
            surface="voice",
            occurred_at="2026-07-21T10:00:00+08:00",
            interaction_kind="explicit_recall",
            retrieval_query="电路分析错题",
        )
    )
    continuity, _ = mind._recall_companion_memory(
        ordinary,
        query="电路分析错题",
        kinds=("recent_conversation",),
        minimum_memory_reference_budget=1,
    )

    assert ordinary.used_evidence_ids == ()
    assert continuity.used_evidence_ids == (evidence_id,)
    continuity_item = json.loads(continuity.prompt_context[0])
    assert continuity_item["fact"] == "用户此前说：准备重做电路分析错题。"
    assert continuity_item["kind"] == "recent_conversation"


def test_initiative_surface_never_retrieves_sensitive_evidence(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"retrieval-initiative-sensitive")
    bootstrap = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-sensitive-bootstrap",
            subject=_subject(),
            request_digest="digest-sensitive-bootstrap",
            surface="voice",
            occurred_at="2026-07-20T08:00:00+08:00",
        )
    )
    mind.commit_turn(
        bootstrap,
        CompanionTurnOutcome(
            visible_response="\u6536\u5230\u3002",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-sensitive-facts",
            subject=_subject(),
            request_digest="digest-sensitive-facts",
            surface="voice",
            occurred_at="2026-07-20T09:00:00+08:00",
        )
    )
    store.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="\u6536\u5230\u3002",
            assistant_action="reply",
            delivery_status="generated",
        ),
        evidence=(
            CompanionEvidence(
                evidence_id="low-study-place",
                pet_id=_subject().pet_id,
                memory_subject_id=_subject().memory_subject_id,
                ownership_scope="user",
                relationship_epoch_id=None,
                kind="preference",
                content={"value": "\u56fe\u4e66\u9986"},
                source_kind="test",
                source_ref="low-study-place",
                source_summary="\u7528\u6237\u559c\u6b22\u5728\u56fe\u4e66\u9986\u5b66\u4e60\u3002",
                attribution="explicit_user_statement",
                confidence=1.0,
                occurred_at="2026-07-20T08:30:00+08:00",
                retention="long_term",
                status="active",
                prompt_eligible=True,
                fact_key="study_place:library",
                sensitivity="low",
            ),
            CompanionEvidence(
                evidence_id="sensitive-study-place",
                pet_id=_subject().pet_id,
                memory_subject_id=_subject().memory_subject_id,
                ownership_scope="user",
                relationship_epoch_id=None,
                kind="wellbeing",
                content={"value": "\u5fc3\u7406\u54a8\u8be2\u5ba4"},
                source_kind="test",
                source_ref="sensitive-study-place",
                source_summary="\u7528\u6237\u66fe\u5728\u5fc3\u7406\u54a8\u8be2\u5ba4\u5b66\u4e60\u3002",
                attribution="explicit_user_statement",
                confidence=1.0,
                occurred_at="2026-07-20T08:45:00+08:00",
                retention="long_term",
                status="active",
                prompt_eligible=True,
                fact_key="study_place:counselling",
                sensitivity="sensitive",
            ),
        ),
    )

    recalled = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-initiative-study-place",
            subject=_subject(),
            request_digest="digest-initiative-study-place",
            surface="initiative",
            occurred_at="2026-07-21T09:00:00+08:00",
            interaction_kind="explicit_recall",
            retrieval_query="\u56fe\u4e66\u9986\u5b66\u4e60\u5730\u70b9",
        )
    )

    assert recalled.used_evidence_ids == ("low-study-place",)
    assert "sensitive-study-place" not in recalled.used_evidence_ids


def test_schema_v8_migration_backfills_fts_and_reaches_current_schema(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    store = CompanionStore(database_path)
    mind = CompanionMind(store=store, token_secret=b"retrieval-v9-migration")
    existing_id = _commit_fact(
        mind,
        turn_id="turn-before-v9",
        occurred_at="2026-07-01T09:00:00+08:00",
        fact_key="study_place",
        value="\u5b89\u9759\u7684\u56fe\u4e66\u9986",
        summary="\u7528\u6237\u559c\u6b22\u5728\u5b89\u9759\u7684\u56fe\u4e66\u9986\u5b66\u4e60\u3002",
    )
    with store.connection() as connection:
        connection.executescript(
            """
            DROP TRIGGER trg_companion_evidence_fts_insert;
            DROP TRIGGER trg_companion_evidence_fts_update;
            DROP TRIGGER trg_companion_evidence_fts_delete;
            DROP TABLE companion_evidence_fts;
            PRAGMA user_version = 8;
            """
        )
        connection.commit()

    migrated_store = CompanionStore(database_path)
    migrated_mind = CompanionMind(
        store=migrated_store,
        token_secret=b"retrieval-v9-migrated",
    )
    new_id = _commit_fact(
        migrated_mind,
        turn_id="turn-after-v9",
        occurred_at="2026-07-20T09:00:00+08:00",
        fact_key="breakfast",
        value="\u8c46\u6d46",
        summary="\u7528\u6237\u65e9\u9910\u559c\u6b22\u559d\u8c46\u6d46\u3002",
    )
    recalled = migrated_mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-recall-after-v9",
            subject=_subject(),
            request_digest="digest-recall-after-v9",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
            interaction_kind="explicit_recall",
            retrieval_query="\u5b89\u9759\u7684\u56fe\u4e66\u9986",
        )
    )
    with migrated_store.connection() as connection:
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        indexed_ids = {
            row[0]
            for row in connection.execute(
                "SELECT evidence_id FROM companion_evidence_fts"
            )
        }

    assert schema_version == SCHEMA_VERSION
    assert {existing_id, new_id} <= indexed_ids
    assert recalled.used_evidence_ids == (existing_id,)


def test_personal_memory_purge_deletes_short_term_retrieval_audits(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"retrieval-audit-purge",
    )
    _commit_fact(
        mind,
        turn_id="turn-before-audit-purge",
        occurred_at="2026-07-20T09:00:00+08:00",
        fact_key="origin",
        value="\u6b66\u6c49",
        summary="\u7528\u6237\u660e\u786e\u8868\u793a\u81ea\u5df1\u6765\u81ea\u6b66\u6c49\u3002",
    )
    mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-audit-before-purge",
            subject=_subject(),
            request_digest="digest-audit-before-purge",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
            interaction_kind="explicit_recall",
            retrieval_query="\u6211\u6765\u81ea\u54ea\u91cc",
            retrieval_hints={"fact_keys": ("origin",)},
        )
    )

    mind.apply_control(
        CompanionControlCommand(
            action="purge_personal_memory",
            subject=_subject(),
            payload={
                "now": "2026-07-21T09:01:00+08:00",
                "idempotency_key": "purge-retrieval-audits",
            },
        )
    )
    operator = mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T09:01:01+08:00",
        )
    )

    assert operator.payload["diagnostics"]["retrieval_audits"] == ()


def test_retrieval_audit_is_deleted_when_seven_day_retention_expires(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"retrieval-audit-retention",
    )
    _commit_fact(
        mind,
        turn_id="turn-before-audit-retention",
        occurred_at="2026-07-20T09:00:00+08:00",
        fact_key="origin",
        value="\u6b66\u6c49",
        summary="\u7528\u6237\u660e\u786e\u8868\u793a\u81ea\u5df1\u6765\u81ea\u6b66\u6c49\u3002",
    )
    mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-audit-before-retention",
            subject=_subject(),
            request_digest="digest-audit-before-retention",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
            interaction_kind="explicit_recall",
            retrieval_query="\u6211\u6765\u81ea\u54ea\u91cc",
            retrieval_hints={"fact_keys": ("origin",)},
        )
    )

    store.expire_derived_objects(now="2026-07-28T09:00:00+08:00")

    with store.connection() as connection:
        audit_count = connection.execute(
            "SELECT COUNT(*) FROM companion_retrieval_audits"
        ).fetchone()[0]
    assert audit_count == 0


def test_specific_query_does_not_fall_back_to_unrelated_evidence_after_forget(
    tmp_path,
):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"retrieval-no-unrelated-fallback",
    )
    forgotten_id = _commit_fact(
        mind,
        turn_id="turn-forgotten-place",
        occurred_at="2026-07-19T09:00:00+08:00",
        fact_key="study_place",
        value="\u661f\u7a7a\u81ea\u4e60\u5ba4",
        summary="\u7528\u6237\u559c\u6b22\u5728\u661f\u7a7a\u81ea\u4e60\u5ba4\u5b66\u4e60\u3002",
    )
    _commit_fact(
        mind,
        turn_id="turn-unrelated-breakfast",
        occurred_at="2026-07-20T09:00:00+08:00",
        fact_key="breakfast",
        value="\u8c46\u6d46",
        summary="\u7528\u6237\u65e9\u9910\u559c\u6b22\u559d\u8c46\u6d46\u3002",
    )
    mind.apply_control(
        CompanionControlCommand(
            action="forget_evidence",
            subject=_subject(),
            payload={
                "evidence_id": forgotten_id,
                "now": "2026-07-20T10:00:00+08:00",
                "idempotency_key": "forget-specific-study-place",
            },
        )
    )

    recalled = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-recall-forgotten-place",
            subject=_subject(),
            request_digest="digest-recall-forgotten-place",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
            interaction_kind="explicit_recall",
            retrieval_query="\u661f\u7a7a\u81ea\u4e60\u5ba4",
        )
    )

    assert recalled.used_evidence_ids == ()


def test_fts_candidates_and_audits_remain_isolated_between_students(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    own_mind = CompanionMind(store=store, token_secret=b"retrieval-own-student")
    other_mind = CompanionMind(store=store, token_secret=b"retrieval-other-student")
    own_id = _commit_fact(
        own_mind,
        turn_id="turn-own-secret-place",
        occurred_at="2026-07-01T09:00:00+08:00",
        fact_key="study_place",
        value="\u661f\u7a7a\u81ea\u4e60\u5ba4",
        summary="\u7528\u6237\u559c\u6b22\u5728\u661f\u7a7a\u81ea\u4e60\u5ba4\u5b66\u4e60\u3002",
    )
    other_id = _commit_fact(
        other_mind,
        turn_id="turn-other-secret-place",
        occurred_at="2026-07-20T09:00:00+08:00",
        fact_key="study_place",
        value="\u661f\u7a7a\u81ea\u4e60\u5ba4",
        summary="\u53e6\u4e00\u4f4d\u7528\u6237\u559c\u6b22\u5728\u661f\u7a7a\u81ea\u4e60\u5ba4\u5b66\u4e60\u3002",
        subject=_other_subject(),
    )

    recalled = own_mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-own-isolated-recall",
            subject=_subject(),
            request_digest="digest-own-isolated-recall",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
            interaction_kind="explicit_recall",
            retrieval_query="\u661f\u7a7a\u81ea\u4e60\u5ba4",
        )
    )
    operator = own_mind.project(
        CompanionProjectionRequest(
            subject=_subject(),
            surface="operator",
            now="2026-07-21T09:00:01+08:00",
        )
    )

    assert recalled.used_evidence_ids == (own_id,)
    assert other_id not in recalled.used_evidence_ids
    assert all(
        other_id not in audit["selected_evidence_ids"]
        for audit in operator.payload["diagnostics"]["retrieval_audits"]
    )


def test_exact_fact_recall_returns_replacement_not_superseded_value(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"retrieval-fact-replacement",
    )
    old_id = _commit_fact(
        mind,
        turn_id="turn-old-origin",
        occurred_at="2026-07-01T09:00:00+08:00",
        fact_key="origin",
        value="\u957f\u6c99",
        summary="\u7528\u6237\u66fe\u8868\u793a\u81ea\u5df1\u6765\u81ea\u957f\u6c99\u3002",
    )
    new_id = _commit_fact(
        mind,
        turn_id="turn-new-origin",
        occurred_at="2026-07-20T09:00:00+08:00",
        fact_key="origin",
        value="\u6b66\u6c49",
        summary="\u7528\u6237\u66f4\u65b0\u4e3a\u81ea\u5df1\u6765\u81ea\u6b66\u6c49\u3002",
    )

    recalled = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-recall-current-origin",
            subject=_subject(),
            request_digest="digest-recall-current-origin",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
            interaction_kind="explicit_recall",
            retrieval_query="\u6211\u6765\u81ea\u54ea\u91cc",
            retrieval_hints={"fact_keys": ("origin",)},
        )
    )

    assert old_id not in recalled.used_evidence_ids
    assert recalled.used_evidence_ids == (new_id,)


def test_fts_does_not_recall_relationship_evidence_from_old_epoch(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"retrieval-old-epoch")
    bootstrap = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-old-epoch-bootstrap",
            subject=_subject(),
            request_digest="digest-old-epoch-bootstrap",
            surface="voice",
            occurred_at="2026-07-19T08:00:00+08:00",
        )
    )
    mind.commit_turn(
        bootstrap,
        CompanionTurnOutcome(
            visible_response="\u6536\u5230\u3002",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-old-epoch-secret",
            subject=_subject(),
            request_digest="digest-old-epoch-secret",
            surface="voice",
            occurred_at="2026-07-19T09:00:00+08:00",
        )
    )
    store.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="\u6536\u5230\u3002",
            assistant_action="reply",
            delivery_status="generated",
        ),
        evidence=(
            CompanionEvidence(
                evidence_id="old-epoch-starry-room",
                pet_id=_subject().pet_id,
                memory_subject_id=_subject().memory_subject_id,
                ownership_scope="relationship",
                relationship_epoch_id=prepared.relationship_epoch_id,
                kind="meaningful_moment",
                content={"value": "\u661f\u7a7a\u81ea\u4e60\u5ba4"},
                source_kind="test",
                source_ref="old-epoch-starry-room",
                source_summary="\u5c0f\u82af\u66fe\u966a\u7528\u6237\u5728\u661f\u7a7a\u81ea\u4e60\u5ba4\u5b66\u4e60\u3002",
                attribution="observed_shared_moment",
                confidence=1.0,
                occurred_at="2026-07-19T08:30:00+08:00",
                retention="long_term",
                status="active",
                prompt_eligible=True,
            ),
        ),
    )
    mind.apply_control(
        CompanionControlCommand(
            action="reset_relationship",
            subject=_subject(),
            payload={
                "now": "2026-07-20T09:00:00+08:00",
                "idempotency_key": "reset-before-old-epoch-recall",
            },
        )
    )

    recalled = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-recall-old-epoch-secret",
            subject=_subject(),
            request_digest="digest-recall-old-epoch-secret",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
            interaction_kind="explicit_recall",
            retrieval_query="\u661f\u7a7a\u81ea\u4e60\u5ba4",
        )
    )

    assert "old-epoch-starry-room" not in recalled.used_evidence_ids
    assert recalled.used_evidence_ids == ()


@pytest.mark.parametrize(
    "request_fields",
    (
        {"retrieval_query": "x" * 501},
        {"retrieval_hints": {"unsupported": ("x",)}},
        {"retrieval_hints": {"fact_keys": "origin"}},
        {"retrieval_hints": {"exclude_sensitivities": ("unknown",)}},
        {"retrieval_hints": {"time_from": "2026-07-01T00:00:00"}},
    ),
)
def test_retrieval_contract_rejects_unbounded_or_invalid_inputs(request_fields):
    with pytest.raises(ValueError):
        CompanionTurnRequest(
            turn_id="turn-invalid-retrieval",
            subject=_subject(),
            request_digest="digest-invalid-retrieval",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
            **request_fields,
        )


def test_fts_never_duplicates_candidate_source_quote(tmp_path):
    class CandidateModel:
        def reflect(self, request):
            source = request.turn_sources[0]
            return ReflectionProposal(
                schema_version="companion-reflection-proposal-v1",
                safe_summary="\u53d1\u73b0\u5019\u9009\u3002",
                proposed_user_facts=(
                    {
                        "fact_key": "goal:english_cet6",
                        "kind": "goal",
                        "value": "\u51c6\u5907\u82f1\u8bed\u516d\u7ea7",
                        "source_turn_id": source.turn_id,
                        "source_quote": "\u6211\u6700\u8fd1\u5728\u51c6\u5907\u82f1\u8bed\u516d\u7ea7",
                        "claim_type": "explicit_statement",
                        "sensitivity": "private",
                        "confidence": 0.9,
                    },
                ),
            )

    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"retrieval-fts-redaction",
        reflection_model=CandidateModel(),
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-candidate-fts-redaction",
            subject=_subject(),
            request_digest="digest-candidate-fts-redaction",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
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
    _run_due_work(mind, now="2026-07-21T09:01:00+08:00")

    with store.connection() as connection:
        indexed_content = connection.execute(
            """
            SELECT fts.content_json
            FROM companion_evidence_fts AS fts
            JOIN companion_evidence AS evidence
              ON evidence.evidence_id = fts.evidence_id
            WHERE evidence.source_kind = 'conversation_candidate'
            """
        ).fetchone()[0]

    assert (
        "\u6211\u6700\u8fd1\u5728\u51c6\u5907\u82f1\u8bed\u516d\u7ea7"
        not in indexed_content
    )
    assert "\u51c6\u5907\u82f1\u8bed\u516d\u7ea7" in indexed_content


def test_retrieval_prompt_context_has_a_fixed_per_evidence_text_limit(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"retrieval-prompt-bound",
    )
    evidence_id = _commit_fact(
        mind,
        turn_id="turn-long-summary",
        occurred_at="2026-07-20T09:00:00+08:00",
        fact_key="origin",
        value="\u6b66\u6c49",
        summary="\u7528\u6237\u6765\u81ea\u6b66\u6c49\u3002" + "x" * 1000,
    )

    recalled = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-recall-long-summary",
            subject=_subject(),
            request_digest="digest-recall-long-summary",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
            interaction_kind="explicit_recall",
            retrieval_query="\u6211\u6765\u81ea\u54ea\u91cc",
            retrieval_hints={"fact_keys": ("origin",)},
        )
    )

    assert recalled.used_evidence_ids == (evidence_id,)
    assert len(recalled.prompt_context) == 1
    assert len(recalled.prompt_context[0]) <= 240


def test_task_start_preference_projects_a_soft_goal_without_fixed_format(tmp_path):
    mind = CompanionMind(
        store=CompanionStore(tmp_path / "xiaoxin_companion.db"),
        token_secret=b"retrieval-task-start-projection",
    )
    evidence_id = _commit_fact(
        mind,
        turn_id="turn-task-start-preference",
        occurred_at="2026-07-20T09:00:00+08:00",
        fact_key="preference:task_start_strategy",
        value="遇到复杂任务时先列一个很短的三步顺序再开始动手",
        summary="用户遇到复杂任务时喜欢先列一个很短的三步顺序再开始动手。",
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-apply-task-start-preference",
            subject=_subject(),
            request_digest="digest-apply-task-start-preference",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
        )
    )
    recalled, _ = mind._recall_companion_memory(
        prepared,
        query="传感器细节很多，不知道怎么开始",
        fact_keys=("preference:task_start_strategy",),
        minimum_memory_reference_budget=1,
    )

    assert recalled.used_evidence_ids == (evidence_id,)
    projection = json.loads(recalled.prompt_context[0])
    assert len(recalled.prompt_context[0]) <= 240
    assert projection["fact_key"] == "preference:task_start_strategy"
    assert "优先降低启动难度" in projection["usage_hint"]
    assert "不必固定条数" in projection["usage_hint"]


def test_emotional_support_preference_softly_overrides_general_style(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"retrieval-emotional-support-projection",
    )
    evidence_id = _commit_fact(
        mind,
        turn_id="turn-emotional-support-preference",
        occurred_at="2026-07-20T09:00:00+08:00",
        fact_key="preference:emotional_support_style",
        value="压力大时先慢慢理清感受，再温和地一起想办法",
        summary="用户压力大时希望先理清感受再一起想办法。",
    )
    with store.connection() as connection:
        epoch_id = connection.execute(
            """
            SELECT epoch_id FROM relationship_epochs
            WHERE pet_id = ? AND ended_at IS NULL
            """,
            (_subject().pet_id,),
        ).fetchone()["epoch_id"]
        for dimension, value, direction in (
            ("response_length", "expanded", "increase"),
            ("closure_style", "familiar", "increase"),
            ("emotional_posture", "neutral", "decrease"),
        ):
            adjustment_id = f"old-general-{dimension}"
            connection.execute(
                """
                INSERT INTO companion_adjustments(
                    adjustment_id, pet_id, relationship_epoch_id, dimension,
                    value_json, scope, behavior_key, context_scope, direction,
                    status, confidence, generated_by, created_at
                ) VALUES (?, ?, ?, ?, ?, 'user_low_mood', ?, 'user_low_mood', ?,
                          'active', 0.9, 'deterministic-test',
                          '2026-07-20T09:01:00+08:00')
                """,
                (
                    adjustment_id,
                    _subject().pet_id,
                    epoch_id,
                    dimension,
                    json.dumps({"value": value}),
                    dimension,
                    direction,
                ),
            )
            connection.execute(
                """
                INSERT INTO adjustment_evidence(adjustment_id, evidence_id, pet_id)
                VALUES (?, ?, ?)
                """,
                (adjustment_id, evidence_id, _subject().pet_id),
            )
        connection.commit()
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-apply-emotional-support-preference",
            subject=_subject(),
            request_digest="digest-apply-emotional-support-preference",
            surface="voice",
            occurred_at="2026-07-21T09:00:00+08:00",
            context="user_low_mood",
        )
    )
    material = store.load_policy_material(
        owner_user_id=_subject().owner_user_id,
        pet_id=_subject().pet_id,
        memory_subject_id=_subject().memory_subject_id,
        relationship_epoch_id=epoch_id,
        now="2026-07-21T09:00:00+08:00",
        surface="voice",
        interaction_kind="conversation",
        context="user_low_mood",
    )

    recalled, _ = mind._recall_companion_memory(
        prepared,
        query="今天很挫败，按适合我的方式陪我",
        fact_keys=("preference:emotional_support_style",),
        minimum_memory_reference_budget=1,
    )

    assert recalled.used_evidence_ids == (evidence_id,)
    projection = json.loads(recalled.prompt_context[0])
    assert len(recalled.prompt_context[0]) <= 240
    assert projection["fact_key"] == "preference:emotional_support_style"
    assert "与一般表达习惯冲突" in projection["usage_hint"]
    assert "自然回应" in projection["usage_hint"]
    assert not {
        "response_length",
        "closure_style",
        "emotional_posture",
    }.intersection(material.active_adjustments)
    assert prepared.policy.emotional_posture != "neutral"


def test_task_start_recall_prefers_canonical_fact_over_legacy_alias(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"retrieval-canonical-task-start",
    )
    canonical_id = _commit_fact(
        mind,
        turn_id="turn-canonical-task-start",
        occurred_at="2026-07-20T09:00:00+08:00",
        fact_key="preference:task_start_strategy",
        value="遇到复杂任务时先列一个很短的三步顺序再开始动手",
        summary="用户遇到复杂任务时喜欢先列一个很短的三步顺序。",
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
                'legacy-focus-on-key-step', ?, ?, 'user', 'preference', ?,
                'preference:focus_on_key_step', 'low', 'control',
                'control:legacy-focus-on-key-step',
                '事情多时希望先抓住最关键的一步',
                'explicit_statement', 1.0, '2026-07-21T09:00:00+08:00',
                'persistent', 'active', 1, '2026-07-21T09:00:00+08:00'
            )
            """,
            (
                _subject().pet_id,
                _subject().memory_subject_id,
                json.dumps(
                    {"canonical_value": "事情多时先抓住最关键的一步"},
                    ensure_ascii=False,
                ),
            ),
        )
        connection.commit()
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-recall-canonical-task-start",
            subject=_subject(),
            request_digest="digest-recall-canonical-task-start",
            surface="voice",
            occurred_at="2026-07-22T09:00:00+08:00",
        )
    )

    recalled, _ = mind._recall_companion_memory(
        prepared,
        query="陌生传感器资料很多，陪我开始第一步",
        fact_keys=("preference:task_start_strategy",),
        minimum_memory_reference_budget=3,
    )

    assert recalled.used_evidence_ids == (canonical_id,)
    assert json.loads(recalled.prompt_context[0])["fact_key"] == (
        "preference:task_start_strategy"
    )
