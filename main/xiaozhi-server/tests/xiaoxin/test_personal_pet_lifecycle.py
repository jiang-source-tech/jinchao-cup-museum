from datetime import date

from core.xiaoxin.identity.models import PersonalPet
from core.xiaoxin.personal_pet_lifecycle import project_personal_pet


def test_active_personal_pet_projects_companion_days_and_year_in_shanghai_time():
    pet = PersonalPet(
        id="pet_1",
        owner_user_id="usr_1",
        status="active",
        created_at="2026-08-31T15:00:00+00:00",
        companion_started_at="2026-08-31T16:30:00+00:00",
        started_at_source="first_device_bind",
        updated_at="2026-08-31T16:30:00+00:00",
    )

    projection = project_personal_pet(pet, as_of=date(2027, 9, 1))

    assert projection.pet_id == "pet_1"
    assert projection.status == "active"
    assert projection.companion_started_at == "2026-08-31T16:30:00+00:00"
    assert projection.companion_days == 366
    assert projection.companion_year == 2
    assert projection.anniversary_date == "2027-09-01"


def test_pending_personal_pet_has_no_companion_age():
    pet = PersonalPet(
        id="pet_pending",
        owner_user_id="usr_1",
        status="pending",
        created_at="2026-08-01T00:00:00+00:00",
        companion_started_at=None,
        started_at_source="",
        updated_at="2026-08-01T00:00:00+00:00",
    )

    projection = project_personal_pet(pet, as_of=date(2026, 9, 1))

    assert projection.companion_days == 0
    assert projection.companion_year == 0
    assert projection.anniversary_date is None


def test_february_29_companion_anniversary_uses_february_28_in_non_leap_year():
    pet = PersonalPet(
        id="pet_leap",
        owner_user_id="usr_1",
        status="active",
        created_at="2024-02-29T08:00:00+08:00",
        companion_started_at="2024-02-29T08:00:00+08:00",
        started_at_source="first_device_bind",
        updated_at="2024-02-29T08:00:00+08:00",
    )

    projection = project_personal_pet(pet, as_of=date(2025, 2, 28))

    assert projection.companion_year == 2
    assert projection.anniversary_date == "2025-02-28"
