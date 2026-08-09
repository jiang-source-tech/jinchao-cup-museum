"""Synthetic timelines for the throwaway companion narrative prototype."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from typing import Callable
from zoneinfo import ZoneInfo

from narrative_model import (
    AcademicTransition,
    AnniversaryBoundary,
    CompanionNarrativeTimeline,
    NarrativeEvidence,
)


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


def _at(year: int, month: int, day: int, hour: int = 9, minute: int = 0) -> str:
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=LOCAL_TIMEZONE,
    ).isoformat()


def _transition(
    engine: CompanionNarrativeTimeline,
    *,
    event_id: str,
    occurred_at: str,
    revision: int,
    kind: str,
    from_stage: str,
    to_stage: str,
    to_status: str = "active",
) -> None:
    engine.observe_academic_transition(
        AcademicTransition(
            event_id=event_id,
            occurred_at=occurred_at,
            source_revision=revision,
            transition_kind=kind,
            from_stage=from_stage,
            to_stage=to_stage,
            to_status=to_status,
        )
    )


def _initialize(
    engine: CompanionNarrativeTimeline,
    *,
    stage: str,
    occurred_at: str,
) -> None:
    _transition(
        engine,
        event_id=f"initialize-{stage}",
        occurred_at=occurred_at,
        revision=1,
        kind="initialized",
        from_stage="unknown",
        to_stage=stage,
    )


def _evidence(
    engine: CompanionNarrativeTimeline,
    *,
    evidence_id: str,
    occurred_at: str,
    stage: str,
    scope: str,
    summary: str,
) -> None:
    engine.observe_evidence(
        NarrativeEvidence(
            event_id=f"event-{evidence_id}",
            evidence_id=evidence_id,
            occurred_at=occurred_at,
            academic_stage=stage,
            ownership_scope=scope,
            safe_summary=summary,
            relationship_epoch_id=engine.relationship_epoch_id,
        )
    )


def _anniversary(
    engine: CompanionNarrativeTimeline,
    *,
    event_id: str,
    occurred_at: str,
    number: int,
) -> None:
    engine.observe_anniversary(
        AnniversaryBoundary(
            event_id=event_id,
            occurred_at=occurred_at,
            anniversary_number=number,
        )
    )


def _deliver(
    engine: CompanionNarrativeTimeline,
    *,
    turn_id: str,
    now: str,
) -> dict[str, object] | None:
    projection = engine.claim_moment(turn_id=turn_id, now=now)
    if projection is not None:
        finish_at = (datetime.fromisoformat(now) + timedelta(minutes=1)).isoformat()
        engine.finish_moment(
            turn_id=turn_id,
            now=finish_at,
            delivery_status="delivered",
        )
    return projection


def _point(
    engine: CompanionNarrativeTimeline,
    label: str,
    now: str,
) -> dict[str, object]:
    state = engine.state(now)
    return {
        "label": label,
        "now": state["now"],
        "academic_stage": state["academic_stage"],
        "academic_status": state["academic_status"],
        "xiaoxin_age": state["xiaoxin_age"],
        "posture": state["posture"],
        "growth_reflections_enabled": state["growth_reflections_enabled"],
        "boundaries": state["boundaries"],
        "chapters": state["chapters"],
        "moments": state["moments"],
        "miniprogram_history": state["miniprogram_history"],
    }


def _add_stage_evidence(
    engine: CompanionNarrativeTimeline,
    *,
    stage: str,
    year: int,
) -> None:
    _evidence(
        engine,
        evidence_id=f"{stage}-{year}-shared-a",
        occurred_at=_at(year, 10, 1),
        stage=stage,
        scope="shared_experience",
        summary=f"Shared outcome during {stage}.",
    )
    _evidence(
        engine,
        evidence_id=f"{stage}-{year}-fact",
        occurred_at=_at(year + 1, 2, 1),
        stage=stage,
        scope="user_fact",
        summary=f"User-owned goal during {stage}.",
    )
    _evidence(
        engine,
        evidence_id=f"{stage}-{year}-shared-b",
        occurred_at=_at(year + 1, 5, 1),
        stage=stage,
        scope="shared_experience",
        summary=f"Second shared outcome during {stage}.",
    )


def four_year_continuity() -> ScenarioRun:
    engine = CompanionNarrativeTimeline()
    timeline: list[dict[str, object]] = []
    _initialize(engine, stage="freshman", occurred_at=_at(2026, 9, 1, 8))

    transitions = (
        ("freshman", "sophomore", 2027, 1, 2),
        ("sophomore", "junior", 2028, 2, 3),
        ("junior", "senior", 2029, 3, 4),
    )
    for from_stage, to_stage, year, anniversary_number, revision in transitions:
        _add_stage_evidence(engine, stage=from_stage, year=year - 1)
        _anniversary(
            engine,
            event_id=f"anniversary-{anniversary_number}",
            occurred_at=_at(year, 9, 1, 8),
            number=anniversary_number,
        )
        _transition(
            engine,
            event_id=f"advance-{to_stage}",
            occurred_at=_at(year, 9, 1, 9),
            revision=revision,
            kind="advanced",
            from_stage=from_stage,
            to_stage=to_stage,
        )
        _deliver(
            engine,
            turn_id=f"deliver-{to_stage}",
            now=_at(year, 9, 1, 10),
        )
        timeline.append(
            _point(engine, f"entered {to_stage}", _at(year, 9, 1, 11))
        )

    _add_stage_evidence(engine, stage="senior", year=2029)
    _transition(
        engine,
        event_id="graduate",
        occurred_at=_at(2030, 6, 30, 9),
        revision=5,
        kind="graduated",
        from_stage="senior",
        to_stage="senior",
        to_status="graduated",
    )
    _deliver(engine, turn_id="deliver-graduation", now=_at(2030, 6, 30, 10))
    timeline.append(_point(engine, "graduated", _at(2030, 6, 30, 11)))
    final = engine.state(_at(2030, 6, 30, 11))
    moments = final["moments"]
    checks = (
        Check("four academic periods create four chapters", [item["academic_stage"] for item in final["chapters"]], ["freshman", "sophomore", "junior", "senior"]),
        Check("three annual transitions plus graduation express once", [item["primary_kind"] for item in moments], ["academic_growth", "academic_growth", "academic_growth", "graduation"]),
        Check("all four moments are delivered", [item["status"] for item in moments], ["expressed"] * 4),
        Check("annual transition and anniversary coalesce", [len(item["boundary_ids"]) for item in moments[:3]], [2, 2, 2]),
        Check("voice never exceeds two sentences", max(item["projection"]["voice"]["max_sentences"] for item in moments), 2),
        Check("graduation does not create age five", (final["academic_status"], final["xiaoxin_age"]), ("graduated", 4)),
    )
    return ScenarioRun(final, tuple(timeline), checks)


def age_change_without_shared_history() -> ScenarioRun:
    engine = CompanionNarrativeTimeline()
    _initialize(engine, stage="sophomore", occurred_at=_at(2026, 9, 1))
    _transition(
        engine,
        event_id="advance-junior-no-history",
        occurred_at=_at(2027, 9, 1),
        revision=2,
        kind="advanced",
        from_stage="sophomore",
        to_stage="junior",
    )
    device_claim = engine.claim_moment(
        turn_id="device-action-turn",
        now=_at(2027, 9, 1, 9, 30),
        context="device_action",
    )
    claimed = engine.claim_moment(turn_id="age-only-turn", now=_at(2027, 9, 1, 10))
    assert claimed is not None
    engine.finish_moment(
        turn_id="age-only-turn",
        now=_at(2027, 9, 1, 10, 1),
        delivery_status="delivered",
    )
    final = engine.state(_at(2027, 9, 1, 11))
    checks = (
        Check("device action cannot consume a growth moment", device_claim, None),
        Check("age-only transition has no invented chapter", len(final["chapters"]), 0),
        Check("age-only transition remains expressible", claimed["mode"], "boundary_only"),
        Check("boundary-only voice is one sentence", claimed["voice"]["max_sentences"], 1),
        Check("boundary-only voice cannot claim shared history", claimed["voice"]["shared_anchor_budget"], 0),
    )
    return ScenarioRun(final, (_point(engine, "age only", _at(2027, 9, 1, 11)),), checks)


def insufficient_evidence_stays_boundary_only() -> ScenarioRun:
    engine = CompanionNarrativeTimeline()
    _initialize(engine, stage="freshman", occurred_at=_at(2026, 9, 1))
    _evidence(
        engine,
        evidence_id="only-shared-evidence",
        occurred_at=_at(2027, 5, 1),
        stage="freshman",
        scope="shared_experience",
        summary="One shared outcome is not a chapter.",
    )
    _evidence(
        engine,
        evidence_id="same-day-user-fact",
        occurred_at=_at(2027, 5, 1, 10),
        stage="freshman",
        scope="user_fact",
        summary="A same-day fact cannot fake longitudinal continuity.",
    )
    _transition(
        engine,
        event_id="advance-with-one-evidence",
        occurred_at=_at(2027, 9, 1),
        revision=2,
        kind="advanced",
        from_stage="freshman",
        to_stage="sophomore",
    )
    final = engine.state(_at(2027, 9, 1, 10))
    moment = final["moments"][0]
    checks = (
        Check("same-day Evidence cannot create a chapter", len(final["chapters"]), 0),
        Check("insufficient Evidence cannot appear in ritual", moment["evidence_ids"], []),
        Check("structured boundary still remains", moment["mode"], "boundary_only"),
    )
    return ScenarioRun(final, (_point(engine, "insufficient evidence", _at(2027, 9, 1, 10)),), checks)


def disabled_reflections_do_not_backlog() -> ScenarioRun:
    engine = CompanionNarrativeTimeline()
    _initialize(engine, stage="freshman", occurred_at=_at(2026, 9, 1))
    _evidence(
        engine,
        evidence_id="disabled-shared",
        occurred_at=_at(2027, 4, 1),
        stage="freshman",
        scope="shared_experience",
        summary="Shared evidence remains stored.",
    )
    _evidence(
        engine,
        evidence_id="disabled-fact",
        occurred_at=_at(2027, 5, 1),
        stage="freshman",
        scope="user_fact",
        summary="User fact remains stored.",
    )
    engine.set_growth_reflections_enabled(False, now=_at(2027, 8, 31))
    _transition(
        engine,
        event_id="advance-while-disabled",
        occurred_at=_at(2027, 9, 1),
        revision=2,
        kind="advanced",
        from_stage="freshman",
        to_stage="sophomore",
    )
    disabled_claim = engine.claim_moment(
        turn_id="disabled-turn",
        now=_at(2027, 9, 1, 10),
    )
    engine.set_growth_reflections_enabled(True, now=_at(2027, 9, 2))
    replay_claim = engine.claim_moment(
        turn_id="reenabled-turn",
        now=_at(2027, 9, 2, 10),
    )
    final = engine.state(_at(2027, 9, 2, 11))
    checks = (
        Check("user control suppresses automatic expression", disabled_claim, None),
        Check("reenabling does not replay suppressed history", replay_claim, None),
        Check("suppressed moment is terminal", final["moments"][0]["status"], "suppressed"),
        Check("chapter facts remain available internally", len(final["chapters"]), 1),
        Check("miniprogram narrative history is hidden", final["miniprogram_history"], []),
    )
    return ScenarioRun(final, (_point(engine, "reflections reenabled", _at(2027, 9, 2, 11)),), checks)


def atomic_retry_delivery() -> ScenarioRun:
    engine = CompanionNarrativeTimeline()
    _initialize(engine, stage="freshman", occurred_at=_at(2026, 9, 1))
    _evidence(
        engine,
        evidence_id="retry-shared",
        occurred_at=_at(2027, 4, 1),
        stage="freshman",
        scope="shared_experience",
        summary="Shared delivery anchor.",
    )
    _evidence(
        engine,
        evidence_id="retry-fact",
        occurred_at=_at(2027, 5, 1),
        stage="freshman",
        scope="user_fact",
        summary="User-owned delivery fact.",
    )
    _transition(
        engine,
        event_id="advance-for-retry",
        occurred_at=_at(2027, 9, 1),
        revision=2,
        kind="advanced",
        from_stage="freshman",
        to_stage="sophomore",
    )
    first = engine.claim_moment(turn_id="turn-a", now=_at(2027, 9, 1, 10))
    concurrent = engine.claim_moment(turn_id="turn-b", now=_at(2027, 9, 1, 10))
    engine.finish_moment(
        turn_id="turn-a",
        now=_at(2027, 9, 1, 10, 1),
        delivery_status="delivery_failed",
    )
    retried = engine.claim_moment(turn_id="turn-c", now=_at(2027, 9, 1, 10, 2))
    engine.finish_moment(
        turn_id="turn-c",
        now=_at(2027, 9, 1, 10, 3),
        delivery_status="delivered",
    )
    later = engine.claim_moment(turn_id="turn-d", now=_at(2027, 9, 1, 10, 4))
    final = engine.state(_at(2027, 9, 1, 11))
    checks = (
        Check("first turn claims the moment", first is not None, True),
        Check("concurrent turn cannot double claim", concurrent, None),
        Check("failed delivery retries the same moment", retried["moment_id"] if retried else None, first["moment_id"] if first else None),
        Check("successful delivery consumes the moment", later, None),
        Check("moment is expressed once", final["moments"][0]["status"], "expressed"),
    )
    return ScenarioRun(final, (_point(engine, "delivery complete", _at(2027, 9, 1, 11)),), checks)


def forgetting_tightens_future_narrative() -> ScenarioRun:
    engine = CompanionNarrativeTimeline()
    _initialize(engine, stage="freshman", occurred_at=_at(2026, 9, 1))
    _evidence(
        engine,
        evidence_id="forget-academic-shared",
        occurred_at=_at(2027, 4, 1),
        stage="freshman",
        scope="shared_experience",
        summary="Academic shared anchor.",
    )
    _evidence(
        engine,
        evidence_id="forget-academic-fact",
        occurred_at=_at(2027, 5, 1),
        stage="freshman",
        scope="user_fact",
        summary="Academic user fact.",
    )
    _transition(
        engine,
        event_id="advance-before-forget",
        occurred_at=_at(2027, 9, 1),
        revision=2,
        kind="advanced",
        from_stage="freshman",
        to_stage="sophomore",
    )
    engine.forget_evidence("forget-academic-shared", now=_at(2027, 9, 1, 10))
    academic_claim = engine.claim_moment(
        turn_id="academic-after-forget",
        now=_at(2027, 9, 1, 10, 1),
    )
    assert academic_claim is not None
    engine.finish_moment(
        turn_id="academic-after-forget",
        now=_at(2027, 9, 1, 10, 2),
        delivery_status="delivered",
    )

    _evidence(
        engine,
        evidence_id="forget-anniversary-shared",
        occurred_at=_at(2028, 4, 1),
        stage="sophomore",
        scope="shared_experience",
        summary="Anniversary shared anchor.",
    )
    _evidence(
        engine,
        evidence_id="forget-anniversary-fact",
        occurred_at=_at(2028, 5, 1),
        stage="sophomore",
        scope="user_fact",
        summary="Anniversary user fact.",
    )
    _anniversary(
        engine,
        event_id="anniversary-before-forget",
        occurred_at=_at(2028, 9, 1),
        number=2,
    )
    engine.forget_evidence("forget-anniversary-shared", now=_at(2028, 9, 1, 10))
    anniversary_claim = engine.claim_moment(
        turn_id="anniversary-after-forget",
        now=_at(2028, 9, 1, 10, 1),
    )
    final = engine.state(_at(2028, 9, 1, 11))
    academic_moment, anniversary_moment = final["moments"]
    checks = (
        Check("academic fact survives as boundary only", academic_claim["mode"], "boundary_only"),
        Check("forgotten shared anchor cannot be spoken", academic_claim["voice"]["shared_anchor_budget"], 0),
        Check("timer-only anniversary is invalidated", anniversary_moment["status"], "invalidated"),
        Check("invalidated anniversary cannot be claimed", anniversary_claim, None),
        Check("citing chapters are invalidated", [item["status"] for item in final["chapters"]], ["invalidated", "invalidated"]),
        Check("academic delivery remains auditable", academic_moment["status"], "expressed"),
    )
    return ScenarioRun(final, (_point(engine, "after forgetting", _at(2028, 9, 1, 11)),), checks)


def nonstandard_paths_do_not_invent_years() -> ScenarioRun:
    engine = CompanionNarrativeTimeline()
    _initialize(engine, stage="freshman", occurred_at=_at(2026, 9, 1))
    _evidence(
        engine,
        evidence_id="skip-shared",
        occurred_at=_at(2027, 4, 1),
        stage="freshman",
        scope="shared_experience",
        summary="Freshman shared anchor.",
    )
    _evidence(
        engine,
        evidence_id="skip-fact",
        occurred_at=_at(2027, 5, 1),
        stage="freshman",
        scope="user_fact",
        summary="Freshman user fact.",
    )
    _transition(
        engine,
        event_id="skip-freshman-to-junior",
        occurred_at=_at(2027, 9, 1),
        revision=2,
        kind="skipped_forward",
        from_stage="freshman",
        to_stage="junior",
    )
    _deliver(engine, turn_id="deliver-skip", now=_at(2027, 9, 1, 10))
    _transition(
        engine,
        event_id="repeat-junior",
        occurred_at=_at(2028, 9, 1),
        revision=3,
        kind="repeated",
        from_stage="junior",
        to_stage="junior",
    )
    _transition(
        engine,
        event_id="real-regression",
        occurred_at=_at(2028, 9, 2),
        revision=4,
        kind="real_regression",
        from_stage="junior",
        to_stage="sophomore",
    )
    regression_claim = engine.claim_moment(
        turn_id="deliver-regression",
        now=_at(2028, 9, 2, 10),
    )
    assert regression_claim is not None
    engine.finish_moment(
        turn_id="deliver-regression",
        now=_at(2028, 9, 2, 10, 1),
        delivery_status="delivered",
    )
    _transition(
        engine,
        event_id="correct-regression",
        occurred_at=_at(2028, 9, 3),
        revision=5,
        kind="correction",
        from_stage="sophomore",
        to_stage="junior",
    )
    _transition(
        engine,
        event_id="leave-junior",
        occurred_at=_at(2028, 10, 1),
        revision=6,
        kind="leave",
        from_stage="junior",
        to_stage="junior",
        to_status="leave",
    )
    _transition(
        engine,
        event_id="resume-junior",
        occurred_at=_at(2029, 1, 1),
        revision=7,
        kind="resume_same",
        from_stage="junior",
        to_stage="junior",
    )
    _transition(
        engine,
        event_id="migrate-junior",
        occurred_at=_at(2029, 2, 1),
        revision=8,
        kind="migration",
        from_stage="junior",
        to_stage="junior",
    )
    final = engine.state(_at(2029, 2, 1, 10))
    checks = (
        Check("skip creates no sophomore chapter", [item["academic_stage"] for item in final["chapters"]], ["freshman"]),
        Check("skip creates one direct boundary", final["boundaries"][0]["to_stage"], "junior"),
        Check("real regression uses neutral voice", regression_claim["voice"]["tone"], "neutral"),
        Check("real regression has no celebratory hardware", regression_claim["hardware"]["enabled"], False),
        Check("correction invalidates derived regression moment", final["moments"][1]["status"], "invalidated"),
        Check("repeat leave resume migration add no rituals", len(final["moments"]), 2),
        Check("corrected stage is authoritative", (final["academic_stage"], final["academic_status"]), ("junior", "active")),
    )
    return ScenarioRun(final, (_point(engine, "nonstandard path", _at(2029, 2, 1, 10)),), checks)


def long_absence_does_not_replay_stale_rituals() -> ScenarioRun:
    engine = CompanionNarrativeTimeline()
    _initialize(engine, stage="freshman", occurred_at=_at(2026, 9, 1))
    _evidence(
        engine,
        evidence_id="absence-shared",
        occurred_at=_at(2027, 4, 1),
        stage="freshman",
        scope="shared_experience",
        summary="Shared anchor before absence.",
    )
    _evidence(
        engine,
        evidence_id="absence-fact",
        occurred_at=_at(2027, 5, 1),
        stage="freshman",
        scope="user_fact",
        summary="User fact before absence.",
    )
    _anniversary(
        engine,
        event_id="stale-anniversary",
        occurred_at=_at(2027, 9, 1),
        number=1,
    )
    engine.set_posture("reunion_cautious", now=_at(2027, 11, 1, 9))
    stale_claim = engine.claim_moment(
        turn_id="stale-anniversary-turn",
        now=_at(2027, 11, 1, 9, 1),
    )
    _transition(
        engine,
        event_id="advance-during-reunion",
        occurred_at=_at(2027, 11, 1, 10),
        revision=2,
        kind="advanced",
        from_stage="freshman",
        to_stage="sophomore",
    )
    day_one = engine.claim_moment(
        turn_id="reunion-day-one",
        now=_at(2027, 11, 1, 10, 1),
    )
    engine.set_posture("reunion_cautious", now=_at(2027, 11, 2, 9))
    day_two = engine.claim_moment(
        turn_id="reunion-day-two",
        now=_at(2027, 11, 2, 9, 1),
    )
    engine.set_posture("steady", now=_at(2027, 11, 3, 9))
    day_three = engine.claim_moment(
        turn_id="reunion-day-three",
        now=_at(2027, 11, 3, 9, 1),
    )
    assert day_three is not None
    engine.finish_moment(
        turn_id="reunion-day-three",
        now=_at(2027, 11, 3, 9, 2),
        delivery_status="delivered",
    )
    final = engine.state(_at(2027, 11, 3, 10))
    checks = (
        Check("stale anniversary silently expires", final["moments"][0]["status"], "expired"),
        Check("stale anniversary cannot replay", stale_claim, None),
        Check("reunion posture blocks day one ritual", day_one, None),
        Check("reunion posture blocks day two ritual", day_two, None),
        Check("steady day can express current transition", day_three["primary_kind"], "academic_growth"),
        Check("old anniversary is not stacked into transition", len(final["moments"][1]["boundary_ids"]), 1),
    )
    return ScenarioRun(final, (_point(engine, "third return day", _at(2027, 11, 3, 10)),), checks)


def graduation_without_history_is_neutral() -> ScenarioRun:
    engine = CompanionNarrativeTimeline()
    _initialize(engine, stage="senior", occurred_at=_at(2029, 9, 1))
    _transition(
        engine,
        event_id="graduate-without-history",
        occurred_at=_at(2030, 6, 30),
        revision=2,
        kind="graduated",
        from_stage="senior",
        to_stage="senior",
        to_status="graduated",
    )
    claimed = engine.claim_moment(
        turn_id="graduation-boundary-only",
        now=_at(2030, 6, 30, 10),
    )
    assert claimed is not None
    final = engine.state(_at(2030, 6, 30, 10, 1))
    checks = (
        Check("graduation keeps age four", final["xiaoxin_age"], 4),
        Check("graduation has no invented chapter", len(final["chapters"]), 0),
        Check("graduation without history is boundary only", claimed["mode"], "boundary_only"),
        Check("graduation voice is one sentence", claimed["voice"]["max_sentences"], 1),
        Check("graduation never initiates a message", claimed["voice"]["initiative_allowed"], False),
    )
    return ScenarioRun(final, (_point(engine, "graduation boundary", _at(2030, 6, 30, 10, 1)),), checks)


def anniversary_without_evidence_is_fact_only() -> ScenarioRun:
    engine = CompanionNarrativeTimeline()
    _initialize(engine, stage="freshman", occurred_at=_at(2026, 9, 1))
    _anniversary(
        engine,
        event_id="anniversary-no-evidence-a",
        occurred_at=_at(2027, 9, 1),
        number=1,
    )
    _anniversary(
        engine,
        event_id="anniversary-no-evidence-b",
        occurred_at=_at(2027, 9, 1, 10),
        number=1,
    )
    claim = engine.claim_moment(
        turn_id="anniversary-no-evidence-turn",
        now=_at(2027, 9, 1, 11),
    )
    final = engine.state(_at(2027, 9, 1, 11))
    checks = (
        Check("elapsed time alone creates no anniversary ritual", len(final["moments"]), 0),
        Check("anniversary fact is recorded once", len(final["boundaries"]), 1),
        Check("duplicate anniversary is idempotent", sum(item["action"] == "duplicate_anniversary_ignored" for item in final["trace"]), 1),
        Check("fact-only anniversary cannot be claimed", claim, None),
    )
    return ScenarioRun(final, (_point(engine, "fact-only anniversary", _at(2027, 9, 1, 11)),), checks)


def forgetting_after_expression_removes_future_anchor() -> ScenarioRun:
    engine = CompanionNarrativeTimeline()
    _initialize(engine, stage="freshman", occurred_at=_at(2026, 9, 1))
    _evidence(
        engine,
        evidence_id="expressed-shared",
        occurred_at=_at(2027, 4, 1),
        stage="freshman",
        scope="shared_experience",
        summary="This anchor will later be forgotten.",
    )
    _evidence(
        engine,
        evidence_id="expressed-fact",
        occurred_at=_at(2027, 5, 1),
        stage="freshman",
        scope="user_fact",
        summary="This fact cannot support a shared claim alone.",
    )
    _transition(
        engine,
        event_id="advance-before-late-forget",
        occurred_at=_at(2027, 9, 1),
        revision=2,
        kind="advanced",
        from_stage="freshman",
        to_stage="sophomore",
    )
    delivered = _deliver(
        engine,
        turn_id="deliver-before-forget",
        now=_at(2027, 9, 1, 10),
    )
    assert delivered is not None
    engine.forget_evidence("expressed-shared", now=_at(2027, 9, 2, 10))
    final = engine.state(_at(2027, 9, 2, 11))
    moment = final["moments"][0]
    history = final["miniprogram_history"][0]
    checks = (
        Check("past delivery remains auditable", moment["status"], "expressed"),
        Check("future projection drops shared narrative", moment["mode"], "boundary_only"),
        Check("future projection drops deleted summaries", history["safe_evidence_summaries"], []),
        Check("expressed timestamp is retained", moment["expressed_at"] is not None, True),
        Check("source chapter is invalidated", final["chapters"][0]["status"], "invalidated"),
    )
    return ScenarioRun(final, (_point(engine, "after late forgetting", _at(2027, 9, 2, 11)),), checks)


def forgetting_rebuilds_still_supported_chapter() -> ScenarioRun:
    engine = CompanionNarrativeTimeline()
    _initialize(engine, stage="freshman", occurred_at=_at(2026, 9, 1))
    _evidence(
        engine,
        evidence_id="rebuild-shared-a",
        occurred_at=_at(2027, 3, 1),
        stage="freshman",
        scope="shared_experience",
        summary="First durable shared anchor.",
    )
    _evidence(
        engine,
        evidence_id="rebuild-user-fact",
        occurred_at=_at(2027, 4, 1),
        stage="freshman",
        scope="user_fact",
        summary="Removable user fact.",
    )
    _evidence(
        engine,
        evidence_id="rebuild-shared-b",
        occurred_at=_at(2027, 5, 1),
        stage="freshman",
        scope="shared_experience",
        summary="Second durable shared anchor.",
    )
    _transition(
        engine,
        event_id="advance-before-rebuild",
        occurred_at=_at(2027, 9, 1),
        revision=2,
        kind="advanced",
        from_stage="freshman",
        to_stage="sophomore",
    )
    engine.forget_evidence("rebuild-user-fact", now=_at(2027, 9, 1, 10))
    claimed = engine.claim_moment(
        turn_id="claim-rebuilt-chapter",
        now=_at(2027, 9, 1, 10, 1),
    )
    assert claimed is not None
    engine.finish_moment(
        turn_id="claim-rebuilt-chapter",
        now=_at(2027, 9, 1, 10, 2),
        delivery_status="delivered",
    )
    final = engine.state(_at(2027, 9, 1, 11))
    checks = (
        Check("old chapter is invalidated and replacement is closed", [item["status"] for item in final["chapters"]], ["invalidated", "closed"]),
        Check("replacement uses a new immutable version", [item["version"] for item in final["chapters"]], [1, 2]),
        Check("remaining cross-day shared Evidence stays eligible", claimed["mode"], "evidence_backed"),
        Check("rebuilt moment cites only active Evidence", final["moments"][0]["evidence_ids"], ["rebuild-shared-a", "rebuild-shared-b"]),
        Check("rebuilt chapter replaces the invalidated link", final["moments"][0]["chapter_ids"], [final["chapters"][1]["chapter_id"]]),
    )
    return ScenarioRun(final, (_point(engine, "chapter rebuilt", _at(2027, 9, 1, 11)),), checks)


def forgetting_during_reservation_releases_old_turn() -> ScenarioRun:
    engine = CompanionNarrativeTimeline()
    _initialize(engine, stage="freshman", occurred_at=_at(2026, 9, 1))
    _evidence(
        engine,
        evidence_id="reserved-shared",
        occurred_at=_at(2027, 4, 1),
        stage="freshman",
        scope="shared_experience",
        summary="Reserved shared anchor.",
    )
    _evidence(
        engine,
        evidence_id="reserved-fact",
        occurred_at=_at(2027, 5, 1),
        stage="freshman",
        scope="user_fact",
        summary="Reserved user fact.",
    )
    _transition(
        engine,
        event_id="advance-before-reserved-forget",
        occurred_at=_at(2027, 9, 1),
        revision=2,
        kind="advanced",
        from_stage="freshman",
        to_stage="sophomore",
    )
    old_claim = engine.claim_moment(
        turn_id="stale-reserved-turn",
        now=_at(2027, 9, 1, 10),
    )
    assert old_claim is not None
    engine.forget_evidence("reserved-shared", now=_at(2027, 9, 1, 10, 1))
    old_finish = engine.finish_moment(
        turn_id="stale-reserved-turn",
        now=_at(2027, 9, 1, 10, 2),
        delivery_status="delivered",
    )
    replacement_claim = engine.claim_moment(
        turn_id="fresh-boundary-only-turn",
        now=_at(2027, 9, 1, 10, 3),
    )
    assert replacement_claim is not None
    engine.finish_moment(
        turn_id="fresh-boundary-only-turn",
        now=_at(2027, 9, 1, 10, 4),
        delivery_status="delivered",
    )
    final = engine.state(_at(2027, 9, 1, 11))
    checks = (
        Check("old prepared turn loses its reservation", old_finish, None),
        Check("new turn must claim the tightened projection", replacement_claim["mode"], "boundary_only"),
        Check("new projection contains no forgotten anchor", replacement_claim["voice"]["safe_anchor"], None),
        Check("single moment is still expressed once", final["moments"][0]["status"], "expressed"),
        Check("reservation release is auditable", "reservation_released_after_forgetting" in final["moments"][0]["reason_codes"], True),
    )
    return ScenarioRun(final, (_point(engine, "reservation replaced", _at(2027, 9, 1, 11)),), checks)


def correction_preserves_independent_anniversary() -> ScenarioRun:
    engine = CompanionNarrativeTimeline()
    _initialize(engine, stage="freshman", occurred_at=_at(2026, 9, 1))
    _evidence(
        engine,
        evidence_id="corrected-anniversary-shared",
        occurred_at=_at(2027, 4, 1),
        stage="freshman",
        scope="shared_experience",
        summary="A real shared anniversary anchor.",
    )
    _evidence(
        engine,
        evidence_id="corrected-anniversary-fact",
        occurred_at=_at(2027, 5, 1),
        stage="freshman",
        scope="user_fact",
        summary="A second real anniversary anchor.",
    )
    _anniversary(
        engine,
        event_id="anniversary-before-correction",
        occurred_at=_at(2027, 9, 1, 8),
        number=1,
    )
    _transition(
        engine,
        event_id="mistaken-academic-advance",
        occurred_at=_at(2027, 9, 1, 9),
        revision=2,
        kind="advanced",
        from_stage="freshman",
        to_stage="sophomore",
    )
    stale_claim = engine.claim_moment(
        turn_id="claim-before-correction",
        now=_at(2027, 9, 1, 10),
    )
    assert stale_claim is not None
    _transition(
        engine,
        event_id="authoritative-stage-correction",
        occurred_at=_at(2027, 9, 1, 11),
        revision=3,
        kind="correction",
        from_stage="sophomore",
        to_stage="freshman",
    )
    stale_finish = engine.finish_moment(
        turn_id="claim-before-correction",
        now=_at(2027, 9, 1, 11, 1),
        delivery_status="delivered",
    )
    anniversary_claim = engine.claim_moment(
        turn_id="claim-anniversary-after-correction",
        now=_at(2027, 9, 1, 11, 2),
    )
    assert anniversary_claim is not None
    engine.finish_moment(
        turn_id="claim-anniversary-after-correction",
        now=_at(2027, 9, 1, 11, 3),
        delivery_status="delivered",
    )
    final = engine.state(_at(2027, 9, 1, 12))
    valid_boundaries = [
        item for item in final["boundaries"] if item["status"] == "valid"
    ]
    checks = (
        Check("anniversary and mistaken advance initially coalesce", len(stale_claim["miniprogram"]["boundary_facts"]), 2),
        Check("stale reserved projection cannot finish", stale_finish, None),
        Check("correction restores the authoritative stage", final["academic_stage"], "freshman"),
        Check("correction creates no additional ritual", len(final["moments"]), 1),
        Check("only the independent anniversary boundary remains valid", [item["kind"] for item in valid_boundaries], ["anniversary"]),
        Check("remaining anniversary stays evidence backed", anniversary_claim["mode"], "evidence_backed"),
        Check("remaining moment is reclassified as anniversary", anniversary_claim["primary_kind"], "anniversary"),
        Check("corrected academic boundary is absent from the new projection", [item["kind"] for item in anniversary_claim["miniprogram"]["boundary_facts"]], ["anniversary"]),
        Check("reservation release after correction is auditable", "reservation_released_after_correction" in final["moments"][0]["reason_codes"], True),
    )
    return ScenarioRun(
        final,
        (_point(engine, "correction preserves anniversary", _at(2027, 9, 1, 12)),),
        checks,
    )


SCENARIOS = (
    Scenario(
        "four_year_continuity",
        "Four academic years form one sparse, evidence-backed narrative",
        "Annual transitions coalesce with anniversaries and graduation remains age four.",
        four_year_continuity,
    ),
    Scenario(
        "age_change_without_shared_history",
        "A real age change can be stated without inventing shared history",
        "Structured boundaries survive while shared-claim budgets remain zero.",
        age_change_without_shared_history,
    ),
    Scenario(
        "insufficient_evidence_stays_boundary_only",
        "One Evidence item is not enough to manufacture a chapter",
        "Insufficient material falls back to a factual one-sentence boundary.",
        insufficient_evidence_stays_boundary_only,
    ),
    Scenario(
        "disabled_reflections_do_not_backlog",
        "Turning off growth reflections is immediate and non-retroactive",
        "Facts remain, but voice, hardware, and miniprogram narrative history stay quiet.",
        disabled_reflections_do_not_backlog,
    ),
    Scenario(
        "atomic_retry_delivery",
        "One-shot delivery is atomic and failed delivery can retry",
        "Concurrent turns cannot double claim the same narrative moment.",
        atomic_retry_delivery,
    ),
    Scenario(
        "forgetting_tightens_future_narrative",
        "Forgetting removes unsupported shared claims",
        "Academic facts degrade to boundary-only; timer-only anniversaries disappear.",
        forgetting_tightens_future_narrative,
    ),
    Scenario(
        "nonstandard_paths_do_not_invent_years",
        "Skipping, regression, correction, leave, and migration keep distinct semantics",
        "Missing years are not backfilled and corrections are not celebrated.",
        nonstandard_paths_do_not_invent_years,
    ),
    Scenario(
        "long_absence_does_not_replay_stale_rituals",
        "Reunion posture does not replay old anniversary backlog",
        "Current transitions wait for steady posture while expired rituals remain silent.",
        long_absence_does_not_replay_stale_rituals,
    ),
    Scenario(
        "graduation_without_history_is_neutral",
        "Graduation is a boundary, not age five or proof of shared history",
        "A factual graduation acknowledgement remains short and non-initiating.",
        graduation_without_history_is_neutral,
    ),
    Scenario(
        "anniversary_without_evidence_is_fact_only",
        "Elapsed time alone cannot manufacture an anniversary ritual",
        "The lifecycle fact remains idempotent without voice or hardware celebration.",
        anniversary_without_evidence_is_fact_only,
    ),
    Scenario(
        "forgetting_after_expression_removes_future_anchor",
        "Past speech cannot be unsaid, but future recap must forget",
        "Audit time remains while deleted Evidence leaves all future projections.",
        forgetting_after_expression_removes_future_anchor,
    ),
    Scenario(
        "forgetting_rebuilds_still_supported_chapter",
        "Forgetting one source can rebuild a still-supported immutable chapter",
        "The old chapter is invalidated while a new version uses only active Evidence.",
        forgetting_rebuilds_still_supported_chapter,
    ),
    Scenario(
        "forgetting_during_reservation_releases_old_turn",
        "Forgetting during a claim invalidates the stale prepared projection",
        "A new turn must claim the tightened boundary-only result.",
        forgetting_during_reservation_releases_old_turn,
    ),
    Scenario(
        "correction_preserves_independent_anniversary",
        "Correcting an academic profile does not erase a real anniversary",
        "The mistaken boundary is removed while the coalesced anniversary remains claimable.",
        correction_preserves_independent_anniversary,
    ),
)
