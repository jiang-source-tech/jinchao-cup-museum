from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import logging
from threading import Event
import time

import pytest

from core.xiaoxin.companion import (
    CompanionControlCommand,
    CompanionMind,
    CompanionSubjectContext,
    CompanionTurnOutcome,
    CompanionTurnRequest,
)
from core.xiaoxin.companion.reflection import (
    ChapterStatementProposal,
    ReflectionProposal,
)
from core.xiaoxin.companion.store import CompanionJobLeaseLostError, CompanionStore
from core.xiaoxin.companion.worker import CompanionWorker, CompanionWorkerConfig
from core.xiaoxin.turn_analysis import explicit_companion_feedback_signals


def _run_due_work(mind, **kwargs):
    return asyncio.run(mind.run_due_work(**kwargs))


class EmptyReflectionModel:
    def __init__(self) -> None:
        self.calls = []

    def reflect(self, request):
        self.calls.append(request)
        return ReflectionProposal(
            schema_version="companion-reflection-proposal-v1",
            safe_summary="本轮没有需要形成长期派生对象的内容。",
        )


class BlockingReflectionModel(EmptyReflectionModel):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()

    def reflect(self, request):
        self.calls.append(request)
        self.started.set()
        assert self.release.wait(timeout=2)
        return ReflectionProposal(
            schema_version="companion-reflection-proposal-v1",
            safe_summary="完成。",
        )


class FailingReflectionModel:
    def reflect(self, request):
        raise TimeoutError("reflection timeout")


class NonChapterJobWithChapterStatementModel:
    def reflect(self, request):
        evidence_ids = tuple(item.evidence_id for item in request.evidence)
        return ReflectionProposal(
            schema_version="companion-reflection-proposal-v1",
            safe_summary="用户明确希望小芯在次日继续关心这件事。",
            evidence_ids=evidence_ids,
            chapter_statements=(
                ChapterStatementProposal(
                    claim_scope="shared_experience",
                    evidence_ids=evidence_ids,
                ),
            ),
        )


class SensitiveFailingReflectionModel:
    def reflect(self, request):
        raise RuntimeError("用户原文：我的银行卡密码是 123456")


class AdvancingClock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds


class LeaseExpiringReflectionModel:
    def __init__(self, clock: AdvancingClock) -> None:
        self.clock = clock

    def reflect(self, request):
        self.clock.seconds = 2.0
        return ReflectionProposal(
            schema_version="companion-reflection-proposal-v1",
            safe_summary="模型返回时 lease 已经过期。",
            evidence_ids=tuple(item.evidence_id for item in request.evidence),
        )


class BlockingLoadStore(CompanionStore):
    def __init__(self, database_path):
        super().__init__(database_path)
        self.load_started = Event()
        self.release_load = Event()

    def load_job_evidence(self, *, job, now):
        evidence = super().load_job_evidence(job=job, now=now)
        self.load_started.set()
        assert self.release_load.wait(timeout=2)
        return evidence


def test_claim_due_jobs_can_be_limited_to_one_pet(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    with store.connection() as connection:
        for label in ("a", "b"):
            connection.execute(
                """
                INSERT INTO consolidation_jobs(
                    job_id, pet_id, relationship_epoch_id, job_kind,
                    idempotency_key, payload_json, status, attempt,
                    due_at, schema_version, created_at, updated_at
                ) VALUES (?, ?, NULL, 'session_consolidation', ?, '{}',
                          'pending', 0, ?, 'test-v1', ?, ?)
                """,
                (
                    f"job-{label}",
                    f"pet-{label}",
                    f"key-{label}",
                    "2026-07-18T10:00:00+08:00",
                    "2026-07-18T09:00:00+08:00",
                    "2026-07-18T09:00:00+08:00",
                ),
            )
        connection.commit()

    claimed = store.claim_due_jobs(
        now="2026-07-18T10:01:00+08:00",
        limit=20,
        lease_seconds=60,
        pet_id="pet-a",
    )

    assert [job.job_id for job in claimed] == ["job-a"]
    with store.connection() as connection:
        untouched = connection.execute(
            "SELECT status, attempt FROM consolidation_jobs WHERE job_id = 'job-b'"
        ).fetchone()
    assert dict(untouched) == {"status": "pending", "attempt": 0}


def test_commit_queues_stable_job_and_worker_completes_it(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = EmptyReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"worker-test-secret",
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
            turn_id="turn-worker-1",
            subject=subject,
            request_digest="digest-worker-1",
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

    assert len(committed.job_ids) == 1
    assert result.claimed == 1
    assert result.succeeded == 1
    assert result.retried == 0
    assert result.failed == 0
    assert len(model.calls) == 1
    assert model.calls[0].job_id == committed.job_ids[0]
    assert len(model.calls[0].evidence) == 1
    with store.connection() as connection:
        row = connection.execute(
            """
            SELECT status, attempt, idempotency_key
            FROM consolidation_jobs
            WHERE job_id = ?
            """,
            (committed.job_ids[0],),
        ).fetchone()
    assert row["status"] == "succeeded"
    assert row["attempt"] == 1
    assert row["idempotency_key"] == "session-consolidation:pet-1:turn-worker-1"

    mind.apply_control(
        CompanionControlCommand(
            action="reset_relationship",
            subject=subject,
            payload={
                "now": "2026-07-18T10:02:00+08:00",
                "idempotency_key": "worker-success-reset",
            },
        )
    )
    with store.connection() as connection:
        status_after_reset = connection.execute(
            "SELECT status FROM consolidation_jobs WHERE job_id = ?",
            (committed.job_ids[0],),
        ).fetchone()[0]
    assert status_after_reset == "succeeded"


def test_session_worker_ignores_model_chapter_statements_and_keeps_capsule(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"worker-non-chapter-statement",
        reflection_model=NonChapterJobWithChapterStatementModel(),
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="freshman",
        persistence_allowed=True,
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-explicit-followup",
            subject=subject,
            request_digest="digest-explicit-followup",
            surface="voice",
            occurred_at="2026-07-24T11:22:00+08:00",
        )
    )
    committed = mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="明天我会问问你。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=(
                {
                    "kind": "meaningful_moment",
                    "ownership_scope": "relationship",
                    "content": {"outcome": "followup_worthwhile"},
                    "source_summary": "用户明确希望小芯明天继续关心这件事。",
                    "attribution": "explicit_user_request",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": True,
                },
            ),
        ),
    )

    result = _run_due_work(mind, now="2026-07-24T11:23:00+08:00", limit=20)

    assert result.succeeded == 1
    assert result.failed == 0
    with store.connection() as connection:
        job = connection.execute(
            "SELECT status, failure_reason FROM consolidation_jobs WHERE job_id = ?",
            (committed.job_ids[0],),
        ).fetchone()
        capsule_count = connection.execute(
            "SELECT COUNT(*) FROM session_capsules"
        ).fetchone()[0]
    assert job["status"] == "succeeded"
    assert job["failure_reason"] is None
    assert capsule_count == 1


def test_two_workers_cannot_claim_the_same_leased_job(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    store = CompanionStore(database_path)
    model = BlockingReflectionModel()
    producer = CompanionMind(
        store=store,
        token_secret=b"worker-test-secret",
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
    prepared = producer.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-worker-concurrent",
            subject=subject,
            request_digest="digest-worker-concurrent",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    producer.commit_turn(
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
    first_worker = CompanionMind(
        store=CompanionStore(database_path),
        reflection_model=model,
    )
    second_worker = CompanionMind(
        store=CompanionStore(database_path),
        reflection_model=model,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            _run_due_work,
            first_worker,
            now="2026-07-18T10:01:00+08:00",
            limit=20,
        )
        assert model.started.wait(timeout=2)
        second_result = _run_due_work(
            second_worker,
            now="2026-07-18T10:01:01+08:00",
            limit=20,
        )
        model.release.set()
        first_result = first_future.result(timeout=2)

    assert first_result.claimed == 1
    assert first_result.succeeded == 1
    assert second_result.claimed == 0
    assert len(model.calls) == 1


def test_expired_worker_cannot_overwrite_new_lease_owner_result(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    store = CompanionStore(database_path)
    producer = CompanionMind(
        store=store,
        token_secret=b"worker-test-secret",
        reflection_model=EmptyReflectionModel(),
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )
    prepared = producer.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-worker-expired-owner",
            subject=subject,
            request_digest="digest-worker-expired-owner",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    committed = producer.commit_turn(
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
    stale_model = BlockingReflectionModel()
    current_model = EmptyReflectionModel()
    worker_config = CompanionWorkerConfig(lease_seconds=1)
    stale_worker = CompanionWorker(
        store=CompanionStore(database_path),
        reflection_model=stale_model,
        config=worker_config,
    )
    current_worker = CompanionWorker(
        store=CompanionStore(database_path),
        reflection_model=current_model,
        config=worker_config,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        stale_future = executor.submit(
            stale_worker.run_due_work,
            now="2026-07-18T10:01:00+08:00",
            limit=20,
        )
        assert stale_model.started.wait(timeout=2)
        current_future = executor.submit(
            current_worker.run_due_work,
            now="2026-07-18T10:01:01+08:00",
            limit=20,
        )
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with store.connection() as connection:
                attempt = connection.execute(
                    "SELECT attempt FROM consolidation_jobs WHERE job_id = ?",
                    (committed.job_ids[0],),
                ).fetchone()[0]
            if attempt == 2:
                break
            time.sleep(0.005)
        assert attempt == 2
        stale_model.release.set()
        current_result = current_future.result(timeout=2)
        stale_result = stale_future.result(timeout=2)

    assert current_result.succeeded == 1
    assert stale_result.succeeded == 0
    assert stale_result.failed == 1
    with store.connection() as connection:
        row = connection.execute(
            "SELECT status, attempt FROM consolidation_jobs WHERE job_id = ?",
            (committed.job_ids[0],),
        ).fetchone()
    assert row["status"] == "succeeded"
    assert row["attempt"] == 2


def test_expired_lease_cannot_apply_before_another_worker_reclaims_job(tmp_path):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    producer = CompanionMind(
        store=store,
        token_secret=b"worker-test-secret",
        reflection_model=EmptyReflectionModel(),
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )
    prepared = producer.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-worker-expired-unclaimed",
            subject=subject,
            request_digest="digest-worker-expired-unclaimed",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    committed = producer.commit_turn(
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
    job = store.claim_due_jobs(
        now="2026-07-18T10:01:00+08:00",
        limit=1,
        lease_seconds=1,
    )[0]
    evidence = store.load_job_evidence(
        job=job,
        now="2026-07-18T10:01:02+08:00",
    )
    proposal = ReflectionProposal(
        schema_version="companion-reflection-proposal-v1",
        safe_summary="过期 worker 不得提交这条整理结果。",
        evidence_ids=tuple(item.evidence_id for item in evidence),
    )

    with pytest.raises(CompanionJobLeaseLostError):
        store.apply_reflection_proposal(
            job=job,
            proposal=proposal,
            evidence_ids=proposal.evidence_ids,
            now="2026-07-18T10:01:02+08:00",
            model="test-model",
            prompt_version="companion-reflection-request-v1",
        )

    with store.connection() as connection:
        status = connection.execute(
            "SELECT status FROM consolidation_jobs WHERE job_id = ?",
            (committed.job_ids[0],),
        ).fetchone()[0]
        capsule_count = connection.execute(
            "SELECT COUNT(*) FROM session_capsules"
        ).fetchone()[0]
    assert status == "running"
    assert capsule_count == 0


def test_worker_uses_elapsed_time_when_applying_model_result(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    store = CompanionStore(database_path)
    producer = CompanionMind(store=store, token_secret=b"worker-test-secret")
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )
    prepared = producer.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-worker-elapsed-lease",
            subject=subject,
            request_digest="digest-worker-elapsed-lease",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    committed = producer.commit_turn(
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
    clock = AdvancingClock()
    worker = CompanionWorker(
        store=store,
        reflection_model=LeaseExpiringReflectionModel(clock),
        config=CompanionWorkerConfig(lease_seconds=1),
        monotonic_clock=clock,
    )

    result = worker.run_due_work(now="2026-07-18T10:01:00+08:00", limit=20)

    assert result.failed == 1
    assert result.succeeded == 0
    with store.connection() as connection:
        status = connection.execute(
            "SELECT status FROM consolidation_jobs WHERE job_id = ?",
            (committed.job_ids[0],),
        ).fetchone()[0]
        capsule_count = connection.execute(
            "SELECT COUNT(*) FROM session_capsules"
        ).fetchone()[0]
    assert status == "running"
    assert capsule_count == 0


def test_failed_model_call_retries_after_restart_when_backoff_is_due(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    store = CompanionStore(database_path)
    producer = CompanionMind(
        store=store,
        token_secret=b"worker-test-secret",
        reflection_model=FailingReflectionModel(),
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )
    prepared = producer.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-worker-retry",
            subject=subject,
            request_digest="digest-worker-retry",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    committed = producer.commit_turn(
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

    first = _run_due_work(
        producer, now="2026-07-18T10:01:00+08:00", limit=20
    )
    restarted_model = EmptyReflectionModel()
    restarted = CompanionMind(
        store=CompanionStore(database_path),
        reflection_model=restarted_model,
    )
    early = _run_due_work(
        restarted, now="2026-07-18T10:01:29+08:00", limit=20
    )
    due = _run_due_work(
        restarted, now="2026-07-18T10:01:30+08:00", limit=20
    )

    assert first.retried == 1
    assert early.claimed == 0
    assert due.claimed == 1
    assert due.succeeded == 1
    assert len(restarted_model.calls) == 1
    with store.connection() as connection:
        row = connection.execute(
            """
            SELECT status, attempt, next_attempt_at
            FROM consolidation_jobs WHERE job_id = ?
            """,
            (committed.job_ids[0],),
        ).fetchone()
    assert row["status"] == "succeeded"
    assert row["attempt"] == 2
    assert row["next_attempt_at"] is None


def test_expired_running_lease_is_reclaimed_after_process_crash(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    store = CompanionStore(database_path)
    producer = CompanionMind(
        store=store,
        token_secret=b"worker-test-secret",
        reflection_model=EmptyReflectionModel(),
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )
    prepared = producer.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-worker-crash",
            subject=subject,
            request_digest="digest-worker-crash",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    producer.commit_turn(
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
    crashed_claim = store.claim_due_jobs(
        now="2026-07-18T10:01:00+08:00",
        limit=20,
        lease_seconds=60,
    )
    restarted = CompanionMind(
        store=CompanionStore(database_path),
        reflection_model=EmptyReflectionModel(),
    )

    before_expiry = _run_due_work(
        restarted,
        now="2026-07-18T10:01:59+08:00",
        limit=20,
    )
    after_expiry = _run_due_work(
        restarted,
        now="2026-07-18T10:02:00+08:00",
        limit=20,
    )

    assert len(crashed_claim) == 1
    assert before_expiry.claimed == 0
    assert after_expiry.claimed == 1
    assert after_expiry.succeeded == 1
    with store.connection() as connection:
        attempt = connection.execute(
            "SELECT attempt FROM consolidation_jobs"
        ).fetchone()[0]
    assert attempt == 2


def test_relationship_reset_linearizes_after_evidence_load_before_remote_call(
    tmp_path,
):
    database_path = tmp_path / "xiaoxin_companion.db"
    producer_store = CompanionStore(database_path)
    producer = CompanionMind(
        store=producer_store,
        token_secret=b"worker-reset-race-test",
        reflection_model=EmptyReflectionModel(),
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )
    prepared = producer.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-worker-reset-race",
            subject=subject,
            request_digest="digest-worker-reset-race",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    committed = producer.commit_turn(
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
    model = EmptyReflectionModel()
    blocking_store = BlockingLoadStore(database_path)
    worker = CompanionWorker(store=blocking_store, reflection_model=model)

    with ThreadPoolExecutor(max_workers=2) as executor:
        work_future = executor.submit(
            worker.run_due_work,
            now="2026-07-18T10:01:00+08:00",
            limit=20,
        )
        assert blocking_store.load_started.wait(timeout=2)
        reset_future = executor.submit(
            producer.apply_control,
            CompanionControlCommand(
                action="reset_relationship",
                subject=subject,
                payload={
                    "now": "2026-07-18T10:01:01+08:00",
                    "idempotency_key": "worker-reset-race",
                },
            ),
        )
        try:
            reset_future.result(timeout=0.1)
        except FutureTimeoutError:
            pass
        else:
            raise AssertionError("reset must wait for reflection dispatch")
        blocking_store.release_load.set()
        work_result = work_future.result(timeout=2)
        reset_result = reset_future.result(timeout=2)

    assert work_result.succeeded == 1
    assert reset_result.status == "applied"
    assert len(model.calls) == 1
    with producer_store.connection() as connection:
        job = connection.execute(
            "SELECT status FROM consolidation_jobs WHERE job_id = ?",
            (committed.job_ids[0],),
        ).fetchone()
        evidence = connection.execute(
            """
            SELECT status, prompt_eligible
            FROM companion_evidence
            WHERE kind = 'meaningful_moment'
            """
        ).fetchone()
    assert job["status"] == "succeeded"
    assert tuple(evidence) == ("superseded", 0)


def test_forget_evidence_linearizes_after_load_before_remote_model_call(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    producer_store = CompanionStore(database_path)
    producer = CompanionMind(
        store=producer_store,
        token_secret=b"worker-forget-race-test",
        reflection_model=EmptyReflectionModel(),
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )
    prepared = producer.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-worker-forget-race",
            subject=subject,
            request_digest="digest-worker-forget-race",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    committed = producer.commit_turn(
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
    model = EmptyReflectionModel()
    blocking_store = BlockingLoadStore(database_path)
    worker = CompanionWorker(store=blocking_store, reflection_model=model)

    with ThreadPoolExecutor(max_workers=2) as executor:
        work_future = executor.submit(
            worker.run_due_work,
            now="2026-07-18T10:01:00+08:00",
            limit=20,
        )
        assert blocking_store.load_started.wait(timeout=2)
        forget_future = executor.submit(
            producer.apply_control,
            CompanionControlCommand(
                action="forget_evidence",
                subject=subject,
                payload={
                    "evidence_id": committed.evidence_ids[0],
                    "now": "2026-07-18T10:01:01+08:00",
                    "idempotency_key": "worker-forget-race",
                },
            ),
        )
        try:
            forget_future.result(timeout=0.1)
        except FutureTimeoutError:
            pass
        else:
            raise AssertionError("forget must wait for reflection dispatch")
        blocking_store.release_load.set()
        work_result = work_future.result(timeout=2)
        forget_result = forget_future.result(timeout=2)

    assert work_result.succeeded == 1
    assert forget_result.forgotten == 1
    assert len(model.calls) == 1
    with producer_store.connection() as connection:
        job_status = connection.execute(
            "SELECT status FROM consolidation_jobs WHERE job_id = ?",
            committed.job_ids,
        ).fetchone()[0]
        evidence_status = connection.execute(
            "SELECT status FROM companion_evidence WHERE evidence_id = ?",
            committed.evidence_ids,
        ).fetchone()[0]
    assert job_status == "succeeded"
    assert evidence_status == "forgotten"


def test_blocking_reflection_model_does_not_block_realtime_prepare_or_commit(tmp_path):
    database_path = tmp_path / "xiaoxin_companion.db"
    model = BlockingReflectionModel()
    mind = CompanionMind(
        store=CompanionStore(database_path),
        token_secret=b"worker-test-secret",
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
    first_prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-worker-blocking-1",
            subject=subject,
            request_digest="digest-worker-blocking-1",
            surface="voice",
            occurred_at="2026-07-18T10:00:00+08:00",
        )
    )
    mind.commit_turn(
        first_prepared,
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

    with ThreadPoolExecutor(max_workers=1) as executor:
        work_future = executor.submit(
            _run_due_work,
            mind,
            now="2026-07-18T10:01:00+08:00",
            limit=20,
        )
        assert model.started.wait(timeout=2)
        second_prepared = mind.prepare_turn(
            CompanionTurnRequest(
                turn_id="turn-worker-blocking-2",
                subject=subject,
                request_digest="digest-worker-blocking-2",
                surface="voice",
                occurred_at="2026-07-18T10:01:01+08:00",
            )
        )
        second_committed = mind.commit_turn(
            second_prepared,
            CompanionTurnOutcome(
                visible_response="实时回复继续。",
                assistant_action="reply",
                delivery_status="generated",
            ),
        )
        model.release.set()
        work_result = work_future.result(timeout=2)

    assert second_committed.status == "committed"
    assert work_result.succeeded == 1


def test_worker_logs_job_metadata_without_evidence_or_user_text(tmp_path, caplog):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    mind = CompanionMind(
        store=store,
        token_secret=b"worker-test-secret",
        reflection_model=SensitiveFailingReflectionModel(),
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
            turn_id="turn-worker-private-log",
            subject=subject,
            request_digest="digest-worker-private-log",
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
                    "content": {"private_detail": "我的银行卡密码是 123456"},
                    "source_summary": "用户提供了一段敏感原文。",
                    "attribution": "observed_interaction",
                    "confidence": 1.0,
                    "retention": "long_term",
                    "prompt_eligible": False,
                },
            ),
        ),
    )
    caplog.set_level(logging.WARNING)

    result = _run_due_work(mind, now="2026-07-18T10:01:00+08:00", limit=20)

    assert result.retried == 1
    assert committed.job_ids[0] in caplog.records[0].companion_job_id
    assert "我的银行卡密码" not in caplog.text
    assert "123456" not in caplog.text
    assert "用户提供了一段敏感原文" not in caplog.text


def test_explicit_style_feedback_creates_candidates_without_reflection_model_call(
    tmp_path,
):
    store = CompanionStore(tmp_path / "xiaoxin_companion.db")
    model = EmptyReflectionModel()
    mind = CompanionMind(
        store=store,
        token_secret=b"worker-explicit-style-feedback",
        reflection_model=model,
    )
    subject = CompanionSubjectContext(
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        speaker_identity="confirmed",
        academic_stage="freshman",
        persistence_allowed=True,
    )
    prepared = mind.prepare_turn(
        CompanionTurnRequest(
            turn_id="turn-explicit-style-feedback",
            subject=subject,
            request_digest="digest-explicit-style-feedback",
            surface="voice",
            occurred_at="2026-08-04T19:00:00+08:00",
        )
    )
    signals = tuple(
        signal
        for signal in explicit_companion_feedback_signals(
            "这几次相处下来，我平时还是喜欢你回答短一点、少追问、简洁收尾。"
        )
        if signal["kind"] == "preference_feedback"
    )
    committed = mind.commit_turn(
        prepared,
        CompanionTurnOutcome(
            visible_response="明白了。",
            assistant_action="reply",
            delivery_status="generated",
            feedback_signals=signals,
        ),
    )

    result = _run_due_work(mind, now="2026-08-04T19:00:01+08:00", limit=20)

    assert len(committed.job_ids) == 1
    assert result.succeeded == 1
    assert model.calls == []
    with store.connection() as connection:
        adjustments = connection.execute(
            """
            SELECT dimension, value_json, behavior_key, context_scope,
                   direction, status, generated_by
            FROM companion_adjustments
            ORDER BY dimension
            """
        ).fetchall()
        qualifications = connection.execute(
            """
            SELECT qualification, reason_code, qualifying_local_date,
                   contributes_date
            FROM adjustment_evidence_qualification
            ORDER BY adjustment_id
            """
        ).fetchall()

    assert [tuple(row) for row in adjustments] == [
        (
            "closure_style",
            '{"value":"concise"}',
            "conversation_closure",
            "conversation",
            "decrease",
            "candidate",
            "deterministic-explicit-preference-feedback",
        ),
        (
            "question_frequency",
            '{"value":"less"}',
            "follow_up_question",
            "conversation",
            "decrease",
            "candidate",
            "deterministic-explicit-preference-feedback",
        ),
        (
            "response_length",
            '{"value":"short"}',
            "response_length",
            "conversation",
            "decrease",
            "candidate",
            "deterministic-explicit-preference-feedback",
        ),
    ]
    assert [tuple(row) for row in qualifications] == [
        ("eligible", "specific_first_party_feedback", "2026-08-04", 1),
        ("eligible", "specific_first_party_feedback", "2026-08-04", 1),
        ("eligible", "specific_first_party_feedback", "2026-08-04", 1),
    ]
