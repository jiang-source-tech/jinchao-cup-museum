from __future__ import annotations

import hashlib
import secrets


def new_id(prefix: str) -> str:
    return (
        f"{prefix}_{secrets.token_urlsafe(18).replace('-', '').replace('_', '')[:24]}"
    )


def stable_hash(*parts: object) -> str:
    normalized = "\x1f".join(str(part or "").strip() for part in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
