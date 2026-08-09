from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

from core.xiaoxin.local_time import local_date_text, local_datetime
from plugins_func.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from core.connection import ConnectionHandler


LOGGER = logging.getLogger(__name__)
_WEATHER_PLACE_SUFFIXES = (
    "特别行政区",
    "自治区",
    "自治州",
    "省",
    "市",
)


def _normalized_weather_place(value: object) -> str:
    normalized = "".join(str(value or "").split())
    for suffix in _WEATHER_PLACE_SUFFIXES:
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


GET_XIAOXIN_WEATHER_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "get_xiaoxin_weather",
        "description": (
            "通过小芯与小程序共用的高德天气数据源查询中国城市天气。"
            "凡是天气、气温、下雨、下雪、是否带伞或短期预报问题都必须调用。"
            "date 使用系统当前日期计算 YYYY-MM-DD；仅支持今天至未来三天。"
            "用户未说明城市时，使用上下文中的 Device location；位置未知则先询问城市。"
            "不得凭模型知识补充工具没有返回的天气事实。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "中国城市中文名，例如杭州。",
                },
                "province": {
                    "type": "string",
                    "description": "省级行政区中文名，例如浙江。可以省略。",
                },
                "date": {
                    "type": "string",
                    "description": "查询日期，格式 YYYY-MM-DD；省略时查询今天。",
                },
            },
            "required": ["city"],
        },
    },
}


@register_function(
    "get_xiaoxin_weather",
    GET_XIAOXIN_WEATHER_FUNCTION_DESC,
    ToolType.SYSTEM_CTL,
)
async def get_xiaoxin_weather(
    conn: "ConnectionHandler",
    city: str,
    province: str = "",
    date: str | None = None,
) -> ActionResponse:
    normalized_city = str(city or "").strip()
    normalized_province = str(province or "").strip()
    if not normalized_city:
        return ActionResponse(
            action=Action.REQLLM,
            result="天气查询缺少城市，请向用户询问城市。",
        )

    runtime = getattr(conn, "xiaoxin_control_runtime", None)
    service = getattr(runtime, "overview_service", None)
    query_daily_weather = getattr(service, "query_daily_weather", None)
    if not callable(query_daily_weather):
        return _unavailable_result(normalized_city)

    clock = getattr(runtime, "overview_clock", None) or local_datetime
    try:
        today_text = local_date_text(clock())
    except Exception:
        LOGGER.exception("Xiaoxin voice weather clock failed")
        return _unavailable_result(normalized_city)

    try:
        selected_date = str(date or today_text).strip()
        selected_day = _parse_supported_date(selected_date, today_text)
    except ValueError as exc:
        return ActionResponse(action=Action.REQLLM, result=str(exc))

    try:
        weather = await query_daily_weather(
            normalized_province,
            normalized_city,
            selected_day.isoformat(),
            device_id=str(getattr(conn, "device_id", "") or "") or None,
        )
        _validate_weather_response(
            weather,
            normalized_province,
            normalized_city,
            selected_day.isoformat(),
        )
    except Exception:
        LOGGER.exception(
            "Xiaoxin voice weather lookup failed for %s %s",
            normalized_province,
            normalized_city,
        )
        return _unavailable_result(normalized_city)

    place = f"{normalized_province}{normalized_city}"
    minimum = _temperature_text(weather.temperature_min_c)
    maximum = _temperature_text(weather.temperature_max_c)
    return ActionResponse(
        action=Action.REQLLM,
        result=(
            f"经高德天气核验：{place}，{weather.date}，{weather.weather_text}，"
            f"最低气温{minimum}℃，最高气温{maximum}℃。"
        ),
    )


def _parse_supported_date(selected_date: str, today_text: str) -> date:
    try:
        selected_day = date.fromisoformat(selected_date)
        today = date.fromisoformat(today_text)
    except ValueError as exc:
        raise ValueError("天气查询日期格式无效，请使用 YYYY-MM-DD。") from exc
    if selected_day < today or selected_day > today + timedelta(days=3):
        raise ValueError("天气数据仅支持查询今天至未来三天。")
    return selected_day


def _validate_weather_response(weather, province: str, city: str, date_text: str):
    if (
        weather is None
        or _normalized_weather_place(weather.city)
        != _normalized_weather_place(city)
        or weather.date != date_text
        or (
            province
            and _normalized_weather_place(weather.province)
            != _normalized_weather_place(province)
        )
    ):
        raise RuntimeError("weather response does not match request")


def _temperature_text(value: float) -> str:
    numeric = float(value)
    return str(int(numeric)) if numeric.is_integer() else f"{numeric:g}"


def _unavailable_result(city: str) -> ActionResponse:
    return ActionResponse(
        action=Action.REQLLM,
        result=f"{city}的天气数据暂时不可用，请如实告知用户，不要猜测。",
    )
