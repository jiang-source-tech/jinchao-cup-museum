from __future__ import annotations

import hashlib
from types import MappingProxyType
from typing import Mapping

from .contracts import BirthTemperament, TemperamentSourceKind


TEMPERAMENT_GENERATOR_VERSION = "xiaoxin-temperament-v1"
TEMPERAMENT_AXIS_LEVELS: Mapping[str, tuple[str, str, str]] = MappingProxyType(
    {
        "exploration_orientation": ("focused", "balanced", "exploratory"),
        "expression_energy": ("calm", "natural", "lively"),
        "thought_organization": ("intuitive", "balanced", "structured"),
        "playfulness": ("restrained", "lighthearted", "playful"),
        "companion_initiative": ("reserved", "timely", "proactive"),
    }
)


def _bucket_for_axis(*, pet_id: str, axis_key: str) -> int:
    payload = b"\0".join(
        (
            TEMPERAMENT_GENERATOR_VERSION.encode("utf-8"),
            pet_id.encode("utf-8"),
            axis_key.encode("ascii"),
        )
    )
    return hashlib.sha256(payload).digest()[0]


def _level_for_bucket(levels: tuple[str, str, str], bucket: int) -> str:
    if bucket < 64:
        return levels[0]
    if bucket < 192:
        return levels[1]
    return levels[2]


def temperament_dimensions_for_pet(pet_id: str) -> dict[str, str]:
    if not isinstance(pet_id, str) or not pet_id.strip():
        raise ValueError("pet_id must be a non-empty string")
    return {
        axis_key: _level_for_bucket(
            levels,
            _bucket_for_axis(pet_id=pet_id, axis_key=axis_key),
        )
        for axis_key, levels in TEMPERAMENT_AXIS_LEVELS.items()
    }


def generate_birth_temperament(
    *,
    pet_id: str,
    generated_at: str,
    source_kind: TemperamentSourceKind,
) -> BirthTemperament:
    return BirthTemperament(
        pet_id=pet_id,
        generator_version=TEMPERAMENT_GENERATOR_VERSION,
        **temperament_dimensions_for_pet(pet_id),
        generated_at=generated_at,
        source_kind=source_kind,
    )


def temperament_matches_generation(temperament: BirthTemperament) -> bool:
    if temperament.generator_version != TEMPERAMENT_GENERATOR_VERSION:
        return False
    expected = temperament_dimensions_for_pet(temperament.pet_id)
    return all(
        getattr(temperament, axis_key) == value
        for axis_key, value in expected.items()
    )
