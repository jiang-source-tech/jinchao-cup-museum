from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import md5
from pathlib import Path
import re
from typing import Any

SAFE_SCOPE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class XiaoxinConfig:
    enabled: bool = False
    knowledge_dir: Path = Path("data/xiaoxin_knowledge")
    companion_db_path: Path = Path("data/xiaoxin_companion.db")
    max_tokens: int = 800
    free_chat_temperature: float = 0.8
    knowledge_temperature: float = 0.35
    boundary_temperature: float = 0.5

    @classmethod
    def from_dict(
        cls, data: dict[str, Any] | None, project_dir: str | Path
    ) -> "XiaoxinConfig":
        data = data or {}
        root = Path(project_dir)
        return cls(
            enabled=bool(data.get("enabled", False)),
            knowledge_dir=_resolve_path(
                root, data.get("knowledge_dir", "data/xiaoxin_knowledge")
            ),
            companion_db_path=_resolve_path(
                root,
                data.get("companion_db_path", "data/xiaoxin_companion.db"),
            ),
            max_tokens=int(data.get("max_tokens", 800)),
            free_chat_temperature=float(data.get("free_chat_temperature", 0.8)),
            knowledge_temperature=float(data.get("knowledge_temperature", 0.35)),
            boundary_temperature=float(data.get("boundary_temperature", 0.5)),
        )


@dataclass
class XiaoxinTurnResult:
    handled: bool
    reply: str | None = None
    model: str | None = None
    route: dict[str, Any] = field(default_factory=dict)
    memory_result: dict[str, Any] | None = None
    relationship: dict[str, Any] | None = None
    bypass_reason: str | None = None

    @classmethod
    def unhandled(cls, reason: str) -> "XiaoxinTurnResult":
        return cls(
            handled=False,
            route={"reply_mode": "existing_tool"},
            bypass_reason=reason,
        )


def normalize_user_scope(raw_user_id: str | None, fallback: str) -> str:
    candidate = str(raw_user_id or "").strip()
    if not candidate:
        return fallback
    if SAFE_SCOPE_RE.match(candidate):
        return candidate
    return "user_" + md5(candidate.encode("utf-8")).hexdigest()


def _resolve_path(project_dir: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_dir / path
