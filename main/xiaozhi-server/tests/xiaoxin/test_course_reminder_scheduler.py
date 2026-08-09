import asyncio

from core.xiaoxin.control_types import XiaoxinEvent
from core.xiaoxin.course_reminder_scheduler import XiaoxinCourseReminderScheduler
from core.xiaoxin.identity.store import XiaoxinIdentityStore


class FakeDispatcher:
    def __init__(self):
        self.submitted = []

    async def submit(self, request):
        self.submitted.append(request)
        return type("Record", (), {"delivery_id": f"del-{len(self.submitted)}"})()


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


def _seed_bound_user_with_semester(store):
    user = store.create_user("liu", "hash-value", "Liu")
    store.upsert_seen_device("device-a", "Device A")
    store.bind_device("device-a", user.id, "Device A")
    store.update_student_semester(
        user.id,
        {
            "label": "2026 spring",
            "startDate": "2026-03-02",
            "totalWeeks": 18,
        },
    )
    return user


def test_course_reminder_scheduler_dispatches_due_bound_course_once_per_occurrence(
    tmp_path,
):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = _seed_bound_user_with_semester(store)
        course = store.create_student_course(
            user.id,
            {
                "title": "Linear Algebra",
                "classroom": "Room 204",
                "teacher": "Wang",
                "weekday": 1,
                "startSection": 1,
                "endSection": 2,
                "weekRange": "1-18",
                "startsAt": "08:00",
                "endsAt": "09:35",
            },
        )
        dispatcher = FakeDispatcher()
        scheduler = XiaoxinCourseReminderScheduler(store, dispatcher)

        before = await scheduler.dispatch_due_courses("2026-03-09T07:44:00+08:00")
        first = await scheduler.dispatch_due_courses("2026-03-09T07:45:00+08:00")
        second = await scheduler.dispatch_due_courses("2026-03-09T08:05:00+08:00")
        next_week = await scheduler.dispatch_due_courses("2026-03-16T07:45:00+08:00")

        stored = store.get_student_course(user.id, course["id"])
        return before, first, second, next_week, dispatcher.submitted, stored

    before, first, second, next_week, submitted, stored = asyncio.run(scenario())

    assert before == []
    assert first == [
        {
            "course_id": stored["id"],
            "delivery_id": "del-1",
            "occurrence_at": "2026-03-09T08:00:00+08:00",
        }
    ]
    assert second == []
    assert next_week == [
        {
            "course_id": stored["id"],
            "delivery_id": "del-2",
            "occurrence_at": "2026-03-16T08:00:00+08:00",
        }
    ]
    assert len(submitted) == 2
    request = submitted[0]
    assert request.device_id == "device-a"
    assert request.event == XiaoxinEvent.COURSE_REMINDER
    assert request.title == "上课提醒"
    assert request.course_name == "Linear Algebra"
    assert request.classroom == "Room 204"
    assert request.starts_at == "08:00"
    assert request.remind_before_min == 15
    assert request.body == "Linear Algebra 08:00 Room 204"
    assert request.speak_text == "小芯提醒你，08:00有Linear Algebra课，地点在Room 204。"
    assert request.ttl_ms == 15 * 60 * 1000
    assert stored["reminded_at"] == "2026-03-16T08:00:00+08:00"
    assert stored["reminder_delivery_id"] == "del-2"


def test_course_reminder_scheduler_uses_student_global_reminder_lead_time(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = _seed_bound_user_with_semester(store)
        course = store.create_student_course(
            user.id,
            {
                "title": "Linear Algebra",
                "weekday": 1,
                "startSection": 1,
                "endSection": 2,
                "weekRange": "1-18",
                "startsAt": "08:00",
            },
        )
        store.update_student_course_reminder_settings(
            user.id, {"remindBeforeMin": 30}
        )
        dispatcher = FakeDispatcher()
        scheduler = XiaoxinCourseReminderScheduler(store, dispatcher)

        before = await scheduler.dispatch_due_courses("2026-03-09T07:29:00+08:00")
        due = await scheduler.dispatch_due_courses("2026-03-09T07:30:00+08:00")

        return course, before, due, dispatcher.submitted

    course, before, due, submitted = asyncio.run(scenario())

    assert before == []
    assert due[0]["course_id"] == course["id"]
    assert due[0]["occurrence_at"] == "2026-03-09T08:00:00+08:00"
    assert submitted[0].remind_before_min == 30
    assert submitted[0].body == "Linear Algebra 08:00"


def test_course_reminder_uses_absolute_time_when_dispatch_is_late(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = _seed_bound_user_with_semester(store)
        store.create_student_course(
            user.id,
            {
                "title": "大学英语",
                "classroom": "教一302",
                "weekday": 1,
                "startSection": 3,
                "endSection": 4,
                "weekRange": "1-18",
                "startsAt": "10:05",
                "endsAt": "11:45",
            },
        )
        dispatcher = FakeDispatcher()
        scheduler = XiaoxinCourseReminderScheduler(store, dispatcher)

        await scheduler.dispatch_due_courses("2026-03-09T10:00:00+08:00")

        return dispatcher.submitted[0]

    request = asyncio.run(scenario())

    assert request.body == "大学英语 10:05 教一302"
    assert "分钟后" not in request.body
    assert request.speak_text == "小芯提醒你，10:05有大学英语课，地点在教一302。"
    assert request.ttl_ms == 5 * 60 * 1000


def test_course_reminder_does_not_dispatch_after_course_start(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = _seed_bound_user_with_semester(store)
        store.create_student_course(
            user.id,
            {
                "title": "大学英语",
                "classroom": "教一302",
                "weekday": 1,
                "startSection": 3,
                "endSection": 4,
                "weekRange": "1-18",
                "startsAt": "10:00",
                "endsAt": "11:45",
            },
        )
        dispatcher = FakeDispatcher()
        scheduler = XiaoxinCourseReminderScheduler(store, dispatcher)

        dispatched = await scheduler.dispatch_due_courses(
            "2026-03-09T10:05:00+08:00"
        )

        return dispatched, dispatcher.submitted

    dispatched, submitted = asyncio.run(scenario())

    assert dispatched == []
    assert submitted == []


def test_course_reminder_does_not_dispatch_after_course_has_ended(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = _seed_bound_user_with_semester(store)
        store.create_student_course(
            user.id,
            {
                "title": "大学英语",
                "classroom": "教一302",
                "weekday": 1,
                "startSection": 3,
                "endSection": 4,
                "weekRange": "1-18",
                "startsAt": "10:05",
                "endsAt": "11:45",
            },
        )
        dispatcher = FakeDispatcher()
        scheduler = XiaoxinCourseReminderScheduler(store, dispatcher)

        dispatched = await scheduler.dispatch_due_courses(
            "2026-03-09T11:46:00+08:00"
        )

        return dispatched, dispatcher.submitted

    dispatched, submitted = asyncio.run(scenario())

    assert dispatched == []
    assert submitted == []


def test_course_reminder_scheduler_dispatches_next_day_course_before_midnight(
    tmp_path,
):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = _seed_bound_user_with_semester(store)
        course = store.create_student_course(
            user.id,
            {
                "title": "Late Lab",
                "weekday": 2,
                "startSection": 1,
                "endSection": 2,
                "weekRange": "1-18",
                "startsAt": "00:10",
            },
        )
        dispatcher = FakeDispatcher()
        scheduler = XiaoxinCourseReminderScheduler(store, dispatcher)

        before = await scheduler.dispatch_due_courses("2026-03-09T23:54:00+08:00")
        due = await scheduler.dispatch_due_courses("2026-03-09T23:55:00+08:00")

        return course, before, due, dispatcher.submitted

    course, before, due, submitted = asyncio.run(scenario())

    assert before == []
    assert due[0]["course_id"] == course["id"]
    assert due[0]["occurrence_at"] == "2026-03-10T00:10:00+08:00"
    assert submitted[0].remind_before_min == 15
    assert submitted[0].ttl_ms == 15 * 60 * 1000


def test_course_reminder_without_end_time_expires_at_course_start(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = _seed_bound_user_with_semester(store)
        store.create_student_course(
            user.id,
            {
                "title": "Linear Algebra",
                "weekday": 1,
                "startSection": 1,
                "endSection": 2,
                "weekRange": "1-18",
                "startsAt": "08:00",
            },
        )
        dispatcher = FakeDispatcher()
        scheduler = XiaoxinCourseReminderScheduler(store, dispatcher)

        dispatched = await scheduler.dispatch_due_courses(
            "2026-03-09T08:30:00+08:00"
        )

        return dispatched, dispatcher.submitted

    dispatched, submitted = asyncio.run(scenario())

    assert dispatched == []
    assert submitted == []


def test_course_reminder_scheduler_claims_before_submit_to_prevent_duplicates(
    tmp_path,
):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = _seed_bound_user_with_semester(store)
        course = store.create_student_course(
            user.id,
            {
                "title": "Linear Algebra",
                "weekday": 1,
                "startSection": 1,
                "endSection": 2,
                "weekRange": "1-18",
                "startsAt": "08:00",
            },
        )
        dispatcher = SlowDispatcher()
        scheduler = XiaoxinCourseReminderScheduler(store, dispatcher)

        first, second = await asyncio.gather(
            scheduler.dispatch_due_courses("2026-03-09T07:45:00+08:00"),
            scheduler.dispatch_due_courses("2026-03-09T07:45:00+08:00"),
        )

        return course, first, second, dispatcher.submitted

    course, first, second, submitted = asyncio.run(scenario())

    assert sorted(first + second, key=lambda item: item["course_id"]) == [
        {
            "course_id": course["id"],
            "delivery_id": "del-1",
            "occurrence_at": "2026-03-09T08:00:00+08:00",
        }
    ]
    assert len(submitted) == 1


def test_course_reminder_scheduler_releases_claim_when_submit_is_cancelled(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = _seed_bound_user_with_semester(store)
        course = store.create_student_course(
            user.id,
            {
                "title": "Linear Algebra",
                "weekday": 1,
                "startSection": 1,
                "endSection": 2,
                "weekRange": "1-18",
                "startsAt": "08:00",
            },
        )
        dispatcher = BlockingDispatcher()
        scheduler = XiaoxinCourseReminderScheduler(store, dispatcher)

        task = asyncio.create_task(
            scheduler.dispatch_due_courses("2026-03-09T07:45:00+08:00")
        )
        await asyncio.wait_for(dispatcher.started.wait(), timeout=0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        return store.get_student_course(user.id, course["id"])

    stored = asyncio.run(scenario())

    assert stored["reminded_at"] == ""
    assert stored["reminder_delivery_id"] == ""


def test_course_reminder_scheduler_uses_default_semester_when_not_persisted(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        store.upsert_seen_device("device-a", "Device A")
        store.bind_device("device-a", user.id, "Device A")
        course = store.create_student_course(
            user.id,
            {
                "title": "Linear Algebra",
                "weekday": 1,
                "startSection": 1,
                "endSection": 2,
                "weekRange": "1-18",
                "startsAt": "08:00",
            },
        )
        dispatcher = FakeDispatcher()
        scheduler = XiaoxinCourseReminderScheduler(store, dispatcher)

        dispatched = await scheduler.dispatch_due_courses(
            "2025-09-01T07:45:00+08:00"
        )

        return course, dispatched

    course, dispatched = asyncio.run(scenario())

    assert dispatched == [
        {
                "course_id": course["id"],
                "delivery_id": "del-1",
                "occurrence_at": "2025-09-01T08:00:00+08:00",
            }
        ]


def test_course_reminder_scheduler_accepts_em_dash_week_range(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = _seed_bound_user_with_semester(store)
        course = store.create_student_course(
            user.id,
            {
                "title": "Linear Algebra",
                "weekday": 1,
                "startSection": 1,
                "endSection": 2,
                "weekRange": "第1—18周",
                "startsAt": "08:00",
            },
        )
        dispatcher = FakeDispatcher()
        scheduler = XiaoxinCourseReminderScheduler(store, dispatcher)

        dispatched = await scheduler.dispatch_due_courses(
            "2026-03-09T07:45:00+08:00"
        )

        return course, dispatched

    course, dispatched = asyncio.run(scenario())

    assert dispatched == [
        {
            "course_id": course["id"],
            "delivery_id": "del-1",
            "occurrence_at": "2026-03-09T08:00:00+08:00",
        }
    ]


def test_student_course_update_resets_reminder_marker(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = _seed_bound_user_with_semester(store)
        course = store.create_student_course(
            user.id,
            {
                "title": "Linear Algebra",
                "weekday": 1,
                "startSection": 1,
                "endSection": 2,
                "weekRange": "1-18",
                "startsAt": "08:00",
            },
        )
        scheduler = XiaoxinCourseReminderScheduler(store, FakeDispatcher())

        await scheduler.dispatch_due_courses("2026-03-09T07:45:00+08:00")
        updated = store.update_student_course(
            user.id,
            course["id"],
            {
                "title": "Linear Algebra",
                "weekday": 1,
                "startSection": 1,
                "endSection": 2,
                "weekRange": "1-18",
                "startsAt": "09:00",
            },
        )
        return updated

    updated = asyncio.run(scenario())

    assert updated["reminded_at"] == ""
    assert updated["reminder_delivery_id"] == ""


def test_course_reminder_scheduler_skips_unbound_courses_without_marking(tmp_path):
    async def scenario():
        store = XiaoxinIdentityStore(tmp_path / "xiaoxin_control.db")
        user = store.create_user("liu", "hash-value", "Liu")
        store.update_student_semester(
            user.id,
            {"label": "2026 spring", "startDate": "2026-03-02", "totalWeeks": 18},
        )
        course = store.create_student_course(
            user.id,
            {
                "title": "Linear Algebra",
                "weekday": 1,
                "startSection": 1,
                "endSection": 2,
                "weekRange": "1-18",
                "startsAt": "08:00",
            },
        )
        dispatcher = FakeDispatcher()
        scheduler = XiaoxinCourseReminderScheduler(store, dispatcher)

        dispatched = await scheduler.dispatch_due_courses(
            "2026-03-09T07:45:00+08:00"
        )

        return dispatched, dispatcher.submitted, store.get_student_course(user.id, course["id"])

    dispatched, submitted, stored = asyncio.run(scenario())

    assert dispatched == []
    assert submitted == []
    assert stored["reminded_at"] == ""
    assert stored["reminder_delivery_id"] == ""
