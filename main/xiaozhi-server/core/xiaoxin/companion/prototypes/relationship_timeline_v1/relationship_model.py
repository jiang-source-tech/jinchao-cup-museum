"""PROTOTYPE ONLY: deterministic multi-year relationship timeline model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import json
from typing import Literal, Mapping
from zoneinfo import ZoneInfo


RelationshipStage = Literal[
    "first_meeting",
    "familiar",
    "attuned",
    "long_term_companion",
]
RelationshipPosture = Literal[
    "steady",
    "reunion_cautious",
    "repairing",
]

STAGE_ORDER: tuple[RelationshipStage, ...] = (
    "first_meeting",
    "familiar",
    "attuned",
    "long_term_companion",
)
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must include a timezone offset")
    return parsed


def _local_date(value: str | datetime) -> date:
    parsed = value if isinstance(value, datetime) else _aware_datetime(value)
    return parsed.astimezone(LOCAL_TIMEZONE).date()


def _week_key(value: date) -> str:
    iso_year, iso_week, _ = value.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


@dataclass(frozen=True)
class RelationshipMetrics:
    turn_count: int
    elapsed_days: int
    active_days: int
    active_weeks: int
    active_months: int
    knowledge_items: int
    helpful_days: int
    attunement_days: int
    helpful_events: int
    attunement_events: int
    negative_events: int


@dataclass(frozen=True)
class RecentRelationshipMetrics:
    active_days: int
    helpful_days: int
    attunement_days: int


@dataclass(frozen=True)
class StageGate:
    minimum_elapsed_days: int
    minimum_active_days: int
    minimum_active_weeks: int
    minimum_active_months: int
    minimum_knowledge_items: int
    minimum_helpful_days: int
    minimum_attunement_days: int
    recent_window_days: int
    minimum_recent_active_days: int
    minimum_recent_helpful_days: int
    minimum_recent_attunement_days: int

    def accepts(
        self,
        metrics: RelationshipMetrics,
        recent: RecentRelationshipMetrics,
    ) -> bool:
        return not self.missing(metrics, recent)

    def missing(
        self,
        metrics: RelationshipMetrics,
        recent: RecentRelationshipMetrics,
    ) -> dict[str, int]:
        requirements = {
            "elapsed_days": self.minimum_elapsed_days,
            "active_days": self.minimum_active_days,
            "active_weeks": self.minimum_active_weeks,
            "active_months": self.minimum_active_months,
            "knowledge_items": self.minimum_knowledge_items,
            "helpful_days": self.minimum_helpful_days,
            "attunement_days": self.minimum_attunement_days,
            "recent_active_days": self.minimum_recent_active_days,
            "recent_helpful_days": self.minimum_recent_helpful_days,
            "recent_attunement_days": self.minimum_recent_attunement_days,
        }
        actual = {
            "elapsed_days": metrics.elapsed_days,
            "active_days": metrics.active_days,
            "active_weeks": metrics.active_weeks,
            "active_months": metrics.active_months,
            "knowledge_items": metrics.knowledge_items,
            "helpful_days": metrics.helpful_days,
            "attunement_days": metrics.attunement_days,
            "recent_active_days": recent.active_days,
            "recent_helpful_days": recent.helpful_days,
            "recent_attunement_days": recent.attunement_days,
        }
        return {
            key: required - actual[key]
            for key, required in requirements.items()
            if actual[key] < required
        }


@dataclass(frozen=True)
class RelationshipTimelineConfig:
    version: str = "relationship-timeline-v1-candidate"
    familiar: StageGate = StageGate(14, 4, 2, 1, 2, 1, 1, 60, 2, 1, 1)
    attuned: StageGate = StageGate(90, 12, 8, 3, 5, 4, 3, 180, 4, 2, 1)
    long_term_companion: StageGate = StageGate(
        365, 36, 24, 9, 10, 8, 6, 365, 8, 2, 2
    )
    familiar_reunion_gap_days: int = 30
    attuned_reunion_gap_days: int = 60
    long_term_reunion_gap_days: int = 120
    reunion_recovery_days: int = 3
    repair_neutral_days: int = 3

    def gate(self, stage: RelationshipStage) -> StageGate | None:
        if stage == "familiar":
            return self.familiar
        if stage == "attuned":
            return self.attuned
        if stage == "long_term_companion":
            return self.long_term_companion
        return None

    def reunion_gap_days(self, stage: RelationshipStage) -> int | None:
        if stage == "familiar":
            return self.familiar_reunion_gap_days
        if stage == "attuned":
            return self.attuned_reunion_gap_days
        if stage == "long_term_companion":
            return self.long_term_reunion_gap_days
        return None


DEFAULT_CONFIG = RelationshipTimelineConfig()


@dataclass(frozen=True)
class RelationshipEvent:
    event_id: str
    occurred_at: str
    kind: str
    device_id: str = "device-a"
    key: str = ""
    turn_weight: int = 1

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id is required")
        _aware_datetime(self.occurred_at)
        if self.kind not in {
            "interaction",
            "knowledge",
            "helpful",
            "attunement",
            "negative_feedback",
            "positive_feedback",
        }:
            raise ValueError("unsupported relationship event kind")
        if self.kind == "knowledge" and not self.key.strip():
            raise ValueError("knowledge events require a stable key")
        if self.turn_weight < 1:
            raise ValueError("turn_weight must be positive")


class RelationshipTimeline:
    """In-memory promotion history plus temporary reunion and repair posture."""

    def __init__(
        self,
        *,
        config: RelationshipTimelineConfig = DEFAULT_CONFIG,
        relationship_epoch_id: str = "epoch-1",
        implicit_adjustments: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self.relationship_epoch_id = relationship_epoch_id
        self.stage: RelationshipStage = "first_meeting"
        self.implicit_adjustments = dict(implicit_adjustments or {})
        self._processed_ids: set[str] = set()
        self._last_event_at: datetime | None = None
        self._first_interaction_at: datetime | None = None
        self._last_interaction_at: datetime | None = None
        self._turn_count = 0
        self._active_dates: set[date] = set()
        self._knowledge_keys: set[str] = set()
        self._helpful_dates: set[date] = set()
        self._attunement_dates: set[date] = set()
        self._helpful_events = 0
        self._attunement_events = 0
        self._negative_events = 0
        self._devices_seen: set[str] = set()
        self._repairing = False
        self._last_negative_at: datetime | None = None
        self._repair_recovery_dates: set[date] = set()
        self._reunion_step = 0
        self._reunion_dates: set[date] = set()
        self.stage_events: list[dict[str, object]] = []
        self.trace: list[dict[str, object]] = []

    @property
    def posture(self) -> RelationshipPosture:
        if self._repairing:
            return "repairing"
        if self._reunion_step:
            return "reunion_cautious"
        return "steady"

    def observe(self, event: RelationshipEvent) -> None:
        if event.event_id in self._processed_ids:
            self._trace(event.occurred_at, "duplicate_ignored", event.event_id)
            return
        occurred_at = _aware_datetime(event.occurred_at)
        if self._last_event_at is not None and occurred_at < self._last_event_at:
            raise ValueError("prototype events must be replayed in chronological order")
        self._processed_ids.add(event.event_id)
        self._last_event_at = occurred_at
        self._devices_seen.add(event.device_id)
        local_day = _local_date(occurred_at)

        if event.kind == "interaction":
            self._observe_interaction(occurred_at, local_day, event.turn_weight)
        elif event.kind == "knowledge":
            self._knowledge_keys.add(event.key)
        elif event.kind == "helpful":
            self._helpful_events += 1
            self._helpful_dates.add(local_day)
        elif event.kind == "attunement":
            self._attunement_events += 1
            self._attunement_dates.add(local_day)
        elif event.kind == "negative_feedback":
            self._negative_events += 1
            self._repairing = True
            self._last_negative_at = occurred_at
            self._repair_recovery_dates.clear()
            self._reunion_step = 0
            self._reunion_dates.clear()
            self._trace(event.occurred_at, "repair_started", "negative_feedback")
        elif event.kind == "positive_feedback":
            self._attunement_events += 1
            self._attunement_dates.add(local_day)
            if self._repairing:
                self._repairing = False
                self._repair_recovery_dates.clear()
                self._trace(event.occurred_at, "repair_cleared", "positive_feedback")

        self._promote(occurred_at)

    def forget_knowledge(self, key: str, *, now: str) -> None:
        _aware_datetime(now)
        self._knowledge_keys.discard(key)
        self._trace(now, "knowledge_forgotten", key)

    def metrics(self, now: str | datetime) -> RelationshipMetrics:
        current = now if isinstance(now, datetime) else _aware_datetime(now)
        elapsed_days = 0
        if self._first_interaction_at is not None:
            elapsed_days = max(
                0,
                (
                    _local_date(current)
                    - _local_date(self._first_interaction_at)
                ).days,
            )
        return RelationshipMetrics(
            turn_count=self._turn_count,
            elapsed_days=elapsed_days,
            active_days=len(self._active_dates),
            active_weeks=len({_week_key(item) for item in self._active_dates}),
            active_months=len({item.strftime("%Y-%m") for item in self._active_dates}),
            knowledge_items=len(self._knowledge_keys),
            helpful_days=len(self._helpful_dates),
            attunement_days=len(self._attunement_dates),
            helpful_events=self._helpful_events,
            attunement_events=self._attunement_events,
            negative_events=self._negative_events,
        )

    def legacy_stage(self, now: str | datetime) -> RelationshipStage:
        metrics = self.metrics(now)
        if (
            metrics.turn_count >= 20
            and metrics.active_days >= 15
            and metrics.knowledge_items >= 6
            and metrics.helpful_events >= 4
            and metrics.attunement_events >= 4
        ):
            return "long_term_companion"
        if (
            metrics.turn_count >= 8
            and metrics.active_days >= 5
            and metrics.knowledge_items >= 3
            and metrics.helpful_events >= 2
            and metrics.attunement_events >= 2
        ):
            return "attuned"
        if (
            metrics.turn_count >= 3
            and metrics.active_days >= 2
            and metrics.knowledge_items >= 1
            and metrics.helpful_events >= 1
            and metrics.attunement_events >= 1
        ):
            return "familiar"
        return "first_meeting"

    def recent_metrics(
        self,
        now: str | datetime,
        *,
        window_days: int,
    ) -> RecentRelationshipMetrics:
        current_day = _local_date(now)

        def within_window(item: date) -> bool:
            age = (current_day - item).days
            return 0 <= age <= window_days

        return RecentRelationshipMetrics(
            active_days=sum(within_window(item) for item in self._active_dates),
            helpful_days=sum(within_window(item) for item in self._helpful_dates),
            attunement_days=sum(
                within_window(item) for item in self._attunement_dates
            ),
        )

    def projection(self) -> dict[str, object]:
        base = {
            "first_meeting": (1, 0, "disabled", "initial"),
            "familiar": (2, 1, "low", "familiar"),
            "attuned": (2, 2, "medium", "attuned"),
            "long_term_companion": (2, 3, "medium", "long_term"),
        }[self.stage]
        question_budget, memory_budget, initiative_level, expression = base
        memory_budget = min(memory_budget, len(self._knowledge_keys))
        adjustment_gain = 1.0
        reason_codes: list[str] = []
        if self.posture == "repairing":
            question_budget = min(question_budget, 1)
            memory_budget = 0
            initiative_level = "disabled"
            adjustment_gain = 0.0
            expression = "repairing"
            reason_codes.append("recent_negative_feedback")
        elif self.posture == "reunion_cautious":
            question_budget = min(question_budget, 1)
            memory_budget = min(memory_budget, 1)
            initiative_level = "disabled"
            adjustment_gain = 0.5 if self._reunion_step == 1 else 0.75
            expression = "reunion_cautious"
            reason_codes.append(f"reunion_recovery_step_{self._reunion_step}")
        return {
            "question_budget": question_budget,
            "memory_reference_budget": memory_budget,
            "initiative_level": initiative_level,
            "relationship_expression": expression,
            "implicit_adjustment_gain": adjustment_gain,
            "reason_codes": reason_codes,
        }

    def state(self, now: str) -> dict[str, object]:
        metrics = self.metrics(now)
        next_stage = self._next_stage()
        next_gate = self.config.gate(next_stage) if next_stage is not None else None
        next_recent = (
            self.recent_metrics(now, window_days=next_gate.recent_window_days)
            if next_gate is not None
            else None
        )
        return {
            "now": _aware_datetime(now).isoformat(),
            "relationship_epoch_id": self.relationship_epoch_id,
            "config_version": self.config.version,
            "stage": self.stage,
            "legacy_stage": self.legacy_stage(now),
            "posture": self.posture,
            "reunion_step": self._reunion_step,
            "metrics": asdict(metrics),
            "devices_seen": sorted(self._devices_seen),
            "implicit_adjustments": dict(sorted(self.implicit_adjustments.items())),
            "projection": self.projection(),
            "next_stage": next_stage,
            "next_gate_missing": (
                next_gate.missing(metrics, next_recent)
                if next_gate is not None and next_recent is not None
                else {}
            ),
            "stage_events": list(self.stage_events),
            "trace": list(self.trace),
        }

    def canonical_json(self, now: str) -> str:
        return json.dumps(
            self.state(now),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _observe_interaction(
        self,
        occurred_at: datetime,
        local_day: date,
        turn_weight: int,
    ) -> None:
        previous_interaction = self._last_interaction_at
        if previous_interaction is None:
            self._first_interaction_at = occurred_at
        else:
            gap_days = (local_day - _local_date(previous_interaction)).days
            reunion_gap = self.config.reunion_gap_days(self.stage)
            if (
                not self._repairing
                and not self._reunion_step
                and reunion_gap is not None
                and gap_days >= reunion_gap
            ):
                self._reunion_step = 1
                self._reunion_dates = {local_day}
                self._trace(
                    occurred_at.isoformat(),
                    "reunion_started",
                    f"gap_days={gap_days}",
                )
            elif self._reunion_step and local_day not in self._reunion_dates:
                self._reunion_dates.add(local_day)
                self._reunion_step = len(self._reunion_dates)
                if self._reunion_step >= self.config.reunion_recovery_days:
                    self._reunion_step = 0
                    self._reunion_dates.clear()
                    self._trace(
                        occurred_at.isoformat(),
                        "reunion_cleared",
                        "distinct_return_days_reached",
                    )

        if (
            self._repairing
            and self._last_negative_at is not None
            and occurred_at > self._last_negative_at
            and local_day not in self._repair_recovery_dates
        ):
            self._repair_recovery_dates.add(local_day)
            if len(self._repair_recovery_dates) >= self.config.repair_neutral_days:
                self._repairing = False
                self._repair_recovery_dates.clear()
                self._trace(
                    occurred_at.isoformat(),
                    "repair_cleared",
                    "neutral_interaction_days_reached",
                )

        self._turn_count += turn_weight
        self._active_dates.add(local_day)
        self._last_interaction_at = occurred_at

    def _promote(self, now: datetime) -> None:
        # Reunion limits current expression; it does not erase qualified history.
        if self.posture == "repairing":
            return
        metrics = self.metrics(now)
        current_index = STAGE_ORDER.index(self.stage)
        for candidate in STAGE_ORDER[current_index + 1 :]:
            gate = self.config.gate(candidate)
            if gate is None:
                break
            recent = self.recent_metrics(
                now,
                window_days=gate.recent_window_days,
            )
            if not gate.accepts(metrics, recent):
                break
            previous = self.stage
            self.stage = candidate
            event = {
                "from_stage": previous,
                "to_stage": candidate,
                "occurred_at": now.isoformat(),
                "reason_codes": [
                    "minimum_relationship_span",
                    "distributed_active_days",
                    "distributed_active_weeks",
                    "distributed_active_months",
                    "reliable_user_knowledge",
                    "confirmed_helpfulness_days",
                    "positive_attunement_days",
                ],
            }
            self.stage_events.append(event)
            self._trace(now.isoformat(), "stage_promoted", candidate)

    def _next_stage(self) -> RelationshipStage | None:
        index = STAGE_ORDER.index(self.stage)
        if index == len(STAGE_ORDER) - 1:
            return None
        return STAGE_ORDER[index + 1]

    def _trace(self, occurred_at: str, action: str, target: str) -> None:
        self.trace.append(
            {
                "sequence": len(self.trace) + 1,
                "occurred_at": occurred_at,
                "action": action,
                "target": target,
            }
        )
