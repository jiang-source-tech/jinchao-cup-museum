import asyncio

from core.xiaoxin.control_types import XiaoxinDeliveryState, XiaoxinEvent
from core.xiaoxin.identity.store import XiaoxinIdentityStore
from core.xiaoxin.todo_reminder_scheduler import XiaoxinTodoReminderScheduler


class FakeDispatcher:
    def __init__(self, final_state=XiaoxinDeliveryState.DONE):
        self.submitted = []
        self.final_state = final_state
        self.records = {}
        self.store = self

    async def submit(self, request):
        self.submitted.append(request)
        delivery_id = f"del-{len(self.submitted)}"
        record = type(
            "Record",
            (),
            {"delivery_id": delivery_id, "state": self.final_state},
        )()
        self.records[delivery_id] = record
        return record

    async def wait_for_delivery_task(self, delivery_id):
        assert delivery_id in self.records

    def get(self, delivery_id):
        return self.records.get(delivery_id)


class SlowDispatcher(FakeDispatcher):
    async def submit(self, request):
        await asyncio.sleep(0.01)
        return await super().submit(request)


class BlockingDispatcher(FakeDispatcher):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()

    async def submit(self, request):
        self.submitted.append(request)
        self.started.set()
        await asyncio.sleep(60)


class PendingDispatcher(FakeDispatcher):
    def __init__(self):
        super().__init__(XiaoxinDeliveryState.RETRY_WAIT)

    async def wait_for_delivery_task(self, delivery_id):
        raise AssertionError("todo reminder scheduling must not wait for delivery")


def test_todo_reminder_scheduler_dispatches_due_bound_todos_once(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        store.upsert_seen_device("device-a", "桌面小芯")
        store.bind_device("device-a", user.id, "桌面小芯")
        due = store.create_student_todo(
            user.id,
            {
                "title": "带学生证",
                "dueAt": "2026-07-06T08:00:00+08:00",
                "notes": "进实验楼",
            },
        )
        store.create_student_todo(
            user.id,
            {"title": "晚上交报告", "dueAt": "2026-07-06T20:00:00+08:00"},
        )
        dispatcher = FakeDispatcher()
        scheduler = XiaoxinTodoReminderScheduler(store, dispatcher)

        first = await scheduler.dispatch_due_todos("2026-07-06T09:00:00+08:00")
        second = await scheduler.dispatch_due_todos("2026-07-06T09:05:00+08:00")

        return due, first, second, dispatcher.submitted, store.get_student_todo(user.id, due["id"])

    due, first, second, submitted, stored = asyncio.run(scenario())

    assert first == [{"todo_id": due["id"], "delivery_id": "del-1"}]
    assert second == []
    assert len(submitted) == 1
    request = submitted[0]
    assert request.device_id == "device-a"
    assert request.event == XiaoxinEvent.TODO_REMINDER
    assert request.title == "提醒事项"
    assert request.body == "带学生证"
    assert request.speak is True
    assert request.speak_text == "小芯提醒你，带学生证。"
    assert request.todo_title == "带学生证"
    assert request.due_at == "2026-07-06T08:00:00+08:00"
    assert stored["status"] == "pending"
    assert stored["reminder_status"] == "dispatched"
    assert stored["reminder_delivery_id"] == "del-1"
    assert stored["reminded_at"] == "2026-07-06T09:00:00+08:00"


def test_todo_reminder_scheduler_submits_once_without_waiting_for_delivery(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        store.upsert_seen_device("device-a", "desktop-xiaoxin")
        store.bind_device("device-a", user.id, "desktop-xiaoxin")
        due = store.create_student_todo(
            user.id,
            {
                "title": "pick up package",
                "dueAt": "2026-07-14T12:30:00+08:00",
            },
        )
        dispatcher = PendingDispatcher()
        scheduler = XiaoxinTodoReminderScheduler(
            store,
            dispatcher,
            replay_window_minutes=120,
        )

        first = await scheduler.dispatch_due_todos("2026-07-14T12:30:00+08:00")
        second = await scheduler.dispatch_due_todos("2026-07-14T12:31:00+08:00")

        return (
            due,
            first,
            second,
            dispatcher.submitted,
            store.get_student_todo(user.id, due["id"]),
        )

    due, first, second, submitted, stored = asyncio.run(scenario())

    assert first == [{"todo_id": due["id"], "delivery_id": "del-1"}]
    assert second == []
    assert len(submitted) == 1
    assert submitted[0].ttl_ms == 120 * 60 * 1000
    assert stored["status"] == "pending"
    assert stored["reminder_status"] == "dispatched"
    assert stored["reminder_delivery_id"] == "del-1"


def test_todo_reminder_scheduler_marks_reminder_missed_at_replay_deadline(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        store.upsert_seen_device("device-a", "desktop-xiaoxin")
        store.bind_device("device-a", user.id, "desktop-xiaoxin")
        due = store.create_student_todo(
            user.id,
            {
                "title": "pick up package",
                "dueAt": "2026-07-14T12:30:00+08:00",
            },
        )
        dispatcher = FakeDispatcher()
        scheduler = XiaoxinTodoReminderScheduler(
            store,
            dispatcher,
            replay_window_minutes=120,
        )

        first = await scheduler.dispatch_due_todos("2026-07-14T14:30:00+08:00")
        second = await scheduler.dispatch_due_todos("2026-07-14T15:00:00+08:00")

        return (
            due,
            first,
            second,
            dispatcher.submitted,
            store.get_student_todo(user.id, due["id"]),
        )

    due, first, second, submitted, stored = asyncio.run(scenario())

    assert first == []
    assert second == []
    assert submitted == []
    assert stored["status"] == "pending"
    assert stored["reminder_status"] == "missed"
    assert stored["reminded_at"] == "2026-07-14T14:30:00+08:00"
    assert stored["reminder_delivery_id"] == ""


def test_todo_reminder_scheduler_uses_remaining_replay_window_for_ttl(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        store.upsert_seen_device("device-a", "desktop-xiaoxin")
        store.bind_device("device-a", user.id, "desktop-xiaoxin")
        store.create_student_todo(
            user.id,
            {
                "title": "pick up package",
                "dueAt": "2026-07-14T12:30:00+08:00",
            },
        )
        dispatcher = FakeDispatcher()
        scheduler = XiaoxinTodoReminderScheduler(
            store,
            dispatcher,
            replay_window_minutes=120,
        )

        await scheduler.dispatch_due_todos("2026-07-14T14:29:00+08:00")
        return dispatcher.submitted[0]

    request = asyncio.run(scenario())

    assert request.ttl_ms == 60 * 1000


def test_todo_reminder_scheduler_claims_before_submit_to_prevent_duplicate_dispatch(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        store.upsert_seen_device("device-a", "桌面小芯")
        store.bind_device("device-a", user.id, "桌面小芯")
        due = store.create_student_todo(
            user.id,
            {"title": "带学生证", "dueAt": "2026-07-06T08:00:00+08:00"},
        )
        dispatcher = SlowDispatcher()
        scheduler = XiaoxinTodoReminderScheduler(store, dispatcher)

        first, second = await asyncio.gather(
            scheduler.dispatch_due_todos("2026-07-06T09:00:00+08:00"),
            scheduler.dispatch_due_todos("2026-07-06T09:00:00+08:00"),
        )

        return due, first, second, dispatcher.submitted, store.get_student_todo(user.id, due["id"])

    due, first, second, submitted, stored = asyncio.run(scenario())

    assert sorted(first + second, key=lambda item: item["todo_id"]) == [
        {"todo_id": due["id"], "delivery_id": "del-1"}
    ]
    assert len(submitted) == 1
    assert stored["reminder_delivery_id"] == "del-1"


def test_todo_reminder_scheduler_does_not_resubmit_after_delivery_later_fails(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        store.upsert_seen_device("device-a", "桌面小芯")
        store.bind_device("device-a", user.id, "桌面小芯")
        due = store.create_student_todo(
            user.id,
            {"title": "带学生证", "dueAt": "2026-07-06T08:00:00+08:00"},
        )
        dispatcher = FakeDispatcher(XiaoxinDeliveryState.FAILED)
        scheduler = XiaoxinTodoReminderScheduler(store, dispatcher)

        first = await scheduler.dispatch_due_todos("2026-07-06T09:00:00+08:00")
        second = await scheduler.dispatch_due_todos("2026-07-06T09:05:00+08:00")
        stored = store.get_student_todo(user.id, due["id"])
        return due, first, second, dispatcher.submitted, stored

    due, first, second, submitted, stored = asyncio.run(scenario())

    assert first == [{"todo_id": due["id"], "delivery_id": "del-1"}]
    assert second == []
    assert len(submitted) == 1
    assert stored["status"] == "pending"
    assert stored["reminder_status"] == "dispatched"
    assert stored["reminder_delivery_id"] == "del-1"


def test_todo_reminder_scheduler_releases_claim_when_submit_is_cancelled(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        store.upsert_seen_device("device-a", "妗岄潰灏忚姱")
        store.bind_device("device-a", user.id, "妗岄潰灏忚姱")
        due = store.create_student_todo(
            user.id,
            {"title": "bring student card", "dueAt": "2026-07-06T08:00:00+08:00"},
        )
        dispatcher = BlockingDispatcher()
        scheduler = XiaoxinTodoReminderScheduler(store, dispatcher)

        task = asyncio.create_task(
            scheduler.dispatch_due_todos("2026-07-06T09:00:00+08:00")
        )
        await asyncio.wait_for(dispatcher.started.wait(), timeout=0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        return store.get_student_todo(user.id, due["id"])

    stored = asyncio.run(scenario())

    assert stored["reminded_at"] == ""
    assert stored["reminder_delivery_id"] == ""


def test_todo_reminder_scheduler_preserves_cancellation_when_release_fails(
    tmp_path, monkeypatch
):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        store.upsert_seen_device("device-a", "妗岄潰灏忚姱")
        store.bind_device("device-a", user.id, "妗岄潰灏忚姱")
        store.create_student_todo(
            user.id,
            {"title": "bring student card", "dueAt": "2026-07-06T08:00:00+08:00"},
        )
        dispatcher = BlockingDispatcher()
        scheduler = XiaoxinTodoReminderScheduler(store, dispatcher)

        def fail_release(user_id, todo_id):
            raise RuntimeError("release failed")

        monkeypatch.setattr(
            store, "release_student_todo_reminder_claim", fail_release
        )

        task = asyncio.create_task(
            scheduler.dispatch_due_todos("2026-07-06T09:00:00+08:00")
        )
        await asyncio.wait_for(dispatcher.started.wait(), timeout=0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return "cancelled"
        except Exception as exc:
            return type(exc).__name__

        return "completed"

    assert asyncio.run(scenario()) == "cancelled"


def test_todo_reminder_scheduler_skips_unbound_due_todos_without_marking(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        due = store.create_student_todo(
            user.id,
            {"title": "带学生证", "dueAt": "2026-07-06T08:00:00+08:00"},
        )
        dispatcher = FakeDispatcher()
        scheduler = XiaoxinTodoReminderScheduler(store, dispatcher)

        dispatched = await scheduler.dispatch_due_todos("2026-07-06T09:00:00+08:00")

        return due, dispatched, dispatcher.submitted, store.get_student_todo(user.id, due["id"])

    due, dispatched, submitted, stored = asyncio.run(scenario())

    assert due["id"]
    assert dispatched == []
    assert submitted == []
    assert stored["reminder_delivery_id"] == ""
