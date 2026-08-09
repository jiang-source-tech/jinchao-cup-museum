import asyncio
from datetime import datetime
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo


class _NoopLogger:
    def configure(self, **kwargs):
        return None

    def remove(self, *args, **kwargs):
        return None

    def add(self, *args, **kwargs):
        return None

    def bind(self, **kwargs):
        return self

    def debug(self, *args, **kwargs):
        return None


try:
    import loguru  # noqa: F401
except ModuleNotFoundError:
    sys.modules["loguru"] = SimpleNamespace(logger=_NoopLogger())

from core.xiaoxin.overview.models import DailyWeather
from core.providers.tools.server_plugins.plugin_executor import ServerPluginExecutor
from plugins_func.functions.get_xiaoxin_weather import get_xiaoxin_weather
from plugins_func.register import Action


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class RecordingWeatherService:
    def __init__(self):
        self.calls = []

    async def query_daily_weather(
        self,
        province,
        city,
        date_text,
        *,
        device_id=None,
    ):
        self.calls.append((province, city, date_text, device_id))
        return DailyWeather(
            province=province,
            city=city,
            date=date_text,
            weather_code=3,
            weather_text="中雨",
            temperature_min_c=24.0,
            temperature_max_c=29.0,
            fetched_at="2026-07-21T12:00:00+00:00",
            timezone_id="Asia/Shanghai",
        )


class FailingWeatherService:
    async def query_daily_weather(
        self,
        province,
        city,
        date_text,
        *,
        device_id=None,
    ):
        raise ValueError("AMap weather request failed (infocode 10003)")


class CanonicalWeatherService(RecordingWeatherService):
    async def query_daily_weather(
        self,
        province,
        city,
        date_text,
        *,
        device_id=None,
    ):
        self.calls.append((province, city, date_text, device_id))
        return DailyWeather(
            province="浙江",
            city="杭州",
            date=date_text,
            weather_code=3,
            weather_text="中雨",
            temperature_min_c=24.0,
            temperature_max_c=29.0,
            fetched_at="2026-07-21T12:00:00+00:00",
            timezone_id="Asia/Shanghai",
        )


def test_server_plugin_executor_registers_native_weather_tool():
    conn = SimpleNamespace(
        config={
            "selected_module": {"Intent": "function_call"},
            "Intent": {
                "function_call": {"functions": ["get_xiaoxin_weather"]}
            },
        }
    )

    tools = ServerPluginExecutor(conn).get_tools()

    assert "get_xiaoxin_weather" in tools
    assert tools["get_xiaoxin_weather"].description["function"]["parameters"][
        "required"
    ] == ["city"]


def test_voice_weather_uses_overview_cache_query():
    service = RecordingWeatherService()
    conn = SimpleNamespace(
        device_id="device-1",
        xiaoxin_control_runtime=SimpleNamespace(
            overview_service=service,
            overview_clock=lambda: datetime(
                2026, 7, 21, 20, 5, tzinfo=SHANGHAI_TZ
            ),
        )
    )

    response = asyncio.run(
        get_xiaoxin_weather(conn, city="杭州", province="浙江")
    )

    assert response.action == Action.REQLLM
    assert service.calls == [("浙江", "杭州", "2026-07-21", "device-1")]
    assert response.result == (
        "经高德天气核验：浙江杭州，2026-07-21，中雨，"
        "最低气温24℃，最高气温29℃。"
    )


def test_voice_weather_accepts_canonical_cached_city_name():
    service = CanonicalWeatherService()
    conn = SimpleNamespace(
        device_id="device-1",
        xiaoxin_control_runtime=SimpleNamespace(
            overview_service=service,
            overview_clock=lambda: datetime(
                2026, 7, 21, 20, 5, tzinfo=SHANGHAI_TZ
            ),
        ),
    )

    response = asyncio.run(
        get_xiaoxin_weather(conn, city="杭州市", province="浙江省")
    )

    assert response.action == Action.REQLLM
    assert response.result == (
        "经高德天气核验：浙江省杭州市，2026-07-21，中雨，"
        "最低气温24℃，最高气温29℃。"
    )


def test_voice_weather_rejects_missing_city_without_querying_service():
    service = RecordingWeatherService()
    conn = SimpleNamespace(
        xiaoxin_control_runtime=SimpleNamespace(
            overview_service=service,
        )
    )

    response = asyncio.run(get_xiaoxin_weather(conn, city=""))

    assert response.action == Action.REQLLM
    assert response.result == "天气查询缺少城市，请向用户询问城市。"
    assert service.calls == []


def test_voice_weather_hides_provider_failures_from_model():
    conn = SimpleNamespace(
        xiaoxin_control_runtime=SimpleNamespace(
            overview_service=FailingWeatherService(),
            overview_clock=lambda: datetime(
                2026, 7, 21, 20, 5, tzinfo=SHANGHAI_TZ
            ),
        )
    )

    response = asyncio.run(
        get_xiaoxin_weather(conn, city="杭州", province="浙江")
    )

    assert response.action == Action.REQLLM
    assert response.result == (
        "杭州的天气数据暂时不可用，请如实告知用户，不要猜测。"
    )
