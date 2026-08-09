import asyncio
from datetime import datetime
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import yaml

from core.providers.tools.server_plugins.plugin_executor import ServerPluginExecutor
from core.providers.intent.intent_llm.intent_llm import IntentProvider
from core.xiaoxin.control_types import XiaoxinEvent
from core.xiaoxin.identity.store import XiaoxinIdentityStore
from core.xiaoxin.semantic_router import is_existing_tool_turn
from core.xiaoxin.todo_reminder_scheduler import XiaoxinTodoReminderScheduler
from core.xiaoxin.voice_reminder import XiaoxinVoiceReminderCreator
from plugins_func.functions.create_xiaoxin_reminder import (
    CREATE_XIAOXIN_REMINDER_FUNCTION_DESC,
    create_xiaoxin_reminder,
)
from plugins_func.register import Action


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class RecordingOverviewService:
    def __init__(self):
        self.refreshes = []

    async def refresh_user_devices(self, user_id, reason):
        self.refreshes.append((user_id, reason))
        return []


class RecordingObservationIngress:
    def __init__(self):
        self.calls = []

    def observe_user_event(self, **kwargs):
        self.calls.append(kwargs)
        return None


class FailingOverviewService:
    async def refresh_user_devices(self, user_id, reason):
        raise RuntimeError("overview unavailable")


class BlockingOverviewService:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def refresh_user_devices(self, user_id, reason):
        self.started.set()
        await self.release.wait()
        return []


class RecordingDispatcher:
    def __init__(self):
        self.submitted = []

    async def submit(self, request):
        self.submitted.append(request)
        return SimpleNamespace(delivery_id=f"del-{len(self.submitted)}")


class NoopCache:
    def get(self, cache_type, key):
        return None

    def set(self, cache_type, key, value):
        return None


class CapturingIntentLlm:
    model_name = "capturing-intent-llm"

    def __init__(self):
        self.user_prompt = ""

    def response_no_stream(self, system_prompt, user_prompt):
        self.user_prompt = user_prompt
        return '{"function_call":{"name":"continue_chat"}}'


def test_bound_device_can_create_relative_voice_reminder(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        store.upsert_seen_device("device-a", "桌面小芯")
        store.bind_device("device-a", user.id, "桌面小芯")
        overview = RecordingOverviewService()
        observations = RecordingObservationIngress()
        creator = XiaoxinVoiceReminderCreator(
            store,
            overview,
            clock=lambda: datetime(2026, 7, 15, 10, 0, tzinfo=SHANGHAI_TZ),
            observation_ingress=observations,
        )

        result = await creator.create(
            device_id="device-a",
            title="喝水",
            delay_minutes=5,
        )
        await asyncio.sleep(0)

        return (
            result,
            store.list_student_todos(user.id),
            overview.refreshes,
            observations.calls,
            user.id,
        )

    result, todos, refreshes, observation_calls, user_id = asyncio.run(scenario())

    assert result.response == "好，5分钟后提醒你喝水。"
    assert result.todo == todos[0]
    assert todos[0]["title"] == "喝水"
    assert todos[0]["due_at"] == "2026-07-15T10:05:00+08:00"
    assert todos[0]["status"] == "pending"
    assert todos[0]["source"] == "voice"
    assert todos[0]["source_device_id"] == "device-a"
    assert refreshes == [(user_id, "voice_todo_created")]
    assert len(observation_calls) == 1
    assert observation_calls[0]["kind"] == "todo_created"
    assert observation_calls[0]["source_kind"] == "voice_todo"
    assert observation_calls[0]["payload"]["status"] == "pending"


def test_existing_todo_table_is_migrated_with_source_columns(tmp_path):
    db_path = tmp_path / "xiaoxin_control.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE student_todos (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                due_at TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                reminded_at TEXT NOT NULL DEFAULT '',
                reminder_delivery_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    XiaoxinIdentityStore(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(student_todos)")}
    assert {"source", "source_device_id"}.issubset(columns)


def test_voice_reminder_tool_creates_todo_and_returns_direct_confirmation(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        store.upsert_seen_device("device-a", "桌面小芯")
        store.bind_device("device-a", user.id, "桌面小芯")
        overview = RecordingOverviewService()
        runtime = SimpleNamespace(
            identity_store=store,
            overview_service=overview,
            overview_clock=lambda: datetime(2026, 7, 15, 10, 0, tzinfo=SHANGHAI_TZ),
        )
        conn = SimpleNamespace(
            device_id="device-a",
            xiaoxin_control_runtime=runtime,
        )

        response = await create_xiaoxin_reminder(
            conn,
            title="喝水",
            delay_minutes=5,
        )
        return response, store.list_student_todos(user.id)

    response, todos = asyncio.run(scenario())

    assert response.action == Action.RESPONSE
    assert response.response == "好，5分钟后提醒你喝水。"
    assert [todo["title"] for todo in todos] == ["喝水"]


def test_server_plugin_executor_awaits_voice_reminder_tool(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        store.upsert_seen_device("device-a", "桌面小芯")
        store.bind_device("device-a", user.id, "桌面小芯")
        conn = SimpleNamespace(
            device_id="device-a",
            config={
                "selected_module": {"Intent": "function_call"},
                "Intent": {
                    "function_call": {
                        "functions": ["create_xiaoxin_reminder"],
                    }
                },
            },
            xiaoxin_control_runtime=SimpleNamespace(
                identity_store=store,
                overview_service=RecordingOverviewService(),
                overview_clock=lambda: datetime(2026, 7, 15, 10, 0, tzinfo=SHANGHAI_TZ),
            ),
        )
        executor = ServerPluginExecutor(conn)
        assert "create_xiaoxin_reminder" in executor.get_tools()

        response = await executor.execute(
            conn,
            "create_xiaoxin_reminder",
            {"title": "喝水", "delay_minutes": 5},
        )
        return response, store.list_student_todos(user.id)

    response, todos = asyncio.run(scenario())

    assert response.action == Action.RESPONSE
    assert response.response == "好，5分钟后提醒你喝水。"
    assert len(todos) == 1


def test_voice_reminder_request_bypasses_xiaoxin_chat_runtime():
    assert is_existing_tool_turn("5分钟后提醒我喝水")
    assert is_existing_tool_turn("明天下午三点设置一个交实验报告的提醒")
    assert is_existing_tool_turn("周三 15:00 提醒我交实验报告")
    assert not is_existing_tool_turn("老师提醒我要交实验报告")
    assert not is_existing_tool_turn("为什么手机每天9点提醒我喝水")
    assert not is_existing_tool_turn("怎么设置喝水提醒")


@pytest.mark.parametrize(
    "phrase",
    [
        "提醒我一分钟之后喝水",
        "一分钟以后提醒我喝水",
        "过一分钟提醒我喝水",
        "再过一分钟叫我喝水",
        "待会儿通知我交实验报告",
        "提醒我喝水",
        "别忘了提醒我带伞",
        "记得提醒我下午开会",
    ],
)
def test_natural_reminder_phrases_bypass_xiaoxin_chat_runtime(phrase):
    assert is_existing_tool_turn(phrase)


@pytest.mark.parametrize(
    "phrase",
    [
        "老师提醒我要交实验报告",
        "手机每天九点提醒我喝水为什么",
        "怎么设置喝水提醒",
        "提醒我是什么意思",
    ],
)
def test_reminder_mentions_in_non_creation_requests_stay_in_chat(phrase):
    assert not is_existing_tool_turn(phrase)


def test_default_config_registers_voice_reminder_tool():
    with open("config.yaml", "r", encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)

    assert "create_xiaoxin_reminder" in config["Intent"]["function_call"]["functions"]
    assert "create_xiaoxin_reminder" in config["Intent"]["intent_llm"]["functions"]


def test_intent_llm_receives_current_time_for_absolute_reminder_parsing():
    async def scenario():
        provider = IntentProvider({})
        llm = CapturingIntentLlm()
        provider.llm = llm
        provider.cache_manager = NoopCache()
        conn = SimpleNamespace(
            device_id="device-a",
            func_handler=SimpleNamespace(
                get_functions=lambda: [CREATE_XIAOXIN_REMINDER_FUNCTION_DESC]
            ),
            config={"plugins": {}},
            dialogue=SimpleNamespace(dialogue=[]),
            logger=SimpleNamespace(
                bind=lambda **kwargs: SimpleNamespace(
                    debug=lambda *args, **kwargs: None,
                    info=lambda *args, **kwargs: None,
                    error=lambda *args, **kwargs: None,
                    warning=lambda *args, **kwargs: None,
                )
            ),
        )

        await provider.detect_intent(conn, [], "明天下午三点提醒我交报告")
        return llm.user_prompt

    user_prompt = asyncio.run(scenario())

    assert "当前时间：" in user_prompt
    assert "今天日期：" in user_prompt
    assert "今天星期：" in user_prompt


def test_bound_device_can_create_absolute_voice_reminder(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        store.upsert_seen_device("device-a", "桌面小芯")
        store.bind_device("device-a", user.id, "桌面小芯")
        creator = XiaoxinVoiceReminderCreator(
            store,
            RecordingOverviewService(),
            clock=lambda: datetime(2026, 7, 15, 10, 0, tzinfo=SHANGHAI_TZ),
        )

        result = await creator.create(
            device_id="device-a",
            title="交实验报告",
            due_at="2026-07-16T15:00:00+08:00",
        )
        return result, store.list_student_todos(user.id)

    result, todos = asyncio.run(scenario())

    assert result.response == "好，明天15点00分提醒你交实验报告。"
    assert todos[0]["due_at"] == "2026-07-16T15:00:00+08:00"


def test_relative_hour_confirmation_uses_hours_instead_of_raw_minutes(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        store.upsert_seen_device("device-a", "桌面小芯")
        store.bind_device("device-a", user.id, "桌面小芯")
        creator = XiaoxinVoiceReminderCreator(
            store,
            RecordingOverviewService(),
            clock=lambda: datetime(2026, 7, 15, 10, 0, tzinfo=SHANGHAI_TZ),
        )

        return await creator.create(
            device_id="device-a",
            title="取快递",
            delay_minutes=120,
        )

    result = asyncio.run(scenario())

    assert result.response == "好，2小时后提醒你取快递。"


def test_overview_refresh_failure_does_not_turn_created_reminder_into_failure(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        store.upsert_seen_device("device-a", "桌面小芯")
        store.bind_device("device-a", user.id, "桌面小芯")
        creator = XiaoxinVoiceReminderCreator(
            store,
            FailingOverviewService(),
            clock=lambda: datetime(2026, 7, 15, 10, 0, tzinfo=SHANGHAI_TZ),
        )

        result = await creator.create(
            device_id="device-a",
            title="喝水",
            delay_minutes=5,
        )
        return result, store.list_student_todos(user.id)

    result, todos = asyncio.run(scenario())

    assert result.response == "好，5分钟后提醒你喝水。"
    assert len(todos) == 1


def test_voice_confirmation_does_not_wait_for_overview_refresh(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        store.upsert_seen_device("device-a", "桌面小芯")
        store.bind_device("device-a", user.id, "桌面小芯")
        overview = BlockingOverviewService()
        creator = XiaoxinVoiceReminderCreator(
            store,
            overview,
            clock=lambda: datetime(2026, 7, 15, 10, 0, tzinfo=SHANGHAI_TZ),
        )

        result = await asyncio.wait_for(
            creator.create(
                device_id="device-a",
                title="喝水",
                delay_minutes=5,
            ),
            timeout=0.1,
        )
        await overview.started.wait()
        overview.release.set()
        await asyncio.sleep(0)
        return result

    result = asyncio.run(scenario())

    assert result.response == "好，5分钟后提醒你喝水。"


def test_unbound_device_cannot_create_voice_reminder(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        store.upsert_seen_device("device-a", "桌面小芯")
        conn = SimpleNamespace(
            device_id="device-a",
            xiaoxin_control_runtime=SimpleNamespace(
                identity_store=store,
                overview_service=RecordingOverviewService(),
                overview_clock=lambda: datetime(2026, 7, 15, 10, 0, tzinfo=SHANGHAI_TZ),
            ),
        )
        return await create_xiaoxin_reminder(
            conn,
            title="喝水",
            delay_minutes=5,
        )

    response = asyncio.run(scenario())

    assert response.action == Action.ERROR
    assert response.response == "这台小芯还没有绑定学生账号，绑定后才能创建提醒。"


@pytest.mark.parametrize(
    ("delay_minutes", "due_at"),
    [
        (None, None),
        (5, "2026-07-16T15:00:00+08:00"),
        (None, "2026-07-15T09:59:00+08:00"),
    ],
)
def test_invalid_voice_reminder_time_does_not_create_todo(
    tmp_path, delay_minutes, due_at
):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        store.upsert_seen_device("device-a", "桌面小芯")
        store.bind_device("device-a", user.id, "桌面小芯")
        creator = XiaoxinVoiceReminderCreator(
            store,
            RecordingOverviewService(),
            clock=lambda: datetime(2026, 7, 15, 10, 0, tzinfo=SHANGHAI_TZ),
        )

        with pytest.raises(ValueError, match="time"):
            await creator.create(
                device_id="device-a",
                title="喝水",
                delay_minutes=delay_minutes,
                due_at=due_at,
            )
        return store.list_student_todos(user.id)

    assert asyncio.run(scenario()) == []


def test_voice_created_todo_reaches_existing_reminder_dispatcher(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        store.upsert_seen_device("device-a", "桌面小芯")
        store.bind_device("device-a", user.id, "桌面小芯")
        creator = XiaoxinVoiceReminderCreator(
            store,
            RecordingOverviewService(),
            clock=lambda: datetime(2026, 7, 15, 10, 0, tzinfo=SHANGHAI_TZ),
        )
        await creator.create(
            device_id="device-a",
            title="喝水",
            delay_minutes=5,
        )
        dispatcher = RecordingDispatcher()
        scheduler = XiaoxinTodoReminderScheduler(store, dispatcher)

        dispatched = await scheduler.dispatch_due_todos("2026-07-15T10:05:00+08:00")
        return dispatched, dispatcher.submitted

    dispatched, submitted = asyncio.run(scenario())

    assert dispatched[0]["delivery_id"] == "del-1"
    assert len(submitted) == 1
    assert submitted[0].event == XiaoxinEvent.TODO_REMINDER
    assert submitted[0].body == "喝水"
    assert submitted[0].speak is True
    assert submitted[0].speak_text == "小芯提醒你，喝水。"
