"""Synthetic timelines for the companion VA throwaway prototype."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Callable

from va_model import (
    BASELINE,
    AffectEvent,
    AffectSnapshot,
    apply_event,
    baseline_snapshot,
    project_affect,
    read_snapshot,
    reset_to_baseline,
    restore_payload,
    restore_snapshot,
)


START = datetime(2026, 9, 1, 9, tzinfo=timezone(timedelta(hours=8)))


def _json_default(value: object) -> object:
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def at(*, minutes: int = 0, hours: int = 0) -> str:
    return (START + timedelta(minutes=minutes, hours=hours)).isoformat(timespec="seconds")


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
    timeline: tuple[dict[str, object], ...]
    checks: tuple[Check, ...]

    def canonical_json(self) -> str:
        return json.dumps(
            {
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
            default=_json_default,
        )


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    title: str
    lesson: str
    run: Callable[[], ScenarioRun]


def point(label: str, snapshot: AffectSnapshot, **extra: object) -> dict[str, object]:
    return {
        "label": label,
        "at": snapshot.observed_at,
        "va": snapshot.point.public(),
        "expires_at": snapshot.expires_at,
        **extra,
    }


def success_and_maturity() -> ScenarioRun:
    young = baseline_snapshot(
        observed_at=at(), age=1, relationship_stage="first_meeting"
    )
    mature = baseline_snapshot(
        observed_at=at(), age=4, relationship_stage="long_term_companion"
    )
    event = AffectEvent("success-1", at(minutes=1), "shared_success")
    young_result = apply_event(
        young, event, age=1, relationship_stage="first_meeting"
    )
    mature_result = apply_event(
        mature, event, age=4, relationship_stage="long_term_companion"
    )
    young_later, _ = read_snapshot(young_result.snapshot, now=at(hours=2))
    mature_later, _ = read_snapshot(mature_result.snapshot, now=at(hours=2))
    timeline = (
        point("1 岁初见成功", young_result.snapshot),
        point("4 岁长期陪伴成功", mature_result.snapshot),
        point("1 岁两小时后", young_later),
        point("4 岁两小时后", mature_later),
    )
    checks = (
        Check(
            "younger first-meeting state reacts more strongly",
            young_result.snapshot.point.arousal > mature_result.snapshot.point.arousal,
            True,
        ),
        Check(
            "mature state still moves in the same positive direction",
            mature_result.snapshot.point.valence > BASELINE.valence,
            True,
        ),
        Check(
            "both states recover toward baseline",
            (
                young_later.point.arousal < young_result.snapshot.point.arousal
                and mature_later.point.arousal < mature_result.snapshot.point.arousal
            ),
            True,
        ),
    )
    return ScenarioRun(timeline, checks)


def ordinary_chat_is_bounded() -> ScenarioRun:
    snapshot = baseline_snapshot(observed_at=at(), age=2, relationship_stage="familiar")
    for index in range(20):
        result = apply_event(
            snapshot,
            AffectEvent(f"chat-{index}", at(minutes=index + 1), "ordinary_chat"),
            age=2,
            relationship_stage="familiar",
        )
        snapshot = result.snapshot
    duplicate = apply_event(
        snapshot,
        AffectEvent("chat-19", at(minutes=20), "ordinary_chat"),
        age=2,
        relationship_stage="familiar",
    )
    checks = (
        Check("ordinary chat does not accumulate to an extreme", snapshot.point.valence < 300, True),
        Check("ordinary chat remains low arousal", snapshot.point.arousal < 100, True),
        Check("duplicate event is ignored", duplicate.status, "duplicate_ignored"),
        Check("duplicate leaves state unchanged", duplicate.snapshot, snapshot),
    )
    return ScenarioRun((point("20 次普通聊天", snapshot, duplicate=duplicate.status),), checks)


def user_distress_does_not_mirror() -> ScenarioRun:
    snapshot = baseline_snapshot(observed_at=at(), age=1, relationship_stage="attuned")
    celebration = apply_event(
        snapshot,
        AffectEvent("success-before-distress", at(minutes=1), "shared_success"),
        age=1,
        relationship_stage="attuned",
    )
    distress = apply_event(
        celebration.snapshot,
        AffectEvent("distress-1", at(minutes=2), "user_distress"),
        age=1,
        relationship_stage="attuned",
    )
    projection = project_affect(
        distress.snapshot.point,
        age=1,
        relationship_stage="attuned",
        context=distress.context,
        surface="voice",
    )
    checks = (
        Check("user distress never makes Xiaoxin negative", distress.snapshot.point.valence >= 0, True),
        Check("celebration is immediately settled", distress.snapshot.point.arousal <= 0, True),
        Check("projection is supportive", projection["emotional_posture"], "supportive_settled"),
        Check("VA cannot create initiative", projection["may_create_initiative"], False),
        Check("humor is suppressed", "no_humor" in projection["hard_constraints"], True),
    )
    return ScenarioRun(
        (
            point("先庆祝", celebration.snapshot),
            point("用户低落", distress.snapshot, projection=projection),
        ),
        checks,
    )


def negative_feedback_is_not_injury() -> ScenarioRun:
    snapshot = baseline_snapshot(observed_at=at(), age=3, relationship_stage="attuned")
    result = apply_event(
        snapshot,
        AffectEvent("feedback-1", at(minutes=1), "negative_feedback"),
        age=3,
        relationship_stage="attuned",
    )
    projection = project_affect(
        result.snapshot.point,
        age=3,
        relationship_stage="attuned",
        context=result.context,
        surface="text",
    )
    checks = (
        Check("negative feedback does not create negative valence", result.snapshot.point.valence >= 0, True),
        Check("response becomes settled", result.snapshot.point.arousal <= -150, True),
        Check("projection receives correction", projection["emotional_posture"], "receptive_brief"),
        Check("self-pity is forbidden", "no_self_pity" in projection["hard_constraints"], True),
        Check("comfort seeking is forbidden", "no_comfort_seeking" in projection["hard_constraints"], True),
    )
    return ScenarioRun((point("收到负反馈", result.snapshot, projection=projection),), checks)


def long_idle_expires() -> ScenarioRun:
    snapshot = baseline_snapshot(observed_at=at(), age=2, relationship_stage="familiar")
    active = apply_event(
        snapshot,
        AffectEvent("success-before-idle", at(minutes=1), "shared_success"),
        age=2,
        relationship_stage="familiar",
    )
    after_two_hours, status_two = read_snapshot(active.snapshot, now=at(hours=2))
    after_six_hours, status_six = read_snapshot(active.snapshot, now=at(hours=7))
    checks = (
        Check("two-hour state has decayed", after_two_hours.point.arousal < active.snapshot.point.arousal, True),
        Check("six-hour snapshot expires", status_six, "expired_to_baseline"),
        Check("expired state is exact baseline", after_six_hours.point, BASELINE),
        Check("reading does not extend original expiry", after_two_hours.expires_at, active.snapshot.expires_at),
    )
    return ScenarioRun(
        (
            point("成功后", active.snapshot),
            point("两小时后", after_two_hours, restore_status=status_two),
            point("六小时以上", after_six_hours, restore_status=status_six),
        ),
        checks,
    )


def low_battery_is_device_override() -> ScenarioRun:
    snapshot = baseline_snapshot(observed_at=at(), age=2, relationship_stage="attuned")
    active = apply_event(
        snapshot,
        AffectEvent("success-before-low-power", at(minutes=1), "shared_success"),
        age=2,
        relationship_stage="attuned",
    )
    before = active.snapshot.point
    projection = project_affect(
        before,
        age=2,
        relationship_stage="attuned",
        context=active.context,
        surface="hardware",
        device_state="low_battery",
    )
    checks = (
        Check("low battery uses hardware override", projection["hardware_expression"]["kind"], "low_power"),
        Check("hardware projection does not mutate VA", active.snapshot.point, before),
        Check("low battery cannot create initiative", projection["may_create_initiative"], False),
    )
    return ScenarioRun((point("低电量覆盖", active.snapshot, projection=projection),), checks)


def restart_uses_wall_clock_decay() -> ScenarioRun:
    snapshot = baseline_snapshot(observed_at=at(), age=2, relationship_stage="familiar")
    active = apply_event(
        snapshot,
        AffectEvent("success-before-restart", at(minutes=1), "shared_success"),
        age=2,
        relationship_stage="familiar",
    )
    payload = active.snapshot.to_dict()
    restored, status = restore_payload(
        json.loads(json.dumps(payload)),
        now=at(minutes=46),
        pet_id="pet-prototype",
        memory_subject_id="subject-prototype",
        relationship_epoch_id="epoch-prototype",
        current_age=2,
        current_relationship_stage="familiar",
    )
    continuous, _ = read_snapshot(active.snapshot, now=at(minutes=46))
    serialized = json.dumps(payload, ensure_ascii=False)
    checks = (
        Check("restart restores with elapsed wall-clock decay", restored.point, continuous.point),
        Check("restart status is restored", status, "restored"),
        Check("snapshot contains no transcript", "transcript" in serialized, False),
        Check("snapshot contains no user mood label", "user_distress" in serialized, False),
    )
    return ScenarioRun((point("重启恢复", restored, restore_status=status),), checks)


def relationship_controls_reset_va() -> ScenarioRun:
    snapshot = baseline_snapshot(observed_at=at(), age=4, relationship_stage="long_term_companion")
    active = apply_event(
        snapshot,
        AffectEvent("success-before-reset", at(minutes=1), "shared_success"),
        age=4,
        relationship_stage="long_term_companion",
    )
    reset = reset_to_baseline(
        active.snapshot,
        now=at(minutes=2),
        relationship_epoch_id="epoch-after-reset",
        age=4,
    )
    purge = reset_to_baseline(
        active.snapshot,
        now=at(minutes=2),
        relationship_epoch_id="epoch-after-purge",
        age=4,
    )
    replay = apply_event(
        reset,
        AffectEvent("success-before-reset", at(minutes=3), "shared_success"),
        age=4,
        relationship_stage="first_meeting",
    )
    checks = (
        Check("relationship reset returns exact baseline", reset.point, BASELINE),
        Check("personal-memory purge returns exact baseline", purge.point, BASELINE),
        Check("reset preserves event idempotency receipts", reset.processed_event_ids, ("success-before-reset",)),
        Check("old event cannot replay after reset", replay.status, "duplicate_ignored"),
        Check("new relationship begins at first meeting", reset.dynamics_relationship_stage, "first_meeting"),
    )
    return ScenarioRun((point("重新磨合", reset), point("清空个人记忆", purge)), checks)


def restart_identity_isolation() -> ScenarioRun:
    snapshot = baseline_snapshot(observed_at=at(), age=2, relationship_stage="familiar")
    active = apply_event(
        snapshot,
        AffectEvent("private-success", at(minutes=1), "shared_success"),
        age=2,
        relationship_stage="familiar",
    )
    wrong_subject, subject_status = restore_snapshot(
        active.snapshot,
        now=at(minutes=2),
        pet_id="pet-prototype",
        memory_subject_id="subject-other",
        relationship_epoch_id="epoch-prototype",
        current_age=1,
        current_relationship_stage="first_meeting",
    )
    new_epoch, epoch_status = restore_snapshot(
        active.snapshot,
        now=at(minutes=2),
        pet_id="pet-prototype",
        memory_subject_id="subject-prototype",
        relationship_epoch_id="epoch-new",
        current_age=2,
        current_relationship_stage="first_meeting",
    )
    checks = (
        Check("other subject cannot restore VA", subject_status, "identity_or_epoch_mismatch"),
        Check("other subject receives baseline", wrong_subject.point, BASELINE),
        Check("other subject uses its own age", wrong_subject.dynamics_age, 1),
        Check(
            "other subject uses its own relationship stage",
            wrong_subject.dynamics_relationship_stage,
            "first_meeting",
        ),
        Check("old epoch cannot restore VA", epoch_status, "identity_or_epoch_mismatch"),
        Check("new epoch receives baseline", new_epoch.point, BASELINE),
        Check("new epoch uses current relationship stage", new_epoch.dynamics_relationship_stage, "first_meeting"),
        Check("mismatch does not inherit event receipts", wrong_subject.processed_event_ids, ()),
    )
    return ScenarioRun(
        (
            point("错误主体恢复", wrong_subject, restore_status=subject_status),
            point("新关系时期恢复", new_epoch, restore_status=epoch_status),
        ),
        checks,
    )


def event_gate_and_projection_contract() -> ScenarioRun:
    snapshot = baseline_snapshot(observed_at=at(), age=2, relationship_stage="attuned")
    current = apply_event(
        snapshot,
        AffectEvent("ordered-event", at(minutes=10), "shared_success"),
        age=2,
        relationship_stage="attuned",
    )
    late = apply_event(
        current.snapshot,
        AffectEvent("late-event", at(minutes=5), "ordinary_chat"),
        age=2,
        relationship_stage="attuned",
    )
    rejected_unknown = False
    try:
        AffectEvent("model-freeform", at(minutes=11), "model_selected_happiness")
    except ValueError:
        rejected_unknown = True
    projection = project_affect(
        current.snapshot.point,
        age=2,
        relationship_stage="attuned",
        context=current.context,
        surface="voice",
    )
    serialized_projection = json.dumps(projection, sort_keys=True)
    future = baseline_snapshot(
        observed_at=at(hours=1), age=2, relationship_stage="attuned"
    )
    rejected_future, future_status = read_snapshot(future, now=at())
    damaged_payload = current.snapshot.to_dict()
    damaged_payload["version"] = "unknown-va-version"
    rejected_damaged, damaged_status = restore_payload(
        damaged_payload,
        now=at(minutes=11),
        pet_id="pet-prototype",
        memory_subject_id="subject-prototype",
        relationship_epoch_id="epoch-prototype",
        current_age=2,
        current_relationship_stage="attuned",
    )
    checks = (
        Check("out-of-order event is ignored", late.status, "out_of_order_ignored"),
        Check("out-of-order event leaves state unchanged", late.snapshot, current.snapshot),
        Check("free-form model event is rejected", rejected_unknown, True),
        Check("projection hides raw valence", "valence" in serialized_projection, False),
        Check("projection hides raw arousal", "arousal" in serialized_projection, False),
        Check("projection cannot grant initiative", projection["may_create_initiative"], False),
        Check("future snapshot is rejected", future_status, "future_snapshot_rejected"),
        Check("future snapshot fails closed to baseline", rejected_future.point, BASELINE),
        Check("damaged version is rejected", damaged_status, "invalid_snapshot"),
        Check("damaged snapshot fails closed to baseline", rejected_damaged.point, BASELINE),
    )
    return ScenarioRun(
        (point("合法事件后拒绝乱序写入", late.snapshot, projection=projection),),
        checks,
    )


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "success_and_maturity",
        "成功庆祝与成熟动力学",
        "年龄与关系改变反应幅度和恢复速度，但不改变事件方向。",
        success_and_maturity,
    ),
    Scenario(
        "ordinary_chat_is_bounded",
        "普通聊天不会累计成兴奋",
        "目标靠近模型让重复普通互动保持在低强度范围。",
        ordinary_chat_is_bounded,
    ),
    Scenario(
        "user_distress_does_not_mirror",
        "用户低落不镜像成小芯低落",
        "先收住庆祝，再用安静支持表达，不创造主动机会。",
        user_distress_does_not_mirror,
    ),
    Scenario(
        "negative_feedback_is_not_injury",
        "负反馈不是小芯受伤",
        "小芯收住并接受纠正，不委屈、不索取安慰。",
        negative_feedback_is_not_injury,
    ),
    Scenario(
        "long_idle_expires",
        "久别后自动回到基线",
        "状态随墙上时间衰减，六小时后不再恢复旧情绪。",
        long_idle_expires,
    ),
    Scenario(
        "low_battery_is_device_override",
        "低电量只覆盖硬件表现",
        "设备状态不改写服务端小芯情绪。",
        low_battery_is_device_override,
    ),
    Scenario(
        "restart_uses_wall_clock_decay",
        "服务重启不中断衰减",
        "短期快照按真实经过时间恢复，且不保存聊天或用户情绪标签。",
        restart_uses_wall_clock_decay,
    ),
    Scenario(
        "relationship_controls_reset_va",
        "关系控制令 VA 回基线",
        "重新磨合与清空个人记忆都从温暖中性状态开始。",
        relationship_controls_reset_va,
    ),
    Scenario(
        "restart_identity_isolation",
        "重启恢复必须匹配主体与关系时期",
        "其他主体和旧关系时期都不能继承当前小芯的短期状态。",
        restart_identity_isolation,
    ),
    Scenario(
        "event_gate_and_projection_contract",
        "事件门禁与安全投影",
        "模型自由事件、乱序更新和原始 VA 暴露都被拒绝。",
        event_gate_and_projection_contract,
    ),
)
