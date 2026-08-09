from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import BinaryIO, Iterator, Mapping
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

from .academic import (
    ACADEMIC_STATUSES,
    ACADEMIC_TRANSITION_KINDS,
    AcademicState,
    resolve_academic_transition,
)
from .contracts import (
    BehaviorAdjustmentSignal,
    BirthTemperament,
    CompanionCommitResult,
    CompanionControlResult,
    CompanionEvidence,
    CompanionIdempotencyConflict,
    CompanionObservation,
    CompanionObserveResult,
    CompanionSubjectContext,
    CompanionVAEvent,
    CompanionVAEventResult,
    TemperamentSourceKind,
    RelationshipEpoch,
    CompanionTurnOutcome,
    PreparedCompanionTurn,
    xiaoxin_age_for_stage,
)
from .initiative_timing import (
    default_initiative_level,
    rescale_connection_threshold,
)
from .temperament import (
    generate_birth_temperament,
    temperament_matches_generation,
)
from .reflection import (
    ALLOWED_ADJUSTMENT_VALUES,
    REFLECTION_REQUEST_VERSION,
    ReflectionProposal,
    ReflectionRequest,
    ReflectionTurnSource,
    ReflectionValidationError,
    validate_reflection_proposal,
)
from .semantic_memory import (
    canonical_memory_fact_key,
    MEMORY_INTERPRETATION_MAX_EXISTING_FACTS,
    MemoryExistingFact,
    MemoryInterpretationRequest,
    MemoryInterpretationResult,
    MemoryProposal,
    MemorySource,
    MemoryWritePolicy,
    memory_fact_key_storage_aliases,
    memory_fact_replacement_is_authorized,
    memory_proposal_is_naturally_persistent,
)
from .va import MODEL_VERSION as VA_MODEL_VERSION
from .va import VAState, apply_event as apply_va_state_event
from .va import baseline as baseline_va_state
from .va import decay as decay_va_state
from .va import semantic_projection as project_va_state


SCHEMA_VERSION = 23
_SEMANTIC_MEMORY_RECOVERY_SUCCESS_STREAK = 10
_EMOTIONAL_SUPPORT_CONTEXTS = frozenset({"user_low_mood", "serious"})
_EMOTIONAL_SUPPORT_SUPERSEDED_ADJUSTMENTS = frozenset(
    {"response_length", "closure_style", "emotional_posture"}
)
_NARRATIVE_WINDOW_DAYS = {
    "academic_growth": 30,
    "academic_reorientation": 30,
    "anniversary": 14,
    "graduation": 90,
}

_SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
_ADJUSTMENT_DIRECTIONS = frozenset({"increase", "decrease"})
_RELATIONSHIP_STAGE_ORDER = {
    "first_meeting": 0,
    "familiar": 1,
    "attuned": 2,
    "long_term_companion": 3,
}
_ADJUSTMENT_BEHAVIOR_DIMENSIONS = {
    "response_length": "response_length",
    "follow_up_question": "question_frequency",
    "proactive_initiative": "initiative_level",
    "memory_reference": "memory_reference_depth",
    "emotional_posture": "emotional_posture",
    "humor": "humor_level",
    "conversation_closure": "closure_style",
    "hardware_expression": "hardware_expression_intensity",
}
_ADJUSTMENT_QUALIFYING_KINDS = frozenset(
    {"interaction_feedback", "preference_feedback"}
)
_ADJUSTMENT_CLUE_ONLY_KINDS = frozenset(
    {
        "accepted_help",
        "followup_completed",
        "interaction_outcome",
        "response_reaction",
    }
)
_ADJUSTMENT_REJECTED_KINDS = frozenset(
    {
        "assistant_action",
        "current_mood",
        "future_event",
        "goal",
        "meaningful_moment",
        "model_inference",
        "profile_fact",
        "recent_conversation",
        "system_event",
    }
)
_REJECTED_CLAIM_CONTEXT_REASONS = {
    "reported": "reported_speech_rejected",
    "hypothetical": "hypothetical_rejected",
    "joke": "joke_rejected",
    "quoted": "quoted_text_rejected",
    "asr_uncertain": "asr_uncertain_rejected",
}

_CONNECTION_IGNORE_BACKOFF_FACTORS = {
    "reserved": 2.5,
    "timely": 2.0,
    "proactive": 1.5,
}
_CONNECTION_IGNORE_BACKOFF_CAP_SECONDS = 30 * 24 * 60 * 60
_CONNECTION_REJECTION_COOLDOWN = timedelta(days=7)
_BOOT_CHECKIN_DELIVERY_RETRY_DELAYS = {
    1: timedelta(minutes=15),
    2: timedelta(hours=1),
}


_LOGGER = logging.getLogger(__name__)


def _is_local_hhmm(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(
        r"(?:[01]\d|2[0-3]):[0-5]\d",
        value,
    ) is not None


_PET_REFLECTION_LOCKS: dict[str, threading.RLock] = {}
_PET_REFLECTION_LOCKS_GUARD = threading.Lock()
_EXPLICIT_MEMORY_CORRECTION_MARKERS = (
    "更新一下",
    "更正一下",
    "更正为",
    "纠正一下",
    "纠正为",
    "改成",
    "更新成",
    "替代旧的",
    "替代之前的",
    "替代原来的",
)
_EXPLICIT_MEMORY_REQUEST_MARKERS = (
    "请记住",
    "请明确记住",
    "帮我记住",
    "替我记住",
    "记一下",
    "记住这件事",
)
_GOAL_TRANSITION_END_MARKERS = (
    "已经结束",
    "已结束",
    "结束了",
    "已经完成",
    "已完成",
    "完成了",
    "不再继续",
    "取消了",
)
_GOAL_TRANSITION_NEXT_MARKERS = (
    "我接下来",
    "接下来",
    "下一步",
    "之后改为",
    "现在改为",
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS companion_pets (
    pet_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relationship_epochs (
    epoch_id TEXT PRIMARY KEY,
    pet_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    start_reason TEXT NOT NULL,
    end_reason TEXT,
    UNIQUE(epoch_id, pet_id),
    CHECK (
        (ended_at IS NULL AND end_reason IS NULL)
        OR (ended_at IS NOT NULL AND end_reason IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_relationship_epochs_active_pet
ON relationship_epochs(pet_id)
WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS companion_turns (
    turn_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    relationship_epoch_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    outcome_digest TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY(turn_id, pet_id),
    FOREIGN KEY(relationship_epoch_id, pet_id)
        REFERENCES relationship_epochs(epoch_id, pet_id)
);

CREATE TABLE IF NOT EXISTS companion_evidence (
    evidence_id TEXT PRIMARY KEY,
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    ownership_scope TEXT NOT NULL
        CHECK (ownership_scope IN ('user', 'relationship')),
    relationship_epoch_id TEXT,
    kind TEXT NOT NULL,
    content_json TEXT NOT NULL,
    content_version INTEGER NOT NULL DEFAULT 1 CHECK (content_version >= 1),
    fact_key TEXT,
    importance REAL NOT NULL DEFAULT 0.5
        CHECK (importance >= 0.0 AND importance <= 1.0),
    sensitivity TEXT NOT NULL DEFAULT 'private'
        CHECK (sensitivity IN ('low', 'private', 'sensitive')),
    valid_from TEXT,
    valid_until TEXT,
    source_kind TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    source_summary TEXT NOT NULL,
    attribution TEXT NOT NULL,
    speaker_identity TEXT NOT NULL DEFAULT 'confirmed'
        CHECK (speaker_identity IN ('confirmed', 'unknown', 'invalid')),
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    occurred_at TEXT NOT NULL,
    retention TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('candidate', 'active', 'superseded', 'forgotten', 'expired')),
    prompt_eligible INTEGER NOT NULL CHECK (prompt_eligible IN (0, 1)),
    expires_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(evidence_id, pet_id),
    FOREIGN KEY(relationship_epoch_id, pet_id)
        REFERENCES relationship_epochs(epoch_id, pet_id),
    CHECK (
        (ownership_scope = 'user' AND relationship_epoch_id IS NULL)
        OR (ownership_scope = 'relationship' AND relationship_epoch_id IS NOT NULL)
    ),
    CHECK (
        status NOT IN ('superseded', 'forgotten', 'expired')
        OR prompt_eligible = 0
    )
);

CREATE INDEX IF NOT EXISTS idx_companion_evidence_recall
ON companion_evidence(
    pet_id,
    memory_subject_id,
    relationship_epoch_id,
    status,
    prompt_eligible,
    occurred_at
);

CREATE VIRTUAL TABLE IF NOT EXISTS companion_evidence_fts USING fts5(
    evidence_id UNINDEXED,
    pet_id UNINDEXED,
    memory_subject_id UNINDEXED,
    fact_key,
    source_summary,
    content_json,
    tokenize='trigram'
);

CREATE TABLE IF NOT EXISTS companion_retrieval_audits (
    audit_id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    relationship_epoch_id TEXT NOT NULL,
    interaction_kind TEXT NOT NULL,
    query_digest TEXT NOT NULL,
    hints_digest TEXT NOT NULL,
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    selected_evidence_ids_json TEXT NOT NULL,
    score_details_json TEXT NOT NULL,
    duration_ms REAL NOT NULL CHECK (duration_ms >= 0.0),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE(turn_id, pet_id)
);

CREATE INDEX IF NOT EXISTS idx_companion_retrieval_audits_subject
ON companion_retrieval_audits(
    pet_id, memory_subject_id, created_at
);

CREATE INDEX IF NOT EXISTS idx_companion_retrieval_audits_expiry
ON companion_retrieval_audits(expires_at);

CREATE TABLE IF NOT EXISTS relationship_stage_events (
    event_id TEXT PRIMARY KEY,
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    relationship_epoch_id TEXT NOT NULL,
    previous_stage TEXT,
    relationship_stage TEXT NOT NULL CHECK (
        relationship_stage IN (
            'first_meeting', 'familiar', 'attuned', 'long_term_companion'
        )
    ),
    quality_json TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(relationship_epoch_id, pet_id)
        REFERENCES relationship_epochs(epoch_id, pet_id)
);

CREATE INDEX IF NOT EXISTS idx_relationship_stage_events_subject
ON relationship_stage_events(
    pet_id, memory_subject_id, relationship_epoch_id, occurred_at
);

CREATE TABLE IF NOT EXISTS companion_va_snapshots (
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    relationship_epoch_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    valence INTEGER NOT NULL CHECK (valence BETWEEN -1000 AND 1000),
    arousal INTEGER NOT NULL CHECK (arousal BETWEEN -1000 AND 1000),
    observed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    dynamics_age INTEGER CHECK (dynamics_age BETWEEN 1 AND 4),
    dynamics_relationship_stage TEXT NOT NULL CHECK (
        dynamics_relationship_stage IN (
            'first_meeting', 'familiar', 'attuned', 'long_term_companion'
        )
    ),
    context TEXT NOT NULL CHECK (
        context IN (
            'ordinary', 'celebration', 'supportive_settled', 'receptive_brief'
        )
    ),
    PRIMARY KEY(pet_id, memory_subject_id),
    FOREIGN KEY(relationship_epoch_id, pet_id)
        REFERENCES relationship_epochs(epoch_id, pet_id)
);

CREATE TABLE IF NOT EXISTS companion_va_events (
    event_id TEXT PRIMARY KEY,
    pet_id TEXT NOT NULL,
    relationship_epoch_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('applied', 'ignored_out_of_order', 'ignored_stale_epoch')
    ),
    created_at TEXT NOT NULL,
    FOREIGN KEY(relationship_epoch_id, pet_id)
        REFERENCES relationship_epochs(epoch_id, pet_id)
);

CREATE INDEX IF NOT EXISTS idx_companion_va_events_subject
ON companion_va_events(pet_id, relationship_epoch_id, created_at);

CREATE TABLE IF NOT EXISTS companion_interaction_contracts (
    contract_id TEXT PRIMARY KEY,
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    dimension TEXT NOT NULL,
    value_json TEXT NOT NULL,
    scope TEXT NOT NULL,
    safe_label TEXT NOT NULL,
    safe_scope TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(pet_id, memory_subject_id, dimension, scope)
);

CREATE INDEX IF NOT EXISTS idx_companion_interaction_contracts_subject
ON companion_interaction_contracts(pet_id, memory_subject_id, status);

CREATE TABLE IF NOT EXISTS evidence_relations (
    relation_id TEXT PRIMARY KEY,
    pet_id TEXT NOT NULL,
    relation_kind TEXT NOT NULL,
    source_evidence_id TEXT NOT NULL,
    target_evidence_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(relation_kind, source_evidence_id, target_evidence_id),
    FOREIGN KEY(source_evidence_id, pet_id)
        REFERENCES companion_evidence(evidence_id, pet_id),
    FOREIGN KEY(target_evidence_id, pet_id)
        REFERENCES companion_evidence(evidence_id, pet_id),
    CHECK (source_evidence_id <> target_evidence_id)
);

CREATE TRIGGER IF NOT EXISTS trg_evidence_relations_no_update
BEFORE UPDATE ON evidence_relations
BEGIN
    SELECT RAISE(ABORT, 'evidence relations are immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_evidence_relations_no_delete
BEFORE DELETE ON evidence_relations
BEGIN
    SELECT RAISE(ABORT, 'evidence relations are immutable');
END;

CREATE TABLE IF NOT EXISTS companion_observations (
    observation_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    owner_user_id TEXT NOT NULL,
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    observation_digest TEXT NOT NULL,
    safe_summary TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('recorded')),
    created_at TEXT NOT NULL,
    UNIQUE(observation_id, pet_id)
);

CREATE TABLE IF NOT EXISTS companion_turn_sources (
    turn_id TEXT NOT NULL,
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    source_text TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY(turn_id, pet_id),
    FOREIGN KEY(turn_id, pet_id)
        REFERENCES companion_turns(turn_id, pet_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_companion_turn_sources_expiry
ON companion_turn_sources(expires_at);

CREATE TABLE IF NOT EXISTS companion_context_messages (
    message_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    conversation_digest TEXT NOT NULL,
    source_text TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY(message_id, pet_id),
    FOREIGN KEY(turn_id, pet_id)
        REFERENCES companion_turns(turn_id, pet_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_companion_context_messages_lookup
ON companion_context_messages(
    pet_id, memory_subject_id, conversation_digest, occurred_at
);

CREATE INDEX IF NOT EXISTS idx_companion_context_messages_expiry
ON companion_context_messages(expires_at);

CREATE TABLE IF NOT EXISTS companion_context_job_pins (
    job_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    pet_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(job_id, message_id, pet_id),
    FOREIGN KEY(job_id) REFERENCES consolidation_jobs(job_id) ON DELETE CASCADE,
    FOREIGN KEY(message_id, pet_id)
        REFERENCES companion_context_messages(message_id, pet_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS semantic_memory_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE,
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (
        mode IN ('shadow', 'candidate', 'active_explicit')
    ),
    release_guard_reason TEXT NOT NULL,
    proposal_count INTEGER NOT NULL CHECK (proposal_count >= 0),
    action_counts_json TEXT NOT NULL,
    reason_counts_json TEXT NOT NULL,
    claim_type_counts_json TEXT NOT NULL,
    legacy_fact_keys_digest TEXT NOT NULL,
    legacy_fact_count INTEGER NOT NULL CHECK (legacy_fact_count >= 0),
    semantic_fact_keys_digest TEXT NOT NULL,
    conflict_count INTEGER NOT NULL CHECK (conflict_count >= 0),
    duration_ms REAL NOT NULL CHECK (duration_ms >= 0.0),
    model TEXT NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_semantic_memory_evaluations_subject
ON semantic_memory_evaluations(
    pet_id, memory_subject_id, created_at
);

CREATE INDEX IF NOT EXISTS idx_companion_observations_subject
ON companion_observations(pet_id, memory_subject_id, occurred_at);

CREATE TABLE IF NOT EXISTS observation_evidence (
    observation_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    pet_id TEXT NOT NULL,
    PRIMARY KEY(observation_id, evidence_id),
    FOREIGN KEY(observation_id, pet_id)
        REFERENCES companion_observations(observation_id, pet_id),
    FOREIGN KEY(evidence_id, pet_id)
        REFERENCES companion_evidence(evidence_id, pet_id)
);

CREATE TABLE IF NOT EXISTS pending_companion_observations (
    observation_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    owner_user_id TEXT NOT NULL,
    pet_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    pending_digest TEXT NOT NULL,
    safe_summary TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    queued_reason TEXT NOT NULL
        CHECK (queued_reason IN ('missing_subject', 'ambiguous_subject')),
    status TEXT NOT NULL CHECK (status IN ('pending')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error_code TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pending_companion_observations_owner
ON pending_companion_observations(owner_user_id, pet_id, occurred_at);

CREATE TABLE IF NOT EXISTS session_capsules (
    capsule_id TEXT PRIMARY KEY,
    pet_id TEXT NOT NULL,
    relationship_epoch_id TEXT NOT NULL,
    safe_summary TEXT NOT NULL,
    interaction_outcome TEXT NOT NULL,
    adjustment_signals_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    UNIQUE(capsule_id, pet_id),
    FOREIGN KEY(relationship_epoch_id, pet_id)
        REFERENCES relationship_epochs(epoch_id, pet_id)
);

CREATE TABLE IF NOT EXISTS capsule_evidence (
    capsule_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    pet_id TEXT NOT NULL,
    PRIMARY KEY(capsule_id, evidence_id),
    FOREIGN KEY(capsule_id, pet_id)
        REFERENCES session_capsules(capsule_id, pet_id) ON DELETE CASCADE,
    FOREIGN KEY(evidence_id, pet_id)
        REFERENCES companion_evidence(evidence_id, pet_id)
);

CREATE TABLE IF NOT EXISTS companion_adjustments (
    adjustment_id TEXT PRIMARY KEY,
    pet_id TEXT NOT NULL,
    relationship_epoch_id TEXT NOT NULL,
    dimension TEXT NOT NULL,
    value_json TEXT NOT NULL,
    scope TEXT NOT NULL,
    behavior_key TEXT,
    context_scope TEXT,
    direction TEXT CHECK (direction IN ('increase', 'decrease')),
    status TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    generated_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    valid_until TEXT,
    UNIQUE(adjustment_id, pet_id),
    FOREIGN KEY(relationship_epoch_id, pet_id)
        REFERENCES relationship_epochs(epoch_id, pet_id)
);

CREATE TABLE IF NOT EXISTS adjustment_evidence (
    adjustment_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    pet_id TEXT NOT NULL,
    PRIMARY KEY(adjustment_id, evidence_id),
    FOREIGN KEY(adjustment_id, pet_id)
        REFERENCES companion_adjustments(adjustment_id, pet_id) ON DELETE CASCADE,
    FOREIGN KEY(evidence_id, pet_id)
        REFERENCES companion_evidence(evidence_id, pet_id)
);

CREATE TABLE IF NOT EXISTS companion_chapters (
    chapter_id TEXT PRIMARY KEY,
    pet_id TEXT NOT NULL,
    relationship_epoch_id TEXT NOT NULL,
    academic_stage TEXT NOT NULL,
    xiaoxin_age INTEGER,
    period_start TEXT NOT NULL,
    period_end TEXT,
    safe_narrative TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    created_at TEXT NOT NULL,
    UNIQUE(chapter_id, pet_id),
    FOREIGN KEY(relationship_epoch_id, pet_id)
        REFERENCES relationship_epochs(epoch_id, pet_id),
    CHECK (xiaoxin_age IS NULL OR xiaoxin_age BETWEEN 1 AND 4)
);

CREATE TABLE IF NOT EXISTS chapter_evidence (
    chapter_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    pet_id TEXT NOT NULL,
    PRIMARY KEY(chapter_id, evidence_id),
    FOREIGN KEY(chapter_id, pet_id)
        REFERENCES companion_chapters(chapter_id, pet_id) ON DELETE CASCADE,
    FOREIGN KEY(evidence_id, pet_id)
        REFERENCES companion_evidence(evidence_id, pet_id)
);

CREATE TABLE IF NOT EXISTS adjustment_evidence_qualification (
    adjustment_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    pet_id TEXT NOT NULL,
    qualification TEXT NOT NULL
        CHECK (qualification IN ('eligible', 'clue_only', 'rejected')),
    reason_code TEXT NOT NULL,
    qualifying_local_date TEXT,
    contributes_date INTEGER NOT NULL DEFAULT 0
        CHECK (contributes_date IN (0, 1)),
    evaluated_at TEXT NOT NULL,
    PRIMARY KEY(adjustment_id, evidence_id),
    FOREIGN KEY(adjustment_id, pet_id)
        REFERENCES companion_adjustments(adjustment_id, pet_id) ON DELETE CASCADE,
    FOREIGN KEY(evidence_id, pet_id)
        REFERENCES companion_evidence(evidence_id, pet_id),
    CHECK (
        (qualification = 'eligible' AND qualifying_local_date IS NOT NULL)
        OR (
            qualification IN ('clue_only', 'rejected')
            AND qualifying_local_date IS NULL
            AND contributes_date = 0
        )
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_adjustment_qualification_date_vote
ON adjustment_evidence_qualification(adjustment_id, qualifying_local_date)
WHERE contributes_date = 1;

CREATE TABLE IF NOT EXISTS companion_birth_temperaments (
    pet_id TEXT PRIMARY KEY,
    generator_version TEXT NOT NULL CHECK (length(trim(generator_version)) > 0),
    exploration_orientation TEXT NOT NULL
        CHECK (exploration_orientation IN ('focused', 'balanced', 'exploratory')),
    expression_energy TEXT NOT NULL
        CHECK (expression_energy IN ('calm', 'natural', 'lively')),
    thought_organization TEXT NOT NULL
        CHECK (thought_organization IN ('intuitive', 'balanced', 'structured')),
    playfulness TEXT NOT NULL
        CHECK (playfulness IN ('restrained', 'lighthearted', 'playful')),
    companion_initiative TEXT NOT NULL
        CHECK (companion_initiative IN ('reserved', 'timely', 'proactive')),
    generated_at TEXT NOT NULL,
    source_kind TEXT NOT NULL
        CHECK (source_kind IN ('pet_created', 'legacy_backfill')),
    FOREIGN KEY(pet_id) REFERENCES companion_pets(pet_id)
);

CREATE TABLE IF NOT EXISTS companion_academic_transitions (
    transition_id TEXT PRIMARY KEY,
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    relationship_epoch_id TEXT NOT NULL,
    from_stage TEXT,
    from_status TEXT,
    to_stage TEXT NOT NULL CHECK (
        to_stage IN ('freshman', 'sophomore', 'junior', 'senior', 'unknown')
    ),
    to_status TEXT NOT NULL CHECK (
        to_status IN ('active', 'leave', 'graduated', 'unknown')
    ),
    transition_kind TEXT NOT NULL CHECK (
        transition_kind IN (
            'initialized', 'advance', 'skip_advance', 'same_stage',
            'regression', 'correction', 'leave', 'resume', 'graduation',
            'major_change', 'explicit_clear', 'migration'
        )
    ),
    effective_at TEXT NOT NULL,
    source_revision INTEGER NOT NULL CHECK (source_revision >= 0),
    source_kind TEXT NOT NULL DEFAULT 'identity:student_profile',
    evidence_id TEXT,
    growth_eligible INTEGER NOT NULL CHECK (growth_eligible IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE(pet_id, memory_subject_id, source_revision),
    FOREIGN KEY(relationship_epoch_id, pet_id)
        REFERENCES relationship_epochs(epoch_id, pet_id),
    FOREIGN KEY(evidence_id, pet_id)
        REFERENCES companion_evidence(evidence_id, pet_id)
);

CREATE INDEX IF NOT EXISTS idx_companion_academic_transitions_subject
ON companion_academic_transitions(
    pet_id, memory_subject_id, source_revision, effective_at
);

CREATE TABLE IF NOT EXISTS companion_academic_states (
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    academic_stage TEXT NOT NULL CHECK (
        academic_stage IN ('freshman', 'sophomore', 'junior', 'senior', 'unknown')
    ),
    academic_status TEXT NOT NULL CHECK (
        academic_status IN ('active', 'leave', 'graduated', 'unknown')
    ),
    effective_at TEXT NOT NULL,
    source_revision INTEGER NOT NULL CHECK (source_revision >= 0),
    transition_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(pet_id, memory_subject_id),
    FOREIGN KEY(pet_id) REFERENCES companion_pets(pet_id),
    FOREIGN KEY(transition_id)
        REFERENCES companion_academic_transitions(transition_id)
);

CREATE TABLE IF NOT EXISTS companion_growth_moments (
    moment_id TEXT PRIMARY KEY,
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    relationship_epoch_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    from_stage TEXT NOT NULL,
    to_stage TEXT NOT NULL,
    xiaoxin_age INTEGER NOT NULL CHECK (xiaoxin_age BETWEEN 1 AND 4),
    safe_summary TEXT NOT NULL,
    continuity_evidence_count INTEGER NOT NULL DEFAULT 0
        CHECK (continuity_evidence_count >= 0),
    expression_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (expression_status IN ('pending', 'reserved', 'expressed')),
    reserved_by_turn_id TEXT,
    lease_until TEXT,
    expressed_at TEXT,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(pet_id, memory_subject_id, from_stage, to_stage, occurred_at),
    FOREIGN KEY(relationship_epoch_id, pet_id)
        REFERENCES relationship_epochs(epoch_id, pet_id),
    FOREIGN KEY(evidence_id, pet_id)
        REFERENCES companion_evidence(evidence_id, pet_id),
    CHECK (
        (expression_status = 'pending' AND reserved_by_turn_id IS NULL
            AND lease_until IS NULL AND expressed_at IS NULL)
        OR (expression_status = 'reserved' AND reserved_by_turn_id IS NOT NULL
            AND lease_until IS NOT NULL AND expressed_at IS NULL)
        OR (expression_status = 'expressed' AND reserved_by_turn_id IS NOT NULL
            AND lease_until IS NULL AND expressed_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_companion_growth_moments_subject
ON companion_growth_moments(
    pet_id, memory_subject_id, relationship_epoch_id, occurred_at
);

CREATE TABLE IF NOT EXISTS companion_narrative_boundaries (
    boundary_id TEXT PRIMARY KEY,
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    relationship_epoch_id TEXT NOT NULL,
    boundary_kind TEXT NOT NULL CHECK (
        boundary_kind IN (
            'academic_growth', 'academic_reorientation',
            'anniversary', 'graduation'
        )
    ),
    source_key TEXT NOT NULL,
    transition_id TEXT,
    evidence_id TEXT,
    from_stage TEXT NOT NULL,
    to_stage TEXT NOT NULL,
    xiaoxin_age INTEGER CHECK (xiaoxin_age BETWEEN 1 AND 4),
    anniversary_number INTEGER CHECK (
        anniversary_number IS NULL OR anniversary_number >= 1
    ),
    effective_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'invalidated')),
    created_at TEXT NOT NULL,
    UNIQUE(pet_id, memory_subject_id, source_key),
    FOREIGN KEY(relationship_epoch_id, pet_id)
        REFERENCES relationship_epochs(epoch_id, pet_id),
    FOREIGN KEY(evidence_id, pet_id)
        REFERENCES companion_evidence(evidence_id, pet_id)
);

CREATE INDEX IF NOT EXISTS idx_companion_narrative_boundaries_subject
ON companion_narrative_boundaries(
    pet_id, memory_subject_id, relationship_epoch_id, effective_at
);

CREATE TABLE IF NOT EXISTS companion_growth_moment_metadata (
    moment_id TEXT PRIMARY KEY,
    primary_kind TEXT NOT NULL CHECK (
        primary_kind IN (
            'academic_growth', 'academic_reorientation',
            'anniversary', 'graduation'
        )
    ),
    mode TEXT NOT NULL CHECK (mode IN ('boundary_only', 'evidence_backed')),
    lifecycle_status TEXT NOT NULL DEFAULT 'active'
        CHECK (
            lifecycle_status IN ('active', 'suppressed', 'expired', 'invalidated')
        ),
    expires_at TEXT NOT NULL,
    reason_code TEXT,
    FOREIGN KEY(moment_id) REFERENCES companion_growth_moments(moment_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS companion_narrative_preferences (
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    growth_moments_enabled INTEGER NOT NULL DEFAULT 1
        CHECK (growth_moments_enabled IN (0, 1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(pet_id, memory_subject_id),
    FOREIGN KEY(pet_id) REFERENCES companion_pets(pet_id)
);

CREATE TABLE IF NOT EXISTS companion_growth_moment_boundaries (
    moment_id TEXT NOT NULL,
    boundary_id TEXT NOT NULL,
    PRIMARY KEY(moment_id, boundary_id),
    FOREIGN KEY(moment_id) REFERENCES companion_growth_moments(moment_id)
        ON DELETE CASCADE,
    FOREIGN KEY(boundary_id) REFERENCES companion_narrative_boundaries(boundary_id)
);

CREATE TABLE IF NOT EXISTS companion_growth_moment_evidence (
    moment_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    pet_id TEXT NOT NULL,
    PRIMARY KEY(moment_id, evidence_id),
    FOREIGN KEY(moment_id) REFERENCES companion_growth_moments(moment_id)
        ON DELETE CASCADE,
    FOREIGN KEY(evidence_id, pet_id)
        REFERENCES companion_evidence(evidence_id, pet_id)
);

CREATE TABLE IF NOT EXISTS companion_chapter_boundaries (
    chapter_id TEXT NOT NULL,
    boundary_id TEXT NOT NULL,
    PRIMARY KEY(chapter_id, boundary_id),
    FOREIGN KEY(chapter_id) REFERENCES companion_chapters(chapter_id)
        ON DELETE CASCADE,
    FOREIGN KEY(boundary_id) REFERENCES companion_narrative_boundaries(boundary_id)
);

CREATE TABLE IF NOT EXISTS memory_controls (
    control_id TEXT PRIMARY KEY,
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    action TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS consolidation_jobs (
    job_id TEXT PRIMARY KEY,
    pet_id TEXT NOT NULL,
    relationship_epoch_id TEXT,
    job_kind TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    due_at TEXT NOT NULL,
    lease_until TEXT,
    next_attempt_at TEXT,
    model TEXT,
    prompt_version TEXT,
    schema_version TEXT NOT NULL,
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(relationship_epoch_id, pet_id)
        REFERENCES relationship_epochs(epoch_id, pet_id)
);

CREATE INDEX IF NOT EXISTS idx_consolidation_jobs_due
ON consolidation_jobs(status, due_at, next_attempt_at);

CREATE TABLE IF NOT EXISTS initiative_decisions (
    decision_id TEXT PRIMARY KEY,
    pet_id TEXT NOT NULL,
    relationship_epoch_id TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    priority TEXT NOT NULL,
    cooldown_until TEXT,
    content_brief TEXT NOT NULL,
    hardware_expression_json TEXT NOT NULL,
    delivery_status TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(relationship_epoch_id, pet_id)
        REFERENCES relationship_epochs(epoch_id, pet_id)
);

CREATE TABLE IF NOT EXISTS initiative_opportunities (
    opportunity_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    relationship_epoch_id TEXT NOT NULL,
    opportunity_kind TEXT NOT NULL CHECK (
        opportunity_kind IN (
            'followup', 'reminder_result', 'goal_progress',
            'future_event', 'celebration', 'checkin', 'connection_bid',
            'boot_checkin'
        )
    ),
    reason_code TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    safe_brief TEXT NOT NULL,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'scheduled', 'deferred', 'claimed', 'delivering', 'delivered',
            'blocked', 'delivery_failed', 'invalidated'
        )
    ),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    lease_until TEXT,
    next_attempt_at TEXT,
    decision_id TEXT UNIQUE,
    delivery_id TEXT,
    outcome_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(relationship_epoch_id, pet_id)
        REFERENCES relationship_epochs(epoch_id, pet_id),
    FOREIGN KEY(decision_id) REFERENCES initiative_decisions(decision_id)
);

CREATE INDEX IF NOT EXISTS idx_initiative_opportunities_due
ON initiative_opportunities(status, due_at, next_attempt_at, lease_until);

CREATE UNIQUE INDEX IF NOT EXISTS idx_connection_bid_single_active
ON initiative_opportunities(
    owner_user_id, pet_id, memory_subject_id, relationship_epoch_id,
    opportunity_kind
)
WHERE opportunity_kind = 'connection_bid'
  AND status IN (
      'scheduled', 'deferred', 'claimed', 'delivering', 'delivered'
  );

CREATE TABLE IF NOT EXISTS companion_relationship_needs (
    owner_user_id TEXT NOT NULL,
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    relationship_epoch_id TEXT NOT NULL,
    need_kind TEXT NOT NULL CHECK (need_kind = 'connection'),
    last_meaningful_interaction_at TEXT NOT NULL,
    last_bid_at TEXT,
    pending_decision_id TEXT,
    source_evidence_id TEXT,
    ignored_streak INTEGER NOT NULL DEFAULT 0 CHECK (ignored_streak >= 0),
    cooldown_until TEXT,
    next_eligible_at TEXT NOT NULL,
    initiative_bias TEXT NOT NULL CHECK (
        initiative_bias IN ('reserved', 'timely', 'proactive')
    ),
    relationship_stage TEXT NOT NULL CHECK (
        relationship_stage IN (
            'first_meeting', 'familiar', 'attuned', 'long_term_companion'
        )
    ),
    initiative_level TEXT NOT NULL DEFAULT 'low' CHECK (
        initiative_level IN ('disabled', 'low', 'medium')
    ),
    threshold_seconds INTEGER NOT NULL CHECK (threshold_seconds > 0),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(
        owner_user_id, pet_id, memory_subject_id,
        relationship_epoch_id, need_kind
    ),
    FOREIGN KEY(relationship_epoch_id, pet_id)
        REFERENCES relationship_epochs(epoch_id, pet_id),
    FOREIGN KEY(pending_decision_id)
        REFERENCES initiative_decisions(decision_id),
    FOREIGN KEY(source_evidence_id)
        REFERENCES companion_evidence(evidence_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_relationship_needs_due
ON companion_relationship_needs(
    need_kind, next_eligible_at, cooldown_until, pending_decision_id
);

CREATE TABLE IF NOT EXISTS companion_device_boot_events (
    boot_event_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    relationship_epoch_id TEXT NOT NULL,
    boot_reason TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'scheduled', 'delivered', 'responded', 'unobserved',
            'suppressed', 'delivery_failed'
        )
    ),
    opportunity_id TEXT UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(relationship_epoch_id, pet_id)
        REFERENCES relationship_epochs(epoch_id, pet_id)
);

CREATE INDEX IF NOT EXISTS idx_companion_boot_events_due
ON companion_device_boot_events(status, due_at, device_id);

CREATE TABLE IF NOT EXISTS companion_presence_leases (
    owner_user_id TEXT NOT NULL,
    pet_id TEXT NOT NULL,
    memory_subject_id TEXT NOT NULL,
    relationship_epoch_id TEXT NOT NULL,
    device_id TEXT,
    source TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'closed', 'expired')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(
        owner_user_id, pet_id, memory_subject_id, relationship_epoch_id
    ),
    FOREIGN KEY(relationship_epoch_id, pet_id)
        REFERENCES relationship_epochs(epoch_id, pet_id)
);

CREATE INDEX IF NOT EXISTS idx_companion_presence_leases_active
ON companion_presence_leases(status, expires_at, device_id);
"""


class CompanionJobLeaseLostError(RuntimeError):
    pass


@dataclass(frozen=True)
class PendingCompanionJob:
    job_id: str
    pet_id: str
    relationship_epoch_id: str | None
    job_kind: str
    idempotency_key: str
    payload: Mapping[str, object]
    due_at: str
    schema_version: str


@dataclass(frozen=True)
class ClaimedCompanionJob:
    job_id: str
    pet_id: str
    relationship_epoch_id: str | None
    job_kind: str
    payload: Mapping[str, object]
    attempt: int
    schema_version: str


def _job_memory_subject_id(
    connection: sqlite3.Connection,
    job: ClaimedCompanionJob,
) -> str:
    memory_subject_id = job.payload.get("memory_subject_id")
    if isinstance(memory_subject_id, str) and memory_subject_id.strip():
        return memory_subject_id

    raw_ids: list[str] = []
    evidence_ids = job.payload.get("evidence_ids")
    if isinstance(evidence_ids, list):
        raw_ids.extend(
            item for item in evidence_ids if isinstance(item, str) and item.strip()
        )
    evidence_id = job.payload.get("evidence_id")
    if isinstance(evidence_id, str) and evidence_id.strip():
        raw_ids.append(evidence_id)
    unique_ids = tuple(dict.fromkeys(raw_ids))
    if unique_ids:
        placeholders = ",".join("?" for _ in unique_ids)
        rows = connection.execute(
            f"""
            SELECT DISTINCT memory_subject_id
            FROM companion_evidence
            WHERE evidence_id IN ({placeholders}) AND pet_id = ?
            """,
            (*unique_ids, job.pet_id),
        ).fetchall()
        if len(rows) == 1:
            return str(rows[0]["memory_subject_id"])
    raise ReflectionValidationError("job memory_subject_id is unavailable")


@dataclass(frozen=True)
class PendingCompanionEvidence:
    evidence_id: str
    ownership_scope: str
    kind: str
    content: Mapping[str, object]
    source_summary: str
    attribution: str
    confidence: float
    retention: str
    prompt_eligible: bool
    expires_at: str | None = None


@dataclass(frozen=True)
class PendingInitiativeOpportunity:
    opportunity_id: str
    opportunity_kind: str
    reason_code: str
    evidence_ids: tuple[str, ...]
    safe_brief: str
    due_at: str


@dataclass(frozen=True)
class PendingConnectionNeedUpdate:
    turn_id: str
    source_evidence_id: str
    last_meaningful_interaction_at: str
    next_eligible_at: str
    initiative_bias: str
    relationship_stage: str
    initiative_level: str
    threshold_seconds: int
    feedback_window_seconds: int
    feedback_outcome: str = "connection_responded"
    presence_window_seconds: int = 2700


@dataclass(frozen=True)
class DueInitiativeOpportunity:
    opportunity_id: str
    owner_user_id: str
    pet_id: str
    memory_subject_id: str
    relationship_epoch_id: str
    opportunity_kind: str
    reason_code: str
    evidence_ids: tuple[str, ...]
    safe_brief: str
    due_at: str
    attempt: int
    decision_id: str | None = None
    initiative_bias: str | None = None
    relationship_stage: str | None = None
    connection_need_strength: str | None = None


@dataclass(frozen=True)
class CompanionPolicyMaterial:
    turn_count: int
    distinct_interaction_days: int
    relationship_started_at: str
    interaction_dates: tuple[str, ...]
    historical_stage: str | None
    relationship_stage_history: tuple[tuple[str, str], ...]
    evidence: tuple[CompanionEvidence, ...]
    active_adjustments: Mapping[str, object]
    behavior_adjustments: tuple[BehaviorAdjustmentSignal, ...] = ()


@dataclass(frozen=True)
class _AdjustmentEvidenceDecision:
    qualification: str
    reason_code: str
    behavior_key: str | None = None
    context_scope: str | None = None
    direction: str | None = None
    qualifying_local_date: str | None = None

    @property
    def structured_key(self) -> tuple[str, str, str] | None:
        if (
            self.behavior_key is None
            or self.context_scope is None
            or self.direction is None
        ):
            return None
        return (self.behavior_key, self.context_scope, self.direction)


def _shanghai_local_date(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReflectionValidationError(
            "adjustment Evidence timestamp must include a timezone"
        )
    return parsed.astimezone(_SHANGHAI_TIMEZONE).date().isoformat()


def _adjustment_evidence_decision(
    row: sqlite3.Row,
    *,
    dimension: str,
    value: str,
    scope: str,
    current_epoch_id: str,
    now: str,
) -> _AdjustmentEvidenceDecision:
    if row["speaker_identity"] != "confirmed":
        return _AdjustmentEvidenceDecision("rejected", "speaker_not_confirmed")
    if row["ownership_scope"] != "relationship":
        return _AdjustmentEvidenceDecision("rejected", "not_relationship_evidence")
    if row["relationship_epoch_id"] != current_epoch_id:
        return _AdjustmentEvidenceDecision("rejected", "relationship_epoch_mismatch")
    if row["status"] != "active":
        return _AdjustmentEvidenceDecision("rejected", "evidence_not_active")
    if row["expires_at"] is not None and datetime.fromisoformat(
        row["expires_at"]
    ) <= datetime.fromisoformat(now):
        return _AdjustmentEvidenceDecision("rejected", "evidence_already_expired")
    try:
        content = json.loads(row["content_json"])
    except (TypeError, json.JSONDecodeError):
        return _AdjustmentEvidenceDecision("rejected", "evidence_content_invalid")
    if not isinstance(content, dict):
        return _AdjustmentEvidenceDecision("rejected", "evidence_content_invalid")

    claim_context = content.get("claim_context")
    if isinstance(claim_context, str) and claim_context != "direct":
        return _AdjustmentEvidenceDecision(
            "rejected",
            _REJECTED_CLAIM_CONTEXT_REASONS.get(
                claim_context, "non_direct_claim_rejected"
            ),
        )
    if content.get("temporal_scope") == "short_term_state":
        return _AdjustmentEvidenceDecision("rejected", "short_term_state_not_growth")
    source_reliability = content.get("source_reliability")
    if source_reliability in {
        "model_inference",
        "assistant_inference",
        "third_party",
        "uncertain",
        "asr_uncertain",
    }:
        return _AdjustmentEvidenceDecision("rejected", "source_not_first_party")
    if row["kind"] in _ADJUSTMENT_REJECTED_KINDS:
        return _AdjustmentEvidenceDecision(
            "rejected", "evidence_kind_not_behavior_learning"
        )
    if row["attribution"] in {"model_inference", "assistant_inference"}:
        return _AdjustmentEvidenceDecision("rejected", "model_inference_not_evidence")

    behavior_key = content.get("behavior_key")
    context_scope = content.get("context_scope")
    direction = content.get("direction")
    has_any_structured_key = any(
        value is not None for value in (behavior_key, context_scope, direction)
    )
    structured_key_is_valid = (
        isinstance(behavior_key, str)
        and _ADJUSTMENT_BEHAVIOR_DIMENSIONS.get(behavior_key) == dimension
        and isinstance(context_scope, str)
        and context_scope == scope
        and direction in _ADJUSTMENT_DIRECTIONS
    )
    if has_any_structured_key and not structured_key_is_valid:
        return _AdjustmentEvidenceDecision(
            "rejected", "behavior_context_direction_mismatch"
        )

    target_dimension = content.get("dimension")
    target_value = content.get("value")
    target_scope = content.get("scope")
    has_any_target = any(
        item is not None for item in (target_dimension, target_value, target_scope)
    )
    if has_any_target and (
        target_dimension != dimension
        or target_value != value
        or target_scope != scope
    ):
        return _AdjustmentEvidenceDecision("rejected", "adjustment_target_mismatch")

    specificity = content.get("feedback_specificity")
    if (
        structured_key_is_valid
        and row["kind"] in _ADJUSTMENT_QUALIFYING_KINDS
        and specificity == "behavior_and_context"
        and source_reliability in {"first_party_observed", "explicit_user_feedback"}
    ):
        return _AdjustmentEvidenceDecision(
            "eligible",
            "specific_first_party_feedback",
            behavior_key=behavior_key,
            context_scope=context_scope,
            direction=direction,
            qualifying_local_date=_shanghai_local_date(row["occurred_at"]),
        )

    if structured_key_is_valid:
        return _AdjustmentEvidenceDecision(
            "clue_only",
            "generic_feedback_clue_only",
            behavior_key=behavior_key,
            context_scope=context_scope,
            direction=direction,
        )
    if (
        row["kind"] in _ADJUSTMENT_CLUE_ONLY_KINDS
        or row["kind"] in _ADJUSTMENT_QUALIFYING_KINDS
        or content.get("outcome") in {"helpful", "accepted_help"}
    ):
        return _AdjustmentEvidenceDecision(
            "clue_only", "unstructured_outcome_clue_only"
        )
    return _AdjustmentEvidenceDecision("rejected", "unsupported_evidence_shape")


def _adjustment_scope_matches(
    scope: str,
    *,
    surface: str,
    interaction_kind: str,
    context: str = "ordinary",
) -> bool:
    return (
        scope == "all"
        or scope == surface
        or scope == interaction_kind
        or scope == context
    )


def _active_initiative_contract_level(
    connection: sqlite3.Connection,
    *,
    pet_id: str,
    memory_subject_id: str,
) -> str | None:
    rows = connection.execute(
        """
        SELECT value_json
        FROM companion_interaction_contracts
        WHERE pet_id = ? AND memory_subject_id = ?
          AND dimension = 'initiative_level' AND status = 'active'
          AND scope IN ('all', 'initiative')
        ORDER BY CASE scope WHEN 'initiative' THEN 0 ELSE 1 END,
                 updated_at DESC, contract_id DESC
        """,
        (pet_id, memory_subject_id),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["value_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        value = payload.get("value") if isinstance(payload, dict) else None
        if value in {"disabled", "low", "medium"}:
            return str(value)
    return None


class CompanionStore:
    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._backup_before_schema_upgrade()
        self._initialize()

    def _backup_before_schema_upgrade(self) -> None:
        if not self.database_path.exists() or self.database_path.stat().st_size == 0:
            return
        with sqlite3.connect(self.database_path, timeout=5.0) as source:
            previous_version = int(source.execute("PRAGMA user_version").fetchone()[0])
            if previous_version <= 0 or previous_version >= SCHEMA_VERSION:
                return
            backup_path = self.database_path.with_name(
                f"{self.database_path.name}.pre-v{SCHEMA_VERSION}.bak"
            )
            if backup_path.exists():
                return
            with sqlite3.connect(backup_path) as destination:
                source.backup(destination)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def pet_reflection_guard(self, pet_id: str) -> Iterator[None]:
        """Linearize reflection dispatch and relationship reset for one pet."""
        lock_path = self._pet_reflection_lock_path(pet_id)
        lock_key = str(lock_path.resolve())
        with _PET_REFLECTION_LOCKS_GUARD:
            process_lock = _PET_REFLECTION_LOCKS.setdefault(
                lock_key,
                threading.RLock(),
            )
        with process_lock:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+b") as lock_file:
                lock_file.seek(0, 2)
                if lock_file.tell() == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                lock_file.seek(0)
                self._lock_reflection_file(lock_file)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    self._unlock_reflection_file(lock_file)

    def _pet_reflection_lock_path(self, pet_id: str) -> Path:
        digest = hashlib.sha256(pet_id.encode("utf-8")).hexdigest()[:32]
        return self.database_path.parent / (
            f".{self.database_path.name}.pet-reflection-{digest}.lock"
        )

    @staticmethod
    def _lock_reflection_file(lock_file: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    return
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EDEADLOCK}:
                        raise
                    time.sleep(0.01)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

    @staticmethod
    def _unlock_reflection_file(lock_file: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _initialize(self) -> None:
        with self.connection() as connection:
            previous_version = connection.execute("PRAGMA user_version").fetchone()[0]
            journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(journal_mode).lower() != "wal":
                raise sqlite3.DatabaseError("CompanionStore requires WAL journal mode")
            connection.executescript(_SCHEMA)
            if not self._companion_turns_schema_is_v3(connection):
                connection.execute("PRAGMA user_version = 2")
                connection.commit()
                self._migrate_companion_turns_v3(connection)
            turn_columns = {
                row["name"]: row
                for row in connection.execute("PRAGMA table_info(companion_turns)")
            }
            policy_version_column = turn_columns.get("policy_version")
            if policy_version_column is None:
                connection.execute(
                    """
                    ALTER TABLE companion_turns
                    ADD COLUMN policy_version TEXT NOT NULL
                    DEFAULT 'companion-policy-v1'
                    """
                )
            elif policy_version_column["notnull"] != 1:
                self._migrate_companion_turns_v4(connection)
            if not self._companion_turns_schema_is_v4(connection):
                raise sqlite3.DatabaseError(
                    "companion_turns schema does not satisfy v4 audit constraints"
                )
            self._migrate_initiative_opportunities_v21(connection)
            self._migrate_initiative_opportunities_v23(connection)
            self._migrate_relationship_needs_v22(connection)
            self._migrate_evidence_v5(connection)
            self._migrate_pending_observations_v7(connection)
            self._migrate_retrieval_v9(
                connection,
                rebuild=previous_version < 9,
            )
            self._migrate_adjustments_v15(connection)
            if previous_version < 16:
                self._backfill_academic_states_v16(connection)
            if previous_version < 17:
                self._backfill_narrative_metadata_v17(connection)
            if previous_version < 14:
                self._backfill_birth_temperaments(
                    connection,
                    generated_at=datetime.now(timezone.utc).isoformat(),
                )
            connection.execute(
                """
                DELETE FROM companion_retrieval_audits
                WHERE julianday(expires_at) <= julianday(?)
                """,
                (datetime.now(timezone.utc).isoformat(),),
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if str(integrity).lower() != "ok":
                raise sqlite3.DatabaseError(
                    f"CompanionStore integrity_check failed: {integrity}"
                )
            foreign_key_violations = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            if foreign_key_violations:
                raise sqlite3.DatabaseError(
                    "CompanionStore foreign_key_check found violations"
                )

    @classmethod
    def _backfill_birth_temperaments(
        cls,
        connection: sqlite3.Connection,
        *,
        generated_at: str,
    ) -> None:
        pet_ids = tuple(
            row["pet_id"]
            for row in connection.execute(
                """
                SELECT pets.pet_id
                FROM companion_pets AS pets
                LEFT JOIN companion_birth_temperaments AS temperament
                  ON temperament.pet_id = pets.pet_id
                WHERE temperament.pet_id IS NULL
                ORDER BY pets.pet_id
                """
            )
        )
        temperaments = tuple(
            generate_birth_temperament(
                pet_id=pet_id,
                generated_at=generated_at,
                source_kind="legacy_backfill",
            )
            for pet_id in pet_ids
        )
        for temperament in temperaments:
            cls._insert_birth_temperament(connection, temperament)

    @staticmethod
    def _insert_birth_temperament(
        connection: sqlite3.Connection,
        temperament: BirthTemperament,
    ) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO companion_birth_temperaments(
                pet_id, generator_version, exploration_orientation,
                expression_energy, thought_organization, playfulness,
                companion_initiative, generated_at, source_kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                temperament.pet_id,
                temperament.generator_version,
                temperament.exploration_orientation,
                temperament.expression_energy,
                temperament.thought_organization,
                temperament.playfulness,
                temperament.companion_initiative,
                temperament.generated_at,
                temperament.source_kind,
            ),
        )

    @classmethod
    def _ensure_birth_temperament_in_connection(
        cls,
        connection: sqlite3.Connection,
        *,
        pet_id: str,
        generated_at: str,
        source_kind: TemperamentSourceKind,
    ) -> BirthTemperament:
        row = connection.execute(
            """
            SELECT pet_id, generator_version, exploration_orientation,
                   expression_energy, thought_organization, playfulness,
                   companion_initiative, generated_at, source_kind
            FROM companion_birth_temperaments
            WHERE pet_id = ?
            """,
            (pet_id,),
        ).fetchone()
        if row is None:
            temperament = generate_birth_temperament(
                pet_id=pet_id,
                generated_at=generated_at,
                source_kind=source_kind,
            )
            cls._insert_birth_temperament(connection, temperament)
            row = connection.execute(
                """
                SELECT pet_id, generator_version, exploration_orientation,
                       expression_energy, thought_organization, playfulness,
                       companion_initiative, generated_at, source_kind
                FROM companion_birth_temperaments
                WHERE pet_id = ?
                """,
                (pet_id,),
            ).fetchone()
        if row is None:
            raise sqlite3.DatabaseError("birth temperament persistence failed")
        return _birth_temperament_from_row(row)

    @staticmethod
    def _migrate_retrieval_v9(
        connection: sqlite3.Connection,
        *,
        rebuild: bool,
    ) -> None:
        connection.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS trg_companion_evidence_fts_insert
            AFTER INSERT ON companion_evidence
            BEGIN
                INSERT INTO companion_evidence_fts(
                    rowid, evidence_id, pet_id, memory_subject_id,
                    fact_key, source_summary, content_json
                ) VALUES (
                    new.rowid, new.evidence_id, new.pet_id, new.memory_subject_id,
                    COALESCE(new.fact_key, ''), new.source_summary,
                    json_remove(
                        new.content_json, '$.source_quote', '$.source_quotes'
                    )
                );
            END;

            CREATE TRIGGER IF NOT EXISTS trg_companion_evidence_fts_update
            AFTER UPDATE OF fact_key, source_summary, content_json,
                            pet_id, memory_subject_id
            ON companion_evidence
            BEGIN
                DELETE FROM companion_evidence_fts WHERE rowid = old.rowid;
                INSERT INTO companion_evidence_fts(
                    rowid, evidence_id, pet_id, memory_subject_id,
                    fact_key, source_summary, content_json
                ) VALUES (
                    new.rowid, new.evidence_id, new.pet_id, new.memory_subject_id,
                    COALESCE(new.fact_key, ''), new.source_summary,
                    json_remove(
                        new.content_json, '$.source_quote', '$.source_quotes'
                    )
                );
            END;

            CREATE TRIGGER IF NOT EXISTS trg_companion_evidence_fts_delete
            AFTER DELETE ON companion_evidence
            BEGIN
                DELETE FROM companion_evidence_fts WHERE rowid = old.rowid;
            END;
            """
        )
        if rebuild:
            connection.execute("DELETE FROM companion_evidence_fts")
            connection.execute(
                """
                INSERT INTO companion_evidence_fts(
                    rowid, evidence_id, pet_id, memory_subject_id,
                    fact_key, source_summary, content_json
                )
                SELECT rowid, evidence_id, pet_id, memory_subject_id,
                       COALESCE(fact_key, ''), source_summary,
                       json_remove(content_json, '$.source_quote')
                FROM companion_evidence
                """
            )

    @staticmethod
    def _migrate_evidence_v5(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(companion_evidence)")
        }
        additions = {
            "fact_key": "TEXT",
            "importance": (
                "REAL NOT NULL DEFAULT 0.5 "
                "CHECK (importance >= 0.0 AND importance <= 1.0)"
            ),
            "sensitivity": (
                "TEXT NOT NULL DEFAULT 'private' "
                "CHECK (sensitivity IN ('low', 'private', 'sensitive'))"
            ),
            "valid_from": "TEXT",
            "valid_until": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE companion_evidence ADD COLUMN {name} {declaration}"
                )
        connection.execute(
            """
            UPDATE companion_evidence
            SET fact_key = json_extract(content_json, '$.fact_key')
            WHERE fact_key IS NULL
              AND json_type(content_json, '$.fact_key') = 'text'
            """
        )
        connection.execute(
            """
            UPDATE companion_evidence
            SET valid_from = occurred_at
            WHERE valid_from IS NULL
            """
        )
        connection.execute(
            """
            UPDATE companion_evidence
            SET valid_until = expires_at
            WHERE valid_until IS NULL AND expires_at IS NOT NULL
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_companion_evidence_fact
            ON companion_evidence(
                pet_id, memory_subject_id, fact_key, status, occurred_at
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_companion_evidence_validity
            ON companion_evidence(
                pet_id, memory_subject_id, status, valid_from, valid_until
            )
            """
        )

    @staticmethod
    def _migrate_pending_observations_v7(
        connection: sqlite3.Connection,
    ) -> None:
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(pending_companion_observations)"
            )
        }
        additions = {
            "attempt_count": ("INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0)"),
            "last_error_code": "TEXT",
            "expires_at": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                connection.execute(
                    "ALTER TABLE pending_companion_observations "
                    f"ADD COLUMN {name} {declaration}"
                )
        connection.execute(
            """
            UPDATE pending_companion_observations
            SET expires_at = strftime(
                '%Y-%m-%dT%H:%M:%f+00:00',
                datetime(created_at, '+30 days')
            )
            WHERE expires_at IS NULL
            """
        )

    @staticmethod
    def _migrate_initiative_opportunities_v21(
        connection: sqlite3.Connection,
    ) -> None:
        schema_row = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = 'initiative_opportunities'
            """
        ).fetchone()
        if (
            schema_row is not None
            and "'connection_bid'" in str(schema_row["sql"])
            and "'deferred'" in str(schema_row["sql"])
        ):
            return
        connection.execute("DROP TABLE IF EXISTS initiative_opportunities_v21")
        connection.execute(
            """
            CREATE TABLE initiative_opportunities_v21 (
                opportunity_id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                pet_id TEXT NOT NULL,
                memory_subject_id TEXT NOT NULL,
                relationship_epoch_id TEXT NOT NULL,
                opportunity_kind TEXT NOT NULL CHECK (
                    opportunity_kind IN (
                        'followup', 'reminder_result', 'goal_progress',
                        'future_event', 'celebration', 'checkin',
                        'connection_bid'
                    )
                ),
                reason_code TEXT NOT NULL,
                evidence_ids_json TEXT NOT NULL,
                safe_brief TEXT NOT NULL,
                due_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN (
                        'scheduled', 'deferred', 'claimed', 'delivering',
                        'delivered',
                        'blocked', 'delivery_failed', 'invalidated'
                    )
                ),
                attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
                lease_until TEXT,
                next_attempt_at TEXT,
                decision_id TEXT UNIQUE,
                delivery_id TEXT,
                outcome_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(relationship_epoch_id, pet_id)
                    REFERENCES relationship_epochs(epoch_id, pet_id),
                FOREIGN KEY(decision_id)
                    REFERENCES initiative_decisions(decision_id)
            )
            """
        )

        if schema_row is not None:
            connection.execute(
                """
                INSERT INTO initiative_opportunities_v21(
                    opportunity_id, owner_user_id, pet_id, memory_subject_id,
                    relationship_epoch_id, opportunity_kind, reason_code,
                    evidence_ids_json, safe_brief, due_at, status, attempt,
                    lease_until, next_attempt_at, decision_id, delivery_id,
                    outcome_code, created_at, updated_at
                )
                SELECT opportunity_id, owner_user_id, pet_id, memory_subject_id,
                       relationship_epoch_id, opportunity_kind, reason_code,
                       evidence_ids_json, safe_brief, due_at, status, attempt,
                       lease_until, next_attempt_at, decision_id, delivery_id,
                       outcome_code, created_at, updated_at
                FROM initiative_opportunities
                """
            )
            connection.execute("DROP TABLE initiative_opportunities")
        connection.execute(
            "ALTER TABLE initiative_opportunities_v21 "
            "RENAME TO initiative_opportunities"
        )
        connection.execute(
            """
            CREATE INDEX idx_initiative_opportunities_due
            ON initiative_opportunities(
                status, due_at, next_attempt_at, lease_until
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX idx_connection_bid_single_active
            ON initiative_opportunities(
                owner_user_id, pet_id, memory_subject_id,
                relationship_epoch_id, opportunity_kind
            )
            WHERE opportunity_kind = 'connection_bid'
              AND status IN (
                  'scheduled', 'deferred', 'claimed', 'delivering', 'delivered'
              )
            """
        )

    @staticmethod
    def _migrate_initiative_opportunities_v23(
        connection: sqlite3.Connection,
    ) -> None:
        schema_row = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = 'initiative_opportunities'
            """
        ).fetchone()
        if schema_row is not None and "'boot_checkin'" in str(schema_row["sql"]):
            return
        connection.execute("DROP TABLE IF EXISTS initiative_opportunities_v23")
        connection.execute(
            """
            CREATE TABLE initiative_opportunities_v23 (
                opportunity_id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                pet_id TEXT NOT NULL,
                memory_subject_id TEXT NOT NULL,
                relationship_epoch_id TEXT NOT NULL,
                opportunity_kind TEXT NOT NULL CHECK (
                    opportunity_kind IN (
                        'followup', 'reminder_result', 'goal_progress',
                        'future_event', 'celebration', 'checkin',
                        'connection_bid', 'boot_checkin'
                    )
                ),
                reason_code TEXT NOT NULL,
                evidence_ids_json TEXT NOT NULL,
                safe_brief TEXT NOT NULL,
                due_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN (
                        'scheduled', 'deferred', 'claimed', 'delivering',
                        'delivered', 'blocked', 'delivery_failed',
                        'invalidated'
                    )
                ),
                attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
                lease_until TEXT,
                next_attempt_at TEXT,
                decision_id TEXT UNIQUE,
                delivery_id TEXT,
                outcome_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(relationship_epoch_id, pet_id)
                    REFERENCES relationship_epochs(epoch_id, pet_id),
                FOREIGN KEY(decision_id)
                    REFERENCES initiative_decisions(decision_id)
            )
            """
        )
        if schema_row is not None:
            connection.execute(
                """
                INSERT INTO initiative_opportunities_v23(
                    opportunity_id, owner_user_id, pet_id, memory_subject_id,
                    relationship_epoch_id, opportunity_kind, reason_code,
                    evidence_ids_json, safe_brief, due_at, status, attempt,
                    lease_until, next_attempt_at, decision_id, delivery_id,
                    outcome_code, created_at, updated_at
                )
                SELECT opportunity_id, owner_user_id, pet_id, memory_subject_id,
                       relationship_epoch_id, opportunity_kind, reason_code,
                       evidence_ids_json, safe_brief, due_at, status, attempt,
                       lease_until, next_attempt_at, decision_id, delivery_id,
                       outcome_code, created_at, updated_at
                FROM initiative_opportunities
                """
            )
            connection.execute("DROP TABLE initiative_opportunities")
        connection.execute(
            "ALTER TABLE initiative_opportunities_v23 "
            "RENAME TO initiative_opportunities"
        )
        connection.execute(
            """
            CREATE INDEX idx_initiative_opportunities_due
            ON initiative_opportunities(
                status, due_at, next_attempt_at, lease_until
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX idx_connection_bid_single_active
            ON initiative_opportunities(
                owner_user_id, pet_id, memory_subject_id,
                relationship_epoch_id, opportunity_kind
            )
            WHERE opportunity_kind = 'connection_bid'
              AND status IN (
                  'scheduled', 'deferred', 'claimed', 'delivering', 'delivered'
              )
            """
        )

    @staticmethod
    def _migrate_relationship_needs_v22(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(companion_relationship_needs)"
            )
        }
        if "initiative_level" in columns:
            return
        connection.execute(
            """
            ALTER TABLE companion_relationship_needs
            ADD COLUMN initiative_level TEXT NOT NULL DEFAULT 'low'
            CHECK (initiative_level IN ('disabled', 'low', 'medium'))
            """
        )
        rows = connection.execute(
            """
            SELECT owner_user_id, pet_id, memory_subject_id,
                   relationship_epoch_id, relationship_stage,
                   initiative_level, initiative_bias, threshold_seconds,
                   ignored_streak, last_meaningful_interaction_at,
                   cooldown_until
            FROM companion_relationship_needs
            WHERE need_kind = 'connection'
            """
        ).fetchall()
        for row in rows:
            target_level = _active_initiative_contract_level(
                connection,
                pet_id=str(row["pet_id"]),
                memory_subject_id=str(row["memory_subject_id"]),
            ) or default_initiative_level(str(row["relationship_stage"]))
            previous_level = str(row["initiative_level"])
            if previous_level == target_level:
                continue
            threshold_seconds = rescale_connection_threshold(
                int(row["threshold_seconds"]),
                previous_level=previous_level,
                next_level=target_level,
            )
            delay_seconds = threshold_seconds
            if int(row["ignored_streak"]) > 0:
                delay_seconds = _connection_ignore_backoff_seconds(
                    threshold_seconds=threshold_seconds,
                    initiative_bias=str(row["initiative_bias"]),
                    ignored_streak=int(row["ignored_streak"]),
                )
            next_eligible_at = (
                datetime.fromisoformat(str(row["last_meaningful_interaction_at"]))
                + timedelta(seconds=delay_seconds)
            )
            if row["cooldown_until"] is not None:
                cooldown_until = datetime.fromisoformat(str(row["cooldown_until"]))
                if cooldown_until > next_eligible_at:
                    next_eligible_at = cooldown_until
            connection.execute(
                """
                UPDATE companion_relationship_needs
                SET initiative_level = ?, threshold_seconds = ?,
                    next_eligible_at = ?, version = version + 1
                WHERE owner_user_id = ? AND pet_id = ?
                  AND memory_subject_id = ? AND relationship_epoch_id = ?
                  AND need_kind = 'connection'
                """,
                (
                    target_level,
                    threshold_seconds,
                    next_eligible_at.isoformat(),
                    row["owner_user_id"],
                    row["pet_id"],
                    row["memory_subject_id"],
                    row["relationship_epoch_id"],
                ),
            )

    @staticmethod
    def _migrate_adjustments_v15(connection: sqlite3.Connection) -> None:
        evidence_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(companion_evidence)")
        }
        if "speaker_identity" not in evidence_columns:
            connection.execute(
                """
                ALTER TABLE companion_evidence
                ADD COLUMN speaker_identity TEXT NOT NULL DEFAULT 'confirmed'
                CHECK (speaker_identity IN ('confirmed', 'unknown', 'invalid'))
                """
            )
            connection.execute(
                "UPDATE companion_evidence SET speaker_identity = 'unknown'"
            )

        adjustment_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(companion_adjustments)")
        }
        additions = {
            "behavior_key": "TEXT",
            "context_scope": "TEXT",
            "direction": ("TEXT CHECK (direction IN ('increase', 'decrease'))"),
        }
        for name, declaration in additions.items():
            if name not in adjustment_columns:
                connection.execute(
                    f"ALTER TABLE companion_adjustments ADD COLUMN {name} {declaration}"
                )
        legacy_window_columns = {"created_at", "valid_until"}
        if legacy_window_columns <= adjustment_columns:
            connection.execute(
                """
                UPDATE companion_adjustments
                SET status = 'candidate',
                    valid_until = COALESCE(
                        valid_until,
                        strftime(
                            '%Y-%m-%dT%H:%M:%f+00:00',
                            datetime(created_at, '+30 days')
                        )
                    )
                WHERE behavior_key IS NULL
                  AND status IN ('trial', 'active')
                """
            )
        else:
            connection.execute(
                """
                UPDATE companion_adjustments
                SET status = 'candidate'
                WHERE behavior_key IS NULL
                  AND status IN ('trial', 'active')
                """
            )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_companion_adjustments_active_behavior
            ON companion_adjustments(
                pet_id, relationship_epoch_id, behavior_key, context_scope
            )
            WHERE status = 'active'
              AND behavior_key IS NOT NULL
              AND context_scope IS NOT NULL
            """
        )

    @staticmethod
    def _backfill_narrative_metadata_v17(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT moment_id, pet_id, memory_subject_id,
                   relationship_epoch_id, evidence_id, from_stage,
                   to_stage, xiaoxin_age, occurred_at
            FROM companion_growth_moments
            """
        ).fetchall()
        for row in rows:
            boundary_id = str(
                uuid5(
                    NAMESPACE_URL, f"xiaoxin:legacy-growth-boundary:{row['moment_id']}"
                )
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO companion_narrative_boundaries(
                    boundary_id, pet_id, memory_subject_id,
                    relationship_epoch_id, boundary_kind, source_key,
                    evidence_id, from_stage, to_stage, xiaoxin_age,
                    effective_at, status, created_at
                ) VALUES (?, ?, ?, ?, 'academic_growth', ?, ?, ?, ?, ?,
                          ?, 'active', ?)
                """,
                (
                    boundary_id,
                    row["pet_id"],
                    row["memory_subject_id"],
                    row["relationship_epoch_id"],
                    f"legacy-growth:{row['moment_id']}",
                    row["evidence_id"],
                    row["from_stage"],
                    row["to_stage"],
                    row["xiaoxin_age"],
                    row["occurred_at"],
                    row["occurred_at"],
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO companion_growth_moment_metadata(
                    moment_id, primary_kind, mode, lifecycle_status,
                    expires_at, reason_code
                ) VALUES (?, 'academic_growth', 'boundary_only', 'active', ?,
                          'legacy_v12_backfill')
                """,
                (
                    row["moment_id"],
                    (
                        datetime.fromisoformat(row["occurred_at"]) + timedelta(days=30)
                    ).isoformat(),
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO companion_growth_moment_boundaries(
                    moment_id, boundary_id
                ) VALUES (?, ?)
                """,
                (row["moment_id"], boundary_id),
            )

    @staticmethod
    def _backfill_academic_states_v16(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT evidence.evidence_id, evidence.pet_id,
                   evidence.memory_subject_id, evidence.content_json,
                   evidence.occurred_at, epochs.epoch_id
            FROM companion_evidence AS evidence
            JOIN relationship_epochs AS epochs
              ON epochs.pet_id = evidence.pet_id AND epochs.ended_at IS NULL
            WHERE evidence.ownership_scope = 'user'
              AND evidence.kind = 'system_event'
              AND evidence.source_ref = 'identity:student_profile'
              AND evidence.status = 'active'
            ORDER BY evidence.pet_id, evidence.memory_subject_id,
                     evidence.occurred_at DESC, evidence.evidence_id DESC
            """
        ).fetchall()
        seen: set[tuple[str, str]] = set()
        for row in rows:
            subject_key = (row["pet_id"], row["memory_subject_id"])
            if subject_key in seen:
                continue
            seen.add(subject_key)
            content = json.loads(row["content_json"])
            stage = content.get("academic_stage")
            if stage not in {"freshman", "sophomore", "junior", "senior", "unknown"}:
                continue
            transition_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"xiaoxin:academic-transition:{row['pet_id']}:"
                    f"{row['memory_subject_id']}:0",
                )
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO companion_academic_transitions(
                    transition_id, pet_id, memory_subject_id,
                    relationship_epoch_id, from_stage, from_status,
                    to_stage, to_status, transition_kind, effective_at,
                    source_revision, source_kind, evidence_id,
                    growth_eligible, created_at
                ) VALUES (?, ?, ?, ?, NULL, NULL, ?, 'active',
                          'initialized', ?, 0, 'legacy_backfill', ?, 0, ?)
                """,
                (
                    transition_id,
                    row["pet_id"],
                    row["memory_subject_id"],
                    row["epoch_id"],
                    stage,
                    row["occurred_at"],
                    row["evidence_id"],
                    row["occurred_at"],
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO companion_academic_states(
                    pet_id, memory_subject_id, academic_stage,
                    academic_status, effective_at, source_revision,
                    transition_id, updated_at
                ) VALUES (?, ?, ?, 'active', ?, 0, ?, ?)
                """,
                (
                    row["pet_id"],
                    row["memory_subject_id"],
                    stage,
                    row["occurred_at"],
                    transition_id,
                    row["occurred_at"],
                ),
            )

    @staticmethod
    def _companion_turns_schema_is_v3(connection: sqlite3.Connection) -> bool:
        columns = {
            row["name"]: row
            for row in connection.execute("PRAGMA table_info(companion_turns)")
        }
        relationship_column = columns.get("relationship_epoch_id")
        if relationship_column is None or relationship_column["notnull"] != 1:
            return False
        foreign_key_groups: dict[int, set[tuple[str, str]]] = {}
        for row in connection.execute("PRAGMA foreign_key_list(companion_turns)"):
            if row["table"] != "relationship_epochs":
                continue
            foreign_key_groups.setdefault(row["id"], set()).add(
                (row["from"], row["to"])
            )
        required_pairs = {
            ("relationship_epoch_id", "epoch_id"),
            ("pet_id", "pet_id"),
        }
        return any(
            required_pairs <= foreign_key_pairs
            for foreign_key_pairs in foreign_key_groups.values()
        )

    @classmethod
    def _companion_turns_schema_is_v4(cls, connection: sqlite3.Connection) -> bool:
        if not cls._companion_turns_schema_is_v3(connection):
            return False
        columns = {
            row["name"]: row
            for row in connection.execute("PRAGMA table_info(companion_turns)")
        }
        policy_version_column = columns.get("policy_version")
        return (
            policy_version_column is not None and policy_version_column["notnull"] == 1
        )

    @staticmethod
    def _migrate_companion_turns_v3(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DROP TABLE IF EXISTS companion_turns_v3")
            connection.execute(
                """
                CREATE TABLE companion_turns_v3 (
                    turn_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    pet_id TEXT NOT NULL,
                    memory_subject_id TEXT NOT NULL,
                    relationship_epoch_id TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    outcome_digest TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    committed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY(turn_id, pet_id),
                    FOREIGN KEY(relationship_epoch_id, pet_id)
                        REFERENCES relationship_epochs(epoch_id, pet_id)
                )
                """
            )
            unresolved_or_ambiguous = connection.execute(
                """
                SELECT COUNT(*)
                FROM companion_turns AS turn_row
                WHERE (
                    SELECT COUNT(*)
                    FROM relationship_epochs AS epoch
                    WHERE epoch.pet_id = turn_row.pet_id
                      AND julianday(epoch.started_at)
                          <= julianday(turn_row.occurred_at)
                      AND (
                          epoch.ended_at IS NULL
                          OR julianday(turn_row.occurred_at)
                             <= julianday(epoch.ended_at)
                      )
                ) <> 1
                """
            ).fetchone()[0]
            if unresolved_or_ambiguous:
                raise sqlite3.DatabaseError(
                    "cannot resolve exactly one relationship epoch for every v2 turn"
                )
            connection.execute(
                """
                INSERT INTO companion_turns_v3(
                    turn_id, owner_user_id, pet_id, memory_subject_id,
                    relationship_epoch_id, request_digest, outcome_digest,
                    occurred_at, committed_at, status
                )
                SELECT
                    turn_id,
                    owner_user_id,
                    pet_id,
                    memory_subject_id,
                    (
                        SELECT epoch.epoch_id
                        FROM relationship_epochs AS epoch
                        WHERE epoch.pet_id = companion_turns.pet_id
                          AND julianday(epoch.started_at)
                              <= julianday(companion_turns.occurred_at)
                          AND (
                              epoch.ended_at IS NULL
                              OR julianday(companion_turns.occurred_at)
                                 <= julianday(epoch.ended_at)
                          )
                        ORDER BY julianday(epoch.started_at) DESC
                        LIMIT 1
                    ),
                    request_digest,
                    outcome_digest,
                    occurred_at,
                    committed_at,
                    status
                FROM companion_turns
                """
            )
            source_count = connection.execute(
                "SELECT COUNT(*) FROM companion_turns"
            ).fetchone()[0]
            migrated_count = connection.execute(
                "SELECT COUNT(*) FROM companion_turns_v3"
            ).fetchone()[0]
            if source_count != migrated_count:
                raise sqlite3.DatabaseError(
                    "companion_turns v3 migration did not preserve every turn"
                )
            connection.execute("DROP TABLE companion_turns")
            connection.execute(
                "ALTER TABLE companion_turns_v3 RENAME TO companion_turns"
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _migrate_companion_turns_v4(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DROP TABLE IF EXISTS companion_turns_v4")
            connection.execute(
                """
                CREATE TABLE companion_turns_v4 (
                    turn_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    pet_id TEXT NOT NULL,
                    memory_subject_id TEXT NOT NULL,
                    relationship_epoch_id TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    outcome_digest TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    committed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY(turn_id, pet_id),
                    FOREIGN KEY(relationship_epoch_id, pet_id)
                        REFERENCES relationship_epochs(epoch_id, pet_id)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO companion_turns_v4(
                    turn_id, owner_user_id, pet_id, memory_subject_id,
                    relationship_epoch_id, policy_version, request_digest,
                    outcome_digest, occurred_at, committed_at, status
                )
                SELECT
                    turn_id,
                    owner_user_id,
                    pet_id,
                    memory_subject_id,
                    relationship_epoch_id,
                    COALESCE(NULLIF(TRIM(policy_version), ''), 'companion-policy-v1'),
                    request_digest,
                    outcome_digest,
                    occurred_at,
                    committed_at,
                    status
                FROM companion_turns
                """
            )
            source_count = connection.execute(
                "SELECT COUNT(*) FROM companion_turns"
            ).fetchone()[0]
            migrated_count = connection.execute(
                "SELECT COUNT(*) FROM companion_turns_v4"
            ).fetchone()[0]
            if source_count != migrated_count:
                raise sqlite3.DatabaseError(
                    "companion_turns v4 migration did not preserve every turn"
                )
            connection.execute("DROP TABLE companion_turns")
            connection.execute(
                "ALTER TABLE companion_turns_v4 RENAME TO companion_turns"
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def claim_due_jobs(
        self,
        *,
        now: str,
        limit: int,
        lease_seconds: int,
        pet_id: str | None = None,
    ) -> tuple[ClaimedCompanionJob, ...]:
        if limit < 1:
            return ()
        lease_until = (
            datetime.fromisoformat(now) + timedelta(seconds=max(lease_seconds, 1))
        ).isoformat()
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                pet_filter = " AND pet_id = ?" if pet_id is not None else ""
                params: list[object] = [now, now, now]
                if pet_id is not None:
                    params.append(pet_id)
                params.append(limit)
                rows = connection.execute(
                    f"""
                    SELECT *
                    FROM consolidation_jobs
                    WHERE (
                        status IN ('pending', 'retry')
                        OR (
                            status = 'running'
                            AND lease_until IS NOT NULL
                            AND julianday(lease_until) <= julianday(?)
                        )
                    )
                      AND julianday(due_at) <= julianday(?)
                      AND (
                        next_attempt_at IS NULL
                       OR julianday(next_attempt_at) <= julianday(?)
                      )
                      {pet_filter}
                    ORDER BY julianday(due_at), job_id
                    LIMIT ?
                    """,
                    tuple(params),
                ).fetchall()
                claimed: list[ClaimedCompanionJob] = []
                for row in rows:
                    attempt = int(row["attempt"]) + 1
                    connection.execute(
                        """
                        UPDATE consolidation_jobs
                        SET status = 'running', attempt = ?, lease_until = ?,
                            next_attempt_at = NULL, failure_reason = NULL,
                            updated_at = ?
                        WHERE job_id = ?
                        """,
                        (attempt, lease_until, now, row["job_id"]),
                    )
                    payload = json.loads(row["payload_json"])
                    if not isinstance(payload, dict):
                        raise sqlite3.DatabaseError("job payload must be an object")
                    claimed.append(
                        ClaimedCompanionJob(
                            job_id=row["job_id"],
                            pet_id=row["pet_id"],
                            relationship_epoch_id=row["relationship_epoch_id"],
                            job_kind=row["job_kind"],
                            payload=payload,
                            attempt=attempt,
                            schema_version=row["schema_version"],
                        )
                    )
                connection.commit()
                return tuple(claimed)
            except Exception:
                connection.rollback()
                raise

    def expire_derived_objects(self, *, now: str) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                DELETE FROM companion_turn_sources
                WHERE julianday(expires_at) <= julianday(?)
                """,
                (now,),
            )
            connection.execute(
                """
                DELETE FROM companion_context_messages
                WHERE julianday(expires_at) <= julianday(?)
                  AND NOT EXISTS (
                      SELECT 1 FROM companion_context_job_pins AS pin
                      WHERE pin.message_id = companion_context_messages.message_id
                        AND pin.pet_id = companion_context_messages.pet_id
                  )
                """,
                (now,),
            )
            connection.execute(
                """
                DELETE FROM companion_retrieval_audits
                WHERE julianday(expires_at) <= julianday(?)
                """,
                (now,),
            )
            connection.commit()
            rows = connection.execute(
                """
                SELECT pet_id
                FROM companion_evidence
                WHERE status IN ('candidate', 'active')
                  AND expires_at IS NOT NULL
                  AND julianday(expires_at) <= julianday(?)
                UNION
                SELECT pet_id
                FROM companion_adjustments
                WHERE status IN ('candidate', 'trial')
                  AND valid_until IS NOT NULL
                  AND julianday(valid_until) <= julianday(?)
                UNION
                SELECT pet_id
                FROM session_capsules
                WHERE status = 'active'
                  AND expires_at IS NOT NULL
                  AND julianday(expires_at) <= julianday(?)
                """,
                (now, now, now),
            ).fetchall()
        for row in rows:
            pet_id = row["pet_id"]
            with self.pet_reflection_guard(pet_id):
                self._expire_derived_objects_for_pet(pet_id=pet_id, now=now)

    def expire_derived_objects_for_pet(self, *, pet_id: str, now: str) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                DELETE FROM companion_turn_sources
                WHERE pet_id = ? AND julianday(expires_at) <= julianday(?)
                """,
                (pet_id, now),
            )
            connection.execute(
                """
                DELETE FROM companion_context_messages
                WHERE pet_id = ?
                  AND julianday(expires_at) <= julianday(?)
                  AND NOT EXISTS (
                    SELECT 1 FROM companion_context_job_pins AS pin
                    WHERE pin.message_id = companion_context_messages.message_id
                      AND pin.pet_id = companion_context_messages.pet_id
                  )
                """,
                (pet_id, now),
            )
            connection.execute(
                """
                DELETE FROM companion_retrieval_audits
                WHERE pet_id = ? AND julianday(expires_at) <= julianday(?)
                """,
                (pet_id, now),
            )
            connection.commit()
        with self.pet_reflection_guard(pet_id):
            self._expire_derived_objects_for_pet(pet_id=pet_id, now=now)

    @staticmethod
    def _revoke_adjustments_for_evidence_ids(
        connection: sqlite3.Connection,
        *,
        evidence_ids: tuple[str, ...],
    ) -> None:
        unique_ids = tuple(dict.fromkeys(evidence_ids))
        if not unique_ids:
            return
        placeholders = ",".join("?" for _ in unique_ids)
        connection.execute(
            f"""
            UPDATE companion_adjustments AS adjustment
            SET status = 'revoked'
            WHERE adjustment.status IN ('candidate', 'trial', 'active')
              AND (
                EXISTS (
                    SELECT 1
                    FROM adjustment_evidence_qualification AS qualification
                    WHERE qualification.adjustment_id = adjustment.adjustment_id
                      AND qualification.evidence_id IN ({placeholders})
                      AND (
                        qualification.contributes_date = 1
                        OR (
                            qualification.qualification = 'clue_only'
                            AND NOT EXISTS (
                                SELECT 1
                                FROM adjustment_evidence_qualification AS vote
                                WHERE vote.adjustment_id = adjustment.adjustment_id
                                  AND vote.contributes_date = 1
                            )
                        )
                      )
                )
                OR (
                    NOT EXISTS (
                        SELECT 1
                        FROM adjustment_evidence_qualification AS any_qualification
                        WHERE any_qualification.adjustment_id = adjustment.adjustment_id
                    )
                    AND EXISTS (
                        SELECT 1
                        FROM adjustment_evidence AS legacy_link
                        WHERE legacy_link.adjustment_id = adjustment.adjustment_id
                          AND legacy_link.evidence_id IN ({placeholders})
                    )
                )
              )
            """,
            (*unique_ids, *unique_ids),
        )

    def _expire_derived_objects_for_pet(self, *, pet_id: str, now: str) -> None:
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    UPDATE companion_evidence
                    SET content_json = json_remove(
                        content_json, '$.source_quote', '$.source_quotes'
                    )
                    WHERE status = 'candidate'
                      AND source_kind = 'conversation_candidate'
                      AND pet_id = ?
                      AND expires_at IS NOT NULL
                      AND julianday(expires_at) <= julianday(?)
                    """,
                    (pet_id, now),
                )
                connection.execute(
                    """
                    UPDATE companion_evidence
                    SET status = 'expired', prompt_eligible = 0
                    WHERE status IN ('candidate', 'active')
                      AND pet_id = ?
                      AND expires_at IS NOT NULL
                      AND julianday(expires_at) <= julianday(?)
                    """,
                    (pet_id, now),
                )
                inactive_adjustment_evidence_ids = tuple(
                    row["evidence_id"]
                    for row in connection.execute(
                        """
                        SELECT evidence_id
                        FROM companion_evidence
                        WHERE pet_id = ?
                          AND status IN ('superseded', 'forgotten', 'expired')
                        """,
                        (pet_id,),
                    )
                )
                connection.execute(
                    """
                    UPDATE session_capsules
                    SET status = 'invalidated'
                    WHERE status = 'active'
                      AND pet_id = ?
                      AND EXISTS (
                        SELECT 1
                        FROM capsule_evidence AS link
                        JOIN companion_evidence AS evidence
                          ON evidence.evidence_id = link.evidence_id
                         AND evidence.pet_id = link.pet_id
                        WHERE link.capsule_id = session_capsules.capsule_id
                          AND evidence.status IN (
                            'superseded', 'forgotten', 'expired'
                          )
                      )
                    """,
                    (pet_id,),
                )
                self._revoke_adjustments_for_evidence_ids(
                    connection,
                    evidence_ids=inactive_adjustment_evidence_ids,
                )
                connection.execute(
                    """
                    UPDATE companion_chapters
                    SET status = 'invalidated'
                    WHERE status = 'active'
                      AND pet_id = ?
                      AND EXISTS (
                        SELECT 1
                        FROM chapter_evidence AS link
                        JOIN companion_evidence AS evidence
                          ON evidence.evidence_id = link.evidence_id
                         AND evidence.pet_id = link.pet_id
                        WHERE link.chapter_id = companion_chapters.chapter_id
                          AND evidence.status IN (
                            'superseded', 'forgotten', 'expired'
                          )
                      )
                    """,
                    (pet_id,),
                )
                connection.execute(
                    """
                    UPDATE companion_adjustments
                    SET status = 'expired'
                    WHERE status IN ('candidate', 'trial')
                      AND pet_id = ?
                      AND valid_until IS NOT NULL
                      AND julianday(valid_until) <= julianday(?)
                    """,
                    (pet_id, now),
                )
                connection.execute(
                    """
                    UPDATE session_capsules AS capsule
                    SET status = 'expired'
                    WHERE capsule.status = 'active'
                      AND capsule.pet_id = ?
                      AND capsule.expires_at IS NOT NULL
                      AND julianday(capsule.expires_at) <= julianday(?)
                      AND NOT EXISTS (
                        SELECT 1
                        FROM capsule_evidence AS capsule_link
                        JOIN adjustment_evidence AS adjustment_link
                          ON adjustment_link.evidence_id = capsule_link.evidence_id
                         AND adjustment_link.pet_id = capsule_link.pet_id
                        JOIN companion_adjustments AS adjustment
                          ON adjustment.adjustment_id = adjustment_link.adjustment_id
                         AND adjustment.pet_id = adjustment_link.pet_id
                        WHERE capsule_link.capsule_id = capsule.capsule_id
                          AND adjustment.status IN ('candidate', 'trial', 'active')
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM capsule_evidence AS capsule_link
                        JOIN chapter_evidence AS chapter_link
                          ON chapter_link.evidence_id = capsule_link.evidence_id
                         AND chapter_link.pet_id = capsule_link.pet_id
                        JOIN companion_chapters AS chapter
                          ON chapter.chapter_id = chapter_link.chapter_id
                         AND chapter.pet_id = chapter_link.pet_id
                        WHERE capsule_link.capsule_id = capsule.capsule_id
                          AND chapter.status = 'active'
                      )
                    """,
                    (pet_id, now),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def load_job_evidence(
        self,
        *,
        job: ClaimedCompanionJob,
        now: str,
    ) -> tuple[CompanionEvidence, ...]:
        raw_ids = job.payload.get("evidence_ids", ())
        if not isinstance(raw_ids, list) or any(
            not isinstance(item, str) or not item.strip() for item in raw_ids
        ):
            raise ValueError("job evidence_ids are invalid")
        evidence_ids = tuple(dict.fromkeys(raw_ids))
        if not evidence_ids:
            return ()
        placeholders = ",".join("?" for _ in evidence_ids)
        with self.connection() as connection:
            memory_subject_id = _job_memory_subject_id(connection, job)
            lease = connection.execute(
                """
                SELECT 1
                FROM consolidation_jobs AS job
                LEFT JOIN relationship_epochs AS epoch
                  ON epoch.epoch_id = job.relationship_epoch_id
                 AND epoch.pet_id = job.pet_id
                WHERE job.job_id = ?
                  AND job.pet_id = ?
                  AND job.status = 'running'
                  AND job.attempt = ?
                  AND (
                    job.relationship_epoch_id IS NULL
                    OR epoch.ended_at IS NULL
                  )
                """,
                (job.job_id, job.pet_id, job.attempt),
            ).fetchone()
            if lease is None:
                raise CompanionJobLeaseLostError(
                    "job or relationship epoch is no longer active"
                )
            rows = connection.execute(
                f"""
                SELECT *
                FROM companion_evidence
                WHERE evidence_id IN ({placeholders})
                  AND pet_id = ?
                  AND memory_subject_id = ?
                  AND status = 'active'
                  AND (expires_at IS NULL OR julianday(expires_at) > julianday(?))
                  AND (
                    ownership_scope = 'user'
                    OR relationship_epoch_id = ?
                  )
                ORDER BY occurred_at, evidence_id
                """,
                (
                    *evidence_ids,
                    job.pet_id,
                    memory_subject_id,
                    now,
                    job.relationship_epoch_id,
                ),
            ).fetchall()
        return tuple(_evidence_from_row(row) for row in rows)

    def load_turn_sources(
        self,
        *,
        job: ClaimedCompanionJob,
        now: str,
    ) -> tuple[ReflectionTurnSource, ...]:
        if job.job_kind != "memory_candidate_extraction":
            raise ValueError("turn sources require a memory candidate job")
        turn_id = job.payload.get("turn_id")
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise ReflectionValidationError("candidate job turn_id is invalid")
        with self.connection() as connection:
            memory_subject_id = _job_memory_subject_id(connection, job)
            row = connection.execute(
                """
                SELECT source.turn_id, source.source_text, source.occurred_at
                FROM companion_turn_sources AS source
                JOIN consolidation_jobs AS job
                  ON job.job_id = ? AND job.pet_id = source.pet_id
                LEFT JOIN relationship_epochs AS epoch
                  ON epoch.epoch_id = job.relationship_epoch_id
                 AND epoch.pet_id = job.pet_id
                WHERE source.turn_id = ?
                  AND source.pet_id = ?
                  AND source.memory_subject_id = ?
                  AND julianday(source.expires_at) > julianday(?)
                  AND job.status = 'running'
                  AND job.attempt = ?
                  AND (
                    job.relationship_epoch_id IS NULL
                    OR epoch.ended_at IS NULL
                  )
                """,
                (
                    job.job_id,
                    turn_id,
                    job.pet_id,
                    memory_subject_id,
                    now,
                    job.attempt,
                ),
            ).fetchone()
        if row is None:
            return ()
        return (
            ReflectionTurnSource(
                turn_id=row["turn_id"],
                text=row["source_text"],
                occurred_at=row["occurred_at"],
            ),
        )

    def load_memory_interpretation_request(
        self,
        *,
        job: ClaimedCompanionJob,
        now: str,
    ) -> MemoryInterpretationRequest | None:
        turn_id = job.payload.get("turn_id")
        conversation_digest = job.payload.get("conversation_digest")
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise ReflectionValidationError("candidate job turn_id is invalid")
        if not isinstance(conversation_digest, str) or not conversation_digest:
            return None
        with self.connection() as connection:
            memory_subject_id = _job_memory_subject_id(connection, job)
            owner = connection.execute(
                "SELECT owner_user_id FROM companion_pets WHERE pet_id = ?",
                (job.pet_id,),
            ).fetchone()
            current = connection.execute(
                """
                SELECT occurred_at FROM companion_turn_sources
                WHERE turn_id = ? AND pet_id = ? AND memory_subject_id = ?
                  AND julianday(expires_at) > julianday(?)
                """,
                (turn_id, job.pet_id, memory_subject_id, now),
            ).fetchone()
            if owner is None or current is None:
                return None
            rows = connection.execute(
                """
                SELECT message_id, turn_id, role, source_text, occurred_at
                FROM companion_context_messages
                WHERE pet_id = ? AND memory_subject_id = ?
                  AND conversation_digest = ?
                  AND julianday(expires_at) > julianday(?)
                  AND julianday(occurred_at) <= julianday(?)
                  AND NOT (turn_id = ? AND role = 'assistant')
                ORDER BY julianday(occurred_at) DESC,
                         CASE role WHEN 'user' THEN 0 ELSE 1 END DESC
                LIMIT 6
                """,
                (
                    job.pet_id,
                    memory_subject_id,
                    conversation_digest,
                    now,
                    current["occurred_at"],
                    turn_id,
                ),
            ).fetchall()
            fact_rows = connection.execute(
                """
                SELECT evidence_id, fact_key, kind, content_json,
                       sensitivity, occurred_at
                FROM companion_evidence
                WHERE pet_id = ? AND memory_subject_id = ?
                  AND ownership_scope = 'user' AND status = 'active'
                  AND fact_key IS NOT NULL
                  AND (expires_at IS NULL OR julianday(expires_at) > julianday(?))
                  AND (valid_until IS NULL OR julianday(valid_until) > julianday(?))
                ORDER BY occurred_at DESC, evidence_id
                LIMIT ?
                """,
                (
                    job.pet_id,
                    memory_subject_id,
                    now,
                    now,
                    MEMORY_INTERPRETATION_MAX_EXISTING_FACTS,
                ),
            ).fetchall()
        selected: list[sqlite3.Row] = []
        used_chars = 0
        for row in rows:
            text = str(row["source_text"])
            if used_chars + len(text) > 3000:
                continue
            selected.append(row)
            used_chars += len(text)
        sources = tuple(
            MemorySource(
                turn_id=(
                    str(row["turn_id"])
                    if row["role"] == "user"
                    else str(row["message_id"])
                ),
                role=str(row["role"]),
                text=str(row["source_text"]),
                occurred_at=str(row["occurred_at"]),
            )
            for row in reversed(selected)
        )
        if not any(item.turn_id == turn_id and item.role == "user" for item in sources):
            return None
        existing_fact_items: list[MemoryExistingFact] = []
        for row in fact_rows:
            fact_key = canonical_memory_fact_key(
                str(row["fact_key"]),
                kind=str(row["kind"]),
            )
            existing_fact_items.append(
                MemoryExistingFact(
                    evidence_id=str(row["evidence_id"]),
                    fact_key=fact_key,
                    kind=str(row["kind"]),
                    canonical_value=_semantic_canonical_value(row["content_json"]),
                    sensitivity=str(row["sensitivity"]),
                    occurred_at=str(row["occurred_at"]),
                )
            )
        existing_facts = tuple(existing_fact_items)
        return MemoryInterpretationRequest(
            request_id=job.job_id,
            subject=CompanionSubjectContext(
                owner_user_id=str(owner["owner_user_id"]),
                pet_id=job.pet_id,
                memory_subject_id=memory_subject_id,
                speaker_identity="confirmed",
                academic_stage="unknown",
                persistence_allowed=True,
            ),
            current_turn_id=turn_id,
            sources=sources,
            existing_facts=existing_facts,
        )

    def semantic_memory_effective_mode(
        self,
        *,
        requested_mode: str,
    ) -> tuple[str, str]:
        if requested_mode not in {"shadow", "candidate", "active_explicit"}:
            raise ReflectionValidationError("semantic memory mode is invalid")
        with self.connection() as connection:
            backlog = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM consolidation_jobs
                    WHERE job_kind = 'memory_candidate_extraction'
                      AND status IN ('pending', 'retry', 'running')
                    """
                ).fetchone()[0]
            )
            terminal = connection.execute(
                """
                SELECT status FROM consolidation_jobs
                WHERE job_kind = 'memory_candidate_extraction'
                  AND status IN ('succeeded', 'failed')
                ORDER BY updated_at DESC, job_id DESC
                LIMIT 50
                """
            ).fetchall()
        failures = sum(row["status"] == "failed" for row in terminal)
        error_rate = failures / len(terminal) if terminal else 0.0
        recently_healthy = len(
            terminal
        ) >= _SEMANTIC_MEMORY_RECOVERY_SUCCESS_STREAK and all(
            row["status"] == "succeeded"
            for row in terminal[:_SEMANTIC_MEMORY_RECOVERY_SUCCESS_STREAK]
        )
        if requested_mode == "active_explicit" and (
            backlog > 100
            or (len(terminal) >= 10 and error_rate > 0.2 and not recently_healthy)
        ):
            return "candidate", "active_release_guard_downgrade"
        if requested_mode == "candidate" and (
            backlog > 500
            or (len(terminal) >= 10 and error_rate > 0.5 and not recently_healthy)
        ):
            return "shadow", "candidate_release_guard_downgrade"
        return requested_mode, "configured_mode"

    def apply_semantic_memory_result(
        self,
        *,
        job: ClaimedCompanionJob,
        request: MemoryInterpretationRequest,
        result: MemoryInterpretationResult,
        mode: str,
        now: str,
        model: str,
        prompt_version: str | None = None,
        duration_ms: float = 0.0,
        release_guard_reason: str = "configured_mode",
        explicit_correction_release_enabled: bool = False,
    ) -> None:
        if job.job_kind != "memory_candidate_extraction":
            raise ReflectionValidationError("job is not a memory candidate extraction")
        if mode not in {"shadow", "candidate", "active_explicit"}:
            raise ReflectionValidationError("semantic memory mode is invalid")
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                memory_subject_id = _job_memory_subject_id(connection, job)
                lease = connection.execute(
                    """
                    SELECT 1 FROM consolidation_jobs
                    WHERE job_id = ? AND pet_id = ? AND status = 'running'
                      AND attempt = ? AND lease_until IS NOT NULL
                      AND julianday(lease_until) > julianday(?)
                    """,
                    (job.job_id, job.pet_id, job.attempt, now),
                ).fetchone()
                if (
                    lease is None
                    or memory_subject_id != request.subject.memory_subject_id
                ):
                    raise CompanionJobLeaseLostError(
                        "semantic memory job lease is no longer active"
                    )
                candidate_expires_at = (
                    datetime.fromisoformat(now) + timedelta(days=30)
                ).isoformat()
                action_counts: dict[str, int] = {}
                reason_counts: dict[str, int] = {}
                claim_type_counts: dict[str, int] = {}
                conflict_count = 0
                semantic_fact_keys: list[str] = []
                policy = MemoryWritePolicy()
                for index, proposal in enumerate(result.proposals):
                    semantic_fact_keys.append(proposal.fact_key)
                    claim_type_counts[proposal.claim_type] = (
                        claim_type_counts.get(proposal.claim_type, 0) + 1
                    )
                    fact_key_aliases = memory_fact_key_storage_aliases(
                        proposal.fact_key
                    )
                    fact_key_placeholders = ",".join("?" for _ in fact_key_aliases)
                    current_rows = list(
                        connection.execute(
                            f"""
                        SELECT evidence_id, fact_key, kind, content_json,
                               sensitivity, occurred_at, status
                        FROM companion_evidence
                        WHERE pet_id = ? AND memory_subject_id = ?
                          AND fact_key IN ({fact_key_placeholders})
                          AND status IN ('candidate', 'active')
                          AND (expires_at IS NULL OR julianday(expires_at) > julianday(?))
                          AND (valid_until IS NULL OR julianday(valid_until) > julianday(?))
                        ORDER BY occurred_at DESC, evidence_id DESC
                        """,
                            (
                                job.pet_id,
                                memory_subject_id,
                                *fact_key_aliases,
                                now,
                                now,
                            ),
                        ).fetchall()
                    )
                    target_row = None
                    if proposal.target_evidence_id is not None:
                        target_row = next(
                            (
                                row
                                for row in current_rows
                                if str(row["evidence_id"])
                                == proposal.target_evidence_id
                            ),
                            None,
                        )
                        if target_row is None:
                            target_row = connection.execute(
                                """
                                SELECT evidence_id, fact_key, kind, content_json,
                                       sensitivity, occurred_at, status
                                FROM companion_evidence
                                WHERE evidence_id = ? AND pet_id = ?
                                  AND memory_subject_id = ?
                                  AND ownership_scope = 'user'
                                  AND status = 'active'
                                  AND (expires_at IS NULL OR julianday(expires_at) > julianday(?))
                                  AND (valid_until IS NULL OR julianday(valid_until) > julianday(?))
                                """,
                                (
                                    proposal.target_evidence_id,
                                    job.pet_id,
                                    memory_subject_id,
                                    now,
                                    now,
                                ),
                            ).fetchone()
                            if target_row is not None:
                                current_rows.append(target_row)
                        if (
                            target_row is None
                            or str(target_row["kind"]) != proposal.kind
                        ):
                            raise ReflectionValidationError(
                                "semantic memory target evidence is no longer active"
                            )
                    completed_goal_text = _explicit_goal_transition_completed_text(
                        request,
                        proposal,
                    )
                    explicit_semantic_memory_request = (
                        _is_explicit_semantic_memory_request(request, proposal)
                    )
                    naturally_persistent = (
                        memory_proposal_is_naturally_persistent(proposal)
                    )
                    goal_transition_replacement_enabled = (
                        completed_goal_text is not None
                        and (
                            explicit_semantic_memory_request
                            or explicit_correction_release_enabled
                        )
                    )
                    if goal_transition_replacement_enabled:
                        other_goal_rows = connection.execute(
                            """
                            SELECT evidence_id, fact_key, kind, content_json,
                                   sensitivity, occurred_at, status
                            FROM companion_evidence
                            WHERE pet_id = ? AND memory_subject_id = ?
                              AND kind = 'goal' AND status = 'active'
                            ORDER BY occurred_at DESC, evidence_id DESC
                            """,
                            (job.pet_id, memory_subject_id),
                        ).fetchall()
                        known_ids = {str(row["evidence_id"]) for row in current_rows}
                        matching_goal_rows = [
                            row
                            for row in other_goal_rows
                            if str(row["evidence_id"]) not in known_ids
                            and _goal_value_is_explicitly_completed(
                                _semantic_canonical_value(row["content_json"]),
                                completed_goal_text,
                            )
                        ]
                        if len(matching_goal_rows) == 1:
                            current_rows.extend(matching_goal_rows)
                    current_facts = tuple(
                        MemoryExistingFact(
                            evidence_id=str(row["evidence_id"]),
                            fact_key=canonical_memory_fact_key(
                                str(row["fact_key"]),
                                kind=str(row["kind"]),
                            ),
                            kind=str(row["kind"]),
                            canonical_value=_semantic_canonical_value(
                                row["content_json"]
                            ),
                            sensitivity=str(row["sensitivity"]),
                            occurred_at=str(row["occurred_at"]),
                        )
                        for row in current_rows
                    )
                    explicit_memory_request = (
                        explicit_semantic_memory_request
                        or (
                            completed_goal_text is not None
                            and explicit_correction_release_enabled
                        )
                    )
                    duplicate = next(
                        (
                            row
                            for row, fact in zip(current_rows, current_facts)
                            if fact.canonical_value == proposal.canonical_value
                        ),
                        None,
                    )
                    if proposal.memory_action == "reinforce":
                        decision = policy.decide(
                            proposal,
                            mode=mode,
                            existing_facts=current_facts,
                        )
                        action = decision.action
                        reason_code = decision.reason_code
                    elif duplicate is not None and not (
                        duplicate["status"] == "candidate"
                        and (explicit_memory_request or naturally_persistent)
                    ):
                        action = "drop"
                        reason_code = (
                            "duplicate_active_fact"
                            if duplicate["status"] == "active"
                            else "duplicate_candidate"
                        )
                    else:
                        policy_facts = (
                            tuple(
                                fact
                                for row, fact in zip(current_rows, current_facts)
                                if row["status"] == "active"
                            )
                            if explicit_memory_request or naturally_persistent
                            else current_facts
                        )
                        decision = policy.decide(
                            proposal,
                            mode=mode,
                            existing_facts=policy_facts,
                            explicit_correction=(
                                explicit_correction_release_enabled
                                and _is_explicit_semantic_correction(request, proposal)
                            ),
                            explicit_memory_request=explicit_memory_request,
                        )
                        action = decision.action
                        reason_code = decision.reason_code
                    if reason_code == "conflicting_fact":
                        conflict_count += 1
                    action_counts[action] = action_counts.get(action, 0) + 1
                    reason_counts[reason_code] = reason_counts.get(reason_code, 0) + 1
                    if action == "reinforce":
                        if target_row is None:
                            raise ReflectionValidationError(
                                "semantic reinforcement target is missing"
                            )
                        reinforced_content = json.loads(target_row["content_json"])
                        previous_count = reinforced_content.get("reinforcement_count", 0)
                        reinforced_content["reinforcement_count"] = (
                            previous_count + 1
                            if isinstance(previous_count, int)
                            and not isinstance(previous_count, bool)
                            else 1
                        )
                        reinforced_content["last_reinforced_at"] = now
                        connection.execute(
                            """
                            UPDATE companion_evidence
                            SET content_json = ?
                            WHERE evidence_id = ? AND status = 'active'
                            """,
                            (
                                _stable_json(reinforced_content),
                                target_row["evidence_id"],
                            ),
                        )
                        continue
                    if action in {"drop", "shadow"}:
                        continue
                    is_active = action == "active"
                    evidence_id = str(
                        uuid5(
                            NAMESPACE_URL,
                            "xiaoxin:semantic-memory:"
                            f"{job.job_id}:{index}:{proposal.fact_key}",
                        )
                    )
                    quotes = [
                        {"turn_id": item.turn_id, "quote": item.quote}
                        for item in proposal.source_quotes
                    ]
                    quote_digests = [
                        {
                            "turn_id": item.turn_id,
                            "quote_digest": hashlib.sha256(
                                item.quote.encode("utf-8")
                            ).hexdigest(),
                        }
                        for item in proposal.source_quotes
                    ]
                    first_quote = proposal.source_quotes[0].quote
                    content = {
                        "canonical_value": proposal.canonical_value,
                        "source_turn_ids": [
                            item.turn_id for item in proposal.source_quotes
                        ],
                        "source_quote_digests": quote_digests,
                        "source_quote_digest": hashlib.sha256(
                            first_quote.encode("utf-8")
                        ).hexdigest(),
                        "claim_type": proposal.claim_type,
                        "subject_scope": proposal.subject_scope,
                        "temporal_scope": proposal.temporal_scope,
                        "reason_code": proposal.reason_code,
                        "write_reason_code": reason_code,
                        "memory_action": proposal.memory_action,
                        "target_evidence_id": proposal.target_evidence_id,
                    }
                    if not is_active:
                        content["source_quotes"] = quotes
                        content["source_quote"] = first_quote
                    connection.execute(
                        """
                        INSERT INTO companion_evidence(
                            evidence_id, pet_id, memory_subject_id,
                            ownership_scope, relationship_epoch_id, kind,
                            content_json, fact_key, importance, sensitivity,
                            valid_from, valid_until, source_kind, source_ref,
                            source_summary, attribution, confidence, occurred_at,
                            retention, status, prompt_eligible, expires_at,
                            created_at
                        ) VALUES (
                            ?, ?, ?, 'user', NULL, ?, ?, ?, 0.6, ?, ?, ?,
                            'conversation_candidate', ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?
                        )
                        """,
                        (
                            evidence_id,
                            job.pet_id,
                            memory_subject_id,
                            proposal.kind,
                            _stable_json(content),
                            proposal.fact_key,
                            proposal.sensitivity,
                            now,
                            proposal.valid_until,
                            request.current_turn_id,
                            proposal.canonical_value[:240],
                            proposal.claim_type,
                            proposal.confidence,
                            now,
                            (
                                "until_expiry"
                                if is_active
                                and proposal.memory_action == "temporary_override"
                                else (
                                    "persistent" if is_active else "until_confirmed"
                                )
                            ),
                            "active" if is_active else "candidate",
                            1 if is_active else 0,
                            (
                                proposal.valid_until
                                if is_active
                                and proposal.memory_action == "temporary_override"
                                else (None if is_active else candidate_expires_at)
                            ),
                            now,
                        ),
                    )
                    if (
                        target_row is not None
                        and proposal.memory_action in {"coexist", "temporary_override"}
                        and not naturally_persistent
                    ):
                        relation_kind = (
                            "coexists_with"
                            if proposal.memory_action == "coexist"
                            else "temporarily_overridden_by"
                        )
                        connection.execute(
                            """
                            INSERT INTO evidence_relations(
                                relation_id, pet_id, relation_kind,
                                source_evidence_id, target_evidence_id, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                str(
                                    uuid5(
                                        NAMESPACE_URL,
                                        "xiaoxin:semantic-relation:"
                                        f"{relation_kind}:"
                                        f"{target_row['evidence_id']}:{evidence_id}",
                                    )
                                ),
                                job.pet_id,
                                relation_kind,
                                target_row["evidence_id"],
                                evidence_id,
                                now,
                            ),
                        )
                    if is_active:
                        if naturally_persistent:
                            replaced_rows = current_rows
                        elif proposal.memory_action == "replace":
                            replaced_rows = (
                                [target_row] if target_row is not None else []
                            )
                        elif proposal.memory_action in {
                            "coexist",
                            "temporary_override",
                        }:
                            replaced_rows = []
                        else:
                            replaced_rows = current_rows
                        for old in replaced_rows:
                            old_evidence_id = str(old["evidence_id"])
                            connection.execute(
                                """
                                UPDATE companion_evidence
                                SET status = 'superseded', prompt_eligible = 0,
                                    content_json = CASE
                                        WHEN source_kind = 'conversation_candidate'
                                        THEN json_remove(
                                            content_json,
                                            '$.source_quote', '$.source_quotes'
                                        )
                                        ELSE content_json
                                    END
                                WHERE evidence_id = ?
                                """,
                                (old_evidence_id,),
                            )
                            connection.execute(
                                """
                                INSERT INTO evidence_relations(
                                    relation_id, pet_id, relation_kind,
                                    source_evidence_id, target_evidence_id,
                                    created_at
                                ) VALUES (?, ?, 'superseded_by', ?, ?, ?)
                                """,
                                (
                                    str(
                                        uuid5(
                                            NAMESPACE_URL,
                                            "xiaoxin:semantic-correction:"
                                            f"{old_evidence_id}:{evidence_id}",
                                        )
                                    ),
                                    job.pet_id,
                                    old_evidence_id,
                                    evidence_id,
                                    now,
                                ),
                            )
                            self._invalidate_initiative_opportunities_for_evidence(
                                connection,
                                evidence_ids=(old_evidence_id,),
                                reason_code="evidence_superseded",
                                now=now,
                                scrub=False,
                            )
                            self._revoke_adjustments_for_evidence_ids(
                                connection,
                                evidence_ids=(old_evidence_id,),
                            )
                legacy_fact_keys = tuple(
                    item
                    for item in job.payload.get("legacy_fact_keys", ())
                    if isinstance(item, str) and item.strip()
                )
                connection.execute(
                    """
                    INSERT INTO semantic_memory_evaluations(
                        evaluation_id, job_id, pet_id, memory_subject_id, mode,
                        release_guard_reason,
                        proposal_count, action_counts_json, reason_counts_json,
                        claim_type_counts_json, legacy_fact_keys_digest,
                        legacy_fact_count, semantic_fact_keys_digest,
                        conflict_count, duration_ms, model, prompt_tokens,
                        completion_tokens, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL,
                              NULL, ?)
                    """,
                    (
                        str(
                            uuid5(NAMESPACE_URL, f"xiaoxin:semantic-eval:{job.job_id}")
                        ),
                        job.job_id,
                        job.pet_id,
                        memory_subject_id,
                        mode,
                        release_guard_reason,
                        len(result.proposals),
                        _stable_json(action_counts),
                        _stable_json(reason_counts),
                        _stable_json(claim_type_counts),
                        hashlib.sha256(
                            _stable_json(legacy_fact_keys).encode()
                        ).hexdigest(),
                        len(legacy_fact_keys),
                        hashlib.sha256(
                            _stable_json(tuple(semantic_fact_keys)).encode()
                        ).hexdigest(),
                        conflict_count,
                        max(float(duration_ms), 0.0),
                        model,
                        now,
                    ),
                )
                updated = connection.execute(
                    """
                    UPDATE consolidation_jobs
                    SET status = 'succeeded', model = ?, prompt_version = ?,
                        lease_until = NULL, next_attempt_at = NULL,
                        failure_reason = NULL, updated_at = ?
                    WHERE job_id = ? AND status = 'running' AND attempt = ?
                    """,
                    (
                        model,
                        prompt_version,
                        now,
                        job.job_id,
                        job.attempt,
                    ),
                )
                if updated.rowcount != 1:
                    raise CompanionJobLeaseLostError(
                        "semantic memory job lease is no longer owned"
                    )
                connection.execute(
                    "DELETE FROM companion_turn_sources WHERE turn_id = ? AND pet_id = ?",
                    (request.current_turn_id, job.pet_id),
                )
                connection.execute(
                    "DELETE FROM companion_context_job_pins WHERE job_id = ?",
                    (job.job_id,),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _select_chapter_evidence_rows(
        rows: list[sqlite3.Row],
    ) -> list[sqlite3.Row]:
        ordered = sorted(rows, key=lambda row: (row["occurred_at"], row["evidence_id"]))
        for newest_index in range(len(ordered) - 1, -1, -1):
            newest = ordered[newest_index]
            newest_date = (
                datetime.fromisoformat(newest["occurred_at"])
                .astimezone(_SHANGHAI_TIMEZONE)
                .date()
            )
            for older_index in range(newest_index - 1, -1, -1):
                older = ordered[older_index]
                older_date = (
                    datetime.fromisoformat(older["occurred_at"])
                    .astimezone(_SHANGHAI_TIMEZONE)
                    .date()
                )
                if older_date == newest_date:
                    continue
                if "relationship" not in {
                    older["ownership_scope"],
                    newest["ownership_scope"],
                }:
                    continue
                selected_ids = {older["evidence_id"], newest["evidence_id"]}
                extra = next(
                    (
                        row
                        for row in reversed(ordered)
                        if row["evidence_id"] not in selected_ids
                    ),
                    None,
                )
                selected = [older, newest]
                if extra is not None:
                    selected.append(extra)
                return sorted(
                    selected,
                    key=lambda row: (row["occurred_at"], row["evidence_id"]),
                )
        return []

    def load_chapter_evidence(
        self,
        *,
        job: ClaimedCompanionJob,
        now: str,
    ) -> tuple[CompanionEvidence, ...]:
        if job.relationship_epoch_id is None:
            return ()
        with self.connection() as connection:
            memory_subject_id = _job_memory_subject_id(connection, job)
            lease = connection.execute(
                """
                SELECT 1
                FROM consolidation_jobs AS job
                JOIN relationship_epochs AS epoch
                  ON epoch.epoch_id = job.relationship_epoch_id
                 AND epoch.pet_id = job.pet_id
                WHERE job.job_id = ? AND job.pet_id = ?
                  AND job.status = 'running' AND job.attempt = ?
                  AND epoch.ended_at IS NULL
                """,
                (job.job_id, job.pet_id, job.attempt),
            ).fetchone()
            if lease is None:
                raise CompanionJobLeaseLostError(
                    "job or relationship epoch is no longer active"
                )
            rows = connection.execute(
                """
                SELECT *
                FROM companion_evidence
                WHERE pet_id = ?
                  AND memory_subject_id = ?
                  AND status = 'active'
                  AND kind <> 'system_event'
                  AND (expires_at IS NULL OR julianday(expires_at) > julianday(?))
                  AND (
                    ownership_scope = 'user'
                    OR relationship_epoch_id = ?
                  )
                ORDER BY occurred_at, evidence_id
                """,
                (job.pet_id, memory_subject_id, now, job.relationship_epoch_id),
            ).fetchall()
        period_start = job.payload.get("period_start")
        period_end = job.payload.get("period_end")
        if isinstance(period_start, str):
            rows = [row for row in rows if row["occurred_at"] >= period_start]
        if isinstance(period_end, str):
            rows = [row for row in rows if row["occurred_at"] <= period_end]
        selected = self._select_chapter_evidence_rows(rows)
        return tuple(_evidence_from_row(row) for row in selected)

    def job_evidence_is_still_active(
        self,
        *,
        job: ClaimedCompanionJob,
        evidence_ids: tuple[str, ...],
        now: str,
    ) -> bool:
        if not evidence_ids:
            return True
        placeholders = ",".join("?" for _ in evidence_ids)
        with self.connection() as connection:
            memory_subject_id = _job_memory_subject_id(connection, job)
            count = connection.execute(
                f"""
                SELECT COUNT(*)
                FROM companion_evidence
                WHERE evidence_id IN ({placeholders})
                  AND pet_id = ?
                  AND memory_subject_id = ?
                  AND status = 'active'
                  AND (expires_at IS NULL OR julianday(expires_at) > julianday(?))
                  AND (
                    ownership_scope = 'user'
                    OR relationship_epoch_id = ?
                  )
                """,
                (
                    *evidence_ids,
                    job.pet_id,
                    memory_subject_id,
                    now,
                    job.relationship_epoch_id,
                ),
            ).fetchone()[0]
        return int(count) == len(set(evidence_ids))

    def apply_reflection_proposal(
        self,
        *,
        job: ClaimedCompanionJob,
        proposal: ReflectionProposal,
        evidence_ids: tuple[str, ...],
        now: str,
        model: str,
        prompt_version: str | None,
    ) -> None:
        unique_evidence_ids = tuple(dict.fromkeys(evidence_ids))
        if not unique_evidence_ids:
            raise ReflectionValidationError("session capsule requires cited Evidence")
        placeholders = ",".join("?" for _ in unique_evidence_ids)
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                memory_subject_id = _job_memory_subject_id(connection, job)
                lease = connection.execute(
                    """
                    SELECT 1
                    FROM consolidation_jobs AS job
                    JOIN relationship_epochs AS epoch
                      ON epoch.epoch_id = job.relationship_epoch_id
                     AND epoch.pet_id = job.pet_id
                    WHERE job.job_id = ?
                      AND job.pet_id = ?
                      AND job.status = 'running'
                      AND job.attempt = ?
                      AND job.lease_until IS NOT NULL
                      AND julianday(job.lease_until) > julianday(?)
                      AND epoch.ended_at IS NULL
                    """,
                    (job.job_id, job.pet_id, job.attempt, now),
                ).fetchone()
                if lease is None:
                    raise CompanionJobLeaseLostError(
                        "job or relationship epoch is no longer active"
                    )
                rows = connection.execute(
                    f"""
                    SELECT evidence_id, kind, content_json, attribution,
                           speaker_identity, ownership_scope,
                           relationship_epoch_id, status, expires_at,
                           occurred_at
                    FROM companion_evidence
                    WHERE evidence_id IN ({placeholders})
                      AND pet_id = ?
                      AND memory_subject_id = ?
                      AND ownership_scope = 'relationship'
                      AND relationship_epoch_id = ?
                      AND status = 'active'
                      AND (expires_at IS NULL OR julianday(expires_at) > julianday(?))
                    ORDER BY occurred_at, evidence_id
                    """,
                    (
                        *unique_evidence_ids,
                        job.pet_id,
                        memory_subject_id,
                        job.relationship_epoch_id,
                        now,
                    ),
                ).fetchall()
                if len(rows) != len(unique_evidence_ids):
                    raise ReflectionValidationError(
                        "session capsule Evidence is no longer active in this epoch"
                    )
                rows_by_id = {row["evidence_id"]: row for row in rows}
                outcome = rows[-1]["kind"]
                for row in rows:
                    content = json.loads(row["content_json"])
                    candidate = (
                        content.get("outcome") if isinstance(content, dict) else None
                    )
                    if isinstance(candidate, str) and candidate.strip():
                        outcome = candidate
                capsule_id = str(
                    uuid5(NAMESPACE_URL, f"xiaoxin:session-capsule:{job.job_id}")
                )
                expires_at = (
                    datetime.fromisoformat(now) + timedelta(days=90)
                ).isoformat()
                adjustment_dimensions = sorted(
                    {adjustment.dimension for adjustment in proposal.adjustments}
                )
                connection.execute(
                    """
                    INSERT INTO session_capsules(
                        capsule_id,
                        pet_id,
                        relationship_epoch_id,
                        safe_summary,
                        interaction_outcome,
                        adjustment_signals_json,
                        status,
                        created_at,
                        expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        capsule_id,
                        job.pet_id,
                        job.relationship_epoch_id,
                        proposal.safe_summary,
                        outcome,
                        _stable_json(adjustment_dimensions),
                        now,
                        expires_at,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO capsule_evidence(capsule_id, evidence_id, pet_id)
                    VALUES (?, ?, ?)
                    """,
                    (
                        (capsule_id, evidence_id, job.pet_id)
                        for evidence_id in unique_evidence_ids
                    ),
                )
                for index, adjustment in enumerate(proposal.adjustments):
                    value_json = _stable_json({"value": adjustment.value})
                    decisions = tuple(
                        (
                            evidence_id,
                            _adjustment_evidence_decision(
                                rows_by_id[evidence_id],
                                dimension=adjustment.dimension,
                                value=adjustment.value,
                                scope=adjustment.scope,
                                current_epoch_id=job.relationship_epoch_id,
                                now=now,
                            ),
                        )
                        for evidence_id in tuple(dict.fromkeys(adjustment.evidence_ids))
                    )
                    structured_keys = {
                        decision.structured_key
                        for _, decision in decisions
                        if decision.qualification in {"eligible", "clue_only"}
                        and decision.structured_key is not None
                    }
                    if len(structured_keys) != 1:
                        continue
                    behavior_key, context_scope, direction = next(iter(structured_keys))
                    existing = connection.execute(
                        """
                        SELECT adjustment_id, confidence, value_json
                        FROM companion_adjustments AS adjustment
                        WHERE adjustment.pet_id = ?
                          AND adjustment.relationship_epoch_id = ?
                          AND adjustment.behavior_key = ?
                          AND adjustment.context_scope = ?
                          AND adjustment.direction = ?
                          AND adjustment.status IN ('candidate', 'trial', 'active')
                          AND (
                            adjustment.valid_until IS NULL
                            OR julianday(adjustment.valid_until) > julianday(?)
                          )
                          AND EXISTS (
                            SELECT 1
                            FROM adjustment_evidence AS link
                            JOIN companion_evidence AS evidence
                              ON evidence.evidence_id = link.evidence_id
                             AND evidence.pet_id = link.pet_id
                            WHERE link.adjustment_id = adjustment.adjustment_id
                              AND evidence.memory_subject_id = ?
                          )
                          AND NOT EXISTS (
                            SELECT 1
                            FROM adjustment_evidence AS link
                            JOIN companion_evidence AS evidence
                              ON evidence.evidence_id = link.evidence_id
                             AND evidence.pet_id = link.pet_id
                            WHERE link.adjustment_id = adjustment.adjustment_id
                              AND evidence.memory_subject_id <> ?
                          )
                        ORDER BY
                          CASE status
                            WHEN 'active' THEN 0
                            WHEN 'trial' THEN 1
                            ELSE 2
                          END,
                          created_at,
                          adjustment_id
                        LIMIT 1
                        """,
                        (
                            job.pet_id,
                            job.relationship_epoch_id,
                            behavior_key,
                            context_scope,
                            direction,
                            now,
                            memory_subject_id,
                            memory_subject_id,
                        ),
                    ).fetchone()
                    if existing is None:
                        adjustment_id = str(
                            uuid5(
                                NAMESPACE_URL,
                                "xiaoxin:companion-adjustment:"
                                f"{job.job_id}:{index}:{adjustment.dimension}:"
                                f"{adjustment.scope}",
                            )
                        )
                        connection.execute(
                            """
                            INSERT INTO companion_adjustments(
                                adjustment_id,
                                pet_id,
                                relationship_epoch_id,
                                dimension,
                                value_json,
                                scope,
                                behavior_key,
                                context_scope,
                                direction,
                                status,
                                confidence,
                                generated_by,
                                created_at,
                                valid_until
                            ) VALUES (
                                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                'candidate', ?, ?, ?, ?
                            )
                            """,
                            (
                                adjustment_id,
                                job.pet_id,
                                job.relationship_epoch_id,
                                adjustment.dimension,
                                value_json,
                                adjustment.scope,
                                behavior_key,
                                context_scope,
                                direction,
                                adjustment.confidence,
                                model,
                                now,
                                (
                                    datetime.fromisoformat(now) + timedelta(days=30)
                                ).isoformat(),
                            ),
                        )
                        confidence = adjustment.confidence
                    else:
                        adjustment_id = existing["adjustment_id"]
                        value_json = existing["value_json"]
                        confidence = max(
                            float(existing["confidence"]),
                            adjustment.confidence,
                        )
                    connection.executemany(
                        """
                        INSERT OR IGNORE INTO adjustment_evidence(
                            adjustment_id, evidence_id, pet_id
                        ) VALUES (?, ?, ?)
                        """,
                        (
                            (adjustment_id, evidence_id, job.pet_id)
                            for evidence_id in tuple(
                                dict.fromkeys(adjustment.evidence_ids)
                            )
                        ),
                    )
                    added_date_vote = False
                    for evidence_id, decision in decisions:
                        contributes_date = 0
                        if decision.qualification == "eligible":
                            existing_vote = connection.execute(
                                """
                                SELECT 1
                                FROM adjustment_evidence_qualification
                                WHERE adjustment_id = ?
                                  AND qualifying_local_date = ?
                                  AND contributes_date = 1
                                """,
                                (
                                    adjustment_id,
                                    decision.qualifying_local_date,
                                ),
                            ).fetchone()
                            if existing_vote is None:
                                contributes_date = 1
                        inserted = connection.execute(
                            """
                            INSERT OR IGNORE INTO adjustment_evidence_qualification(
                                adjustment_id,
                                evidence_id,
                                pet_id,
                                qualification,
                                reason_code,
                                qualifying_local_date,
                                contributes_date,
                                evaluated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                adjustment_id,
                                evidence_id,
                                job.pet_id,
                                decision.qualification,
                                decision.reason_code,
                                decision.qualifying_local_date,
                                contributes_date,
                                now,
                            ),
                        )
                        added_date_vote = added_date_vote or (
                            inserted.rowcount == 1 and contributes_date == 1
                        )
                    distinct_days = connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM adjustment_evidence_qualification
                        WHERE adjustment_id = ?
                          AND qualification = 'eligible'
                          AND contributes_date = 1
                        """,
                        (adjustment_id,),
                    ).fetchone()[0]
                    current_state = connection.execute(
                        """
                        SELECT status, valid_until
                        FROM companion_adjustments
                        WHERE adjustment_id = ?
                        """,
                        (adjustment_id,),
                    ).fetchone()
                    status = current_state["status"]
                    valid_until = current_state["valid_until"]
                    if int(distinct_days) >= 3:
                        status = "active"
                        valid_until = None
                    elif added_date_vote and int(distinct_days) >= 2:
                        status = "trial"
                        valid_until = (
                            datetime.fromisoformat(now) + timedelta(days=60)
                        ).isoformat()
                    elif added_date_vote:
                        status = "candidate"
                        valid_until = (
                            datetime.fromisoformat(now) + timedelta(days=30)
                        ).isoformat()
                    if status == "active":
                        connection.execute(
                            """
                            UPDATE companion_adjustments
                            SET status = 'superseded'
                            WHERE pet_id = ?
                              AND relationship_epoch_id = ?
                              AND behavior_key = ?
                              AND context_scope = ?
                              AND adjustment_id <> ?
                              AND status IN ('candidate', 'trial', 'active')
                            """,
                            (
                                job.pet_id,
                                job.relationship_epoch_id,
                                behavior_key,
                                context_scope,
                                adjustment_id,
                            ),
                        )
                    connection.execute(
                        """
                        UPDATE companion_adjustments
                        SET status = ?, confidence = ?, generated_by = ?,
                            valid_until = ?
                        WHERE adjustment_id = ?
                        """,
                        (
                            status,
                            confidence,
                            model,
                            valid_until,
                            adjustment_id,
                        ),
                    )
                updated = connection.execute(
                    """
                    UPDATE consolidation_jobs
                    SET status = 'succeeded', model = ?, prompt_version = ?,
                        lease_until = NULL, next_attempt_at = NULL,
                        failure_reason = NULL, updated_at = ?
                    WHERE job_id = ? AND status = 'running' AND attempt = ?
                    """,
                    (model, prompt_version, now, job.job_id, job.attempt),
                )
                if updated.rowcount != 1:
                    raise CompanionJobLeaseLostError(
                        "job lease is no longer owned by this attempt"
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def apply_chapter_proposal(
        self,
        *,
        job: ClaimedCompanionJob,
        proposal: ReflectionProposal,
        evidence_ids: tuple[str, ...],
        now: str,
        model: str,
        prompt_version: str | None,
    ) -> None:
        if job.job_kind not in {"academic_stage_changed", "narrative_boundary"}:
            raise ReflectionValidationError("job is not a narrative boundary")
        stage = job.payload.get("chapter_stage", job.payload.get("from_stage"))
        if not isinstance(stage, str):
            raise ReflectionValidationError("chapter academic stage is invalid")
        period_start = job.payload.get("period_start", now)
        period_end = job.payload.get("period_end", now)
        boundary_id = job.payload.get("boundary_id")
        if not isinstance(period_start, str) or not isinstance(period_end, str):
            raise ReflectionValidationError("chapter period is invalid")
        unique_evidence_ids = tuple(dict.fromkeys(evidence_ids))
        if not 2 <= len(unique_evidence_ids) <= 3 or job.relationship_epoch_id is None:
            raise ReflectionValidationError("chapter Evidence is insufficient")
        placeholders = ",".join("?" for _ in unique_evidence_ids)
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                memory_subject_id = _job_memory_subject_id(connection, job)
                lease = connection.execute(
                    """
                    SELECT 1
                    FROM consolidation_jobs AS job
                    JOIN relationship_epochs AS epoch
                      ON epoch.epoch_id = job.relationship_epoch_id
                     AND epoch.pet_id = job.pet_id
                    WHERE job.job_id = ?
                      AND job.pet_id = ?
                      AND job.status = 'running'
                      AND job.attempt = ?
                      AND job.lease_until IS NOT NULL
                      AND julianday(job.lease_until) > julianday(?)
                      AND epoch.ended_at IS NULL
                    """,
                    (job.job_id, job.pet_id, job.attempt, now),
                ).fetchone()
                if lease is None:
                    raise CompanionJobLeaseLostError(
                        "job lease or relationship epoch is no longer active"
                    )
                rows = connection.execute(
                    f"""
                    SELECT evidence_id, ownership_scope, relationship_epoch_id,
                           source_summary, occurred_at
                    FROM companion_evidence
                    WHERE evidence_id IN ({placeholders})
                      AND pet_id = ?
                      AND memory_subject_id = ?
                      AND status = 'active'
                      AND (expires_at IS NULL OR julianday(expires_at) > julianday(?))
                      AND (
                        ownership_scope = 'user'
                        OR relationship_epoch_id = ?
                      )
                    """,
                    (
                        *unique_evidence_ids,
                        job.pet_id,
                        memory_subject_id,
                        now,
                        job.relationship_epoch_id,
                    ),
                ).fetchall()
                if len(rows) != len(unique_evidence_ids) or not any(
                    row["ownership_scope"] == "relationship" for row in rows
                ):
                    raise ReflectionValidationError(
                        "chapter Evidence is unavailable or lacks shared experience"
                    )
                local_dates = {
                    datetime.fromisoformat(row["occurred_at"])
                    .astimezone(_SHANGHAI_TIMEZONE)
                    .date()
                    for row in rows
                }
                if len(local_dates) < 2:
                    raise ReflectionValidationError(
                        "chapter Evidence must span two local dates"
                    )
                evidence_by_id = {row["evidence_id"]: row for row in rows}
                statement_evidence_ids: set[str] = set()
                narrative_parts: list[str] = []
                has_shared_statement = False
                for statement in proposal.chapter_statements:
                    expected_ownership = (
                        "user"
                        if statement.claim_scope == "user_fact"
                        else (
                            "relationship"
                            if statement.claim_scope == "shared_experience"
                            else None
                        )
                    )
                    if expected_ownership is None or not statement.evidence_ids:
                        raise ReflectionValidationError(
                            "chapter statement claim scope is invalid"
                        )
                    if statement_evidence_ids.intersection(statement.evidence_ids):
                        raise ReflectionValidationError(
                            "chapter Evidence cannot be reused across statements"
                        )
                    statement_rows = []
                    for evidence_id in statement.evidence_ids:
                        row = evidence_by_id.get(evidence_id)
                        if row is None or row["ownership_scope"] != expected_ownership:
                            raise ReflectionValidationError(
                                "chapter statement ownership does not match Evidence"
                            )
                        statement_rows.append(row)
                    statement_evidence_ids.update(statement.evidence_ids)
                    label = (
                        "用户自己的事实"
                        if statement.claim_scope == "user_fact"
                        else "共同经历"
                    )
                    has_shared_statement = has_shared_statement or (
                        statement.claim_scope == "shared_experience"
                    )
                    narrative_parts.append(
                        f"{label}："
                        + "；".join(row["source_summary"] for row in statement_rows)
                    )
                if not has_shared_statement or statement_evidence_ids != set(
                    unique_evidence_ids
                ):
                    raise ReflectionValidationError(
                        "chapter statements must account for cited Evidence"
                    )
                safe_narrative = "\n".join(narrative_parts)
                version = int(
                    connection.execute(
                        """
                        SELECT COALESCE(MAX(version), 0) + 1
                        FROM companion_chapters AS chapter
                        WHERE chapter.pet_id = ?
                          AND chapter.relationship_epoch_id = ?
                          AND chapter.academic_stage = ?
                          AND EXISTS (
                            SELECT 1
                            FROM chapter_evidence AS link
                            JOIN companion_evidence AS evidence
                              ON evidence.evidence_id = link.evidence_id
                             AND evidence.pet_id = link.pet_id
                            WHERE link.chapter_id = chapter.chapter_id
                              AND evidence.memory_subject_id = ?
                          )
                          AND NOT EXISTS (
                            SELECT 1
                            FROM chapter_evidence AS link
                            JOIN companion_evidence AS evidence
                              ON evidence.evidence_id = link.evidence_id
                             AND evidence.pet_id = link.pet_id
                            WHERE link.chapter_id = chapter.chapter_id
                              AND evidence.memory_subject_id <> ?
                          )
                        """,
                        (
                            job.pet_id,
                            job.relationship_epoch_id,
                            stage,
                            memory_subject_id,
                            memory_subject_id,
                        ),
                    ).fetchone()[0]
                )
                connection.execute(
                    """
                    UPDATE companion_chapters AS chapter
                    SET status = 'superseded', period_end = ?
                    WHERE chapter.pet_id = ?
                      AND chapter.relationship_epoch_id = ?
                      AND chapter.academic_stage = ?
                      AND chapter.status = 'active'
                      AND EXISTS (
                        SELECT 1
                        FROM chapter_evidence AS link
                        JOIN companion_evidence AS evidence
                          ON evidence.evidence_id = link.evidence_id
                         AND evidence.pet_id = link.pet_id
                        WHERE link.chapter_id = chapter.chapter_id
                          AND evidence.memory_subject_id = ?
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM chapter_evidence AS link
                        JOIN companion_evidence AS evidence
                          ON evidence.evidence_id = link.evidence_id
                         AND evidence.pet_id = link.pet_id
                        WHERE link.chapter_id = chapter.chapter_id
                          AND evidence.memory_subject_id <> ?
                      )
                    """,
                    (
                        period_end,
                        job.pet_id,
                        job.relationship_epoch_id,
                        stage,
                        memory_subject_id,
                        memory_subject_id,
                    ),
                )
                chapter_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"xiaoxin:companion-chapter:{job.pet_id}:"
                        f"{memory_subject_id}:{job.relationship_epoch_id}:"
                        f"{stage}:{version}",
                    )
                )
                connection.execute(
                    """
                    INSERT INTO companion_chapters(
                        chapter_id, pet_id, relationship_epoch_id,
                        academic_stage, xiaoxin_age, period_start, period_end,
                        safe_narrative, status, version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        chapter_id,
                        job.pet_id,
                        job.relationship_epoch_id,
                        stage,
                        xiaoxin_age_for_stage(stage),
                        period_start,
                        period_end,
                        safe_narrative,
                        version,
                        now,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO chapter_evidence(chapter_id, evidence_id, pet_id)
                    VALUES (?, ?, ?)
                    """,
                    (
                        (chapter_id, evidence_id, job.pet_id)
                        for evidence_id in unique_evidence_ids
                    ),
                )
                updated = connection.execute(
                    """
                    UPDATE consolidation_jobs
                    SET status = 'succeeded', model = ?, prompt_version = ?,
                        lease_until = NULL, next_attempt_at = NULL,
                        failure_reason = NULL, updated_at = ?
                    WHERE job_id = ? AND status = 'running' AND attempt = ?
                    """,
                    (model, prompt_version, now, job.job_id, job.attempt),
                )
                if isinstance(boundary_id, str) and boundary_id:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO companion_chapter_boundaries(
                            chapter_id, boundary_id
                        ) VALUES (?, ?)
                        """,
                        (chapter_id, boundary_id),
                    )
                    self._attach_chapter_to_growth_moment_in_connection(
                        connection,
                        boundary_id=boundary_id,
                        evidence_ids=unique_evidence_ids,
                        now=now,
                    )
                if updated.rowcount != 1:
                    raise CompanionJobLeaseLostError(
                        "job lease is no longer owned by this attempt"
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def apply_memory_candidate_proposal(
        self,
        *,
        job: ClaimedCompanionJob,
        proposal: ReflectionProposal,
        now: str,
        model: str,
        prompt_version: str | None,
    ) -> None:
        if job.job_kind != "memory_candidate_extraction":
            raise ReflectionValidationError("job is not a memory candidate extraction")
        turn_id = job.payload.get("turn_id")
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise ReflectionValidationError("candidate job turn_id is invalid")
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                memory_subject_id = _job_memory_subject_id(connection, job)
                source = connection.execute(
                    """
                    SELECT source_text, occurred_at
                    FROM companion_turn_sources
                    WHERE turn_id = ? AND pet_id = ?
                      AND memory_subject_id = ?
                      AND julianday(expires_at) > julianday(?)
                    """,
                    (turn_id, job.pet_id, memory_subject_id, now),
                ).fetchone()
                lease = connection.execute(
                    """
                    SELECT 1
                    FROM consolidation_jobs AS job
                    LEFT JOIN relationship_epochs AS epoch
                      ON epoch.epoch_id = job.relationship_epoch_id
                     AND epoch.pet_id = job.pet_id
                    WHERE job.job_id = ? AND job.pet_id = ?
                      AND job.status = 'running' AND job.attempt = ?
                      AND job.lease_until IS NOT NULL
                      AND julianday(job.lease_until) > julianday(?)
                      AND (
                        job.relationship_epoch_id IS NULL
                        OR epoch.ended_at IS NULL
                      )
                    """,
                    (job.job_id, job.pet_id, job.attempt, now),
                ).fetchone()
                if source is None or lease is None:
                    raise CompanionJobLeaseLostError(
                        "candidate source or job lease is no longer active"
                    )
                validate_reflection_proposal(
                    ReflectionRequest(
                        job_id=job.job_id,
                        job_kind=job.job_kind,
                        pet_id=job.pet_id,
                        relationship_epoch_id=job.relationship_epoch_id,
                        evidence=(),
                        turn_sources=(
                            ReflectionTurnSource(
                                turn_id=turn_id,
                                text=source["source_text"],
                                occurred_at=source["occurred_at"],
                            ),
                        ),
                    ),
                    proposal,
                )
                for index, candidate in enumerate(proposal.proposed_user_facts):
                    source_turn_id = str(candidate["source_turn_id"])
                    source_quote = str(candidate["source_quote"])
                    value = str(candidate["value"])
                    if (
                        source_turn_id != turn_id
                        or source_quote not in source["source_text"]
                        or value.strip() not in source_quote
                    ):
                        raise ReflectionValidationError(
                            "candidate source changed before persistence"
                        )
                    fact_key = str(candidate["fact_key"])
                    claim_type = str(candidate["claim_type"])
                    evidence_id = str(
                        uuid5(
                            NAMESPACE_URL,
                            "xiaoxin:memory-candidate:"
                            f"{job.job_id}:{index}:{fact_key}",
                        )
                    )
                    expires_at = (
                        datetime.fromisoformat(now) + timedelta(days=30)
                    ).isoformat()
                    connection.execute(
                        """
                        INSERT INTO companion_evidence(
                            evidence_id, pet_id, memory_subject_id,
                            ownership_scope, relationship_epoch_id, kind,
                            content_json, fact_key, importance, sensitivity,
                            valid_from, valid_until, source_kind, source_ref,
                            source_summary, attribution, confidence, occurred_at,
                            retention, status, prompt_eligible, expires_at,
                            created_at
                        ) VALUES (
                            ?, ?, ?, 'user', NULL, ?, ?, ?, 0.6, ?, ?, NULL,
                            'conversation_candidate', ?, ?, ?, ?, ?,
                            'until_confirmed', 'candidate', 0, ?, ?
                        )
                        """,
                        (
                            evidence_id,
                            job.pet_id,
                            memory_subject_id,
                            str(candidate["kind"]),
                            _stable_json(
                                {
                                    "value": value,
                                    "source_quote": source_quote,
                                    "source_quote_digest": hashlib.sha256(
                                        source_quote.encode("utf-8")
                                    ).hexdigest(),
                                    "source_turn_id": source_turn_id,
                                    "claim_type": claim_type,
                                    "fact_key": fact_key,
                                }
                            ),
                            fact_key,
                            str(candidate["sensitivity"]),
                            source["occurred_at"],
                            turn_id,
                            f"候选记忆：{value}",
                            f"candidate_{claim_type}",
                            float(candidate["confidence"]),
                            source["occurred_at"],
                            expires_at,
                            now,
                        ),
                    )
                updated = connection.execute(
                    """
                    UPDATE consolidation_jobs
                    SET status = 'succeeded', model = ?, prompt_version = ?,
                        lease_until = NULL, next_attempt_at = NULL,
                        failure_reason = NULL, updated_at = ?
                    WHERE job_id = ? AND status = 'running' AND attempt = ?
                    """,
                    (model, prompt_version, now, job.job_id, job.attempt),
                )
                if updated.rowcount != 1:
                    raise CompanionJobLeaseLostError(
                        "job lease is no longer owned by this attempt"
                    )
                connection.execute(
                    "DELETE FROM companion_turn_sources WHERE turn_id = ? AND pet_id = ?",
                    (turn_id, job.pet_id),
                )
                connection.execute(
                    "DELETE FROM companion_context_job_pins WHERE job_id = ?",
                    (job.job_id,),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def mark_job_succeeded(
        self,
        *,
        job: ClaimedCompanionJob,
        now: str,
        model: str,
        prompt_version: str | None,
    ) -> None:
        self._update_running_job(
            job=job,
            now=now,
            status="succeeded",
            model=model,
            prompt_version=prompt_version,
            next_attempt_at=None,
            failure_reason=None,
        )

    def recompute_adjustments_after_evidence_change(
        self,
        *,
        job: ClaimedCompanionJob,
        now: str,
    ) -> None:
        """Rebuild revoked adjustment lineage without reviving its audit row."""
        payload = job.payload
        memory_subject_id = payload.get("memory_subject_id")
        raw_evidence_ids = payload.get("evidence_ids")
        if not isinstance(raw_evidence_ids, list):
            raw_evidence_ids = [
                evidence_id
                for evidence_id in (
                    payload.get("evidence_id"),
                    payload.get("old_evidence_id"),
                )
                if isinstance(evidence_id, str)
            ]
        evidence_ids = tuple(
            dict.fromkeys(
                evidence_id
                for evidence_id in raw_evidence_ids
                if isinstance(evidence_id, str) and evidence_id
            )
        )
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                active_epoch = (
                    job.relationship_epoch_id is not None
                    and connection.execute(
                        """
                        SELECT 1
                        FROM relationship_epochs
                        WHERE epoch_id = ? AND pet_id = ? AND ended_at IS NULL
                        """,
                        (job.relationship_epoch_id, job.pet_id),
                    ).fetchone()
                    is not None
                )
                if active_epoch and isinstance(memory_subject_id, str) and evidence_ids:
                    placeholders = ",".join("?" for _ in evidence_ids)
                    adjustments = connection.execute(
                        f"""
                        SELECT DISTINCT adjustment.*
                        FROM companion_adjustments AS adjustment
                        JOIN adjustment_evidence_qualification AS qualification
                          ON qualification.adjustment_id = adjustment.adjustment_id
                        JOIN companion_evidence AS evidence
                          ON evidence.evidence_id = qualification.evidence_id
                         AND evidence.pet_id = qualification.pet_id
                        WHERE adjustment.pet_id = ?
                          AND adjustment.relationship_epoch_id = ?
                          AND adjustment.status = 'revoked'
                          AND adjustment.behavior_key IS NOT NULL
                          AND adjustment.context_scope IS NOT NULL
                          AND adjustment.direction IS NOT NULL
                          AND qualification.evidence_id IN ({placeholders})
                          AND evidence.memory_subject_id = ?
                        ORDER BY adjustment.created_at, adjustment.adjustment_id
                        """,
                        (
                            job.pet_id,
                            job.relationship_epoch_id,
                            *evidence_ids,
                            memory_subject_id,
                        ),
                    ).fetchall()
                    for adjustment in adjustments:
                        lineage = connection.execute(
                            """
                            SELECT
                                qualification.evidence_id,
                                qualification.qualification,
                                qualification.reason_code,
                                qualification.qualifying_local_date,
                                evidence.occurred_at
                            FROM adjustment_evidence_qualification AS qualification
                            JOIN companion_evidence AS evidence
                              ON evidence.evidence_id = qualification.evidence_id
                             AND evidence.pet_id = qualification.pet_id
                            WHERE qualification.adjustment_id = ?
                              AND evidence.pet_id = ?
                              AND evidence.memory_subject_id = ?
                              AND evidence.status = 'active'
                              AND (
                                evidence.ownership_scope = 'user'
                                OR evidence.relationship_epoch_id = ?
                              )
                              AND (
                                evidence.expires_at IS NULL
                                OR julianday(evidence.expires_at) > julianday(?)
                              )
                              AND (
                                evidence.valid_from IS NULL
                                OR julianday(evidence.valid_from) <= julianday(?)
                              )
                              AND (
                                evidence.valid_until IS NULL
                                OR julianday(evidence.valid_until) > julianday(?)
                              )
                            ORDER BY
                                qualification.qualifying_local_date,
                                evidence.occurred_at,
                                qualification.evidence_id
                            """,
                            (
                                adjustment["adjustment_id"],
                                job.pet_id,
                                memory_subject_id,
                                job.relationship_epoch_id,
                                now,
                                now,
                                now,
                            ),
                        ).fetchall()
                        eligible_dates: set[str] = set()
                        rebuilt_lineage: list[tuple[sqlite3.Row, int]] = []
                        for item in lineage:
                            contributes_date = 0
                            local_date = item["qualifying_local_date"]
                            if (
                                item["qualification"] == "eligible"
                                and isinstance(local_date, str)
                                and local_date not in eligible_dates
                            ):
                                eligible_dates.add(local_date)
                                contributes_date = 1
                            rebuilt_lineage.append((item, contributes_date))
                        if not eligible_dates:
                            continue
                        distinct_days = len(eligible_dates)
                        if distinct_days >= 3:
                            status = "active"
                            valid_until = None
                        elif distinct_days == 2:
                            status = "trial"
                            valid_until = (
                                datetime.fromisoformat(now) + timedelta(days=60)
                            ).isoformat()
                        else:
                            status = "candidate"
                            valid_until = (
                                datetime.fromisoformat(now) + timedelta(days=30)
                            ).isoformat()
                        adjustment_id = str(
                            uuid5(
                                NAMESPACE_URL,
                                "xiaoxin:recomputed-adjustment:"
                                f"{job.job_id}:{adjustment['adjustment_id']}",
                            )
                        )
                        if status == "active":
                            connection.execute(
                                """
                                UPDATE companion_adjustments
                                SET status = 'superseded'
                                WHERE pet_id = ?
                                  AND relationship_epoch_id = ?
                                  AND behavior_key = ?
                                  AND context_scope = ?
                                  AND status IN ('candidate', 'trial', 'active')
                                """,
                                (
                                    job.pet_id,
                                    job.relationship_epoch_id,
                                    adjustment["behavior_key"],
                                    adjustment["context_scope"],
                                ),
                            )
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO companion_adjustments(
                                adjustment_id, pet_id, relationship_epoch_id,
                                dimension, value_json, scope, behavior_key,
                                context_scope, direction, status, confidence,
                                generated_by, created_at, valid_until
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                adjustment_id,
                                job.pet_id,
                                job.relationship_epoch_id,
                                adjustment["dimension"],
                                adjustment["value_json"],
                                adjustment["scope"],
                                adjustment["behavior_key"],
                                adjustment["context_scope"],
                                adjustment["direction"],
                                status,
                                adjustment["confidence"],
                                "deterministic-derived-recompute",
                                now,
                                valid_until,
                            ),
                        )
                        connection.executemany(
                            """
                            INSERT OR IGNORE INTO adjustment_evidence(
                                adjustment_id, evidence_id, pet_id
                            ) VALUES (?, ?, ?)
                            """,
                            (
                                (adjustment_id, item["evidence_id"], job.pet_id)
                                for item, _ in rebuilt_lineage
                            ),
                        )
                        connection.executemany(
                            """
                            INSERT OR IGNORE INTO adjustment_evidence_qualification(
                                adjustment_id, evidence_id, pet_id, qualification,
                                reason_code, qualifying_local_date,
                                contributes_date, evaluated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                (
                                    adjustment_id,
                                    item["evidence_id"],
                                    job.pet_id,
                                    item["qualification"],
                                    item["reason_code"],
                                    item["qualifying_local_date"],
                                    contributes_date,
                                    now,
                                )
                                for item, contributes_date in rebuilt_lineage
                            ),
                        )
                updated = connection.execute(
                    """
                    UPDATE consolidation_jobs
                    SET status = 'succeeded', model = ?, prompt_version = ?,
                        lease_until = NULL, next_attempt_at = NULL,
                        failure_reason = NULL, updated_at = ?
                    WHERE job_id = ? AND status = 'running' AND attempt = ?
                    """,
                    (
                        "deterministic-derived-recompute",
                        REFLECTION_REQUEST_VERSION,
                        now,
                        job.job_id,
                        job.attempt,
                    ),
                )
                if updated.rowcount != 1:
                    raise CompanionJobLeaseLostError(
                        "job lease is no longer owned by this attempt"
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def mark_job_retry(
        self,
        *,
        job: ClaimedCompanionJob,
        now: str,
        next_attempt_at: str,
        reason: str,
    ) -> None:
        self._update_running_job(
            job=job,
            now=now,
            status="retry",
            model=None,
            prompt_version=None,
            next_attempt_at=next_attempt_at,
            failure_reason=reason,
        )

    def mark_job_failed(
        self,
        *,
        job: ClaimedCompanionJob,
        now: str,
        reason: str,
    ) -> None:
        self._update_running_job(
            job=job,
            now=now,
            status="failed",
            model=None,
            prompt_version=None,
            next_attempt_at=None,
            failure_reason=reason,
        )

    def _update_running_job(
        self,
        *,
        job: ClaimedCompanionJob,
        now: str,
        status: str,
        model: str | None,
        prompt_version: str | None,
        next_attempt_at: str | None,
        failure_reason: str | None,
    ) -> None:
        with self.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE consolidation_jobs
                SET status = ?, lease_until = NULL, next_attempt_at = ?,
                    model = COALESCE(?, model),
                    prompt_version = COALESCE(?, prompt_version),
                    failure_reason = ?, updated_at = ?
                WHERE job_id = ? AND status = 'running' AND attempt = ?
                  AND lease_until IS NOT NULL
                  AND julianday(lease_until) > julianday(?)
                """,
                (
                    status,
                    next_attempt_at,
                    model,
                    prompt_version,
                    failure_reason,
                    now,
                    job.job_id,
                    job.attempt,
                    now,
                ),
            )
            if cursor.rowcount != 1:
                raise CompanionJobLeaseLostError("job lease is no longer owned")
            if (
                status in {"succeeded", "failed"}
                and job.job_kind == "memory_candidate_extraction"
            ):
                turn_id = job.payload.get("turn_id")
                if isinstance(turn_id, str) and turn_id.strip():
                    connection.execute(
                        """
                        DELETE FROM companion_turn_sources
                        WHERE turn_id = ? AND pet_id = ?
                        """,
                        (turn_id, job.pet_id),
                    )
                connection.execute(
                    "DELETE FROM companion_context_job_pins WHERE job_id = ?",
                    (job.job_id,),
                )
            connection.commit()

    def get_active_epoch(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
    ) -> RelationshipEpoch | None:
        with self.connection() as connection:
            pet = connection.execute(
                "SELECT owner_user_id FROM companion_pets WHERE pet_id = ?",
                (pet_id,),
            ).fetchone()
            if pet is None:
                return None
            if pet["owner_user_id"] != owner_user_id:
                raise PermissionError("owner does not control this personal pet")
            row = connection.execute(
                """
                SELECT epoch_id, pet_id, started_at, ended_at,
                       start_reason, end_reason
                FROM relationship_epochs
                WHERE pet_id = ? AND ended_at IS NULL
                """,
                (pet_id,),
            ).fetchone()
        return _relationship_epoch_from_row(row) if row is not None else None

    def create_boot_checkin(
        self,
        *,
        boot_event_id: str,
        device_id: str,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        boot_reason: str,
        occurred_at: str,
        due_at: str,
        now: str,
    ) -> str | None:
        if not boot_event_id.strip() or not device_id.strip():
            raise ValueError("boot event identity is required")
        occurred = datetime.fromisoformat(occurred_at)
        due = datetime.fromisoformat(due_at)
        if due < occurred:
            raise ValueError("boot checkin due_at cannot precede occurred_at")
        opportunity_id = str(
            uuid5(NAMESPACE_URL, f"xiaoxin:boot-checkin:{boot_event_id}")
        )
        safe_brief = "你在附近吗？如果方便，和我说句话吧。"
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """
                    SELECT opportunity_id, status
                    FROM companion_device_boot_events
                    WHERE boot_event_id = ?
                    """,
                    (boot_event_id,),
                ).fetchone()
                if existing is not None:
                    connection.commit()
                    return (
                        str(existing["opportunity_id"])
                        if existing["opportunity_id"] is not None
                        else None
                    )
                connection.execute(
                    """
                    INSERT INTO companion_device_boot_events(
                        boot_event_id, device_id, owner_user_id, pet_id,
                        memory_subject_id, relationship_epoch_id,
                        boot_reason, occurred_at, due_at, status,
                        opportunity_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, ?, ?)
                    """,
                    (
                        boot_event_id,
                        device_id,
                        owner_user_id,
                        pet_id,
                        memory_subject_id,
                        relationship_epoch_id,
                        boot_reason,
                        occurred_at,
                        due_at,
                        opportunity_id,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO initiative_opportunities(
                        opportunity_id, owner_user_id, pet_id,
                        memory_subject_id, relationship_epoch_id,
                        opportunity_kind, reason_code, evidence_ids_json,
                        safe_brief, due_at, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'boot_checkin',
                              'device_booted', '[]', ?, ?, 'scheduled', ?, ?)
                    """,
                    (
                        opportunity_id,
                        owner_user_id,
                        pet_id,
                        memory_subject_id,
                        relationship_epoch_id,
                        safe_brief,
                        due_at,
                        now,
                        now,
                    ),
                )
                connection.commit()
                return opportunity_id
            except Exception:
                connection.rollback()
                raise

    def expire_boot_checkins(
        self,
        *,
        now: str,
        feedback_window_seconds: int,
        limit: int,
    ) -> int:
        cutoff = (
            datetime.fromisoformat(now)
            - timedelta(seconds=max(int(feedback_window_seconds), 1))
        ).isoformat()
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    """
                    SELECT event.boot_event_id, event.opportunity_id,
                           opportunity.decision_id
                    FROM companion_device_boot_events AS event
                    JOIN initiative_opportunities AS opportunity
                      ON opportunity.opportunity_id = event.opportunity_id
                     AND opportunity.opportunity_kind = 'boot_checkin'
                     AND opportunity.status = 'delivered'
                    JOIN initiative_decisions AS decision
                      ON decision.decision_id = opportunity.decision_id
                     AND decision.delivery_status = 'delivered'
                    WHERE event.status = 'delivered'
                      AND julianday(opportunity.updated_at) < julianday(?)
                    ORDER BY opportunity.updated_at, event.boot_event_id
                    LIMIT ?
                    """,
                    (cutoff, max(int(limit), 1)),
                ).fetchall()
                expired = 0
                for row in rows:
                    updated = connection.execute(
                        """
                        UPDATE initiative_opportunities
                        SET status = 'invalidated', outcome_code = 'unobserved',
                            lease_until = NULL, next_attempt_at = NULL,
                            delivery_id = NULL, updated_at = ?
                        WHERE opportunity_id = ? AND status = 'delivered'
                        """,
                        (now, row["opportunity_id"]),
                    )
                    connection.execute(
                        """
                        UPDATE initiative_decisions
                        SET delivery_status = 'unobserved'
                        WHERE decision_id = ? AND delivery_status = 'delivered'
                        """,
                        (row["decision_id"],),
                    )
                    connection.execute(
                        """
                        UPDATE companion_device_boot_events
                        SET status = 'unobserved', updated_at = ?
                        WHERE boot_event_id = ? AND status = 'delivered'
                        """,
                        (now, row["boot_event_id"]),
                    )
                    expired += int(updated.rowcount == 1)
                connection.commit()
                return expired
            except Exception:
                connection.rollback()
                raise

    def expire_stale_boot_checkins(
        self,
        *,
        now: str,
        delivery_window_seconds: int,
        limit: int,
    ) -> int:
        cutoff = (
            datetime.fromisoformat(now)
            - timedelta(seconds=max(int(delivery_window_seconds), 1))
        ).isoformat()
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    """
                    SELECT event.boot_event_id, event.opportunity_id,
                           opportunity.decision_id
                    FROM companion_device_boot_events AS event
                    JOIN initiative_opportunities AS opportunity
                      ON opportunity.opportunity_id = event.opportunity_id
                     AND opportunity.opportunity_kind = 'boot_checkin'
                     AND (
                         opportunity.status IN ('scheduled', 'deferred')
                         OR (
                             opportunity.status = 'claimed'
                             AND opportunity.lease_until IS NOT NULL
                             AND julianday(opportunity.lease_until) <= julianday(?)
                         )
                     )
                    WHERE event.status = 'scheduled'
                      AND julianday(event.occurred_at) < julianday(?)
                    ORDER BY event.occurred_at, event.boot_event_id
                    LIMIT ?
                    """,
                    (now, cutoff, max(int(limit), 1)),
                ).fetchall()
                expired = 0
                for row in rows:
                    updated = connection.execute(
                        """
                        UPDATE initiative_opportunities
                        SET status = 'invalidated',
                            outcome_code = 'boot_checkin_stale',
                            lease_until = NULL, next_attempt_at = NULL,
                            delivery_id = NULL, updated_at = ?
                        WHERE opportunity_id = ?
                          AND opportunity_kind = 'boot_checkin'
                          AND (
                              status IN ('scheduled', 'deferred')
                              OR (
                                  status = 'claimed'
                                  AND lease_until IS NOT NULL
                                  AND julianday(lease_until) <= julianday(?)
                              )
                          )
                        """,
                        (now, row["opportunity_id"], now),
                    )
                    if updated.rowcount != 1:
                        continue
                    connection.execute(
                        """
                        UPDATE initiative_decisions
                        SET delivery_status = 'invalidated'
                        WHERE decision_id = ?
                          AND delivery_status IN ('pending', 'composing', 'dispatching')
                        """,
                        (row["decision_id"],),
                    )
                    connection.execute(
                        """
                        UPDATE companion_device_boot_events
                        SET status = 'suppressed', updated_at = ?
                        WHERE boot_event_id = ? AND status = 'scheduled'
                        """,
                        (now, row["boot_event_id"]),
                    )
                    expired += 1
                connection.commit()
                return expired
            except Exception:
                connection.rollback()
                raise

    def has_active_presence_lease(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        now: str,
    ) -> bool:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM companion_presence_leases
                WHERE owner_user_id = ? AND pet_id = ?
                  AND memory_subject_id = ? AND relationship_epoch_id = ?
                  AND status = 'active'
                  AND julianday(expires_at) > julianday(?)
                """,
                (
                    owner_user_id,
                    pet_id,
                    memory_subject_id,
                    relationship_epoch_id,
                    now,
                ),
            ).fetchone()
        return row is not None

    @staticmethod
    def _open_presence_lease_in_connection(
        connection: sqlite3.Connection,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        device_id: str | None,
        source: str,
        source_ref: str,
        opened_at: str,
        expires_at: str,
        updated_at: str,
    ) -> None:
        existing = connection.execute(
            """
            SELECT expires_at
            FROM companion_presence_leases
            WHERE owner_user_id = ? AND pet_id = ?
              AND memory_subject_id = ? AND relationship_epoch_id = ?
            """,
            (owner_user_id, pet_id, memory_subject_id, relationship_epoch_id),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO companion_presence_leases(
                    owner_user_id, pet_id, memory_subject_id,
                    relationship_epoch_id, device_id, source, source_ref,
                    opened_at, expires_at, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    owner_user_id,
                    pet_id,
                    memory_subject_id,
                    relationship_epoch_id,
                    device_id,
                    source,
                    source_ref,
                    opened_at,
                    expires_at,
                    updated_at,
                    updated_at,
                ),
            )
            return
        effective_expires_at = max(
            datetime.fromisoformat(str(existing["expires_at"])),
            datetime.fromisoformat(expires_at),
        ).isoformat()
        connection.execute(
            """
            UPDATE companion_presence_leases
            SET device_id = COALESCE(?, device_id), source = ?, source_ref = ?,
                opened_at = ?, expires_at = ?, status = 'active', updated_at = ?
            WHERE owner_user_id = ? AND pet_id = ?
              AND memory_subject_id = ? AND relationship_epoch_id = ?
            """,
            (
                device_id,
                source,
                source_ref,
                opened_at,
                effective_expires_at,
                updated_at,
                owner_user_id,
                pet_id,
                memory_subject_id,
                relationship_epoch_id,
            ),
        )

    @staticmethod
    def _record_boot_checkin_response_in_connection(
        connection: sqlite3.Connection,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        interaction_at: str,
        feedback_window_seconds: int,
        presence_window_seconds: int,
        created_at: str,
    ) -> bool:
        row = connection.execute(
            """
            SELECT event.boot_event_id, event.device_id,
                   opportunity.opportunity_id, opportunity.decision_id,
                   opportunity.updated_at
            FROM companion_device_boot_events AS event
            JOIN initiative_opportunities AS opportunity
              ON opportunity.opportunity_id = event.opportunity_id
             AND opportunity.opportunity_kind = 'boot_checkin'
             AND opportunity.status = 'delivered'
            JOIN initiative_decisions AS decision
              ON decision.decision_id = opportunity.decision_id
             AND decision.delivery_status = 'delivered'
            WHERE event.owner_user_id = ? AND event.pet_id = ?
              AND event.memory_subject_id = ?
              AND event.relationship_epoch_id = ?
              AND event.status = 'delivered'
            ORDER BY opportunity.updated_at DESC, event.boot_event_id DESC
            LIMIT 1
            """,
            (
                owner_user_id,
                pet_id,
                memory_subject_id,
                relationship_epoch_id,
            ),
        ).fetchone()
        if row is None:
            return False
        delivered_at = datetime.fromisoformat(str(row["updated_at"]))
        interaction = datetime.fromisoformat(interaction_at)
        deadline = delivered_at + timedelta(
            seconds=max(int(feedback_window_seconds), 1)
        )
        if not delivered_at <= interaction <= deadline:
            return False
        connection.execute(
            """
            UPDATE initiative_opportunities
            SET status = 'invalidated', outcome_code = 'connection_responded',
                updated_at = ?
            WHERE opportunity_id = ? AND status = 'delivered'
            """,
            (interaction_at, row["opportunity_id"]),
        )
        connection.execute(
            """
            UPDATE initiative_decisions
            SET delivery_status = 'connection_responded'
            WHERE decision_id = ? AND delivery_status = 'delivered'
            """,
            (row["decision_id"],),
        )
        connection.execute(
            """
            UPDATE companion_device_boot_events
            SET status = 'responded', updated_at = ?
            WHERE boot_event_id = ? AND status = 'delivered'
            """,
            (interaction_at, row["boot_event_id"]),
        )
        CompanionStore._open_presence_lease_in_connection(
            connection,
            owner_user_id=owner_user_id,
            pet_id=pet_id,
            memory_subject_id=memory_subject_id,
            relationship_epoch_id=relationship_epoch_id,
            device_id=str(row["device_id"]),
            source="boot_checkin_response",
            source_ref=str(row["decision_id"]),
            opened_at=interaction_at,
            expires_at=(
                interaction + timedelta(seconds=max(int(presence_window_seconds), 1))
            ).isoformat(),
            updated_at=created_at,
        )
        return True

    def apply_va_event(
        self,
        *,
        event: CompanionVAEvent,
        xiaoxin_age: int | None,
        relationship_stage: str,
    ) -> CompanionVAEventResult:
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._assert_owner_in_connection(
                    connection,
                    owner_user_id=event.subject.owner_user_id,
                    pet_id=event.subject.pet_id,
                )
                existing = connection.execute(
                    "SELECT event_id FROM companion_va_events WHERE event_id = ?",
                    (event.event_id,),
                ).fetchone()
                if existing is not None:
                    connection.commit()
                    return CompanionVAEventResult(
                        event_id=event.event_id,
                        status="duplicate",
                    )
                epoch = connection.execute(
                    """
                    SELECT epoch_id, started_at
                    FROM relationship_epochs
                    WHERE pet_id = ? AND epoch_id = ?
                    """,
                    (event.subject.pet_id, event.relationship_epoch_id),
                ).fetchone()
                if epoch is None:
                    raise ValueError("VA event relationship epoch is invalid")
                relationship_epoch_id = str(epoch["epoch_id"])
                active_epoch = connection.execute(
                    """
                    SELECT epoch_id FROM relationship_epochs
                    WHERE pet_id = ? AND ended_at IS NULL
                    """,
                    (event.subject.pet_id,),
                ).fetchone()
                status = "applied"
                if (
                    active_epoch is None
                    or active_epoch["epoch_id"] != relationship_epoch_id
                    or datetime.fromisoformat(event.occurred_at)
                    < datetime.fromisoformat(str(epoch["started_at"]))
                ):
                    status = "ignored_stale_epoch"
                row = connection.execute(
                    """
                    SELECT * FROM companion_va_snapshots
                    WHERE pet_id = ? AND memory_subject_id = ?
                    """,
                    (event.subject.pet_id, event.subject.memory_subject_id),
                ).fetchone()
                state: VAState
                snapshot_valid = False
                if row is not None and status == "applied":
                    try:
                        state = _va_state_from_row(row)
                        identity_valid = (
                            row["relationship_epoch_id"] == relationship_epoch_id
                            and row["model_version"] == VA_MODEL_VERSION
                        )
                        if identity_valid:
                            if datetime.fromisoformat(
                                state.observed_at
                            ) > datetime.fromisoformat(event.received_at):
                                snapshot_valid = False
                            elif datetime.fromisoformat(
                                event.occurred_at
                            ) < datetime.fromisoformat(state.observed_at):
                                status = "ignored_out_of_order"
                            else:
                                snapshot_valid = True
                    except (KeyError, TypeError, ValueError):
                        snapshot_valid = False
                if not snapshot_valid:
                    state = baseline_va_state(
                        now=event.occurred_at,
                        age=xiaoxin_age,
                        relationship_stage=relationship_stage,
                    )
                if status == "applied":
                    state = apply_va_state_event(
                        state,
                        kind=event.kind,
                        occurred_at=event.occurred_at,
                        age=xiaoxin_age,
                        relationship_stage=relationship_stage,
                    )
                    connection.execute(
                        """
                        INSERT INTO companion_va_snapshots(
                            pet_id, memory_subject_id, relationship_epoch_id,
                            model_version, valence, arousal, observed_at, expires_at,
                            dynamics_age, dynamics_relationship_stage, context
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(pet_id, memory_subject_id) DO UPDATE SET
                            relationship_epoch_id = excluded.relationship_epoch_id,
                            model_version = excluded.model_version,
                            valence = excluded.valence,
                            arousal = excluded.arousal,
                            observed_at = excluded.observed_at,
                            expires_at = excluded.expires_at,
                            dynamics_age = excluded.dynamics_age,
                            dynamics_relationship_stage = excluded.dynamics_relationship_stage,
                            context = excluded.context
                        """,
                        (
                            event.subject.pet_id,
                            event.subject.memory_subject_id,
                            relationship_epoch_id,
                            VA_MODEL_VERSION,
                            state.valence,
                            state.arousal,
                            state.observed_at,
                            state.expires_at,
                            state.age,
                            state.relationship_stage,
                            state.context,
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO companion_va_events(
                        event_id, pet_id, relationship_epoch_id, status, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.subject.pet_id,
                        relationship_epoch_id,
                        status,
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    ),
                )
                connection.commit()
                return CompanionVAEventResult(event_id=event.event_id, status=status)
            except Exception:
                connection.rollback()
                raise

    def load_va_projection(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        now: str,
        xiaoxin_age: int | None,
        relationship_stage: str,
    ) -> Mapping[str, object]:
        fallback = baseline_va_state(
            now=now,
            age=xiaoxin_age,
            relationship_stage=relationship_stage,
        )
        with self.connection() as connection:
            self._assert_owner_in_connection(
                connection,
                owner_user_id=owner_user_id,
                pet_id=pet_id,
            )
            row = connection.execute(
                """
                SELECT * FROM companion_va_snapshots
                WHERE pet_id = ? AND memory_subject_id = ?
                """,
                (pet_id, memory_subject_id),
            ).fetchone()
        if row is None:
            return project_va_state(fallback)
        try:
            state = _va_state_from_row(row)
            if (
                row["relationship_epoch_id"] != relationship_epoch_id
                or row["model_version"] != VA_MODEL_VERSION
                or datetime.fromisoformat(state.observed_at)
                > datetime.fromisoformat(now)
            ):
                return project_va_state(fallback)
            return project_va_state(decay_va_state(state, now=now))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            return project_va_state(fallback)

    def get_birth_temperament(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
    ) -> BirthTemperament | None:
        with self.connection() as connection:
            self._assert_owner_in_connection(
                connection,
                owner_user_id=owner_user_id,
                pet_id=pet_id,
            )
            row = connection.execute(
                """
                SELECT pet_id, generator_version, exploration_orientation,
                       expression_energy, thought_organization, playfulness,
                       companion_initiative, generated_at, source_kind
                FROM companion_birth_temperaments
                WHERE pet_id = ?
                """,
                (pet_id,),
            ).fetchone()
        if row is None:
            _LOGGER.error(
                "Birth temperament is missing for persisted pet",
                extra={"companion_pet_id": pet_id},
            )
            return None
        temperament = _birth_temperament_from_row(row)
        if not temperament_matches_generation(temperament):
            _LOGGER.warning(
                "Birth temperament audit mismatch; preserving stored values",
                extra={
                    "companion_pet_id": pet_id,
                    "companion_temperament_generator_version": (
                        temperament.generator_version
                    ),
                },
            )
        return temperament

    def ensure_subject(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        started_at: str,
    ) -> RelationshipEpoch:
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                inserted_pet = connection.execute(
                    """
                    INSERT OR IGNORE INTO companion_pets(
                        pet_id, owner_user_id, created_at
                    ) VALUES (?, ?, ?)
                    """,
                    (pet_id, owner_user_id, started_at),
                )
                self._assert_owner_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                )
                self._ensure_birth_temperament_in_connection(
                    connection,
                    pet_id=pet_id,
                    generated_at=(
                        started_at
                        if inserted_pet.rowcount == 1
                        else datetime.now(timezone.utc).isoformat()
                    ),
                    source_kind=(
                        "pet_created"
                        if inserted_pet.rowcount == 1
                        else "legacy_backfill"
                    ),
                )
                row = connection.execute(
                    """
                    SELECT epoch_id, pet_id, started_at, ended_at,
                           start_reason, end_reason
                    FROM relationship_epochs
                    WHERE pet_id = ? AND ended_at IS NULL
                    """,
                    (pet_id,),
                ).fetchone()
                if row is None:
                    epoch_id = str(uuid4())
                    connection.execute(
                        """
                        INSERT INTO relationship_epochs(
                            epoch_id, pet_id, started_at, start_reason
                        ) VALUES (?, ?, ?, 'first_use')
                        """,
                        (epoch_id, pet_id, started_at),
                    )
                    row = connection.execute(
                        """
                        SELECT epoch_id, pet_id, started_at, ended_at,
                               start_reason, end_reason
                        FROM relationship_epochs
                        WHERE epoch_id = ?
                        """,
                        (epoch_id,),
                    ).fetchone()
                connection.commit()
                return _relationship_epoch_from_row(row)
            except Exception:
                connection.rollback()
                raise

    def recall_evidence(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        turn_id: str,
        interaction_kind: str,
        now: str,
        retrieval_query: str | None = None,
        retrieval_hints: Mapping[str, object] | None = None,
        limit: int = 2,
    ) -> tuple[CompanionEvidence, ...]:
        started_at = time.perf_counter()
        retrieval_hints = retrieval_hints or {}
        limit = min(max(int(limit), 0), 8)
        if limit == 0:
            return ()
        fact_keys = tuple(dict.fromkeys(retrieval_hints.get("fact_keys", ())))
        kinds = tuple(dict.fromkeys(retrieval_hints.get("kinds", ())))
        excluded_sensitivities = tuple(
            dict.fromkeys(retrieval_hints.get("exclude_sensitivities", ()))
        )
        exact_order_sql = "0.0"
        exact_order_params: tuple[object, ...] = ()
        if fact_keys:
            placeholders = ",".join("?" for _ in fact_keys)
            exact_order_sql = (
                f"CASE WHEN evidence.fact_key IN ({placeholders}) THEN 1 ELSE 0 END"
            )
            exact_order_params = fact_keys
        fts_expression = _fts_query_expression(retrieval_query)
        lexical_join = ""
        lexical_order_sql = "0.0"
        lexical_params: tuple[object, ...] = ()
        if fts_expression is not None:
            lexical_join = """
                LEFT JOIN (
                    SELECT evidence_id,
                           -bm25(companion_evidence_fts) AS lexical_score
                    FROM companion_evidence_fts
                    WHERE companion_evidence_fts MATCH ?
                      AND pet_id = ?
                      AND memory_subject_id = ?
                ) AS lexical
                  ON lexical.evidence_id = evidence.evidence_id
            """
            lexical_order_sql = "COALESCE(lexical.lexical_score, 0.0)"
            lexical_params = (
                fts_expression,
                pet_id,
                memory_subject_id,
            )
        filter_sql: list[str] = []
        filter_params: list[object] = []
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            filter_sql.append(f"evidence.kind IN ({placeholders})")
            filter_params.extend(kinds)
        if "recent_conversation" not in kinds:
            filter_sql.append("evidence.kind != 'recent_conversation'")
        if excluded_sensitivities:
            placeholders = ",".join("?" for _ in excluded_sensitivities)
            filter_sql.append(f"evidence.sensitivity NOT IN ({placeholders})")
            filter_params.extend(excluded_sensitivities)
        time_from = retrieval_hints.get("time_from")
        if isinstance(time_from, str):
            filter_sql.append("julianday(evidence.occurred_at) >= julianday(?)")
            filter_params.append(time_from)
        time_to = retrieval_hints.get("time_to")
        if isinstance(time_to, str):
            filter_sql.append("julianday(evidence.occurred_at) <= julianday(?)")
            filter_params.append(time_to)
        extra_filters = ""
        if filter_sql:
            extra_filters = " AND " + " AND ".join(filter_sql)
        with self.connection() as connection:
            try:
                connection.execute("BEGIN")
                self._assert_owner_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                )
                connection.execute(
                    """
                    DELETE FROM companion_retrieval_audits
                    WHERE julianday(expires_at) <= julianday(?)
                    """,
                    (now,),
                )
                recent_rows = connection.execute(
                    """
                    SELECT selected_evidence_ids_json
                    FROM companion_retrieval_audits
                    WHERE pet_id = ? AND memory_subject_id = ?
                      AND turn_id <> ?
                      AND julianday(expires_at) > julianday(?)
                    ORDER BY julianday(created_at) DESC, audit_id
                    LIMIT 8
                    """,
                    (pet_id, memory_subject_id, turn_id, now),
                ).fetchall()
                recent_reference_counts: dict[str, int] = {}
                for recent in recent_rows:
                    try:
                        recent_ids = json.loads(recent["selected_evidence_ids_json"])
                    except json.JSONDecodeError as exc:
                        raise sqlite3.DatabaseError(
                            "retrieval audit Evidence IDs must be JSON"
                        ) from exc
                    if not isinstance(recent_ids, list):
                        raise sqlite3.DatabaseError(
                            "retrieval audit Evidence IDs must be a list"
                        )
                    for evidence_id in recent_ids:
                        if isinstance(evidence_id, str):
                            recent_reference_counts[evidence_id] = (
                                recent_reference_counts.get(evidence_id, 0) + 1
                            )
                rows = connection.execute(
                    f"""
                    SELECT evidence.*
                    FROM companion_evidence AS evidence
                    {lexical_join}
                    WHERE evidence.pet_id = ?
                      AND evidence.memory_subject_id = ?
                      AND evidence.status IN ('candidate', 'active')
                      AND evidence.prompt_eligible = 1
                      AND (
                        evidence.expires_at IS NULL
                        OR julianday(evidence.expires_at) > julianday(?)
                      )
                      AND (
                        evidence.valid_from IS NULL
                        OR julianday(evidence.valid_from) <= julianday(?)
                      )
                      AND (
                        evidence.valid_until IS NULL
                        OR julianday(evidence.valid_until) > julianday(?)
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM evidence_relations AS override_relation
                        JOIN companion_evidence AS override
                          ON override.evidence_id = override_relation.target_evidence_id
                         AND override.pet_id = override_relation.pet_id
                        WHERE override_relation.relation_kind =
                              'temporarily_overridden_by'
                          AND override_relation.source_evidence_id =
                              evidence.evidence_id
                          AND override.status = 'active'
                          AND override.prompt_eligible = 1
                          AND (
                            override.expires_at IS NULL
                            OR julianday(override.expires_at) > julianday(?)
                          )
                          AND (
                            override.valid_until IS NULL
                            OR julianday(override.valid_until) > julianday(?)
                          )
                      )
                      AND (
                        evidence.ownership_scope = 'user'
                        OR (
                            evidence.ownership_scope = 'relationship'
                            AND evidence.relationship_epoch_id = ?
                        )
                      )
                      {extra_filters}
                    ORDER BY {exact_order_sql} DESC,
                             {lexical_order_sql} DESC,
                             evidence.occurred_at DESC,
                             evidence.evidence_id
                    LIMIT 64
                    """,
                    (
                        *lexical_params,
                        pet_id,
                        memory_subject_id,
                        now,
                        now,
                        now,
                        now,
                        now,
                        relationship_epoch_id,
                        *filter_params,
                        *exact_order_params,
                    ),
                ).fetchall()
                ranked: list[tuple[CompanionEvidence, dict[str, object]]] = []
                requires_relevance = bool(
                    _search_trigrams(retrieval_query) or fact_keys or kinds
                )
                structured_time_match = isinstance(time_from, str) or isinstance(
                    time_to,
                    str,
                )
                for row in rows:
                    evidence = _evidence_from_row(row)
                    details = _retrieval_score(
                        evidence,
                        query=retrieval_query,
                        fact_keys=fact_keys,
                        kinds=kinds,
                        now=now,
                        recent_reference_count=recent_reference_counts.get(
                            evidence.evidence_id,
                            0,
                        ),
                        apply_recent_reference_penalty=(
                            interaction_kind != "explicit_recall"
                        ),
                    )
                    if requires_relevance and not (
                        details["exact_fact"]
                        or details["kind_match"]
                        or float(details["lexical_overlap"]) > 0.0
                        or structured_time_match
                    ):
                        continue
                    ranked.append((evidence, details))
                ranked.sort(
                    key=lambda item: (
                        -float(item[1]["total_score"]),
                        -datetime.fromisoformat(item[0].occurred_at).timestamp(),
                        item[0].evidence_id,
                    )
                )
                ranked = _deduplicate_memory_fact_key_aliases(ranked)
                selected = ranked[:limit]
                primary_kinds = tuple(kind for kind in kinds if kind != "interest")
                if (
                    interaction_kind == "explicit_recall"
                    and 1 < len(primary_kinds) <= limit
                ):
                    required_ids: set[str] = set()
                    for requested_kind in primary_kinds:
                        match = next(
                            (
                                item
                                for item in ranked
                                if item[0].kind == requested_kind
                                and item[0].evidence_id not in required_ids
                            ),
                            None,
                        )
                        if match is not None:
                            required_ids.add(match[0].evidence_id)
                    selected = [
                        item for item in ranked if item[0].evidence_id in required_ids
                    ]
                    selected.extend(
                        item
                        for item in ranked
                        if item[0].evidence_id not in required_ids
                    )
                    selected = selected[:limit]
                selected_ids = [item[0].evidence_id for item in selected]
                duration_ms = max(
                    (time.perf_counter() - started_at) * 1000,
                    0.0,
                )
                audit_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"xiaoxin:retrieval-audit:{pet_id}:{turn_id}",
                    )
                )
                expires_at = (
                    datetime.fromisoformat(now) + timedelta(days=7)
                ).isoformat()
                connection.execute(
                    """
                    INSERT INTO companion_retrieval_audits(
                        audit_id, turn_id, pet_id, memory_subject_id,
                        relationship_epoch_id, interaction_kind,
                        query_digest, hints_digest, candidate_count,
                        selected_evidence_ids_json, score_details_json,
                        duration_ms, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(turn_id, pet_id) DO UPDATE SET
                        memory_subject_id = excluded.memory_subject_id,
                        relationship_epoch_id = excluded.relationship_epoch_id,
                        interaction_kind = excluded.interaction_kind,
                        query_digest = excluded.query_digest,
                        hints_digest = excluded.hints_digest,
                        candidate_count = excluded.candidate_count,
                        selected_evidence_ids_json = excluded.selected_evidence_ids_json,
                        score_details_json = excluded.score_details_json,
                        duration_ms = excluded.duration_ms,
                        created_at = excluded.created_at,
                        expires_at = excluded.expires_at
                    """,
                    (
                        audit_id,
                        turn_id,
                        pet_id,
                        memory_subject_id,
                        relationship_epoch_id,
                        interaction_kind,
                        hashlib.sha256(
                            (retrieval_query or "").encode("utf-8")
                        ).hexdigest(),
                        hashlib.sha256(
                            _stable_json(dict(retrieval_hints)).encode("utf-8")
                        ).hexdigest(),
                        len(rows),
                        _stable_json(selected_ids),
                        _stable_json([item[1] for item in selected]),
                        duration_ms,
                        now,
                        expires_at,
                    ),
                )
                connection.commit()
                return tuple(item[0] for item in selected)
            except Exception:
                connection.rollback()
                raise

    def load_policy_material(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        now: str,
        surface: str,
        interaction_kind: str,
        context: str = "ordinary",
    ) -> CompanionPolicyMaterial:
        with self.connection() as connection:
            connection.execute("BEGIN")
            self._assert_owner_in_connection(
                connection,
                owner_user_id=owner_user_id,
                pet_id=pet_id,
            )
            active_epoch = connection.execute(
                """
                SELECT started_at FROM relationship_epochs
                WHERE epoch_id = ? AND pet_id = ? AND ended_at IS NULL
                """,
                (relationship_epoch_id, pet_id),
            ).fetchone()
            if active_epoch is None:
                connection.rollback()
                raise ValueError("relationship epoch is not active for this pet")
            turn_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM companion_turns
                WHERE pet_id = ?
                  AND memory_subject_id = ?
                  AND relationship_epoch_id = ?
                  AND julianday(occurred_at) <= julianday(?)
                """,
                (pet_id, memory_subject_id, relationship_epoch_id, now),
            ).fetchone()[0]
            interaction_date_rows = connection.execute(
                """
                SELECT occurred_at
                FROM companion_turns
                WHERE pet_id = ?
                  AND memory_subject_id = ?
                  AND relationship_epoch_id = ?
                  AND julianday(occurred_at) <= julianday(?)
                """,
                (pet_id, memory_subject_id, relationship_epoch_id, now),
            ).fetchall()
            interaction_dates = tuple(
                sorted(
                    {
                        _shanghai_local_date(row["occurred_at"])
                        for row in interaction_date_rows
                    }
                )
            )
            distinct_interaction_days = len(interaction_dates)
            historical_stage_row = connection.execute(
                """
                SELECT relationship_stage
                FROM relationship_stage_events
                WHERE pet_id = ?
                  AND memory_subject_id = ?
                  AND relationship_epoch_id = ?
                  AND julianday(occurred_at) <= julianday(?)
                ORDER BY CASE relationship_stage
                    WHEN 'long_term_companion' THEN 3
                    WHEN 'attuned' THEN 2
                    WHEN 'familiar' THEN 1
                    ELSE 0
                END DESC, rowid DESC
                LIMIT 1
                """,
                (pet_id, memory_subject_id, relationship_epoch_id, now),
            ).fetchone()
            relationship_stage_rows = connection.execute(
                """
                SELECT occurred_at, relationship_stage
                FROM relationship_stage_events
                WHERE pet_id = ?
                  AND memory_subject_id = ?
                  AND relationship_epoch_id = ?
                  AND julianday(occurred_at) <= julianday(?)
                ORDER BY julianday(occurred_at), rowid
                """,
                (pet_id, memory_subject_id, relationship_epoch_id, now),
            ).fetchall()
            evidence_rows = connection.execute(
                """
                SELECT *
                FROM companion_evidence
                WHERE pet_id = ?
                  AND memory_subject_id = ?
                  AND status = 'active'
                  AND julianday(occurred_at) <= julianday(?)
                  AND (expires_at IS NULL OR julianday(expires_at) > julianday(?))
                  AND (valid_from IS NULL OR julianday(valid_from) <= julianday(?))
                  AND (valid_until IS NULL OR julianday(valid_until) > julianday(?))
                  AND NOT EXISTS (
                    SELECT 1
                    FROM evidence_relations AS override_relation
                    JOIN companion_evidence AS override
                      ON override.evidence_id = override_relation.target_evidence_id
                     AND override.pet_id = override_relation.pet_id
                    WHERE override_relation.relation_kind =
                          'temporarily_overridden_by'
                      AND override_relation.source_evidence_id =
                          companion_evidence.evidence_id
                      AND override.status = 'active'
                      AND override.prompt_eligible = 1
                      AND (
                        override.expires_at IS NULL
                        OR julianday(override.expires_at) > julianday(?)
                      )
                      AND (
                        override.valid_until IS NULL
                        OR julianday(override.valid_until) > julianday(?)
                      )
                  )
                  AND (
                    ownership_scope = 'user'
                    OR relationship_epoch_id = ?
                  )
                ORDER BY occurred_at, evidence_id
                """,
                (
                    pet_id,
                    memory_subject_id,
                    now,
                    now,
                    now,
                    now,
                    now,
                    now,
                    relationship_epoch_id,
                ),
            ).fetchall()
            adjustment_rows = connection.execute(
                """
                SELECT adjustment.dimension, adjustment.value_json, adjustment.scope,
                       adjustment.direction, adjustment.confidence,
                       adjustment.generated_by
                FROM companion_adjustments AS adjustment
                WHERE adjustment.pet_id = ?
                  AND adjustment.relationship_epoch_id = ?
                  AND adjustment.status = 'active'
                  AND adjustment.behavior_key IS NOT NULL
                  AND adjustment.context_scope IS NOT NULL
                  AND adjustment.direction IS NOT NULL
                  AND (
                    adjustment.valid_until IS NULL
                    OR julianday(adjustment.valid_until) > julianday(?)
                  )
                  AND EXISTS (
                    SELECT 1
                    FROM adjustment_evidence AS link
                    JOIN companion_evidence AS evidence
                      ON evidence.evidence_id = link.evidence_id
                     AND evidence.pet_id = link.pet_id
                    WHERE link.adjustment_id = adjustment.adjustment_id
                      AND evidence.memory_subject_id = ?
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM adjustment_evidence AS link
                    JOIN companion_evidence AS evidence
                      ON evidence.evidence_id = link.evidence_id
                     AND evidence.pet_id = link.pet_id
                    WHERE link.adjustment_id = adjustment.adjustment_id
                      AND evidence.memory_subject_id <> ?
                  )
                ORDER BY adjustment.created_at, adjustment.adjustment_id
                """,
                (
                    pet_id,
                    relationship_epoch_id,
                    now,
                    memory_subject_id,
                    memory_subject_id,
                ),
            ).fetchall()
            contract_rows = connection.execute(
                """
                SELECT dimension, value_json, scope
                FROM companion_interaction_contracts
                WHERE pet_id = ? AND memory_subject_id = ? AND status = 'active'
                ORDER BY created_at, contract_id
                """,
                (pet_id, memory_subject_id),
            ).fetchall()
            connection.commit()
        adjustments: dict[str, object] = {}
        behavior_adjustments: list[BehaviorAdjustmentSignal] = []
        for row in adjustment_rows:
            if not _adjustment_scope_matches(
                row["scope"],
                surface=surface,
                interaction_kind=interaction_kind,
                context=context,
            ):
                continue
            try:
                payload = json.loads(row["value_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            dimension = row["dimension"]
            if not isinstance(payload, dict) or set(payload) != {"value"}:
                continue
            value = payload["value"]
            if not isinstance(value, str):
                continue
            if value not in ALLOWED_ADJUSTMENT_VALUES.get(dimension, frozenset()):
                continue
            adjustments[dimension] = value
            behavior_adjustments.append(
                BehaviorAdjustmentSignal(
                    dimension=dimension,
                    value=value,
                    source_kind=(
                        "explicit_feedback"
                        if row["generated_by"]
                        == "deterministic-explicit-preference-feedback"
                        else "inferred_adjustment"
                    ),
                    confidence=float(row["confidence"]),
                    direction=row["direction"],
                )
            )
        has_explicit_emotional_support_preference = any(
            row["fact_key"] == "preference:emotional_support_style"
            and row["ownership_scope"] == "user"
            and row["attribution"]
            in {
                "explicit_statement",
                "explicit_user_statement",
                "user_confirmed_candidate",
            }
            for row in evidence_rows
        )
        if (
            has_explicit_emotional_support_preference
            and context in _EMOTIONAL_SUPPORT_CONTEXTS
        ):
            for dimension in _EMOTIONAL_SUPPORT_SUPERSEDED_ADJUSTMENTS:
                adjustments.pop(dimension, None)
            behavior_adjustments = [
                signal
                for signal in behavior_adjustments
                if signal.dimension not in _EMOTIONAL_SUPPORT_SUPERSEDED_ADJUSTMENTS
            ]
        for row in contract_rows:
            if not _adjustment_scope_matches(
                row["scope"],
                surface=surface,
                interaction_kind=interaction_kind,
                context=context,
            ):
                continue
            try:
                payload = json.loads(row["value_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            dimension = row["dimension"]
            value = payload.get("value") if isinstance(payload, dict) else None
            if (
                not isinstance(payload, dict)
                or set(payload) != {"value"}
                or not isinstance(value, str)
                or value not in ALLOWED_ADJUSTMENT_VALUES.get(dimension, frozenset())
            ):
                continue
            adjustments[dimension] = value
            behavior_adjustments = [
                signal
                for signal in behavior_adjustments
                if signal.dimension != dimension
            ]
            behavior_adjustments.append(
                BehaviorAdjustmentSignal(
                    dimension=dimension,
                    value=value,
                    source_kind="explicit_contract",
                    confidence=1.0,
                )
            )
        return CompanionPolicyMaterial(
            turn_count=int(turn_count),
            distinct_interaction_days=int(distinct_interaction_days),
            relationship_started_at=active_epoch["started_at"],
            interaction_dates=interaction_dates,
            historical_stage=(
                historical_stage_row["relationship_stage"]
                if historical_stage_row is not None
                else None
            ),
            relationship_stage_history=tuple(
                (row["occurred_at"], row["relationship_stage"])
                for row in relationship_stage_rows
            ),
            evidence=tuple(_evidence_from_row(row) for row in evidence_rows),
            active_adjustments=adjustments,
            behavior_adjustments=tuple(behavior_adjustments),
        )

    def record_relationship_stage_event(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        relationship_stage: str,
        quality: Mapping[str, int],
        reason_codes: tuple[str, ...],
        policy_version: str,
        now: str,
    ) -> None:
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._assert_owner_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                )
                active_epoch = connection.execute(
                    """
                    SELECT 1 FROM relationship_epochs
                    WHERE epoch_id = ? AND pet_id = ? AND ended_at IS NULL
                    """,
                    (relationship_epoch_id, pet_id),
                ).fetchone()
                if active_epoch is None:
                    raise ValueError("relationship epoch is not active for this pet")
                previous = connection.execute(
                    """
                    SELECT relationship_stage
                    FROM relationship_stage_events
                    WHERE pet_id = ? AND memory_subject_id = ?
                      AND relationship_epoch_id = ?
                    ORDER BY CASE relationship_stage
                        WHEN 'long_term_companion' THEN 3
                        WHEN 'attuned' THEN 2
                        WHEN 'familiar' THEN 1
                        ELSE 0
                    END DESC, rowid DESC
                    LIMIT 1
                    """,
                    (pet_id, memory_subject_id, relationship_epoch_id),
                ).fetchone()
                previous_stage = (
                    previous["relationship_stage"] if previous is not None else None
                )
                if (
                    previous_stage is not None
                    and _RELATIONSHIP_STAGE_ORDER[relationship_stage]
                    <= _RELATIONSHIP_STAGE_ORDER[previous_stage]
                ):
                    connection.commit()
                    return
                connection.execute(
                    """
                    INSERT INTO relationship_stage_events(
                        event_id, pet_id, memory_subject_id,
                        relationship_epoch_id, previous_stage,
                        relationship_stage, quality_json, reason_codes_json,
                        policy_version, occurred_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        pet_id,
                        memory_subject_id,
                        relationship_epoch_id,
                        previous_stage,
                        relationship_stage,
                        _stable_json(dict(quality)),
                        _stable_json(reason_codes),
                        policy_version,
                        now,
                        now,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def decide_initiative(
        self,
        *,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        evidence_ids: tuple[str, ...],
        content_brief: str,
        hardware_expression: Mapping[str, object],
        now: str,
    ) -> Mapping[str, object]:
        unique_evidence_ids = tuple(dict.fromkeys(evidence_ids))
        if not unique_evidence_ids:
            return {"eligible": False, "reason_code": "no_evidence"}
        local_date = now[:10]
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                epoch = connection.execute(
                    """
                    SELECT 1
                    FROM relationship_epochs
                    WHERE epoch_id = ? AND pet_id = ? AND ended_at IS NULL
                    """,
                    (relationship_epoch_id, pet_id),
                ).fetchone()
                placeholders = ",".join("?" for _ in unique_evidence_ids)
                evidence_rows = connection.execute(
                    f"""
                    SELECT evidence_id, source_summary
                    FROM companion_evidence
                    WHERE evidence_id IN ({placeholders})
                      AND pet_id = ?
                      AND memory_subject_id = ?
                      AND status = 'active'
                      AND prompt_eligible = 1
                      AND (expires_at IS NULL OR julianday(expires_at) > julianday(?))
                      AND kind IN (
                        'accepted_help', 'followup_completed',
                        'interaction_feedback', 'meaningful_moment'
                      )
                      AND (
                        ownership_scope = 'user'
                        OR relationship_epoch_id = ?
                      )
                    """,
                    (
                        *unique_evidence_ids,
                        pet_id,
                        memory_subject_id,
                        now,
                        relationship_epoch_id,
                    ),
                ).fetchall()
                if epoch is None or len(evidence_rows) != len(unique_evidence_ids):
                    connection.commit()
                    return {"eligible": False, "reason_code": "no_evidence"}
                summaries = {row["source_summary"] for row in evidence_rows}
                if content_brief not in summaries:
                    raise ValueError(
                        "initiative content brief must come from cited Evidence"
                    )
                rejected = connection.execute(
                    """
                    SELECT 1 FROM initiative_decisions
                    WHERE pet_id = ? AND relationship_epoch_id = ?
                      AND reason_code = 'evidence_backed_followup'
                      AND delivery_status = 'rejected'
                      AND cooldown_until IS NOT NULL
                      AND julianday(cooldown_until) > julianday(?)
                    LIMIT 1
                    """,
                    (pet_id, relationship_epoch_id, now),
                ).fetchone()
                if rejected is not None:
                    connection.commit()
                    return {
                        "eligible": False,
                        "reason_code": "rejection_cooldown",
                    }
                existing = connection.execute(
                    """
                    SELECT 1
                    FROM initiative_decisions AS decision
                    LEFT JOIN initiative_opportunities AS opportunity
                      ON opportunity.decision_id = decision.decision_id
                    WHERE decision.pet_id = ?
                      AND decision.relationship_epoch_id = ?
                      AND decision.priority = 'low'
                      AND substr(decision.created_at, 1, 10) = ?
                      AND (
                        opportunity.opportunity_kind IS NULL
                        OR opportunity.opportunity_kind <> 'boot_checkin'
                      )
                    """,
                    (pet_id, relationship_epoch_id, local_date),
                ).fetchone()
                if existing is not None:
                    connection.commit()
                    return {"eligible": False, "reason_code": "daily_limit"}
                decision_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"xiaoxin:initiative:{pet_id}:{relationship_epoch_id}:"
                        f"{local_date}",
                    )
                )
                connection.execute(
                    """
                    INSERT INTO initiative_decisions(
                        decision_id, pet_id, relationship_epoch_id, reason_code,
                        evidence_ids_json, priority, cooldown_until,
                        content_brief, hardware_expression_json,
                        delivery_status, created_at
                    ) VALUES (?, ?, ?, 'evidence_backed_followup', ?, 'low',
                              ?, ?, ?, 'pending', ?)
                    """,
                    (
                        decision_id,
                        pet_id,
                        relationship_epoch_id,
                        _stable_json(unique_evidence_ids),
                        (datetime.fromisoformat(now) + timedelta(days=1)).isoformat(),
                        content_brief,
                        _stable_json(hardware_expression),
                        now,
                    ),
                )
                connection.commit()
                return {
                    "eligible": True,
                    "decision_id": decision_id,
                    "reason_code": "evidence_backed_followup",
                    "evidence_ids": unique_evidence_ids,
                    "content_brief": content_brief,
                    "hardware_expression": dict(hardware_expression),
                }
            except Exception:
                connection.rollback()
                raise

    def claim_initiative_delivery(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        decision_id: str,
        now: str,
    ) -> Mapping[str, object] | None:
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._assert_owner_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                )
                decision = connection.execute(
                    """
                    SELECT d.relationship_epoch_id, d.reason_code,
                           d.evidence_ids_json, d.content_brief,
                           d.hardware_expression_json
                    FROM initiative_decisions AS d
                    JOIN relationship_epochs AS epoch
                      ON epoch.epoch_id = d.relationship_epoch_id
                     AND epoch.pet_id = d.pet_id
                    WHERE d.decision_id = ?
                      AND d.pet_id = ?
                      AND d.delivery_status = 'pending'
                      AND epoch.ended_at IS NULL
                    """,
                    (decision_id, pet_id),
                ).fetchone()
                if decision is None:
                    connection.commit()
                    return None
                try:
                    raw_evidence_ids = json.loads(decision["evidence_ids_json"])
                    hardware_expression = json.loads(
                        decision["hardware_expression_json"]
                    )
                except (TypeError, json.JSONDecodeError) as exc:
                    raise ValueError("initiative decision payload is invalid") from exc
                if not isinstance(raw_evidence_ids, list) or not isinstance(
                    hardware_expression, dict
                ):
                    raise ValueError("initiative decision payload is invalid")
                evidence_ids = tuple(raw_evidence_ids)
                if (
                    not evidence_ids
                    or len(set(evidence_ids)) != len(evidence_ids)
                    or any(
                        not isinstance(evidence_id, str) or not evidence_id
                        for evidence_id in evidence_ids
                    )
                ):
                    raise ValueError("initiative decision Evidence is invalid")
                placeholders = ",".join("?" for _ in evidence_ids)
                subject_evidence_count = connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM companion_evidence
                    WHERE evidence_id IN ({placeholders})
                      AND pet_id = ?
                      AND memory_subject_id = ?
                    """,
                    (*evidence_ids, pet_id, memory_subject_id),
                ).fetchone()[0]
                if int(subject_evidence_count) != len(evidence_ids):
                    raise PermissionError(
                        "initiative decision does not belong to this subject"
                    )
                active_count = connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM companion_evidence
                    WHERE evidence_id IN ({placeholders})
                      AND pet_id = ?
                      AND memory_subject_id = ?
                      AND status = 'active'
                      AND prompt_eligible = 1
                      AND (expires_at IS NULL OR julianday(expires_at) > julianday(?))
                      AND (
                        ownership_scope = 'user'
                        OR relationship_epoch_id = ?
                      )
                    """,
                    (
                        *evidence_ids,
                        pet_id,
                        memory_subject_id,
                        now,
                        decision["relationship_epoch_id"],
                    ),
                ).fetchone()[0]
                if int(active_count) != len(set(evidence_ids)):
                    connection.execute(
                        """
                        UPDATE initiative_decisions
                        SET delivery_status = 'invalidated'
                        WHERE decision_id = ? AND delivery_status = 'pending'
                        """,
                        (decision_id,),
                    )
                    connection.commit()
                    return None
                updated = connection.execute(
                    """
                    UPDATE initiative_decisions
                    SET delivery_status = 'dispatching'
                    WHERE decision_id = ? AND delivery_status = 'pending'
                    """,
                    (decision_id,),
                )
                if updated.rowcount != 1:
                    connection.rollback()
                    return None
                connection.commit()
                return {
                    "eligible": True,
                    "decision_id": decision_id,
                    "reason_code": decision["reason_code"],
                    "evidence_ids": evidence_ids,
                    "content_brief": decision["content_brief"],
                    "hardware_expression": hardware_expression,
                }
            except Exception:
                connection.rollback()
                raise

    def load_initiative_quiet_hours(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
    ) -> Mapping[str, object] | None:
        with self.connection() as connection:
            self._assert_owner_in_connection(
                connection,
                owner_user_id=owner_user_id,
                pet_id=pet_id,
            )
            row = connection.execute(
                """
                SELECT value_json
                FROM companion_interaction_contracts
                WHERE pet_id = ? AND memory_subject_id = ?
                  AND dimension = 'initiative_quiet_hours'
                  AND scope = 'initiative' AND status = 'active'
                ORDER BY updated_at DESC, contract_id DESC
                LIMIT 1
                """,
                (pet_id, memory_subject_id),
            ).fetchone()
            if row is None:
                return None
            try:
                payload = json.loads(row["value_json"])
            except (TypeError, json.JSONDecodeError):
                return None
            if (
                not isinstance(payload, dict)
                or set(payload) != {"enabled", "start", "end"}
                or not isinstance(payload["enabled"], bool)
                or not _is_local_hhmm(payload["start"])
                or not _is_local_hhmm(payload["end"])
            ):
                return None
            return payload

    def load_projection_state(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        now: str,
    ) -> Mapping[str, object]:
        with self.connection() as connection:
            self._assert_owner_in_connection(
                connection,
                owner_user_id=owner_user_id,
                pet_id=pet_id,
            )
            adjustments = tuple(
                {
                    "adjustment_id": row["adjustment_id"],
                    "dimension": row["dimension"],
                    "value": json.loads(row["value_json"])["value"],
                    "scope": row["scope"],
                    "confidence": row["confidence"],
                }
                for row in connection.execute(
                    """
                    SELECT adjustment.adjustment_id, adjustment.dimension,
                           adjustment.value_json, adjustment.scope,
                           adjustment.confidence
                    FROM companion_adjustments AS adjustment
                    WHERE adjustment.pet_id = ?
                      AND adjustment.relationship_epoch_id = ?
                      AND adjustment.status = 'active'
                      AND (
                        adjustment.valid_until IS NULL
                        OR julianday(adjustment.valid_until) > julianday(?)
                      )
                      AND EXISTS (
                        SELECT 1
                        FROM adjustment_evidence AS link
                        JOIN companion_evidence AS evidence
                          ON evidence.evidence_id = link.evidence_id
                         AND evidence.pet_id = link.pet_id
                        WHERE link.adjustment_id = adjustment.adjustment_id
                          AND evidence.memory_subject_id = ?
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM adjustment_evidence AS link
                        JOIN companion_evidence AS evidence
                          ON evidence.evidence_id = link.evidence_id
                         AND evidence.pet_id = link.pet_id
                        WHERE link.adjustment_id = adjustment.adjustment_id
                          AND evidence.memory_subject_id <> ?
                      )
                    ORDER BY adjustment.created_at, adjustment.adjustment_id
                    """,
                    (
                        pet_id,
                        relationship_epoch_id,
                        now,
                        memory_subject_id,
                        memory_subject_id,
                    ),
                )
            )
            contracts_list: list[Mapping[str, object]] = []
            for row in connection.execute(
                """
                SELECT contract_id, dimension, value_json, scope,
                       safe_label, safe_scope
                FROM companion_interaction_contracts
                WHERE pet_id = ? AND memory_subject_id = ? AND status = 'active'
                ORDER BY created_at, contract_id
                """,
                (pet_id, memory_subject_id),
            ):
                try:
                    payload = json.loads(row["value_json"])
                except (TypeError, json.JSONDecodeError):
                    continue
                if row["dimension"] == "initiative_quiet_hours":
                    if (
                        not isinstance(payload, dict)
                        or set(payload) != {"enabled", "start", "end"}
                        or not isinstance(payload["enabled"], bool)
                        or not _is_local_hhmm(payload["start"])
                        or not _is_local_hhmm(payload["end"])
                    ):
                        continue
                    contracts_list.append(
                        {
                            "contract_id": row["contract_id"],
                            "dimension": row["dimension"],
                            "value": payload,
                            "scope": row["scope"],
                            "safe_label": row["safe_label"],
                            "safe_scope": row["safe_scope"],
                        }
                    )
                    continue
                value = payload.get("value") if isinstance(payload, dict) else None
                if (
                    not isinstance(payload, dict)
                    or set(payload) != {"value"}
                    or not isinstance(value, str)
                    or value
                    not in ALLOWED_ADJUSTMENT_VALUES.get(
                        row["dimension"], frozenset()
                    )
                ):
                    continue
                contracts_list.append(
                    {
                        "contract_id": row["contract_id"],
                        "dimension": row["dimension"],
                        "value": value,
                        "scope": row["scope"],
                        "safe_label": row["safe_label"],
                        "safe_scope": row["safe_scope"],
                    }
                )
            contracts = tuple(contracts_list)
            chapters = tuple(
                {
                    "chapter_id": row["chapter_id"],
                    "academic_stage": row["academic_stage"],
                    "safe_narrative": row["safe_narrative"],
                    "version": row["version"],
                    "evidence_ids": tuple(
                        link[0]
                        for link in connection.execute(
                            """
                            SELECT evidence_id FROM chapter_evidence
                            WHERE chapter_id = ? ORDER BY evidence_id
                            """,
                            (row["chapter_id"],),
                        )
                    ),
                }
                for row in connection.execute(
                    """
                    SELECT chapter.chapter_id, chapter.academic_stage,
                           chapter.safe_narrative, chapter.version
                    FROM companion_chapters AS chapter
                    WHERE chapter.pet_id = ?
                      AND chapter.relationship_epoch_id = ?
                      AND chapter.status = 'active'
                      AND EXISTS (
                        SELECT 1
                        FROM chapter_evidence AS link
                        JOIN companion_evidence AS evidence
                          ON evidence.evidence_id = link.evidence_id
                         AND evidence.pet_id = link.pet_id
                        WHERE link.chapter_id = chapter.chapter_id
                          AND evidence.memory_subject_id = ?
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM chapter_evidence AS link
                        JOIN companion_evidence AS evidence
                          ON evidence.evidence_id = link.evidence_id
                         AND evidence.pet_id = link.pet_id
                        WHERE link.chapter_id = chapter.chapter_id
                          AND evidence.memory_subject_id <> ?
                      )
                    ORDER BY chapter.created_at DESC, chapter.chapter_id DESC
                    LIMIT 3
                    """,
                    (
                        pet_id,
                        relationship_epoch_id,
                        memory_subject_id,
                        memory_subject_id,
                    ),
                )
            )
            jobs = tuple(
                {
                    "job_id": row["job_id"],
                    "job_kind": row["job_kind"],
                    "status": row["status"],
                    "attempt": row["attempt"],
                    "model": row["model"],
                    "prompt_version": row["prompt_version"],
                    "schema_version": row["schema_version"],
                    "failure_reason": row["failure_reason"],
                }
                for row in connection.execute(
                    """
                    SELECT job_id, job_kind, status, attempt, model,
                           prompt_version, schema_version, failure_reason
                    FROM consolidation_jobs
                    WHERE pet_id = ? AND relationship_epoch_id = ?
                      AND json_extract(payload_json, '$.memory_subject_id') = ?
                    ORDER BY updated_at DESC, job_id DESC LIMIT 20
                    """,
                    (pet_id, relationship_epoch_id, memory_subject_id),
                )
            )
            pending_memory_candidates = tuple(
                {
                    "candidate_id": row["evidence_id"],
                    "kind": row["kind"],
                    "safe_summary": row["source_summary"],
                    "safe_basis": "来自你最近的对话，确认后才会用于陪伴。",
                    "occurred_at": row["occurred_at"],
                    "expires_at": row["expires_at"],
                    "available_actions": (
                        "confirm",
                        "reject",
                        "correct",
                        "delete",
                    ),
                }
                for row in connection.execute(
                    """
                    SELECT evidence_id, kind, source_summary, occurred_at,
                           expires_at
                    FROM companion_evidence
                    WHERE pet_id = ? AND memory_subject_id = ?
                      AND source_kind = 'conversation_candidate'
                      AND status = 'candidate' AND prompt_eligible = 0
                      AND (expires_at IS NULL
                           OR julianday(expires_at) > julianday(?))
                    ORDER BY occurred_at DESC, evidence_id DESC
                    LIMIT 20
                    """,
                    (pet_id, memory_subject_id, now),
                )
            )
            preference = connection.execute(
                """
                SELECT growth_moments_enabled
                FROM companion_narrative_preferences
                WHERE pet_id = ? AND memory_subject_id = ?
                """,
                (pet_id, memory_subject_id),
            ).fetchone()
            growth_moments_enabled = (
                bool(preference["growth_moments_enabled"])
                if preference is not None
                else True
            )
        return {
            "active_adjustments": adjustments,
            "interaction_contracts": contracts,
            "chapters": chapters,
            "jobs": jobs,
            "pending_memory_candidates": pending_memory_candidates,
            "growth_moments_enabled": growth_moments_enabled,
        }

    def load_admin_subject_counts(
        self,
        memory_subject_ids: tuple[str, ...],
    ) -> Mapping[str, Mapping[str, object]]:
        subject_ids = tuple(dict.fromkeys(memory_subject_ids))
        if not subject_ids:
            return {}
        placeholders = ", ".join("?" for _ in subject_ids)
        counts: dict[str, dict[str, object]] = {
            subject_id: {
                "available": True,
                "evidence": 0,
                "candidate_facts": 0,
                "jobs": 0,
                "errors": 0,
            }
            for subject_id in subject_ids
        }
        with self.connection() as connection:
            evidence_rows = connection.execute(
                f"""
                SELECT memory_subject_id,
                       COUNT(*) AS evidence_count,
                       SUM(CASE WHEN status = 'candidate' THEN 1 ELSE 0 END)
                           AS candidate_count
                FROM companion_evidence
                WHERE memory_subject_id IN ({placeholders})
                GROUP BY memory_subject_id
                """,
                subject_ids,
            ).fetchall()
            job_rows = connection.execute(
                f"""
                SELECT json_extract(payload_json, '$.memory_subject_id') AS subject_id,
                       COUNT(*) AS job_count,
                       SUM(CASE WHEN status IN ('failed', 'dead') THEN 1 ELSE 0 END)
                           AS error_count
                FROM consolidation_jobs
                WHERE json_extract(payload_json, '$.memory_subject_id')
                      IN ({placeholders})
                GROUP BY json_extract(payload_json, '$.memory_subject_id')
                """,
                subject_ids,
            ).fetchall()
        for row in evidence_rows:
            subject_id = str(row["memory_subject_id"])
            counts[subject_id]["evidence"] = int(row["evidence_count"] or 0)
            counts[subject_id]["candidate_facts"] = int(row["candidate_count"] or 0)
        for row in job_rows:
            subject_id = str(row["subject_id"])
            counts[subject_id]["jobs"] = int(row["job_count"] or 0)
            counts[subject_id]["errors"] = int(row["error_count"] or 0)
        return counts

    def load_operator_diagnostics(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        now: str,
    ) -> Mapping[str, object]:
        """Return safe, subject-scoped history for the developer operator surface."""
        with self.connection() as connection:
            self._assert_owner_in_connection(
                connection,
                owner_user_id=owner_user_id,
                pet_id=pet_id,
            )
            rows = connection.execute(
                """
                SELECT evidence_id, ownership_scope, relationship_epoch_id,
                       kind, source_kind, source_ref, source_summary,
                       attribution, speaker_identity, confidence,
                       occurred_at, retention,
                       status, prompt_eligible, expires_at, fact_key,
                       importance, sensitivity, valid_from, valid_until
                FROM companion_evidence
                WHERE pet_id = ? AND memory_subject_id = ?
                ORDER BY occurred_at DESC, evidence_id DESC
                LIMIT 250
                """,
                (pet_id, memory_subject_id),
            ).fetchall()
            adjustments = tuple(
                {
                    "adjustment_id": row["adjustment_id"],
                    "dimension": row["dimension"],
                    "value": json.loads(row["value_json"])["value"],
                    "scope": row["scope"],
                    "behavior_key": row["behavior_key"],
                    "context_scope": row["context_scope"],
                    "direction": row["direction"],
                    "confidence": row["confidence"],
                    "evidence_ids": tuple(
                        link[0]
                        for link in connection.execute(
                            """
                            SELECT evidence_id FROM adjustment_evidence
                            WHERE adjustment_id = ? ORDER BY evidence_id
                            """,
                            (row["adjustment_id"],),
                        )
                    ),
                    "qualification_lineage": tuple(
                        {
                            "evidence_id": link["evidence_id"],
                            "qualification": link["qualification"],
                            "reason_code": link["reason_code"],
                            "qualifying_local_date": link["qualifying_local_date"],
                            "contributes_date": bool(link["contributes_date"]),
                        }
                        for link in connection.execute(
                            """
                            SELECT evidence_id, qualification, reason_code,
                                   qualifying_local_date, contributes_date
                            FROM adjustment_evidence_qualification
                            WHERE adjustment_id = ?
                            ORDER BY qualifying_local_date, evidence_id
                            """,
                            (row["adjustment_id"],),
                        )
                    ),
                }
                for row in connection.execute(
                    """
                    SELECT adjustment.adjustment_id, adjustment.dimension,
                           adjustment.value_json, adjustment.scope,
                           adjustment.behavior_key,
                           adjustment.context_scope,
                           adjustment.direction,
                           adjustment.confidence
                    FROM companion_adjustments AS adjustment
                    WHERE adjustment.pet_id = ?
                      AND adjustment.relationship_epoch_id = ?
                      AND adjustment.status = 'active'
                      AND EXISTS (
                        SELECT 1 FROM adjustment_evidence AS link
                        JOIN companion_evidence AS evidence
                          ON evidence.pet_id = link.pet_id
                         AND evidence.evidence_id = link.evidence_id
                        WHERE link.adjustment_id = adjustment.adjustment_id
                          AND evidence.memory_subject_id = ?
                      )
                      AND NOT EXISTS (
                        SELECT 1 FROM adjustment_evidence AS link
                        JOIN companion_evidence AS evidence
                          ON evidence.pet_id = link.pet_id
                         AND evidence.evidence_id = link.evidence_id
                        WHERE link.adjustment_id = adjustment.adjustment_id
                          AND evidence.memory_subject_id <> ?
                      )
                    ORDER BY adjustment.created_at, adjustment.adjustment_id
                    """,
                    (
                        pet_id,
                        relationship_epoch_id,
                        memory_subject_id,
                        memory_subject_id,
                    ),
                )
            )
            epochs = tuple(
                {
                    "epoch_id": row["epoch_id"],
                    "started_at": row["started_at"],
                    "ended_at": row["ended_at"],
                    "start_reason": row["start_reason"],
                    "end_reason": row["end_reason"],
                    "is_current": row["epoch_id"] == relationship_epoch_id,
                    "evidence_count": row["evidence_count"],
                }
                for row in connection.execute(
                    """
                    SELECT epoch.epoch_id, epoch.started_at, epoch.ended_at,
                           epoch.start_reason, epoch.end_reason,
                           COUNT(evidence.evidence_id) AS evidence_count
                    FROM relationship_epochs AS epoch
                    LEFT JOIN companion_evidence AS evidence
                      ON evidence.pet_id = epoch.pet_id
                     AND evidence.relationship_epoch_id = epoch.epoch_id
                     AND evidence.memory_subject_id = ?
                    WHERE epoch.pet_id = ?
                    GROUP BY epoch.epoch_id, epoch.started_at, epoch.ended_at,
                             epoch.start_reason, epoch.end_reason
                    ORDER BY epoch.started_at DESC, epoch.epoch_id DESC
                    """,
                    (memory_subject_id, pet_id),
                )
            )
            relations = tuple(
                {
                    "relation_id": row["relation_id"],
                    "relation_kind": row["relation_kind"],
                    "source_evidence_id": row["source_evidence_id"],
                    "target_evidence_id": row["target_evidence_id"],
                    "created_at": row["created_at"],
                }
                for row in connection.execute(
                    """
                    SELECT relation.relation_id, relation.relation_kind,
                           relation.source_evidence_id,
                           relation.target_evidence_id, relation.created_at
                    FROM evidence_relations AS relation
                    JOIN companion_evidence AS source
                      ON source.pet_id = relation.pet_id
                     AND source.evidence_id = relation.source_evidence_id
                    JOIN companion_evidence AS target
                      ON target.pet_id = relation.pet_id
                     AND target.evidence_id = relation.target_evidence_id
                    WHERE relation.pet_id = ?
                      AND source.memory_subject_id = ?
                      AND target.memory_subject_id = ?
                    ORDER BY relation.created_at DESC, relation.relation_id DESC
                    LIMIT 250
                    """,
                    (pet_id, memory_subject_id, memory_subject_id),
                )
            )
            capsules = tuple(
                {
                    "capsule_id": row["capsule_id"],
                    "relationship_epoch_id": row["relationship_epoch_id"],
                    "safe_summary": row["safe_summary"],
                    "interaction_outcome": row["interaction_outcome"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "expires_at": row["expires_at"],
                    "evidence_ids": tuple(
                        link[0]
                        for link in connection.execute(
                            """
                            SELECT evidence_id FROM capsule_evidence
                            WHERE capsule_id = ? ORDER BY evidence_id
                            """,
                            (row["capsule_id"],),
                        )
                    ),
                }
                for row in connection.execute(
                    """
                    SELECT capsule.capsule_id, capsule.relationship_epoch_id,
                           capsule.safe_summary, capsule.interaction_outcome,
                           capsule.status, capsule.created_at, capsule.expires_at
                    FROM session_capsules AS capsule
                    WHERE capsule.pet_id = ?
                      AND EXISTS (
                        SELECT 1 FROM capsule_evidence AS link
                        JOIN companion_evidence AS evidence
                          ON evidence.pet_id = link.pet_id
                         AND evidence.evidence_id = link.evidence_id
                        WHERE link.capsule_id = capsule.capsule_id
                          AND evidence.memory_subject_id = ?
                      )
                      AND NOT EXISTS (
                        SELECT 1 FROM capsule_evidence AS link
                        JOIN companion_evidence AS evidence
                          ON evidence.pet_id = link.pet_id
                         AND evidence.evidence_id = link.evidence_id
                        WHERE link.capsule_id = capsule.capsule_id
                          AND evidence.memory_subject_id <> ?
                      )
                    ORDER BY capsule.created_at DESC, capsule.capsule_id DESC
                    LIMIT 100
                    """,
                    (pet_id, memory_subject_id, memory_subject_id),
                )
            )
            job_counts = {
                row["status"]: row["count"]
                for row in connection.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM consolidation_jobs
                    WHERE pet_id = ? AND relationship_epoch_id = ?
                      AND json_extract(payload_json, '$.memory_subject_id') = ?
                    GROUP BY status ORDER BY status
                    """,
                    (pet_id, relationship_epoch_id, memory_subject_id),
                )
            }
            evidence_counts = {
                row["status"]: row["count"]
                for row in connection.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM companion_evidence
                    WHERE pet_id = ? AND memory_subject_id = ?
                    GROUP BY status ORDER BY status
                    """,
                    (pet_id, memory_subject_id),
                )
            }
            observations = tuple(
                {
                    "observation_id": row["observation_id"],
                    "kind": row["kind"],
                    "source_kind": row["source_kind"],
                    "source_ref": row["source_ref"],
                    "safe_summary": row["safe_summary"],
                    "occurred_at": row["occurred_at"],
                    "status": row["status"],
                    "evidence_ids": tuple(
                        link[0]
                        for link in connection.execute(
                            """
                            SELECT evidence_id
                            FROM observation_evidence
                            WHERE observation_id = ? AND pet_id = ?
                            ORDER BY evidence_id
                            """,
                            (row["observation_id"], pet_id),
                        )
                    ),
                }
                for row in connection.execute(
                    """
                    SELECT observation_id, kind, source_kind, source_ref,
                           safe_summary, occurred_at, status
                    FROM companion_observations
                    WHERE pet_id = ? AND memory_subject_id = ?
                    ORDER BY occurred_at DESC, observation_id DESC
                    LIMIT 250
                    """,
                    (pet_id, memory_subject_id),
                )
            )
            observation_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM companion_observations
                WHERE pet_id = ? AND memory_subject_id = ?
                """,
                (pet_id, memory_subject_id),
            ).fetchone()[0]
            temporary_turn_source_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM companion_turn_sources
                WHERE pet_id = ? AND memory_subject_id = ?
                """,
                (pet_id, memory_subject_id),
            ).fetchone()[0]
            context_message_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM companion_context_messages
                WHERE pet_id = ? AND memory_subject_id = ?
                  AND julianday(expires_at) > julianday(?)
                """,
                (pet_id, memory_subject_id, now),
            ).fetchone()[0]
            context_pin_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM companion_context_job_pins AS pin
                JOIN companion_context_messages AS message
                  ON message.message_id = pin.message_id
                 AND message.pet_id = pin.pet_id
                WHERE message.pet_id = ? AND message.memory_subject_id = ?
                """,
                (pet_id, memory_subject_id),
            ).fetchone()[0]
            semantic_evaluation_rows = connection.execute(
                """
                SELECT evaluation_id, mode, release_guard_reason, proposal_count,
                       action_counts_json, reason_counts_json,
                       claim_type_counts_json, legacy_fact_keys_digest,
                       legacy_fact_count, semantic_fact_keys_digest,
                       conflict_count, duration_ms, model, prompt_tokens,
                       completion_tokens, created_at
                FROM semantic_memory_evaluations
                WHERE pet_id = ? AND memory_subject_id = ?
                ORDER BY julianday(created_at) DESC, evaluation_id DESC
                LIMIT 100
                """,
                (pet_id, memory_subject_id),
            ).fetchall()
            retrieval_audit_rows = connection.execute(
                """
                SELECT turn_id, relationship_epoch_id, interaction_kind,
                       query_digest, hints_digest, candidate_count,
                       selected_evidence_ids_json, score_details_json,
                       duration_ms, created_at, expires_at
                FROM companion_retrieval_audits
                WHERE pet_id = ? AND memory_subject_id = ?
                  AND julianday(expires_at) > julianday(?)
                ORDER BY julianday(created_at) DESC, audit_id DESC
                LIMIT 20
                """,
                (pet_id, memory_subject_id, now),
            ).fetchall()
            relationship_stage_rows = connection.execute(
                """
                SELECT event_id, memory_subject_id, relationship_epoch_id,
                       previous_stage, relationship_stage,
                       quality_json, reason_codes_json, policy_version,
                       occurred_at
                FROM (
                    SELECT event_id, memory_subject_id,
                           relationship_epoch_id, previous_stage,
                           relationship_stage, quality_json,
                           reason_codes_json, policy_version, occurred_at,
                           rowid AS stage_rowid
                    FROM relationship_stage_events
                    WHERE pet_id = ? AND memory_subject_id = ?
                      AND relationship_epoch_id = ?
                      AND julianday(occurred_at) <= julianday(?)
                    ORDER BY rowid DESC
                    LIMIT 100
                )
                ORDER BY stage_rowid
                """,
                (pet_id, memory_subject_id, relationship_epoch_id, now),
            ).fetchall()
            initiative_opportunities = tuple(
                {
                    "opportunity_id": row["opportunity_id"],
                    "opportunity_kind": row["opportunity_kind"],
                    "reason_code": row["reason_code"],
                    "evidence_ids": _json_text_ids(row["evidence_ids_json"]),
                    "safe_brief": row["safe_brief"],
                    "due_at": row["due_at"],
                    "status": row["status"],
                    "attempt": int(row["attempt"]),
                    "next_attempt_at": row["next_attempt_at"],
                    "decision_id": row["decision_id"],
                    "delivery_id": row["delivery_id"],
                    "outcome_code": row["outcome_code"],
                    "updated_at": row["updated_at"],
                }
                for row in connection.execute(
                    """
                    SELECT opportunity_id, opportunity_kind, reason_code,
                           evidence_ids_json, safe_brief, due_at, status,
                           attempt, next_attempt_at, decision_id, delivery_id,
                           outcome_code, updated_at
                    FROM initiative_opportunities
                    WHERE pet_id = ? AND memory_subject_id = ?
                    ORDER BY due_at DESC, opportunity_id DESC
                    LIMIT 100
                    """,
                    (pet_id, memory_subject_id),
                )
            )
            initiative_counts = {
                row["status"]: int(row["count"])
                for row in connection.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM initiative_opportunities
                    WHERE pet_id = ? AND memory_subject_id = ?
                    GROUP BY status ORDER BY status
                    """,
                    (pet_id, memory_subject_id),
                )
            }
            connection_need_row = connection.execute(
                """
                SELECT need_kind, last_meaningful_interaction_at, last_bid_at,
                       pending_decision_id, ignored_streak, cooldown_until,
                       next_eligible_at, initiative_bias, relationship_stage,
                       threshold_seconds, version, updated_at
                FROM companion_relationship_needs
                WHERE owner_user_id = ? AND pet_id = ?
                  AND memory_subject_id = ? AND relationship_epoch_id = ?
                  AND need_kind = 'connection'
                """,
                (
                    owner_user_id,
                    pet_id,
                    memory_subject_id,
                    relationship_epoch_id,
                ),
            ).fetchone()
        pending_diagnostics = self.load_pending_observation_diagnostics(
            owner_user_id=owner_user_id,
            pet_id=pet_id,
            now=now,
        )
        pending_observations = pending_diagnostics["pending_observations"]
        pending_observation_counts = pending_diagnostics[
            "pending_observations_by_status"
        ]
        return {
            "evidence_timeline": tuple(
                {
                    "evidence_id": row["evidence_id"],
                    "ownership_scope": row["ownership_scope"],
                    "relationship_epoch_id": row["relationship_epoch_id"],
                    "kind": row["kind"],
                    "source_kind": row["source_kind"],
                    "source_ref": row["source_ref"],
                    "source_summary": row["source_summary"],
                    "attribution": row["attribution"],
                    "confidence": row["confidence"],
                    "occurred_at": row["occurred_at"],
                    "retention": row["retention"],
                    "status": row["status"],
                    "prompt_eligible": bool(row["prompt_eligible"]),
                    "expires_at": row["expires_at"],
                    "fact_key": row["fact_key"],
                    "importance": row["importance"],
                    "sensitivity": row["sensitivity"],
                    "valid_from": row["valid_from"],
                    "valid_until": row["valid_until"],
                    "is_current_epoch": row["relationship_epoch_id"]
                    in {None, relationship_epoch_id},
                }
                for row in rows
            ),
            "epochs": epochs,
            "relations": relations,
            "capsules": capsules,
            "adjustments": adjustments,
            "observations": observations,
            "retrieval_audits": tuple(
                _retrieval_audit_from_row(row) for row in retrieval_audit_rows
            ),
            "semantic_memory_evaluations": tuple(
                {
                    "evaluation_id": row["evaluation_id"],
                    "mode": row["mode"],
                    "release_guard_reason": row["release_guard_reason"],
                    "proposal_count": int(row["proposal_count"]),
                    "action_counts": json.loads(row["action_counts_json"]),
                    "reason_counts": json.loads(row["reason_counts_json"]),
                    "claim_type_counts": json.loads(row["claim_type_counts_json"]),
                    "legacy_fact_keys_digest": row["legacy_fact_keys_digest"],
                    "legacy_fact_count": int(row["legacy_fact_count"]),
                    "semantic_fact_keys_digest": row["semantic_fact_keys_digest"],
                    "conflict_count": int(row["conflict_count"]),
                    "duration_ms": float(row["duration_ms"]),
                    "model": row["model"],
                    "prompt_tokens": row["prompt_tokens"],
                    "completion_tokens": row["completion_tokens"],
                    "created_at": row["created_at"],
                }
                for row in semantic_evaluation_rows
            ),
            "relationship_stage_events": tuple(
                _relationship_stage_event_from_row(row)
                for row in relationship_stage_rows
            ),
            "connection_need": (
                dict(connection_need_row)
                if connection_need_row is not None
                else None
            ),
            "initiative_opportunities": initiative_opportunities,
            "pending_observations": pending_observations,
            "health": {
                "evidence_by_status": evidence_counts,
                "jobs_by_status": job_counts,
                "observations": int(observation_count),
                "temporary_turn_sources": int(temporary_turn_source_count),
                "temporary_context_messages": int(context_message_count),
                "temporary_context_pins": int(context_pin_count),
                "semantic_memory_evaluations": len(semantic_evaluation_rows),
                "retrieval_audits": len(retrieval_audit_rows),
                "relationship_stage_events": len(relationship_stage_rows),
                "initiative_opportunities_by_status": initiative_counts,
                "pending_observations": int(
                    pending_observation_counts.get("pending", 0)
                ),
                "pending_observations_by_status": pending_observation_counts,
            },
        }

    def record_initiative_feedback(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        decision_id: str,
        outcome: str,
        now: str,
        idempotency_key: str,
    ) -> CompanionControlResult:
        if outcome not in {"ignored", "accepted", "rejected", "delivery_failed"}:
            raise ValueError("initiative feedback outcome is invalid")
        request_digest = _control_request_digest(
            action="record_initiative_feedback",
            owner_user_id=owner_user_id,
            pet_id=pet_id,
            memory_subject_id=memory_subject_id,
            payload={
                "decision_id": decision_id,
                "outcome": outcome,
                "now": now,
            },
        )
        with self.pet_reflection_guard(pet_id):
            with self.connection() as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    self._assert_owner_in_connection(
                        connection,
                        owner_user_id=owner_user_id,
                        pet_id=pet_id,
                    )
                    existing = self._load_control_replay(
                        connection,
                        action="record_initiative_feedback",
                        pet_id=pet_id,
                        memory_subject_id=memory_subject_id,
                        request_digest=request_digest,
                        idempotency_key=idempotency_key,
                    )
                    if existing is not None:
                        connection.commit()
                        return existing
                    decision = connection.execute(
                        """
                        SELECT decision.relationship_epoch_id,
                               decision.reason_code,
                               decision.delivery_status,
                               decision.evidence_ids_json,
                               epoch.ended_at
                        FROM initiative_decisions AS decision
                        JOIN relationship_epochs AS epoch
                          ON epoch.epoch_id = decision.relationship_epoch_id
                         AND epoch.pet_id = decision.pet_id
                        WHERE decision.decision_id = ? AND decision.pet_id = ?
                        """,
                        (decision_id, pet_id),
                    ).fetchone()
                    if decision is None:
                        raise ValueError("initiative decision does not exist")
                    if decision["delivery_status"] == "invalidated":
                        raise ValueError("initiative decision is no longer active")
                    if decision["delivery_status"] in {
                        "ignored",
                        "accepted",
                        "rejected",
                        "delivery_failed",
                        "connection_responded",
                    }:
                        raise CompanionIdempotencyConflict(
                            "initiative decision already has a terminal outcome"
                        )
                    try:
                        decision_evidence_ids = tuple(
                            json.loads(decision["evidence_ids_json"])
                        )
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise ValueError(
                            "initiative decision Evidence is invalid"
                        ) from exc
                    if not decision_evidence_ids:
                        raise ValueError("initiative decision Evidence is invalid")
                    placeholders = ",".join("?" for _ in decision_evidence_ids)
                    subject_evidence_count = connection.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM companion_evidence
                        WHERE evidence_id IN ({placeholders})
                          AND pet_id = ?
                          AND memory_subject_id = ?
                        """,
                        (*decision_evidence_ids, pet_id, memory_subject_id),
                    ).fetchone()[0]
                    if int(subject_evidence_count) != len(set(decision_evidence_ids)):
                        raise PermissionError(
                            "initiative decision does not belong to this subject"
                        )
                    feedback_status = (
                        "active" if decision["ended_at"] is None else "superseded"
                    )
                    feedback_prompt_eligible = int(decision["ended_at"] is None)
                    cooldown_until = (
                        (
                            datetime.fromisoformat(now)
                            + _CONNECTION_REJECTION_COOLDOWN
                        ).isoformat()
                        if outcome == "rejected"
                        else None
                    )
                    observation_id = str(
                        uuid5(
                            NAMESPACE_URL,
                            f"companion-observation:{pet_id}:{idempotency_key}",
                        )
                    )
                    observation_payload = {
                        "decision_id": decision_id,
                        "outcome": outcome,
                        "reason_code": decision["reason_code"],
                    }
                    observation_summary = {
                        "ignored": "用户未响应这次主动陪伴。",
                        "accepted": "用户接受了这次主动陪伴。",
                        "rejected": "用户拒绝了这次主动陪伴。",
                        "delivery_failed": "这次主动陪伴投递失败。",
                    }[outcome]
                    observation_digest = hashlib.sha256(
                        _stable_json(
                            {
                                "owner_user_id": owner_user_id,
                                "pet_id": pet_id,
                                "memory_subject_id": memory_subject_id,
                                "kind": "initiative_feedback",
                                "source_kind": "initiative",
                                "source_ref": decision_id,
                                "occurred_at": now,
                                "payload": observation_payload,
                                "safe_summary": observation_summary,
                            }
                        ).encode("utf-8")
                    ).hexdigest()
                    existing_observation = connection.execute(
                        """
                        SELECT observation_digest
                        FROM companion_observations
                        WHERE idempotency_key = ?
                        """,
                        (idempotency_key,),
                    ).fetchone()
                    if existing_observation is not None:
                        raise CompanionIdempotencyConflict(
                            "initiative feedback observation already exists "
                            "without its control replay record"
                        )
                    connection.execute(
                        """
                        INSERT INTO companion_observations(
                            observation_id, idempotency_key, owner_user_id,
                            pet_id, memory_subject_id, kind, source_kind,
                            source_ref, payload_json, observation_digest,
                            safe_summary, occurred_at, status, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, 'initiative_feedback', 'initiative',
                            ?, ?, ?, ?, ?, 'recorded', ?
                        )
                        """,
                        (
                            observation_id,
                            idempotency_key,
                            owner_user_id,
                            pet_id,
                            memory_subject_id,
                            decision_id,
                            _stable_json(observation_payload),
                            observation_digest,
                            observation_summary,
                            now,
                            now,
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE initiative_decisions
                        SET delivery_status = ?, cooldown_until = COALESCE(?, cooldown_until)
                        WHERE decision_id = ?
                        """,
                        (outcome, cooldown_until, decision_id),
                    )
                    evidence_id = str(
                        uuid5(
                            NAMESPACE_URL,
                            f"xiaoxin:initiative-feedback:{idempotency_key}",
                        )
                    )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO companion_evidence(
                            evidence_id, pet_id, memory_subject_id,
                            ownership_scope, relationship_epoch_id, kind,
                            content_json, source_kind, source_ref, source_summary,
                            attribution, confidence, occurred_at, retention,
                            status, prompt_eligible, created_at
                        ) VALUES (?, ?, ?, 'relationship', ?,
                                  'initiative_feedback', ?, 'initiative', ?, ?,
                                  ?, 1.0, ?,
                                  'long_term', ?, ?, ?)
                        """,
                        (
                            evidence_id,
                            pet_id,
                            memory_subject_id,
                            decision["relationship_epoch_id"],
                            _stable_json(
                                {
                                    "outcome": outcome,
                                    "reason_code": decision["reason_code"],
                                }
                            ),
                            decision_id,
                            observation_summary,
                            (
                                "observed_delivery_outcome"
                                if outcome == "delivery_failed"
                                else "observed_user_feedback"
                            ),
                            now,
                            feedback_status,
                            feedback_prompt_eligible,
                            now,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO observation_evidence(
                            observation_id, evidence_id, pet_id
                        ) VALUES (?, ?, ?)
                        """,
                        (observation_id, evidence_id, pet_id),
                    )
                    opportunity = connection.execute(
                        """
                        SELECT opportunity_kind
                        FROM initiative_opportunities
                        WHERE decision_id = ?
                        """,
                        (decision_id,),
                    ).fetchone()
                    if (
                        opportunity is not None
                        and opportunity["opportunity_kind"] == "connection_bid"
                        and outcome != "delivery_failed"
                    ):
                        self._apply_connection_feedback_in_connection(
                            connection,
                            owner_user_id=owner_user_id,
                            pet_id=pet_id,
                            memory_subject_id=memory_subject_id,
                            relationship_epoch_id=decision["relationship_epoch_id"],
                            decision_id=decision_id,
                            outcome=outcome,
                            now=now,
                        )
                    result = CompanionControlResult(
                        action="record_initiative_feedback",
                        status="applied",
                        retained=1,
                    )
                    self._insert_control_record(
                        connection,
                        pet_id=pet_id,
                        memory_subject_id=memory_subject_id,
                        action="record_initiative_feedback",
                        payload={
                            "decision_id": decision_id,
                            "outcome": outcome,
                        },
                        request_digest=request_digest,
                        result=result,
                        now=now,
                        idempotency_key=idempotency_key,
                    )
                    connection.commit()
                    return result
                except Exception:
                    connection.rollback()
                    raise

    def get_academic_state(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
    ) -> Mapping[str, object] | None:
        with self.connection() as connection:
            owner = connection.execute(
                "SELECT owner_user_id FROM companion_pets WHERE pet_id = ?",
                (pet_id,),
            ).fetchone()
            if owner is None:
                return None
            if owner["owner_user_id"] != owner_user_id:
                raise PermissionError("owner does not control this personal pet")
            row = connection.execute(
                """
                SELECT academic_stage, academic_status, effective_at,
                       source_revision, transition_id
                FROM companion_academic_states
                WHERE pet_id = ? AND memory_subject_id = ?
                """,
                (pet_id, memory_subject_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "academic_stage": row["academic_stage"],
            "academic_status": row["academic_status"],
            "effective_at": row["effective_at"],
            "source_revision": int(row["source_revision"]),
            "transition_id": row["transition_id"],
        }

    def list_academic_transitions(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
    ) -> tuple[Mapping[str, object], ...]:
        with self.connection() as connection:
            self._assert_owner_in_connection(
                connection,
                owner_user_id=owner_user_id,
                pet_id=pet_id,
            )
            rows = connection.execute(
                """
                SELECT transition_id, relationship_epoch_id,
                       from_stage, from_status, to_stage, to_status,
                       transition_kind, effective_at, source_revision,
                       source_kind, evidence_id, growth_eligible
                FROM companion_academic_transitions
                WHERE pet_id = ? AND memory_subject_id = ?
                ORDER BY source_revision, transition_id
                """,
                (pet_id, memory_subject_id),
            ).fetchall()
        return tuple(
            {
                "transition_id": row["transition_id"],
                "relationship_epoch_id": row["relationship_epoch_id"],
                "from_stage": row["from_stage"],
                "from_status": row["from_status"],
                "to_stage": row["to_stage"],
                "to_status": row["to_status"],
                "transition_kind": row["transition_kind"],
                "effective_at": row["effective_at"],
                "source_revision": int(row["source_revision"]),
                "source_kind": row["source_kind"],
                "evidence_id": row["evidence_id"],
                "growth_eligible": bool(row["growth_eligible"]),
            }
            for row in rows
        )

    @staticmethod
    def _narrative_kind_for_transition(transition) -> str | None:
        if transition.kind == "graduation":
            return "graduation"
        if transition.growth_eligible:
            return "academic_growth"
        if transition.kind == "regression":
            return "academic_reorientation"
        return None

    @staticmethod
    def _create_narrative_boundary_in_connection(
        connection: sqlite3.Connection,
        *,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        boundary_kind: str,
        source_key: str,
        transition_id: str | None,
        evidence_id: str | None,
        from_stage: str,
        to_stage: str,
        xiaoxin_age: int | None,
        effective_at: str,
        created_at: str,
        anniversary_number: int | None = None,
    ) -> str:
        boundary_id = str(
            uuid5(
                NAMESPACE_URL,
                f"xiaoxin:narrative-boundary:{pet_id}:{memory_subject_id}:{source_key}",
            )
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO companion_narrative_boundaries(
                boundary_id, pet_id, memory_subject_id,
                relationship_epoch_id, boundary_kind, source_key,
                transition_id, evidence_id, from_stage, to_stage,
                xiaoxin_age, anniversary_number, effective_at,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
            """,
            (
                boundary_id,
                pet_id,
                memory_subject_id,
                relationship_epoch_id,
                boundary_kind,
                source_key,
                transition_id,
                evidence_id,
                from_stage,
                to_stage,
                xiaoxin_age,
                anniversary_number,
                effective_at,
                created_at,
            ),
        )
        return boundary_id

    @staticmethod
    def _growth_summary(
        *,
        primary_kind: str,
        from_stage: str,
        to_stage: str,
        xiaoxin_age: int | None,
        anniversary_number: int | None = None,
    ) -> str:
        labels = {
            "freshman": "大一",
            "sophomore": "大二",
            "junior": "大三",
            "senior": "大四",
            "unknown": "当前阶段",
        }
        if primary_kind == "anniversary":
            return f"今天是陪伴第{anniversary_number}周年。"
        if primary_kind == "graduation":
            return f"你毕业了，小芯保持{xiaoxin_age}岁。"
        if primary_kind == "academic_reorientation":
            return f"学业阶段调整为{labels[to_stage]}，小芯按真实阶段同行。"
        return (
            f"你从{labels[from_stage]}进入{labels[to_stage]}了，"
            f"小芯现在{xiaoxin_age}岁。"
        )

    @staticmethod
    def _create_growth_moment_in_connection(
        connection: sqlite3.Connection,
        *,
        boundary_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        evidence_id: str,
        primary_kind: str,
        from_stage: str,
        to_stage: str,
        xiaoxin_age: int,
        occurred_at: str,
        created_at: str,
        anniversary_number: int | None = None,
        mode: str = "boundary_only",
    ) -> str:
        moment_id = str(uuid5(NAMESPACE_URL, f"growth-moment:{boundary_id}"))
        safe_summary = CompanionStore._growth_summary(
            primary_kind=primary_kind,
            from_stage=from_stage,
            to_stage=to_stage,
            xiaoxin_age=xiaoxin_age,
            anniversary_number=anniversary_number,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO companion_growth_moments(
                moment_id, pet_id, memory_subject_id,
                relationship_epoch_id, evidence_id, from_stage,
                to_stage, xiaoxin_age, safe_summary,
                continuity_evidence_count, expression_status,
                occurred_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'pending', ?, ?)
            """,
            (
                moment_id,
                pet_id,
                memory_subject_id,
                relationship_epoch_id,
                evidence_id,
                from_stage,
                to_stage,
                xiaoxin_age,
                safe_summary,
                occurred_at,
                created_at,
            ),
        )
        expires_at = (
            datetime.fromisoformat(occurred_at)
            + timedelta(days=_NARRATIVE_WINDOW_DAYS[primary_kind])
        ).isoformat()
        preference = connection.execute(
            """
            SELECT growth_moments_enabled
            FROM companion_narrative_preferences
            WHERE pet_id = ? AND memory_subject_id = ?
            """,
            (pet_id, memory_subject_id),
        ).fetchone()
        lifecycle_status = (
            "suppressed"
            if preference is not None and not bool(preference["growth_moments_enabled"])
            else "active"
        )
        reason_code = (
            "growth_moments_disabled" if lifecycle_status == "suppressed" else None
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO companion_growth_moment_metadata(
                moment_id, primary_kind, mode, lifecycle_status, expires_at,
                reason_code
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                moment_id,
                primary_kind,
                mode,
                lifecycle_status,
                expires_at,
                reason_code,
            ),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO companion_growth_moment_boundaries(
                moment_id, boundary_id
            ) VALUES (?, ?)
            """,
            (moment_id, boundary_id),
        )
        return moment_id

    @staticmethod
    def _merge_nearby_anniversaries_into_growth_moment(
        connection: sqlite3.Connection,
        *,
        moment_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        occurred_at: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT boundary.boundary_id, old_moment.moment_id AS old_moment_id,
                   old_moment.expression_status AS old_expression_status,
                   old_moment.expressed_at AS old_expressed_at
            FROM companion_narrative_boundaries AS boundary
            LEFT JOIN companion_growth_moment_boundaries AS old_link
              ON old_link.boundary_id = boundary.boundary_id
            LEFT JOIN companion_growth_moments AS old_moment
              ON old_moment.moment_id = old_link.moment_id
            LEFT JOIN companion_growth_moment_metadata AS old_metadata
              ON old_metadata.moment_id = old_moment.moment_id
             AND old_metadata.lifecycle_status = 'active'
            WHERE boundary.pet_id = ?
              AND boundary.memory_subject_id = ?
              AND boundary.relationship_epoch_id = ?
              AND boundary.boundary_kind = 'anniversary'
              AND boundary.status = 'active'
              AND ABS(julianday(boundary.effective_at) - julianday(?)) <= 30
              AND (old_moment.moment_id IS NULL OR old_metadata.moment_id IS NOT NULL)
            ORDER BY boundary.effective_at, boundary.boundary_id
            """,
            (pet_id, memory_subject_id, relationship_epoch_id, occurred_at),
        ).fetchall()
        for row in rows:
            connection.execute(
                """
                INSERT OR IGNORE INTO companion_growth_moment_boundaries(
                    moment_id, boundary_id
                ) VALUES (?, ?)
                """,
                (moment_id, row["boundary_id"]),
            )
            old_moment_id = row["old_moment_id"]
            if old_moment_id is None or old_moment_id == moment_id:
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO companion_growth_moment_evidence(
                    moment_id, evidence_id, pet_id
                )
                SELECT ?, evidence_id, pet_id
                FROM companion_growth_moment_evidence
                WHERE moment_id = ?
                """,
                (moment_id, old_moment_id),
            )
            connection.execute(
                """
                UPDATE companion_growth_moment_metadata
                SET lifecycle_status = 'suppressed',
                    reason_code = 'merged_into_academic_moment'
                WHERE moment_id = ? AND lifecycle_status = 'active'
                """,
                (old_moment_id,),
            )
            connection.execute(
                """
                UPDATE companion_growth_moments
                SET expression_status = CASE
                      WHEN expression_status = 'reserved'
                      THEN 'pending' ELSE expression_status END,
                    reserved_by_turn_id = NULL,
                    lease_until = NULL
                WHERE moment_id = ?
                """,
                (old_moment_id,),
            )
            if row["old_expression_status"] == "expressed":
                connection.execute(
                    """
                    UPDATE companion_growth_moments
                    SET expression_status = 'expressed',
                        expressed_at = COALESCE(?, expressed_at),
                        reserved_by_turn_id = NULL,
                        lease_until = NULL
                    WHERE moment_id = ?
                    """,
                    (row["old_expressed_at"], moment_id),
                )
        evidence_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM companion_growth_moment_evidence AS link
            JOIN companion_evidence AS evidence
              ON evidence.evidence_id = link.evidence_id
             AND evidence.pet_id = link.pet_id
            WHERE link.moment_id = ? AND evidence.status = 'active'
            """,
            (moment_id,),
        ).fetchone()[0]
        if evidence_count:
            connection.execute(
                """
                UPDATE companion_growth_moment_metadata
                SET mode = 'evidence_backed'
                WHERE moment_id = ? AND lifecycle_status = 'active'
                """,
                (moment_id,),
            )
            connection.execute(
                """
                UPDATE companion_growth_moments
                SET continuity_evidence_count = ?
                WHERE moment_id = ?
                """,
                (min(int(evidence_count), 3), moment_id),
            )

    @staticmethod
    def _attach_chapter_to_growth_moment_in_connection(
        connection: sqlite3.Connection,
        *,
        boundary_id: str,
        evidence_ids: tuple[str, ...],
        now: str,
    ) -> str | None:
        boundary = connection.execute(
            "SELECT * FROM companion_narrative_boundaries WHERE boundary_id = ?",
            (boundary_id,),
        ).fetchone()
        if boundary is None or boundary["status"] != "active":
            return None
        moment = connection.execute(
            """
            SELECT moment.moment_id
            FROM companion_growth_moments AS moment
            JOIN companion_growth_moment_boundaries AS link
              ON link.moment_id = moment.moment_id
            WHERE link.boundary_id = ?
            ORDER BY moment.occurred_at, moment.moment_id
            LIMIT 1
            """,
            (boundary_id,),
        ).fetchone()
        if moment is None:
            if (
                boundary["boundary_kind"] != "anniversary"
                or not boundary["evidence_id"]
            ):
                return None
            moment_id = CompanionStore._create_growth_moment_in_connection(
                connection,
                boundary_id=boundary_id,
                pet_id=boundary["pet_id"],
                memory_subject_id=boundary["memory_subject_id"],
                relationship_epoch_id=boundary["relationship_epoch_id"],
                evidence_id=boundary["evidence_id"],
                primary_kind=boundary["boundary_kind"],
                from_stage=boundary["from_stage"],
                to_stage=boundary["to_stage"],
                xiaoxin_age=int(boundary["xiaoxin_age"]),
                occurred_at=boundary["effective_at"],
                created_at=now,
                anniversary_number=int(boundary["anniversary_number"]),
                mode="evidence_backed",
            )
        else:
            moment_id = moment["moment_id"]
        connection.executemany(
            """
            INSERT OR IGNORE INTO companion_growth_moment_evidence(
                moment_id, evidence_id, pet_id
            ) VALUES (?, ?, ?)
            """,
            (
                (moment_id, evidence_id, boundary["pet_id"])
                for evidence_id in evidence_ids
            ),
        )
        connection.execute(
            """
            UPDATE companion_growth_moment_metadata
            SET mode = 'evidence_backed'
            WHERE moment_id = ? AND lifecycle_status = 'active'
            """,
            (moment_id,),
        )
        connection.execute(
            """
            UPDATE companion_growth_moments
            SET continuity_evidence_count = ?
            WHERE moment_id = ?
            """,
            (len(evidence_ids), moment_id),
        )
        return moment_id

    @staticmethod
    def _enqueue_narrative_job_in_connection(
        connection: sqlite3.Connection,
        *,
        pet_id: str,
        relationship_epoch_id: str,
        memory_subject_id: str,
        boundary_id: str,
        boundary_kind: str,
        from_stage: str,
        to_stage: str,
        period_start: str,
        period_end: str,
        evidence_id: str,
        source_revision: int | None,
        now: str,
        idempotency_suffix: str | None = None,
    ) -> str:
        idempotency_key = f"narrative-boundary:{boundary_id}"
        if idempotency_suffix:
            idempotency_key = f"{idempotency_key}:{idempotency_suffix}"
        job_id = str(uuid5(NAMESPACE_URL, idempotency_key))
        connection.execute(
            """
            INSERT OR IGNORE INTO consolidation_jobs(
                job_id, pet_id, relationship_epoch_id, job_kind,
                idempotency_key, payload_json, status, due_at,
                schema_version, created_at, updated_at
            ) VALUES (?, ?, ?, 'academic_stage_changed', ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (
                job_id,
                pet_id,
                relationship_epoch_id,
                idempotency_key,
                _stable_json(
                    {
                        "memory_subject_id": memory_subject_id,
                        "boundary_id": boundary_id,
                        "boundary_kind": boundary_kind,
                        "from_stage": from_stage,
                        "to_stage": to_stage,
                        "chapter_stage": from_stage,
                        "period_start": period_start,
                        "period_end": period_end,
                        "effective_at": period_end,
                        "source_revision": source_revision,
                        "evidence_id": evidence_id,
                    }
                ),
                now,
                "companion-narrative-boundary-v1",
                now,
                now,
            ),
        )
        return job_id

    @staticmethod
    def _enqueue_chapter_rebuilds_after_evidence_change(
        connection: sqlite3.Connection,
        *,
        evidence_ids: tuple[str, ...],
        now: str,
    ) -> None:
        unique_ids = tuple(dict.fromkeys(evidence_ids))
        if not unique_ids:
            return
        placeholders = ",".join("?" for _ in unique_ids)
        rows = connection.execute(
            f"""
            SELECT DISTINCT boundary.boundary_id, boundary.pet_id,
                   boundary.memory_subject_id, boundary.relationship_epoch_id,
                   boundary.boundary_kind, boundary.from_stage,
                   boundary.to_stage, boundary.effective_at,
                   boundary.evidence_id, transition.source_revision,
                   chapter.period_start AS chapter_start,
                   chapter.period_end AS chapter_end
            FROM companion_chapters AS chapter
            JOIN chapter_evidence AS cited
              ON cited.chapter_id = chapter.chapter_id
            JOIN companion_chapter_boundaries AS chapter_boundary
              ON chapter_boundary.chapter_id = chapter.chapter_id
            JOIN companion_narrative_boundaries AS boundary
              ON boundary.boundary_id = chapter_boundary.boundary_id
            LEFT JOIN companion_academic_transitions AS transition
              ON transition.transition_id = boundary.transition_id
             AND transition.pet_id = boundary.pet_id
            WHERE cited.evidence_id IN ({placeholders})
              AND chapter.status = 'invalidated'
              AND boundary.status = 'active'
              AND boundary.evidence_id IS NOT NULL
            ORDER BY boundary.effective_at, boundary.boundary_id
            """,
            unique_ids,
        ).fetchall()
        for row in rows:
            period_start = row["chapter_start"]
            period_end = row["chapter_end"] or row["effective_at"]
            if not isinstance(period_start, str) or not isinstance(period_end, str):
                continue
            CompanionStore._enqueue_narrative_job_in_connection(
                connection,
                pet_id=row["pet_id"],
                relationship_epoch_id=row["relationship_epoch_id"],
                memory_subject_id=row["memory_subject_id"],
                boundary_id=row["boundary_id"],
                boundary_kind=row["boundary_kind"],
                from_stage=row["from_stage"],
                to_stage=row["to_stage"],
                period_start=period_start,
                period_end=period_end,
                evidence_id=row["evidence_id"],
                source_revision=(
                    int(row["source_revision"])
                    if row["source_revision"] is not None
                    else None
                ),
                now=now,
                idempotency_suffix=f"rebuild-after-evidence:{unique_ids[0]}",
            )

    def ensure_anniversary_boundaries(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        academic_stage: str,
        now: str,
    ) -> int:
        current = datetime.fromisoformat(now).astimezone(_SHANGHAI_TIMEZONE)
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._assert_owner_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                )
                pet = connection.execute(
                    "SELECT created_at FROM companion_pets WHERE pet_id = ?",
                    (pet_id,),
                ).fetchone()
                epoch = connection.execute(
                    """
                    SELECT started_at FROM relationship_epochs
                    WHERE epoch_id = ? AND pet_id = ? AND ended_at IS NULL
                    """,
                    (relationship_epoch_id, pet_id),
                ).fetchone()
                if pet is None or epoch is None:
                    connection.commit()
                    return 0
                created = datetime.fromisoformat(pet["created_at"]).astimezone(
                    _SHANGHAI_TIMEZONE
                )
                if current < created:
                    connection.commit()
                    return 0
                created_count = 0
                for number in range(1, current.year - created.year + 1):
                    target_year = created.year + number
                    try:
                        anniversary = created.replace(year=target_year)
                    except ValueError:
                        anniversary = created.replace(
                            year=target_year,
                            month=2,
                            day=28,
                        )
                    if anniversary > current:
                        continue
                    evidence_id = str(
                        uuid5(
                            NAMESPACE_URL,
                            f"xiaoxin:anniversary-evidence:{pet_id}:"
                            f"{memory_subject_id}:{number}",
                        )
                    )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO companion_evidence(
                            evidence_id, pet_id, memory_subject_id,
                            ownership_scope, relationship_epoch_id, kind,
                            content_json, source_kind, source_ref,
                            source_summary, attribution, confidence,
                            occurred_at, retention, status, prompt_eligible,
                            created_at
                        ) VALUES (
                            ?, ?, ?, 'relationship', ?, 'system_event', ?,
                            'lifecycle', ?, ?, 'system_asserted', 1.0,
                            ?, 'persistent', 'active', 0, ?
                        )
                        """,
                        (
                            evidence_id,
                            pet_id,
                            memory_subject_id,
                            relationship_epoch_id,
                            _stable_json(
                                {
                                    "event": "companion_anniversary",
                                    "anniversary_number": number,
                                }
                            ),
                            f"companion:anniversary:{number}",
                            f"陪伴第{number}周年已到。",
                            anniversary.isoformat(),
                            now,
                        ),
                    )
                    age = xiaoxin_age_for_stage(academic_stage)
                    source_key = f"anniversary:{number}"
                    boundary_id = self._create_narrative_boundary_in_connection(
                        connection,
                        pet_id=pet_id,
                        memory_subject_id=memory_subject_id,
                        relationship_epoch_id=relationship_epoch_id,
                        boundary_kind="anniversary",
                        source_key=source_key,
                        transition_id=None,
                        evidence_id=evidence_id,
                        from_stage=academic_stage,
                        to_stage=academic_stage,
                        xiaoxin_age=age,
                        anniversary_number=number,
                        effective_at=anniversary.isoformat(),
                        created_at=now,
                    )
                    existing_link = connection.execute(
                        """
                        SELECT 1 FROM companion_growth_moment_boundaries
                        WHERE boundary_id = ?
                        """,
                        (boundary_id,),
                    ).fetchone()
                    if existing_link is not None:
                        continue
                    created_count += 1
                    if age is None:
                        continue
                    nearby = connection.execute(
                        """
                        SELECT moment.moment_id
                        FROM companion_growth_moments AS moment
                        JOIN companion_growth_moment_metadata AS metadata
                          ON metadata.moment_id = moment.moment_id
                        WHERE moment.pet_id = ?
                          AND moment.memory_subject_id = ?
                          AND moment.relationship_epoch_id = ?
                          AND metadata.lifecycle_status = 'active'
                          AND metadata.primary_kind IN (
                            'academic_growth', 'graduation'
                          )
                          AND ABS(
                            julianday(moment.occurred_at) - julianday(?)
                          ) <= 30
                        ORDER BY CASE metadata.primary_kind
                                   WHEN 'graduation' THEN 0 ELSE 1 END,
                                 moment.occurred_at, moment.moment_id
                        LIMIT 1
                        """,
                        (
                            pet_id,
                            memory_subject_id,
                            relationship_epoch_id,
                            anniversary.isoformat(),
                        ),
                    ).fetchone()
                    if nearby is not None:
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO companion_growth_moment_boundaries(
                                moment_id, boundary_id
                            ) VALUES (?, ?)
                            """,
                            (nearby["moment_id"], boundary_id),
                        )
                        continue
                    if current > anniversary + timedelta(days=14):
                        continue
                    academic = connection.execute(
                        """
                        SELECT effective_at, source_revision
                        FROM companion_academic_states
                        WHERE pet_id = ? AND memory_subject_id = ?
                        """,
                        (pet_id, memory_subject_id),
                    ).fetchone()
                    period_start = epoch["started_at"]
                    source_revision = None
                    if academic is not None:
                        period_start = max(period_start, academic["effective_at"])
                        source_revision = int(academic["source_revision"])
                    self._enqueue_narrative_job_in_connection(
                        connection,
                        pet_id=pet_id,
                        relationship_epoch_id=relationship_epoch_id,
                        memory_subject_id=memory_subject_id,
                        boundary_id=boundary_id,
                        boundary_kind="anniversary",
                        from_stage=academic_stage,
                        to_stage=academic_stage,
                        period_start=period_start,
                        period_end=anniversary.isoformat(),
                        evidence_id=evidence_id,
                        source_revision=source_revision,
                        now=now,
                    )
                connection.commit()
                return created_count
            except Exception:
                connection.rollback()
                raise

    def set_growth_moments_enabled(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        enabled: bool,
        now: str,
        idempotency_key: str,
    ) -> CompanionControlResult:
        request_digest = _control_request_digest(
            action="set_growth_moments_enabled",
            owner_user_id=owner_user_id,
            pet_id=pet_id,
            memory_subject_id=memory_subject_id,
            payload={"enabled": enabled, "now": now},
        )
        with self.pet_reflection_guard(pet_id):
            with self.connection() as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    self._assert_owner_in_connection(
                        connection,
                        owner_user_id=owner_user_id,
                        pet_id=pet_id,
                    )
                    existing = self._load_control_replay(
                        connection,
                        action="set_growth_moments_enabled",
                        pet_id=pet_id,
                        memory_subject_id=memory_subject_id,
                        request_digest=request_digest,
                        idempotency_key=idempotency_key,
                    )
                    if existing is not None:
                        connection.commit()
                        return existing
                    connection.execute(
                        """
                        INSERT INTO companion_narrative_preferences(
                            pet_id, memory_subject_id,
                            growth_moments_enabled, updated_at
                        ) VALUES (?, ?, ?, ?)
                        ON CONFLICT(pet_id, memory_subject_id) DO UPDATE SET
                            growth_moments_enabled = excluded.growth_moments_enabled,
                            updated_at = excluded.updated_at
                        """,
                        (pet_id, memory_subject_id, int(enabled), now),
                    )
                    deactivated = 0
                    if not enabled:
                        suppressed = connection.execute(
                            """
                            UPDATE companion_growth_moment_metadata
                            SET lifecycle_status = 'suppressed',
                                reason_code = 'growth_moments_disabled'
                            WHERE lifecycle_status = 'active'
                              AND moment_id IN (
                                SELECT moment_id
                                FROM companion_growth_moments
                                WHERE pet_id = ? AND memory_subject_id = ?
                              )
                            """,
                            (pet_id, memory_subject_id),
                        )
                        deactivated = suppressed.rowcount
                        connection.execute(
                            """
                            UPDATE companion_growth_moments
                            SET expression_status = CASE
                                  WHEN expression_status = 'reserved'
                                  THEN 'pending' ELSE expression_status END,
                                reserved_by_turn_id = NULL,
                                lease_until = NULL
                            WHERE pet_id = ? AND memory_subject_id = ?
                              AND expression_status = 'reserved'
                            """,
                            (pet_id, memory_subject_id),
                        )
                    result = CompanionControlResult(
                        action="set_growth_moments_enabled",
                        status="applied",
                        deactivated=deactivated,
                    )
                    self._insert_control_record(
                        connection,
                        pet_id=pet_id,
                        memory_subject_id=memory_subject_id,
                        action="set_growth_moments_enabled",
                        payload={"enabled": enabled},
                        request_digest=request_digest,
                        result=result,
                        now=now,
                        idempotency_key=idempotency_key,
                    )
                    connection.commit()
                    return result
                except Exception:
                    connection.rollback()
                    raise

    def sync_academic_stage(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        academic_stage: str,
        now: str,
        academic_status: str = "active",
        transition_kind: str | None = None,
        effective_at: str | None = None,
        source_revision: int | None = None,
        clear_stage: bool = False,
    ) -> tuple[str | None, str | None]:
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._assert_owner_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                )
                result = self._sync_academic_stage_in_connection(
                    connection,
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    relationship_epoch_id=relationship_epoch_id,
                    academic_stage=academic_stage,
                    now=now,
                    academic_status=academic_status,
                    transition_kind=transition_kind,
                    effective_at=effective_at,
                    source_revision=source_revision,
                    clear_stage=clear_stage,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _sync_academic_stage_in_connection(
        connection: sqlite3.Connection,
        *,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        academic_stage: str,
        now: str,
        academic_status: str = "active",
        transition_kind: str | None = None,
        effective_at: str | None = None,
        source_revision: int | None = None,
        clear_stage: bool = False,
        initial_only: bool = False,
    ) -> tuple[str | None, str | None]:
        if academic_status not in ACADEMIC_STATUSES:
            raise ValueError("academic_status is invalid")
        if (
            transition_kind is not None
            and transition_kind not in ACADEMIC_TRANSITION_KINDS
        ):
            raise ValueError("transition_kind is invalid")
        effective_at = effective_at or now
        current = connection.execute(
            """
            SELECT academic_stage, academic_status, effective_at,
                   source_revision, transition_id
            FROM companion_academic_states
            WHERE pet_id = ? AND memory_subject_id = ?
            """,
            (pet_id, memory_subject_id),
        ).fetchone()
        if current is not None and initial_only:
            return None, None

        previous = None
        if current is not None:
            previous = AcademicState(
                stage=current["academic_stage"],
                status=current["academic_status"],
                effective_at=current["effective_at"],
                source_revision=int(current["source_revision"]),
            )
        if source_revision is not None and (
            isinstance(source_revision, bool)
            or not isinstance(source_revision, int)
            or source_revision < 0
        ):
            raise ValueError("source_revision must be a non-negative integer")

        effective_stage = academic_stage
        if previous is not None and academic_stage == "unknown" and not clear_stage:
            effective_stage = previous.stage
        if source_revision is None:
            if not initial_only or previous is not None:
                raise ValueError("source_revision is required for academic updates")
            source_revision = 0
        elif previous is not None and source_revision < previous.source_revision:
            return None, None
        elif previous is not None and source_revision == previous.source_revision:
            recorded = connection.execute(
                """
                SELECT to_stage, to_status, transition_kind, effective_at
                FROM companion_academic_transitions
                WHERE transition_id = ?
                """,
                (current["transition_id"],),
            ).fetchone()
            if (
                recorded is not None
                and recorded["to_stage"] == effective_stage
                and recorded["to_status"] == academic_status
                and recorded["effective_at"] == effective_at
                and (
                    transition_kind is None
                    or recorded["transition_kind"] == transition_kind
                )
            ):
                return None, None
            raise CompanionIdempotencyConflict(
                "academic source revision was already applied with different content"
            )

        transition = resolve_academic_transition(
            previous=previous,
            stage=academic_stage,
            status=academic_status,
            effective_at=effective_at,
            source_revision=source_revision,
            requested_kind=transition_kind,
            clear_stage=clear_stage,
        )
        active_evidence = connection.execute(
            """
            SELECT evidence_id
            FROM companion_evidence
            WHERE pet_id = ? AND memory_subject_id = ?
              AND ownership_scope = 'user'
              AND kind = 'system_event'
              AND source_ref = 'identity:student_profile'
              AND status = 'active'
            ORDER BY occurred_at DESC, evidence_id DESC
            LIMIT 1
            """,
            (pet_id, memory_subject_id),
        ).fetchone()
        if active_evidence is not None:
            connection.execute(
                """
                UPDATE companion_evidence
                SET status = 'superseded', prompt_eligible = 0
                WHERE evidence_id = ?
                """,
                (active_evidence["evidence_id"],),
            )
        evidence_id = str(
            uuid5(
                NAMESPACE_URL,
                f"xiaoxin:academic-state:{pet_id}:{memory_subject_id}:{source_revision}",
            )
        )
        transition_id = str(
            uuid5(
                NAMESPACE_URL,
                f"xiaoxin:academic-transition:{pet_id}:"
                f"{memory_subject_id}:{source_revision}",
            )
        )
        connection.execute(
            """
            INSERT INTO companion_evidence(
                evidence_id, pet_id, memory_subject_id, ownership_scope,
                relationship_epoch_id, kind, content_json, source_kind,
                source_ref, source_summary, attribution, confidence,
                occurred_at, retention, status, prompt_eligible, created_at
            ) VALUES (
                ?, ?, ?, 'user', NULL, 'system_event', ?, 'identity',
                'identity:student_profile', ?, 'system_asserted', 1.0,
                ?, 'persistent', 'active', 0, ?
            )
            """,
            (
                evidence_id,
                pet_id,
                memory_subject_id,
                _stable_json(
                    {
                        "event": f"academic_{transition.kind}",
                        "academic_stage": transition.current.stage,
                        "academic_status": transition.current.status,
                        "transition_kind": transition.kind,
                        "effective_at": transition.current.effective_at,
                        "source_revision": transition.current.source_revision,
                    }
                ),
                "学生权威资料已同步。",
                transition.current.effective_at,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO companion_academic_transitions(
                transition_id, pet_id, memory_subject_id,
                relationship_epoch_id, from_stage, from_status,
                to_stage, to_status, transition_kind, effective_at,
                source_revision, source_kind, evidence_id,
                growth_eligible, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      'identity:student_profile', ?, ?, ?)
            """,
            (
                transition_id,
                pet_id,
                memory_subject_id,
                relationship_epoch_id,
                previous.stage if previous is not None else None,
                previous.status if previous is not None else None,
                transition.current.stage,
                transition.current.status,
                transition.kind,
                transition.current.effective_at,
                transition.current.source_revision,
                evidence_id,
                int(transition.growth_eligible),
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO companion_academic_states(
                pet_id, memory_subject_id, academic_stage,
                academic_status, effective_at, source_revision,
                transition_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pet_id, memory_subject_id) DO UPDATE SET
                academic_stage = excluded.academic_stage,
                academic_status = excluded.academic_status,
                effective_at = excluded.effective_at,
                source_revision = excluded.source_revision,
                transition_id = excluded.transition_id,
                updated_at = excluded.updated_at
            """,
            (
                pet_id,
                memory_subject_id,
                transition.current.stage,
                transition.current.status,
                transition.current.effective_at,
                transition.current.source_revision,
                transition_id,
                now,
            ),
        )
        if transition.kind == "correction" and previous is not None:
            correction_floor = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(source_revision), 0)
                    FROM companion_academic_transitions
                    WHERE pet_id = ? AND memory_subject_id = ?
                      AND source_revision < ?
                      AND transition_kind IN ('correction', 'migration')
                    """,
                    (pet_id, memory_subject_id, source_revision),
                ).fetchone()[0]
            )
            derived_rows = connection.execute(
                """
                SELECT evidence_id FROM companion_academic_transitions
                WHERE pet_id = ? AND memory_subject_id = ?
                  AND source_revision > ? AND source_revision < ?
                  AND evidence_id IS NOT NULL
                """,
                (pet_id, memory_subject_id, correction_floor, source_revision),
            ).fetchall()
            derived_evidence_ids = tuple(row["evidence_id"] for row in derived_rows)
            if derived_evidence_ids:
                placeholders = ",".join("?" for _ in derived_evidence_ids)
                CompanionStore._reconcile_growth_moments_after_evidence_change(
                    connection,
                    evidence_ids=derived_evidence_ids,
                    reason_code="academic_profile_corrected",
                )
                connection.execute(
                    f"""
                    UPDATE consolidation_jobs
                    SET status = 'succeeded',
                        model = 'deterministic-academic-correction',
                        lease_until = NULL,
                        updated_at = ?
                    WHERE status IN ('pending', 'retry', 'running')
                      AND json_extract(payload_json, '$.evidence_id')
                          IN ({placeholders})
                    """,
                    (now, *derived_evidence_ids),
                )
                connection.execute(
                    f"""
                    UPDATE companion_chapters
                    SET status = 'invalidated'
                    WHERE chapter_id IN (
                        SELECT chapter_id FROM chapter_evidence
                        WHERE evidence_id IN ({placeholders})
                    )
                    """,
                    derived_evidence_ids,
                )
                connection.execute(
                    f"""
                    UPDATE companion_chapters
                    SET status = 'invalidated'
                    WHERE chapter_id IN (
                        SELECT chapter_link.chapter_id
                        FROM companion_chapter_boundaries AS chapter_link
                        JOIN companion_narrative_boundaries AS boundary
                          ON boundary.boundary_id = chapter_link.boundary_id
                        WHERE boundary.evidence_id IN ({placeholders})
                    )
                    """,
                    derived_evidence_ids,
                )
                connection.execute(
                    f"""
                    UPDATE session_capsules
                    SET status = 'invalidated'
                    WHERE capsule_id IN (
                        SELECT capsule_id FROM capsule_evidence
                        WHERE evidence_id IN ({placeholders})
                    )
                    """,
                    derived_evidence_ids,
                )
                CompanionStore._revoke_adjustments_for_evidence_ids(
                    connection,
                    evidence_ids=derived_evidence_ids,
                )
                CompanionStore._invalidate_initiative_opportunities_for_evidence(
                    connection,
                    evidence_ids=derived_evidence_ids,
                    reason_code="academic_profile_corrected",
                    now=now,
                    scrub=False,
                )
        if transition.kind in {"graduation", "explicit_clear"}:
            connection.execute(
                """
                UPDATE companion_chapters AS chapter
                SET status = 'superseded', period_end = ?
                WHERE chapter.pet_id = ?
                  AND chapter.relationship_epoch_id = ?
                  AND chapter.status = 'active'
                  AND EXISTS (
                    SELECT 1
                    FROM chapter_evidence AS link
                    JOIN companion_evidence AS evidence
                      ON evidence.evidence_id = link.evidence_id
                     AND evidence.pet_id = link.pet_id
                    WHERE link.chapter_id = chapter.chapter_id
                      AND evidence.memory_subject_id = ?
                  )
                """,
                (
                    transition.current.effective_at,
                    pet_id,
                    relationship_epoch_id,
                    memory_subject_id,
                ),
            )
        if previous is None:
            return evidence_id, None
        boundary_kind = CompanionStore._narrative_kind_for_transition(transition)
        if boundary_kind is None:
            return evidence_id, None
        from_stage = previous.stage
        to_stage = transition.current.stage
        age = xiaoxin_age_for_stage(to_stage)
        if boundary_kind == "graduation":
            age = xiaoxin_age_for_stage(from_stage)
        if age is None:
            return evidence_id, None
        boundary_id = CompanionStore._create_narrative_boundary_in_connection(
            connection,
            pet_id=pet_id,
            memory_subject_id=memory_subject_id,
            relationship_epoch_id=relationship_epoch_id,
            boundary_kind=boundary_kind,
            source_key=f"academic:{transition_id}",
            transition_id=transition_id,
            evidence_id=evidence_id,
            from_stage=from_stage,
            to_stage=to_stage,
            xiaoxin_age=age,
            effective_at=transition.current.effective_at,
            created_at=now,
        )
        moment_id = CompanionStore._create_growth_moment_in_connection(
            connection,
            boundary_id=boundary_id,
            pet_id=pet_id,
            memory_subject_id=memory_subject_id,
            relationship_epoch_id=relationship_epoch_id,
            evidence_id=evidence_id,
            primary_kind=boundary_kind,
            from_stage=from_stage,
            to_stage=to_stage,
            xiaoxin_age=age,
            occurred_at=transition.current.effective_at,
            created_at=now,
        )
        if boundary_kind in {"academic_growth", "graduation"}:
            CompanionStore._merge_nearby_anniversaries_into_growth_moment(
                connection,
                moment_id=moment_id,
                pet_id=pet_id,
                memory_subject_id=memory_subject_id,
                relationship_epoch_id=relationship_epoch_id,
                occurred_at=transition.current.effective_at,
            )
        job_id = CompanionStore._enqueue_narrative_job_in_connection(
            connection,
            pet_id=pet_id,
            relationship_epoch_id=relationship_epoch_id,
            memory_subject_id=memory_subject_id,
            boundary_id=boundary_id,
            boundary_kind=boundary_kind,
            from_stage=from_stage,
            to_stage=to_stage,
            period_start=previous.effective_at,
            period_end=transition.current.effective_at,
            evidence_id=evidence_id,
            source_revision=source_revision,
            now=now,
        )
        return evidence_id, job_id

    @staticmethod
    def _growth_moment_payload(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        now: str,
    ) -> Mapping[str, object]:
        boundary_rows = connection.execute(
            """
            SELECT boundary.boundary_id, boundary.boundary_kind,
                   boundary.from_stage, boundary.to_stage,
                   boundary.xiaoxin_age, boundary.anniversary_number,
                   boundary.effective_at
            FROM companion_growth_moment_boundaries AS link
            JOIN companion_narrative_boundaries AS boundary
              ON boundary.boundary_id = link.boundary_id
            WHERE link.moment_id = ? AND boundary.status = 'active'
            ORDER BY boundary.effective_at, boundary.boundary_id
            """,
            (row["moment_id"],),
        ).fetchall()
        evidence_rows = connection.execute(
            """
            SELECT evidence.evidence_id, evidence.ownership_scope,
                   evidence.source_summary
            FROM companion_growth_moment_evidence AS link
            JOIN companion_evidence AS evidence
              ON evidence.evidence_id = link.evidence_id
             AND evidence.pet_id = link.pet_id
            WHERE link.moment_id = ? AND evidence.status = 'active'
              AND (
                evidence.expires_at IS NULL
                OR julianday(evidence.expires_at) > julianday(?)
              )
            ORDER BY evidence.occurred_at, evidence.evidence_id
            LIMIT 3
            """,
            (row["moment_id"], now),
        ).fetchall()
        primary_kind = row["primary_kind"]
        hardware_semantic = {
            "academic_growth": "growth_acknowledgement",
            "academic_reorientation": None,
            "anniversary": "anniversary_acknowledgement",
            "graduation": "graduation_acknowledgement",
        }[primary_kind]
        shared_summaries = [
            evidence["source_summary"]
            for evidence in evidence_rows
            if evidence["ownership_scope"] == "relationship"
        ]
        return {
            "moment_id": row["moment_id"],
            "from_stage": row["from_stage"],
            "to_stage": row["to_stage"],
            "xiaoxin_age": row["xiaoxin_age"],
            "safe_summary": row["safe_summary"],
            "occurred_at": row["occurred_at"],
            "relationship_epoch_id": row["relationship_epoch_id"],
            "evidence_id": row["evidence_id"],
            "continuity_evidence_count": row["continuity_evidence_count"],
            "expression_status": row["expression_status"],
            "primary_kind": primary_kind,
            "mode": row["mode"],
            "lifecycle_status": row["lifecycle_status"],
            "expires_at": row["expires_at"],
            "boundary_ids": tuple(
                boundary["boundary_id"] for boundary in boundary_rows
            ),
            "boundary_facts": tuple(
                {
                    "kind": boundary["boundary_kind"],
                    "from_stage": boundary["from_stage"],
                    "to_stage": boundary["to_stage"],
                    "xiaoxin_age": boundary["xiaoxin_age"],
                    "anniversary_number": boundary["anniversary_number"],
                    "occurred_at": boundary["effective_at"],
                }
                for boundary in boundary_rows
            ),
            "evidence_ids": tuple(
                evidence["evidence_id"] for evidence in evidence_rows
            ),
            "safe_evidence_summaries": tuple(
                evidence["source_summary"] for evidence in evidence_rows
            ),
            "voice": {
                "max_sentences": 2 if row["mode"] == "evidence_backed" else 1,
                "shared_anchor_budget": 1 if shared_summaries else 0,
                "safe_anchor": shared_summaries[-1] if shared_summaries else None,
                "initiative_allowed": False,
            },
            "hardware": {
                "enabled": hardware_semantic is not None,
                "semantic": hardware_semantic,
                "intensity": "low" if hardware_semantic is not None else None,
                "duration": "brief" if hardware_semantic is not None else None,
            },
        }

    def load_growth_moment(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        academic_stage: str,
        now: str,
    ) -> Mapping[str, object] | None:
        with self.connection() as connection:
            self._assert_owner_in_connection(
                connection,
                owner_user_id=owner_user_id,
                pet_id=pet_id,
            )
            connection.execute(
                """
                UPDATE companion_growth_moment_metadata
                SET lifecycle_status = 'expired', reason_code = 'expression_window_expired'
                WHERE lifecycle_status = 'active'
                  AND julianday(expires_at) < julianday(?)
                  AND moment_id IN (
                    SELECT moment_id FROM companion_growth_moments WHERE pet_id = ?
                  )
                """,
                (now, pet_id),
            )
            row = connection.execute(
                """
                SELECT moment.*, metadata.primary_kind, metadata.mode,
                       metadata.lifecycle_status, metadata.expires_at
                FROM companion_growth_moments AS moment
                JOIN companion_growth_moment_metadata AS metadata
                  ON metadata.moment_id = moment.moment_id
                WHERE moment.pet_id = ? AND moment.memory_subject_id = ?
                  AND moment.relationship_epoch_id = ? AND moment.to_stage = ?
                  AND metadata.lifecycle_status = 'active'
                  AND EXISTS (
                    SELECT 1
                    FROM companion_growth_moment_boundaries AS link
                    JOIN companion_narrative_boundaries AS boundary
                      ON boundary.boundary_id = link.boundary_id
                    WHERE link.moment_id = moment.moment_id
                      AND boundary.status = 'active'
                  )
                ORDER BY moment.occurred_at DESC, moment.moment_id DESC
                LIMIT 1
                """,
                (pet_id, memory_subject_id, relationship_epoch_id, academic_stage),
            ).fetchone()
            payload = (
                self._growth_moment_payload(connection, row, now=now)
                if row is not None
                else None
            )
            connection.commit()
        return payload

    def claim_growth_moment(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        academic_stage: str,
        turn_id: str,
        now: str,
        lease_seconds: int = 300,
    ) -> Mapping[str, object] | None:
        lease_until = (
            datetime.fromisoformat(now) + timedelta(seconds=lease_seconds)
        ).isoformat()
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._assert_owner_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                )
                connection.execute(
                    """
                    UPDATE companion_growth_moment_metadata
                    SET lifecycle_status = 'expired',
                        reason_code = 'expression_window_expired'
                    WHERE lifecycle_status = 'active'
                      AND julianday(expires_at) < julianday(?)
                      AND moment_id IN (
                        SELECT moment_id FROM companion_growth_moments WHERE pet_id = ?
                      )
                    """,
                    (now, pet_id),
                )
                row = connection.execute(
                    """
                    SELECT moment.*, metadata.primary_kind, metadata.mode,
                           metadata.lifecycle_status, metadata.expires_at
                    FROM companion_growth_moments AS moment
                    JOIN companion_growth_moment_metadata AS metadata
                      ON metadata.moment_id = moment.moment_id
                    WHERE moment.pet_id = ? AND moment.memory_subject_id = ?
                      AND moment.relationship_epoch_id = ? AND moment.to_stage = ?
                      AND metadata.lifecycle_status = 'active'
                      AND julianday(metadata.expires_at) >= julianday(?)
                      AND EXISTS (
                        SELECT 1
                        FROM companion_growth_moment_boundaries AS link
                        JOIN companion_narrative_boundaries AS boundary
                          ON boundary.boundary_id = link.boundary_id
                        WHERE link.moment_id = moment.moment_id
                          AND boundary.status = 'active'
                      )
                      AND (
                        moment.expression_status = 'pending'
                        OR (
                            moment.expression_status = 'reserved'
                            AND julianday(moment.lease_until) <= julianday(?)
                        )
                      )
                    ORDER BY moment.occurred_at, moment.moment_id
                    LIMIT 1
                    """,
                    (
                        pet_id,
                        memory_subject_id,
                        relationship_epoch_id,
                        academic_stage,
                        now,
                        now,
                    ),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                updated = connection.execute(
                    """
                    UPDATE companion_growth_moments
                    SET expression_status = 'reserved', reserved_by_turn_id = ?,
                        lease_until = ?, expressed_at = NULL
                    WHERE moment_id = ? AND (
                        expression_status = 'pending'
                        OR (
                            expression_status = 'reserved'
                            AND julianday(lease_until) <= julianday(?)
                        )
                    )
                    """,
                    (turn_id, lease_until, row["moment_id"], now),
                )
                if updated.rowcount != 1:
                    connection.commit()
                    return None
                claimed = connection.execute(
                    """
                    SELECT moment.*, metadata.primary_kind, metadata.mode,
                           metadata.lifecycle_status, metadata.expires_at
                    FROM companion_growth_moments AS moment
                    JOIN companion_growth_moment_metadata AS metadata
                      ON metadata.moment_id = moment.moment_id
                    WHERE moment.moment_id = ?
                    """,
                    (row["moment_id"],),
                ).fetchone()
                payload = self._growth_moment_payload(connection, claimed, now=now)
                connection.commit()
                return payload
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _finish_growth_moment_in_connection(
        connection: sqlite3.Connection,
        *,
        prepared: PreparedCompanionTurn,
        outcome: CompanionTurnOutcome,
    ) -> None:
        growth_moment = prepared.growth_moment
        if growth_moment is None:
            return
        moment_id = growth_moment.get("moment_id")
        if not isinstance(moment_id, str) or not moment_id:
            raise ValueError("prepared growth moment ID is invalid")
        if outcome.delivery_status in ("generated", "delivered"):
            updated = connection.execute(
                """
                UPDATE companion_growth_moments
                SET expression_status = 'expressed', lease_until = NULL,
                    expressed_at = ?
                WHERE moment_id = ? AND reserved_by_turn_id = ?
                  AND expression_status = 'reserved'
                """,
                (prepared.occurred_at, moment_id, prepared.turn_id),
            )
        else:
            updated = connection.execute(
                """
                UPDATE companion_growth_moments
                SET expression_status = 'pending', reserved_by_turn_id = NULL,
                    lease_until = NULL, expressed_at = NULL
                WHERE moment_id = ? AND reserved_by_turn_id = ?
                  AND expression_status = 'reserved'
                """,
                (moment_id, prepared.turn_id),
            )
        if updated.rowcount != 1:
            return

    def defer_observation(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        idempotency_key: str,
        kind: str,
        source_kind: str,
        source_ref: str,
        occurred_at: str,
        payload: Mapping[str, object],
        safe_summary: str,
        queued_reason: str,
    ) -> CompanionObserveResult:
        if queued_reason not in {"missing_subject", "ambiguous_subject"}:
            raise ValueError("pending observation reason is invalid")
        observation_id = str(
            uuid5(
                NAMESPACE_URL,
                f"companion-observation:{pet_id}:{idempotency_key}",
            )
        )
        pending_digest = _pending_observation_digest(
            owner_user_id=owner_user_id,
            pet_id=pet_id,
            kind=kind,
            source_kind=source_kind,
            source_ref=source_ref,
            occurred_at=occurred_at,
            payload=payload,
            safe_summary=safe_summary,
        )
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                recorded = connection.execute(
                    """
                    SELECT owner_user_id, pet_id, kind, source_kind, source_ref,
                           occurred_at, payload_json, safe_summary
                    FROM companion_observations
                    WHERE idempotency_key = ?
                    """,
                    (idempotency_key,),
                ).fetchone()
                if recorded is not None:
                    recorded_digest = _pending_observation_digest(
                        owner_user_id=recorded["owner_user_id"],
                        pet_id=recorded["pet_id"],
                        kind=recorded["kind"],
                        source_kind=recorded["source_kind"],
                        source_ref=recorded["source_ref"],
                        occurred_at=recorded["occurred_at"],
                        payload=json.loads(recorded["payload_json"]),
                        safe_summary=recorded["safe_summary"],
                    )
                    if recorded_digest != pending_digest:
                        raise CompanionIdempotencyConflict(
                            "pending observation idempotency key conflicts with "
                            "recorded content"
                        )
                    connection.commit()
                    return CompanionObserveResult(
                        observation_id=observation_id,
                        status="duplicate",
                    )
                existing = connection.execute(
                    """
                    SELECT observation_id, pending_digest
                    FROM pending_companion_observations
                    WHERE idempotency_key = ?
                    """,
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    if existing["pending_digest"] != pending_digest:
                        raise CompanionIdempotencyConflict(
                            "pending observation idempotency key conflicts with "
                            "different content"
                        )
                    connection.commit()
                    return CompanionObserveResult(
                        observation_id=existing["observation_id"],
                        status="duplicate",
                    )
                created_at = datetime.now(timezone.utc).isoformat()
                expires_at = (
                    datetime.fromisoformat(created_at) + timedelta(days=30)
                ).isoformat()
                inserted_pet = connection.execute(
                    """
                    INSERT OR IGNORE INTO companion_pets(
                        pet_id, owner_user_id, created_at
                    ) VALUES (?, ?, ?)
                    """,
                    (pet_id, owner_user_id, created_at),
                )
                self._assert_owner_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                )
                self._ensure_birth_temperament_in_connection(
                    connection,
                    pet_id=pet_id,
                    generated_at=(
                        created_at
                        if inserted_pet.rowcount == 1
                        else datetime.now(timezone.utc).isoformat()
                    ),
                    source_kind=(
                        "pet_created"
                        if inserted_pet.rowcount == 1
                        else "legacy_backfill"
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO pending_companion_observations(
                        observation_id, idempotency_key, owner_user_id, pet_id,
                        kind, source_kind, source_ref, payload_json,
                        pending_digest, safe_summary, occurred_at, queued_reason,
                        status, expires_at, created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?
                    )
                    """,
                    (
                        observation_id,
                        idempotency_key,
                        owner_user_id,
                        pet_id,
                        kind,
                        source_kind,
                        source_ref,
                        _stable_json(payload),
                        pending_digest,
                        safe_summary,
                        occurred_at,
                        queued_reason,
                        expires_at,
                        created_at,
                    ),
                )
                connection.commit()
                return CompanionObserveResult(
                    observation_id=observation_id,
                    status="deferred",
                )
            except Exception:
                connection.rollback()
                raise

    def materialize_due_connection_bids(
        self,
        *,
        now: str,
        limit: int,
    ) -> int:
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    """
                    SELECT need.*
                    FROM companion_relationship_needs AS need
                    JOIN relationship_epochs AS epoch
                      ON epoch.epoch_id = need.relationship_epoch_id
                     AND epoch.pet_id = need.pet_id
                    JOIN companion_evidence AS evidence
                      ON evidence.evidence_id = need.source_evidence_id
                     AND evidence.pet_id = need.pet_id
                     AND evidence.memory_subject_id = need.memory_subject_id
                    WHERE need.need_kind = 'connection'
                      AND epoch.ended_at IS NULL
                      AND need.pending_decision_id IS NULL
                      AND julianday(need.next_eligible_at) <= julianday(?)
                      AND (
                        need.cooldown_until IS NULL
                        OR julianday(need.cooldown_until) <= julianday(?)
                      )
                      AND evidence.status = 'active'
                      AND evidence.prompt_eligible = 1
                      AND evidence.sensitivity <> 'sensitive'
                      AND (
                        evidence.expires_at IS NULL
                        OR julianday(evidence.expires_at) > julianday(?)
                      )
                      AND (
                        evidence.valid_until IS NULL
                        OR julianday(evidence.valid_until) > julianday(?)
                      )
                      AND EXISTS (
                        SELECT 1
                        FROM companion_presence_leases AS lease
                        WHERE lease.owner_user_id = need.owner_user_id
                          AND lease.pet_id = need.pet_id
                          AND lease.memory_subject_id = need.memory_subject_id
                          AND lease.relationship_epoch_id = need.relationship_epoch_id
                          AND lease.status = 'active'
                          AND julianday(lease.expires_at) > julianday(?)
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM initiative_opportunities AS opportunity
                        WHERE opportunity.owner_user_id = need.owner_user_id
                          AND opportunity.pet_id = need.pet_id
                          AND opportunity.memory_subject_id = need.memory_subject_id
                          AND opportunity.relationship_epoch_id =
                              need.relationship_epoch_id
                          AND opportunity.opportunity_kind = 'connection_bid'
                          AND opportunity.status IN (
                              'scheduled', 'deferred', 'claimed', 'delivering',
                              'delivered'
                          )
                      )
                    ORDER BY need.next_eligible_at, need.pet_id,
                             need.memory_subject_id
                    LIMIT ?
                    """,
                    (now, now, now, now, now, max(int(limit), 1)),
                ).fetchall()
                inserted = 0
                safe_briefs = {
                    "reserved": (
                        "有一阵子没有互动了。用克制、不给压力的方式表达想联系，"
                        "不追问原因。"
                    ),
                    "timely": (
                        "有一阵子没有互动了。自然表达想和用户说说话，允许用户"
                        "稍后再回应。"
                    ),
                    "proactive": (
                        "有一阵子没有互动了。可以更直接但不黏人地表达想念这段"
                        "交流，不要求签到。"
                    ),
                }
                for row in rows:
                    opportunity_id = str(
                        uuid5(
                            NAMESPACE_URL,
                            "xiaoxin:connection-bid:"
                            f"{row['owner_user_id']}:{row['pet_id']}:"
                            f"{row['memory_subject_id']}:"
                            f"{row['relationship_epoch_id']}:"
                            f"{row['next_eligible_at']}:{row['version']}",
                        )
                    )
                    result = connection.execute(
                        """
                        INSERT OR IGNORE INTO initiative_opportunities(
                            opportunity_id, owner_user_id, pet_id,
                            memory_subject_id, relationship_epoch_id,
                            opportunity_kind, reason_code, evidence_ids_json,
                            safe_brief, due_at, status, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, 'connection_bid',
                            'relationship_connection_due', ?, ?, ?,
                            'scheduled', ?, ?
                        )
                        """,
                        (
                            opportunity_id,
                            row["owner_user_id"],
                            row["pet_id"],
                            row["memory_subject_id"],
                            row["relationship_epoch_id"],
                            _stable_json((row["source_evidence_id"],)),
                            safe_briefs[row["initiative_bias"]],
                            row["next_eligible_at"],
                            now,
                            now,
                        ),
                    )
                    inserted += int(result.rowcount == 1)
                connection.commit()
                return inserted
            except Exception:
                connection.rollback()
                raise

    def load_connection_need(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
    ) -> Mapping[str, object] | None:
        with self.connection() as connection:
            self._assert_owner_in_connection(
                connection,
                owner_user_id=owner_user_id,
                pet_id=pet_id,
            )
            row = connection.execute(
                """
                SELECT * FROM companion_relationship_needs
                WHERE owner_user_id = ? AND pet_id = ?
                  AND memory_subject_id = ? AND relationship_epoch_id = ?
                  AND need_kind = 'connection'
                """,
                (
                    owner_user_id,
                    pet_id,
                    memory_subject_id,
                    relationship_epoch_id,
                ),
            ).fetchone()
        return dict(row) if row is not None else None

    def expire_connection_feedback(
        self,
        *,
        now: str,
        feedback_window_seconds: int,
        limit: int,
    ) -> int:
        window_seconds = max(int(feedback_window_seconds), 1)
        cutoff = (
            datetime.fromisoformat(now) - timedelta(seconds=window_seconds)
        ).isoformat()
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT need.owner_user_id, need.pet_id,
                       need.memory_subject_id, need.pending_decision_id
                FROM companion_relationship_needs AS need
                JOIN relationship_epochs AS epoch
                  ON epoch.epoch_id = need.relationship_epoch_id
                 AND epoch.pet_id = need.pet_id
                JOIN initiative_opportunities AS opportunity
                  ON opportunity.decision_id = need.pending_decision_id
                 AND opportunity.opportunity_kind = 'connection_bid'
                 AND opportunity.status = 'delivered'
                JOIN initiative_decisions AS decision
                  ON decision.decision_id = need.pending_decision_id
                 AND decision.delivery_status = 'delivered'
                WHERE need.need_kind = 'connection'
                  AND epoch.ended_at IS NULL
                  AND julianday(opportunity.updated_at) < julianday(?)
                ORDER BY opportunity.updated_at, need.pet_id,
                         need.memory_subject_id
                LIMIT ?
                """,
                (cutoff, max(int(limit), 1)),
            ).fetchall()
        expired = 0
        for row in rows:
            decision_id = str(row["pending_decision_id"])
            try:
                self.record_initiative_feedback(
                    owner_user_id=str(row["owner_user_id"]),
                    pet_id=str(row["pet_id"]),
                    memory_subject_id=str(row["memory_subject_id"]),
                    decision_id=decision_id,
                    outcome="ignored",
                    now=now,
                    idempotency_key=f"connection-ignored:{decision_id}",
                )
            except CompanionIdempotencyConflict:
                continue
            expired += 1
        return expired

    def list_due_initiative_opportunities(
        self,
        *,
        now: str,
        limit: int,
    ) -> tuple[DueInitiativeOpportunity, ...]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT opportunity.*,
                       need.initiative_bias AS connection_initiative_bias,
                       need.relationship_stage AS connection_relationship_stage,
                       need.threshold_seconds AS connection_threshold_seconds
                FROM initiative_opportunities AS opportunity
                LEFT JOIN companion_relationship_needs AS need
                  ON opportunity.opportunity_kind = 'connection_bid'
                 AND need.owner_user_id = opportunity.owner_user_id
                 AND need.pet_id = opportunity.pet_id
                 AND need.memory_subject_id = opportunity.memory_subject_id
                 AND need.relationship_epoch_id =
                     opportunity.relationship_epoch_id
                 AND need.need_kind = 'connection'
                WHERE (
                    opportunity.status IN ('scheduled', 'deferred')
                    OR (
                        opportunity.status = 'claimed'
                        AND opportunity.lease_until IS NOT NULL
                        AND julianday(opportunity.lease_until) <= julianday(?)
                    )
                )
                  AND julianday(opportunity.due_at) <= julianday(?)
                  AND (
                    opportunity.next_attempt_at IS NULL
                    OR julianday(opportunity.next_attempt_at) <= julianday(?)
                  )
                ORDER BY opportunity.due_at, opportunity.opportunity_id
                LIMIT ?
                """,
                (now, now, now, max(int(limit), 1)),
            ).fetchall()
        return tuple(_initiative_opportunity_from_row(row, now=now) for row in rows)

    def load_active_initiative_contract_level(
        self,
        *,
        pet_id: str,
        memory_subject_id: str,
    ) -> str | None:
        with self.connection() as connection:
            return _active_initiative_contract_level(
                connection,
                pet_id=pet_id,
                memory_subject_id=memory_subject_id,
            )

    def validate_initiative_opportunity(
        self,
        *,
        opportunity_id: str,
        now: str,
        boot_checkin_delivery_window_seconds: int | None = None,
    ) -> str:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT opportunity.relationship_epoch_id,
                       opportunity.pet_id,
                       opportunity.memory_subject_id,
                       opportunity.opportunity_kind,
                       opportunity.evidence_ids_json,
                       epoch.ended_at
                FROM initiative_opportunities AS opportunity
                JOIN relationship_epochs AS epoch
                  ON epoch.epoch_id = opportunity.relationship_epoch_id
                 AND epoch.pet_id = opportunity.pet_id
                WHERE opportunity.opportunity_id = ?
                  AND opportunity.status IN ('scheduled', 'deferred', 'claimed')
                """,
                (opportunity_id,),
            ).fetchone()
            if row is None or row["ended_at"] is not None:
                return "stale_epoch"
            if row["opportunity_kind"] == "boot_checkin":
                boot_event = connection.execute(
                    """
                    SELECT status, occurred_at
                    FROM companion_device_boot_events
                    WHERE opportunity_id = ?
                    """,
                    (opportunity_id,),
                ).fetchone()
                if boot_event is None or boot_event["status"] != "scheduled":
                    return "boot_event_missing"
                if boot_checkin_delivery_window_seconds is not None:
                    cutoff = (
                        datetime.fromisoformat(now)
                        - timedelta(
                            seconds=max(
                                int(boot_checkin_delivery_window_seconds), 1
                            )
                        )
                    )
                    if datetime.fromisoformat(boot_event["occurred_at"]) < cutoff:
                        return "boot_checkin_stale"
                return "eligible"
            evidence_ids = _json_text_ids(row["evidence_ids_json"])
            if not evidence_ids:
                return "no_evidence"
            placeholders = ",".join("?" for _ in evidence_ids)
            evidence_rows = connection.execute(
                f"""
                SELECT status, prompt_eligible, sensitivity, ownership_scope,
                       relationship_epoch_id, expires_at, valid_from, valid_until
                FROM companion_evidence
                WHERE evidence_id IN ({placeholders})
                  AND pet_id = ?
                  AND memory_subject_id = ?
                """,
                (*evidence_ids, row["pet_id"], row["memory_subject_id"]),
            ).fetchall()
            if len(evidence_rows) != len(evidence_ids):
                return "no_evidence"
            for evidence in evidence_rows:
                if evidence["sensitivity"] == "sensitive":
                    return "sensitive_evidence"
                if (
                    evidence["status"] != "active"
                    or not bool(evidence["prompt_eligible"])
                    or (
                        evidence["expires_at"] is not None
                        and datetime.fromisoformat(evidence["expires_at"])
                        <= datetime.fromisoformat(now)
                    )
                    or (
                        evidence["valid_from"] is not None
                        and datetime.fromisoformat(evidence["valid_from"])
                        > datetime.fromisoformat(now)
                    )
                    or (
                        evidence["valid_until"] is not None
                        and row["opportunity_kind"] != "reminder_result"
                        and datetime.fromisoformat(evidence["valid_until"])
                        <= datetime.fromisoformat(now)
                    )
                    or (
                        evidence["ownership_scope"] == "relationship"
                        and evidence["relationship_epoch_id"]
                        != row["relationship_epoch_id"]
                    )
                ):
                    return "no_evidence"
            disabled_boundary = connection.execute(
                """
                SELECT 1
                FROM companion_evidence
                WHERE pet_id = ? AND memory_subject_id = ? AND status = 'active'
                  AND kind IN ('boundary', 'explicit_boundary')
                  AND COALESCE(
                        fact_key,
                        json_extract(content_json, '$.fact_key')
                      ) IN (
                        'boundary:initiative_level',
                        'boundary:initiative_frequency'
                      )
                  AND lower(CAST(json_extract(content_json, '$.value') AS TEXT))
                      IN ('disabled', 'off', 'never')
                LIMIT 1
                """,
                (row["pet_id"], row["memory_subject_id"]),
            ).fetchone()
            if disabled_boundary is not None:
                return "disabled"
            if (
                _active_initiative_contract_level(
                    connection,
                    pet_id=str(row["pet_id"]),
                    memory_subject_id=str(row["memory_subject_id"]),
                )
                == "disabled"
            ):
                return "disabled"
        return "eligible"

    def block_initiative_opportunity(
        self,
        *,
        opportunity_id: str,
        reason_code: str,
        now: str,
    ) -> None:
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT * FROM initiative_opportunities
                    WHERE opportunity_id = ?
                      AND status IN ('scheduled', 'deferred', 'claimed')
                    """,
                    (opportunity_id,),
                ).fetchone()
                if row is not None:
                    target_status = (
                        "invalidated"
                        if row["opportunity_kind"] == "boot_checkin"
                        and reason_code == "boot_checkin_stale"
                        else "blocked"
                    )
                    connection.execute(
                        """
                        UPDATE initiative_opportunities
                        SET status = ?, outcome_code = ?,
                            lease_until = NULL, next_attempt_at = NULL,
                            updated_at = ?
                        WHERE opportunity_id = ?
                          AND status IN ('scheduled', 'deferred', 'claimed')
                        """,
                        (target_status, reason_code, now, opportunity_id),
                    )
                    if target_status == "invalidated":
                        connection.execute(
                            """
                            UPDATE initiative_decisions
                            SET delivery_status = 'invalidated'
                            WHERE decision_id = ?
                              AND delivery_status IN (
                                  'pending', 'composing', 'dispatching'
                              )
                            """,
                            (row["decision_id"],),
                        )
                        connection.execute(
                            """
                            UPDATE companion_device_boot_events
                            SET status = 'suppressed', updated_at = ?
                            WHERE opportunity_id = ? AND status = 'scheduled'
                            """,
                            (now, opportunity_id),
                        )
                    if row["opportunity_kind"] == "connection_bid":
                        self._backoff_connection_need_in_connection(
                            connection,
                            owner_user_id=row["owner_user_id"],
                            pet_id=row["pet_id"],
                            memory_subject_id=row["memory_subject_id"],
                            relationship_epoch_id=row["relationship_epoch_id"],
                            decision_id=row["decision_id"],
                            now=now,
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _apply_connection_feedback_in_connection(
        connection: sqlite3.Connection,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        decision_id: str,
        outcome: str,
        now: str,
    ) -> bool:
        need = connection.execute(
            """
            SELECT threshold_seconds, initiative_bias, ignored_streak,
                   pending_decision_id
            FROM companion_relationship_needs
            WHERE owner_user_id = ? AND pet_id = ?
              AND memory_subject_id = ? AND relationship_epoch_id = ?
              AND need_kind = 'connection'
            """,
            (
                owner_user_id,
                pet_id,
                memory_subject_id,
                relationship_epoch_id,
            ),
        ).fetchone()
        if need is None or need["pending_decision_id"] != decision_id:
            return False
        threshold_seconds = max(int(need["threshold_seconds"]), 1)
        ignored_streak = int(need["ignored_streak"])
        cooldown_until = None
        if outcome == "ignored":
            ignored_streak += 1
            delay_seconds = _connection_ignore_backoff_seconds(
                threshold_seconds=threshold_seconds,
                initiative_bias=str(need["initiative_bias"]),
                ignored_streak=ignored_streak,
            )
            next_eligible_at = (
                datetime.fromisoformat(now) + timedelta(seconds=delay_seconds)
            ).isoformat()
        elif outcome == "rejected":
            cooldown_until = (
                datetime.fromisoformat(now) + _CONNECTION_REJECTION_COOLDOWN
            ).isoformat()
            next_eligible_at = cooldown_until
        elif outcome == "accepted":
            ignored_streak = 0
            next_eligible_at = (
                datetime.fromisoformat(now)
                + timedelta(seconds=threshold_seconds)
            ).isoformat()
        else:
            return False
        connection.execute(
            """
            UPDATE initiative_opportunities
            SET status = 'invalidated', outcome_code = ?, updated_at = ?
            WHERE decision_id = ? AND opportunity_kind = 'connection_bid'
              AND status = 'delivered'
            """,
            (outcome, now, decision_id),
        )
        updated = connection.execute(
            """
            UPDATE companion_relationship_needs
            SET pending_decision_id = NULL, ignored_streak = ?,
                cooldown_until = ?, next_eligible_at = ?,
                version = version + 1, updated_at = ?
            WHERE owner_user_id = ? AND pet_id = ?
              AND memory_subject_id = ? AND relationship_epoch_id = ?
              AND need_kind = 'connection' AND pending_decision_id = ?
            """,
            (
                ignored_streak,
                cooldown_until,
                next_eligible_at,
                now,
                owner_user_id,
                pet_id,
                memory_subject_id,
                relationship_epoch_id,
                decision_id,
            ),
        )
        return updated.rowcount == 1

    @staticmethod
    def _backoff_connection_need_in_connection(
        connection: sqlite3.Connection,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        decision_id: str | None,
        now: str,
    ) -> None:
        need = connection.execute(
            """
            SELECT threshold_seconds, pending_decision_id
            FROM companion_relationship_needs
            WHERE owner_user_id = ? AND pet_id = ?
              AND memory_subject_id = ? AND relationship_epoch_id = ?
              AND need_kind = 'connection'
            """,
            (
                owner_user_id,
                pet_id,
                memory_subject_id,
                relationship_epoch_id,
            ),
        ).fetchone()
        if need is None or need["pending_decision_id"] not in {None, decision_id}:
            return
        next_eligible_at = (
            datetime.fromisoformat(now)
            + timedelta(seconds=int(need["threshold_seconds"]))
        ).isoformat()
        connection.execute(
            """
            UPDATE companion_relationship_needs
            SET pending_decision_id = NULL, next_eligible_at = ?,
                version = version + 1, updated_at = ?
            WHERE owner_user_id = ? AND pet_id = ?
              AND memory_subject_id = ? AND relationship_epoch_id = ?
              AND need_kind = 'connection'
            """,
            (
                next_eligible_at,
                now,
                owner_user_id,
                pet_id,
                memory_subject_id,
                relationship_epoch_id,
            ),
        )

    def defer_initiative_opportunity(
        self,
        *,
        opportunity_id: str,
        reason_code: str,
        retry_at: str,
        now: str,
    ) -> None:
        retry_at_value = datetime.fromisoformat(retry_at)
        now_value = datetime.fromisoformat(now)
        if retry_at_value <= now_value:
            raise ValueError("initiative retry_at must be after now")
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT decision_id FROM initiative_opportunities
                    WHERE opportunity_id = ?
                      AND status IN (
                          'scheduled', 'deferred', 'claimed', 'delivering'
                      )
                    """,
                    (opportunity_id,),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return
                connection.execute(
                    """
                    UPDATE initiative_opportunities
                    SET status = 'deferred', outcome_code = ?,
                        lease_until = NULL, next_attempt_at = ?,
                        delivery_id = NULL, updated_at = ?
                    WHERE opportunity_id = ?
                      AND status IN (
                          'scheduled', 'deferred', 'claimed', 'delivering'
                      )
                    """,
                    (reason_code, retry_at, now, opportunity_id),
                )
                if row["decision_id"] is not None:
                    connection.execute(
                        """
                        UPDATE initiative_decisions
                        SET delivery_status = 'composing'
                        WHERE decision_id = ?
                          AND delivery_status IN ('composing', 'dispatching')
                        """,
                        (row["decision_id"],),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def claim_initiative_opportunity(
        self,
        *,
        opportunity_id: str,
        hardware_expression: Mapping[str, object],
        now: str,
        lease_seconds: int = 60,
        boot_checkin_delivery_window_seconds: int | None = None,
    ) -> DueInitiativeOpportunity | None:
        if (
            self.validate_initiative_opportunity(
                opportunity_id=opportunity_id,
                now=now,
                boot_checkin_delivery_window_seconds=(
                    boot_checkin_delivery_window_seconds
                ),
            )
            != "eligible"
        ):
            return None
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT * FROM initiative_opportunities
                    WHERE opportunity_id = ?
                      AND (
                        status IN ('scheduled', 'deferred')
                        OR (
                            status = 'claimed' AND lease_until IS NOT NULL
                            AND julianday(lease_until) <= julianday(?)
                        )
                      )
                    """,
                    (opportunity_id, now),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                internal_reason = self.validate_initiative_opportunity(
                    opportunity_id=opportunity_id,
                    now=now,
                    boot_checkin_delivery_window_seconds=(
                        boot_checkin_delivery_window_seconds
                    ),
                )
                if internal_reason != "eligible":
                    connection.execute(
                        """
                        UPDATE initiative_opportunities
                        SET status = 'blocked', outcome_code = ?,
                            lease_until = NULL, updated_at = ?
                        WHERE opportunity_id = ?
                          AND status IN ('scheduled', 'deferred', 'claimed')
                        """,
                        (internal_reason, now, opportunity_id),
                    )
                    if row["opportunity_kind"] == "connection_bid":
                        self._backoff_connection_need_in_connection(
                            connection,
                            owner_user_id=row["owner_user_id"],
                            pet_id=row["pet_id"],
                            memory_subject_id=row["memory_subject_id"],
                            relationship_epoch_id=row["relationship_epoch_id"],
                            decision_id=row["decision_id"],
                            now=now,
                        )
                    connection.commit()
                    return None
                if row["opportunity_kind"] != "boot_checkin":
                    rejected = connection.execute(
                        """
                        SELECT 1 FROM initiative_decisions
                        WHERE pet_id = ? AND relationship_epoch_id = ?
                          AND reason_code = ? AND delivery_status = 'rejected'
                          AND cooldown_until IS NOT NULL
                          AND julianday(cooldown_until) > julianday(?)
                        LIMIT 1
                        """,
                        (
                            row["pet_id"],
                            row["relationship_epoch_id"],
                            row["reason_code"],
                            now,
                        ),
                    ).fetchone()
                    if rejected is not None:
                        connection.execute(
                            """
                            UPDATE initiative_opportunities
                            SET status = 'blocked', outcome_code = 'rejection_cooldown',
                                updated_at = ?
                            WHERE opportunity_id = ?
                            """,
                            (now, opportunity_id),
                        )
                        if row["opportunity_kind"] == "connection_bid":
                            self._backoff_connection_need_in_connection(
                                connection,
                                owner_user_id=row["owner_user_id"],
                                pet_id=row["pet_id"],
                                memory_subject_id=row["memory_subject_id"],
                                relationship_epoch_id=row["relationship_epoch_id"],
                                decision_id=row["decision_id"],
                                now=now,
                            )
                        connection.commit()
                        return None
                    existing_today = connection.execute(
                        """
                        SELECT 1
                        FROM initiative_decisions AS decision
                        LEFT JOIN initiative_opportunities AS opportunity
                          ON opportunity.decision_id = decision.decision_id
                        WHERE decision.pet_id = ?
                          AND decision.relationship_epoch_id = ?
                          AND decision.priority = 'low'
                          AND substr(decision.created_at, 1, 10) = substr(?, 1, 10)
                          AND decision.decision_id <> COALESCE(?, '')
                          AND (
                            opportunity.opportunity_kind IS NULL
                            OR opportunity.opportunity_kind <> 'boot_checkin'
                          )
                        LIMIT 1
                        """,
                        (
                            row["pet_id"],
                            row["relationship_epoch_id"],
                            now,
                            row["decision_id"],
                        ),
                    ).fetchone()
                    if existing_today is not None:
                        connection.execute(
                            """
                            UPDATE initiative_opportunities
                            SET status = 'blocked', outcome_code = 'daily_limit',
                                updated_at = ?
                            WHERE opportunity_id = ?
                            """,
                            (now, opportunity_id),
                        )
                        if row["opportunity_kind"] == "connection_bid":
                            self._backoff_connection_need_in_connection(
                                connection,
                                owner_user_id=row["owner_user_id"],
                                pet_id=row["pet_id"],
                                memory_subject_id=row["memory_subject_id"],
                                relationship_epoch_id=row["relationship_epoch_id"],
                                decision_id=row["decision_id"],
                                now=now,
                            )
                        connection.commit()
                        return None
                    unanswered = connection.execute(
                        """
                        SELECT COUNT(*) AS count, MAX(decision.created_at) AS latest
                        FROM initiative_decisions AS decision
                        LEFT JOIN initiative_opportunities AS opportunity
                          ON opportunity.decision_id = decision.decision_id
                        WHERE decision.pet_id = ?
                          AND decision.relationship_epoch_id = ?
                          AND decision.delivery_status IN ('delivered', 'ignored')
                          AND julianday(decision.created_at) > julianday(?, '-7 days')
                          AND (
                            opportunity.opportunity_kind IS NULL
                            OR opportunity.opportunity_kind <> 'boot_checkin'
                          )
                        """,
                        (row["pet_id"], row["relationship_epoch_id"], now),
                    ).fetchone()
                    if (
                        int(unanswered["count"]) >= 2
                        and unanswered["latest"] is not None
                        and datetime.fromisoformat(unanswered["latest"])
                        + timedelta(days=3)
                        > datetime.fromisoformat(now)
                    ):
                        connection.execute(
                            """
                            UPDATE initiative_opportunities
                            SET status = 'blocked', outcome_code = 'unanswered_backoff',
                                updated_at = ?
                            WHERE opportunity_id = ?
                            """,
                            (now, opportunity_id),
                        )
                        if row["opportunity_kind"] == "connection_bid":
                            self._backoff_connection_need_in_connection(
                                connection,
                                owner_user_id=row["owner_user_id"],
                                pet_id=row["pet_id"],
                                memory_subject_id=row["memory_subject_id"],
                                relationship_epoch_id=row["relationship_epoch_id"],
                                decision_id=row["decision_id"],
                                now=now,
                            )
                        connection.commit()
                        return None
                decision_id = row["decision_id"] or str(
                    uuid5(
                        NAMESPACE_URL,
                        f"xiaoxin:initiative-opportunity:{opportunity_id}",
                    )
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO initiative_decisions(
                        decision_id, pet_id, relationship_epoch_id, reason_code,
                        evidence_ids_json, priority, cooldown_until,
                        content_brief, hardware_expression_json,
                        delivery_status, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'low', ?, ?, ?, 'composing', ?)
                    """,
                    (
                        decision_id,
                        row["pet_id"],
                        row["relationship_epoch_id"],
                        row["reason_code"],
                        row["evidence_ids_json"],
                        (datetime.fromisoformat(now) + timedelta(days=1)).isoformat(),
                        row["safe_brief"],
                        _stable_json(hardware_expression),
                        now,
                    ),
                )
                lease_until = (
                    datetime.fromisoformat(now) + timedelta(seconds=lease_seconds)
                ).isoformat()
                updated = connection.execute(
                    """
                    UPDATE initiative_opportunities
                    SET status = 'claimed', attempt = attempt + 1,
                        lease_until = ?, next_attempt_at = NULL,
                        decision_id = ?, outcome_code = NULL, updated_at = ?
                    WHERE opportunity_id = ?
                      AND status IN ('scheduled', 'deferred', 'claimed')
                    """,
                    (lease_until, decision_id, now, opportunity_id),
                )
                if updated.rowcount != 1:
                    connection.rollback()
                    return None
                if row["opportunity_kind"] == "connection_bid":
                    connection.execute(
                        """
                        UPDATE companion_relationship_needs
                        SET pending_decision_id = ?, last_bid_at = ?,
                            version = version + 1, updated_at = ?
                        WHERE owner_user_id = ? AND pet_id = ?
                          AND memory_subject_id = ?
                          AND relationship_epoch_id = ?
                          AND need_kind = 'connection'
                          AND pending_decision_id IS NULL
                        """,
                        (
                            decision_id,
                            now,
                            now,
                            row["owner_user_id"],
                            row["pet_id"],
                            row["memory_subject_id"],
                            row["relationship_epoch_id"],
                        ),
                    )
                claimed_row = connection.execute(
                    """
                    SELECT opportunity.*,
                           need.initiative_bias AS connection_initiative_bias,
                           need.relationship_stage AS connection_relationship_stage,
                           need.threshold_seconds AS connection_threshold_seconds
                    FROM initiative_opportunities AS opportunity
                    LEFT JOIN companion_relationship_needs AS need
                      ON opportunity.opportunity_kind = 'connection_bid'
                     AND need.owner_user_id = opportunity.owner_user_id
                     AND need.pet_id = opportunity.pet_id
                     AND need.memory_subject_id = opportunity.memory_subject_id
                     AND need.relationship_epoch_id =
                         opportunity.relationship_epoch_id
                     AND need.need_kind = 'connection'
                    WHERE opportunity.opportunity_id = ?
                    """,
                    (opportunity_id,),
                ).fetchone()
                connection.commit()
                return _initiative_opportunity_from_row(claimed_row, now=now)
            except Exception:
                connection.rollback()
                raise

    def begin_initiative_delivery(
        self,
        *,
        opportunity: DueInitiativeOpportunity,
        content: str,
        now: str,
    ):
        from .initiative import InitiativeDeliveryRequest

        if not opportunity.decision_id:
            raise ValueError("claimed opportunity requires decision_id")
        if not isinstance(content, str) or not content.strip() or len(content) > 160:
            raise ValueError("initiative delivery content is invalid")
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                internal_reason = self.validate_initiative_opportunity(
                    opportunity_id=opportunity.opportunity_id,
                    now=now,
                )
                if internal_reason != "eligible":
                    connection.execute(
                        """
                        UPDATE initiative_opportunities
                        SET status = 'blocked', outcome_code = ?,
                            lease_until = NULL, updated_at = ?
                        WHERE opportunity_id = ? AND status = 'claimed'
                        """,
                        (internal_reason, now, opportunity.opportunity_id),
                    )
                    connection.execute(
                        """
                        UPDATE initiative_decisions
                        SET delivery_status = 'invalidated'
                        WHERE decision_id = ? AND delivery_status = 'composing'
                        """,
                        (opportunity.decision_id,),
                    )
                    if opportunity.opportunity_kind == "connection_bid":
                        self._backoff_connection_need_in_connection(
                            connection,
                            owner_user_id=opportunity.owner_user_id,
                            pet_id=opportunity.pet_id,
                            memory_subject_id=opportunity.memory_subject_id,
                            relationship_epoch_id=opportunity.relationship_epoch_id,
                            decision_id=opportunity.decision_id,
                            now=now,
                        )
                    connection.commit()
                    raise CompanionJobLeaseLostError(
                        "initiative opportunity is no longer eligible"
                    )
                updated = connection.execute(
                    """
                    UPDATE initiative_opportunities
                    SET status = 'delivering', lease_until = NULL, updated_at = ?
                    WHERE opportunity_id = ? AND status = 'claimed'
                      AND decision_id = ?
                    """,
                    (now, opportunity.opportunity_id, opportunity.decision_id),
                )
                if updated.rowcount != 1:
                    raise CompanionJobLeaseLostError(
                        "initiative opportunity claim is no longer active"
                    )
                decision = connection.execute(
                    """
                    SELECT hardware_expression_json
                    FROM initiative_decisions WHERE decision_id = ?
                    """,
                    (opportunity.decision_id,),
                ).fetchone()
                decision_updated = connection.execute(
                    """
                    UPDATE initiative_decisions
                    SET content_brief = ?, delivery_status = 'dispatching'
                    WHERE decision_id = ? AND delivery_status = 'composing'
                    """,
                    (content, opportunity.decision_id),
                )
                if decision_updated.rowcount != 1:
                    raise CompanionJobLeaseLostError(
                        "initiative decision is no longer composable"
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return InitiativeDeliveryRequest(
            opportunity_id=opportunity.opportunity_id,
            decision_id=opportunity.decision_id,
            owner_user_id=opportunity.owner_user_id,
            pet_id=opportunity.pet_id,
            memory_subject_id=opportunity.memory_subject_id,
            opportunity_kind=opportunity.opportunity_kind,
            reason_code=opportunity.reason_code,
            content=content,
            hardware_expression=json.loads(decision["hardware_expression_json"]),
            attempted_at=now,
        )

    def finish_initiative_delivery(
        self,
        *,
        opportunity: DueInitiativeOpportunity,
        result,
        now: str,
    ) -> bool:
        if not opportunity.decision_id:
            raise ValueError("claimed opportunity requires decision_id")
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                retry_delay = None
                if (
                    opportunity.opportunity_kind == "boot_checkin"
                    and result.status == "delivery_failed"
                ):
                    retry_delay = _BOOT_CHECKIN_DELIVERY_RETRY_DELAYS.get(
                        int(opportunity.attempt)
                    )
                if retry_delay is not None:
                    retry_at = (
                        datetime.fromisoformat(now) + retry_delay
                    ).isoformat()
                    updated = connection.execute(
                        """
                        UPDATE initiative_opportunities
                        SET status = 'deferred', delivery_id = NULL,
                            outcome_code = ?, lease_until = NULL,
                            next_attempt_at = ?, updated_at = ?
                        WHERE opportunity_id = ? AND status = 'delivering'
                        """,
                        (
                            "delivery_retry:"
                            f"{result.failure_reason or result.status}",
                            retry_at,
                            now,
                            opportunity.opportunity_id,
                        ),
                    )
                    if updated.rowcount == 1:
                        connection.execute(
                            """
                            UPDATE initiative_decisions
                            SET delivery_status = 'composing'
                            WHERE decision_id = ? AND delivery_status = 'dispatching'
                            """,
                            (opportunity.decision_id,),
                        )
                        connection.execute(
                            """
                            UPDATE companion_device_boot_events
                            SET status = 'scheduled', updated_at = ?
                            WHERE opportunity_id = ?
                            """,
                            (now, opportunity.opportunity_id),
                        )
                        connection.commit()
                        return True
                opportunity_updated = connection.execute(
                    """
                    UPDATE initiative_opportunities
                    SET status = ?, delivery_id = ?, outcome_code = ?,
                        updated_at = ?
                    WHERE opportunity_id = ? AND status = 'delivering'
                    """,
                    (
                        result.status,
                        result.delivery_id,
                        result.failure_reason or result.status,
                        now,
                        opportunity.opportunity_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE initiative_decisions SET delivery_status = ?
                    WHERE decision_id = ? AND delivery_status = 'dispatching'
                    """,
                    (result.status, opportunity.decision_id),
                )
                if (
                    opportunity_updated.rowcount == 1
                    and opportunity.opportunity_kind == "boot_checkin"
                ):
                    connection.execute(
                        """
                        UPDATE companion_device_boot_events
                        SET status = ?, updated_at = ?
                        WHERE opportunity_id = ?
                        """,
                        (
                            "delivered"
                            if result.status == "delivered"
                            else "delivery_failed",
                            now,
                            opportunity.opportunity_id,
                        ),
                    )
                if (
                    opportunity_updated.rowcount == 1
                    and opportunity.opportunity_kind == "connection_bid"
                    and result.status == "delivery_failed"
                ):
                    self._backoff_connection_need_in_connection(
                        connection,
                        owner_user_id=opportunity.owner_user_id,
                        pet_id=opportunity.pet_id,
                        memory_subject_id=opportunity.memory_subject_id,
                        relationship_epoch_id=opportunity.relationship_epoch_id,
                        decision_id=opportunity.decision_id,
                        now=now,
                    )
                connection.commit()
                return False
            except Exception:
                connection.rollback()
                raise

    def should_retry_initiative_delivery(
        self,
        *,
        opportunity: DueInitiativeOpportunity,
        status: str,
    ) -> bool:
        return (
            status == "delivery_failed"
            and opportunity.opportunity_kind == "boot_checkin"
            and int(opportunity.attempt) in _BOOT_CHECKIN_DELIVERY_RETRY_DELAYS
        )

    def retry_initiative_composition(
        self,
        *,
        opportunity: DueInitiativeOpportunity,
        error_code: str,
        now: str,
    ) -> None:
        next_attempt_at = (
            datetime.fromisoformat(now)
            + timedelta(seconds=min(30 * (2 ** max(opportunity.attempt - 1, 0)), 3600))
        ).isoformat()
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                updated = connection.execute(
                    """
                    UPDATE initiative_opportunities
                    SET status = 'scheduled', lease_until = NULL,
                        next_attempt_at = ?, outcome_code = ?, updated_at = ?
                    WHERE opportunity_id = ? AND status = 'claimed'
                    """,
                    (
                        next_attempt_at,
                        error_code,
                        now,
                        opportunity.opportunity_id,
                    ),
                )
                if updated.rowcount == 1 and opportunity.decision_id:
                    connection.execute(
                        """
                        UPDATE initiative_decisions SET delivery_status = 'composing'
                        WHERE decision_id = ?
                        """,
                        (opportunity.decision_id,),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def list_pending_observations(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
    ) -> tuple[Mapping[str, object], ...]:
        with self.connection() as connection:
            owner = connection.execute(
                "SELECT owner_user_id FROM companion_pets WHERE pet_id = ?",
                (pet_id,),
            ).fetchone()
            if owner is None:
                return ()
            if owner["owner_user_id"] != owner_user_id:
                raise PermissionError("owner does not control this personal pet")
            rows = connection.execute(
                """
                SELECT * FROM pending_companion_observations
                WHERE owner_user_id = ? AND pet_id = ? AND status = 'pending'
                  AND attempt_count < 3
                  AND (
                    expires_at IS NULL
                    OR julianday(expires_at) > julianday(?)
                  )
                ORDER BY julianday(occurred_at), created_at, observation_id
                """,
                (owner_user_id, pet_id, datetime.now(timezone.utc).isoformat()),
            ).fetchall()
        return tuple(
            {
                **dict(row),
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        )

    def load_pending_observation_diagnostics(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        now: str,
    ) -> Mapping[str, object]:
        with self.connection() as connection:
            owner = connection.execute(
                "SELECT owner_user_id FROM companion_pets WHERE pet_id = ?",
                (pet_id,),
            ).fetchone()
            if owner is None:
                return {
                    "pending_observations": (),
                    "pending_observations_by_status": {},
                }
            if owner["owner_user_id"] != owner_user_id:
                raise PermissionError("owner does not control this personal pet")
            pending = tuple(
                {
                    "observation_id": row["observation_id"],
                    "kind": row["kind"],
                    "source_kind": row["source_kind"],
                    "source_ref": row["source_ref"],
                    "safe_summary": row["safe_summary"],
                    "occurred_at": row["occurred_at"],
                    "queued_reason": row["queued_reason"],
                    "status": row["effective_status"],
                    "attempt_count": row["attempt_count"],
                    "last_error_code": row["last_error_code"],
                    "expires_at": row["expires_at"],
                }
                for row in connection.execute(
                    """
                    SELECT observation_id, kind, source_kind, source_ref,
                           safe_summary, occurred_at, queued_reason,
                           attempt_count, last_error_code, expires_at,
                           CASE
                             WHEN expires_at IS NOT NULL
                              AND julianday(expires_at) <= julianday(?)
                               THEN 'expired'
                             WHEN attempt_count >= 3 THEN 'failed'
                             ELSE 'pending'
                           END AS effective_status
                    FROM pending_companion_observations
                    WHERE pet_id = ?
                    ORDER BY occurred_at DESC, observation_id DESC
                    LIMIT 250
                    """,
                    (now, pet_id),
                )
            )
            counts = {
                row["effective_status"]: row["count"]
                for row in connection.execute(
                    """
                    SELECT effective_status, COUNT(*) AS count
                    FROM (
                      SELECT CASE
                        WHEN expires_at IS NOT NULL
                         AND julianday(expires_at) <= julianday(?)
                          THEN 'expired'
                        WHEN attempt_count >= 3 THEN 'failed'
                        ELSE 'pending'
                      END AS effective_status
                      FROM pending_companion_observations
                      WHERE pet_id = ?
                    )
                    GROUP BY effective_status ORDER BY effective_status
                    """,
                    (now, pet_id),
                )
            }
        return {
            "pending_observations": pending,
            "pending_observations_by_status": counts,
        }

    def mark_pending_observation_failure(
        self,
        *,
        observation_id: str,
        pending_digest: str,
        error_code: str,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE pending_companion_observations
                SET attempt_count = attempt_count + 1,
                    last_error_code = ?
                WHERE observation_id = ? AND pending_digest = ?
                  AND status = 'pending' AND attempt_count < 3
                """,
                (error_code, observation_id, pending_digest),
            )
            connection.commit()

    def delete_pending_observation(
        self,
        *,
        observation_id: str,
        pending_digest: str,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                DELETE FROM pending_companion_observations
                WHERE observation_id = ? AND pending_digest = ?
                """,
                (observation_id, pending_digest),
            )
            connection.commit()

    def record_observation(
        self,
        observation: CompanionObservation,
        *,
        observation_id: str,
        evidence: tuple[CompanionEvidence, ...] = (),
        opportunities: tuple[PendingInitiativeOpportunity, ...] = (),
    ) -> CompanionObserveResult:
        digest_payload = {
            "owner_user_id": observation.subject.owner_user_id,
            "pet_id": observation.subject.pet_id,
            "memory_subject_id": observation.subject.memory_subject_id,
            "kind": observation.kind,
            "source_kind": observation.source_kind,
            "source_ref": observation.source_ref,
            "occurred_at": observation.occurred_at,
            "payload": dict(observation.payload),
            "safe_summary": observation.safe_summary,
        }
        observation_json = _stable_json(digest_payload)
        observation_digest = hashlib.sha256(
            observation_json.encode("utf-8")
        ).hexdigest()
        subject = observation.subject
        for item in evidence:
            if item.pet_id != subject.pet_id:
                raise ValueError("Evidence pet_id does not match observation")
            if item.memory_subject_id != subject.memory_subject_id:
                raise ValueError(
                    "Evidence memory_subject_id does not match observation"
                )
        evidence_ids = {item.evidence_id for item in evidence}
        for opportunity in opportunities:
            if not opportunity.evidence_ids:
                raise ValueError("initiative opportunity requires Evidence")
            if not set(opportunity.evidence_ids) <= evidence_ids:
                raise ValueError(
                    "initiative opportunity must cite observation Evidence"
                )
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """
                    SELECT observation_id, observation_digest
                    FROM companion_observations
                    WHERE idempotency_key = ?
                    """,
                    (observation.idempotency_key,),
                ).fetchone()
                if existing is not None:
                    if existing["observation_digest"] != observation_digest:
                        raise CompanionIdempotencyConflict(
                            "observation idempotency key conflicts with "
                            "different content"
                        )
                    evidence_ids = tuple(
                        row[0]
                        for row in connection.execute(
                            """
                            SELECT evidence_id
                            FROM observation_evidence
                            WHERE observation_id = ?
                            ORDER BY evidence_id
                            """,
                            (existing["observation_id"],),
                        )
                    )
                    connection.commit()
                    return CompanionObserveResult(
                        observation_id=existing["observation_id"],
                        status="duplicate",
                        evidence_ids=evidence_ids,
                    )
                inserted_pet = connection.execute(
                    """
                    INSERT OR IGNORE INTO companion_pets(
                        pet_id, owner_user_id, created_at
                    ) VALUES (?, ?, ?)
                    """,
                    (subject.pet_id, subject.owner_user_id, observation.occurred_at),
                )
                self._assert_owner_in_connection(
                    connection,
                    owner_user_id=subject.owner_user_id,
                    pet_id=subject.pet_id,
                )
                self._ensure_birth_temperament_in_connection(
                    connection,
                    pet_id=subject.pet_id,
                    generated_at=(
                        observation.occurred_at
                        if inserted_pet.rowcount == 1
                        else datetime.now(timezone.utc).isoformat()
                    ),
                    source_kind=(
                        "pet_created"
                        if inserted_pet.rowcount == 1
                        else "legacy_backfill"
                    ),
                )
                epoch = connection.execute(
                    """
                    SELECT epoch_id
                    FROM relationship_epochs
                    WHERE pet_id = ? AND ended_at IS NULL
                    """,
                    (subject.pet_id,),
                ).fetchone()
                if epoch is None:
                    relationship_epoch_id = str(uuid4())
                    connection.execute(
                        """
                        INSERT INTO relationship_epochs(
                            epoch_id, pet_id, started_at, start_reason
                        ) VALUES (?, ?, ?, 'first_observation')
                        """,
                        (
                            relationship_epoch_id,
                            subject.pet_id,
                            observation.occurred_at,
                        ),
                    )
                else:
                    relationship_epoch_id = str(epoch["epoch_id"])
                effective_evidence = tuple(
                    replace(
                        item,
                        relationship_epoch_id=(
                            relationship_epoch_id
                            if item.ownership_scope == "relationship"
                            else None
                        ),
                    )
                    for item in evidence
                )
                created_at = datetime.now(timezone.utc).isoformat()
                connection.execute(
                    """
                    INSERT INTO companion_observations(
                        observation_id, idempotency_key, owner_user_id,
                        pet_id, memory_subject_id, kind, source_kind,
                        source_ref, payload_json, observation_digest,
                        safe_summary, occurred_at, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'recorded', ?)
                    """,
                    (
                        observation_id,
                        observation.idempotency_key,
                        subject.owner_user_id,
                        subject.pet_id,
                        subject.memory_subject_id,
                        observation.kind,
                        observation.source_kind,
                        observation.source_ref,
                        _stable_json(observation.payload),
                        observation_digest,
                        observation.safe_summary,
                        observation.occurred_at,
                        created_at,
                    ),
                )
                for item in effective_evidence:
                    connection.execute(
                        """
                        INSERT INTO companion_evidence(
                            evidence_id, pet_id, memory_subject_id,
                            ownership_scope, relationship_epoch_id, kind,
                            content_json, fact_key, importance, sensitivity,
                            valid_from, valid_until, source_kind, source_ref,
                            source_summary, attribution, confidence, occurred_at,
                            retention, status, prompt_eligible, expires_at,
                            created_at, speaker_identity
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        (
                            item.evidence_id,
                            item.pet_id,
                            item.memory_subject_id,
                            item.ownership_scope,
                            item.relationship_epoch_id,
                            item.kind,
                            _stable_json(item.content),
                            item.fact_key,
                            item.importance,
                            item.sensitivity,
                            item.valid_from,
                            item.valid_until,
                            item.source_kind,
                            item.source_ref,
                            item.source_summary,
                            item.attribution,
                            item.confidence,
                            item.occurred_at,
                            item.retention,
                            item.status,
                            int(item.prompt_eligible),
                            item.expires_at,
                            created_at,
                            item.speaker_identity,
                        ),
                    )
                self._supersede_replaced_facts(
                    connection,
                    evidence=effective_evidence,
                    created_at=created_at,
                )
                for item in effective_evidence:
                    connection.execute(
                        """
                        INSERT INTO observation_evidence(
                            observation_id, evidence_id, pet_id
                        ) VALUES (?, ?, ?)
                        """,
                        (observation_id, item.evidence_id, subject.pet_id),
                    )
                self._insert_initiative_opportunities(
                    connection,
                    opportunities=opportunities,
                    owner_user_id=subject.owner_user_id,
                    pet_id=subject.pet_id,
                    memory_subject_id=subject.memory_subject_id,
                    relationship_epoch_id=relationship_epoch_id,
                    created_at=created_at,
                )
                self._insert_reminder_result_opportunity(
                    connection,
                    observation=observation,
                    observation_id=observation_id,
                    relationship_epoch_id=relationship_epoch_id,
                    created_at=created_at,
                )
                connection.commit()
                return CompanionObserveResult(
                    observation_id=observation_id,
                    status="recorded",
                    evidence_ids=tuple(item.evidence_id for item in effective_evidence),
                )
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _insert_initiative_opportunities(
        connection: sqlite3.Connection,
        *,
        opportunities: tuple[PendingInitiativeOpportunity, ...],
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        created_at: str,
    ) -> None:
        for opportunity in opportunities:
            connection.execute(
                """
                INSERT INTO initiative_opportunities(
                    opportunity_id, owner_user_id, pet_id, memory_subject_id,
                    relationship_epoch_id, opportunity_kind, reason_code,
                    evidence_ids_json, safe_brief, due_at, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, ?)
                """,
                (
                    opportunity.opportunity_id,
                    owner_user_id,
                    pet_id,
                    memory_subject_id,
                    relationship_epoch_id,
                    opportunity.opportunity_kind,
                    opportunity.reason_code,
                    _stable_json(opportunity.evidence_ids),
                    opportunity.safe_brief,
                    opportunity.due_at,
                    created_at,
                    created_at,
                ),
            )

    @staticmethod
    def _insert_connection_turn_event_in_connection(
        connection: sqlite3.Connection,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        decision_id: str,
        turn_id: str,
        outcome: str,
        reason_code: str,
        occurred_at: str,
        created_at: str,
    ) -> None:
        event_kind = {
            "connection_responded": "connection_responded",
            "rejected": "connection_rejected",
            "ignored": "connection_ignored",
        }[outcome]
        summary = {
            "connection_responded": "用户在反馈窗口内回应了这次连接主动。",
            "rejected": "用户明确拒绝了这次连接主动。",
            "ignored": "这次连接主动在反馈窗口内没有收到回应。",
        }[outcome]
        idempotency_key = f"{event_kind}:{decision_id}:{turn_id}"
        observation_id = str(
            uuid5(
                NAMESPACE_URL,
                f"companion-observation:{pet_id}:{idempotency_key}",
            )
        )
        payload = {
            "decision_id": decision_id,
            "turn_id": turn_id,
            "outcome": outcome,
            "reason_code": reason_code,
        }
        observation_digest = hashlib.sha256(
            _stable_json(
                {
                    "owner_user_id": owner_user_id,
                    "pet_id": pet_id,
                    "memory_subject_id": memory_subject_id,
                    "kind": event_kind,
                    "source_kind": "turn",
                    "source_ref": turn_id,
                    "occurred_at": occurred_at,
                    "payload": payload,
                    "safe_summary": summary,
                }
            ).encode("utf-8")
        ).hexdigest()
        connection.execute(
            """
            INSERT OR IGNORE INTO companion_observations(
                observation_id, idempotency_key, owner_user_id,
                pet_id, memory_subject_id, kind, source_kind,
                source_ref, payload_json, observation_digest,
                safe_summary, occurred_at, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'turn', ?, ?, ?, ?, ?,
                      'recorded', ?)
            """,
            (
                observation_id,
                idempotency_key,
                owner_user_id,
                pet_id,
                memory_subject_id,
                event_kind,
                turn_id,
                _stable_json(payload),
                observation_digest,
                summary,
                occurred_at,
                created_at,
            ),
        )
        evidence_id = str(
            uuid5(NAMESPACE_URL, f"xiaoxin:{event_kind}:{decision_id}:{turn_id}")
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO companion_evidence(
                evidence_id, pet_id, memory_subject_id,
                ownership_scope, relationship_epoch_id, kind,
                content_json, source_kind, source_ref, source_summary,
                attribution, confidence, occurred_at, retention,
                status, prompt_eligible, created_at
            ) VALUES (?, ?, ?, 'relationship', ?, ?, ?, 'turn', ?, ?,
                      ?, 1.0, ?, 'long_term', 'active', 0, ?)
            """,
            (
                evidence_id,
                pet_id,
                memory_subject_id,
                relationship_epoch_id,
                event_kind,
                _stable_json(payload),
                turn_id,
                summary,
                (
                    "observed_user_response"
                    if outcome == "connection_responded"
                    else "explicit_user_feedback"
                    if outcome == "rejected"
                    else "observed_feedback_window"
                ),
                occurred_at,
                created_at,
            ),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO observation_evidence(
                observation_id, evidence_id, pet_id
            ) VALUES (?, ?, ?)
            """,
            (observation_id, evidence_id, pet_id),
        )
        connection.execute(
            """
            UPDATE initiative_decisions SET delivery_status = ?
            WHERE decision_id = ? AND delivery_status = 'delivered'
            """,
            (outcome, decision_id),
        )

    @classmethod
    def _upsert_connection_need_in_connection(
        cls,
        connection: sqlite3.Connection,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        update: PendingConnectionNeedUpdate,
        created_at: str,
    ) -> None:
        boot_responded = cls._record_boot_checkin_response_in_connection(
            connection,
            owner_user_id=owner_user_id,
            pet_id=pet_id,
            memory_subject_id=memory_subject_id,
            relationship_epoch_id=relationship_epoch_id,
            interaction_at=update.last_meaningful_interaction_at,
            feedback_window_seconds=update.feedback_window_seconds,
            presence_window_seconds=update.presence_window_seconds,
            created_at=created_at,
        )
        if update.feedback_outcome != "rejected" and not boot_responded:
            interaction_at = datetime.fromisoformat(
                update.last_meaningful_interaction_at
            )
            cls._open_presence_lease_in_connection(
                connection,
                owner_user_id=owner_user_id,
                pet_id=pet_id,
                memory_subject_id=memory_subject_id,
                relationship_epoch_id=relationship_epoch_id,
                device_id=None,
                source="conversation",
                source_ref=update.turn_id,
                opened_at=update.last_meaningful_interaction_at,
                expires_at=(
                    interaction_at
                    + timedelta(seconds=max(int(update.presence_window_seconds), 1))
                ).isoformat(),
                updated_at=created_at,
            )
        existing = connection.execute(
            """
            SELECT * FROM companion_relationship_needs
            WHERE owner_user_id = ? AND pet_id = ?
              AND memory_subject_id = ? AND relationship_epoch_id = ?
              AND need_kind = 'connection'
            """,
            (
                owner_user_id,
                pet_id,
                memory_subject_id,
                relationship_epoch_id,
            ),
        ).fetchone()
        if existing is not None and existing["pending_decision_id"] is not None:
            pending = connection.execute(
                """
                SELECT opportunity.status, opportunity.updated_at,
                       opportunity.reason_code, decision.delivery_status
                FROM initiative_opportunities AS opportunity
                JOIN initiative_decisions AS decision
                  ON decision.decision_id = opportunity.decision_id
                WHERE opportunity.decision_id = ?
                  AND opportunity.opportunity_kind = 'connection_bid'
                """,
                (existing["pending_decision_id"],),
            ).fetchone()
            if (
                pending is None
                or pending["status"] != "delivered"
                or pending["delivery_status"] != "delivered"
            ):
                return
            decision_id = str(existing["pending_decision_id"])
            interaction_at = datetime.fromisoformat(
                update.last_meaningful_interaction_at
            )
            delivered_at = datetime.fromisoformat(str(pending["updated_at"]))
            feedback_deadline = delivered_at + timedelta(
                seconds=max(int(update.feedback_window_seconds), 1)
            )
            if update.feedback_outcome == "rejected":
                cls._insert_connection_turn_event_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    relationship_epoch_id=relationship_epoch_id,
                    decision_id=decision_id,
                    turn_id=update.turn_id,
                    outcome="rejected",
                    reason_code=str(pending["reason_code"]),
                    occurred_at=update.last_meaningful_interaction_at,
                    created_at=created_at,
                )
                cls._apply_connection_feedback_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    relationship_epoch_id=relationship_epoch_id,
                    decision_id=decision_id,
                    outcome="rejected",
                    now=update.last_meaningful_interaction_at,
                )
            elif delivered_at <= interaction_at <= feedback_deadline:
                cls._insert_connection_turn_event_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    relationship_epoch_id=relationship_epoch_id,
                    decision_id=decision_id,
                    turn_id=update.turn_id,
                    outcome="connection_responded",
                    reason_code=str(pending["reason_code"]),
                    occurred_at=update.last_meaningful_interaction_at,
                    created_at=created_at,
                )
                connection.execute(
                    """
                    UPDATE initiative_opportunities
                    SET status = 'invalidated',
                        outcome_code = 'connection_responded', updated_at = ?
                    WHERE decision_id = ?
                      AND opportunity_kind = 'connection_bid'
                      AND status = 'delivered'
                    """,
                    (update.last_meaningful_interaction_at, decision_id),
                )
                connection.execute(
                    """
                    UPDATE companion_relationship_needs
                    SET last_meaningful_interaction_at = ?,
                        source_evidence_id = ?, pending_decision_id = NULL,
                        ignored_streak = 0, cooldown_until = NULL,
                        next_eligible_at = ?, initiative_bias = ?,
                        relationship_stage = ?, initiative_level = ?,
                        threshold_seconds = ?,
                        version = version + 1, updated_at = ?
                    WHERE owner_user_id = ? AND pet_id = ?
                      AND memory_subject_id = ? AND relationship_epoch_id = ?
                      AND need_kind = 'connection'
                      AND pending_decision_id = ?
                    """,
                    (
                        update.last_meaningful_interaction_at,
                        update.source_evidence_id,
                        update.next_eligible_at,
                        update.initiative_bias,
                        update.relationship_stage,
                        update.initiative_level,
                        update.threshold_seconds,
                        created_at,
                        owner_user_id,
                        pet_id,
                        memory_subject_id,
                        relationship_epoch_id,
                        decision_id,
                    ),
                )
                return
            elif interaction_at > feedback_deadline:
                cls._insert_connection_turn_event_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    relationship_epoch_id=relationship_epoch_id,
                    decision_id=decision_id,
                    turn_id=update.turn_id,
                    outcome="ignored",
                    reason_code=str(pending["reason_code"]),
                    occurred_at=feedback_deadline.isoformat(),
                    created_at=created_at,
                )
                cls._apply_connection_feedback_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    relationship_epoch_id=relationship_epoch_id,
                    decision_id=decision_id,
                    outcome="ignored",
                    now=feedback_deadline.isoformat(),
                )
            else:
                return
            existing = connection.execute(
                """
                SELECT * FROM companion_relationship_needs
                WHERE owner_user_id = ? AND pet_id = ?
                  AND memory_subject_id = ? AND relationship_epoch_id = ?
                  AND need_kind = 'connection'
                """,
                (
                    owner_user_id,
                    pet_id,
                    memory_subject_id,
                    relationship_epoch_id,
                ),
            ).fetchone()
        connection.execute(
            """
            UPDATE initiative_opportunities
            SET status = 'invalidated', outcome_code = 'interaction_resumed',
                updated_at = ?
            WHERE owner_user_id = ? AND pet_id = ? AND memory_subject_id = ?
              AND relationship_epoch_id = ?
              AND opportunity_kind = 'connection_bid'
              AND status IN ('scheduled', 'deferred')
            """,
            (
                created_at,
                owner_user_id,
                pet_id,
                memory_subject_id,
                relationship_epoch_id,
            ),
        )
        next_eligible_at = update.next_eligible_at
        if existing is not None and int(existing["ignored_streak"]) > 0:
            delay_seconds = _connection_ignore_backoff_seconds(
                threshold_seconds=update.threshold_seconds,
                initiative_bias=update.initiative_bias,
                ignored_streak=int(existing["ignored_streak"]),
            )
            next_eligible_at = (
                datetime.fromisoformat(update.last_meaningful_interaction_at)
                + timedelta(seconds=delay_seconds)
            ).isoformat()
        if existing is not None and existing["cooldown_until"] is not None:
            cooldown_until = datetime.fromisoformat(str(existing["cooldown_until"]))
            if cooldown_until > datetime.fromisoformat(next_eligible_at):
                next_eligible_at = cooldown_until.isoformat()
        connection.execute(
            """
            INSERT INTO companion_relationship_needs(
                owner_user_id, pet_id, memory_subject_id,
                relationship_epoch_id, need_kind,
                last_meaningful_interaction_at, source_evidence_id,
                next_eligible_at, initiative_bias, relationship_stage,
                initiative_level, threshold_seconds, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'connection', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                owner_user_id, pet_id, memory_subject_id,
                relationship_epoch_id, need_kind
            ) DO UPDATE SET
                last_meaningful_interaction_at = CASE
                    WHEN companion_relationship_needs.pending_decision_id IS NULL
                    THEN excluded.last_meaningful_interaction_at
                    ELSE companion_relationship_needs.last_meaningful_interaction_at
                END,
                source_evidence_id = CASE
                    WHEN companion_relationship_needs.pending_decision_id IS NULL
                    THEN excluded.source_evidence_id
                    ELSE companion_relationship_needs.source_evidence_id
                END,
                next_eligible_at = CASE
                    WHEN companion_relationship_needs.pending_decision_id IS NULL
                    THEN excluded.next_eligible_at
                    ELSE companion_relationship_needs.next_eligible_at
                END,
                initiative_bias = CASE
                    WHEN companion_relationship_needs.pending_decision_id IS NULL
                    THEN excluded.initiative_bias
                    ELSE companion_relationship_needs.initiative_bias
                END,
                relationship_stage = CASE
                    WHEN companion_relationship_needs.pending_decision_id IS NULL
                    THEN excluded.relationship_stage
                    ELSE companion_relationship_needs.relationship_stage
                END,
                initiative_level = CASE
                    WHEN companion_relationship_needs.pending_decision_id IS NULL
                    THEN excluded.initiative_level
                    ELSE companion_relationship_needs.initiative_level
                END,
                threshold_seconds = CASE
                    WHEN companion_relationship_needs.pending_decision_id IS NULL
                    THEN excluded.threshold_seconds
                    ELSE companion_relationship_needs.threshold_seconds
                END,
                version = CASE
                    WHEN companion_relationship_needs.pending_decision_id IS NULL
                    THEN companion_relationship_needs.version + 1
                    ELSE companion_relationship_needs.version
                END,
                updated_at = CASE
                    WHEN companion_relationship_needs.pending_decision_id IS NULL
                    THEN excluded.updated_at
                    ELSE companion_relationship_needs.updated_at
                END
            """,
            (
                owner_user_id,
                pet_id,
                memory_subject_id,
                relationship_epoch_id,
                update.last_meaningful_interaction_at,
                update.source_evidence_id,
                next_eligible_at,
                update.initiative_bias,
                update.relationship_stage,
                update.initiative_level,
                update.threshold_seconds,
                created_at,
                created_at,
            ),
        )

    @classmethod
    def _reschedule_connection_need_for_initiative_level_in_connection(
        cls,
        connection: sqlite3.Connection,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        target_level: str | None,
        now: str,
    ) -> None:
        blocked_decision_ids: set[str] = set()
        if target_level == "disabled":
            decision_rows = connection.execute(
                """
                SELECT decision_id FROM initiative_opportunities
                WHERE owner_user_id = ? AND pet_id = ? AND memory_subject_id = ?
                  AND status IN ('scheduled', 'deferred', 'claimed')
                  AND decision_id IS NOT NULL
                """,
                (owner_user_id, pet_id, memory_subject_id),
            ).fetchall()
            blocked_decision_ids = {
                str(decision["decision_id"]) for decision in decision_rows
            }
            connection.execute(
                """
                UPDATE initiative_opportunities
                SET status = 'blocked', outcome_code = 'disabled',
                    lease_until = NULL, next_attempt_at = NULL, updated_at = ?
                WHERE owner_user_id = ? AND pet_id = ? AND memory_subject_id = ?
                  AND status IN ('scheduled', 'deferred', 'claimed')
                """,
                (now, owner_user_id, pet_id, memory_subject_id),
            )
            for decision_id in blocked_decision_ids:
                connection.execute(
                    """
                    UPDATE initiative_decisions SET delivery_status = 'invalidated'
                    WHERE decision_id = ?
                      AND delivery_status IN ('pending', 'composing')
                    """,
                    (decision_id,),
                )
        need = connection.execute(
            """
            SELECT * FROM companion_relationship_needs
            WHERE owner_user_id = ? AND pet_id = ? AND memory_subject_id = ?
              AND need_kind = 'connection'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (owner_user_id, pet_id, memory_subject_id),
        ).fetchone()
        if need is None:
            return
        effective_level = target_level or default_initiative_level(
            str(need["relationship_stage"])
        )
        previous_level = str(need["initiative_level"])
        threshold_seconds = rescale_connection_threshold(
            int(need["threshold_seconds"]),
            previous_level=previous_level,
            next_level=effective_level,
        )
        delay_seconds = threshold_seconds
        if int(need["ignored_streak"]) > 0:
            delay_seconds = _connection_ignore_backoff_seconds(
                threshold_seconds=threshold_seconds,
                initiative_bias=str(need["initiative_bias"]),
                ignored_streak=int(need["ignored_streak"]),
            )
        next_eligible_at = (
            datetime.fromisoformat(str(need["last_meaningful_interaction_at"]))
            + timedelta(seconds=delay_seconds)
        )
        if need["cooldown_until"] is not None:
            cooldown_until = datetime.fromisoformat(str(need["cooldown_until"]))
            if cooldown_until > next_eligible_at:
                next_eligible_at = cooldown_until

        pending_was_invalidated = (
            need["pending_decision_id"] is not None
            and str(need["pending_decision_id"]) in blocked_decision_ids
        )
        if need["pending_decision_id"] is not None:
            if not pending_was_invalidated:
                invalidated = connection.execute(
                    """
                    UPDATE initiative_opportunities
                    SET status = 'invalidated', outcome_code = 'cadence_changed',
                        lease_until = NULL, next_attempt_at = NULL, updated_at = ?
                    WHERE decision_id = ? AND opportunity_kind = 'connection_bid'
                      AND status IN ('scheduled', 'deferred', 'claimed')
                    """,
                    (now, need["pending_decision_id"]),
                )
                pending_was_invalidated = invalidated.rowcount > 0
                if pending_was_invalidated:
                    connection.execute(
                        """
                        UPDATE initiative_decisions SET delivery_status = 'invalidated'
                        WHERE decision_id = ?
                          AND delivery_status IN ('pending', 'composing')
                        """,
                        (need["pending_decision_id"],),
                    )

        connection.execute(
            """
            UPDATE companion_relationship_needs
            SET initiative_level = ?, threshold_seconds = ?,
                next_eligible_at = ?,
                pending_decision_id = CASE WHEN ? THEN NULL
                                           ELSE pending_decision_id END,
                version = version + 1, updated_at = ?
            WHERE owner_user_id = ? AND pet_id = ? AND memory_subject_id = ?
              AND relationship_epoch_id = ? AND need_kind = 'connection'
            """,
            (
                effective_level,
                threshold_seconds,
                next_eligible_at.isoformat(),
                int(pending_was_invalidated),
                now,
                owner_user_id,
                pet_id,
                memory_subject_id,
                need["relationship_epoch_id"],
            ),
        )

    @classmethod
    def _insert_reminder_result_opportunity(
        cls,
        connection: sqlite3.Connection,
        *,
        observation: CompanionObservation,
        observation_id: str,
        relationship_epoch_id: str,
        created_at: str,
    ) -> None:
        if observation.kind != "reminder_tts_completed":
            return
        todo_id = observation.payload.get("todo_id")
        if not isinstance(todo_id, str) or not todo_id.strip():
            return
        evidence = connection.execute(
            """
            SELECT evidence_id
            FROM companion_evidence
            WHERE pet_id = ? AND memory_subject_id = ?
              AND fact_key = ? AND kind = 'future_event'
              AND status = 'active' AND prompt_eligible = 1
            ORDER BY occurred_at DESC, evidence_id DESC
            LIMIT 1
            """,
            (
                observation.subject.pet_id,
                observation.subject.memory_subject_id,
                f"todo:{todo_id}",
            ),
        ).fetchone()
        if evidence is None:
            return
        due_at = (
            datetime.fromisoformat(observation.occurred_at) + timedelta(minutes=15)
        ).isoformat()
        cls._insert_initiative_opportunities(
            connection,
            opportunities=(
                PendingInitiativeOpportunity(
                    opportunity_id=str(
                        uuid5(
                            NAMESPACE_URL,
                            "xiaoxin:initiative-opportunity:reminder-result:"
                            f"{observation_id}",
                        )
                    ),
                    opportunity_kind="reminder_result",
                    reason_code="reminder_result_checkin",
                    evidence_ids=(evidence["evidence_id"],),
                    safe_brief="刚才的一项待办提醒已完成播放。",
                    due_at=due_at,
                ),
            ),
            owner_user_id=observation.subject.owner_user_id,
            pet_id=observation.subject.pet_id,
            memory_subject_id=observation.subject.memory_subject_id,
            relationship_epoch_id=relationship_epoch_id,
            created_at=created_at,
        )

    def commit_turn(
        self,
        prepared: PreparedCompanionTurn,
        outcome: CompanionTurnOutcome,
        *,
        academic_stage: str | None = None,
        pending_evidence: tuple[PendingCompanionEvidence, ...] = (),
        evidence: tuple[CompanionEvidence, ...] = (),
        jobs: tuple[PendingCompanionJob, ...] = (),
        opportunities: tuple[PendingInitiativeOpportunity, ...] = (),
        connection_need_update: PendingConnectionNeedUpdate | None = None,
    ) -> CompanionCommitResult:
        outcome_digest = _outcome_digest(outcome)
        for item in evidence:
            if item.pet_id != prepared.pet_id:
                raise ValueError("Evidence pet_id does not match prepared turn")
            if item.memory_subject_id != prepared.memory_subject_id:
                raise ValueError(
                    "Evidence memory_subject_id does not match prepared turn"
                )
        for job in jobs:
            if job.pet_id != prepared.pet_id:
                raise ValueError("job pet_id does not match prepared turn")
        pending_evidence_ids = {item.evidence_id for item in pending_evidence}
        if (
            connection_need_update is not None
            and connection_need_update.source_evidence_id not in pending_evidence_ids
        ):
            raise ValueError("connection need must cite turn Evidence")
        for opportunity in opportunities:
            if not set(opportunity.evidence_ids) <= pending_evidence_ids:
                raise ValueError("initiative opportunity must cite turn Evidence")
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """
                    SELECT request_digest, outcome_digest
                    FROM companion_turns
                    WHERE turn_id = ? AND pet_id = ?
                    """,
                    (prepared.turn_id, prepared.pet_id),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["request_digest"] == prepared.request_digest
                        and existing["outcome_digest"] == outcome_digest
                    ):
                        connection.commit()
                        return CompanionCommitResult(
                            turn_id=prepared.turn_id,
                            status="already_committed",
                            evidence_ids=tuple(
                                row[0]
                                for row in connection.execute(
                                    """
                                    SELECT evidence_id
                                    FROM companion_evidence
                                    WHERE pet_id = ?
                                      AND memory_subject_id = ?
                                      AND source_kind = 'turn'
                                      AND source_ref = ?
                                    ORDER BY evidence_id
                                    """,
                                    (
                                        prepared.pet_id,
                                        prepared.memory_subject_id,
                                        prepared.turn_id,
                                    ),
                                )
                            ),
                            job_ids=tuple(
                                row[0]
                                for row in connection.execute(
                                    """
                                    SELECT job_id
                                    FROM consolidation_jobs
                                    WHERE pet_id = ?
                                      AND json_extract(payload_json, '$.turn_id') = ?
                                    ORDER BY job_id
                                    """,
                                    (prepared.pet_id, prepared.turn_id),
                                )
                            ),
                        )
                    raise CompanionIdempotencyConflict(
                        f"turn {prepared.turn_id!r} for pet {prepared.pet_id!r} "
                        "was already committed with different content"
                    )
                inserted_pet = connection.execute(
                    """
                    INSERT OR IGNORE INTO companion_pets(
                        pet_id, owner_user_id, created_at
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        prepared.pet_id,
                        prepared.owner_user_id,
                        prepared.occurred_at,
                    ),
                )
                self._assert_owner_in_connection(
                    connection,
                    owner_user_id=prepared.owner_user_id,
                    pet_id=prepared.pet_id,
                )
                self._ensure_birth_temperament_in_connection(
                    connection,
                    pet_id=prepared.pet_id,
                    generated_at=(
                        prepared.occurred_at
                        if inserted_pet.rowcount == 1
                        else datetime.now(timezone.utc).isoformat()
                    ),
                    source_kind=(
                        "pet_created"
                        if inserted_pet.rowcount == 1
                        else "legacy_backfill"
                    ),
                )
                epoch = connection.execute(
                    """
                    SELECT epoch_id
                    FROM relationship_epochs
                    WHERE pet_id = ? AND ended_at IS NULL
                    """,
                    (prepared.pet_id,),
                ).fetchone()
                if epoch is None:
                    relationship_epoch_id = str(uuid4())
                    connection.execute(
                        """
                        INSERT INTO relationship_epochs(
                            epoch_id, pet_id, started_at, start_reason
                        ) VALUES (?, ?, ?, 'first_use')
                        """,
                        (
                            relationship_epoch_id,
                            prepared.pet_id,
                            prepared.occurred_at,
                        ),
                    )
                else:
                    relationship_epoch_id = epoch["epoch_id"]
                if (
                    prepared.relationship_epoch_id is not None
                    and prepared.relationship_epoch_id != relationship_epoch_id
                ):
                    raise CompanionIdempotencyConflict(
                        "prepared relationship epoch is no longer active"
                    )
                if academic_stage is not None:
                    self._sync_academic_stage_in_connection(
                        connection,
                        pet_id=prepared.pet_id,
                        memory_subject_id=prepared.memory_subject_id,
                        relationship_epoch_id=relationship_epoch_id,
                        academic_stage=academic_stage,
                        now=prepared.occurred_at,
                        initial_only=True,
                    )
                turn_evidence = evidence + tuple(
                    CompanionEvidence(
                        evidence_id=item.evidence_id,
                        pet_id=prepared.pet_id,
                        memory_subject_id=prepared.memory_subject_id,
                        ownership_scope=item.ownership_scope,
                        relationship_epoch_id=(
                            relationship_epoch_id
                            if item.ownership_scope == "relationship"
                            else None
                        ),
                        kind=item.kind,
                        content=item.content,
                        source_kind="turn",
                        source_ref=prepared.turn_id,
                        source_summary=item.source_summary,
                        attribution=item.attribution,
                        confidence=item.confidence,
                        occurred_at=prepared.occurred_at,
                        retention=item.retention,
                        status="active",
                        prompt_eligible=item.prompt_eligible,
                        expires_at=item.expires_at,
                    )
                    for item in pending_evidence
                )
                for item in turn_evidence:
                    if (
                        item.ownership_scope == "relationship"
                        and item.relationship_epoch_id != relationship_epoch_id
                    ):
                        raise ValueError(
                            "relationship Evidence epoch does not match active turn epoch"
                        )
                effective_jobs = tuple(
                    replace(
                        job,
                        relationship_epoch_id=(
                            relationship_epoch_id
                            if job.relationship_epoch_id is None
                            else job.relationship_epoch_id
                        ),
                    )
                    for job in jobs
                )
                for job in effective_jobs:
                    if job.relationship_epoch_id != relationship_epoch_id:
                        raise ValueError(
                            "job relationship epoch does not match active turn epoch"
                        )
                connection.execute(
                    """
                    INSERT INTO companion_turns(
                        turn_id,
                        owner_user_id,
                        pet_id,
                        memory_subject_id,
                        relationship_epoch_id,
                        policy_version,
                        request_digest,
                        outcome_digest,
                        occurred_at,
                        committed_at,
                        status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prepared.turn_id,
                        prepared.owner_user_id,
                        prepared.pet_id,
                        prepared.memory_subject_id,
                        relationship_epoch_id,
                        prepared.policy.version,
                        prepared.request_digest,
                        outcome_digest,
                        prepared.occurred_at,
                        datetime.now(timezone.utc).isoformat(),
                        outcome.delivery_status,
                    ),
                )
                if prepared.source_text is not None:
                    connection.execute(
                        """
                        INSERT INTO companion_turn_sources(
                            turn_id, pet_id, memory_subject_id, source_text,
                            source_digest, occurred_at, expires_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            prepared.turn_id,
                            prepared.pet_id,
                            prepared.memory_subject_id,
                            prepared.source_text,
                            hashlib.sha256(
                                prepared.source_text.encode("utf-8")
                            ).hexdigest(),
                            prepared.occurred_at,
                            (
                                datetime.fromisoformat(prepared.occurred_at)
                                + timedelta(days=1)
                            ).isoformat(),
                        ),
                    )
                    if prepared.conversation_digest is not None:
                        context_expires_at = (
                            datetime.fromisoformat(prepared.occurred_at)
                            + timedelta(minutes=30)
                        ).isoformat()
                        context_messages = (
                            ("user", prepared.source_text),
                            ("assistant", outcome.visible_response[:2000]),
                        )
                        connection.executemany(
                            """
                            INSERT INTO companion_context_messages(
                                message_id, turn_id, role, pet_id,
                                memory_subject_id, conversation_digest,
                                source_text, source_digest, occurred_at,
                                expires_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                (
                                    f"{prepared.turn_id}:{role}",
                                    prepared.turn_id,
                                    role,
                                    prepared.pet_id,
                                    prepared.memory_subject_id,
                                    prepared.conversation_digest,
                                    text,
                                    hashlib.sha256(text.encode("utf-8")).hexdigest(),
                                    prepared.occurred_at,
                                    context_expires_at,
                                )
                                for role, text in context_messages
                            ),
                        )
                created_at = datetime.now(timezone.utc).isoformat()
                for item in turn_evidence:
                    connection.execute(
                        """
                        INSERT INTO companion_evidence(
                            evidence_id,
                            pet_id,
                            memory_subject_id,
                            ownership_scope,
                            relationship_epoch_id,
                            kind,
                            content_json,
                            fact_key,
                            importance,
                            sensitivity,
                            valid_from,
                            valid_until,
                            source_kind,
                            source_ref,
                            source_summary,
                            attribution,
                            confidence,
                            occurred_at,
                            retention,
                            status,
                            prompt_eligible,
                            expires_at,
                            created_at,
                            speaker_identity
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        (
                            item.evidence_id,
                            item.pet_id,
                            item.memory_subject_id,
                            item.ownership_scope,
                            item.relationship_epoch_id,
                            item.kind,
                            _stable_json(item.content),
                            item.fact_key
                            or (
                                item.content.get("fact_key")
                                if isinstance(item.content.get("fact_key"), str)
                                else None
                            ),
                            item.importance,
                            item.sensitivity,
                            item.valid_from or item.occurred_at,
                            item.valid_until or item.expires_at,
                            item.source_kind,
                            item.source_ref,
                            item.source_summary,
                            item.attribution,
                            item.confidence,
                            item.occurred_at,
                            item.retention,
                            item.status,
                            int(item.prompt_eligible),
                            item.expires_at,
                            created_at,
                            item.speaker_identity,
                        ),
                    )
                self._supersede_replaced_facts(
                    connection,
                    evidence=turn_evidence,
                    created_at=created_at,
                )
                if connection_need_update is not None:
                    self._upsert_connection_need_in_connection(
                        connection,
                        owner_user_id=prepared.owner_user_id,
                        pet_id=prepared.pet_id,
                        memory_subject_id=prepared.memory_subject_id,
                        relationship_epoch_id=relationship_epoch_id,
                        update=connection_need_update,
                        created_at=created_at,
                    )
                for job in effective_jobs:
                    connection.execute(
                        """
                        INSERT INTO consolidation_jobs(
                            job_id,
                            pet_id,
                            relationship_epoch_id,
                            job_kind,
                            idempotency_key,
                            payload_json,
                            status,
                            due_at,
                            schema_version,
                            created_at,
                            updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                        """,
                        (
                            job.job_id,
                            job.pet_id,
                            job.relationship_epoch_id,
                            job.job_kind,
                            job.idempotency_key,
                            _stable_json(job.payload),
                            job.due_at,
                            job.schema_version,
                            created_at,
                            created_at,
                        ),
                    )
                    if (
                        job.job_kind == "memory_candidate_extraction"
                        and prepared.conversation_digest is not None
                    ):
                        connection.execute(
                            """
                            INSERT INTO companion_context_job_pins(
                                job_id, message_id, pet_id, created_at
                            )
                            SELECT ?, message_id, pet_id, ?
                            FROM companion_context_messages
                            WHERE pet_id = ? AND memory_subject_id = ?
                              AND conversation_digest = ?
                              AND julianday(occurred_at) <= julianday(?)
                            ORDER BY julianday(occurred_at) DESC,
                                     CASE role WHEN 'user' THEN 0 ELSE 1 END DESC
                            LIMIT 6
                            """,
                            (
                                job.job_id,
                                created_at,
                                prepared.pet_id,
                                prepared.memory_subject_id,
                                prepared.conversation_digest,
                                prepared.occurred_at,
                            ),
                        )
                self._insert_initiative_opportunities(
                    connection,
                    opportunities=opportunities,
                    owner_user_id=prepared.owner_user_id,
                    pet_id=prepared.pet_id,
                    memory_subject_id=prepared.memory_subject_id,
                    relationship_epoch_id=relationship_epoch_id,
                    created_at=created_at,
                )
                self._finish_growth_moment_in_connection(
                    connection,
                    prepared=prepared,
                    outcome=outcome,
                )
                connection.commit()
                return CompanionCommitResult(
                    turn_id=prepared.turn_id,
                    status="committed",
                    evidence_ids=tuple(item.evidence_id for item in turn_evidence),
                    job_ids=tuple(job.job_id for job in effective_jobs),
                )
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _supersede_replaced_facts(
        connection: sqlite3.Connection,
        *,
        evidence: tuple[CompanionEvidence, ...],
        created_at: str,
    ) -> None:
        winners: dict[tuple[str, str, str], CompanionEvidence] = {}
        for item in evidence:
            fact_key = item.fact_key or item.content.get("fact_key")
            if (
                item.status == "active"
                and isinstance(fact_key, str)
                and fact_key.strip()
            ):
                winners[(item.pet_id, item.memory_subject_id, fact_key)] = item

        for (pet_id, memory_subject_id, fact_key), winner in winners.items():
            replaced = connection.execute(
                """
                SELECT evidence_id
                FROM companion_evidence
                WHERE pet_id = ?
                  AND memory_subject_id = ?
                  AND COALESCE(
                        fact_key,
                        json_extract(content_json, '$.fact_key')
                      ) = ?
                  AND status IN ('candidate', 'active')
                  AND evidence_id <> ?
                ORDER BY occurred_at, evidence_id
                """,
                (pet_id, memory_subject_id, fact_key, winner.evidence_id),
            ).fetchall()
            for old in replaced:
                old_evidence_id = old["evidence_id"]
                connection.execute(
                    """
                    UPDATE companion_evidence
                    SET status = 'superseded', prompt_eligible = 0,
                        content_json = CASE
                            WHEN source_kind = 'conversation_candidate'
                            THEN json_remove(
                                content_json, '$.source_quote', '$.source_quotes'
                            )
                            ELSE content_json
                        END
                    WHERE evidence_id = ?
                    """,
                    (old_evidence_id,),
                )
                connection.execute(
                    """
                    INSERT INTO evidence_relations(
                        relation_id, pet_id, relation_kind,
                        source_evidence_id, target_evidence_id, created_at
                    ) VALUES (?, ?, 'superseded_by', ?, ?, ?)
                    """,
                    (
                        str(
                            uuid5(
                                NAMESPACE_URL,
                                "xiaoxin:user-fact-supersession:"
                                f"{old_evidence_id}:{winner.evidence_id}",
                            )
                        ),
                        pet_id,
                        old_evidence_id,
                        winner.evidence_id,
                        created_at,
                    ),
                )

    def reset_relationship(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        now: str,
        idempotency_key: str,
    ) -> CompanionControlResult:
        with self.pet_reflection_guard(pet_id):
            return self._reset_relationship_locked(
                owner_user_id=owner_user_id,
                pet_id=pet_id,
                memory_subject_id=memory_subject_id,
                now=now,
                idempotency_key=idempotency_key,
            )

    def apply_expression_control(
        self,
        *,
        action: str,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        payload: Mapping[str, object],
        now: str,
        idempotency_key: str,
    ) -> CompanionControlResult:
        request_digest = _control_request_digest(
            action=action,
            owner_user_id=owner_user_id,
            pet_id=pet_id,
            memory_subject_id=memory_subject_id,
            payload={**payload, "now": now},
        )
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._assert_owner_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                )
                existing = self._load_control_replay(
                    connection,
                    action=action,
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    request_digest=request_digest,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    connection.commit()
                    return existing
                epoch = connection.execute(
                    """
                    SELECT epoch_id FROM relationship_epochs
                    WHERE pet_id = ? AND ended_at IS NULL
                    """,
                    (pet_id,),
                ).fetchone()
                if epoch is None:
                    raise ValueError("pet has no active relationship epoch")
                epoch_id = str(epoch["epoch_id"])
                retained = 0
                deactivated = 0
                if action == "revoke_adjustment":
                    cursor = connection.execute(
                        """
                        UPDATE companion_adjustments
                        SET status = 'revoked'
                        WHERE adjustment_id = ? AND pet_id = ?
                          AND relationship_epoch_id = ?
                          AND status IN ('candidate', 'trial', 'active')
                          AND EXISTS (
                            SELECT 1 FROM adjustment_evidence AS link
                            JOIN companion_evidence AS evidence
                              ON evidence.evidence_id = link.evidence_id
                             AND evidence.pet_id = link.pet_id
                            WHERE link.adjustment_id = companion_adjustments.adjustment_id
                              AND evidence.memory_subject_id = ?
                          )
                          AND NOT EXISTS (
                            SELECT 1 FROM adjustment_evidence AS link
                            JOIN companion_evidence AS evidence
                              ON evidence.evidence_id = link.evidence_id
                             AND evidence.pet_id = link.pet_id
                            WHERE link.adjustment_id = companion_adjustments.adjustment_id
                              AND evidence.memory_subject_id <> ?
                          )
                        """,
                        (
                            payload["adjustment_id"],
                            pet_id,
                            epoch_id,
                            memory_subject_id,
                            memory_subject_id,
                        ),
                    )
                    deactivated = int(cursor.rowcount)
                elif action == "restore_default_expression":
                    cursor = connection.execute(
                        """
                        UPDATE companion_adjustments
                        SET status = 'revoked'
                        WHERE pet_id = ? AND relationship_epoch_id = ?
                          AND status IN ('candidate', 'trial', 'active')
                          AND EXISTS (
                            SELECT 1 FROM adjustment_evidence AS link
                            JOIN companion_evidence AS evidence
                              ON evidence.evidence_id = link.evidence_id
                             AND evidence.pet_id = link.pet_id
                            WHERE link.adjustment_id = companion_adjustments.adjustment_id
                              AND evidence.memory_subject_id = ?
                          )
                          AND NOT EXISTS (
                            SELECT 1 FROM adjustment_evidence AS link
                            JOIN companion_evidence AS evidence
                              ON evidence.evidence_id = link.evidence_id
                             AND evidence.pet_id = link.pet_id
                            WHERE link.adjustment_id = companion_adjustments.adjustment_id
                              AND evidence.memory_subject_id <> ?
                          )
                        """,
                        (pet_id, epoch_id, memory_subject_id, memory_subject_id),
                    )
                    deactivated = int(cursor.rowcount)
                elif action == "set_initiative_quiet_hours":
                    dimension = "initiative_quiet_hours"
                    scope = "initiative"
                    contract_id = str(
                        uuid5(
                            NAMESPACE_URL,
                            "xiaoxin:interaction-contract:"
                            f"{pet_id}:{memory_subject_id}:{dimension}:{scope}",
                        )
                    )
                    connection.execute(
                        """
                        INSERT INTO companion_interaction_contracts(
                            contract_id, pet_id, memory_subject_id, dimension,
                            value_json, scope, safe_label, safe_scope, status,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                        ON CONFLICT(pet_id, memory_subject_id, dimension, scope)
                        DO UPDATE SET value_json = excluded.value_json,
                            safe_label = excluded.safe_label,
                            safe_scope = excluded.safe_scope,
                            status = 'active', updated_at = excluded.updated_at
                        """,
                        (
                            contract_id,
                            pet_id,
                            memory_subject_id,
                            dimension,
                            _stable_json(
                                {
                                    "enabled": payload["enabled"],
                                    "start": payload["start"],
                                    "end": payload["end"],
                                }
                            ),
                            scope,
                            payload["safe_label"],
                            payload["safe_scope"],
                            now,
                            now,
                        ),
                    )
                    retained = 1
                elif action == "set_interaction_contract":
                    dimension = str(payload["dimension"])
                    scope = str(payload["scope"])
                    contract_id = str(
                        uuid5(
                            NAMESPACE_URL,
                            "xiaoxin:interaction-contract:"
                            f"{pet_id}:{memory_subject_id}:{dimension}:{scope}",
                        )
                    )
                    connection.execute(
                        """
                        INSERT INTO companion_interaction_contracts(
                            contract_id, pet_id, memory_subject_id, dimension,
                            value_json, scope, safe_label, safe_scope, status,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                        ON CONFLICT(pet_id, memory_subject_id, dimension, scope)
                        DO UPDATE SET value_json = excluded.value_json,
                            safe_label = excluded.safe_label,
                            safe_scope = excluded.safe_scope,
                            status = 'active', updated_at = excluded.updated_at
                        """,
                        (
                            contract_id,
                            pet_id,
                            memory_subject_id,
                            dimension,
                            _stable_json({"value": payload["value"]}),
                            scope,
                            payload["safe_label"],
                            payload["safe_scope"],
                            now,
                            now,
                        ),
                    )
                    retained = 1
                    cursor = connection.execute(
                        """
                        UPDATE companion_adjustments
                        SET status = 'revoked'
                        WHERE pet_id = ? AND relationship_epoch_id = ?
                          AND dimension = ?
                          AND (scope = 'all' OR ? = 'all' OR scope = ?)
                          AND status IN ('candidate', 'trial', 'active')
                          AND EXISTS (
                            SELECT 1 FROM adjustment_evidence AS link
                            JOIN companion_evidence AS evidence
                              ON evidence.evidence_id = link.evidence_id
                             AND evidence.pet_id = link.pet_id
                            WHERE link.adjustment_id = companion_adjustments.adjustment_id
                              AND evidence.memory_subject_id = ?
                          )
                          AND NOT EXISTS (
                            SELECT 1 FROM adjustment_evidence AS link
                            JOIN companion_evidence AS evidence
                              ON evidence.evidence_id = link.evidence_id
                             AND evidence.pet_id = link.pet_id
                            WHERE link.adjustment_id = companion_adjustments.adjustment_id
                              AND evidence.memory_subject_id <> ?
                          )
                        """,
                        (
                            pet_id,
                            epoch_id,
                            dimension,
                            scope,
                            scope,
                            memory_subject_id,
                            memory_subject_id,
                        ),
                    )
                    deactivated = int(cursor.rowcount)
                    if dimension == "initiative_level" and scope in {
                        "all",
                        "initiative",
                    }:
                        self._reschedule_connection_need_for_initiative_level_in_connection(
                            connection,
                            owner_user_id=owner_user_id,
                            pet_id=pet_id,
                            memory_subject_id=memory_subject_id,
                            target_level=str(payload["value"]),
                            now=now,
                        )
                elif action == "revoke_interaction_contract":
                    contract = connection.execute(
                        """
                        SELECT dimension, scope
                        FROM companion_interaction_contracts
                        WHERE contract_id = ? AND pet_id = ?
                          AND memory_subject_id = ? AND status = 'active'
                        """,
                        (payload["contract_id"], pet_id, memory_subject_id),
                    ).fetchone()
                    cursor = connection.execute(
                        """
                        UPDATE companion_interaction_contracts
                        SET status = 'revoked', updated_at = ?
                        WHERE contract_id = ? AND pet_id = ?
                          AND memory_subject_id = ? AND status = 'active'
                        """,
                        (
                            now,
                            payload["contract_id"],
                            pet_id,
                            memory_subject_id,
                        ),
                    )
                    deactivated = int(cursor.rowcount)
                    if (
                        deactivated
                        and contract is not None
                        and contract["dimension"] == "initiative_level"
                        and contract["scope"] in {"all", "initiative"}
                    ):
                        self._reschedule_connection_need_for_initiative_level_in_connection(
                            connection,
                            owner_user_id=owner_user_id,
                            pet_id=pet_id,
                            memory_subject_id=memory_subject_id,
                            target_level=_active_initiative_contract_level(
                                connection,
                                pet_id=pet_id,
                                memory_subject_id=memory_subject_id,
                            ),
                            now=now,
                        )
                else:
                    raise ValueError("unsupported expression control")
                result = CompanionControlResult(
                    action=action,
                    status="applied" if retained or deactivated else "already_applied",
                    retained=retained,
                    deactivated=deactivated,
                )
                self._insert_control_record(
                    connection,
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    action=action,
                    payload=dict(payload),
                    request_digest=request_digest,
                    result=result,
                    now=now,
                    idempotency_key=idempotency_key,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def _reset_relationship_locked(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        now: str,
        idempotency_key: str,
    ) -> CompanionControlResult:
        request_digest = _control_request_digest(
            action="reset_relationship",
            owner_user_id=owner_user_id,
            pet_id=pet_id,
            memory_subject_id=memory_subject_id,
            payload={"now": now},
        )
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._assert_owner_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                )
                existing = self._load_control_replay(
                    connection,
                    action="reset_relationship",
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    request_digest=request_digest,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    connection.commit()
                    return existing
                old_epoch = connection.execute(
                    """
                    SELECT epoch_id
                    FROM relationship_epochs
                    WHERE pet_id = ? AND ended_at IS NULL
                    """,
                    (pet_id,),
                ).fetchone()
                if old_epoch is None:
                    raise ValueError("pet has no active relationship epoch")
                old_epoch_id = old_epoch["epoch_id"]
                retained = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM companion_evidence
                    WHERE pet_id = ?
                      AND memory_subject_id = ?
                      AND ownership_scope = 'user'
                      AND status IN ('candidate', 'active')
                      AND prompt_eligible = 1
                    """,
                    (pet_id, memory_subject_id),
                ).fetchone()[0]
                deactivated = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM companion_evidence
                    WHERE pet_id = ?
                      AND memory_subject_id = ?
                      AND ownership_scope = 'relationship'
                      AND relationship_epoch_id = ?
                      AND status IN ('candidate', 'active')
                    """,
                    (pet_id, memory_subject_id, old_epoch_id),
                ).fetchone()[0]
                connection.execute(
                    """
                    UPDATE relationship_epochs
                    SET ended_at = ?, end_reason = 'user_reset'
                    WHERE epoch_id = ? AND ended_at IS NULL
                    """,
                    (now, old_epoch_id),
                )
                connection.execute(
                    """
                    UPDATE companion_evidence
                    SET status = 'superseded', prompt_eligible = 0
                    WHERE pet_id = ?
                      AND ownership_scope = 'relationship'
                      AND relationship_epoch_id = ?
                      AND status IN ('candidate', 'active')
                    """,
                    (pet_id, old_epoch_id),
                )
                connection.execute(
                    """
                    UPDATE session_capsules
                    SET status = 'inactive'
                    WHERE pet_id = ? AND relationship_epoch_id = ?
                    """,
                    (pet_id, old_epoch_id),
                )
                connection.execute(
                    """
                    UPDATE companion_adjustments
                    SET status = 'revoked'
                    WHERE pet_id = ? AND relationship_epoch_id = ?
                    """,
                    (pet_id, old_epoch_id),
                )
                connection.execute(
                    """
                    UPDATE companion_chapters
                    SET status = 'invalidated'
                    WHERE pet_id = ? AND relationship_epoch_id = ?
                    """,
                    (pet_id, old_epoch_id),
                )
                connection.execute(
                    """
                    DELETE FROM companion_turn_sources AS source
                    WHERE source.pet_id = ?
                      AND EXISTS (
                          SELECT 1
                          FROM consolidation_jobs AS job
                          WHERE job.pet_id = source.pet_id
                            AND job.relationship_epoch_id = ?
                            AND job.job_kind = 'memory_candidate_extraction'
                            AND job.status NOT IN ('succeeded', 'failed', 'cancelled')
                            AND json_extract(job.payload_json, '$.turn_id') = source.turn_id
                      )
                    """,
                    (pet_id, old_epoch_id),
                )
                connection.execute(
                    "DELETE FROM companion_context_messages WHERE pet_id = ?",
                    (pet_id,),
                )
                connection.execute(
                    "DELETE FROM companion_va_snapshots WHERE pet_id = ?",
                    (pet_id,),
                )
                connection.execute(
                    """
                    UPDATE consolidation_jobs
                    SET status = 'cancelled',
                        failure_reason = 'relationship_reset',
                        updated_at = ?
                    WHERE pet_id = ?
                      AND relationship_epoch_id = ?
                      AND status NOT IN ('succeeded', 'failed', 'cancelled')
                    """,
                    (now, pet_id, old_epoch_id),
                )
                connection.execute(
                    """
                    UPDATE initiative_decisions
                    SET delivery_status = 'invalidated'
                    WHERE pet_id = ?
                      AND relationship_epoch_id = ?
                      AND delivery_status IN ('pending', 'composing')
                    """,
                    (pet_id, old_epoch_id),
                )
                connection.execute(
                    """
                    UPDATE initiative_opportunities
                    SET status = 'invalidated', outcome_code = 'relationship_reset',
                        lease_until = NULL, updated_at = ?
                    WHERE pet_id = ? AND relationship_epoch_id = ?
                       AND status IN ('scheduled', 'deferred', 'claimed')
                    """,
                    (now, pet_id, old_epoch_id),
                )
                new_epoch_id = str(uuid4())
                connection.execute(
                    """
                    INSERT INTO relationship_epochs(
                        epoch_id, pet_id, started_at, start_reason
                    ) VALUES (?, ?, ?, 'relationship_reset')
                    """,
                    (new_epoch_id, pet_id, now),
                )
                result = CompanionControlResult(
                    action="reset_relationship",
                    status="applied",
                    retained=int(retained),
                    deactivated=int(deactivated),
                )
                self._insert_control_record(
                    connection,
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    action="reset_relationship",
                    payload={
                        "old_epoch_id": old_epoch_id,
                        "new_epoch_id": new_epoch_id,
                    },
                    request_digest=request_digest,
                    result=result,
                    now=now,
                    idempotency_key=idempotency_key,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def resolve_memory_candidate(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        evidence_id: str,
        resolution: str,
        now: str,
        idempotency_key: str,
    ) -> CompanionControlResult:
        if resolution not in {"confirmed", "rejected"}:
            raise ValueError("memory candidate resolution is invalid")
        action = (
            "confirm_candidate" if resolution == "confirmed" else "reject_candidate"
        )
        request_digest = _control_request_digest(
            action=action,
            owner_user_id=owner_user_id,
            pet_id=pet_id,
            memory_subject_id=memory_subject_id,
            payload={"evidence_id": evidence_id, "now": now},
        )
        with self.pet_reflection_guard(pet_id):
            with self.connection() as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    self._assert_owner_in_connection(
                        connection,
                        owner_user_id=owner_user_id,
                        pet_id=pet_id,
                    )
                    existing = self._load_control_replay(
                        connection,
                        action=action,
                        pet_id=pet_id,
                        memory_subject_id=memory_subject_id,
                        request_digest=request_digest,
                        idempotency_key=idempotency_key,
                    )
                    if existing is not None:
                        connection.commit()
                        return existing
                    candidate = connection.execute(
                        """
                        SELECT *
                        FROM companion_evidence
                        WHERE evidence_id = ? AND pet_id = ?
                          AND memory_subject_id = ?
                          AND source_kind = 'conversation_candidate'
                        """,
                        (evidence_id, pet_id, memory_subject_id),
                    ).fetchone()
                    if candidate is None:
                        raise ValueError(
                            "memory candidate does not exist in subject scope"
                        )
                    if candidate["status"] != "candidate":
                        raise ValueError("memory candidate is no longer pending")
                    candidate_content = json.loads(candidate["content_json"])
                    if not isinstance(candidate_content, dict):
                        raise sqlite3.DatabaseError(
                            "memory candidate content must be an object"
                        )
                    candidate_content.pop("source_quote", None)
                    candidate_content.pop("source_quotes", None)
                    scrubbed_content_json = _stable_json(candidate_content)
                    deactivated = 0
                    if resolution == "confirmed":
                        candidate_fact_key_aliases = memory_fact_key_storage_aliases(
                            str(candidate["fact_key"])
                        )
                        candidate_fact_key_placeholders = ",".join(
                            "?" for _ in candidate_fact_key_aliases
                        )
                        replaced = connection.execute(
                            f"""
                            SELECT evidence_id
                            FROM companion_evidence
                            WHERE pet_id = ? AND memory_subject_id = ?
                              AND fact_key IN ({candidate_fact_key_placeholders})
                              AND status IN ('candidate', 'active')
                              AND evidence_id <> ?
                            ORDER BY occurred_at, evidence_id
                            """,
                            (
                                pet_id,
                                memory_subject_id,
                                *candidate_fact_key_aliases,
                                evidence_id,
                            ),
                        ).fetchall()
                        for old in replaced:
                            connection.execute(
                                """
                                UPDATE companion_evidence
                                SET status = 'superseded', prompt_eligible = 0,
                                    content_json = CASE
                                        WHEN source_kind = 'conversation_candidate'
                                        THEN json_remove(
                                            content_json,
                                            '$.source_quote', '$.source_quotes'
                                        )
                                        ELSE content_json
                                    END
                                WHERE evidence_id = ?
                                """,
                                (old["evidence_id"],),
                            )
                            connection.execute(
                                """
                                INSERT INTO evidence_relations(
                                    relation_id, pet_id, relation_kind,
                                    source_evidence_id, target_evidence_id, created_at
                                ) VALUES (?, ?, 'superseded_by', ?, ?, ?)
                                """,
                                (
                                    str(
                                        uuid5(
                                            NAMESPACE_URL,
                                            "xiaoxin:candidate-confirmation:"
                                            f"{old['evidence_id']}:{evidence_id}",
                                        )
                                    ),
                                    pet_id,
                                    old["evidence_id"],
                                    evidence_id,
                                    now,
                                ),
                            )
                        deactivated = len(replaced)
                        connection.execute(
                            """
                            UPDATE companion_evidence
                            SET status = 'active', prompt_eligible = 1,
                                attribution = 'user_confirmed_candidate',
                                retention = 'long_term', expires_at = NULL,
                                content_json = ?,
                                source_summary = '用户确认了这条对话记忆。'
                            WHERE evidence_id = ?
                            """,
                            (scrubbed_content_json, evidence_id),
                        )
                        result = CompanionControlResult(
                            action=action,
                            status="applied",
                            retained=1,
                            deactivated=deactivated,
                        )
                        observation_kind = "memory_candidate_confirmed"
                        observation_summary = "用户确认了一条对话记忆候选。"
                    else:
                        self._delete_context_for_evidence_ids(
                            connection,
                            pet_id=pet_id,
                            evidence_ids=(evidence_id,),
                        )
                        connection.execute(
                            """
                            UPDATE companion_evidence
                            SET status = 'forgotten', prompt_eligible = 0,
                                attribution = 'user_rejected_candidate',
                                expires_at = NULL,
                                content_json = ?,
                                source_summary = '用户拒绝了这条对话记忆候选。'
                            WHERE evidence_id = ?
                            """,
                            (scrubbed_content_json, evidence_id),
                        )
                        result = CompanionControlResult(
                            action=action,
                            status="applied",
                            forgotten=1,
                        )
                        observation_kind = "memory_candidate_rejected"
                        observation_summary = "用户拒绝了一条对话记忆候选。"
                    observation_idempotency_key = (
                        f"{observation_kind}:{idempotency_key}"
                    )
                    observation_id = str(
                        uuid5(
                            NAMESPACE_URL,
                            f"companion-observation:{pet_id}:"
                            f"{observation_idempotency_key}",
                        )
                    )
                    observation_payload = {"evidence_id": evidence_id}
                    observation_digest = hashlib.sha256(
                        _stable_json(
                            {
                                "owner_user_id": owner_user_id,
                                "pet_id": pet_id,
                                "memory_subject_id": memory_subject_id,
                                "kind": observation_kind,
                                "source_kind": "memory_control",
                                "source_ref": evidence_id,
                                "occurred_at": now,
                                "payload": observation_payload,
                                "safe_summary": observation_summary,
                            }
                        ).encode("utf-8")
                    ).hexdigest()
                    connection.execute(
                        """
                        INSERT INTO companion_observations(
                            observation_id, idempotency_key, owner_user_id,
                            pet_id, memory_subject_id, kind, source_kind,
                            source_ref, payload_json, observation_digest,
                            safe_summary, occurred_at, status, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, 'memory_control', ?, ?, ?, ?, ?,
                            'recorded', ?
                        )
                        """,
                        (
                            observation_id,
                            observation_idempotency_key,
                            owner_user_id,
                            pet_id,
                            memory_subject_id,
                            observation_kind,
                            evidence_id,
                            _stable_json(observation_payload),
                            observation_digest,
                            observation_summary,
                            now,
                            now,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO observation_evidence(
                            observation_id, evidence_id, pet_id
                        ) VALUES (?, ?, ?)
                        """,
                        (observation_id, evidence_id, pet_id),
                    )
                    self._insert_control_record(
                        connection,
                        pet_id=pet_id,
                        memory_subject_id=memory_subject_id,
                        action=action,
                        payload={"evidence_id": evidence_id},
                        request_digest=request_digest,
                        result=result,
                        now=now,
                        idempotency_key=idempotency_key,
                    )
                    connection.commit()
                    return result
                except Exception:
                    connection.rollback()
                    raise

    def forget_evidence(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        evidence_id: str,
        now: str,
        idempotency_key: str,
    ) -> CompanionControlResult:
        with self.pet_reflection_guard(pet_id):
            return self._forget_evidence_locked(
                owner_user_id=owner_user_id,
                pet_id=pet_id,
                memory_subject_id=memory_subject_id,
                evidence_id=evidence_id,
                now=now,
                idempotency_key=idempotency_key,
            )

    @staticmethod
    def _invalidate_initiative_opportunities_for_evidence(
        connection: sqlite3.Connection,
        *,
        evidence_ids: tuple[str, ...],
        reason_code: str,
        now: str,
        scrub: bool,
    ) -> None:
        unique_ids = tuple(dict.fromkeys(evidence_ids))
        if not unique_ids:
            return
        placeholders = ",".join("?" for _ in unique_ids)
        matching_decisions = connection.execute(
            f"""
            SELECT DISTINCT decision_id
            FROM initiative_decisions AS decision
            WHERE EXISTS (
                SELECT 1 FROM json_each(decision.evidence_ids_json)
                WHERE json_each.value IN ({placeholders})
            )
            """,
            unique_ids,
        ).fetchall()
        if matching_decisions:
            decision_ids = tuple(row["decision_id"] for row in matching_decisions)
            decision_placeholders = ",".join("?" for _ in decision_ids)
            connection.execute(
                f"""
                UPDATE initiative_decisions
                SET delivery_status = CASE
                      WHEN delivery_status IN ('pending', 'composing')
                      THEN 'invalidated' ELSE delivery_status END,
                    evidence_ids_json = CASE WHEN ? THEN '[]'
                      ELSE evidence_ids_json END,
                    content_brief = CASE WHEN ? THEN 'forgotten'
                      ELSE content_brief END
                WHERE decision_id IN ({decision_placeholders})
                """,
                (int(scrub), int(scrub), *decision_ids),
            )
        connection.execute(
            f"""
            UPDATE initiative_opportunities AS opportunity
            SET status = CASE
                  WHEN status IN ('scheduled', 'deferred', 'claimed')
                  THEN 'invalidated' ELSE status END,
                evidence_ids_json = CASE WHEN ? THEN '[]'
                  ELSE evidence_ids_json END,
                safe_brief = CASE WHEN ? THEN 'forgotten'
                  ELSE safe_brief END,
                outcome_code = CASE
                  WHEN status IN ('scheduled', 'deferred', 'claimed') THEN ?
                  ELSE outcome_code END,
                lease_until = CASE
                  WHEN status IN ('scheduled', 'deferred', 'claimed') THEN NULL
                  ELSE lease_until END,
                updated_at = ?
            WHERE EXISTS (
                SELECT 1 FROM json_each(opportunity.evidence_ids_json)
                WHERE json_each.value IN ({placeholders})
            )
            """,
            (int(scrub), int(scrub), reason_code, now, *unique_ids),
        )

    @staticmethod
    def _delete_context_for_evidence_ids(
        connection: sqlite3.Connection,
        *,
        pet_id: str,
        evidence_ids: tuple[str, ...],
    ) -> None:
        unique_ids = tuple(dict.fromkeys(evidence_ids))
        if not unique_ids:
            return
        placeholders = ",".join("?" for _ in unique_ids)
        rows = connection.execute(
            f"""
            SELECT source_ref, content_json
            FROM companion_evidence
            WHERE pet_id = ? AND evidence_id IN ({placeholders})
            """,
            (pet_id, *unique_ids),
        ).fetchall()
        turn_ids: set[str] = set()
        for row in rows:
            source_ref = row["source_ref"]
            if isinstance(source_ref, str) and source_ref.strip():
                turn_ids.add(source_ref)
            try:
                content = json.loads(row["content_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(content, dict):
                continue
            for turn_id in content.get("source_turn_ids", ()):
                if isinstance(turn_id, str) and turn_id.strip():
                    turn_ids.add(turn_id)
            for quote in content.get("source_quotes", ()):
                if not isinstance(quote, dict):
                    continue
                turn_id = quote.get("turn_id")
                if isinstance(turn_id, str) and turn_id.strip():
                    turn_ids.add(turn_id)
        if not turn_ids:
            return
        turn_placeholders = ",".join("?" for _ in turn_ids)
        connection.execute(
            f"""
            DELETE FROM companion_context_messages
            WHERE pet_id = ? AND turn_id IN ({turn_placeholders})
            """,
            (pet_id, *sorted(turn_ids)),
        )

    @staticmethod
    def _reconcile_growth_moments_after_evidence_change(
        connection: sqlite3.Connection,
        *,
        evidence_ids: tuple[str, ...],
        reason_code: str,
    ) -> None:
        unique_ids = tuple(dict.fromkeys(evidence_ids))
        if not unique_ids:
            return
        placeholders = ",".join("?" for _ in unique_ids)
        connection.execute(
            f"""
            UPDATE companion_narrative_boundaries
            SET status = 'invalidated'
            WHERE evidence_id IN ({placeholders})
            """,
            unique_ids,
        )
        moment_rows = connection.execute(
            f"""
            SELECT DISTINCT moment_id
            FROM companion_growth_moment_boundaries
            WHERE boundary_id IN (
                SELECT boundary_id FROM companion_narrative_boundaries
                WHERE evidence_id IN ({placeholders})
            )
            UNION
            SELECT DISTINCT moment_id
            FROM companion_growth_moment_evidence
            WHERE evidence_id IN ({placeholders})
            """,
            (*unique_ids, *unique_ids),
        ).fetchall()
        priority = {
            "academic_reorientation": 1,
            "anniversary": 2,
            "academic_growth": 3,
            "graduation": 4,
        }
        for moment_row in moment_rows:
            moment_id = moment_row["moment_id"]
            boundaries = connection.execute(
                """
                SELECT boundary_kind
                FROM companion_growth_moment_boundaries AS link
                JOIN companion_narrative_boundaries AS boundary
                  ON boundary.boundary_id = link.boundary_id
                WHERE link.moment_id = ? AND boundary.status = 'active'
                """,
                (moment_id,),
            ).fetchall()
            evidence_rows = connection.execute(
                """
                SELECT evidence.evidence_id, evidence.ownership_scope,
                       evidence.occurred_at
                FROM companion_growth_moment_evidence AS link
                JOIN companion_evidence AS evidence
                  ON evidence.evidence_id = link.evidence_id
                 AND evidence.pet_id = link.pet_id
                WHERE link.moment_id = ? AND evidence.status = 'active'
                ORDER BY evidence.occurred_at, evidence.evidence_id
                """,
                (moment_id,),
            ).fetchall()
            selected = CompanionStore._select_chapter_evidence_rows(evidence_rows)
            lifecycle_status = "active"
            primary_kind = None
            mode = "evidence_backed" if selected else "boundary_only"
            if boundaries:
                primary_kind = max(
                    (row["boundary_kind"] for row in boundaries),
                    key=priority.__getitem__,
                )
                if primary_kind == "anniversary" and not selected:
                    lifecycle_status = "invalidated"
            else:
                lifecycle_status = "invalidated"
            if primary_kind is None:
                primary_kind = "academic_growth"
            connection.execute(
                """
                UPDATE companion_growth_moment_metadata
                SET primary_kind = ?, mode = ?, lifecycle_status = ?,
                    reason_code = ?
                WHERE moment_id = ?
                """,
                (primary_kind, mode, lifecycle_status, reason_code, moment_id),
            )
            connection.execute(
                """
                UPDATE companion_growth_moments
                SET expression_status = CASE
                      WHEN expression_status = 'reserved' THEN 'pending'
                      ELSE expression_status END,
                    reserved_by_turn_id = CASE
                      WHEN expression_status = 'reserved' THEN NULL
                      ELSE reserved_by_turn_id END,
                    lease_until = CASE
                      WHEN expression_status = 'reserved' THEN NULL
                      ELSE lease_until END,
                    continuity_evidence_count = ?
                WHERE moment_id = ?
                """,
                (len(selected), moment_id),
            )

    def _forget_evidence_locked(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        evidence_id: str,
        now: str,
        idempotency_key: str,
    ) -> CompanionControlResult:
        request_digest = _control_request_digest(
            action="forget_evidence",
            owner_user_id=owner_user_id,
            pet_id=pet_id,
            memory_subject_id=memory_subject_id,
            payload={"evidence_id": evidence_id, "now": now},
        )
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._assert_owner_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                )
                existing = self._load_control_replay(
                    connection,
                    action="forget_evidence",
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    request_digest=request_digest,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    connection.commit()
                    return existing
                evidence = connection.execute(
                    """
                    SELECT status
                    FROM companion_evidence
                    WHERE evidence_id = ?
                      AND pet_id = ?
                      AND memory_subject_id = ?
                    """,
                    (evidence_id, pet_id, memory_subject_id),
                ).fetchone()
                if evidence is None:
                    raise ValueError("Evidence does not exist in this subject scope")
                forgotten = int(evidence["status"] != "forgotten")
                connection.execute(
                    """
                    UPDATE companion_evidence
                    SET status = 'forgotten', prompt_eligible = 0,
                        content_json = CASE
                            WHEN source_kind = 'conversation_candidate'
                            THEN json_remove(
                                content_json, '$.source_quote', '$.source_quotes'
                            )
                            ELSE content_json
                        END
                    WHERE evidence_id = ?
                    """,
                    (evidence_id,),
                )
                self._delete_context_for_evidence_ids(
                    connection,
                    pet_id=pet_id,
                    evidence_ids=(evidence_id,),
                )
                self._invalidate_initiative_opportunities_for_evidence(
                    connection,
                    evidence_ids=(evidence_id,),
                    reason_code="evidence_forgotten",
                    now=now,
                    scrub=True,
                )
                connection.execute(
                    """
                    UPDATE session_capsules
                    SET status = 'invalidated'
                    WHERE capsule_id IN (
                        SELECT capsule_id FROM capsule_evidence WHERE evidence_id = ?
                    )
                    """,
                    (evidence_id,),
                )
                self._revoke_adjustments_for_evidence_ids(
                    connection,
                    evidence_ids=(evidence_id,),
                )
                connection.execute(
                    """
                    UPDATE companion_chapters
                    SET status = 'invalidated'
                    WHERE chapter_id IN (
                        SELECT chapter_id FROM chapter_evidence WHERE evidence_id = ?
                    )
                    """,
                    (evidence_id,),
                )
                self._reconcile_growth_moments_after_evidence_change(
                    connection,
                    evidence_ids=(evidence_id,),
                    reason_code="evidence_forgotten",
                )
                self._enqueue_chapter_rebuilds_after_evidence_change(
                    connection,
                    evidence_ids=(evidence_id,),
                    now=now,
                )
                epoch = connection.execute(
                    """
                    SELECT epoch_id
                    FROM relationship_epochs
                    WHERE pet_id = ? AND ended_at IS NULL
                    """,
                    (pet_id,),
                ).fetchone()
                job_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"xiaoxin:recompute-after-forget:{idempotency_key}",
                    )
                )
                connection.execute(
                    """
                    INSERT INTO consolidation_jobs(
                        job_id,
                        pet_id,
                        relationship_epoch_id,
                        job_kind,
                        idempotency_key,
                        payload_json,
                        status,
                        due_at,
                        schema_version,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, 'recompute_after_forget', ?, ?, 'pending', ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        pet_id,
                        epoch["epoch_id"] if epoch is not None else None,
                        f"recompute-after-forget:{idempotency_key}",
                        _stable_json(
                            {
                                "memory_subject_id": memory_subject_id,
                                "evidence_id": evidence_id,
                            }
                        ),
                        now,
                        "companion-recompute-v1",
                        now,
                        now,
                    ),
                )
                result = CompanionControlResult(
                    action="forget_evidence",
                    status="applied",
                    forgotten=forgotten,
                    requeued=1,
                )
                self._insert_control_record(
                    connection,
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    action="forget_evidence",
                    payload={"evidence_id": evidence_id},
                    request_digest=request_digest,
                    result=result,
                    now=now,
                    idempotency_key=idempotency_key,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def forget_theme(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        theme: str,
        now: str,
        idempotency_key: str,
    ) -> CompanionControlResult:
        with self.pet_reflection_guard(pet_id):
            return self._forget_theme_locked(
                owner_user_id=owner_user_id,
                pet_id=pet_id,
                memory_subject_id=memory_subject_id,
                theme=theme,
                now=now,
                idempotency_key=idempotency_key,
            )

    def _forget_theme_locked(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        theme: str,
        now: str,
        idempotency_key: str,
    ) -> CompanionControlResult:
        request_digest = _control_request_digest(
            action="forget_theme",
            owner_user_id=owner_user_id,
            pet_id=pet_id,
            memory_subject_id=memory_subject_id,
            payload={"theme": theme, "now": now},
        )
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._assert_owner_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                )
                existing = self._load_control_replay(
                    connection,
                    action="forget_theme",
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    request_digest=request_digest,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    connection.commit()
                    return existing
                rows = connection.execute(
                    """
                    SELECT evidence_id
                    FROM companion_evidence
                    WHERE pet_id = ?
                      AND memory_subject_id = ?
                      AND status IN ('candidate', 'active')
                      AND json_extract(content_json, '$.theme') = ?
                    """,
                    (pet_id, memory_subject_id, theme),
                ).fetchall()
                evidence_ids = tuple(row["evidence_id"] for row in rows)
                if evidence_ids:
                    placeholders = ",".join("?" for _ in evidence_ids)
                    self._delete_context_for_evidence_ids(
                        connection,
                        pet_id=pet_id,
                        evidence_ids=evidence_ids,
                    )
                    connection.execute(
                        f"""
                        UPDATE companion_evidence
                        SET status = 'forgotten', prompt_eligible = 0
                        WHERE evidence_id IN ({placeholders})
                        """,
                        evidence_ids,
                    )
                    connection.execute(
                        f"""
                        UPDATE session_capsules
                        SET status = 'invalidated'
                        WHERE capsule_id IN (
                            SELECT capsule_id
                            FROM capsule_evidence
                            WHERE evidence_id IN ({placeholders})
                        )
                        """,
                        evidence_ids,
                    )
                    self._revoke_adjustments_for_evidence_ids(
                        connection,
                        evidence_ids=evidence_ids,
                    )
                    connection.execute(
                        f"""
                        UPDATE companion_chapters
                        SET status = 'invalidated'
                        WHERE chapter_id IN (
                            SELECT chapter_id
                            FROM chapter_evidence
                            WHERE evidence_id IN ({placeholders})
                        )
                        """,
                        evidence_ids,
                    )
                    self._invalidate_initiative_opportunities_for_evidence(
                        connection,
                        evidence_ids=evidence_ids,
                        reason_code="theme_forgotten",
                        now=now,
                        scrub=True,
                    )
                epoch = connection.execute(
                    """
                    SELECT epoch_id FROM relationship_epochs
                    WHERE pet_id = ? AND ended_at IS NULL
                    """,
                    (pet_id,),
                ).fetchone()
                job_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"xiaoxin:recompute-after-theme-forget:{idempotency_key}",
                    )
                )
                connection.execute(
                    """
                    INSERT INTO consolidation_jobs(
                        job_id, pet_id, relationship_epoch_id, job_kind,
                        idempotency_key, payload_json, status, due_at,
                        schema_version, created_at, updated_at
                    ) VALUES (?, ?, ?, 'recompute_after_forget', ?, ?, 'pending', ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        pet_id,
                        epoch["epoch_id"] if epoch is not None else None,
                        f"recompute-after-theme-forget:{idempotency_key}",
                        _stable_json(
                            {
                                "memory_subject_id": memory_subject_id,
                                "theme": theme,
                                "evidence_ids": evidence_ids,
                            }
                        ),
                        now,
                        "companion-recompute-v1",
                        now,
                        now,
                    ),
                )
                result = CompanionControlResult(
                    action="forget_theme",
                    status="applied",
                    forgotten=len(evidence_ids),
                    requeued=1,
                )
                self._insert_control_record(
                    connection,
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    action="forget_theme",
                    payload={"theme": theme},
                    request_digest=request_digest,
                    result=result,
                    now=now,
                    idempotency_key=idempotency_key,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def repair_misclassified_semantic_evidence(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        obsolete_evidence_id: str,
        replacement_evidence_id: str,
        obsolete_fact_key: str,
        replacement_fact_key: str,
        now: str,
    ) -> Mapping[str, object]:
        """Supersede one verified polluted fact with an existing active fact."""

        if obsolete_evidence_id == replacement_evidence_id:
            raise ValueError("Evidence repair requires two different evidence ids")
        if not memory_fact_replacement_is_authorized(
            obsolete_fact_key,
            replacement_fact_key,
        ):
            raise ValueError("Evidence repair fact-key transition is not authorized")
        parsed_now = datetime.fromisoformat(now)
        if parsed_now.utcoffset() is None:
            raise ValueError("Evidence repair timestamp must include a timezone")
        with self.pet_reflection_guard(pet_id):
            with self.connection() as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    self._assert_owner_in_connection(
                        connection,
                        owner_user_id=owner_user_id,
                        pet_id=pet_id,
                    )
                    rows = connection.execute(
                        """
                        SELECT evidence_id, ownership_scope, kind, fact_key,
                               status, prompt_eligible
                        FROM companion_evidence
                        WHERE pet_id = ? AND memory_subject_id = ?
                          AND evidence_id IN (?, ?)
                        """,
                        (
                            pet_id,
                            memory_subject_id,
                            obsolete_evidence_id,
                            replacement_evidence_id,
                        ),
                    ).fetchall()
                    by_id = {str(row["evidence_id"]): row for row in rows}
                    obsolete = by_id.get(obsolete_evidence_id)
                    replacement = by_id.get(replacement_evidence_id)
                    if obsolete is None or replacement is None:
                        raise ValueError("Evidence repair ids do not match the subject")
                    if (
                        obsolete["ownership_scope"] != "user"
                        or replacement["ownership_scope"] != "user"
                    ):
                        raise ValueError("Evidence repair only supports user-owned facts")
                    if (
                        obsolete["kind"] != "preference"
                        or replacement["kind"] != "preference"
                    ):
                        raise ValueError("Evidence repair kinds do not match the transition")
                    actual_obsolete_fact_key = canonical_memory_fact_key(
                        str(obsolete["fact_key"]),
                        kind=str(obsolete["kind"]),
                    )
                    actual_replacement_fact_key = canonical_memory_fact_key(
                        str(replacement["fact_key"]),
                        kind=str(replacement["kind"]),
                    )
                    if (
                        actual_obsolete_fact_key != obsolete_fact_key
                        or actual_replacement_fact_key != replacement_fact_key
                    ):
                        raise ValueError("Evidence repair fact keys do not match")
                    if obsolete["status"] not in {
                        "candidate",
                        "active",
                        "superseded",
                    }:
                        raise ValueError("Obsolete evidence is not repairable")
                    if (
                        replacement["status"] != "active"
                        or not bool(replacement["prompt_eligible"])
                    ):
                        raise ValueError("Replacement evidence is not active")
                    existing_relation = connection.execute(
                        """
                        SELECT target_evidence_id
                        FROM evidence_relations
                        WHERE relation_kind = 'superseded_by'
                          AND source_evidence_id = ?
                        """,
                        (obsolete_evidence_id,),
                    ).fetchone()
                    if (
                        existing_relation is not None
                        and existing_relation["target_evidence_id"]
                        != replacement_evidence_id
                    ):
                        raise ValueError(
                            "Obsolete evidence was already superseded by another fact"
                        )
                    self._delete_context_for_evidence_ids(
                        connection,
                        pet_id=pet_id,
                        evidence_ids=(obsolete_evidence_id,),
                    )
                    connection.execute(
                        """
                        UPDATE companion_evidence
                        SET status = 'superseded', prompt_eligible = 0,
                            content_json = CASE
                                WHEN source_kind = 'conversation_candidate'
                                THEN json_remove(
                                    content_json, '$.source_quote', '$.source_quotes'
                                )
                                ELSE content_json
                            END
                        WHERE evidence_id = ?
                        """,
                        (obsolete_evidence_id,),
                    )
                    self._invalidate_initiative_opportunities_for_evidence(
                        connection,
                        evidence_ids=(obsolete_evidence_id,),
                        reason_code="evidence_repaired",
                        now=now,
                        scrub=False,
                    )
                    self._revoke_adjustments_for_evidence_ids(
                        connection,
                        evidence_ids=(obsolete_evidence_id,),
                    )
                    connection.execute(
                        """
                        UPDATE session_capsules
                        SET status = 'invalidated'
                        WHERE status = 'active'
                          AND capsule_id IN (
                            SELECT capsule_id FROM capsule_evidence
                            WHERE evidence_id = ?
                          )
                        """,
                        (obsolete_evidence_id,),
                    )
                    connection.execute(
                        """
                        UPDATE companion_chapters
                        SET status = 'invalidated'
                        WHERE status = 'active'
                          AND chapter_id IN (
                            SELECT chapter_id FROM chapter_evidence
                            WHERE evidence_id = ?
                          )
                        """,
                        (obsolete_evidence_id,),
                    )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO evidence_relations(
                            relation_id, pet_id, relation_kind,
                            source_evidence_id, target_evidence_id, created_at
                        ) VALUES (?, ?, 'superseded_by', ?, ?, ?)
                        """,
                        (
                            str(
                                uuid5(
                                    NAMESPACE_URL,
                                    "xiaoxin:semantic-evidence-repair:"
                                    f"{obsolete_evidence_id}:{replacement_evidence_id}",
                                )
                            ),
                            pet_id,
                            obsolete_evidence_id,
                            replacement_evidence_id,
                            now,
                        ),
                    )
                    connection.commit()
                    return {
                        "status": (
                            "already_applied"
                            if obsolete["status"] == "superseded"
                            else "applied"
                        ),
                        "obsolete_evidence_id": obsolete_evidence_id,
                        "replacement_evidence_id": replacement_evidence_id,
                    }
                except Exception:
                    connection.rollback()
                    raise

    def correct_evidence_control(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        evidence_id: str,
        replacement_content: Mapping[str, object],
        source_summary: str,
        now: str,
        idempotency_key: str,
    ) -> CompanionControlResult:
        with self.pet_reflection_guard(pet_id):
            return self._correct_evidence_control_locked(
                owner_user_id=owner_user_id,
                pet_id=pet_id,
                memory_subject_id=memory_subject_id,
                evidence_id=evidence_id,
                replacement_content=replacement_content,
                source_summary=source_summary,
                now=now,
                idempotency_key=idempotency_key,
            )

    def _correct_evidence_control_locked(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        evidence_id: str,
        replacement_content: Mapping[str, object],
        source_summary: str,
        now: str,
        idempotency_key: str,
    ) -> CompanionControlResult:
        request_digest = _control_request_digest(
            action="correct_evidence",
            owner_user_id=owner_user_id,
            pet_id=pet_id,
            memory_subject_id=memory_subject_id,
            payload={
                "evidence_id": evidence_id,
                "replacement_content": replacement_content,
                "source_summary": source_summary,
                "now": now,
            },
        )
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._assert_owner_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                )
                existing = self._load_control_replay(
                    connection,
                    action="correct_evidence",
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    request_digest=request_digest,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    connection.commit()
                    return existing
                old = connection.execute(
                    """
                    SELECT *
                    FROM companion_evidence
                    WHERE evidence_id = ?
                      AND pet_id = ?
                      AND memory_subject_id = ?
                    """,
                    (evidence_id, pet_id, memory_subject_id),
                ).fetchone()
                if old is None:
                    raise ValueError("Evidence does not exist in this subject scope")
                if old["status"] in {"forgotten", "superseded", "expired"}:
                    raise ValueError("Evidence is not active")
                self._delete_context_for_evidence_ids(
                    connection,
                    pet_id=pet_id,
                    evidence_ids=(evidence_id,),
                )
                replacement_id = str(
                    uuid5(NAMESPACE_URL, f"xiaoxin:correction:{idempotency_key}")
                )
                relation_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"xiaoxin:correction-relation:{idempotency_key}",
                    )
                )
                observation_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"companion-observation:{pet_id}:{idempotency_key}",
                    )
                )
                observation_idempotency_key = f"memory-corrected:{idempotency_key}"
                observation_payload = {
                    "old_evidence_id": evidence_id,
                    "replacement_evidence_id": replacement_id,
                }
                observation_digest = hashlib.sha256(
                    _stable_json(
                        {
                            "owner_user_id": owner_user_id,
                            "pet_id": pet_id,
                            "memory_subject_id": memory_subject_id,
                            "kind": "memory_corrected",
                            "source_kind": "memory_control",
                            "source_ref": evidence_id,
                            "occurred_at": now,
                            "payload": observation_payload,
                            "safe_summary": source_summary,
                        }
                    ).encode("utf-8")
                ).hexdigest()
                existing_observation = connection.execute(
                    """
                    SELECT 1 FROM companion_observations
                    WHERE idempotency_key = ?
                    """,
                    (observation_idempotency_key,),
                ).fetchone()
                if existing_observation is not None:
                    raise CompanionIdempotencyConflict(
                        "memory correction observation already exists without "
                        "its control replay record"
                    )
                connection.execute(
                    """
                    INSERT INTO companion_observations(
                        observation_id, idempotency_key, owner_user_id,
                        pet_id, memory_subject_id, kind, source_kind,
                        source_ref, payload_json, observation_digest,
                        safe_summary, occurred_at, status, created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, 'memory_corrected', 'memory_control',
                        ?, ?, ?, ?, ?, 'recorded', ?
                    )
                    """,
                    (
                        observation_id,
                        observation_idempotency_key,
                        owner_user_id,
                        pet_id,
                        memory_subject_id,
                        evidence_id,
                        _stable_json(observation_payload),
                        observation_digest,
                        source_summary,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE companion_evidence
                    SET status = 'superseded', prompt_eligible = 0,
                        content_json = CASE
                            WHEN source_kind = 'conversation_candidate'
                            THEN json_remove(
                                content_json, '$.source_quote', '$.source_quotes'
                            )
                            ELSE content_json
                        END
                    WHERE evidence_id = ?
                    """,
                    (evidence_id,),
                )
                self._invalidate_initiative_opportunities_for_evidence(
                    connection,
                    evidence_ids=(evidence_id,),
                    reason_code="evidence_corrected",
                    now=now,
                    scrub=False,
                )
                connection.execute(
                    """
                    UPDATE session_capsules
                    SET status = 'invalidated'
                    WHERE capsule_id IN (
                        SELECT capsule_id
                        FROM capsule_evidence
                        WHERE evidence_id = ?
                    )
                    """,
                    (evidence_id,),
                )
                self._revoke_adjustments_for_evidence_ids(
                    connection,
                    evidence_ids=(evidence_id,),
                )
                connection.execute(
                    """
                    UPDATE companion_chapters
                    SET status = 'invalidated'
                    WHERE chapter_id IN (
                        SELECT chapter_id
                        FROM chapter_evidence
                        WHERE evidence_id = ?
                    )
                    """,
                    (evidence_id,),
                )
                connection.execute(
                    """
                    INSERT INTO companion_evidence(
                        evidence_id,
                        pet_id,
                        memory_subject_id,
                        ownership_scope,
                        relationship_epoch_id,
                        kind,
                        content_json,
                        content_version,
                        fact_key,
                        importance,
                        sensitivity,
                        valid_from,
                        valid_until,
                        source_kind,
                        source_ref,
                        source_summary,
                        attribution,
                        confidence,
                        occurred_at,
                        retention,
                        status,
                        prompt_eligible,
                        expires_at,
                        created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        'control', ?, ?, ?, 1.0, ?, ?, 'active', 1, ?, ?
                    )
                    """,
                    (
                        replacement_id,
                        pet_id,
                        memory_subject_id,
                        old["ownership_scope"],
                        old["relationship_epoch_id"],
                        old["kind"],
                        _stable_json(replacement_content),
                        old["content_version"],
                        old["fact_key"],
                        old["importance"],
                        old["sensitivity"],
                        old["valid_from"],
                        old["valid_until"],
                        f"control:{idempotency_key}",
                        source_summary,
                        old["attribution"],
                        now,
                        old["retention"],
                        old["expires_at"],
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO evidence_relations(
                        relation_id,
                        pet_id,
                        relation_kind,
                        source_evidence_id,
                        target_evidence_id,
                        created_at
                    ) VALUES (?, ?, 'superseded_by', ?, ?, ?)
                    """,
                    (relation_id, pet_id, evidence_id, replacement_id, now),
                )
                connection.execute(
                    """
                    INSERT INTO observation_evidence(
                        observation_id, evidence_id, pet_id
                    ) VALUES (?, ?, ?)
                    """,
                    (observation_id, replacement_id, pet_id),
                )
                epoch = connection.execute(
                    """
                    SELECT epoch_id FROM relationship_epochs
                    WHERE pet_id = ? AND ended_at IS NULL
                    """,
                    (pet_id,),
                ).fetchone()
                job_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"xiaoxin:recompute-after-correction:{idempotency_key}",
                    )
                )
                connection.execute(
                    """
                    INSERT INTO consolidation_jobs(
                        job_id, pet_id, relationship_epoch_id, job_kind,
                        idempotency_key, payload_json, status, due_at,
                        schema_version, created_at, updated_at
                    ) VALUES (?, ?, ?, 'recompute_after_correction', ?, ?, 'pending', ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        pet_id,
                        epoch["epoch_id"] if epoch is not None else None,
                        f"recompute-after-correction:{idempotency_key}",
                        _stable_json(
                            {
                                "memory_subject_id": memory_subject_id,
                                "old_evidence_id": evidence_id,
                                "replacement_evidence_id": replacement_id,
                            }
                        ),
                        now,
                        "companion-recompute-v1",
                        now,
                        now,
                    ),
                )
                result = CompanionControlResult(
                    action="correct_evidence",
                    status="applied",
                    deactivated=1,
                    requeued=1,
                )
                self._insert_control_record(
                    connection,
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    action="correct_evidence",
                    payload={
                        "old_evidence_id": evidence_id,
                        "replacement_evidence_id": replacement_id,
                    },
                    request_digest=request_digest,
                    result=result,
                    now=now,
                    idempotency_key=idempotency_key,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def set_boundary(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        boundary_key: str,
        value: object,
        source_summary: str,
        now: str,
        idempotency_key: str,
    ) -> CompanionControlResult:
        with self.pet_reflection_guard(pet_id):
            return self._set_boundary_locked(
                owner_user_id=owner_user_id,
                pet_id=pet_id,
                memory_subject_id=memory_subject_id,
                boundary_key=boundary_key,
                value=value,
                source_summary=source_summary,
                now=now,
                idempotency_key=idempotency_key,
            )

    def _set_boundary_locked(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        boundary_key: str,
        value: object,
        source_summary: str,
        now: str,
        idempotency_key: str,
    ) -> CompanionControlResult:
        request_digest = _control_request_digest(
            action="set_boundary",
            owner_user_id=owner_user_id,
            pet_id=pet_id,
            memory_subject_id=memory_subject_id,
            payload={
                "boundary_key": boundary_key,
                "value": value,
                "source_summary": source_summary,
                "now": now,
            },
        )
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._assert_owner_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                )
                existing = self._load_control_replay(
                    connection,
                    action="set_boundary",
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    request_digest=request_digest,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    connection.commit()
                    return existing
                evidence_id = str(
                    uuid5(NAMESPACE_URL, f"xiaoxin:boundary:{idempotency_key}")
                )
                connection.execute(
                    """
                    INSERT INTO companion_evidence(
                        evidence_id, pet_id, memory_subject_id, ownership_scope,
                        relationship_epoch_id, kind, content_json, source_kind,
                        source_ref, source_summary, attribution, confidence,
                        occurred_at, retention, status, prompt_eligible, created_at
                    ) VALUES (
                        ?, ?, ?, 'user', NULL, 'explicit_boundary', ?, 'control',
                        ?, ?, 'explicit_user_statement', 1.0, ?, 'persistent',
                        'active', 1, ?
                    )
                    """,
                    (
                        evidence_id,
                        pet_id,
                        memory_subject_id,
                        _stable_json({"boundary_key": boundary_key, "value": value}),
                        f"control:{idempotency_key}",
                        source_summary,
                        now,
                        now,
                    ),
                )
                result = CompanionControlResult(
                    action="set_boundary",
                    status="applied",
                    retained=1,
                )
                self._insert_control_record(
                    connection,
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    action="set_boundary",
                    payload={"evidence_id": evidence_id, "boundary_key": boundary_key},
                    request_digest=request_digest,
                    result=result,
                    now=now,
                    idempotency_key=idempotency_key,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def revoke_boundary(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        evidence_id: str,
        now: str,
        idempotency_key: str,
    ) -> CompanionControlResult:
        with self.pet_reflection_guard(pet_id):
            return self._revoke_boundary_locked(
                owner_user_id=owner_user_id,
                pet_id=pet_id,
                memory_subject_id=memory_subject_id,
                evidence_id=evidence_id,
                now=now,
                idempotency_key=idempotency_key,
            )

    def _revoke_boundary_locked(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        evidence_id: str,
        now: str,
        idempotency_key: str,
    ) -> CompanionControlResult:
        request_digest = _control_request_digest(
            action="revoke_boundary",
            owner_user_id=owner_user_id,
            pet_id=pet_id,
            memory_subject_id=memory_subject_id,
            payload={"evidence_id": evidence_id, "now": now},
        )
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._assert_owner_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                )
                existing = self._load_control_replay(
                    connection,
                    action="revoke_boundary",
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    request_digest=request_digest,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    connection.commit()
                    return existing
                boundary = connection.execute(
                    """
                    SELECT status
                    FROM companion_evidence
                    WHERE evidence_id = ?
                      AND pet_id = ?
                      AND memory_subject_id = ?
                      AND ownership_scope = 'user'
                      AND kind = 'explicit_boundary'
                    """,
                    (evidence_id, pet_id, memory_subject_id),
                ).fetchone()
                if boundary is None:
                    raise ValueError("boundary Evidence does not exist")
                deactivated = int(boundary["status"] in {"candidate", "active"})
                connection.execute(
                    """
                    UPDATE companion_evidence
                    SET status = 'forgotten', prompt_eligible = 0
                    WHERE evidence_id = ?
                    """,
                    (evidence_id,),
                )
                connection.execute(
                    """
                    UPDATE session_capsules
                    SET status = 'invalidated'
                    WHERE capsule_id IN (
                        SELECT capsule_id FROM capsule_evidence WHERE evidence_id = ?
                    )
                    """,
                    (evidence_id,),
                )
                connection.execute(
                    """
                    UPDATE companion_adjustments
                    SET status = 'revoked'
                    WHERE adjustment_id IN (
                        SELECT adjustment_id
                        FROM adjustment_evidence
                        WHERE evidence_id = ?
                    )
                    """,
                    (evidence_id,),
                )
                connection.execute(
                    """
                    UPDATE companion_chapters
                    SET status = 'invalidated'
                    WHERE chapter_id IN (
                        SELECT chapter_id FROM chapter_evidence WHERE evidence_id = ?
                    )
                    """,
                    (evidence_id,),
                )
                result = CompanionControlResult(
                    action="revoke_boundary",
                    status="applied",
                    deactivated=deactivated,
                )
                self._insert_control_record(
                    connection,
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    action="revoke_boundary",
                    payload={"evidence_id": evidence_id},
                    request_digest=request_digest,
                    result=result,
                    now=now,
                    idempotency_key=idempotency_key,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def purge_personal_memory(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        now: str,
        idempotency_key: str,
    ) -> CompanionControlResult:
        with self.pet_reflection_guard(pet_id):
            return self._purge_personal_memory_locked(
                owner_user_id=owner_user_id,
                pet_id=pet_id,
                memory_subject_id=memory_subject_id,
                now=now,
                idempotency_key=idempotency_key,
            )

    def _purge_personal_memory_locked(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        now: str,
        idempotency_key: str,
    ) -> CompanionControlResult:
        request_digest = _control_request_digest(
            action="purge_personal_memory",
            owner_user_id=owner_user_id,
            pet_id=pet_id,
            memory_subject_id=memory_subject_id,
            payload={"now": now},
        )
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._assert_owner_in_connection(
                    connection,
                    owner_user_id=owner_user_id,
                    pet_id=pet_id,
                )
                existing = self._load_control_replay(
                    connection,
                    action="purge_personal_memory",
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    request_digest=request_digest,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    connection.commit()
                    return existing
                forgotten = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM companion_evidence
                    WHERE pet_id = ?
                      AND status <> 'forgotten'
                    """,
                    (pet_id,),
                ).fetchone()[0]
                active_epoch = connection.execute(
                    """
                    SELECT epoch_id FROM relationship_epochs
                    WHERE pet_id = ? AND ended_at IS NULL
                    """,
                    (pet_id,),
                ).fetchone()
                if active_epoch is None:
                    raise ValueError("pet has no active relationship epoch")
                connection.execute(
                    """
                    UPDATE relationship_epochs
                    SET ended_at = ?, end_reason = 'personal_memory_purge'
                    WHERE epoch_id = ?
                    """,
                    (now, active_epoch["epoch_id"]),
                )
                connection.execute(
                    """
                    UPDATE companion_evidence
                    SET content_json = '{}',
                        source_ref = 'purged',
                        source_summary = 'purged',
                        attribution = 'purged',
                        status = 'forgotten',
                        prompt_eligible = 0,
                        expires_at = NULL
                    WHERE pet_id = ?
                    """,
                    (pet_id,),
                )
                connection.execute(
                    """
                    UPDATE session_capsules
                    SET safe_summary = 'purged',
                        interaction_outcome = 'purged',
                        adjustment_signals_json = '[]',
                        status = 'invalidated'
                    WHERE pet_id = ?
                    """,
                    (pet_id,),
                )
                connection.execute(
                    """
                    UPDATE companion_adjustments
                    SET value_json = '{}', status = 'revoked'
                    WHERE pet_id = ?
                    """,
                    (pet_id,),
                )
                connection.execute(
                    """
                    UPDATE companion_chapters
                    SET safe_narrative = 'purged', status = 'invalidated'
                    WHERE pet_id = ?
                    """,
                    (pet_id,),
                )
                connection.execute(
                    """
                    UPDATE initiative_decisions
                    SET evidence_ids_json = '[]',
                        content_brief = 'purged',
                        hardware_expression_json = '{}'
                    WHERE pet_id = ?
                    """,
                    (pet_id,),
                )
                connection.execute(
                    """
                    UPDATE initiative_opportunities
                    SET evidence_ids_json = '[]', safe_brief = 'purged',
                        status = CASE
                          WHEN status IN ('scheduled', 'deferred', 'claimed')
                          THEN 'invalidated' ELSE status END,
                        outcome_code = CASE
                          WHEN status IN ('scheduled', 'deferred', 'claimed')
                          THEN 'personal_memory_purged' ELSE outcome_code END,
                        lease_until = NULL, updated_at = ?
                    WHERE pet_id = ?
                    """,
                    (now, pet_id),
                )
                connection.execute(
                    "DELETE FROM companion_turn_sources WHERE pet_id = ?",
                    (pet_id,),
                )
                connection.execute(
                    "DELETE FROM companion_context_messages WHERE pet_id = ?",
                    (pet_id,),
                )
                connection.execute(
                    "DELETE FROM companion_va_snapshots WHERE pet_id = ?",
                    (pet_id,),
                )
                connection.execute(
                    "DELETE FROM companion_retrieval_audits WHERE pet_id = ?",
                    (pet_id,),
                )
                connection.execute(
                    "DELETE FROM companion_interaction_contracts WHERE pet_id = ?",
                    (pet_id,),
                )
                connection.execute(
                    "DELETE FROM relationship_stage_events WHERE pet_id = ?",
                    (pet_id,),
                )
                connection.execute(
                    "DELETE FROM semantic_memory_evaluations WHERE pet_id = ?",
                    (pet_id,),
                )
                connection.execute(
                    """
                    UPDATE consolidation_jobs
                    SET payload_json = '{}',
                        status = 'cancelled',
                        updated_at = ?
                    WHERE pet_id = ?
                    """,
                    (now, pet_id),
                )
                connection.execute(
                    """
                    UPDATE memory_controls
                    SET payload_json = '{}'
                    WHERE pet_id = ?
                    """,
                    (pet_id,),
                )
                new_epoch_id = str(uuid4())
                connection.execute(
                    """
                    INSERT INTO relationship_epochs(
                        epoch_id, pet_id, started_at, start_reason
                    ) VALUES (?, ?, ?, 'personal_memory_purge')
                    """,
                    (new_epoch_id, pet_id, now),
                )
                result = CompanionControlResult(
                    action="purge_personal_memory",
                    status="applied",
                    forgotten=int(forgotten),
                )
                self._insert_control_record(
                    connection,
                    pet_id=pet_id,
                    memory_subject_id=memory_subject_id,
                    action="purge_personal_memory",
                    payload={
                        "retained": [
                            "account",
                            "device_ownership",
                            "student_profile",
                            "personal_pet",
                        ]
                    },
                    request_digest=request_digest,
                    result=result,
                    now=now,
                    idempotency_key=idempotency_key,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _insert_control_record(
        connection: sqlite3.Connection,
        *,
        pet_id: str,
        memory_subject_id: str,
        action: str,
        payload: Mapping[str, object],
        request_digest: str,
        result: CompanionControlResult,
        now: str,
        idempotency_key: str,
    ) -> None:
        audit_payload = dict(payload)
        audit_payload["request_digest"] = request_digest
        connection.execute(
            """
            INSERT INTO memory_controls(
                control_id, pet_id, memory_subject_id, action,
                payload_json, result_json, created_at, idempotency_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                pet_id,
                memory_subject_id,
                action,
                _stable_json(audit_payload),
                _stable_json(
                    {
                        "action": result.action,
                        "status": result.status,
                        "retained": result.retained,
                        "deactivated": result.deactivated,
                        "forgotten": result.forgotten,
                        "requeued": result.requeued,
                    }
                ),
                now,
                idempotency_key,
            ),
        )

    @staticmethod
    def _load_control_replay(
        connection: sqlite3.Connection,
        *,
        action: str,
        pet_id: str,
        memory_subject_id: str,
        request_digest: str,
        idempotency_key: str,
    ) -> CompanionControlResult | None:
        row = connection.execute(
            """
            SELECT pet_id, memory_subject_id, action, payload_json, result_json
            FROM memory_controls
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        try:
            stored_digest = json.loads(row["payload_json"])["request_digest"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise CompanionIdempotencyConflict(
                f"control idempotency key {idempotency_key!r} has no request digest"
            ) from exc
        if (
            row["pet_id"] != pet_id
            or row["memory_subject_id"] != memory_subject_id
            or row["action"] != action
            or stored_digest != request_digest
        ):
            raise CompanionIdempotencyConflict(
                f"control idempotency key {idempotency_key!r} was reused "
                "for a different command"
            )
        return CompanionControlResult(**json.loads(row["result_json"]))

    @staticmethod
    def _assert_owner_in_connection(
        connection: sqlite3.Connection,
        *,
        owner_user_id: str,
        pet_id: str,
    ) -> None:
        row = connection.execute(
            """
            SELECT owner_user_id
            FROM companion_pets
            WHERE pet_id = ?
            """,
            (pet_id,),
        ).fetchone()
        if row is None or row["owner_user_id"] != owner_user_id:
            raise PermissionError("owner does not control this personal pet")

    def correct_evidence(
        self,
        *,
        old_evidence_id: str,
        replacement: CompanionEvidence,
        relation_id: str,
        created_at: str,
    ) -> None:
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                old = connection.execute(
                    """
                    SELECT pet_id, memory_subject_id, status
                    FROM companion_evidence
                    WHERE evidence_id = ?
                    """,
                    (old_evidence_id,),
                ).fetchone()
                if old is None:
                    raise ValueError("old Evidence does not exist")
                if old["status"] in {"forgotten", "superseded", "expired"}:
                    raise ValueError("old Evidence is not active")
                if replacement.pet_id != old["pet_id"]:
                    raise ValueError("replacement pet_id does not match old Evidence")
                if replacement.memory_subject_id != old["memory_subject_id"]:
                    raise ValueError(
                        "replacement memory_subject_id does not match old Evidence"
                    )
                if replacement.status != "active" or not replacement.prompt_eligible:
                    raise ValueError(
                        "replacement Evidence must be active and prompt eligible"
                    )
                connection.execute(
                    """
                    UPDATE companion_evidence
                    SET status = 'superseded', prompt_eligible = 0,
                        content_json = CASE
                            WHEN source_kind = 'conversation_candidate'
                            THEN json_remove(
                                content_json, '$.source_quote', '$.source_quotes'
                            )
                            ELSE content_json
                        END
                    WHERE evidence_id = ?
                    """,
                    (old_evidence_id,),
                )
                connection.execute(
                    """
                    INSERT INTO companion_evidence(
                        evidence_id,
                        pet_id,
                        memory_subject_id,
                        ownership_scope,
                        relationship_epoch_id,
                        kind,
                        content_json,
                        source_kind,
                        source_ref,
                        source_summary,
                        attribution,
                        confidence,
                        occurred_at,
                        retention,
                        status,
                        prompt_eligible,
                        expires_at,
                        created_at,
                        speaker_identity
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        replacement.evidence_id,
                        replacement.pet_id,
                        replacement.memory_subject_id,
                        replacement.ownership_scope,
                        replacement.relationship_epoch_id,
                        replacement.kind,
                        _stable_json(replacement.content),
                        replacement.source_kind,
                        replacement.source_ref,
                        replacement.source_summary,
                        replacement.attribution,
                        replacement.confidence,
                        replacement.occurred_at,
                        replacement.retention,
                        replacement.status,
                        int(replacement.prompt_eligible),
                        replacement.expires_at,
                        created_at,
                        replacement.speaker_identity,
                    ),
                )
                CompanionStore._invalidate_initiative_opportunities_for_evidence(
                    connection,
                    evidence_ids=(old_evidence_id,),
                    reason_code="evidence_superseded",
                    now=created_at,
                    scrub=False,
                )
                connection.execute(
                    """
                    INSERT INTO evidence_relations(
                        relation_id,
                        pet_id,
                        relation_kind,
                        source_evidence_id,
                        target_evidence_id,
                        created_at
                    ) VALUES (?, ?, 'superseded_by', ?, ?, ?)
                    """,
                    (
                        relation_id,
                        replacement.pet_id,
                        old_evidence_id,
                        replacement.evidence_id,
                        created_at,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise


def _outcome_digest(outcome: CompanionTurnOutcome) -> str:
    payload = _stable_json(
        {
            "visible_response": outcome.visible_response,
            "assistant_action": outcome.assistant_action,
            "delivery_status": outcome.delivery_status,
            "feedback_signals": list(outcome.feedback_signals),
        },
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _stable_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _fts_query_expression(query: str | None) -> str | None:
    unique_terms = _search_trigrams(query)[:32]
    if not unique_terms:
        return None
    return " OR ".join(
        f'"{term.replace(chr(34), chr(34) * 2)}"' for term in unique_terms
    )


def _search_trigrams(value: str | None) -> tuple[str, ...]:
    if not isinstance(value, str) or not value.strip():
        return ()
    terms: list[str] = []
    for segment in re.findall(r"[\w\u3400-\u9fff]+", value.lower()):
        if len(segment) < 3:
            continue
        terms.extend(segment[index : index + 3] for index in range(len(segment) - 2))
    return tuple(dict.fromkeys(terms))


def _retrieval_score(
    evidence: CompanionEvidence,
    *,
    query: str | None,
    fact_keys: tuple[object, ...],
    kinds: tuple[object, ...],
    now: str,
    recent_reference_count: int,
    apply_recent_reference_penalty: bool,
) -> dict[str, object]:
    exact_fact = bool(evidence.fact_key and evidence.fact_key in fact_keys)
    kind_match = evidence.kind in kinds
    query_terms = set(_search_trigrams(query))
    document_terms = set(
        _search_trigrams(
            " ".join(
                (
                    evidence.fact_key or "",
                    evidence.source_summary,
                    _stable_json(dict(evidence.content)),
                )
            )
        )
    )
    lexical_overlap = (
        len(query_terms.intersection(document_terms)) / len(query_terms)
        if query_terms
        else 0.0
    )
    age_days = max(
        (
            datetime.fromisoformat(now) - datetime.fromisoformat(evidence.occurred_at)
        ).total_seconds()
        / 86400.0,
        0.0,
    )
    freshness = max(0.0, 1.0 - min(age_days / 365.0, 1.0))
    sensitivity_score = {
        "low": 2.0,
        "private": 0.0,
        "sensitive": -20.0,
    }[evidence.sensitivity]
    factors = {
        "exact_fact": 100.0 if exact_fact else 0.0,
        "kind_match": 25.0 if kind_match else 0.0,
        "lexical_overlap": lexical_overlap * 45.0,
        "confidence": evidence.confidence * 8.0,
        "importance": evidence.importance * 8.0,
        "freshness": freshness * 8.0,
        "current_epoch": 2.0 if evidence.ownership_scope == "relationship" else 0.0,
        "sensitivity": sensitivity_score,
        "recent_reference": (
            -12.0 * min(recent_reference_count, 2)
            if apply_recent_reference_penalty
            else 0.0
        ),
    }
    total_score = sum(factors.values())
    return {
        "evidence_id": evidence.evidence_id,
        "total_score": round(total_score, 6),
        "exact_fact": exact_fact,
        "kind_match": kind_match,
        "lexical_overlap": round(lexical_overlap, 6),
        "recent_reference_count": recent_reference_count,
        "sensitivity": evidence.sensitivity,
    }


def _deduplicate_memory_fact_key_aliases(
    ranked: list[tuple[CompanionEvidence, dict[str, object]]],
) -> list[tuple[CompanionEvidence, dict[str, object]]]:
    stored_keys_by_canonical_slot: dict[str, set[str]] = {}
    for evidence, _ in ranked:
        if not evidence.fact_key:
            continue
        canonical_slot = canonical_memory_fact_key(
            evidence.fact_key,
            kind=evidence.kind,
        )
        stored_keys_by_canonical_slot.setdefault(canonical_slot, set()).add(
            evidence.fact_key
        )
    selected_legacy_slots: set[str] = set()
    deduplicated: list[tuple[CompanionEvidence, dict[str, object]]] = []
    for item in ranked:
        evidence = item[0]
        if not evidence.fact_key:
            deduplicated.append(item)
            continue
        canonical_slot = canonical_memory_fact_key(
            evidence.fact_key,
            kind=evidence.kind,
        )
        stored_keys = stored_keys_by_canonical_slot[canonical_slot]
        if len(stored_keys) == 1:
            deduplicated.append(item)
            continue
        if canonical_slot in stored_keys:
            if evidence.fact_key == canonical_slot:
                deduplicated.append(item)
            continue
        if canonical_slot in selected_legacy_slots:
            continue
        selected_legacy_slots.add(canonical_slot)
        deduplicated.append(item)
    return deduplicated


def _retrieval_audit_from_row(row: sqlite3.Row) -> Mapping[str, object]:
    try:
        selected_ids = json.loads(row["selected_evidence_ids_json"])
        score_details = json.loads(row["score_details_json"])
    except json.JSONDecodeError as exc:
        raise sqlite3.DatabaseError("retrieval audit payload must be JSON") from exc
    if (
        not isinstance(selected_ids, list)
        or any(not isinstance(item, str) for item in selected_ids)
        or not isinstance(score_details, list)
        or any(not isinstance(item, dict) for item in score_details)
    ):
        raise sqlite3.DatabaseError("retrieval audit payload shape is invalid")
    return {
        "turn_id": row["turn_id"],
        "relationship_epoch_id": row["relationship_epoch_id"],
        "interaction_kind": row["interaction_kind"],
        "query_digest": row["query_digest"],
        "hints_digest": row["hints_digest"],
        "candidate_count": int(row["candidate_count"]),
        "selected_evidence_ids": tuple(selected_ids),
        "score_details": tuple(score_details),
        "duration_ms": float(row["duration_ms"]),
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
    }


def _relationship_stage_event_from_row(
    row: sqlite3.Row,
) -> Mapping[str, object]:
    try:
        quality = json.loads(row["quality_json"])
        reason_codes = json.loads(row["reason_codes_json"])
    except json.JSONDecodeError as exc:
        raise sqlite3.DatabaseError(
            "relationship stage event payload must be JSON"
        ) from exc
    if (
        not isinstance(quality, dict)
        or set(quality) != {"continuity", "knowledge", "helpfulness", "attunement"}
        or any(type(value) is not int or value < 0 for value in quality.values())
        or not isinstance(reason_codes, list)
        or any(not isinstance(item, str) for item in reason_codes)
    ):
        raise sqlite3.DatabaseError("relationship stage event payload is invalid")
    return {
        "event_id": row["event_id"],
        "memory_subject_id": row["memory_subject_id"],
        "relationship_epoch_id": row["relationship_epoch_id"],
        "previous_stage": row["previous_stage"],
        "relationship_stage": row["relationship_stage"],
        "quality": quality,
        "reason_codes": tuple(reason_codes),
        "policy_version": row["policy_version"],
        "occurred_at": row["occurred_at"],
    }


def _is_explicit_semantic_correction(
    request: MemoryInterpretationRequest,
    proposal: MemoryProposal,
) -> bool:
    current_source = next(
        (
            source
            for source in request.sources
            if source.turn_id == request.current_turn_id and source.role == "user"
        ),
        None,
    )
    if current_source is None or not any(
        marker in current_source.text for marker in _EXPLICIT_MEMORY_CORRECTION_MARKERS
    ):
        return False
    return all(
        quote.turn_id == request.current_turn_id and quote.quote in current_source.text
        for quote in proposal.source_quotes
    )


def _explicit_goal_transition_completed_text(
    request: MemoryInterpretationRequest,
    proposal: MemoryProposal,
) -> str | None:
    if (
        proposal.kind != "goal"
        or proposal.claim_type != "explicit_statement"
        or proposal.subject_scope != "self"
        or not proposal.source_quotes
    ):
        return None
    current_source = next(
        (
            source
            for source in request.sources
            if source.turn_id == request.current_turn_id and source.role == "user"
        ),
        None,
    )
    if current_source is None or not all(
        quote.turn_id == request.current_turn_id and quote.quote in current_source.text
        for quote in proposal.source_quotes
    ):
        return None
    if not any(
        marker in current_source.text
        for marker in (
            *_EXPLICIT_MEMORY_CORRECTION_MARKERS,
            *_EXPLICIT_MEMORY_REQUEST_MARKERS,
        )
    ):
        return None
    transition_indexes = [
        current_source.text.find(marker)
        for marker in _GOAL_TRANSITION_NEXT_MARKERS
        if marker in current_source.text
    ]
    if not transition_indexes:
        return None
    completed_text = current_source.text[: min(transition_indexes)]
    if not any(marker in completed_text for marker in _GOAL_TRANSITION_END_MARKERS):
        return None
    return completed_text


def _goal_value_is_explicitly_completed(
    canonical_value: str,
    completed_text: str,
) -> bool:
    value = re.sub(r"[\s，。！？、,:：;；]", "", canonical_value)
    value = re.sub(
        r"^(?:当前主要目标是|当前目标是|主要目标是)?"
        r"(?:正在准备参加|准备参加|计划参加|正在准备|正在推进|准备|计划|推进|参加)?",
        "",
        value,
        count=1,
    )
    if len(value) < 2:
        return False
    for clause in re.split(r"[。！？；;]", completed_text):
        compact_clause = re.sub(r"[\s，、,:：]", "", clause)
        for end_marker in _GOAL_TRANSITION_END_MARKERS:
            if end_marker not in compact_clause:
                continue
            ended_subject = compact_clause.split(end_marker, 1)[0]
            for prefix in (
                *_EXPLICIT_MEMORY_CORRECTION_MARKERS,
                "旧目标",
                "原来的目标",
                "之前的目标",
                "我之前准备的",
                "之前准备的",
                "我准备的",
            ):
                if ended_subject.startswith(prefix):
                    ended_subject = ended_subject[len(prefix) :]
            if ended_subject in {
                value,
                f"{value}考试",
                f"{value}目标",
                f"{value}这个目标",
            }:
                return True
    return False


def _is_explicit_semantic_memory_request(
    request: MemoryInterpretationRequest,
    proposal: MemoryProposal,
) -> bool:
    current_source = next(
        (
            source
            for source in request.sources
            if source.turn_id == request.current_turn_id and source.role == "user"
        ),
        None,
    )
    if current_source is None or not any(
        marker in current_source.text for marker in _EXPLICIT_MEMORY_REQUEST_MARKERS
    ):
        return False
    return bool(proposal.source_quotes) and all(
        quote.turn_id == request.current_turn_id and quote.quote in current_source.text
        for quote in proposal.source_quotes
    )


def _pending_observation_digest(
    *,
    owner_user_id: str,
    pet_id: str,
    kind: str,
    source_kind: str,
    source_ref: str,
    occurred_at: str,
    payload: Mapping[str, object],
    safe_summary: str,
) -> str:
    value = {
        "owner_user_id": owner_user_id,
        "pet_id": pet_id,
        "kind": kind,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "occurred_at": occurred_at,
        "payload": dict(payload),
        "safe_summary": safe_summary,
    }
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _control_request_digest(
    *,
    action: str,
    owner_user_id: str,
    pet_id: str,
    memory_subject_id: str,
    payload: Mapping[str, object],
) -> str:
    semantic_payload = {key: value for key, value in payload.items() if key != "now"}
    encoded = _stable_json(
        {
            "action": action,
            "owner_user_id": owner_user_id,
            "pet_id": pet_id,
            "memory_subject_id": memory_subject_id,
            "payload": semantic_payload,
        }
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _birth_temperament_from_row(row: sqlite3.Row) -> BirthTemperament:
    return BirthTemperament(
        pet_id=row["pet_id"],
        generator_version=row["generator_version"],
        exploration_orientation=row["exploration_orientation"],
        expression_energy=row["expression_energy"],
        thought_organization=row["thought_organization"],
        playfulness=row["playfulness"],
        companion_initiative=row["companion_initiative"],
        generated_at=row["generated_at"],
        source_kind=row["source_kind"],
    )


def _relationship_epoch_from_row(row: sqlite3.Row) -> RelationshipEpoch:
    return RelationshipEpoch(
        epoch_id=row["epoch_id"],
        pet_id=row["pet_id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        start_reason=row["start_reason"],
        end_reason=row["end_reason"],
    )


def _semantic_canonical_value(content_json: str) -> str:
    try:
        content = json.loads(content_json)
    except (TypeError, json.JSONDecodeError):
        return "unknown"
    if isinstance(content, dict):
        for key in ("canonical_value", "value", "preference", "event", "goal"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value[:200]
    return "unknown"


def _evidence_from_row(row: sqlite3.Row) -> CompanionEvidence:
    return CompanionEvidence(
        evidence_id=row["evidence_id"],
        pet_id=row["pet_id"],
        memory_subject_id=row["memory_subject_id"],
        ownership_scope=row["ownership_scope"],
        relationship_epoch_id=row["relationship_epoch_id"],
        kind=row["kind"],
        content=json.loads(row["content_json"]),
        source_kind=row["source_kind"],
        source_ref=row["source_ref"],
        source_summary=row["source_summary"],
        attribution=row["attribution"],
        confidence=float(row["confidence"]),
        occurred_at=row["occurred_at"],
        retention=row["retention"],
        status=row["status"],
        prompt_eligible=bool(row["prompt_eligible"]),
        expires_at=row["expires_at"],
        fact_key=row["fact_key"],
        importance=float(row["importance"]),
        sensitivity=row["sensitivity"],
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
        speaker_identity=row["speaker_identity"],
    )


def _va_state_from_row(row: sqlite3.Row) -> VAState:
    valence = int(row["valence"])
    arousal = int(row["arousal"])
    age = row["dynamics_age"]
    relationship_stage = str(row["dynamics_relationship_stage"])
    context = str(row["context"])
    if not -1000 <= valence <= 1000 or not -1000 <= arousal <= 1000:
        raise ValueError("VA snapshot coordinates are invalid")
    if age not in {None, 1, 2, 3, 4}:
        raise ValueError("VA snapshot age is invalid")
    if relationship_stage not in {
        "first_meeting",
        "familiar",
        "attuned",
        "long_term_companion",
    }:
        raise ValueError("VA snapshot relationship stage is invalid")
    if context not in {
        "ordinary",
        "celebration",
        "supportive_settled",
        "receptive_brief",
    }:
        raise ValueError("VA snapshot context is invalid")
    state = VAState(
        valence=valence,
        arousal=arousal,
        observed_at=str(row["observed_at"]),
        expires_at=str(row["expires_at"]),
        age=age,
        relationship_stage=relationship_stage,
        context=context,
    )
    observed = datetime.fromisoformat(state.observed_at)
    expires = datetime.fromisoformat(state.expires_at)
    if (
        observed.tzinfo is None
        or observed.utcoffset() is None
        or expires.tzinfo is None
        or expires.utcoffset() is None
        or expires <= observed
    ):
        raise ValueError("VA snapshot timestamps are invalid")
    return state


def _json_text_ids(value: str) -> tuple[str, ...]:
    try:
        raw = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return ()
    if (
        not isinstance(raw, list)
        or not raw
        or len(set(raw)) != len(raw)
        or any(not isinstance(item, str) or not item.strip() for item in raw)
    ):
        return ()
    return tuple(raw)


def _initiative_opportunity_from_row(
    row: sqlite3.Row,
    *,
    now: str | None = None,
) -> DueInitiativeOpportunity:
    evidence_ids = _json_text_ids(row["evidence_ids_json"])
    if not evidence_ids:
        empty_boot_evidence = (
            row["opportunity_kind"] == "boot_checkin"
            and str(row["evidence_ids_json"]).strip() == "[]"
        )
        if not empty_boot_evidence:
            raise ValueError("initiative opportunity Evidence is invalid")
    row_keys = set(row.keys())
    initiative_bias = (
        row["connection_initiative_bias"]
        if "connection_initiative_bias" in row_keys
        else None
    )
    relationship_stage = (
        row["connection_relationship_stage"]
        if "connection_relationship_stage" in row_keys
        else None
    )
    threshold_seconds = (
        row["connection_threshold_seconds"]
        if "connection_threshold_seconds" in row_keys
        else None
    )
    return DueInitiativeOpportunity(
        opportunity_id=row["opportunity_id"],
        owner_user_id=row["owner_user_id"],
        pet_id=row["pet_id"],
        memory_subject_id=row["memory_subject_id"],
        relationship_epoch_id=row["relationship_epoch_id"],
        opportunity_kind=row["opportunity_kind"],
        reason_code=row["reason_code"],
        evidence_ids=evidence_ids,
        safe_brief=row["safe_brief"],
        due_at=row["due_at"],
        attempt=int(row["attempt"]),
        decision_id=row["decision_id"],
        initiative_bias=(str(initiative_bias) if initiative_bias is not None else None),
        relationship_stage=(
            str(relationship_stage) if relationship_stage is not None else None
        ),
        connection_need_strength=_connection_need_strength(
            opportunity_kind=str(row["opportunity_kind"]),
            due_at=str(row["due_at"]),
            threshold_seconds=threshold_seconds,
            now=now,
        ),
    )


def _connection_ignore_backoff_seconds(
    *,
    threshold_seconds: int,
    initiative_bias: str,
    ignored_streak: int,
) -> int:
    if initiative_bias not in _CONNECTION_IGNORE_BACKOFF_FACTORS:
        raise ValueError("connection initiative bias is invalid")
    streak = max(int(ignored_streak), 1)
    delay = (
        max(int(threshold_seconds), 1)
        * _CONNECTION_IGNORE_BACKOFF_FACTORS[initiative_bias]
        * (2 ** (streak - 1))
    )
    return min(max(int(delay), 1), _CONNECTION_IGNORE_BACKOFF_CAP_SECONDS)


def _connection_need_strength(
    *,
    opportunity_kind: str,
    due_at: str,
    threshold_seconds: object,
    now: str | None,
) -> str | None:
    if opportunity_kind != "connection_bid" or threshold_seconds is None:
        return None
    threshold = max(int(threshold_seconds), 1)
    overdue_seconds = 0.0
    if now is not None:
        overdue_seconds = max(
            (datetime.fromisoformat(now) - datetime.fromisoformat(due_at)).total_seconds(),
            0.0,
        )
    if overdue_seconds >= threshold:
        return "clear"
    if overdue_seconds >= threshold * 0.25:
        return "steady"
    return "light"
