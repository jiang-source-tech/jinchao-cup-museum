from __future__ import annotations

from collections.abc import Mapping
import os


def resolve_api_key(
    config: Mapping[str, object],
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve an API key without requiring deployment secrets in YAML."""
    environment = os.environ if environ is None else environ
    env_name = str(config.get("api_key_env") or "").strip()
    if env_name:
        value = str(environment.get(env_name) or "").strip()
        if not value:
            raise ValueError(
                f"LLM API key environment variable {env_name} is not set"
            )
        return value

    value = str(config.get("api_key") or "").strip()
    if not value:
        raise ValueError("LLM API key is not configured")
    return value


def resolve_max_retries(config: Mapping[str, object]) -> int:
    raw_value = config.get("max_retries", 2)
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("LLM max_retries must be an integer") from exc
    if not 0 <= value <= 10:
        raise ValueError("LLM max_retries must be between 0 and 10")
    return value
