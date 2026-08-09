from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_DEMO_DATA: dict[str, Any] = {
    "overview": {
        "weather": {
            "configured": True,
            "available": True,
            "summary": "多云 26°C",
            "detail": "湿度 72% · 东风 2级",
        },
        "course": {
            "configured": True,
            "available_today": True,
            "title": "高等数学 10:10",
            "detail": "3教204 · 还有24分钟",
        },
        "todo": {
            "configured": True,
            "count": 2,
            "detail": "实验报告 · 晚自习",
        },
    },
    "notifications": [
        {
            "id": "course-reminder-demo",
            "event": "course_reminder",
            "title": "上课提醒",
            "body": "高等数学 10:10 3教204",
            "tag": "课程",
            "priority": 1,
            "ttl_ms": 0,
            "speak": True,
            "speak_text": "小新提醒你，10:10有高等数学课，地点在三教二零四。",
            "course_name": "高等数学",
            "classroom": "3教204",
            "starts_at": "2026-07-05T10:10:00+08:00",
            "remind_before_min": 15,
        },
        {
            "id": "todo-reminder-demo",
            "event": "todo_reminder",
            "title": "待办提醒",
            "body": "实验报告今晚 18:00 前提交",
            "tag": "待办",
            "priority": 2,
            "ttl_ms": 0,
            "speak": True,
            "speak_text": "小新提醒你，实验报告今晚六点前提交。",
            "todo_title": "提交实验报告",
            "due_at": "2026-07-05T18:00:00+08:00",
        },
        {
            "id": "network-demo",
            "event": "notification",
            "title": "校园网状态",
            "body": "宿舍区 Wi-Fi 已恢复，设备重新联网成功。",
            "tag": "网络",
            "priority": 2,
            "ttl_ms": 0,
            "speak": False,
            "speak_text": "",
        },
        {
            "id": "battery-demo",
            "event": "notification",
            "title": "电量提醒",
            "body": "小新电量剩余 18%，建议放回底座充电。",
            "tag": "电量",
            "priority": 3,
            "ttl_ms": 0,
            "speak": True,
            "speak_text": "小新电量剩余百分之十八，建议放回底座充电。",
        },
        {
            "id": "system-update-demo",
            "event": "notification",
            "title": "系统更新",
            "body": "新版通知中心已就绪，课程、待办、网络、电量统一收纳。",
            "tag": "系统",
            "priority": 1,
            "ttl_ms": 0,
            "speak": False,
            "speak_text": "",
        }
    ],
}


class XiaoxinDemoDataStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return deepcopy(DEFAULT_DEMO_DATA)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return deepcopy(DEFAULT_DEMO_DATA)
        return normalize_demo_data(data)

    def save(self, data: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_demo_data(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.path)
        return normalized

    def notification(self, notification_id: str) -> dict[str, Any] | None:
        for notification in self.load()["notifications"]:
            if notification["id"] == notification_id:
                return notification
        return None


def normalize_demo_data(data: dict[str, Any]) -> dict[str, Any]:
    source = data if isinstance(data, dict) else {}
    default = deepcopy(DEFAULT_DEMO_DATA)
    overview = source.get("overview") if isinstance(source.get("overview"), dict) else {}
    notifications = source.get("notifications")
    return {
        "overview": {
            "weather": _normalize_weather(overview.get("weather"), default),
            "course": _normalize_course(overview.get("course"), default),
            "todo": _normalize_todo(overview.get("todo"), default),
        },
        "notifications": _normalize_notifications(notifications, default),
    }


def _normalize_weather(value: Any, default: dict[str, Any]) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    fallback = default["overview"]["weather"]
    return {
        "configured": bool(source.get("configured", fallback["configured"])),
        "available": bool(source.get("available", fallback["available"])),
        "summary": _string(source.get("summary", fallback["summary"])),
        "detail": _string(source.get("detail", fallback["detail"])),
    }


def _normalize_course(value: Any, default: dict[str, Any]) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    fallback = default["overview"]["course"]
    return {
        "configured": bool(source.get("configured", fallback["configured"])),
        "available_today": bool(
            source.get("available_today", fallback["available_today"])
        ),
        "title": _string(source.get("title", fallback["title"])),
        "detail": _string(source.get("detail", fallback["detail"])),
    }


def _normalize_todo(value: Any, default: dict[str, Any]) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    fallback = default["overview"]["todo"]
    return {
        "configured": bool(source.get("configured", fallback["configured"])),
        "count": _int_range(source.get("count", fallback["count"]), 0, 99),
        "detail": _string(source.get("detail", fallback["detail"])),
    }


def _normalize_notifications(value: Any, default: dict[str, Any]) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else default["notifications"]
    normalized = []
    for index, item in enumerate(items[:20]):
        if not isinstance(item, dict):
            continue
        event = _event(item.get("event", "notification"))
        notification = {
            "id": _string(item.get("id")) or f"notification-{index + 1}",
            "event": event,
            "title": _string(item.get("title")) or "提醒",
            "body": _string(item.get("body")) or "提醒内容",
            "tag": _string(item.get("tag")),
            "priority": _int_range(item.get("priority", 2), 0, 4),
            "ttl_ms": _int_range(item.get("ttl_ms", 0), 0, 24 * 60 * 60 * 1000),
            "speak": bool(item.get("speak", False)),
            "speak_text": _string(item.get("speak_text")),
            "course_name": _string(item.get("course_name")),
            "classroom": _string(item.get("classroom")),
            "starts_at": _string(item.get("starts_at")),
            "remind_before_min": _optional_int(item.get("remind_before_min"), 0, 10080),
            "todo_title": _string(item.get("todo_title")),
            "due_at": _string(item.get("due_at")),
        }
        if notification["speak"] and not notification["speak_text"]:
            notification["speak_text"] = notification["body"]
        normalized.append(notification)
    return normalized or deepcopy(default["notifications"])


def _event(value: Any) -> str:
    event = _string(value)
    if event in {"notification", "course_reminder", "todo_reminder"}:
        return event
    return "notification"


def _string(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _optional_int(value: Any, minimum: int, maximum: int) -> int | None:
    if value in (None, ""):
        return None
    return _int_range(value, minimum, maximum)


def _int_range(value: Any, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return minimum
    return max(minimum, min(maximum, number))
