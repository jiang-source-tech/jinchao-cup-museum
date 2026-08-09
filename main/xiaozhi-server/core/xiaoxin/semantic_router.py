from __future__ import annotations

import json
import re
from typing import Any

from . import boundary_guard as guard


DEFAULT_ROUTE = {
    "intent": "open_chat",
    "focus": None,
    "mentioned_not_focus": [],
    "knowledge_domains": [],
    "reply_mode": "free_chat",
    "reason": "",
    "source": "fallback",
}


def is_existing_tool_turn(user_text: str) -> bool:
    text = user_text or ""
    if _is_boundary_contact_request(text):
        return False
    if _is_reminder_creation_request(text):
        return True
    if _matches_registered_tool_category(text):
        return True
    tool_markers = (
        "拜拜",
        "再见",
        "晚安",
        "退出",
        "待机",
        "放一首歌",
        "播放音乐",
        "来首歌",
        "开灯",
        "关灯",
        "灯光控制",
        "把灯关掉",
        "空调",
        "音量调",
        "声音调",
        "天气",
        "新闻",
    )
    boundary_not_tools = (
        "联系老师",
        "联系学长",
        "联系学姐",
        "要电话",
        "源文件",
        "帮我问",
    )
    if any(marker in text for marker in boundary_not_tools):
        return False
    return any(marker in text for marker in tool_markers)


def _is_reminder_creation_request(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text or "")
    if not normalized:
        return False

    if any(
        marker in normalized
        for marker in (
            "为什么",
            "为何",
            "怎么",
            "如何",
            "什么意思",
            "是什么",
            "什么时候",
        )
    ):
        return False

    # A statement about another person or application reminding the user is
    # not a request for Xiaoxin to create a reminder.
    if re.search(
        r"(?:老师|辅导员|妈妈|爸爸|同学|朋友|他|她|手机|系统|闹钟|日历|应用)"
        r".{0,10}提醒(?:一下)?我",
        normalized,
    ):
        return False

    if "提醒" in normalized and any(
        marker in normalized for marker in ("设置", "创建", "添加", "加个", "设个")
    ):
        return True

    # Explicit first-person reminder requests should enter the tool-capable
    # flow even when the time is missing or phrased in an unfamiliar way. The
    # intent model can parse the time or ask a clarification question.
    if re.search(r"提醒(?:一下)?我", normalized):
        return True
    if any(marker in normalized for marker in ("帮我提醒", "记得提醒", "别忘了提醒")):
        return True

    # Natural alternatives such as "过一分钟叫我" and "待会儿通知我" are
    # reminder requests only when they also contain a time expression.
    alternate_actions = ("叫我", "通知我", "提示我")
    if any(action in normalized for action in alternate_actions):
        return _has_reminder_time_expression(normalized)
    return False


def _has_reminder_time_expression(text: str) -> bool:
    if re.search(
        r"(?:再?过)?[零〇一二两三四五六七八九十百千万\d]+"
        r"(?:秒钟?|分钟|小时|天|周|个月)"
        r"(?:后|之后|以后)?",
        text,
    ):
        return True
    if re.search(r"(?:周|星期|礼拜)[一二三四五六日天1-7]", text):
        return True
    return any(
        marker in text
        for marker in (
            "今天",
            "明天",
            "后天",
            "大后天",
            "今晚",
            "明早",
            "早上",
            "上午",
            "中午",
            "下午",
            "晚上",
            "待会",
            "等会",
            "稍后",
            "过会",
            "一会",
            "点",
            "时候",
        )
    )


def _matches_registered_tool_category(text: str) -> bool:
    tool_markers = (
        "拜拜",
        "再见",
        "晚安",
        "退出",
        "结束对话",
        "待机",
        "播放",
        "放一首",
        "来首歌",
        "来点音乐",
        "音乐",
        "歌曲",
        "开灯",
        "关灯",
        "打开灯",
        "关闭灯",
        "把灯打开",
        "把灯关",
        "客厅灯",
        "卧室灯",
        "台灯",
        "空调",
        "音量",
        "声音",
        "静音",
        "亮度",
        "色温",
        "天气",
        "新闻",
        "课表",
        "课程安排",
        "今天有什么课",
        "明天有什么课",
        "待办",
        "通知记录",
        "搜索",
        "查一个",
        "联网搜",
        "现在几点",
        "几点了",
        "农历",
        "阴历",
        "黄历",
        "日历",
        "今天几号",
        "日期",
        "切换角色",
        "换成",
        "角色",
        "呼叫",
        "打电话给",
        "连线",
        "打给",
        "接听",
        "接通",
        "拒接",
        "挂断",
    )
    return any(marker in text for marker in tool_markers)


def _is_boundary_contact_request(text: str) -> bool:
    sensitive_people = (
        "老师",
        "辅导员",
        "学长",
        "学姐",
        "同学",
        "负责人",
        "教师",
    )
    contact_actions = (
        "帮我联系",
        "替我联系",
        "联系一个",
        "要电话",
        "拿电话",
        "要联系方式",
        "拿联系方式",
        "私人联系方式",
        "打电话给",
        "呼叫",
        "连线",
    )
    return any(person in text for person in sensitive_people) and any(
        action in text for action in contact_actions
    )


def normalize_route(data: dict[str, Any] | None) -> dict[str, Any]:
    route = dict(DEFAULT_ROUTE)
    route.update(data or {})
    if not isinstance(route.get("mentioned_not_focus"), list):
        route["mentioned_not_focus"] = []
    if not isinstance(route.get("knowledge_domains"), list):
        route["knowledge_domains"] = []
    if route.get("reply_mode") not in {
        "hard_template",
        "knowledge_grounded",
        "free_chat",
        "message_drafting",
    }:
        route["reply_mode"] = "free_chat"
    if route.get("focus") in ("", [], {}):
        route["focus"] = None
    if not route.get("intent"):
        route["intent"] = "open_chat"
    return route


def fallback_route(user_text: str, reason: str = "fallback") -> dict[str, Any]:
    category = guard.classify_message(user_text)
    if category in {"official_contact", "competition_resources", "crisis"}:
        reply_mode = "hard_template"
        knowledge_domains: list[str] = []
    elif category == "campus_knowledge":
        reply_mode = "knowledge_grounded"
        knowledge_domains = ["campus_directory", "student_affairs", "campus_life"]
    elif category == "message_drafting":
        reply_mode = "message_drafting"
        knowledge_domains = []
    else:
        reply_mode = "free_chat"
        knowledge_domains = []
    return normalize_route(
        {
            "intent": category,
            "reply_mode": reply_mode,
            "knowledge_domains": knowledge_domains,
            "reason": reason,
            "source": "fallback",
        }
    )


def route_message(
    user_text: str,
    history: list[dict],
    client=None,
    model: str | None = None,
) -> dict[str, Any]:
    category = guard.classify_message(user_text)
    if category in {"official_contact", "competition_resources", "crisis"}:
        return normalize_route(
            {
                "intent": category,
                "reply_mode": "hard_template",
                "knowledge_domains": [],
                "reason": "hard boundary category",
                "source": "hard_boundary",
            }
        )
    if client is None or model is None:
        return fallback_route(user_text, reason="router_unavailable")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=_router_messages(user_text, history),
            temperature=0,
            max_tokens=220,
        )
        content = response.choices[0].message.content
        parsed = _parse_route_content(content)
        if parsed is not None:
            return normalize_route(parsed)
    except Exception as exc:
        return fallback_route(user_text, reason=f"router_failed:{exc}")
    return fallback_route(user_text, reason="router_unparseable")


def _router_messages(user_text: str, history: list[dict]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "你是消息路由器。只输出 JSON 对象，字段包括 "
                "intent, focus, mentioned_not_focus, knowledge_domains, reply_mode, reason, source。"
                "reply_mode 只能是 hard_template、knowledge_grounded、free_chat、message_drafting。"
                "如果用户是在让助手帮他起草自己发送的消息、短信、邮件、申请文本，使用 message_drafting；"
                "如果用户是在要求你代为联系老师、索要电话或私人联系方式，使用 hard_template。"
            ),
        }
    ]

    for turn in history[-4:]:
        role = turn.get("role")
        content = turn.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_text or ""})
    return messages


def _parse_route_content(content: Any) -> dict[str, Any] | None:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None
