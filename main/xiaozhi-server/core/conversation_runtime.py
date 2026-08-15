from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class TurnRequest:
    request_id: str
    transport_session_id: str
    visitor_session_id: str | None
    device_id: str | None
    user_text: str
    history: tuple[dict[str, Any], ...]
    occurred_at: datetime
    llm: Any
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnOutcome:
    handled: bool
    spoken_text: str | None = None
    knowledge_status: str | None = None
    fact_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    content_version: int | None = None
    museum_state: Mapping[str, Any] = field(default_factory=dict)
    audit_id: str | None = None
    display_state: Mapping[str, Any] = field(default_factory=dict)
    audit_record: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    output_committed: bool = False

    @classmethod
    def unhandled(cls, reason: str | None = None) -> "TurnOutcome":
        return cls(handled=False, error_code=reason)


class ConversationRuntime(Protocol):
    def handle_turn(self, request: TurnRequest) -> TurnOutcome: ...
