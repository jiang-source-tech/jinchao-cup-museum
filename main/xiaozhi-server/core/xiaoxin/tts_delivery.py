from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TtsAckResult:
    state: str
    sentence_id: str
    reason: str | None = None

    @property
    def successful(self) -> bool:
        return self.state in {"ready", "done"} and self.reason is None


class TtsAttemptError(RuntimeError):
    def __init__(self, sentence_id: str, reason: str):
        super().__init__(f"tts attempt failed: {reason}")
        self.sentence_id = sentence_id
        self.reason = reason


@dataclass(frozen=True)
class TtsAttemptOutcome:
    sentence_id: str
    status: str
    reason: str | None = None
