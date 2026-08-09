from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import logging
from time import monotonic
from typing import Callable
from zoneinfo import ZoneInfo

from .contracts import CompanionEvidence, CompanionWorkResult
from .model_harness import StructuredOutputError
from .reflection import (
    REFLECTION_REQUEST_VERSION,
    AdjustmentProposal,
    ReflectionEvidence,
    ReflectionModel,
    ReflectionProposal,
    ReflectionRequest,
    ReflectionValidationError,
    validate_reflection_proposal,
)
from .store import CompanionJobLeaseLostError, CompanionStore
from .semantic_memory import (
    MemoryInterpretationError,
    MemoryInterpreter,
)


LOGGER = logging.getLogger(__name__)


_MEANINGFUL_SESSION_EVIDENCE_KINDS = frozenset(
    {
        "accepted_help",
        "explicit_boundary",
        "followup_completed",
        "interaction_feedback",
        "meaningful_moment",
        "preference_feedback",
    }
)
_DETERMINISTIC_RECOMPUTE_JOB_KINDS = frozenset(
    {"recompute_after_correction", "recompute_after_forget"}
)
_CHAPTER_JOB_KINDS = frozenset({"academic_stage_changed", "narrative_boundary"})
_SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _has_structured_output_cause(exc: BaseException) -> bool:
    cause = exc.__cause__
    while cause is not None:
        if isinstance(cause, StructuredOutputError):
            return True
        cause = cause.__cause__
    return False


def _deterministic_preference_feedback_proposal(
    evidence: tuple[CompanionEvidence, ...],
) -> ReflectionProposal | None:
    adjustments: list[AdjustmentProposal] = []
    evidence_ids: list[str] = []
    adjustment_keys: set[tuple[str, str]] = set()
    for item in evidence:
        if item.kind != "preference_feedback":
            continue
        content = item.content
        dimension = content.get("dimension")
        value = content.get("value")
        scope = content.get("scope")
        if not all(
            isinstance(field, str) and field.strip()
            for field in (dimension, value, scope)
        ):
            raise ReflectionValidationError(
                "structured preference feedback is incomplete"
            )
        adjustment_key = (dimension, scope)
        if adjustment_key in adjustment_keys:
            raise ReflectionValidationError(
                "structured preference feedback is contradictory"
            )
        adjustment_keys.add(adjustment_key)
        evidence_ids.append(item.evidence_id)
        adjustments.append(
            AdjustmentProposal(
                dimension=dimension,
                value=value,
                scope=scope,
                evidence_ids=(item.evidence_id,),
                confidence=item.confidence,
            )
        )
    if not adjustments:
        return None
    return ReflectionProposal(
        schema_version="companion-reflection-proposal-v1",
        safe_summary="用户给出了明确、具体且可核对的相处偏好。",
        evidence_ids=tuple(evidence_ids),
        adjustments=tuple(adjustments),
    )


@dataclass(frozen=True)
class CompanionWorkerConfig:
    lease_seconds: int = 60
    max_attempts: int = 5
    initial_backoff_seconds: int = 30
    maximum_backoff_seconds: int = 3600


class CompanionWorker:
    def __init__(
        self,
        *,
        store: CompanionStore,
        reflection_model: ReflectionModel | None = None,
        memory_interpreter: MemoryInterpreter | None = None,
        memory_interpreter_mode: str = "off",
        config: CompanionWorkerConfig = CompanionWorkerConfig(),
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        self._store = store
        self._reflection_model = reflection_model
        self._memory_interpreter = memory_interpreter
        self._memory_interpreter_mode = memory_interpreter_mode
        self._config = config
        self._monotonic_clock = monotonic_clock

    def run_due_work(
        self, *, now: str, limit: int, pet_id: str | None = None
    ) -> CompanionWorkResult:
        logical_start = datetime.fromisoformat(now)
        monotonic_start = self._monotonic_clock()

        def current_logical_now() -> str:
            elapsed_seconds = max(
                int(self._monotonic_clock() - monotonic_start),
                0,
            )
            return (logical_start + timedelta(seconds=elapsed_seconds)).isoformat()

        if pet_id is None:
            self._store.expire_derived_objects(now=now)
        else:
            self._store.expire_derived_objects_for_pet(pet_id=pet_id, now=now)
        jobs = self._store.claim_due_jobs(
            now=now,
            limit=limit,
            lease_seconds=self._config.lease_seconds,
            pet_id=pet_id,
        )
        succeeded = 0
        retried = 0
        failed = 0
        for job in jobs:
            job_now = current_logical_now()
            with self._store.pet_reflection_guard(job.pet_id):
                try:
                    if (
                        job.job_kind == "memory_candidate_extraction"
                        and self._memory_interpreter is not None
                        and self._memory_interpreter_mode != "off"
                    ):
                        request = self._store.load_memory_interpretation_request(
                            job=job,
                            now=job_now,
                        )
                        if request is None:
                            self._store.mark_job_succeeded(
                                job=job,
                                now=job_now,
                                model="deterministic-source-unavailable",
                                prompt_version=None,
                            )
                            succeeded += 1
                            continue
                        effective_mode, release_guard_reason = (
                            self._store.semantic_memory_effective_mode(
                                requested_mode=self._memory_interpreter_mode
                            )
                        )
                        interpretation_started = self._monotonic_clock()
                        result = self._memory_interpreter.interpret(request)
                        interpretation_duration_ms = max(
                            (
                                self._monotonic_clock()
                                - interpretation_started
                            )
                            * 1000,
                            0.0,
                        )
                        job_now = current_logical_now()
                        self._store.apply_semantic_memory_result(
                            job=job,
                            request=request,
                            result=result,
                            mode=effective_mode,
                            now=job_now,
                            model=self._memory_interpreter.model_name,
                            prompt_version=self._memory_interpreter.prompt_version,
                            duration_ms=interpretation_duration_ms,
                            release_guard_reason=release_guard_reason,
                            explicit_correction_release_enabled=(
                                self._memory_interpreter_mode == "active_explicit"
                            ),
                        )
                        succeeded += 1
                        continue
                    if job.job_kind in _DETERMINISTIC_RECOMPUTE_JOB_KINDS:
                        self._store.recompute_adjustments_after_evidence_change(
                            job=job,
                            now=job_now,
                        )
                        succeeded += 1
                        continue
                    turn_sources = ()
                    if job.job_kind == "memory_candidate_extraction":
                        turn_sources = self._store.load_turn_sources(
                            job=job,
                            now=job_now,
                        )
                        if not turn_sources:
                            self._store.mark_job_succeeded(
                                job=job,
                                now=job_now,
                                model="deterministic-source-unavailable",
                                prompt_version=None,
                            )
                            succeeded += 1
                            continue
                        evidence = ()
                    elif job.job_kind in _CHAPTER_JOB_KINDS:
                        evidence = self._store.load_chapter_evidence(
                            job=job, now=job_now
                        )
                        if not _chapter_evidence_qualified(evidence):
                            self._store.mark_job_succeeded(
                                job=job,
                                now=job_now,
                                model="deterministic-insufficient-chapter",
                                prompt_version=None,
                            )
                            succeeded += 1
                            continue
                    else:
                        evidence = self._store.load_job_evidence(job=job, now=job_now)
                    requested_evidence_ids = job.payload.get("evidence_ids", ())
                    if isinstance(requested_evidence_ids, list) and set(
                        requested_evidence_ids
                    ) != {item.evidence_id for item in evidence}:
                        raise ReflectionValidationError(
                            "job Evidence is no longer active"
                        )
                    if (
                        job.job_kind == "session_consolidation"
                        and not _has_meaningful_session_outcome(evidence)
                    ):
                        self._store.mark_job_succeeded(
                            job=job,
                            now=job_now,
                            model="deterministic-session-filter",
                            prompt_version=None,
                        )
                        succeeded += 1
                        continue
                    request = ReflectionRequest(
                        job_id=job.job_id,
                        job_kind=(
                            "academic_stage_changed"
                            if job.job_kind == "narrative_boundary"
                            else job.job_kind
                        ),
                        pet_id=job.pet_id,
                        relationship_epoch_id=job.relationship_epoch_id,
                        evidence=tuple(
                            ReflectionEvidence(
                                evidence_id=item.evidence_id,
                                kind=item.kind,
                                ownership_scope=item.ownership_scope,
                                source_summary=item.source_summary,
                                confidence=item.confidence,
                            )
                            for item in evidence
                        ),
                        turn_sources=turn_sources,
                        schema_version=REFLECTION_REQUEST_VERSION,
                    )
                    deterministic_proposal = (
                        _deterministic_preference_feedback_proposal(evidence)
                    )
                    if deterministic_proposal is not None:
                        proposal = deterministic_proposal
                        model_name = "deterministic-explicit-preference-feedback"
                        prompt_version = "companion-explicit-preference-feedback-v1"
                    else:
                        if self._reflection_model is None:
                            raise ReflectionValidationError(
                                "reflection model is unavailable"
                            )
                        proposal = self._reflection_model.reflect(request)
                        model_name = getattr(
                            self._reflection_model,
                            "model_name",
                            type(self._reflection_model).__name__,
                        )
                        if not isinstance(model_name, str) or not model_name:
                            model_name = type(self._reflection_model).__name__
                        prompt_version_for = getattr(
                            self._reflection_model, "prompt_version_for", None
                        )
                        prompt_version = (
                            prompt_version_for(request)
                            if callable(prompt_version_for)
                            else None
                        )
                    job_now = current_logical_now()
                    if (
                        job.job_kind not in _CHAPTER_JOB_KINDS
                        and proposal.chapter_statements
                    ):
                        LOGGER.warning(
                            "Ignoring chapter statements from non-chapter reflection job",
                            extra={"companion_job_id": job.job_id},
                        )
                        proposal = replace(proposal, chapter_statements=())
                    validate_reflection_proposal(request, proposal)
                    if job.job_kind == "memory_candidate_extraction":
                        self._store.apply_memory_candidate_proposal(
                            job=job,
                            proposal=proposal,
                            now=job_now,
                            model=model_name,
                            prompt_version=prompt_version,
                        )
                        succeeded += 1
                        continue
                    referenced_ids = set(proposal.evidence_ids)
                    for adjustment in proposal.adjustments:
                        referenced_ids.update(adjustment.evidence_ids)
                    if not self._store.job_evidence_is_still_active(
                        job=job,
                        evidence_ids=tuple(sorted(referenced_ids)),
                        now=job_now,
                    ):
                        raise ReflectionValidationError(
                            "proposal Evidence is no longer active"
                        )
                    if job.job_kind == "session_consolidation" and referenced_ids:
                        self._store.apply_reflection_proposal(
                            job=job,
                            proposal=proposal,
                            evidence_ids=tuple(sorted(referenced_ids)),
                            now=job_now,
                            model=model_name,
                            prompt_version=prompt_version,
                        )
                    elif job.job_kind in _CHAPTER_JOB_KINDS:
                        self._store.apply_chapter_proposal(
                            job=job,
                            proposal=proposal,
                            evidence_ids=tuple(sorted(referenced_ids)),
                            now=job_now,
                            model=model_name,
                            prompt_version=prompt_version,
                        )
                    else:
                        self._store.mark_job_succeeded(
                            job=job,
                            now=job_now,
                            model=model_name,
                            prompt_version=prompt_version,
                        )
                    succeeded += 1
                except CompanionJobLeaseLostError:
                    failed += 1
                    LOGGER.warning(
                        "Companion reflection job lease lost",
                        extra={
                            "companion_job_id": job.job_id,
                            "companion_attempt": job.attempt,
                        },
                    )
                except Exception as exc:
                    job_now = current_logical_now()
                    is_permanent_validation_error = isinstance(
                        exc,
                        (ReflectionValidationError, MemoryInterpretationError),
                    ) and not _has_structured_output_cause(exc)
                    try:
                        if is_permanent_validation_error:
                            self._store.mark_job_failed(
                                job=job,
                                now=job_now,
                                reason=str(exc),
                            )
                            failed += 1
                        elif job.attempt >= self._config.max_attempts:
                            self._store.mark_job_failed(
                                job=job,
                                now=job_now,
                                reason=type(exc).__name__,
                            )
                            failed += 1
                        else:
                            backoff = min(
                                self._config.initial_backoff_seconds
                                * (2 ** max(job.attempt - 1, 0)),
                                self._config.maximum_backoff_seconds,
                            )
                            next_attempt_at = (
                                datetime.fromisoformat(job_now)
                                + timedelta(seconds=backoff)
                            ).isoformat()
                            self._store.mark_job_retry(
                                job=job,
                                now=job_now,
                                next_attempt_at=next_attempt_at,
                                reason=type(exc).__name__,
                            )
                            retried += 1
                    except CompanionJobLeaseLostError:
                        failed += 1
                        LOGGER.warning(
                            "Companion reflection job lease lost",
                            extra={
                                "companion_job_id": job.job_id,
                                "companion_attempt": job.attempt,
                            },
                        )
                    if is_permanent_validation_error:
                        LOGGER.warning(
                            "Companion reflection proposal rejected: %s",
                            str(exc),
                            extra={"companion_job_id": job.job_id},
                        )
                    else:
                        LOGGER.warning(
                            "Companion reflection execution failed",
                            extra={
                                "companion_job_id": job.job_id,
                                "companion_attempt": job.attempt,
                                "companion_error_type": type(exc).__name__,
                            },
                        )
        return CompanionWorkResult(
            claimed=len(jobs),
            succeeded=succeeded,
            retried=retried,
            failed=failed,
        )


def _has_meaningful_session_outcome(
    evidence: tuple[CompanionEvidence, ...],
) -> bool:
    for item in evidence:
        if item.kind in _MEANINGFUL_SESSION_EVIDENCE_KINDS:
            return True
        if item.kind == "followup" and item.content.get("status") == "completed":
            return True
    return False


def _chapter_evidence_qualified(
    evidence: tuple[CompanionEvidence, ...],
) -> bool:
    return (
        2 <= len(evidence) <= 3
        and any(item.ownership_scope == "relationship" for item in evidence)
        and len(
            {
                datetime.fromisoformat(item.occurred_at)
                .astimezone(_SHANGHAI_TIMEZONE)
                .date()
                for item in evidence
            }
        )
        >= 2
    )
