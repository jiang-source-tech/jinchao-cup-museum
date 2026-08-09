from __future__ import annotations

import inspect
import ipaddress
import json
import math
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.xiaoxin.overview.models import DailyWeather, IpCityLocation


HttpGet = Callable[[str, dict[str, object], float], object]


class IpLocationProvider(Protocol):
    async def locate(self, public_ip: str) -> IpCityLocation | None: ...


class WeatherProvider(Protocol):
    async def validate_city(self, province: str, city: str) -> None: ...

    async def daily(
        self, province: str, city: str, date_text: str
    ) -> DailyWeather: ...


class ProviderDataError(ValueError):
    """A provider returned data that cannot be safely normalized."""


class LocationValidationError(ProviderDataError):
    """A user-supplied province/city pair has no strict CN match."""


async def _default_http_get(
    url: str, params: dict[str, object], timeout: float
) -> object:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=timeout) as response:
            response.raise_for_status()
            return await response.json(content_type=None)


async def _response_json(response: object, provider_name: str) -> object:
    if isinstance(response, (dict, list)):
        return response

    raise_for_status = getattr(response, "raise_for_status", None)
    if callable(raise_for_status):
        status_result = raise_for_status()
        if inspect.isawaitable(status_result):
            await status_result

    json_method = getattr(response, "json", None)
    if not callable(json_method):
        raise ProviderDataError(f"malformed JSON from {provider_name}")
    try:
        try:
            payload = json_method(content_type=None)
        except TypeError:
            payload = json_method()
        if inspect.isawaitable(payload):
            payload = await payload
        return payload
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise ProviderDataError(
            f"malformed JSON from {provider_name}"
        ) from exc


async def _get_json(
    http_get: HttpGet,
    url: str,
    params: dict[str, object],
    timeout: float,
    provider_name: str,
) -> object:
    request = http_get(url, params, timeout)
    if hasattr(request, "__aenter__") and hasattr(request, "__aexit__"):
        async with request as response:
            return await _response_json(response, provider_name)

    response = await request if inspect.isawaitable(request) else request
    return await _response_json(response, provider_name)


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PconlineIpLocationProvider:
    ENDPOINT = "https://whois.pconline.com.cn/ipJson.jsp"

    def __init__(
        self,
        *,
        http_get: HttpGet | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._http_get = http_get or _default_http_get
        self._timeout_seconds = timeout_seconds

    async def locate(self, public_ip: str) -> IpCityLocation | None:
        try:
            address = ipaddress.ip_address(public_ip)
        except ValueError:
            return None
        if (
            not address.is_global
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
            or getattr(address, "is_site_local", False)
        ):
            return None

        payload = await _get_json(
            self._http_get,
            self.ENDPOINT,
            {"ip": str(address), "json": "true"},
            self._timeout_seconds,
            "PConline IP location",
        )
        if not isinstance(payload, dict):
            raise ProviderDataError("malformed PConline IP location data")
        province = payload.get("pro")
        city = payload.get("city")
        if not isinstance(province, str) or not province.strip():
            raise ProviderDataError("malformed PConline IP location data: pro")
        if not isinstance(city, str) or not city.strip():
            raise ProviderDataError("malformed PConline IP location data: city")
        return IpCityLocation(
            province=province.strip(),
            city=city.strip(),
            country_code="CN",
            located_at=_utc_now_text(),
        )


_WMO_WEATHER_TEXT = {
    0: "晴",
    1: "晴",
    2: "多云",
    3: "多云",
    45: "雾",
    48: "雾",
    51: "小雨",
    53: "小雨",
    55: "小雨",
    56: "冻雨",
    57: "冻雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "小雪",
    80: "小雨",
    81: "中雨",
    82: "大雨",
    85: "小雪",
    86: "大雪",
    95: "雷阵雨",
    96: "雷阵雨伴冰雹",
    99: "雷阵雨伴冰雹",
}


def _normalized_place(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = "".join(value.split())
    for suffix in ("特别行政区", "自治区", "自治州", "省", "市"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _coordinate(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    coordinate = float(value)
    return coordinate if math.isfinite(coordinate) else None


def _numeric_text(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


_AMAP_WEATHER_CODE_RULES: tuple[tuple[tuple[str, ...], int], ...] = (
    (("雷阵雨并伴有冰雹", "雷阵雨伴有冰雹", "冰雹"), 96),
    (("雷阵雨", "雷暴"), 95),
    (("冻雨",), 66),
    (("特大暴雨", "大暴雨", "暴雨", "大雨"), 65),
    (("中雨",), 63),
    (("阵雨",), 80),
    (("暴雪", "大雪"), 75),
    (("中雪",), 73),
    (("阵雪",), 85),
    (("小雪", "雨夹雪", "雪"), 71),
    (("小雨", "毛毛雨", "细雨", "雨"), 61),
    (("沙尘暴", "扬沙", "浮尘", "霾", "雾"), 45),
    (("阴",), 3),
    (("多云", "少云"), 2),
    (("晴",), 0),
)


def _amap_weather_code(*weather_texts: str) -> int:
    combined = " ".join(weather_texts)
    for keywords, weather_code in _AMAP_WEATHER_CODE_RULES:
        if any(keyword in combined for keyword in keywords):
            return weather_code
    return -1


class AmapWeatherProvider:
    DEFAULT_API_HOST = "restapi.amap.com"
    GEOCODING_ENDPOINT = "https://restapi.amap.com/v3/geocode/geo"
    WEATHER_ENDPOINT = "https://restapi.amap.com/v3/weather/weatherInfo"

    def __init__(
        self,
        *,
        api_key: str,
        api_host: str = DEFAULT_API_HOST,
        city_adcodes: Mapping[str, str] | None = None,
        http_get: HttpGet | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        normalized_key = str(api_key or "").strip()
        if not normalized_key:
            raise ValueError("AMap weather API key is required")
        normalized_host = str(api_host or self.DEFAULT_API_HOST).strip().rstrip("/")
        if not normalized_host:
            normalized_host = self.DEFAULT_API_HOST
        if normalized_host.startswith(("http://", "https://")):
            base_url = normalized_host
        else:
            base_url = f"https://{normalized_host}"

        self._api_key = normalized_key
        self._http_get = http_get or _default_http_get
        self._timeout_seconds = timeout_seconds
        self._city_adcodes = self._normalize_city_adcodes(city_adcodes or {})
        self.GEOCODING_ENDPOINT = f"{base_url}/v3/geocode/geo"
        self.WEATHER_ENDPOINT = f"{base_url}/v3/weather/weatherInfo"

    async def validate_city(self, province: str, city: str) -> None:
        await self._resolve_adcode(province, city)

    async def daily(
        self, province: str, city: str, date_text: str
    ) -> DailyWeather:
        adcode = await self._resolve_adcode(province, city)
        payload = await _get_json(
            self._http_get,
            self.WEATHER_ENDPOINT,
            {
                "city": adcode,
                "extensions": "all",
                "output": "JSON",
                "key": self._api_key,
            },
            self._timeout_seconds,
            "AMap weather",
        )
        self._require_success(payload, "weather")
        weather_code, weather_text, minimum, maximum = self._daily_values(
            payload,
            adcode,
            date_text,
            province,
            city,
        )
        return DailyWeather(
            province=province,
            city=city,
            date=date_text,
            weather_code=weather_code,
            weather_text=weather_text,
            temperature_min_c=minimum,
            temperature_max_c=maximum,
            fetched_at=_utc_now_text(),
            timezone_id="Asia/Shanghai",
            country_code="CN",
        )

    async def _resolve_adcode(self, province: str, city: str) -> str:
        configured = self._city_adcodes.get(
            self._city_adcode_key(province, city)
        )
        if configured:
            return configured
        resolved = await self._geocode(province, city)
        self._city_adcodes[self._city_adcode_key(province, city)] = resolved
        return resolved

    @classmethod
    def _normalize_city_adcodes(
        cls,
        city_adcodes: Mapping[str, str],
    ) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for location, adcode in city_adcodes.items():
            parts = str(location).split("/", 1)
            if len(parts) != 2:
                raise ValueError(
                    "AMap city adcode keys must use province/city format"
                )
            key = cls._city_adcode_key(parts[0], parts[1])
            normalized_adcode = str(adcode or "").strip()
            if not all(key.split("/")) or not cls._valid_adcode(
                normalized_adcode
            ):
                raise ValueError("invalid AMap city adcode configuration")
            normalized[key] = normalized_adcode
        return normalized

    @staticmethod
    def _city_adcode_key(province: object, city: object) -> str:
        return f"{_normalized_place(province)}/{_normalized_place(city)}"

    async def _geocode(self, province: str, city: str) -> str:
        payload = await _get_json(
            self._http_get,
            self.GEOCODING_ENDPOINT,
            {
                "address": f"{province}{city}",
                "output": "JSON",
                "key": self._api_key,
            },
            self._timeout_seconds,
            "AMap geocoding",
        )
        self._require_success(payload, "geocoding")
        if not isinstance(payload, dict) or not isinstance(
            payload.get("geocodes"), list
        ):
            raise ProviderDataError("malformed AMap geocoding data")

        candidates = [
            item
            for item in payload["geocodes"]
            if isinstance(item, dict) and self._valid_adcode(item.get("adcode"))
        ]
        province_key = _normalized_place(province)
        province_matches = [
            item
            for item in candidates
            if not province_key
            or _normalized_place(item.get("province")) == province_key
        ]
        if province_key and not province_matches:
            raise LocationValidationError(
                f"AMap geocoding found no result in province {province}"
            )

        city_key = _normalized_place(city)
        city_matches = [
            item
            for item in province_matches
            if not city_key or city_key in self._geocode_place_names(item)
        ]
        if not city_matches:
            raise LocationValidationError(
                f"AMap geocoding found no result for {province} {city}"
            )
        return str(city_matches[0]["adcode"]).strip()

    @staticmethod
    def _valid_adcode(value: object) -> bool:
        return (
            isinstance(value, str)
            and len(value.strip()) == 6
            and value.strip().isdigit()
        )

    @staticmethod
    def _geocode_place_names(item: dict[str, object]) -> set[str]:
        names = {
            _normalized_place(item.get("city")),
            _normalized_place(item.get("district")),
        }
        province_name = _normalized_place(item.get("province"))
        if province_name in {"北京", "天津", "上海", "重庆", "香港", "澳门"}:
            names.add(province_name)
        return {name for name in names if name}

    @staticmethod
    def _require_success(payload: object, operation: str) -> None:
        if not isinstance(payload, dict):
            raise ProviderDataError(f"malformed AMap {operation} data")
        status = str(payload.get("status") or "")
        infocode = str(payload.get("infocode") or "")
        if status != "1" or (infocode and infocode != "10000"):
            safe_code = infocode or "unknown"
            raise ProviderDataError(
                f"AMap {operation} request failed (infocode {safe_code})"
            )

    @classmethod
    def _daily_values(
        cls,
        payload: object,
        adcode: str,
        date_text: str,
        province: str,
        city: str,
    ) -> tuple[int, str, float, float]:
        if not isinstance(payload, dict) or not isinstance(
            payload.get("forecasts"), list
        ):
            raise ProviderDataError("malformed AMap weather data")
        forecast = next(
            (
                item
                for item in payload["forecasts"]
                if isinstance(item, dict)
                and str(item.get("adcode") or "").strip() == adcode
            ),
            None,
        )
        if forecast is None or not isinstance(forecast.get("casts"), list):
            raise ProviderDataError("malformed AMap weather data")
        province_key = _normalized_place(province)
        if (
            (
                province_key
                and _normalized_place(forecast.get("province"))
                != province_key
            )
            or _normalized_place(forecast.get("city"))
            != _normalized_place(city)
        ):
            raise ProviderDataError(
                "AMap weather response does not match requested location"
            )
        cast = next(
            (
                item
                for item in forecast["casts"]
                if isinstance(item, dict)
                and str(item.get("date") or "").strip() == date_text
            ),
            None,
        )
        if cast is None:
            raise ProviderDataError(
                f"AMap weather data has no forecast for {date_text}"
            )

        day_weather = str(cast.get("dayweather") or "").strip()
        night_weather = str(cast.get("nightweather") or "").strip()
        if not day_weather or not night_weather:
            raise ProviderDataError("malformed AMap weather data: weather text")
        weather_text = (
            day_weather
            if day_weather == night_weather
            else f"{day_weather}转{night_weather}"
        )
        minimum = _numeric_text(cast.get("nighttemp"))
        maximum = _numeric_text(cast.get("daytemp"))
        if minimum is None or maximum is None:
            raise ProviderDataError("malformed AMap weather data: temperature")
        return (
            _amap_weather_code(day_weather, night_weather),
            weather_text,
            minimum,
            maximum,
        )


class OpenMeteoWeatherProvider:
    GEOCODING_ENDPOINT = "https://geocoding-api.open-meteo.com/v1/search"
    FORECAST_ENDPOINT = "https://api.open-meteo.com/v1/forecast"

    def __init__(
        self,
        *,
        http_get: HttpGet | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._http_get = http_get or _default_http_get
        self._timeout_seconds = timeout_seconds

    async def validate_city(self, province: str, city: str) -> None:
        await self._geocode(province, city)

    async def daily(
        self, province: str, city: str, date_text: str
    ) -> DailyWeather:
        latitude, longitude = await self._geocode(province, city)
        payload = await _get_json(
            self._http_get,
            self.FORECAST_ENDPOINT,
            {
                "latitude": latitude,
                "longitude": longitude,
                "daily": (
                    "weather_code,temperature_2m_min,temperature_2m_max"
                ),
                "timezone": "auto",
                "start_date": date_text,
                "end_date": date_text,
            },
            self._timeout_seconds,
            "Open-Meteo forecast",
        )
        weather_code, minimum, maximum, timezone_id = self._daily_values(
            payload, date_text
        )
        weather_text = _WMO_WEATHER_TEXT.get(weather_code)
        if weather_text is None:
            raise ProviderDataError(
                f"unknown WMO weather code: {weather_code}"
            )
        return DailyWeather(
            province=province,
            city=city,
            date=date_text,
            weather_code=weather_code,
            weather_text=weather_text,
            temperature_min_c=minimum,
            temperature_max_c=maximum,
            fetched_at=_utc_now_text(),
            timezone_id=timezone_id,
            country_code="CN",
        )

    async def _geocode(self, province: str, city: str) -> tuple[float, float]:
        payload = await _get_json(
            self._http_get,
            self.GEOCODING_ENDPOINT,
            {
                "name": city,
                "count": 10,
                "language": "zh",
                "format": "json",
                "countryCode": "CN",
            },
            self._timeout_seconds,
            "Open-Meteo geocoding",
        )
        if not isinstance(payload, dict) or not isinstance(
            payload.get("results"), list
        ):
            raise ProviderDataError("malformed Open-Meteo geocoding data")

        candidates = [
            item
            for item in payload["results"]
            if isinstance(item, dict)
            and str(item.get("country_code", "")).upper() == "CN"
            and _coordinate(item.get("latitude")) is not None
            and _coordinate(item.get("longitude")) is not None
        ]
        province_key = _normalized_place(province)
        province_matches = [
            item
            for item in candidates
            if _normalized_place(item.get("admin1")) == province_key
        ]
        if province_key and not province_matches:
            raise LocationValidationError(
                f"Open-Meteo geocoding found no CN result in province {province}"
            )
        selected = province_matches if province_key else candidates
        if not selected:
            raise LocationValidationError(
                f"Open-Meteo geocoding found no CN result for {province} {city}"
            )
        latitude = _coordinate(selected[0].get("latitude"))
        longitude = _coordinate(selected[0].get("longitude"))
        if latitude is None or longitude is None:
            raise ProviderDataError("malformed Open-Meteo geocoding data")
        return latitude, longitude

    @staticmethod
    def _daily_values(
        payload: object, date_text: str
    ) -> tuple[int, float, float, str]:
        if not isinstance(payload, dict):
            raise ProviderDataError("malformed Open-Meteo forecast data")

        timezone_value = payload.get("timezone")
        if not isinstance(timezone_value, str) or not timezone_value.strip():
            raise ProviderDataError(
                "malformed Open-Meteo forecast data: missing timezone"
            )
        timezone_id = timezone_value.strip()
        try:
            ZoneInfo(timezone_id)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ProviderDataError(
                f"malformed Open-Meteo forecast data: invalid timezone {timezone_id}"
            ) from exc

        daily = payload.get("daily")
        if not isinstance(daily, dict):
            raise ProviderDataError("malformed Open-Meteo forecast data")
        dates = daily.get("time")
        codes = daily.get("weather_code")
        minimums = daily.get("temperature_2m_min")
        maximums = daily.get("temperature_2m_max")
        if not all(
            isinstance(values, list)
            for values in (dates, codes, minimums, maximums)
        ):
            raise ProviderDataError("malformed Open-Meteo forecast data")
        try:
            index = dates.index(date_text)
            code_value = codes[index]
            minimum_value = minimums[index]
            maximum_value = maximums[index]
        except (ValueError, IndexError) as exc:
            raise ProviderDataError(
                "malformed Open-Meteo forecast data"
            ) from exc

        if (
            isinstance(code_value, bool)
            or not isinstance(code_value, (int, float))
            or not float(code_value).is_integer()
        ):
            raise ProviderDataError("malformed Open-Meteo forecast data")
        weather_code = int(code_value)
        minimum = _coordinate(minimum_value)
        maximum = _coordinate(maximum_value)
        if minimum is None or maximum is None:
            raise ProviderDataError("malformed Open-Meteo forecast data")
        return weather_code, minimum, maximum, timezone_id


__all__ = [
    "AmapWeatherProvider",
    "IpLocationProvider",
    "LocationValidationError",
    "OpenMeteoWeatherProvider",
    "PconlineIpLocationProvider",
    "ProviderDataError",
    "WeatherProvider",
]
