"""PROTOTYPE ONLY: deterministic chapter and growth-ritual timeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
import json
from typing import Literal
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo


AcademicStage = Literal[
    "freshman",
    "sophomore",
    "junior",
    "senior",
    "unknown",
]
AcademicStatus = Literal["active", "leave", "graduated", "unknown"]
TransitionKind = Literal[
    "initialized",
    "advanced",
    "skipped_forward",
    "repeated",
    "real_regression",
    "correction",
    "leave",
    "resume_same",
    "resume_advanced",
    "graduated",
    "migration",
    "cleared",
]
MomentKind = Literal[
    "academic_growth",
    "academic_reorientation",
    "anniversary",
    "graduation",
]
MomentStatus = Literal[
    "pending",
    "reserved",
    "expressed",
    "suppressed",
    "expired",
    "invalidated",
]
RelationshipPosture = Literal["steady", "reunion_cautious", "repairing"]

LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
STAGE_AGE: dict[AcademicStage, int | None] = {
    "freshman": 1,
    "sophomore": 2,
    "junior": 3,
    "senior": 4,
    "unknown": None,
}
STAGE_ORDER: tuple[AcademicStage, ...] = (
    "freshman",
    "sophomore",
    "junior",
    "senior",
)
MOMENT_PRIORITY: dict[MomentKind, int] = {
    "graduation": 4,
    "academic_growth": 3,
    "academic_reorientation": 2,
    "anniversary": 1,
}


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must include a timezone offset")
    return parsed


def _local_date(value: str | datetime) -> date:
    parsed = value if isinstance(value, datetime) else _aware(value)
    return parsed.astimezone(LOCAL_TIMEZONE).date()


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


@dataclass(frozen=True)
class NarrativeConfig:
    version: str = "companion-narrative-v1-candidate"
    minimum_chapter_evidence: int = 2
    minimum_shared_evidence: int = 1
    minimum_chapter_dates: int = 2
    chapter_evidence_limit: int = 3
    academic_expression_window_days: int = 30
    anniversary_expression_window_days: int = 14
    graduation_expression_window_days: int = 90
    coalesce_window_days: int = 30
    reservation_lease_seconds: int = 300

    def expression_window_days(self, kind: MomentKind) -> int:
        if kind == "anniversary":
            return self.anniversary_expression_window_days
        if kind == "graduation":
            return self.graduation_expression_window_days
        return self.academic_expression_window_days


DEFAULT_CONFIG = NarrativeConfig()


@dataclass(frozen=True)
class NarrativeEvidence:
    event_id: str
    evidence_id: str
    occurred_at: str
    academic_stage: AcademicStage
    ownership_scope: Literal["user_fact", "shared_experience"]
    safe_summary: str
    relationship_epoch_id: str = "epoch-1"

    def __post_init__(self) -> None:
        for value in (self.event_id, self.evidence_id, self.safe_summary):
            if not value.strip():
                raise ValueError("Evidence text fields cannot be blank")
        _aware(self.occurred_at)
        if self.academic_stage not in STAGE_AGE:
            raise ValueError("unsupported academic stage")


@dataclass(frozen=True)
class AcademicTransition:
    event_id: str
    occurred_at: str
    source_revision: int
    transition_kind: TransitionKind
    from_stage: AcademicStage
    to_stage: AcademicStage
    to_status: AcademicStatus = "active"

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id is required")
        _aware(self.occurred_at)
        if self.source_revision < 1:
            raise ValueError("source_revision must be positive")
        if self.from_stage not in STAGE_AGE or self.to_stage not in STAGE_AGE:
            raise ValueError("unsupported academic stage")


@dataclass(frozen=True)
class AnniversaryBoundary:
    event_id: str
    occurred_at: str
    anniversary_number: int

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id is required")
        _aware(self.occurred_at)
        if self.anniversary_number < 1:
            raise ValueError("anniversary_number must be positive")


@dataclass
class EvidenceRecord:
    evidence_id: str
    occurred_at: str
    academic_stage: AcademicStage
    ownership_scope: str
    safe_summary: str
    relationship_epoch_id: str
    status: str = "active"


@dataclass
class BoundaryRecord:
    boundary_id: str
    kind: str
    occurred_at: str
    relationship_epoch_id: str
    status: str = "valid"
    from_stage: AcademicStage = "unknown"
    to_stage: AcademicStage = "unknown"
    xiaoxin_age: int | None = None
    source_revision: int | None = None
    anniversary_number: int | None = None
    moment_kind: MomentKind | None = None
    reason_codes: list[str] = field(default_factory=list)


@dataclass
class ChapterRecord:
    chapter_id: str
    relationship_epoch_id: str
    academic_stage: AcademicStage
    period_start: str
    period_end: str
    evidence_ids: list[str]
    safe_evidence_summaries: list[str]
    boundary_id: str
    version: int
    status: str = "closed"
    reason_codes: list[str] = field(default_factory=list)


@dataclass
class MomentRecord:
    moment_id: str
    primary_kind: MomentKind
    boundary_ids: list[str]
    relationship_epoch_id: str
    occurred_at: str
    expires_at: str
    mode: str
    chapter_ids: list[str]
    evidence_ids: list[str]
    status: MomentStatus
    reserved_by_turn_id: str | None = None
    lease_until: str | None = None
    expressed_at: str | None = None
    reason_codes: list[str] = field(default_factory=list)


class CompanionNarrativeTimeline:
    """In-memory narrative boundaries, chapter views, and one-shot delivery."""

    def __init__(
        self,
        *,
        config: NarrativeConfig = DEFAULT_CONFIG,
        pet_id: str = "pet-1",
        memory_subject_id: str = "subject-1",
        relationship_epoch_id: str = "epoch-1",
    ) -> None:
        self.config = config
        self.pet_id = pet_id
        self.memory_subject_id = memory_subject_id
        self.relationship_epoch_id = relationship_epoch_id
        self.current_stage: AcademicStage = "unknown"
        self.academic_status: AcademicStatus = "unknown"
        self.source_revision = 0
        self.growth_reflections_enabled = True
        self.posture: RelationshipPosture = "steady"
        self._stage_period_start: str | None = None
        self._latest_academic_boundary_id: str | None = None
        self._processed_event_ids: set[str] = set()
        self._anniversary_numbers: set[int] = set()
        self._last_event_at: datetime | None = None
        self._last_expression_at: datetime | None = None
        self.evidence: dict[str, EvidenceRecord] = {}
        self.boundaries: list[BoundaryRecord] = []
        self.chapters: list[ChapterRecord] = []
        self.moments: list[MomentRecord] = []
        self.trace: list[dict[str, object]] = []

    def observe_evidence(self, item: NarrativeEvidence) -> None:
        if not self._accept_event(item.event_id, item.occurred_at):
            return
        if item.evidence_id in self.evidence:
            raise ValueError("evidence_id must be globally idempotent")
        self.evidence[item.evidence_id] = EvidenceRecord(
            evidence_id=item.evidence_id,
            occurred_at=_aware(item.occurred_at).isoformat(),
            academic_stage=item.academic_stage,
            ownership_scope=item.ownership_scope,
            safe_summary=item.safe_summary,
            relationship_epoch_id=item.relationship_epoch_id,
        )
        self._trace(item.occurred_at, "evidence_observed", item.evidence_id)

    def observe_academic_transition(self, item: AcademicTransition) -> None:
        if not self._accept_event(item.event_id, item.occurred_at):
            return
        if item.source_revision <= self.source_revision:
            self._trace(
                item.occurred_at,
                "stale_revision_ignored",
                f"revision={item.source_revision}",
            )
            return
        if item.transition_kind != "initialized" and item.from_stage != self.current_stage:
            raise ValueError("transition from_stage does not match current stage")
        self.source_revision = item.source_revision

        if item.transition_kind == "initialized":
            self.current_stage = item.to_stage
            self.academic_status = item.to_status
            self._stage_period_start = _aware(item.occurred_at).isoformat()
            self._trace(item.occurred_at, "academic_initialized", item.to_stage)
            return

        if item.transition_kind in {"repeated", "resume_same", "leave", "migration"}:
            if item.to_stage != self.current_stage:
                raise ValueError("non-stage transition cannot change academic stage")
            self.academic_status = item.to_status
            self._trace(item.occurred_at, "academic_state_updated", item.transition_kind)
            return

        if item.transition_kind == "cleared":
            self.current_stage = "unknown"
            self.academic_status = item.to_status
            self._stage_period_start = None
            self._trace(item.occurred_at, "academic_stage_cleared", "unknown")
            return

        if item.transition_kind == "correction":
            if self._latest_academic_boundary_id is not None:
                self._invalidate_boundary(
                    self._latest_academic_boundary_id,
                    reason="source_profile_corrected",
                    now=item.occurred_at,
                )
            self.current_stage = item.to_stage
            self.academic_status = item.to_status
            self._stage_period_start = _aware(item.occurred_at).isoformat()
            self._trace(item.occurred_at, "academic_profile_corrected", item.to_stage)
            return

        if item.transition_kind == "graduated":
            if item.to_stage != self.current_stage:
                raise ValueError("graduation cannot synthesize a new academic stage")
            boundary = self._add_boundary(
                event_id=item.event_id,
                kind="graduation",
                occurred_at=item.occurred_at,
                from_stage=item.from_stage,
                to_stage=item.to_stage,
                source_revision=item.source_revision,
            )
            chapter = self._close_chapter(boundary)
            self.academic_status = "graduated"
            self._latest_academic_boundary_id = boundary.boundary_id
            self._create_or_coalesce_moment(
                boundary=boundary,
                kind="graduation",
                chapter=chapter,
            )
            self._trace(item.occurred_at, "graduation_boundary_created", boundary.boundary_id)
            return

        if item.transition_kind not in {
            "advanced",
            "skipped_forward",
            "resume_advanced",
            "real_regression",
        }:
            raise ValueError("unsupported transition kind")
        self._validate_stage_direction(item)
        boundary = self._add_boundary(
            event_id=item.event_id,
            kind="academic_transition",
            occurred_at=item.occurred_at,
            from_stage=item.from_stage,
            to_stage=item.to_stage,
            source_revision=item.source_revision,
        )
        chapter = self._close_chapter(boundary)
        self.current_stage = item.to_stage
        self.academic_status = item.to_status
        self._stage_period_start = _aware(item.occurred_at).isoformat()
        self._latest_academic_boundary_id = boundary.boundary_id
        kind: MomentKind = (
            "academic_reorientation"
            if item.transition_kind == "real_regression"
            else "academic_growth"
        )
        self._create_or_coalesce_moment(
            boundary=boundary,
            kind=kind,
            chapter=None if kind == "academic_reorientation" else chapter,
        )
        self._trace(item.occurred_at, "academic_boundary_created", boundary.boundary_id)

    def observe_anniversary(self, item: AnniversaryBoundary) -> None:
        if not self._accept_event(item.event_id, item.occurred_at):
            return
        if item.anniversary_number in self._anniversary_numbers:
            self._trace(
                item.occurred_at,
                "duplicate_anniversary_ignored",
                str(item.anniversary_number),
            )
            return
        self._anniversary_numbers.add(item.anniversary_number)
        boundary = self._add_boundary(
            event_id=f"anniversary-{item.anniversary_number}",
            kind="anniversary",
            occurred_at=item.occurred_at,
            from_stage=self.current_stage,
            to_stage=self.current_stage,
            anniversary_number=item.anniversary_number,
        )
        chapter = self._close_chapter(boundary)
        if chapter is None:
            boundary.reason_codes.append("insufficient_shared_evidence")
            self._trace(
                item.occurred_at,
                "anniversary_recorded_without_ritual",
                boundary.boundary_id,
            )
            return
        self._create_or_coalesce_moment(
            boundary=boundary,
            kind="anniversary",
            chapter=chapter,
        )
        self._trace(item.occurred_at, "anniversary_boundary_created", boundary.boundary_id)

    def set_growth_reflections_enabled(self, enabled: bool, *, now: str) -> None:
        current = self._touch(now)
        self.growth_reflections_enabled = enabled
        if not enabled:
            for moment in self.moments:
                if moment.status in {"pending", "reserved"}:
                    moment.status = "suppressed"
                    moment.reserved_by_turn_id = None
                    moment.lease_until = None
                    moment.reason_codes.append("user_disabled_growth_reflections")
        self._trace(current.isoformat(), "growth_reflections_changed", str(enabled))

    def set_posture(self, posture: RelationshipPosture, *, now: str) -> None:
        if posture not in {"steady", "reunion_cautious", "repairing"}:
            raise ValueError("unsupported relationship posture")
        current = self._touch(now)
        self.posture = posture
        self._trace(current.isoformat(), "relationship_posture_changed", posture)

    def claim_moment(
        self,
        *,
        turn_id: str,
        now: str,
        context: str = "ordinary_conversation",
    ) -> dict[str, object] | None:
        if not turn_id.strip():
            raise ValueError("turn_id is required")
        current = self._touch(now)
        self._refresh_moments(current)
        if (
            not self.growth_reflections_enabled
            or self.posture != "steady"
            or context != "ordinary_conversation"
        ):
            self._trace(current.isoformat(), "moment_claim_blocked", self.posture)
            return None
        candidates = [item for item in self.moments if item.status == "pending"]
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: (
                -MOMENT_PRIORITY[item.primary_kind],
                item.occurred_at,
                item.moment_id,
            )
        )
        moment = candidates[0]
        moment.status = "reserved"
        moment.reserved_by_turn_id = turn_id
        moment.lease_until = (
            current + timedelta(seconds=self.config.reservation_lease_seconds)
        ).isoformat()
        self._trace(current.isoformat(), "moment_reserved", moment.moment_id)
        return self._moment_projection(moment)

    def finish_moment(
        self,
        *,
        turn_id: str,
        now: str,
        delivery_status: str,
    ) -> str | None:
        current = self._touch(now)
        self._refresh_moments(current)
        moment = next(
            (
                item
                for item in self.moments
                if item.status == "reserved"
                and item.reserved_by_turn_id == turn_id
            ),
            None,
        )
        if moment is None:
            return None
        if delivery_status in {"generated", "delivered"}:
            moment.status = "expressed"
            moment.expressed_at = current.isoformat()
            moment.lease_until = None
            self._last_expression_at = current
            self._trace(current.isoformat(), "moment_expressed", moment.moment_id)
        else:
            moment.status = "pending"
            moment.reserved_by_turn_id = None
            moment.lease_until = None
            self._trace(current.isoformat(), "moment_released_for_retry", moment.moment_id)
        return moment.moment_id

    def forget_evidence(self, evidence_id: str, *, now: str) -> None:
        current = self._touch(now)
        item = self.evidence.get(evidence_id)
        if item is None:
            return
        item.status = "forgotten"
        affected_chapters = [
            chapter
            for chapter in self.chapters
            if evidence_id in chapter.evidence_ids and chapter.status != "invalidated"
        ]
        replacements: dict[str, ChapterRecord] = {}
        for chapter in affected_chapters:
            chapter.status = "invalidated"
            chapter.reason_codes.append("cited_evidence_forgotten")
            remaining_records = [
                self.evidence[candidate]
                for candidate in chapter.evidence_ids
                if self.evidence[candidate].status == "active"
            ]
            if self._records_are_chapter_qualified(remaining_records):
                replacements[chapter.chapter_id] = self._rebuild_chapter(
                    chapter,
                    remaining_records,
                    now=current.isoformat(),
                )
        affected_chapter_ids = {item.chapter_id for item in affected_chapters}
        for moment in self.moments:
            if evidence_id not in moment.evidence_ids:
                continue
            if moment.status == "reserved":
                moment.status = "pending"
                moment.reserved_by_turn_id = None
                moment.lease_until = None
                moment.reason_codes.append("reservation_released_after_forgetting")
            next_chapter_ids: list[str] = []
            for chapter_id in moment.chapter_ids:
                if chapter_id in replacements:
                    next_chapter_ids.append(replacements[chapter_id].chapter_id)
                elif chapter_id not in affected_chapter_ids:
                    next_chapter_ids.append(chapter_id)
            moment.chapter_ids = _ordered_unique(next_chapter_ids)
            moment.evidence_ids = _ordered_unique(
                [
                    candidate
                    for chapter_id in moment.chapter_ids
                    for candidate in self._chapter_by_id(chapter_id).evidence_ids
                    if self.evidence[candidate].status == "active"
                ]
            )
            if self._evidence_ids_are_chapter_qualified(moment.evidence_ids):
                moment.mode = "evidence_backed"
                moment.reason_codes.append("evidence_reduced_after_forgetting")
            elif moment.primary_kind == "anniversary":
                moment.status = "invalidated"
                moment.reserved_by_turn_id = None
                moment.lease_until = None
                moment.reason_codes.append("anniversary_evidence_invalidated")
            else:
                moment.mode = "boundary_only"
                moment.evidence_ids = []
                moment.chapter_ids = []
                moment.reason_codes.append("shared_narrative_removed")
        self._trace(current.isoformat(), "evidence_forgotten", evidence_id)

    def reset_relationship(self, *, new_epoch_id: str, now: str) -> None:
        if not new_epoch_id.strip() or new_epoch_id == self.relationship_epoch_id:
            raise ValueError("new relationship epoch is required")
        current = self._touch(now)
        old_epoch = self.relationship_epoch_id
        for chapter in self.chapters:
            if chapter.relationship_epoch_id == old_epoch:
                chapter.status = "invalidated"
                chapter.reason_codes.append("relationship_reset")
        for moment in self.moments:
            if moment.relationship_epoch_id == old_epoch:
                moment.status = "invalidated"
                moment.reserved_by_turn_id = None
                moment.lease_until = None
                moment.reason_codes.append("relationship_reset")
        self.relationship_epoch_id = new_epoch_id
        self.posture = "steady"
        self._stage_period_start = current.isoformat()
        self._trace(current.isoformat(), "relationship_reset", new_epoch_id)

    def state(self, now: str) -> dict[str, object]:
        current = _aware(now)
        self._refresh_moments(current)
        return {
            "now": current.isoformat(),
            "config_version": self.config.version,
            "pet_id": self.pet_id,
            "memory_subject_id": self.memory_subject_id,
            "relationship_epoch_id": self.relationship_epoch_id,
            "academic_stage": self.current_stage,
            "academic_status": self.academic_status,
            "xiaoxin_age": STAGE_AGE[self.current_stage],
            "source_revision": self.source_revision,
            "growth_reflections_enabled": self.growth_reflections_enabled,
            "posture": self.posture,
            "evidence": [
                asdict(item)
                for item in sorted(self.evidence.values(), key=lambda value: value.evidence_id)
            ],
            "boundaries": [asdict(item) for item in self.boundaries],
            "chapters": [asdict(item) for item in self.chapters],
            "moments": [
                {
                    **asdict(item),
                    "projection": self._moment_projection(item),
                }
                for item in self.moments
            ],
            "miniprogram_history": [
                self._moment_projection(item)["miniprogram"]
                for item in self.moments
                if self._moment_projection(item)["miniprogram"]["visible"]
            ],
            "trace": list(self.trace),
        }

    def canonical_json(self, now: str) -> str:
        return json.dumps(
            self.state(now),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _validate_stage_direction(self, item: AcademicTransition) -> None:
        if item.from_stage not in STAGE_ORDER or item.to_stage not in STAGE_ORDER:
            raise ValueError("stage movement requires known academic stages")
        source_index = STAGE_ORDER.index(item.from_stage)
        target_index = STAGE_ORDER.index(item.to_stage)
        if item.transition_kind == "real_regression" and target_index >= source_index:
            raise ValueError("real_regression must move to an earlier stage")
        if item.transition_kind != "real_regression" and target_index <= source_index:
            raise ValueError("forward transition must move to a later stage")

    def _add_boundary(
        self,
        *,
        event_id: str,
        kind: str,
        occurred_at: str,
        from_stage: AcademicStage,
        to_stage: AcademicStage,
        source_revision: int | None = None,
        anniversary_number: int | None = None,
    ) -> BoundaryRecord:
        boundary_id = str(
            uuid5(
                NAMESPACE_URL,
                f"xiaoxin:narrative-boundary:{self.pet_id}:"
                f"{self.memory_subject_id}:{event_id}",
            )
        )
        boundary = BoundaryRecord(
            boundary_id=boundary_id,
            kind=kind,
            occurred_at=_aware(occurred_at).isoformat(),
            relationship_epoch_id=self.relationship_epoch_id,
            from_stage=from_stage,
            to_stage=to_stage,
            xiaoxin_age=STAGE_AGE[to_stage],
            source_revision=source_revision,
            anniversary_number=anniversary_number,
        )
        self.boundaries.append(boundary)
        return boundary

    def _close_chapter(self, boundary: BoundaryRecord) -> ChapterRecord | None:
        if self.current_stage == "unknown" or self._stage_period_start is None:
            return None
        period_start = _aware(self._stage_period_start)
        period_end = _aware(boundary.occurred_at)
        used_ids = {
            evidence_id
            for chapter in self.chapters
            if chapter.status != "invalidated"
            for evidence_id in chapter.evidence_ids
        }
        candidates = [
            item
            for item in self.evidence.values()
            if item.status == "active"
            and item.relationship_epoch_id == self.relationship_epoch_id
            and item.academic_stage == self.current_stage
            and item.evidence_id not in used_ids
            and period_start <= _aware(item.occurred_at) <= period_end
        ]
        if not self._records_are_chapter_qualified(candidates):
            return None
        selected = self._select_chapter_evidence(candidates)
        version = 1 + max(
            (
                chapter.version
                for chapter in self.chapters
                if chapter.relationship_epoch_id == self.relationship_epoch_id
                and chapter.academic_stage == self.current_stage
            ),
            default=0,
        )
        chapter_id = str(
            uuid5(
                NAMESPACE_URL,
                f"xiaoxin:narrative-chapter:{self.pet_id}:"
                f"{self.memory_subject_id}:{self.relationship_epoch_id}:"
                f"{boundary.boundary_id}:{version}",
            )
        )
        chapter = ChapterRecord(
            chapter_id=chapter_id,
            relationship_epoch_id=self.relationship_epoch_id,
            academic_stage=self.current_stage,
            period_start=period_start.isoformat(),
            period_end=period_end.isoformat(),
            evidence_ids=[item.evidence_id for item in selected],
            safe_evidence_summaries=[item.safe_summary for item in selected],
            boundary_id=boundary.boundary_id,
            version=version,
        )
        self.chapters.append(chapter)
        self._stage_period_start = period_end.isoformat()
        self._trace(period_end.isoformat(), "chapter_closed", chapter.chapter_id)
        return chapter

    def _select_chapter_evidence(
        self,
        candidates: list[EvidenceRecord],
    ) -> list[EvidenceRecord]:
        ordered = sorted(
            candidates,
            key=lambda item: (_aware(item.occurred_at), item.evidence_id),
        )
        selected = ordered[-self.config.chapter_evidence_limit :]
        if not any(item.ownership_scope == "shared_experience" for item in selected):
            shared = next(
                item
                for item in reversed(ordered)
                if item.ownership_scope == "shared_experience"
            )
            selected[0] = shared
            selected = sorted(
                {item.evidence_id: item for item in selected}.values(),
                key=lambda item: (_aware(item.occurred_at), item.evidence_id),
            )
        return selected

    def _records_are_chapter_qualified(
        self,
        records: list[EvidenceRecord],
    ) -> bool:
        return (
            len(records) >= self.config.minimum_chapter_evidence
            and sum(item.ownership_scope == "shared_experience" for item in records)
            >= self.config.minimum_shared_evidence
            and len({_local_date(item.occurred_at) for item in records})
            >= self.config.minimum_chapter_dates
        )

    def _evidence_ids_are_chapter_qualified(self, evidence_ids: list[str]) -> bool:
        records = [
            self.evidence[evidence_id]
            for evidence_id in evidence_ids
            if self.evidence[evidence_id].status == "active"
        ]
        return self._records_are_chapter_qualified(records)

    def _create_or_coalesce_moment(
        self,
        *,
        boundary: BoundaryRecord,
        kind: MomentKind,
        chapter: ChapterRecord | None,
    ) -> MomentRecord:
        occurred_at = _aware(boundary.occurred_at)
        self._refresh_moments(occurred_at)
        boundary.moment_kind = kind
        evidence_ids = list(chapter.evidence_ids) if chapter is not None else []
        chapter_ids = [chapter.chapter_id] if chapter is not None else []
        for existing in reversed(self.moments):
            if (
                existing.status == "pending"
                and existing.relationship_epoch_id == self.relationship_epoch_id
                and "anniversary" in {existing.primary_kind, kind}
                and abs((_local_date(occurred_at) - _local_date(existing.occurred_at)).days)
                <= self.config.coalesce_window_days
            ):
                existing.boundary_ids = _ordered_unique(
                    existing.boundary_ids + [boundary.boundary_id]
                )
                existing.chapter_ids = _ordered_unique(existing.chapter_ids + chapter_ids)
                existing.evidence_ids = _ordered_unique(
                    existing.evidence_ids + evidence_ids
                )[: self.config.chapter_evidence_limit]
                if evidence_ids:
                    existing.mode = "evidence_backed"
                if MOMENT_PRIORITY[kind] > MOMENT_PRIORITY[existing.primary_kind]:
                    existing.primary_kind = kind
                    existing.occurred_at = occurred_at.isoformat()
                    existing.expires_at = (
                        occurred_at
                        + timedelta(days=self.config.expression_window_days(kind))
                    ).isoformat()
                existing.reason_codes.append("nearby_boundary_coalesced")
                self._trace(
                    boundary.occurred_at,
                    "boundary_coalesced",
                    existing.moment_id,
                )
                return existing

        status: MomentStatus = "pending"
        reasons: list[str] = []
        if not self.growth_reflections_enabled:
            status = "suppressed"
            reasons.append("user_disabled_growth_reflections")
        elif (
            kind == "anniversary"
            and self._last_expression_at is not None
            and 0
            <= (_local_date(occurred_at) - _local_date(self._last_expression_at)).days
            <= self.config.coalesce_window_days
        ):
            status = "suppressed"
            reasons.append("recent_growth_expression")
        moment_id = str(
            uuid5(
                NAMESPACE_URL,
                f"xiaoxin:narrative-moment:{self.pet_id}:"
                f"{self.memory_subject_id}:{self.relationship_epoch_id}:"
                f"{boundary.boundary_id}",
            )
        )
        moment = MomentRecord(
            moment_id=moment_id,
            primary_kind=kind,
            boundary_ids=[boundary.boundary_id],
            relationship_epoch_id=self.relationship_epoch_id,
            occurred_at=occurred_at.isoformat(),
            expires_at=(
                occurred_at + timedelta(days=self.config.expression_window_days(kind))
            ).isoformat(),
            mode="evidence_backed" if evidence_ids else "boundary_only",
            chapter_ids=chapter_ids,
            evidence_ids=evidence_ids,
            status=status,
            reason_codes=reasons,
        )
        self.moments.append(moment)
        return moment

    def _rebuild_chapter(
        self,
        original: ChapterRecord,
        remaining_records: list[EvidenceRecord],
        *,
        now: str,
    ) -> ChapterRecord:
        selected = self._select_chapter_evidence(remaining_records)
        version = 1 + max(
            (
                chapter.version
                for chapter in self.chapters
                if chapter.relationship_epoch_id == original.relationship_epoch_id
                and chapter.academic_stage == original.academic_stage
            ),
            default=0,
        )
        chapter_id = str(
            uuid5(
                NAMESPACE_URL,
                f"xiaoxin:narrative-chapter-recomputed:{self.pet_id}:"
                f"{self.memory_subject_id}:{original.relationship_epoch_id}:"
                f"{original.boundary_id}:{version}",
            )
        )
        replacement = ChapterRecord(
            chapter_id=chapter_id,
            relationship_epoch_id=original.relationship_epoch_id,
            academic_stage=original.academic_stage,
            period_start=original.period_start,
            period_end=original.period_end,
            evidence_ids=[item.evidence_id for item in selected],
            safe_evidence_summaries=[item.safe_summary for item in selected],
            boundary_id=original.boundary_id,
            version=version,
            reason_codes=["recomputed_after_forgetting"],
        )
        self.chapters.append(replacement)
        self._trace(now, "chapter_recomputed", replacement.chapter_id)
        return replacement

    def _chapter_by_id(self, chapter_id: str) -> ChapterRecord:
        return next(item for item in self.chapters if item.chapter_id == chapter_id)

    def _boundary_by_id(self, boundary_id: str) -> BoundaryRecord:
        return next(item for item in self.boundaries if item.boundary_id == boundary_id)

    def _moment_projection(self, moment: MomentRecord) -> dict[str, object]:
        active_evidence = [
            self.evidence[evidence_id]
            for evidence_id in moment.evidence_ids
            if evidence_id in self.evidence
            and self.evidence[evidence_id].status == "active"
        ]
        shared = [
            item for item in active_evidence if item.ownership_scope == "shared_experience"
        ]
        boundary_facts = []
        for boundary_id in moment.boundary_ids:
            boundary = next(
                item for item in self.boundaries if item.boundary_id == boundary_id
            )
            boundary_facts.append(
                {
                    "kind": boundary.kind,
                    "from_stage": boundary.from_stage,
                    "to_stage": boundary.to_stage,
                    "xiaoxin_age": boundary.xiaoxin_age,
                    "anniversary_number": boundary.anniversary_number,
                    "occurred_at": boundary.occurred_at,
                }
            )
        user_visible = (
            self.growth_reflections_enabled
            and moment.status in {"pending", "reserved", "expressed"}
        )
        hardware_semantic = {
            "academic_growth": "growth_acknowledgement",
            "academic_reorientation": None,
            "anniversary": "anniversary_acknowledgement",
            "graduation": "graduation_acknowledgement",
        }[moment.primary_kind]
        return {
            "moment_id": moment.moment_id,
            "status": moment.status,
            "primary_kind": moment.primary_kind,
            "mode": moment.mode,
            "voice": {
                "enabled": user_visible,
                "max_sentences": 2 if moment.mode == "evidence_backed" else 1,
                "shared_anchor_budget": 1 if shared else 0,
                "safe_anchor": shared[-1].safe_summary if shared else None,
                "tone": (
                    "neutral"
                    if moment.primary_kind == "academic_reorientation"
                    else "warm_restrained"
                ),
                "initiative_allowed": False,
            },
            "miniprogram": {
                "visible": user_visible,
                "boundary_facts": boundary_facts,
                "safe_evidence_summaries": [
                    item.safe_summary for item in active_evidence
                ],
                "show_internal_counts": False,
            },
            "hardware": {
                "enabled": user_visible and hardware_semantic is not None,
                "semantic": hardware_semantic,
                "intensity": "low" if hardware_semantic is not None else None,
                "duration": "brief" if hardware_semantic is not None else None,
            },
        }

    def _invalidate_boundary(self, boundary_id: str, *, reason: str, now: str) -> None:
        boundary = next(
            (item for item in self.boundaries if item.boundary_id == boundary_id),
            None,
        )
        if boundary is None:
            return
        boundary.status = "invalidated"
        boundary.reason_codes.append(reason)
        for chapter in self.chapters:
            if chapter.boundary_id == boundary_id:
                chapter.status = "invalidated"
                chapter.reason_codes.append(reason)
        for moment in self.moments:
            if boundary_id in moment.boundary_ids:
                if moment.status == "reserved":
                    moment.status = "pending"
                    moment.reserved_by_turn_id = None
                    moment.lease_until = None
                    moment.reason_codes.append("reservation_released_after_correction")
                moment.boundary_ids = [
                    candidate
                    for candidate in moment.boundary_ids
                    if candidate != boundary_id
                ]
                moment.chapter_ids = [
                    chapter_id
                    for chapter_id in moment.chapter_ids
                    if self._chapter_by_id(chapter_id).boundary_id != boundary_id
                ]
                moment.evidence_ids = _ordered_unique(
                    [
                        evidence_id
                        for chapter_id in moment.chapter_ids
                        for evidence_id in self._chapter_by_id(chapter_id).evidence_ids
                        if self.evidence[evidence_id].status == "active"
                    ]
                )
                valid_boundaries = [
                    self._boundary_by_id(candidate)
                    for candidate in moment.boundary_ids
                    if self._boundary_by_id(candidate).status == "valid"
                    and self._boundary_by_id(candidate).moment_kind is not None
                ]
                if not valid_boundaries:
                    moment.status = "invalidated"
                    moment.reserved_by_turn_id = None
                    moment.lease_until = None
                else:
                    primary = max(
                        valid_boundaries,
                        key=lambda item: MOMENT_PRIORITY[item.moment_kind],
                    )
                    moment.primary_kind = primary.moment_kind
                    moment.occurred_at = primary.occurred_at
                    moment.expires_at = (
                        _aware(primary.occurred_at)
                        + timedelta(
                            days=self.config.expression_window_days(
                                moment.primary_kind
                            )
                        )
                    ).isoformat()
                    if self._evidence_ids_are_chapter_qualified(moment.evidence_ids):
                        moment.mode = "evidence_backed"
                    elif moment.primary_kind == "anniversary":
                        moment.status = "invalidated"
                        moment.reserved_by_turn_id = None
                        moment.lease_until = None
                    else:
                        moment.mode = "boundary_only"
                        moment.chapter_ids = []
                        moment.evidence_ids = []
                    moment.reason_codes.append("coalesced_boundary_removed")
                moment.reason_codes.append(reason)
        self._trace(now, "boundary_invalidated", boundary_id)

    def _refresh_moments(self, now: datetime) -> None:
        for moment in self.moments:
            if (
                moment.status == "reserved"
                and moment.lease_until is not None
                and _aware(moment.lease_until) <= now
            ):
                moment.status = "pending"
                moment.reserved_by_turn_id = None
                moment.lease_until = None
                moment.reason_codes.append("reservation_lease_expired")
            if moment.status in {"pending", "reserved"} and _aware(moment.expires_at) < now:
                moment.status = "expired"
                moment.reserved_by_turn_id = None
                moment.lease_until = None
                moment.reason_codes.append("expression_window_expired")

    def _accept_event(self, event_id: str, occurred_at: str) -> bool:
        if event_id in self._processed_event_ids:
            self._trace(occurred_at, "duplicate_event_ignored", event_id)
            return False
        if not event_id.strip():
            raise ValueError("event_id is required")
        current = _aware(occurred_at)
        if self._last_event_at is not None and current < self._last_event_at:
            raise ValueError("prototype events must be replayed chronologically")
        self._processed_event_ids.add(event_id)
        self._last_event_at = current
        return True

    def _touch(self, now: str) -> datetime:
        current = _aware(now)
        if self._last_event_at is not None and current < self._last_event_at:
            raise ValueError("prototype actions must be replayed chronologically")
        self._last_event_at = current
        return current

    def _trace(self, occurred_at: str, action: str, target: str) -> None:
        self.trace.append(
            {
                "sequence": len(self.trace) + 1,
                "occurred_at": _aware(occurred_at).isoformat(),
                "action": action,
                "target": target,
            }
        )
