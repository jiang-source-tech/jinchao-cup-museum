from __future__ import annotations

from datetime import timedelta


_RELATIONSHIP_STAGE_FACTORS = {
    "first_meeting": 1.25,
    "familiar": 1.0,
    "attuned": 0.9,
    "long_term_companion": 0.8,
}
_INITIATIVE_CADENCE_FACTORS = {
    "disabled": 1.0,
    "low": 1.0,
    "medium": 0.5,
}


def default_initiative_level(relationship_stage: str) -> str:
    if relationship_stage in {"first_meeting", "familiar"}:
        return "low"
    if relationship_stage in {"attuned", "long_term_companion"}:
        return "medium"
    raise ValueError("relationship stage is invalid")


def connection_bid_delay(
    base_delay: timedelta,
    *,
    relationship_stage: str,
    initiative_level: str,
) -> timedelta:
    try:
        stage_factor = _RELATIONSHIP_STAGE_FACTORS[relationship_stage]
        cadence_factor = _INITIATIVE_CADENCE_FACTORS[initiative_level]
    except KeyError as exc:
        raise ValueError("connection bid timing input is invalid") from exc
    return base_delay * stage_factor * cadence_factor


def rescale_connection_threshold(
    threshold_seconds: int,
    *,
    previous_level: str,
    next_level: str,
) -> int:
    try:
        previous_factor = _INITIATIVE_CADENCE_FACTORS[previous_level]
        next_factor = _INITIATIVE_CADENCE_FACTORS[next_level]
    except KeyError as exc:
        raise ValueError("initiative level is invalid") from exc
    return max(int(round(max(int(threshold_seconds), 1) * next_factor / previous_factor)), 1)
