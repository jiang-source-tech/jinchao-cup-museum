"""Synthetic timelines for the throwaway relationship-stage prototype."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from typing import Callable
from zoneinfo import ZoneInfo

from relationship_model import RelationshipEvent, RelationshipTimeline


LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class Check:
    label: str
    actual: object
    expected: object

    @property
    def passed(self) -> bool:
        return self.actual == self.expected


@dataclass(frozen=True)
class ScenarioRun:
    final_state: dict[str, object]
    timeline: tuple[dict[str, object], ...]
    checks: tuple[Check, ...]

    def canonical_json(self) -> str:
        return json.dumps(
            {
                "final_state": self.final_state,
                "timeline": self.timeline,
                "checks": [
                    {
                        "label": item.label,
                        "actual": item.actual,
                        "expected": item.expected,
                        "passed": item.passed,
                    }
                    for item in self.checks
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    title: str
    lesson: str
    run: Callable[[], ScenarioRun]


def _at(start: datetime, *, days: int = 0, hour: int = 9, minute: int = 0) -> str:
    return (start + timedelta(days=days)).replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    ).isoformat()


def _month_at(start_year: int, start_month: int, offset: int, day: int) -> str:
    absolute = start_year * 12 + start_month - 1 + offset
    year, month_index = divmod(absolute, 12)
    return datetime(
        year,
        month_index + 1,
        day,
        9,
        tzinfo=LOCAL_TIMEZONE,
    ).isoformat()


def _event(
    engine: RelationshipTimeline,
    event_id: str,
    occurred_at: str,
    kind: str,
    *,
    device_id: str = "device-a",
    key: str = "",
    turn_weight: int = 1,
) -> None:
    engine.observe(
        RelationshipEvent(
            event_id=event_id,
            occurred_at=occurred_at,
            kind=kind,
            device_id=device_id,
            key=key,
            turn_weight=turn_weight,
        )
    )


def _point(engine: RelationshipTimeline, label: str, now: str) -> dict[str, object]:
    state = engine.state(now)
    return {
        "label": label,
        "now": state["now"],
        "stage": state["stage"],
        "legacy_stage": state["legacy_stage"],
        "posture": state["posture"],
        "reunion_step": state["reunion_step"],
        "metrics": state["metrics"],
        "projection": state["projection"],
        "next_stage": state["next_stage"],
        "next_gate_missing": state["next_gate_missing"],
        "devices_seen": state["devices_seen"],
        "implicit_adjustments": state["implicit_adjustments"],
    }


def _seed_high_frequency(
    engine: RelationshipTimeline,
    *,
    start: datetime,
    through_day: int,
) -> None:
    early_quality_days = {1, 3, 5, 7, 9, 11}
    later_help_days = {30, 60, 120, 180, 240, 300}
    later_attunement_days = {60, 150, 240, 330}
    for day in range(through_day + 1):
        for turn in range(2):
            _event(
                engine,
                f"daily-{day}-turn-{turn}",
                _at(start, days=day, minute=turn),
                "interaction",
            )
        if day < 12:
            _event(
                engine,
                f"knowledge-{day}",
                _at(start, days=day, hour=10),
                "knowledge",
                key=f"fact-{day}",
            )
        if day in early_quality_days or day in later_help_days:
            _event(
                engine,
                f"helpful-{day}",
                _at(start, days=day, hour=11),
                "helpful",
            )
        if day in early_quality_days or day in later_attunement_days:
            _event(
                engine,
                f"attunement-{day}",
                _at(start, days=day, hour=12),
                "attunement",
            )


def high_frequency_new_student() -> ScenarioRun:
    start = datetime(2026, 9, 1, 9, tzinfo=LOCAL_TIMEZONE)
    engine = RelationshipTimeline()
    timeline: list[dict[str, object]] = []
    checkpoints: dict[int, dict[str, object]] = {}
    for target in (14, 90, 365):
        previous = max(checkpoints, default=-1)
        if previous < 0:
            _seed_high_frequency(engine, start=start, through_day=target)
        else:
            for day in range(previous + 1, target + 1):
                for turn in range(2):
                    _event(
                        engine,
                        f"daily-{day}-turn-{turn}",
                        _at(start, days=day, minute=turn),
                        "interaction",
                    )
                if day in {30, 60, 120, 180, 240, 300}:
                    _event(
                        engine,
                        f"helpful-{day}",
                        _at(start, days=day, hour=11),
                        "helpful",
                    )
                if day in {60, 150, 240, 330}:
                    _event(
                        engine,
                        f"attunement-{day}",
                        _at(start, days=day, hour=12),
                        "attunement",
                    )
        checkpoints[target] = _point(
            engine,
            f"day {target}",
            _at(start, days=target, hour=13),
        )
        timeline.append(checkpoints[target])

    checks = (
        Check(
            "current production thresholds can report long-term by day 14",
            checkpoints[14]["legacy_stage"],
            "long_term_companion",
        ),
        Check(
            "candidate model is only familiar at day 14",
            checkpoints[14]["stage"],
            "familiar",
        ),
        Check(
            "candidate model reaches attuned at day 90",
            checkpoints[90]["stage"],
            "attuned",
        ),
        Check(
            "candidate model waits one year for long-term",
            checkpoints[365]["stage"],
            "long_term_companion",
        ),
    )
    return ScenarioRun(engine.state(_at(start, days=365, hour=13)), tuple(timeline), checks)


def low_frequency_stable_use() -> ScenarioRun:
    engine = RelationshipTimeline()
    timeline: list[dict[str, object]] = []
    checkpoints: dict[int, dict[str, object]] = {}
    interaction_index = 0
    for month in range(18):
        for day in (1, 15):
            interaction_index += 1
            occurred_at = _month_at(2026, 9, month, day)
            _event(
                engine,
                f"low-{interaction_index}",
                occurred_at,
                "interaction",
            )
            if interaction_index <= 10:
                _event(
                    engine,
                    f"low-knowledge-{interaction_index}",
                    occurred_at.replace("T09:", "T10:"),
                    "knowledge",
                    key=f"low-fact-{interaction_index}",
                )
            if interaction_index % 3 == 0:
                _event(
                    engine,
                    f"low-helpful-{interaction_index}",
                    occurred_at.replace("T09:", "T11:"),
                    "helpful",
                )
            if interaction_index % 4 == 0:
                _event(
                    engine,
                    f"low-attunement-{interaction_index}",
                    occurred_at.replace("T09:", "T12:"),
                    "attunement",
                )
            if interaction_index in {4, 12, 36}:
                checkpoints[interaction_index] = _point(
                    engine,
                    f"interaction {interaction_index}",
                    occurred_at.replace("T09:", "T13:"),
                )
                timeline.append(checkpoints[interaction_index])

    final_now = _month_at(2026, 9, 17, 15).replace("T09:", "T13:")
    checks = (
        Check("low-frequency use can become familiar", checkpoints[4]["stage"], "familiar"),
        Check("low-frequency use can become attuned", checkpoints[12]["stage"], "attuned"),
        Check(
            "low-frequency stability eventually becomes long-term",
            checkpoints[36]["stage"],
            "long_term_companion",
        ),
    )
    return ScenarioRun(engine.state(final_now), tuple(timeline), checks)


def monthly_stable_use() -> ScenarioRun:
    engine = RelationshipTimeline()
    timeline: list[dict[str, object]] = []
    checkpoints: dict[int, dict[str, object]] = {}
    for interaction_index in range(1, 37):
        occurred_at = _month_at(2026, 9, interaction_index - 1, 1)
        _event(
            engine,
            f"monthly-{interaction_index}",
            occurred_at,
            "interaction",
        )
        if interaction_index <= 10:
            _event(
                engine,
                f"monthly-knowledge-{interaction_index}",
                occurred_at.replace("T09:", "T10:"),
                "knowledge",
                key=f"monthly-fact-{interaction_index}",
            )
        if interaction_index % 3 == 0:
            _event(
                engine,
                f"monthly-helpful-{interaction_index}",
                occurred_at.replace("T09:", "T11:"),
                "helpful",
            )
        if interaction_index % 4 == 0:
            _event(
                engine,
                f"monthly-attunement-{interaction_index}",
                occurred_at.replace("T09:", "T12:"),
                "attunement",
            )
        if interaction_index in {4, 12, 36}:
            checkpoints[interaction_index] = _point(
                engine,
                f"monthly interaction {interaction_index}",
                occurred_at.replace("T09:", "T13:"),
            )
            timeline.append(checkpoints[interaction_index])

    final_now = _month_at(2026, 9, 35, 1).replace("T09:", "T13:")
    checks = (
        Check("monthly use can eventually become familiar", checkpoints[4]["stage"], "familiar"),
        Check("monthly use can eventually become attuned", checkpoints[12]["stage"], "attuned"),
        Check(
            "historical promotion does not cancel cautious reunion posture",
            checkpoints[12]["posture"],
            "reunion_cautious",
        ),
        Check(
            "three years of monthly quality can become long-term",
            checkpoints[36]["stage"],
            "long_term_companion",
        ),
    )
    return ScenarioRun(engine.state(final_now), tuple(timeline), checks)


def short_term_volume_farming() -> ScenarioRun:
    start = datetime(2026, 9, 1, 9, tzinfo=LOCAL_TIMEZONE)
    engine = RelationshipTimeline()
    timeline: list[dict[str, object]] = []
    for day in range(3):
        _event(
            engine,
            f"spam-{day}",
            _at(start, days=day),
            "interaction",
            turn_weight=200,
        )
        if day == 0:
            for index in range(20):
                _event(
                    engine,
                    f"spam-knowledge-{index}",
                    _at(start, days=day, hour=10, minute=index),
                    "knowledge",
                    key=f"spam-fact-{index}",
                )
        for index in range(20):
            _event(
                engine,
                f"spam-helpful-{day}-{index}",
                _at(start, days=day, hour=11, minute=index),
                "helpful",
            )
        for index in range(20):
            _event(
                engine,
                f"spam-attune-{day}-{index}",
                _at(start, days=day, hour=12, minute=index),
                "attunement",
            )
    after_burst = _point(engine, "600 turns across three days", _at(start, days=2, hour=13))
    timeline.append(after_burst)
    before_return = _point(engine, "one year passes without interaction", _at(start, days=365))
    timeline.append(before_return)
    _event(engine, "spam-return", _at(start, days=365, hour=10), "interaction")
    after_return = _point(engine, "one later return", _at(start, days=365, hour=11))
    timeline.append(after_return)
    checks = (
        Check("message volume does not leave first meeting", after_burst["stage"], "first_meeting"),
        Check("waiting alone does not promote", before_return["stage"], "first_meeting"),
        Check("one return cannot combine with stale burst quality", after_return["stage"], "first_meeting"),
        Check(
            "same-day quality events count as three helpful dates",
            after_return["metrics"]["helpful_days"],
            3,
        ),
    )
    return ScenarioRun(engine.state(_at(start, days=365, hour=11)), tuple(timeline), checks)


def long_absence_reunion() -> ScenarioRun:
    start = datetime(2026, 9, 1, 9, tzinfo=LOCAL_TIMEZONE)
    engine = RelationshipTimeline(
        implicit_adjustments={"question_frequency": "less", "closure_style": "warm"}
    )
    _seed_high_frequency(engine, start=start, through_day=365)
    timeline = [
        _point(engine, "long-term before absence", _at(start, days=365, hour=13))
    ]
    for offset, label in ((486, "return day 1"), (487, "return day 2"), (488, "return day 3")):
        _event(engine, f"return-{offset}", _at(start, days=offset), "interaction")
        timeline.append(_point(engine, label, _at(start, days=offset, hour=10)))
    day_one, day_two, day_three = timeline[1:]
    checks = (
        Check("long absence does not downgrade stage", day_one["stage"], "long_term_companion"),
        Check("first return is cautious", day_one["posture"], "reunion_cautious"),
        Check("first return halves implicit gain", day_one["projection"]["implicit_adjustment_gain"], 0.5),
        Check("reunion disables initiative", day_one["projection"]["initiative_level"], "disabled"),
        Check("second distinct day remains cautious", day_two["projection"]["implicit_adjustment_gain"], 0.75),
        Check("third distinct day restores normal posture", day_three["posture"], "steady"),
        Check(
            "stored adjustments survive silence",
            day_three["implicit_adjustments"],
            {"closure_style": "warm", "question_frequency": "less"},
        ),
    )
    return ScenarioRun(engine.state(_at(start, days=488, hour=10)), tuple(timeline), checks)


def consecutive_negative_feedback() -> ScenarioRun:
    start = datetime(2026, 9, 1, 9, tzinfo=LOCAL_TIMEZONE)
    engine = RelationshipTimeline(implicit_adjustments={"memory_reference_depth": "deep"})
    _seed_high_frequency(engine, start=start, through_day=100)
    timeline = [_point(engine, "attuned before feedback", _at(start, days=100, hour=13))]
    for day in (101, 102, 103):
        _event(engine, f"negative-interaction-{day}", _at(start, days=day), "interaction")
        _event(
            engine,
            f"negative-{day}",
            _at(start, days=day, hour=10),
            "negative_feedback",
        )
    repairing = _point(engine, "three negative days", _at(start, days=103, hour=11))
    timeline.append(repairing)
    _event(engine, "repair-interaction", _at(start, days=104), "interaction")
    _event(engine, "repair-positive", _at(start, days=104, hour=10), "positive_feedback")
    recovered = _point(engine, "explicit positive feedback after correction", _at(start, days=104, hour=11))
    timeline.append(recovered)
    checks = (
        Check("negative feedback does not punish stage", repairing["stage"], "attuned"),
        Check("negative feedback starts repair posture", repairing["posture"], "repairing"),
        Check("repair suppresses implicit gain", repairing["projection"]["implicit_adjustment_gain"], 0.0),
        Check("repair disables initiative", repairing["projection"]["initiative_level"], "disabled"),
        Check("positive feedback clears repair", recovered["posture"], "steady"),
        Check("historical stage remains attuned", recovered["stage"], "attuned"),
    )
    return ScenarioRun(engine.state(_at(start, days=104, hour=11)), tuple(timeline), checks)


def cross_device_continuity() -> ScenarioRun:
    engine = RelationshipTimeline()
    interaction_index = 0
    timeline: list[dict[str, object]] = []
    for month in range(18):
        for day in (1, 15):
            interaction_index += 1
            occurred_at = _month_at(2026, 9, month, day)
            device_id = "device-a" if interaction_index % 2 else "device-b"
            event_id = f"cross-{interaction_index}"
            _event(engine, event_id, occurred_at, "interaction", device_id=device_id)
            if interaction_index == 8:
                _event(engine, event_id, occurred_at, "interaction", device_id="device-b")
            if interaction_index <= 10:
                _event(
                    engine,
                    f"cross-knowledge-{interaction_index}",
                    occurred_at.replace("T09:", "T10:"),
                    "knowledge",
                    device_id=device_id,
                    key=f"cross-fact-{interaction_index}",
                )
            if interaction_index % 3 == 0:
                _event(
                    engine,
                    f"cross-helpful-{interaction_index}",
                    occurred_at.replace("T09:", "T11:"),
                    "helpful",
                    device_id=device_id,
                )
            if interaction_index % 4 == 0:
                _event(
                    engine,
                    f"cross-attune-{interaction_index}",
                    occurred_at.replace("T09:", "T12:"),
                    "attunement",
                    device_id=device_id,
                )
    final_now = _month_at(2026, 9, 17, 15).replace("T09:", "T13:")
    final_point = _point(engine, "same pet across two devices", final_now)
    timeline.append(final_point)
    checks = (
        Check("device changes do not split relationship", final_point["stage"], "long_term_companion"),
        Check("both devices are observed", final_point["devices_seen"], ["device-a", "device-b"]),
        Check("duplicate cross-device event is idempotent", final_point["metrics"]["turn_count"], 36),
    )
    return ScenarioRun(engine.state(final_now), tuple(timeline), checks)


def forgetting_preserves_history() -> ScenarioRun:
    start = datetime(2026, 9, 1, 9, tzinfo=LOCAL_TIMEZONE)
    engine = RelationshipTimeline()
    _seed_high_frequency(engine, start=start, through_day=100)
    before = _point(engine, "attuned with reliable knowledge", _at(start, days=100, hour=13))
    for index in range(12):
        engine.forget_knowledge(f"fact-{index}", now=_at(start, days=101, minute=index))
    after = _point(engine, "user forgets every stored fact", _at(start, days=101, hour=13))
    checks = (
        Check("forgetting does not rewrite historical stage", after["stage"], "attuned"),
        Check("no facts means no memory references", after["projection"]["memory_reference_budget"], 0),
        Check("stage stayed the same across forgetting", (before["stage"], after["stage"]), ("attuned", "attuned")),
    )
    return ScenarioRun(engine.state(_at(start, days=101, hour=13)), (before, after), checks)


SCENARIOS = (
    Scenario(
        "high_frequency_new_student",
        "High-frequency use cannot finish a multi-year relationship in fifteen days",
        "The current thresholds reach long-term at day 14; the candidate model uses 14/90/365-day span gates.",
        high_frequency_new_student,
    ),
    Scenario(
        "low_frequency_stable_use",
        "Low-frequency but stable companionship can still mature",
        "Distribution across weeks and months matters more than daily volume.",
        low_frequency_stable_use,
    ),
    Scenario(
        "monthly_stable_use",
        "Monthly use is slow but not permanently excluded",
        "A real four-year companion must recognize sparse, durable quality without accepting stale bursts.",
        monthly_stable_use,
    ),
    Scenario(
        "short_term_volume_farming",
        "Messages and same-day outcomes cannot farm a deep relationship",
        "Raw turns are diagnostic only; quality votes are capped by local date and must be distributed.",
        short_term_volume_farming,
    ),
    Scenario(
        "long_absence_reunion",
        "Long absence preserves history but temporarily softens expression",
        "Reunion posture recovers over three distinct return dates without deleting learned adjustments.",
        long_absence_reunion,
    ),
    Scenario(
        "consecutive_negative_feedback",
        "Negative feedback repairs behavior instead of subtracting intimacy",
        "The historical stage stays monotonic while current expression becomes cautious.",
        consecutive_negative_feedback,
    ),
    Scenario(
        "cross_device_continuity",
        "The relationship follows the pet and subject, not the device",
        "Device changes and duplicate delivery cannot split or inflate the relationship.",
        cross_device_continuity,
    ),
    Scenario(
        "forgetting_preserves_history",
        "Forgetting facts removes permissions without rewriting relationship history",
        "Historical stage remains while current memory-reference capability shrinks to available Evidence.",
        forgetting_preserves_history,
    ),
)
