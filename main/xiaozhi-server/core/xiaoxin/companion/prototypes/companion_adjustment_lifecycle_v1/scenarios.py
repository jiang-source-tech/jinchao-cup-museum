"""Synthetic timelines for the adjustment lifecycle throwaway prototype."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable

from lifecycle_model import AdjustmentLifecycle, EvidenceSignal


DEFAULT_DIMENSION = "question_frequency"
DEFAULT_SCOPE = "conversation"


@dataclass(frozen=True)
class Check:
    label: str
    actual: object
    expected: object

    @property
    def passed(self) -> bool:
        return self.actual == self.expected

    def as_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "actual": self.actual,
            "expected": self.expected,
            "passed": self.passed,
        }


@dataclass(frozen=True)
class ScenarioRun:
    checks: tuple[Check, ...]
    timeline: tuple[dict[str, object], ...]
    final_state: dict[str, object]

    def canonical_json(self) -> str:
        return json.dumps(
            {
                "checks": [item.as_dict() for item in self.checks],
                "timeline": self.timeline,
                "final_state": self.final_state,
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


def _at(day: str, time: str = "10:00:00") -> str:
    return f"{day}T{time}+08:00"


def _signal(
    evidence_id: str,
    day: str,
    value: str,
    *,
    time: str = "10:00:00",
    dimension: str = DEFAULT_DIMENSION,
    scope: str = DEFAULT_SCOPE,
    epoch: str = "epoch-1",
    **overrides: object,
) -> EvidenceSignal:
    values: dict[str, object] = {
        "evidence_id": evidence_id,
        "occurred_at": _at(day, time),
        "dimension": dimension,
        "value": value,
        "relationship_epoch_id": epoch,
        "scope": scope,
    }
    values.update(overrides)
    return EvidenceSignal(**values)  # type: ignore[arg-type]


def _status(
    engine: AdjustmentLifecycle,
    value: str,
    *,
    dimension: str = DEFAULT_DIMENSION,
    scope: str = DEFAULT_SCOPE,
) -> str | None:
    return engine.current_status(
        dimension=dimension,
        scope=scope,
        value=value,
    )


def _reason(
    engine: AdjustmentLifecycle,
    value: str,
    *,
    dimension: str = DEFAULT_DIMENSION,
    scope: str = DEFAULT_SCOPE,
) -> str | None:
    return engine.current_terminal_reason(
        dimension=dimension,
        scope=scope,
        value=value,
    )


def _dates(
    engine: AdjustmentLifecycle,
    value: str,
    *,
    dimension: str = DEFAULT_DIMENSION,
    scope: str = DEFAULT_SCOPE,
) -> tuple[str, ...]:
    return engine.current_qualifying_dates(
        dimension=dimension,
        scope=scope,
        value=value,
    )


def _timeline_state(
    engine: AdjustmentLifecycle,
    label: str,
) -> dict[str, object]:
    return {
        "label": label,
        "epoch": engine.relationship_epoch_id,
        "effective": engine.effective_adjustments(),
        "contracts": {
            f"{key[0]}@{key[1]}": item.value
            for key, item in sorted(engine.contracts.items())
        },
        "adjustments": [
            {
                "id": item.adjustment_id,
                "value": item.value,
                "status": item.status,
                "days": list(item.qualifying_dates),
                "terminal_reason": item.terminal_reason,
            }
            for item in engine.adjustments
        ],
        "decision_counts": engine.decision_counts(),
    }


def _result(
    engine: AdjustmentLifecycle,
    checks: list[Check],
    timeline: list[dict[str, object]],
) -> ScenarioRun:
    return ScenarioRun(
        checks=tuple(checks),
        timeline=tuple(timeline),
        final_state=engine.snapshot(),
    )


def _seed_active(
    engine: AdjustmentLifecycle,
    *,
    value: str = "less",
    prefix: str = "base",
) -> None:
    for index, day in enumerate(("2026-07-01", "2026-07-02", "2026-07-03"), 1):
        engine.observe(_signal(f"{prefix}-{index}", day, value))


def cross_day_promotion() -> ScenarioRun:
    engine = AdjustmentLifecycle()
    checks: list[Check] = []
    timeline: list[dict[str, object]] = []

    engine.observe(_signal("promote-1", "2026-07-01", "less"))
    timeline.append(_timeline_state(engine, "day 1: first qualified signal"))
    checks.append(Check("day 1 is candidate", _status(engine, "less"), "candidate"))

    engine.observe(_signal("promote-2", "2026-07-02", "less"))
    timeline.append(_timeline_state(engine, "day 2: second qualified date"))
    checks.append(Check("day 2 is trial", _status(engine, "less"), "trial"))

    engine.observe(_signal("promote-3", "2026-07-03", "less"))
    timeline.append(_timeline_state(engine, "day 3: third qualified date"))
    checks.extend(
        (
            Check("day 3 is active", _status(engine, "less"), "active"),
            Check(
                "active adjustment is projected",
                engine.effective_adjustments(),
                {"question_frequency@conversation": "less"},
            ),
        )
    )
    return _result(engine, checks, timeline)


def same_day_repetition() -> ScenarioRun:
    engine = AdjustmentLifecycle()
    checks: list[Check] = []
    timeline: list[dict[str, object]] = []

    for index in range(12):
        engine.observe(
            _signal(
                f"same-day-{index}",
                "2026-07-01",
                "less",
                time=f"{8 + index:02d}:00:00",
            )
        )
    engine.observe(_signal("same-day-0", "2026-07-01", "less", time="21:00:00"))
    timeline.append(_timeline_state(engine, "12 signals and one duplicate on one date"))
    checks.extend(
        (
            Check("same day remains candidate", _status(engine, "less"), "candidate"),
            Check(
                "same day contributes one date",
                _dates(engine, "less"),
                ("2026-07-01",),
            ),
            Check("duplicate id is stored once", len(engine.evidence), 12),
        )
    )
    return _result(engine, checks, timeline)


def model_confidence_is_not_a_vote() -> ScenarioRun:
    engine = AdjustmentLifecycle()
    checks: list[Check] = []
    timeline: list[dict[str, object]] = []

    weak = engine.observe(
        _signal(
            "generic-high-confidence",
            "2026-07-01",
            "less",
            evidence_kind="accepted_help",
            attribution="observed_interaction",
            specificity="generic",
            model_confidence=0.99,
        )
    )
    for index, (day, confidence) in enumerate(
        (
            ("2026-07-02", 0.01),
            ("2026-07-03", 0.50),
            ("2026-07-04", 0.90),
        ),
        1,
    ):
        engine.observe(
            _signal(
                f"verified-{index}",
                day,
                "less",
                model_confidence=confidence,
            )
        )
    timeline.append(_timeline_state(engine, "confidence values do not change votes"))
    checks.extend(
        (
            Check("generic result is candidate-only", weak.route, "candidate_only"),
            Check("three verified dates activate", _status(engine, "less"), "active"),
            Check(
                "only structurally qualified dates count",
                _dates(engine, "less"),
                ("2026-07-02", "2026-07-03", "2026-07-04"),
            ),
        )
    )
    return _result(engine, checks, timeline)


def candidate_only_never_promotes() -> ScenarioRun:
    engine = AdjustmentLifecycle()
    checks: list[Check] = []
    timeline: list[dict[str, object]] = []

    for index, day in enumerate(("2026-07-01", "2026-07-02", "2026-07-03"), 1):
        engine.observe(
            _signal(
                f"indirect-{index}",
                day,
                "less",
                evidence_kind="accepted_help",
                attribution="observed_interaction",
                specificity="generic",
            )
        )
    timeline.append(_timeline_state(engine, "three indirect outcomes"))
    checks.extend(
        (
            Check("indirect outcomes remain candidate", _status(engine, "less"), "candidate"),
            Check("indirect outcomes add no qualified date", _dates(engine, "less"), ()),
            Check("candidate is not projected", engine.effective_adjustments(), {}),
        )
    )
    return _result(engine, checks, timeline)


def single_counter_signal() -> ScenarioRun:
    engine = AdjustmentLifecycle()
    _seed_active(engine, value="less")
    checks: list[Check] = []
    timeline = [_timeline_state(engine, "incumbent is active")]

    engine.observe(_signal("counter-1", "2026-07-04", "often"))
    timeline.append(_timeline_state(engine, "one opposite qualified date"))
    checks.extend(
        (
            Check("old direction stays active", _status(engine, "less"), "active"),
            Check("opposite is only candidate", _status(engine, "often"), "candidate"),
            Check(
                "one opposite signal does not flip policy",
                engine.effective_adjustments(),
                {"question_frequency@conversation": "less"},
            ),
        )
    )
    return _result(engine, checks, timeline)


def sustained_counterevidence() -> ScenarioRun:
    engine = AdjustmentLifecycle()
    _seed_active(engine, value="less")
    checks: list[Check] = []
    timeline = [_timeline_state(engine, "old direction active")]

    engine.observe(_signal("counter-1", "2026-07-04", "often"))
    after_one = engine.effective_adjustments()
    timeline.append(_timeline_state(engine, "counter day 1: no flip"))

    engine.observe(_signal("counter-2", "2026-07-05", "often"))
    after_two = engine.effective_adjustments()
    timeline.append(_timeline_state(engine, "counter day 2: baseline"))

    engine.observe(_signal("counter-3", "2026-07-06", "often"))
    after_three = engine.effective_adjustments()
    timeline.append(_timeline_state(engine, "counter day 3: opposite active"))
    checks.extend(
        (
            Check(
                "first contrary day preserves old direction",
                after_one,
                {"question_frequency@conversation": "less"},
            ),
            Check("second contrary day returns to baseline", after_two, {}),
            Check(
                "third contrary day activates opposite direction",
                after_three,
                {"question_frequency@conversation": "often"},
            ),
            Check("old direction is superseded", _status(engine, "less"), "superseded"),
            Check("opposite direction is active", _status(engine, "often"), "active"),
        )
    )
    return _result(engine, checks, timeline)


def reaffirmation_cancels_weak_challenge() -> ScenarioRun:
    engine = AdjustmentLifecycle()
    _seed_active(engine, value="less")
    checks: list[Check] = []
    timeline: list[dict[str, object]] = []

    engine.observe(_signal("weak-counter", "2026-07-04", "often"))
    timeline.append(_timeline_state(engine, "one contrary candidate"))
    engine.observe(_signal("reaffirm-old", "2026-07-05", "less"))
    timeline.append(_timeline_state(engine, "old direction reaffirmed"))
    checks.extend(
        (
            Check("incumbent remains active", _status(engine, "less"), "active"),
            Check("weak challenge is superseded", _status(engine, "often"), "superseded"),
            Check(
                "projection remains stable",
                engine.effective_adjustments(),
                {"question_frequency@conversation": "less"},
            ),
        )
    )
    return _result(engine, checks, timeline)


def explicit_user_correction() -> ScenarioRun:
    engine = AdjustmentLifecycle()
    _seed_active(engine, value="less")
    checks: list[Check] = []
    timeline = [_timeline_state(engine, "learned adjustment active")]

    engine.correct_adjustment(
        dimension=DEFAULT_DIMENSION,
        scope=DEFAULT_SCOPE,
        now=_at("2026-07-04"),
    )
    timeline.append(_timeline_state(engine, "user explicitly corrects the inference"))
    status_after_correction = _status(engine, "less")
    reason_after_correction = _reason(engine, "less")
    engine.observe(_signal("post-correction-new", "2026-07-05", "less"))
    timeline.append(_timeline_state(engine, "new Evidence starts from zero"))
    checks.extend(
        (
            Check("correction revokes immediately", status_after_correction, "revoked"),
            Check(
                "correction has explicit terminal reason",
                reason_after_correction,
                "explicit_user_correction",
            ),
            Check("correction creates no opposite guess", _status(engine, "often"), None),
            Check("old Evidence is not automatically reused", _status(engine, "less"), "candidate"),
            Check(
                "new learning has only its new date",
                _dates(engine, "less"),
                ("2026-07-05",),
            ),
            Check("policy returns to baseline", engine.effective_adjustments(), {}),
        )
    )
    return _result(engine, checks, timeline)


def forgotten_decisive_evidence() -> ScenarioRun:
    engine = AdjustmentLifecycle()
    _seed_active(engine, value="less", prefix="forget")
    checks: list[Check] = []
    timeline = [_timeline_state(engine, "active before forgetting")]

    engine.forget_evidence("forget-2", now=_at("2026-07-04"))
    timeline.append(_timeline_state(engine, "one decisive source is forgotten"))
    old, rebuilt = engine.adjustments
    checks.extend(
        (
            Check("old derived record is revoked", old.status, "revoked"),
            Check(
                "forget reason is traceable",
                old.terminal_reason,
                "source_evidence_forgotten",
            ),
            Check("two remaining dates rebuild trial", rebuilt.status, "trial"),
            Check(
                "forgotten source is absent from rebuilt lineage",
                "forget-2" in rebuilt.evidence_ids,
                False,
            ),
            Check("forgotten source cannot remain effective", engine.effective_adjustments(), {}),
        )
    )
    return _result(engine, checks, timeline)


def robust_adjustment_survives_one_forgotten_source() -> ScenarioRun:
    engine = AdjustmentLifecycle()
    checks: list[Check] = []
    timeline: list[dict[str, object]] = []
    for index, day in enumerate(
        (
            "2026-07-01",
            "2026-07-02",
            "2026-07-03",
            "2026-07-04",
            "2026-07-05",
        ),
        1,
    ):
        engine.observe(_signal(f"robust-{index}", day, "less"))
    timeline.append(_timeline_state(engine, "active with five independent dates"))

    engine.forget_evidence("robust-3", now=_at("2026-07-06"))
    timeline.append(_timeline_state(engine, "one of five sources is forgotten"))
    old, rebuilt = engine.adjustments
    checks.extend(
        (
            Check("old lineage is revoked", old.status, "revoked"),
            Check("four remaining dates rebuild active", rebuilt.status, "active"),
            Check("rebuilt lineage has four sources", len(rebuilt.qualifying_evidence_ids), 4),
            Check("forgotten source is not reused", "robust-3" in rebuilt.evidence_ids, False),
            Check(
                "robust behavior remains effective",
                engine.effective_adjustments(),
                {"question_frequency@conversation": "less"},
            ),
        )
    )
    return _result(engine, checks, timeline)


def nondecisive_source_removal_keeps_active_record() -> ScenarioRun:
    engine = AdjustmentLifecycle()
    checks: list[Check] = []
    timeline: list[dict[str, object]] = []
    engine.observe(
        _signal(
            "weak-seed",
            "2026-06-30",
            "less",
            evidence_kind="accepted_help",
            attribution="observed_interaction",
            specificity="generic",
        )
    )
    _seed_active(engine, value="less", prefix="strong")
    timeline.append(_timeline_state(engine, "active with a weak seed and three qualified dates"))

    engine.forget_evidence("weak-seed", now=_at("2026-07-04"))
    timeline.append(_timeline_state(engine, "nondecisive weak seed is forgotten"))
    checks.extend(
        (
            Check("active record remains current", len(engine.adjustments), 1),
            Check("weak source was never a promotion vote", _dates(engine, "less"), (
                "2026-07-01",
                "2026-07-02",
                "2026-07-03",
            )),
            Check("active status remains", _status(engine, "less"), "active"),
            Check(
                "effective behavior remains",
                engine.effective_adjustments(),
                {"question_frequency@conversation": "less"},
            ),
        )
    )
    return _result(engine, checks, timeline)


def expired_decisive_evidence() -> ScenarioRun:
    engine = AdjustmentLifecycle()
    checks: list[Check] = []
    timeline: list[dict[str, object]] = []
    engine.observe(
        _signal(
            "expiring-1",
            "2026-07-01",
            "less",
            expires_at=_at("2026-07-05"),
        )
    )
    engine.observe(_signal("expiring-2", "2026-07-02", "less"))
    engine.observe(_signal("expiring-3", "2026-07-03", "less"))
    timeline.append(_timeline_state(engine, "active while every source is valid"))

    engine.advance_time(_at("2026-07-05"))
    timeline.append(_timeline_state(engine, "a decisive source expires"))
    old, rebuilt = engine.adjustments
    checks.extend(
        (
            Check("source expiry revokes old lineage", old.status, "revoked"),
            Check(
                "source expiry reason is traceable",
                old.terminal_reason,
                "source_evidence_expired",
            ),
            Check("remaining sources rebuild trial", rebuilt.status, "trial"),
            Check(
                "expired source is absent from rebuilt lineage",
                "expiring-1" in rebuilt.evidence_ids,
                False,
            ),
            Check("expired source is not projected", engine.effective_adjustments(), {}),
        )
    )
    return _result(engine, checks, timeline)


def reinforcement_windows_expire() -> ScenarioRun:
    engine = AdjustmentLifecycle()
    checks: list[Check] = []
    timeline: list[dict[str, object]] = []

    engine.observe(_signal("candidate-window", "2026-07-01", "less"))
    engine.advance_time(_at("2026-07-31"))
    timeline.append(_timeline_state(engine, "candidate after 30 days without reinforcement"))
    candidate_status = _status(engine, "less")

    engine.observe(
        _signal(
            "trial-window-1",
            "2026-08-01",
            "low",
            dimension="humor_level",
        )
    )
    engine.observe(
        _signal(
            "trial-window-2",
            "2026-08-02",
            "low",
            dimension="humor_level",
        )
    )
    engine.advance_time(_at("2026-10-01"))
    timeline.append(_timeline_state(engine, "trial after 60 days without revalidation"))
    trial_status = _status(engine, "low", dimension="humor_level")
    checks.extend(
        (
            Check("candidate expires after 30 days", candidate_status, "expired"),
            Check("trial expires after 60 days", trial_status, "expired"),
            Check("stale hypotheses never project", engine.effective_adjustments(), {}),
        )
    )
    return _result(engine, checks, timeline)


def relationship_reset_keeps_contract_only() -> ScenarioRun:
    engine = AdjustmentLifecycle()
    engine.set_contract(
        dimension="response_length",
        scope=DEFAULT_SCOPE,
        value="short",
        now=_at("2026-06-30"),
    )
    _seed_active(engine, value="less")
    checks: list[Check] = []
    timeline = [_timeline_state(engine, "contract and learned adjustment before reset")]

    engine.reset_relationship(new_epoch_id="epoch-2", now=_at("2026-07-04"))
    timeline.append(_timeline_state(engine, "new relationship epoch"))
    checks.extend(
        (
            Check("old adjustment is revoked", _status(engine, "less"), "revoked"),
            Check(
                "old adjustment has reset reason",
                _reason(engine, "less"),
                "relationship_reset",
            ),
            Check(
                "new epoch starts without implicit adjustment",
                engine.effective_adjustments(),
                {},
            ),
            Check("interaction contract is retained", len(engine.contracts), 1),
            Check("epoch changes", engine.relationship_epoch_id, "epoch-2"),
        )
    )
    return _result(engine, checks, timeline)


def unsafe_or_ambiguous_sources_are_rejected() -> ScenarioRun:
    engine = AdjustmentLifecycle()
    checks: list[Check] = []
    timeline: list[dict[str, object]] = []
    cases = (
        ("unknown-speaker", {"speaker_identity": "unknown"}),
        ("reported", {"claim_context": "reported"}),
        ("hypothetical", {"claim_context": "hypothetical"}),
        ("joke", {"claim_context": "joke"}),
        ("asr", {"claim_context": "asr_uncertain"}),
        ("short-mood", {"temporal_scope": "short_term_state"}),
        ("user-fact", {"ownership_scope": "user"}),
        ("old-epoch", {"epoch": "epoch-0"}),
        (
            "model-only",
            {
                "evidence_kind": "model_inference",
                "attribution": "model_inference",
                "model_confidence": 1.0,
            },
        ),
    )
    for index, (name, overrides) in enumerate(cases, 1):
        signal_overrides = dict(overrides)
        epoch = str(signal_overrides.pop("epoch", "epoch-1"))
        engine.observe(
            _signal(
                f"reject-{index}-{name}",
                "2026-07-01",
                "less",
                time=f"{8 + index:02d}:00:00",
                epoch=epoch,
                **signal_overrides,
            )
        )
    timeline.append(_timeline_state(engine, "nine unsafe or ambiguous sources"))
    checks.extend(
        (
            Check(
                "every unsafe source is rejected",
                engine.decision_counts(),
                {"qualifying": 0, "candidate_only": 0, "rejected": 9},
            ),
            Check("rejected sources create no adjustment", len(engine.adjustments), 0),
            Check("rejected sources never affect policy", engine.effective_adjustments(), {}),
        )
    )
    return _result(engine, checks, timeline)


SCENARIOS = (
    Scenario(
        "cross_day_promotion",
        "Three distinct dates promote candidate to trial to active",
        (
            "Only an active adjustment enters CompanionPolicy; "
            "the first two stages are observation states."
        ),
        cross_day_promotion,
    ),
    Scenario(
        "same_day_repetition",
        "Same-day repetition and duplicate delivery cannot farm growth",
        "A local calendar date contributes at most one vote, regardless of message volume.",
        same_day_repetition,
    ),
    Scenario(
        "model_confidence_is_not_a_vote",
        "Model confidence cannot replace structural Evidence",
        "Confidence is audit metadata; directness and provenance determine eligibility.",
        model_confidence_is_not_a_vote,
    ),
    Scenario(
        "candidate_only_never_promotes",
        "Indirect outcomes may seed a hypothesis but never activate it",
        "Generic helpfulness and completion do not prove which style caused the result.",
        candidate_only_never_promotes,
    ),
    Scenario(
        "single_counter_signal",
        "One contrary signal does not flip an established adjustment",
        "The old active behavior stays effective while the opposite direction is only a candidate.",
        single_counter_signal,
    ),
    Scenario(
        "sustained_counterevidence",
        "Contrary learning returns to baseline before changing direction",
        "Two contrary dates retire the old adjustment; the third activates the opposite direction.",
        sustained_counterevidence,
    ),
    Scenario(
        "reaffirmation_cancels_weak_challenge",
        "Reaffirmed behavior clears a one-day contrary hypothesis",
        "Alternating noise cannot accumulate silently into a future personality flip.",
        reaffirmation_cancels_weak_challenge,
    ),
    Scenario(
        "explicit_user_correction",
        "Explicit correction immediately revokes the inferred adjustment",
        "Correction denies the inference; it does not manufacture an opposite preference.",
        explicit_user_correction,
    ),
    Scenario(
        "forgotten_decisive_evidence",
        "Forgetting one of three decisive dates rebuilds a non-effective trial",
        "The old lineage is revoked and the forgotten source is excluded from recomputation.",
        forgotten_decisive_evidence,
    ),
    Scenario(
        "robust_adjustment_survives_one_forgotten_source",
        "Four remaining dates preserve behavior after one of five sources is forgotten",
        "Forgetting one source does not erase a behavior independently supported by enough dates.",
        robust_adjustment_survives_one_forgotten_source,
    ),
    Scenario(
        "nondecisive_source_removal_keeps_active_record",
        "Removing a weak candidate-only source does not revoke strong learning",
        "Only Evidence that determined lifecycle status can invalidate that derived status.",
        nondecisive_source_removal_keeps_active_record,
    ),
    Scenario(
        "expired_decisive_evidence",
        "Expired decisive Evidence revokes old lineage and rebuilds from valid sources",
        "Natural expiry and targeted forgetting share deterministic lineage recomputation.",
        expired_decisive_evidence,
    ),
    Scenario(
        "reinforcement_windows_expire",
        "Unreinforced candidate and trial hypotheses expire",
        "Candidate has a 30-day window and trial has a 60-day window; active has no silence TTL.",
        reinforcement_windows_expire,
    ),
    Scenario(
        "relationship_reset_keeps_contract_only",
        "Relationship reset removes implicit learning but retains user contracts",
        "The pet stays the same, while the new relationship epoch starts from the birth baseline.",
        relationship_reset_keeps_contract_only,
    ),
    Scenario(
        "unsafe_or_ambiguous_sources_are_rejected",
        "Unknown, quoted, hypothetical, playful, uncertain, and state signals are rejected",
        "No amount of confidence or repetition turns an unsafe source into personality Evidence.",
        unsafe_or_ambiguous_sources_are_rejected,
    ),
)
