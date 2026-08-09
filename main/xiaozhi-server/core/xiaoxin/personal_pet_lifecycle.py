from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .identity.models import PersonalPet


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class PersonalPetProjection:
    pet_id: str
    status: str
    companion_started_at: str | None
    companion_days: int
    companion_year: int
    anniversary_date: str | None


def project_personal_pet(
    pet: PersonalPet,
    *,
    as_of: date,
) -> PersonalPetProjection:
    if not pet.companion_started_at:
        return PersonalPetProjection(
            pet_id=pet.id,
            status=pet.status,
            companion_started_at=None,
            companion_days=0,
            companion_year=0,
            anniversary_date=None,
        )

    started_at = datetime.fromisoformat(
        pet.companion_started_at.replace("Z", "+00:00")
    )
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=SHANGHAI_TZ)
    started_on = started_at.astimezone(SHANGHAI_TZ).date()
    if as_of < started_on:
        raise ValueError("personal pet companion start is in the future")

    anniversary = _anniversary_for_year(started_on, as_of.year)
    if as_of < anniversary:
        companion_year = as_of.year - started_on.year
        anniversary = _anniversary_for_year(started_on, as_of.year - 1)
    else:
        companion_year = as_of.year - started_on.year + 1

    return PersonalPetProjection(
        pet_id=pet.id,
        status=pet.status,
        companion_started_at=pet.companion_started_at,
        companion_days=(as_of - started_on).days + 1,
        companion_year=max(1, companion_year),
        anniversary_date=anniversary.isoformat(),
    )


def _anniversary_for_year(started_on: date, year: int) -> date:
    try:
        return started_on.replace(year=year)
    except ValueError:
        return date(year, 2, 28)
