from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import core.xiaoxin.overview.providers as overview_providers

from core.xiaoxin.overview.providers import (
    AmapWeatherProvider,
    OpenMeteoWeatherProvider,
    PconlineIpLocationProvider,
    ProviderDataError,
)


class FakeResponse:
    def __init__(self, payload: Any = None, *, json_error: Exception | None = None):
        self.payload = payload
        self.json_error = json_error

    def raise_for_status(self) -> None:
        return None

    async def json(self, **_kwargs: object) -> Any:
        if self.json_error is not None:
            raise self.json_error
        return self.payload


def test_location_validation_error_is_provider_data_error_subclass():
    assert hasattr(overview_providers, "LocationValidationError")
    assert issubclass(
        overview_providers.LocationValidationError,
        ProviderDataError,
    )


def test_pconline_provider_parses_province_and_city_fields():
    calls = []

    async def fake_get(url, params, timeout):
        calls.append((url, params, timeout))
        return FakeResponse({"pro": "浙江", "city": "杭州"})

    result = asyncio.run(
        PconlineIpLocationProvider(http_get=fake_get).locate("8.8.8.8")
    )

    assert result is not None
    assert (result.province, result.city, result.country_code) == (
        "浙江",
        "杭州",
        "CN",
    )
    assert calls == [
        (
            "https://whois.pconline.com.cn/ipJson.jsp",
            {"ip": "8.8.8.8", "json": "true"},
            5.0,
        )
    ]


@pytest.mark.parametrize(
    "address",
    [
        "192.168.1.10",
        "127.0.0.1",
        "169.254.1.10",
        "224.0.0.1",
        "0.0.0.0",
        "255.255.255.255",
        "ff02::1",
        "::",
        "fec0::1",
        "100::1",
        "2001:db8::1",
        "not-an-ip",
    ],
)
def test_pconline_provider_accepts_only_global_unicast_without_http(address):
    async def unexpected_get(*_args, **_kwargs):
        raise AssertionError("private or invalid IP must not reach provider")

    result = asyncio.run(
        PconlineIpLocationProvider(http_get=unexpected_get).locate(address)
    )

    assert result is None


def test_pconline_provider_propagates_request_timeout():
    async def timed_out_get(_url, _params, timeout):
        assert timeout == 5.0
        raise asyncio.TimeoutError("provider timed out")

    provider = PconlineIpLocationProvider(http_get=timed_out_get)

    with pytest.raises(asyncio.TimeoutError, match="provider timed out"):
        asyncio.run(provider.locate("8.8.8.8"))


def test_pconline_provider_rejects_malformed_json():
    async def fake_get(_url, _params, _timeout):
        return FakeResponse(
            json_error=json.JSONDecodeError("bad provider JSON", "{", 1)
        )

    provider = PconlineIpLocationProvider(http_get=fake_get)

    with pytest.raises(ValueError, match="malformed JSON"):
        asyncio.run(provider.locate("8.8.8.8"))


def test_amap_provider_normalizes_daily_forecast_with_configured_adcode():
    calls = []

    async def fake_get(url, params, timeout):
        calls.append((url, params, timeout))
        if url.endswith("/v3/geocode/geo"):
            raise AssertionError("configured city adcode must skip geocoding")
        return FakeResponse(
            {
                "status": "1",
                "info": "OK",
                "infocode": "10000",
                "count": "1",
                "forecasts": [
                    {
                        "city": "杭州市",
                        "adcode": "330100",
                        "province": "浙江省",
                        "reporttime": "2026-07-10 11:00:00",
                        "casts": [
                            {
                                "date": "2026-07-10",
                                "week": "5",
                                "dayweather": "小雨",
                                "nightweather": "多云",
                                "daytemp": "34",
                                "nighttemp": "26",
                            }
                        ],
                    }
                ],
            }
        )

    result = asyncio.run(
        AmapWeatherProvider(
            api_key="test-api-key",
            api_host="restapi.amap.com",
            city_adcodes={"浙江/杭州": "330100"},
            http_get=fake_get,
        ).daily("浙江", "杭州", "2026-07-10")
    )

    assert result.province == "浙江"
    assert result.city == "杭州"
    assert result.country_code == "CN"
    assert result.date == "2026-07-10"
    assert result.weather_code == 61
    assert result.weather_text == "小雨转多云"
    assert result.temperature_min_c == 26.0
    assert result.temperature_max_c == 34.0
    assert result.timezone_id == "Asia/Shanghai"
    assert calls == [
        (
            "https://restapi.amap.com/v3/weather/weatherInfo",
            {
                "city": "330100",
                "extensions": "all",
                "output": "JSON",
                "key": "test-api-key",
            },
            5.0,
        ),
    ]


def test_amap_validate_city_only_geocodes_without_requesting_weather():
    calls = []

    async def fake_get(url, params, timeout):
        calls.append((url, params, timeout))
        if url.endswith("/v3/weather/weatherInfo"):
            raise AssertionError("city validation must not request weather")
        return FakeResponse(
            {
                "status": "1",
                "info": "OK",
                "infocode": "10000",
                "count": "1",
                "geocodes": [
                    {
                        "province": "浙江省",
                        "city": "杭州市",
                        "district": [],
                        "adcode": "330100",
                    }
                ],
            }
        )

    provider = AmapWeatherProvider(api_key="test-api-key", http_get=fake_get)

    asyncio.run(provider.validate_city("浙江", "杭州"))

    assert len(calls) == 1
    assert calls[0][0] == provider.GEOCODING_ENDPOINT


def test_amap_provider_caches_geocoded_city_and_avoids_duplicate_weather_text():
    calls = []

    async def fake_get(url, params, timeout):
        calls.append((url, params, timeout))
        if url.endswith("/v3/geocode/geo"):
            return FakeResponse(
                {
                    "status": "1",
                    "info": "OK",
                    "infocode": "10000",
                    "count": "1",
                    "geocodes": [
                        {
                            "province": "浙江省",
                            "city": "杭州市",
                            "district": [],
                            "adcode": "330100",
                        }
                    ],
                }
            )
        return FakeResponse(
            {
                "status": "1",
                "info": "OK",
                "infocode": "10000",
                "count": "1",
                "forecasts": [
                    {
                        "city": "杭州市",
                        "adcode": "330100",
                        "province": "浙江省",
                        "casts": [
                            {
                                "date": "2026-07-10",
                                "dayweather": "晴",
                                "nightweather": "晴",
                                "daytemp": "35",
                                "nighttemp": "27",
                            }
                        ],
                    }
                ],
            }
        )

    provider = AmapWeatherProvider(api_key="test-api-key", http_get=fake_get)

    asyncio.run(provider.validate_city("浙江", "杭州"))
    result = asyncio.run(provider.daily("浙江", "杭州", "2026-07-10"))

    assert result.weather_text == "晴"
    assert result.weather_code == 0
    assert sum(url.endswith("/v3/geocode/geo") for url, _, _ in calls) == 1


def test_amap_provider_accepts_geocoded_city_when_province_is_omitted():
    async def fake_get(url, _params, _timeout):
        if url.endswith("/v3/geocode/geo"):
            return FakeResponse(
                {
                    "status": "1",
                    "info": "OK",
                    "infocode": "10000",
                    "count": "1",
                    "geocodes": [
                        {
                            "province": "浙江省",
                            "city": "杭州市",
                            "district": [],
                            "adcode": "330100",
                        }
                    ],
                }
            )
        return FakeResponse(
            {
                "status": "1",
                "info": "OK",
                "infocode": "10000",
                "count": "1",
                "forecasts": [
                    {
                        "city": "杭州市",
                        "adcode": "330100",
                        "province": "浙江省",
                        "casts": [
                            {
                                "date": "2026-07-21",
                                "dayweather": "小雨",
                                "nightweather": "小雨",
                                "daytemp": "34",
                                "nighttemp": "26",
                            }
                        ],
                    }
                ],
            }
        )

    result = asyncio.run(
        AmapWeatherProvider(
            api_key="test-api-key",
            http_get=fake_get,
        ).daily("", "杭州", "2026-07-21")
    )

    assert result.city == "杭州"
    assert result.weather_text == "小雨"
    assert result.temperature_min_c == 26.0
    assert result.temperature_max_c == 34.0


def test_amap_provider_rejects_weather_for_wrong_configured_location():
    async def fake_get(url, _params, _timeout):
        if url.endswith("/v3/geocode/geo"):
            raise AssertionError("configured city adcode must skip geocoding")
        return FakeResponse(
            {
                "status": "1",
                "info": "OK",
                "infocode": "10000",
                "count": "1",
                "forecasts": [
                    {
                        "city": "广州市",
                        "adcode": "440100",
                        "province": "广东省",
                        "casts": [
                            {
                                "date": "2026-07-10",
                                "dayweather": "晴",
                                "nightweather": "晴",
                                "daytemp": "35",
                                "nighttemp": "27",
                            }
                        ],
                    }
                ],
            }
        )

    provider = AmapWeatherProvider(
        api_key="test-api-key",
        city_adcodes={"浙江/杭州": "440100"},
        http_get=fake_get,
    )

    with pytest.raises(ProviderDataError, match="requested location"):
        asyncio.run(provider.daily("浙江", "杭州", "2026-07-10"))


def test_amap_provider_rejects_cross_province_geocode_without_weather_request():
    async def fake_get(url, _params, _timeout):
        if url.endswith("/v3/weather/weatherInfo"):
            raise AssertionError("invalid city must not request weather")
        return FakeResponse(
            {
                "status": "1",
                "info": "OK",
                "infocode": "10000",
                "count": "1",
                "geocodes": [
                    {
                        "province": "广东省",
                        "city": "杭州市",
                        "district": [],
                        "adcode": "440100",
                    }
                ],
            }
        )

    provider = AmapWeatherProvider(api_key="test-api-key", http_get=fake_get)

    with pytest.raises(ProviderDataError, match="province"):
        asyncio.run(provider.daily("浙江", "杭州", "2026-07-10"))


def test_amap_provider_surfaces_provider_infocode_without_exposing_api_key():
    async def fake_get(_url, _params, _timeout):
        return FakeResponse(
            {
                "status": "0",
                "info": "INVALID_USER_KEY",
                "infocode": "10001",
            }
        )

    provider = AmapWeatherProvider(api_key="secret-test-key", http_get=fake_get)

    with pytest.raises(ProviderDataError, match="10001") as raised:
        asyncio.run(provider.validate_city("浙江", "杭州"))
    assert "secret-test-key" not in str(raised.value)


def test_open_meteo_provider_normalizes_daily_forecast_and_cn_geocoding():
    calls = []

    async def fake_get(url, params, timeout):
        calls.append((url, params, timeout))
        if "geocoding-api" in url:
            return FakeResponse(
                {
                    "results": [
                        {
                            "name": "Hangzhou",
                            "country_code": "US",
                            "latitude": 30.0,
                            "longitude": -97.0,
                        },
                        {
                            "name": "杭州",
                            "country_code": "CN",
                            "admin1": "广东",
                            "latitude": 23.1291,
                            "longitude": 113.2644,
                        },
                        {
                            "name": "杭州",
                            "country_code": "CN",
                            "admin1": "浙江",
                            "latitude": 30.2741,
                            "longitude": 120.1551,
                        },
                    ]
                }
            )
        return FakeResponse(
            {
                "timezone": "Asia/Shanghai",
                "daily": {
                    "time": ["2026-07-10"],
                    "weather_code": [3],
                    "temperature_2m_min": [26.0],
                    "temperature_2m_max": [35.0],
                },
            }
        )

    result = asyncio.run(
        OpenMeteoWeatherProvider(http_get=fake_get).daily(
            "浙江", "杭州", "2026-07-10"
        )
    )

    assert result.province == "浙江"
    assert result.city == "杭州"
    assert result.country_code == "CN"
    assert result.date == "2026-07-10"
    assert result.weather_code == 3
    assert result.weather_text == "多云"
    assert result.temperature_min_c == 26.0
    assert result.temperature_max_c == 35.0
    assert result.timezone_id == "Asia/Shanghai"
    assert calls[0] == (
        "https://geocoding-api.open-meteo.com/v1/search",
        {
            "name": "杭州",
            "count": 10,
            "language": "zh",
            "format": "json",
            "countryCode": "CN",
        },
        5.0,
    )
    assert calls[1] == (
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": 30.2741,
            "longitude": 120.1551,
            "daily": "weather_code,temperature_2m_min,temperature_2m_max",
            "timezone": "auto",
            "start_date": "2026-07-10",
            "end_date": "2026-07-10",
        },
        5.0,
    )


def test_open_meteo_validate_city_only_runs_strict_cn_geocoding():
    calls = []

    async def fake_get(url, params, timeout):
        calls.append((url, params, timeout))
        if "forecast" in url:
            raise AssertionError("city validation must not request a forecast")
        return FakeResponse(
            {
                "results": [
                    {
                        "name": "Hangzhou",
                        "country_code": "US",
                        "admin1": "Texas",
                        "latitude": 30.0,
                        "longitude": -97.0,
                    },
                    {
                        "name": "杭州",
                        "country_code": "CN",
                        "admin1": "浙江",
                        "latitude": 30.2741,
                        "longitude": 120.1551,
                    },
                ]
            }
        )

    provider = OpenMeteoWeatherProvider(http_get=fake_get)

    asyncio.run(provider.validate_city("浙江", "杭州"))

    assert len(calls) == 1
    assert calls[0][0] == provider.GEOCODING_ENDPOINT


def test_open_meteo_validate_city_rejects_cross_province_match_without_forecast():
    async def fake_get(url, _params, _timeout):
        if "forecast" in url:
            raise AssertionError("invalid city must not request a forecast")
        return FakeResponse(
            {
                "results": [
                    {
                        "name": "长安",
                        "country_code": "CN",
                        "admin1": "陕西",
                        "latitude": 34.157,
                        "longitude": 108.906,
                    }
                ]
            }
        )

    provider = OpenMeteoWeatherProvider(http_get=fake_get)

    with pytest.raises(ProviderDataError, match="province"):
        asyncio.run(provider.validate_city("河北", "长安"))


def test_open_meteo_validate_city_classifies_malformed_schema_as_provider_error():
    async def fake_get(_url, _params, _timeout):
        return FakeResponse({"results": "not-a-list"})

    provider = OpenMeteoWeatherProvider(http_get=fake_get)

    with pytest.raises(ProviderDataError) as raised:
        asyncio.run(provider.validate_city("浙江", "杭州"))
    assert not isinstance(
        raised.value,
        overview_providers.LocationValidationError,
    )


def test_open_meteo_provider_rejects_cn_candidate_from_wrong_province():
    async def fake_get(url, _params, _timeout):
        if "geocoding-api" in url:
            return FakeResponse(
                {
                    "results": [
                        {
                            "name": "长安",
                            "country_code": "CN",
                            "admin1": "陕西",
                            "latitude": 34.157,
                            "longitude": 108.906,
                        }
                    ]
                }
            )
        raise AssertionError("forecast must not run for a province mismatch")

    provider = OpenMeteoWeatherProvider(http_get=fake_get)

    with pytest.raises(ProviderDataError, match="province"):
        asyncio.run(provider.daily("河北", "长安", "2026-07-10"))


def _weather_get_with_forecast(forecast_payload):
    async def fake_get(url, _params, _timeout):
        if "geocoding-api" in url:
            return FakeResponse(
                {
                    "results": [
                        {
                            "name": "杭州",
                            "country_code": "CN",
                            "admin1": "浙江",
                            "latitude": 30.2741,
                            "longitude": 120.1551,
                        }
                    ]
                }
            )
        return FakeResponse(forecast_payload)

    return fake_get


def test_open_meteo_provider_rejects_unknown_wmo_code():
    provider = OpenMeteoWeatherProvider(
        http_get=_weather_get_with_forecast(
            {
                "timezone": "Asia/Shanghai",
                "daily": {
                    "time": ["2026-07-10"],
                    "weather_code": [999],
                    "temperature_2m_min": [26.0],
                    "temperature_2m_max": [35.0],
                },
            }
        )
    )

    with pytest.raises(ValueError, match="unknown WMO weather code: 999"):
        asyncio.run(provider.daily("浙江", "杭州", "2026-07-10"))


@pytest.mark.parametrize("timezone_value", [None, "", "Mars/Olympus_Mons"])
def test_open_meteo_provider_requires_valid_response_timezone(timezone_value):
    provider = OpenMeteoWeatherProvider(
        http_get=_weather_get_with_forecast(
            {
                "timezone": timezone_value,
                "daily": {
                    "time": ["2026-07-10"],
                    "weather_code": [3],
                    "temperature_2m_min": [26.0],
                    "temperature_2m_max": [35.0],
                },
            }
        )
    )

    with pytest.raises(ValueError, match="timezone"):
        asyncio.run(provider.daily("浙江", "杭州", "2026-07-10"))


def test_open_meteo_provider_rejects_malformed_daily_payload():
    provider = OpenMeteoWeatherProvider(
        http_get=_weather_get_with_forecast(
            {"timezone": "Asia/Shanghai", "daily": {"weather_code": [3]}}
        )
    )

    with pytest.raises(ValueError, match="malformed Open-Meteo forecast data"):
        asyncio.run(provider.daily("浙江", "杭州", "2026-07-10"))
