from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IpCityLocation:
    province: str
    city: str
    country_code: str
    located_at: str


@dataclass(frozen=True)
class DailyWeather:
    province: str
    city: str
    date: str
    weather_code: int
    weather_text: str
    temperature_min_c: float
    temperature_max_c: float
    fetched_at: str
    timezone_id: str
    country_code: str = "CN"


@dataclass(frozen=True)
class OverviewSnapshot:
    device_id: str
    owner_user_id: str | None
    revision: int
    content_hash: str
    payload: dict[str, object]
    publish_state: str
    publish_attempts: int
    next_attempt_at: str | None
