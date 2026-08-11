from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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
    coarse_intent: str = ""
    fine_intent: str = ""
    intent_confidence: float = 0.0
    guard_result: str = "not_evaluated"
    llm_invoked: bool = False
    llm_model: str = ""
    llm_prompt_version: str = ""
    llm_result: str = "not_called"
    llm_response_summary: str = "{}"
