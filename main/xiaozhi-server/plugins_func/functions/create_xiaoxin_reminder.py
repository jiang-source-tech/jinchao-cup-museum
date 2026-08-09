from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.xiaoxin.local_time import local_datetime
from core.xiaoxin.voice_reminder import XiaoxinVoiceReminderCreator
from plugins_func.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from core.connection import ConnectionHandler


LOGGER = logging.getLogger(__name__)


CREATE_XIAOXIN_REMINDER_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "create_xiaoxin_reminder",
        "description": (
            "为当前小芯设备绑定的学生创建提醒事项。"
            "用户表达‘几分钟后提醒我做某事’时必须调用。"
            "相对时间优先使用 delay_minutes，绝对日期或钟点使用 due_at。"
            "五分钟后：title=喝水, delay_minutes=5；"
            "两小时后：delay_minutes=120；"
            "今天、明天或周几的具体钟点：根据系统提供的当前日期计算最近一个未来时间，"
            "due_at 必须带 +08:00 时区。"
            "title 只保留事项本身，不要包含‘提醒我’‘设置提醒’等指令词。"
            "工具成功后会直接回复创建结果，不要在调用前二次确认。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "要提醒的具体事项，例如‘喝水’。",
                },
                "delay_minutes": {
                    "type": "number",
                    "description": (
                        "从当前时间开始计算的分钟数。"
                        "例如五分钟后填 5，两小时后填 120。"
                    ),
                },
                "due_at": {
                    "type": "string",
                    "description": (
                        "带时区的 ISO 8601 绝对时间，例如 "
                        "2026-07-15T15:30:00+08:00。"
                    ),
                },
            },
            "required": ["title"],
        },
    },
}


@register_function(
    "create_xiaoxin_reminder",
    CREATE_XIAOXIN_REMINDER_FUNCTION_DESC,
    ToolType.SYSTEM_CTL,
)
async def create_xiaoxin_reminder(
    conn: "ConnectionHandler",
    title: str,
    delay_minutes: float | None = None,
    due_at: str | None = None,
) -> ActionResponse:
    runtime = getattr(conn, "xiaoxin_control_runtime", None)
    if runtime is None:
        return ActionResponse(
            action=Action.ERROR,
            response="提醒服务暂时不可用，请稍后再试。",
        )

    creator = XiaoxinVoiceReminderCreator(
        runtime.identity_store,
        runtime.overview_service,
        clock=getattr(runtime, "overview_clock", local_datetime),
        observation_ingress=getattr(runtime, "observation_ingress", None),
    )
    try:
        result = await creator.create(
            device_id=str(getattr(conn, "device_id", "") or ""),
            title=title,
            delay_minutes=delay_minutes,
            due_at=due_at,
        )
    except ValueError as exc:
        return ActionResponse(action=Action.ERROR, response=_user_error(str(exc)))
    except Exception:
        LOGGER.exception("Xiaoxin voice reminder creation failed")
        return ActionResponse(
            action=Action.ERROR,
            response="提醒没有创建成功，请稍后再试。",
        )

    return ActionResponse(action=Action.RESPONSE, response=result.response)


def _user_error(message: str) -> str:
    if "bound" in message:
        return "这台小芯还没有绑定学生账号，绑定后才能创建提醒。"
    if "time" in message or "delay" in message:
        return "提醒时间没有听清，请说一个未来的明确时间。"
    if "title" in message:
        return "提醒事项没有听清，请再说一遍要提醒什么。"
    return "提醒没有创建成功，请再说一遍。"
