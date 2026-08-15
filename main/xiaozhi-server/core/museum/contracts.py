from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Mapping


@dataclass(frozen=True)
class ExhibitResolution:
    status: str
    exhibit_id: str | None = None
    exhibit_name: str | None = None
    matched_text: str | None = None
    candidate_ids: tuple[str, ...] = ()
    context_source: str = "missing"

    def __post_init__(self) -> None:
        valid_statuses = {
            "explicit",
            "inherited",
            "ambiguous",
            "missing",
            "not_found",
        }
        if self.status not in valid_statuses:
            raise ValueError(f"unsupported exhibit resolution status: {self.status}")
        if self.status in {"explicit", "inherited"} and not self.exhibit_id:
            raise ValueError(f"{self.status} resolution requires an exhibit_id")


@dataclass(frozen=True)
class ExhibitContext:
    museum_id: str
    museum_name: str
    zone_id: str
    zone_name: str
    exhibit_id: str
    exhibit_name: str
    context_source: str


@dataclass(frozen=True)
class VisitorSession:
    id: str
    device_id: str
    current_exhibit_id: str
    visitor_mode: str
    started_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class SourceDocumentRecord:
    """Immutable metadata for a source file or captured web document."""

    id: str
    museum_id: str
    title: str
    source_type: str
    locator: str
    rights_note: str
    publisher: str = ""
    published_date: str = ""
    accessed_at: str = ""
    language: str = "zh-CN"
    checksum: str = ""
    source_level: str = "demo_curated"
    rights_status: str = "demo_authorized"
    original_path: str = ""
    parser_version: str = ""
    version_id: str = ""
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceSegmentRecord:
    """A locatable, versioned text segment derived from a source document."""

    id: str
    source_id: str
    text: str
    locator: str
    exhibit_ids: tuple[str, ...] = ()
    section: str = ""
    page: int | None = None
    ordinal: int = 0
    content_hash: str = ""
    parser_version: str = ""
    source_version_id: str = ""
    ocr_confidence: float | None = None
    status: str = "published"
    content_version: int = 1


@dataclass(frozen=True)
class IngestionReport:
    run_id: str
    source_ids: tuple[str, ...]
    segment_ids: tuple[str, ...]
    skipped_source_ids: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    kind: str
    text: str
    source_id: str
    segment_id: str = ""
    fact_id: str = ""
    source_title: str = ""
    locator: str = ""
    score: float = 0.0
    rank: int = 0
    source_level: str = ""
    content_version: int = 0
    exhibit_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceClaim:
    id: str
    exhibit_id: str
    fact_type: str
    statement: str
    source_ids: tuple[str, ...] = ()
    certainty: str = "confirmed"
    supporting_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidencePack:
    query_id: str
    exhibit_ids: tuple[str, ...]
    items: tuple[EvidenceItem, ...]
    claims: tuple[EvidenceClaim, ...] = ()
    index_version: str = ""
    retrieval_trace: Mapping[str, object] = field(default_factory=dict)
    conflict_groups: tuple[tuple[str, ...], ...] = ()

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                item.source_id
                for item in self.items
                if item.source_id
            )
        )

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.items)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceFact:
    id: str
    fact_type: str
    statement: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceSnapshot:
    exhibit_id: str
    content_revision_id: str
    content_version: int
    facts: tuple[EvidenceFact, ...]

    @property
    def fact_ids(self) -> tuple[str, ...]:
        return tuple(fact.id for fact in self.facts)

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                source_id
                for fact in self.facts
                for source_id in fact.source_ids
            )
        )


@dataclass(frozen=True)
class AnswerClaim:
    text: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnswerResult:
    knowledge_status: str
    spoken_text: str
    evidence: EvidenceSnapshot | None
    retrieval_ms: int
    composition_ms: int
    coarse_intent: str = ""
    fine_intent: str = ""
    intent_confidence: float = 0.0
    guard_result: str = "not_evaluated"
    llm_invoked: bool = False
    llm_model: str = ""
    llm_prompt_version: str = ""
    llm_result: str = "not_called"
    llm_response_summary: str = "{}"
    llm_ms: int = 0
    retrieval_trace: dict[str, object] = field(default_factory=dict)
    evidence_pack: EvidencePack | None = None
    cited_evidence_ids: tuple[str, ...] = ()
    answer_claims: tuple[AnswerClaim, ...] = ()
