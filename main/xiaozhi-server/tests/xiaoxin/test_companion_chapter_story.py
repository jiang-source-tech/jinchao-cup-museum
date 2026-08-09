from __future__ import annotations

import asyncio
import pytest

from core.xiaoxin.companion import (
    CompanionControlCommand,
    CompanionMind,
    CompanionProjectionRequest,
    CompanionSubjectContext,
    CompanionTurnOutcome,
    CompanionTurnRequest,
)
from core.xiaoxin.companion.reflection import (
    ChapterStatementProposal,
    ReflectionProposal,
)
from core.xiaoxin.companion.store import CompanionJobLeaseLostError, CompanionStore


def _run_due_work(mind, **kwargs):
    return asyncio.run(mind.run_due_work(**kwargs))


class EvidenceBackedChapterModel:
    def __init__(self) -> None:
        self.calls = []

    def reflect(self, request):
        self.calls.append(request)
        chapter_statements = ()
        if request.job_kind == "academic_stage_changed":
            chapter_statements = tuple(
                ChapterStatementProposal(
                    claim_scope=(
                        "user_fact"
                        if ownership_scope == "user"
                        else "shared_experience"
                    ),
                    evidence_ids=tuple(
                        item.evidence_id
                        for item in request.evidence
                        if item.ownership_scope == ownership_scope
                    ),
                )
                for ownership_scope in ("user", "relationship")
                if any(
                    item.ownership_scope == ownership_scope
                    for item in request.evidence
                )
            )
        return ReflectionProposal(
            schema_version="companion-reflection-proposal-v1",
            safe_summary="这一阶段有两次得到明确反馈的共同互动。",
            evidence_ids=tuple(item.evidence_id for item in request.evidence),
            chapter_statements=chapter_statements,
        )


def _subject(stage: str) -> CompanionSubjectContext:
    return CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage=stage,
        persistence_allowed=True,
    )


def _commit(
    mind: CompanionMind,
    *,
    turn_id: str,
    stage: str,
    occurred_at: str,
    meaningful: bool = False,
):
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id=turn_id,
            subject=_subject(stage),
            request_digest=f"digest-{turn_id}",
            surface="voice",
            occurred_at=occurred_at,
        )
    )
    signals = ()
    if meaningful:
        signals = (
            {
                "kind": "meaningful_moment",
                "ownership_scope": "relationship",
                "content": {"outcome": "helpful", "theme": turn_id},
                "source_summary": "用户确认本轮互动有帮助。",
                "attribution": "observed_interaction",
                "confidence": 1.0,
                "retention": "long_term",
                "prompt_eligible": True,
            },
        )
    committed = mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="收到。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=signals,
        ),
    )
    return prepared, committed


def _sync_academic_stage(
    mind: CompanionMind,
    *,
    stage: str,
    effective_at: str,
    revision: int = 1,
) -> None:
    mind.apply_control(
        CompanionControlCommand(
            action="sync_academic_stage",
            subject=_subject(stage),
            payload={
                "now": effective_at,
                "effective_at": effective_at,
                "source_revision": revision,
            },
        )
    )


def test_stage_change_builds_evidence_backed_chapter_without_resetting_epoch(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = EvidenceBackedChapterModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"chapter-story",
        reflection_model=model,
    )
    first, _ = _commit(
        mind,
        turn_id="turn-first-use",
        stage="freshman",
        occurred_at="2026-07-18T09:00:00+08:00",
    )
    established, first_moment = _commit(
        mind,
        turn_id="turn-shared-1",
        stage="freshman",
        occurred_at="2026-07-18T10:00:00+08:00",
        meaningful=True,
    )
    _, second_moment = _commit(
        mind,
        turn_id="turn-shared-2",
        stage="freshman",
        occurred_at="2026-07-19T10:00:00+08:00",
        meaningful=True,
    )
    _sync_academic_stage(
        mind,
        stage="sophomore",
        effective_at="2026-09-01T08:59:00+08:00",
    )
    changed, changed_commit = _commit(
        mind,
        turn_id="turn-stage-change",
        stage="sophomore",
        occurred_at="2026-09-01T09:00:00+08:00",
    )

    result = _run_due_work(mind, now="2026-09-01T09:01:00+08:00")

    assert established.relationship_epoch_id == changed.relationship_epoch_id
    assert len(changed_commit.job_ids) == 0
    assert result.succeeded == 3
    with store.connection() as connection:
        chapters = connection.execute(
            """
            SELECT academic_stage, xiaoxin_age, relationship_epoch_id,
                   safe_narrative, status, version
            FROM companion_chapters
            """
        ).fetchall()
        chapter_evidence_ids = {
            row[0] for row in connection.execute("SELECT evidence_id FROM chapter_evidence")
        }
        epoch_count = connection.execute(
            "SELECT COUNT(*) FROM relationship_epochs"
        ).fetchone()[0]

    assert len(chapters) == 1
    assert tuple(chapters[0]) == (
        "freshman",
        1,
        established.relationship_epoch_id,
        "共同经历：用户确认本轮互动有帮助。；用户确认本轮互动有帮助。",
        "active",
        1,
    )
    assert chapter_evidence_ids == set(
        first_moment.evidence_ids + second_moment.evidence_ids
    )
    assert epoch_count == 1

    mind.apply_control(
        CompanionControlCommand(
            action="sync_academic_stage",
            subject=_subject("junior"),
            payload={
                "now": "2027-09-01T08:59:00+08:00",
                "effective_at": "2027-09-01T08:59:00+08:00",
                "source_revision": 2,
            },
        )
    )
    junior, _ = _commit(
        mind,
        turn_id="turn-stage-change-junior",
        stage="junior",
        occurred_at="2027-09-01T09:00:00+08:00",
    )
    assert _run_due_work(mind, now="2027-09-01T09:01:00+08:00").succeeded == 1
    mind.apply_control(
        CompanionControlCommand(
            action="sync_academic_stage",
            subject=_subject("sophomore"),
            payload={
                "now": "2027-09-02T08:59:00+08:00",
                "effective_at": "2027-09-02T08:59:00+08:00",
                "source_revision": 3,
            },
        )
    )
    sophomore_again, _ = _commit(
        mind,
        turn_id="turn-stage-change-sophomore-again",
        stage="sophomore",
        occurred_at="2027-09-02T09:00:00+08:00",
    )
    assert _run_due_work(mind, now="2027-09-02T09:01:00+08:00").succeeded == 1

    assert junior.relationship_epoch_id == established.relationship_epoch_id
    assert sophomore_again.relationship_epoch_id == established.relationship_epoch_id
    with store.connection() as connection:
        versions = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT academic_stage, version, status,
                       period_end IS NOT NULL AS is_closed
                FROM companion_chapters
                ORDER BY created_at, chapter_id
                """
            )
        ]
    assert versions == [("freshman", 1, "active", 1)]

    forgotten = mind.apply_control(
        CompanionControlCommand(
            action="forget_evidence",
            subject=_subject("sophomore"),
            payload={
                "evidence_id": first_moment.evidence_ids[0],
                "now": "2027-09-02T09:02:00+08:00",
                "idempotency_key": "forget-chapter-evidence",
            },
        )
    )
    assert forgotten.forgotten == 1
    with store.connection() as connection:
        active_version_status = connection.execute(
            """
            SELECT status FROM companion_chapters
            WHERE academic_stage = 'freshman' AND version = 1
            """
        ).fetchone()[0]
    assert active_version_status == "invalidated"


def test_revoking_boundary_invalidates_chapter_that_cites_it(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"chapter-story",
        reflection_model=EvidenceBackedChapterModel(),
    )
    _commit(
        mind,
        turn_id="turn-boundary-chapter-first-use",
        stage="freshman",
        occurred_at="2026-07-18T09:00:00+08:00",
    )
    mind.apply_control(
        CompanionControlCommand(
            action="set_boundary",
            subject=_subject("freshman"),
            payload={
                "boundary_key": "question_frequency",
                "value": "never",
                "source_summary": "用户明确要求不要追问。",
                "now": "2026-07-18T09:05:00+08:00",
                "idempotency_key": "boundary-for-chapter",
            },
        )
    )
    _commit(
        mind,
        turn_id="turn-boundary-chapter-shared-2",
        stage="freshman",
        occurred_at="2026-07-19T10:00:00+08:00",
        meaningful=True,
    )
    _sync_academic_stage(
        mind,
        stage="sophomore",
        effective_at="2026-09-01T08:59:00+08:00",
    )
    _commit(
        mind,
        turn_id="turn-boundary-chapter-stage-change",
        stage="sophomore",
        occurred_at="2026-09-01T09:00:00+08:00",
    )
    assert _run_due_work(mind, now="2026-09-01T09:01:00+08:00").succeeded == 2
    with store.connection() as connection:
        boundary_id = connection.execute(
            """
            SELECT evidence_id FROM companion_evidence
            WHERE kind = 'explicit_boundary'
            """
        ).fetchone()[0]
        chapter_status = connection.execute(
            "SELECT status FROM companion_chapters"
        ).fetchone()[0]
        cited = connection.execute(
            """
            SELECT COUNT(*) FROM chapter_evidence
            WHERE evidence_id = ?
            """,
            (boundary_id,),
        ).fetchone()[0]
    assert chapter_status == "active"
    assert cited == 1

    mind.apply_control(
        CompanionControlCommand(
            action="revoke_boundary",
            subject=_subject("sophomore"),
            payload={
                "evidence_id": boundary_id,
                "now": "2026-09-01T09:02:00+08:00",
                "idempotency_key": "revoke-boundary-for-chapter",
            },
        )
    )

    with store.connection() as connection:
        chapter_status = connection.execute(
            "SELECT status FROM companion_chapters"
        ).fetchone()[0]
    assert chapter_status == "invalidated"


def test_expired_chapter_lease_cannot_create_chapter_before_reclaim(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = EvidenceBackedChapterModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"chapter-story",
        reflection_model=model,
    )
    _commit(
        mind,
        turn_id="turn-expired-chapter-first-use",
        stage="freshman",
        occurred_at="2026-07-18T09:00:00+08:00",
    )
    _commit(
        mind,
        turn_id="turn-expired-chapter-shared-1",
        stage="freshman",
        occurred_at="2026-07-18T10:00:00+08:00",
        meaningful=True,
    )
    _commit(
        mind,
        turn_id="turn-expired-chapter-shared-2",
        stage="freshman",
        occurred_at="2026-07-19T10:00:00+08:00",
        meaningful=True,
    )
    _sync_academic_stage(
        mind,
        stage="sophomore",
        effective_at="2026-09-01T08:59:00+08:00",
    )
    _commit(
        mind,
        turn_id="turn-expired-chapter-stage-change",
        stage="sophomore",
        occurred_at="2026-09-01T09:00:00+08:00",
    )
    jobs = store.claim_due_jobs(
        now="2026-09-01T09:01:00+08:00",
        limit=20,
        lease_seconds=1,
    )
    chapter_job = next(job for job in jobs if job.job_kind == "academic_stage_changed")
    evidence = store.load_chapter_evidence(
        job=chapter_job,
        now="2026-09-01T09:01:02+08:00",
    )
    proposal = model.reflect(
        type(
            "ChapterRequest",
            (),
            {"job_kind": "academic_stage_changed", "evidence": evidence},
        )()
    )

    with pytest.raises(CompanionJobLeaseLostError):
        store.apply_chapter_proposal(
            job=chapter_job,
            proposal=proposal,
            evidence_ids=proposal.evidence_ids,
            now="2026-09-01T09:01:02+08:00",
            model="test-model",
            prompt_version="companion-reflection-request-v1",
        )

    with store.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM companion_chapters"
        ).fetchone()[0] == 0


def test_first_use_and_insufficient_stage_change_do_not_invent_chapters(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = EvidenceBackedChapterModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"chapter-story",
        reflection_model=model,
    )
    first, _ = _commit(
        mind,
        turn_id="turn-first-sophomore",
        stage="sophomore",
        occurred_at="2026-07-18T09:00:00+08:00",
    )
    established, _ = _commit(
        mind,
        turn_id="turn-establish-sophomore",
        stage="sophomore",
        occurred_at="2026-07-18T09:01:00+08:00",
    )
    assert first.policy.xiaoxin_age == 2
    assert _run_due_work(mind, now="2026-07-18T09:02:00+08:00").claimed == 0

    _sync_academic_stage(
        mind,
        stage="junior",
        effective_at="2027-09-01T08:59:00+08:00",
    )
    changed, _ = _commit(
        mind,
        turn_id="turn-insufficient-junior",
        stage="junior",
        occurred_at="2027-09-01T09:00:00+08:00",
    )
    result = _run_due_work(mind, now="2027-09-01T09:01:00+08:00")

    assert established.relationship_epoch_id == changed.relationship_epoch_id
    assert result.succeeded == 1
    assert model.calls == []
    with store.connection() as connection:
        chapter_count = connection.execute(
            "SELECT COUNT(*) FROM companion_chapters"
        ).fetchone()[0]
        stages = {
            row[0]
            for row in connection.execute(
                """
                SELECT json_extract(content_json, '$.academic_stage')
                FROM companion_evidence
                WHERE kind = 'system_event' AND status = 'active'
                  AND source_ref = 'identity:student_profile'
                """
            )
        }
    assert chapter_count == 0
    assert stages == {"junior"}


def test_chapter_keeps_user_facts_distinct_from_shared_experiences(tmp_path):
    class AttributionAwareModel(EvidenceBackedChapterModel):
        def reflect(self, request):
            self.calls.append(request)
            if request.job_kind != "academic_stage_changed":
                relationship_ids = tuple(
                    item.evidence_id
                    for item in request.evidence
                    if item.ownership_scope == "relationship"
                )
                return ReflectionProposal(
                    schema_version="companion-reflection-proposal-v1",
                    safe_summary="本轮互动整理完成。",
                    evidence_ids=relationship_ids,
                )
            return ReflectionProposal(
                schema_version="companion-reflection-proposal-v1",
                safe_summary="小芯和用户共同完成了用户自己的目标。",
                evidence_ids=tuple(item.evidence_id for item in request.evidence),
                chapter_statements=tuple(
                    ChapterStatementProposal(
                        claim_scope=(
                            "user_fact"
                            if ownership_scope == "user"
                            else "shared_experience"
                        ),
                        evidence_ids=tuple(
                            item.evidence_id
                            for item in request.evidence
                            if item.ownership_scope == ownership_scope
                        ),
                    )
                    for ownership_scope in ("user", "relationship")
                    if any(
                        item.ownership_scope == ownership_scope
                        for item in request.evidence
                    )
                ),
            )

    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = AttributionAwareModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"chapter-story",
        reflection_model=model,
    )
    _commit(
        mind,
        turn_id="turn-attribution-bootstrap",
        stage="freshman",
        occurred_at="2026-07-18T09:00:00+08:00",
    )
    _, shared = _commit(
        mind,
        turn_id="turn-attribution-shared",
        stage="freshman",
        occurred_at="2026-07-18T10:00:00+08:00",
        meaningful=True,
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-attribution-user-fact",
            subject=_subject("freshman"),
            request_digest="digest-attribution-user-fact",
            surface="voice",
            occurred_at="2026-07-19T10:00:00+08:00",
        )
    )
    user_fact = mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="记下了。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=(
                {
                    "kind": "user_life_event",
                    "ownership_scope": "user",
                    "content": {"event": "completed_personal_goal"},
                    "source_summary": "用户完成了自己设定的目标。",
                    "attribution": "explicit_user_statement",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
            ),
        ),
    )
    _sync_academic_stage(
        mind,
        stage="sophomore",
        effective_at="2026-09-01T08:59:00+08:00",
    )
    _commit(
        mind,
        turn_id="turn-attribution-stage-change",
        stage="sophomore",
        occurred_at="2026-09-01T09:00:00+08:00",
    )

    result = _run_due_work(mind, now="2026-09-01T09:01:00+08:00")

    assert result.succeeded == 3
    chapter_request = next(
        call for call in model.calls if call.job_kind == "academic_stage_changed"
    )
    assert {item.ownership_scope for item in chapter_request.evidence} == {
        "relationship",
        "user",
    }
    with store.connection() as connection:
        chapter = connection.execute(
            "SELECT safe_narrative FROM companion_chapters"
        ).fetchone()[0]
        linked_ids = {
            row[0] for row in connection.execute("SELECT evidence_id FROM chapter_evidence")
        }
    assert chapter == (
        "用户自己的事实：用户完成了自己设定的目标。\n"
        "共同经历：用户确认本轮互动有帮助。"
    )
    assert linked_ids == set(shared.evidence_ids + user_fact.evidence_ids)


def _story_each_standard_transition_closes_the_stage_actually_left(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"four-year-chapters",
        reflection_model=EvidenceBackedChapterModel(),
    )
    _commit(
        mind,
        turn_id="four-year-bootstrap",
        stage="freshman",
        occurred_at="2026-01-01T09:00:00+08:00",
    )
    stages = (
        ("freshman", "sophomore", 1, "2026-01", "2026-02-01T09:00:00+08:00"),
        ("sophomore", "junior", 2, "2026-02", "2026-03-01T09:00:00+08:00"),
        ("junior", "senior", 3, "2026-03", "2026-04-01T09:00:00+08:00"),
    )
    for from_stage, to_stage, revision, month, transition_at in stages:
        for day in (2, 3):
            _commit(
                mind,
                turn_id=f"{from_stage}-shared-{day}",
                stage=from_stage,
                occurred_at=f"{month}-{day:02d}T10:00:00+08:00",
                meaningful=True,
            )
        _sync_academic_stage(
            mind,
            stage=to_stage,
            effective_at=transition_at,
            revision=revision,
        )
        _run_due_work(mind, now=transition_at)

    with store.connection() as connection:
        chapters = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT academic_stage, xiaoxin_age, version, status
                FROM companion_chapters
                ORDER BY period_end, chapter_id
                """
            )
        ]
    assert chapters == [
        ("freshman", 1, 1, "active"),
        ("sophomore", 2, 1, "active"),
        ("junior", 3, 1, "active"),
    ]
    mind.apply_control(
        CompanionControlCommand(
            action="sync_academic_stage",
            subject=_subject("senior"),
            payload={
                "now": "2026-04-02T09:00:00+08:00",
                "effective_at": "2026-04-02T09:00:00+08:00",
                "source_revision": 4,
                "transition_kind": "correction",
            },
        )
    )
    with store.connection() as connection:
        statuses = {
            row["status"]
            for row in connection.execute("SELECT status FROM companion_chapters")
        }
    assert statuses == {"invalidated"}


def _story_skip_advance_does_not_invent_intermediate_chapters(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"skip-chapters",
        reflection_model=EvidenceBackedChapterModel(),
    )
    _commit(
        mind,
        turn_id="skip-bootstrap",
        stage="freshman",
        occurred_at="2026-01-01T09:00:00+08:00",
    )
    for day in (2, 3):
        _commit(
            mind,
            turn_id=f"skip-shared-{day}",
            stage="freshman",
            occurred_at=f"2026-01-{day:02d}T10:00:00+08:00",
            meaningful=True,
        )
    _sync_academic_stage(
        mind,
        stage="senior",
        effective_at="2026-02-01T09:00:00+08:00",
    )
    _run_due_work(mind, now="2026-02-01T09:01:00+08:00")

    with store.connection() as connection:
        chapter_stages = [
            row["academic_stage"]
            for row in connection.execute(
                "SELECT academic_stage FROM companion_chapters"
            )
        ]
        boundary = connection.execute(
            """
            SELECT from_stage, to_stage FROM companion_narrative_boundaries
            WHERE boundary_kind = 'academic_growth'
            """
        ).fetchone()
    assert chapter_stages == ["freshman"]
    assert tuple(boundary) == ("freshman", "senior")


def _story_forgetting_one_of_three_evidence_rebuilds_an_immutable_chapter(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"chapter-rebuild",
        reflection_model=EvidenceBackedChapterModel(),
    )
    _commit(
        mind,
        turn_id="rebuild-bootstrap",
        stage="freshman",
        occurred_at="2026-01-01T09:00:00+08:00",
    )
    evidence_ids = []
    for day in (2, 3, 4):
        _, committed = _commit(
            mind,
            turn_id=f"rebuild-shared-{day}",
            stage="freshman",
            occurred_at=f"2026-01-{day:02d}T10:00:00+08:00",
            meaningful=True,
        )
        evidence_ids.extend(committed.evidence_ids)
    _sync_academic_stage(
        mind,
        stage="sophomore",
        effective_at="2026-02-01T09:00:00+08:00",
    )
    _run_due_work(mind, now="2026-02-01T09:01:00+08:00")
    reserved = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="rebuild-reserved-turn",
            subject=_subject("sophomore"),
            request_digest="digest-rebuild-reserved-turn",
            surface="voice",
            occurred_at="2026-02-01T09:02:00+08:00",
        )
    )
    assert reserved.growth_moment is not None

    mind.apply_control(
        CompanionControlCommand(
            action="forget_evidence",
            subject=_subject("sophomore"),
            payload={
                "evidence_id": evidence_ids[0],
                "now": "2026-02-01T09:03:00+08:00",
                "idempotency_key": "forget-one-of-three",
            },
        )
    )
    with store.connection() as connection:
        released = connection.execute(
            """
            SELECT moment.expression_status, moment.reserved_by_turn_id,
                   metadata.mode
            FROM companion_growth_moments AS moment
            JOIN companion_growth_moment_metadata AS metadata
              ON metadata.moment_id = moment.moment_id
            """
        ).fetchone()
    assert tuple(released) == ("pending", None, "evidence_backed")
    mind.commit_turn(
        reserved,
        CompanionTurnOutcome(
            visible_response="旧投影不再消费成长时刻。",
            assistant_action="reply",
            delivery_status="generated",
        ),
    )

    _run_due_work(mind, now="2026-02-01T09:04:00+08:00")
    with store.connection() as connection:
        chapters = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT version, status FROM companion_chapters
                ORDER BY version
                """
            )
        ]
        active_evidence = {
            row["evidence_id"]
            for row in connection.execute(
                """
                SELECT link.evidence_id
                FROM chapter_evidence AS link
                JOIN companion_chapters AS chapter
                  ON chapter.chapter_id = link.chapter_id
                WHERE chapter.status = 'active'
                """
            )
        }
        rebuilt_mode = connection.execute(
            "SELECT mode FROM companion_growth_moment_metadata"
        ).fetchone()[0]
    assert chapters == [(1, "invalidated"), (2, "active")]
    assert active_evidence == set(evidence_ids[1:])
    assert rebuilt_mode == "evidence_backed"


def test_stage_boundaries_and_evidence_rebuild_follow_real_history(tmp_path):
    for name, story in (
        ("standard", _story_each_standard_transition_closes_the_stage_actually_left),
        ("skip", _story_skip_advance_does_not_invent_intermediate_chapters),
        ("rebuild", _story_forgetting_one_of_three_evidence_rebuilds_an_immutable_chapter),
    ):
        story_dir = tmp_path / name
        story_dir.mkdir()
        story(story_dir)


def test_anniversary_requires_a_qualified_chapter_and_expires_after_14_days(
    tmp_path,
):
    empty_store = CompanionStore(tmp_path / "empty.db")
    empty_mind = CompanionMind(
        store=empty_store,
        token_secret=b"empty-anniversary",
        reflection_model=EvidenceBackedChapterModel(),
    )
    _commit(
        empty_mind,
        turn_id="empty-anniversary-bootstrap",
        stage="freshman",
        occurred_at="2026-07-18T09:00:00+08:00",
    )
    empty_mind.project(
        CompanionProjectionRequest(
            subject=_subject("freshman"),
            surface="miniprogram",
            now="2027-07-18T09:00:00+08:00",
        )
    )
    _run_due_work(empty_mind, now="2027-07-18T09:01:00+08:00")
    with empty_store.connection() as connection:
        empty_counts = tuple(
            connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM companion_narrative_boundaries
                   WHERE boundary_kind = 'anniversary'),
                  (SELECT COUNT(*) FROM companion_chapters),
                  (SELECT COUNT(*) FROM companion_growth_moments)
                """
            ).fetchone()
        )
    assert empty_counts == (1, 0, 0)
    empty_epoch = empty_store.get_active_epoch(
        owner_user_id="owner-1",
        pet_id="pet-1",
    )
    assert empty_epoch is not None
    empty_store.ensure_anniversary_boundaries(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-2",
        relationship_epoch_id=empty_epoch.epoch_id,
        academic_stage="freshman",
        now="2027-07-18T09:02:00+08:00",
    )
    empty_store.ensure_anniversary_boundaries(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-unknown",
        relationship_epoch_id=empty_epoch.epoch_id,
        academic_stage="unknown",
        now="2027-07-18T09:03:00+08:00",
    )
    with empty_store.connection() as connection:
        anniversary_evidence = connection.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT evidence_id)
            FROM companion_evidence
            WHERE source_kind = 'lifecycle'
            """
        ).fetchone()
        unknown_age = connection.execute(
            """
            SELECT xiaoxin_age FROM companion_narrative_boundaries
            WHERE memory_subject_id = 'subject-unknown'
            """
        ).fetchone()[0]
    assert tuple(anniversary_evidence) == (3, 3)
    assert unknown_age is None

    store = CompanionStore(tmp_path / "qualified.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"qualified-anniversary",
        reflection_model=EvidenceBackedChapterModel(),
    )
    _commit(
        mind,
        turn_id="qualified-anniversary-bootstrap",
        stage="freshman",
        occurred_at="2026-07-18T09:00:00+08:00",
    )
    for day in (19, 20):
        _commit(
            mind,
            turn_id=f"qualified-anniversary-{day}",
            stage="freshman",
            occurred_at=f"2026-07-{day:02d}T10:00:00+08:00",
            meaningful=True,
        )
    before = mind.project(
        CompanionProjectionRequest(
            subject=_subject("freshman"),
            surface="miniprogram",
            now="2027-07-18T09:00:00+08:00",
        )
    )
    assert "growth_moment" not in before.payload
    _run_due_work(mind, now="2027-07-18T09:01:00+08:00")
    active = mind.project(
        CompanionProjectionRequest(
            subject=_subject("freshman"),
            surface="miniprogram",
            now="2027-07-18T09:02:00+08:00",
        )
    ).payload["growth_moment"]
    assert active["primary_kind"] == "anniversary"
    assert active["mode"] == "evidence_backed"
    assert len(active["evidence_ids"]) == 2

    expired = mind.project(
        CompanionProjectionRequest(
            subject=_subject("freshman"),
            surface="miniprogram",
            now="2027-08-02T09:02:00+08:00",
        )
    )
    assert "growth_moment" not in expired.payload
    with store.connection() as connection:
        lifecycle = connection.execute(
            "SELECT lifecycle_status FROM companion_growth_moment_metadata"
        ).fetchone()[0]
    assert lifecycle == "expired"
    merge_dir = tmp_path / "merge"
    merge_dir.mkdir()
    _story_anniversary_merges_into_nearby_academic_moment_without_losing_boundaries(
        merge_dir
    )


def _story_anniversary_merges_into_nearby_academic_moment_without_losing_boundaries(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(store=store, token_secret=b"merged-anniversary")
    _commit(
        mind,
        turn_id="merged-anniversary-bootstrap",
        stage="freshman",
        occurred_at="2026-09-01T09:00:00+08:00",
    )
    _sync_academic_stage(
        mind,
        stage="sophomore",
        effective_at="2027-08-20T09:00:00+08:00",
    )
    projection = mind.project(
        CompanionProjectionRequest(
            subject=_subject("sophomore"),
            surface="miniprogram",
            now="2027-09-01T09:00:00+08:00",
        )
    )

    moment = projection.payload["growth_moment"]
    assert moment["primary_kind"] == "academic_growth"
    assert {fact["kind"] for fact in moment["boundary_facts"]} == {
        "academic_growth",
        "anniversary",
    }
    with store.connection() as connection:
        counts = tuple(
            connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM companion_growth_moments),
                  (SELECT COUNT(*) FROM companion_growth_moment_boundaries)
                """
            ).fetchone()
        )
    assert counts == (1, 2)

    reverse_store = CompanionStore(tmp_path / "reverse.db")
    reverse_mind = CompanionMind(
        store=reverse_store,
        token_secret=b"reverse-merged-anniversary",
        reflection_model=EvidenceBackedChapterModel(),
    )
    _commit(
        reverse_mind,
        turn_id="reverse-anniversary-bootstrap",
        stage="freshman",
        occurred_at="2026-07-18T09:00:00+08:00",
    )
    for day in (19, 20):
        _commit(
            reverse_mind,
            turn_id=f"reverse-anniversary-{day}",
            stage="freshman",
            occurred_at=f"2026-07-{day:02d}T10:00:00+08:00",
            meaningful=True,
        )
    reverse_mind.project(
        CompanionProjectionRequest(
            subject=_subject("freshman"),
            surface="miniprogram",
            now="2027-07-18T09:00:00+08:00",
        )
    )
    _run_due_work(reverse_mind, now="2027-07-18T09:01:00+08:00")
    _sync_academic_stage(
        reverse_mind,
        stage="sophomore",
        effective_at="2027-07-25T09:00:00+08:00",
    )
    reverse = reverse_mind.project(
        CompanionProjectionRequest(
            subject=_subject("sophomore"),
            surface="miniprogram",
            now="2027-07-25T09:01:00+08:00",
        )
    ).payload["growth_moment"]
    assert reverse["primary_kind"] == "academic_growth"
    assert {fact["kind"] for fact in reverse["boundary_facts"]} == {
        "academic_growth",
        "anniversary",
    }
    with reverse_store.connection() as connection:
        lifecycle_statuses = [
            row["lifecycle_status"]
            for row in connection.execute(
                """
                SELECT lifecycle_status FROM companion_growth_moment_metadata
                ORDER BY lifecycle_status
                """
            )
        ]
    assert lifecycle_statuses == ["active", "suppressed"]
