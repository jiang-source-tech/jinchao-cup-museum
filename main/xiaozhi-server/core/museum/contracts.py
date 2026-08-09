from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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
class AnswerResult:
    knowledge_status: str
    spoken_text: str
    evidence: EvidenceSnapshot | None
    retrieval_ms: int
    composition_ms: int
