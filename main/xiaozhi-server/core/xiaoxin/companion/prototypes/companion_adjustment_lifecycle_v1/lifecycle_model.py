"""PROTOTYPE ONLY: deterministic Evidence qualification and adjustment lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
from typing import Literal
from zoneinfo import ZoneInfo


EvidenceRoute = Literal["qualifying", "candidate_only", "rejected"]
AdjustmentStatus = Literal[
    "candidate",
    "trial",
    "active",
    "superseded",
    "expired",
    "revoked",
]

LIVE_ADJUSTMENT_STATUSES = frozenset({"candidate", "trial", "active"})
TERMINAL_ADJUSTMENT_STATUSES = frozenset({"superseded", "expired", "revoked"})

PROMOTION_EVIDENCE_KINDS = frozenset(
    {
        "interaction_feedback",
        "preference_feedback",
    }
)
CANDIDATE_ONLY_EVIDENCE_KINDS = frozenset(
    {
        "accepted_help",
        "followup_completed",
        "interaction_outcome",
        "response_reaction",
    }
)
REJECTED_EVIDENCE_KINDS = frozenset(
    {
        "assistant_action",
        "current_mood",
        "future_event",
        "goal",
        "meaningful_moment",
        "model_inference",
        "profile_fact",
        "recent_conversation",
        "system_event",
    }
)

CANDIDATE_TTL_DAYS = 30
TRIAL_TTL_DAYS = 60
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return parsed


def _local_date(value: str) -> str:
    return _aware_datetime(value).astimezone(LOCAL_TIMEZONE).date().isoformat()


def _deadline(value: str, *, days: int) -> str:
    return (_aware_datetime(value) + timedelta(days=days)).isoformat()


@dataclass(frozen=True)
class EvidenceSignal:
    evidence_id: str
    occurred_at: str
    dimension: str
    value: str
    relationship_epoch_id: str
    scope: str = "conversation"
    evidence_kind: str = "interaction_feedback"
    attribution: str = "explicit_user_feedback"
    speaker_identity: str = "confirmed"
    ownership_scope: str = "relationship"
    claim_context: str = "direct"
    source_certainty: str = "verified"
    specificity: str = "behavior_specific"
    behavior_linked: bool = True
    temporal_scope: str = "behavior_pattern"
    status: str = "active"
    expires_at: str | None = None
    source_ref: str = ""
    model_confidence: float = 0.5

    def __post_init__(self) -> None:
        if not self.evidence_id.strip():
            raise ValueError("evidence_id is required")
        _aware_datetime(self.occurred_at)
        if self.expires_at is not None:
            _aware_datetime(self.expires_at)
        if not 0.0 <= self.model_confidence <= 1.0:
            raise ValueError("model_confidence must be between 0 and 1")

    @property
    def key(self) -> tuple[str, str]:
        return (self.dimension, self.scope)


@dataclass(frozen=True)
class EvidenceDecision:
    route: EvidenceRoute
    reason_code: str


@dataclass
class AdjustmentRecord:
    adjustment_id: str
    relationship_epoch_id: str
    dimension: str
    scope: str
    value: str
    status: AdjustmentStatus
    created_at: str
    updated_at: str
    valid_until: str | None
    evidence_ids: list[str] = field(default_factory=list)
    qualifying_evidence_ids: list[str] = field(default_factory=list)
    qualifying_dates: list[str] = field(default_factory=list)
    terminal_reason: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.dimension, self.scope)

    def as_dict(self) -> dict[str, object]:
        return {
            "adjustment_id": self.adjustment_id,
            "relationship_epoch_id": self.relationship_epoch_id,
            "dimension": self.dimension,
            "scope": self.scope,
            "value": self.value,
            "status": self.status,
            "evidence_ids": list(self.evidence_ids),
            "qualifying_evidence_ids": list(self.qualifying_evidence_ids),
            "qualifying_dates": list(self.qualifying_dates),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "valid_until": self.valid_until,
            "terminal_reason": self.terminal_reason,
        }


@dataclass(frozen=True)
class ContractRecord:
    dimension: str
    scope: str
    value: str
    created_at: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.dimension, self.scope)

    def as_dict(self) -> dict[str, str]:
        return {
            "dimension": self.dimension,
            "scope": self.scope,
            "value": self.value,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class TraceEntry:
    sequence: int
    action: str
    reason_code: str
    target: str

    def as_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "action": self.action,
            "reason_code": self.reason_code,
            "target": self.target,
        }


def qualify_evidence(
    signal: EvidenceSignal,
    *,
    current_epoch_id: str,
    now: str | None = None,
) -> EvidenceDecision:
    """Classify Evidence without trusting model confidence as a lifecycle vote."""

    effective_now = _aware_datetime(now or signal.occurred_at)
    if signal.speaker_identity != "confirmed":
        return EvidenceDecision("rejected", "speaker_not_confirmed")
    if signal.ownership_scope != "relationship":
        return EvidenceDecision("rejected", "not_relationship_evidence")
    if signal.relationship_epoch_id != current_epoch_id:
        return EvidenceDecision("rejected", "relationship_epoch_mismatch")
    if signal.status != "active":
        return EvidenceDecision("rejected", "evidence_not_active")
    if (
        signal.expires_at is not None
        and _aware_datetime(signal.expires_at) <= effective_now
    ):
        return EvidenceDecision("rejected", "evidence_already_expired")
    if signal.claim_context != "direct":
        return EvidenceDecision(
            "rejected",
            {
                "reported": "reported_speech_rejected",
                "hypothetical": "hypothetical_rejected",
                "joke": "joke_rejected",
                "quoted": "quoted_text_rejected",
                "asr_uncertain": "asr_uncertain_rejected",
            }.get(signal.claim_context, "non_direct_claim_rejected"),
        )
    if signal.source_certainty != "verified":
        return EvidenceDecision("rejected", "source_uncertain")
    if signal.temporal_scope == "short_term_state":
        return EvidenceDecision("rejected", "short_term_state_not_growth")
    if not signal.behavior_linked or not signal.dimension or not signal.value:
        return EvidenceDecision("rejected", "not_linked_to_specific_behavior")
    if signal.evidence_kind in REJECTED_EVIDENCE_KINDS:
        return EvidenceDecision("rejected", "evidence_kind_not_behavior_learning")
    if signal.attribution in {"model_inference", "assistant_inference"}:
        return EvidenceDecision("rejected", "model_inference_not_evidence")
    if (
        signal.evidence_kind in PROMOTION_EVIDENCE_KINDS
        and signal.attribution == "explicit_user_feedback"
        and signal.specificity == "behavior_specific"
    ):
        return EvidenceDecision("qualifying", "specific_direct_user_feedback")
    if (
        signal.evidence_kind in CANDIDATE_ONLY_EVIDENCE_KINDS
        or signal.specificity == "generic"
        or signal.attribution
        in {"observed_interaction", "observed_business_event"}
    ):
        return EvidenceDecision("candidate_only", "indirect_outcome_candidate_only")
    return EvidenceDecision("rejected", "unsupported_evidence_shape")


class AdjustmentLifecycle:
    """In-memory reducer for the #7 lifecycle decision; never touches production data."""

    def __init__(self, *, relationship_epoch_id: str = "epoch-1") -> None:
        self.relationship_epoch_id = relationship_epoch_id
        self.evidence: dict[str, EvidenceSignal] = {}
        self.evidence_states: dict[str, str] = {}
        self.evidence_decisions: dict[str, EvidenceDecision] = {}
        self.adjustments: list[AdjustmentRecord] = []
        self.contracts: dict[tuple[str, str], ContractRecord] = {}
        self.trace: list[TraceEntry] = []
        self._adjustment_sequence = 0
        self._trace_sequence = 0

    def observe(self, signal: EvidenceSignal) -> EvidenceDecision:
        self.advance_time(signal.occurred_at)
        if signal.evidence_id in self.evidence:
            self._record_trace(
                "ignore",
                "duplicate_evidence_id",
                signal.evidence_id,
            )
            return self.evidence_decisions[signal.evidence_id]

        decision = qualify_evidence(
            signal,
            current_epoch_id=self.relationship_epoch_id,
            now=signal.occurred_at,
        )
        self.evidence[signal.evidence_id] = signal
        self.evidence_states[signal.evidence_id] = signal.status
        self.evidence_decisions[signal.evidence_id] = decision
        if decision.route == "rejected":
            self._record_trace(
                "reject_evidence",
                decision.reason_code,
                signal.evidence_id,
            )
            return decision
        if decision.route == "candidate_only":
            self._apply_candidate_only(signal)
            return decision
        self._apply_qualifying(signal)
        return decision

    def advance_time(self, now: str) -> None:
        effective_now = _aware_datetime(now)
        for evidence_id, signal in tuple(self.evidence.items()):
            if self.evidence_states[evidence_id] != "active":
                continue
            if (
                signal.expires_at is not None
                and _aware_datetime(signal.expires_at) <= effective_now
            ):
                self.evidence_states[evidence_id] = "expired"
                self._invalidate_evidence_dependents(
                    evidence_id,
                    reason_code="source_evidence_expired",
                    now=now,
                )
        for adjustment in self.adjustments:
            if adjustment.status not in {"candidate", "trial"}:
                continue
            if (
                adjustment.valid_until is not None
                and _aware_datetime(adjustment.valid_until) <= effective_now
            ):
                self._terminate(
                    adjustment,
                    status="expired",
                    reason_code="reinforcement_window_elapsed",
                    now=now,
                )

    def forget_evidence(self, evidence_id: str, *, now: str) -> None:
        self.advance_time(now)
        if evidence_id not in self.evidence:
            self._record_trace("ignore", "evidence_not_found", evidence_id)
            return
        if self.evidence_states[evidence_id] != "active":
            self._record_trace("ignore", "evidence_already_inactive", evidence_id)
            return
        self.evidence_states[evidence_id] = "forgotten"
        self._invalidate_evidence_dependents(
            evidence_id,
            reason_code="source_evidence_forgotten",
            now=now,
        )

    def correct_adjustment(
        self,
        *,
        dimension: str,
        scope: str,
        now: str,
    ) -> None:
        self.advance_time(now)
        key = (dimension, scope)
        matched = False
        for adjustment in self._live_adjustments(key):
            matched = True
            self._terminate(
                adjustment,
                status="revoked",
                reason_code="explicit_user_correction",
                now=now,
            )
        if not matched:
            self._record_trace(
                "ignore",
                "no_live_adjustment_to_correct",
                self._key_label(key),
            )

    def set_contract(
        self,
        *,
        dimension: str,
        scope: str,
        value: str,
        now: str,
    ) -> None:
        self.advance_time(now)
        key = (dimension, scope)
        for adjustment in self._live_adjustments(key):
            self._terminate(
                adjustment,
                status="revoked",
                reason_code="interaction_contract_set",
                now=now,
            )
        self.contracts[key] = ContractRecord(
            dimension=dimension,
            scope=scope,
            value=value,
            created_at=now,
        )
        self._record_trace(
            "set_contract",
            "explicit_contract_bypasses_learning",
            self._key_label(key),
        )

    def reset_relationship(self, *, new_epoch_id: str, now: str) -> None:
        self.advance_time(now)
        old_epoch_id = self.relationship_epoch_id
        for adjustment in self.adjustments:
            if (
                adjustment.relationship_epoch_id == old_epoch_id
                and adjustment.status in LIVE_ADJUSTMENT_STATUSES
            ):
                self._terminate(
                    adjustment,
                    status="revoked",
                    reason_code="relationship_reset",
                    now=now,
                )
        self.relationship_epoch_id = new_epoch_id
        self._record_trace(
            "reset_relationship",
            "new_relationship_epoch",
            new_epoch_id,
        )

    def current_status(
        self,
        *,
        dimension: str,
        scope: str,
        value: str,
    ) -> str | None:
        for adjustment in reversed(self.adjustments):
            if (
                adjustment.dimension == dimension
                and adjustment.scope == scope
                and adjustment.value == value
            ):
                return adjustment.status
        return None

    def current_terminal_reason(
        self,
        *,
        dimension: str,
        scope: str,
        value: str,
    ) -> str | None:
        for adjustment in reversed(self.adjustments):
            if (
                adjustment.dimension == dimension
                and adjustment.scope == scope
                and adjustment.value == value
            ):
                return adjustment.terminal_reason
        return None

    def current_qualifying_dates(
        self,
        *,
        dimension: str,
        scope: str,
        value: str,
    ) -> tuple[str, ...]:
        for adjustment in reversed(self.adjustments):
            if (
                adjustment.dimension == dimension
                and adjustment.scope == scope
                and adjustment.value == value
            ):
                return tuple(adjustment.qualifying_dates)
        return ()

    def effective_adjustments(self) -> dict[str, str]:
        effective: dict[str, str] = {}
        for adjustment in self.adjustments:
            if (
                adjustment.relationship_epoch_id == self.relationship_epoch_id
                and adjustment.status == "active"
                and adjustment.key not in self.contracts
            ):
                effective[self._key_label(adjustment.key)] = adjustment.value
        return effective

    def decision_counts(self) -> dict[str, int]:
        counts = {"qualifying": 0, "candidate_only": 0, "rejected": 0}
        for decision in self.evidence_decisions.values():
            counts[decision.route] += 1
        return counts

    def snapshot(self) -> dict[str, object]:
        return {
            "relationship_epoch_id": self.relationship_epoch_id,
            "effective_adjustments": self.effective_adjustments(),
            "contracts": [
                item.as_dict()
                for _, item in sorted(self.contracts.items())
            ],
            "evidence_decisions": {
                evidence_id: {
                    "route": decision.route,
                    "reason_code": decision.reason_code,
                    "state": self.evidence_states[evidence_id],
                }
                for evidence_id, decision in sorted(self.evidence_decisions.items())
            },
            "adjustments": [item.as_dict() for item in self.adjustments],
            "trace": [item.as_dict() for item in self.trace],
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.snapshot(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _apply_candidate_only(self, signal: EvidenceSignal) -> None:
        same = self._latest_live(signal.key, value=signal.value)
        if same is not None:
            self._append_evidence(same, signal.evidence_id, qualifying=False)
            self._record_trace(
                "retain_hypothesis",
                "candidate_only_does_not_promote",
                same.adjustment_id,
            )
            return
        if self._live_adjustments(signal.key):
            self._record_trace(
                "ignore_challenge",
                "candidate_only_cannot_replace_live_hypothesis",
                signal.evidence_id,
            )
            return
        adjustment = self._create_adjustment(signal, qualifying=False)
        self._record_trace(
            "create_candidate",
            "candidate_only_seed",
            adjustment.adjustment_id,
        )

    def _apply_qualifying(self, signal: EvidenceSignal) -> None:
        same = self._latest_live(signal.key, value=signal.value)
        if same is None:
            conflicts = self._live_adjustments(signal.key)
            if not any(item.status == "active" for item in conflicts):
                for conflict in conflicts:
                    self._terminate(
                        conflict,
                        status="superseded",
                        reason_code="new_qualified_direction",
                        now=signal.occurred_at,
                    )
            same = self._create_adjustment(signal, qualifying=True)
            self._record_trace(
                "create_candidate",
                "first_qualified_day",
                same.adjustment_id,
            )
            return

        self._append_evidence(same, signal.evidence_id, qualifying=True)
        local_date = _local_date(signal.occurred_at)
        if local_date in same.qualifying_dates:
            self._record_trace(
                "retain_status",
                "same_local_day_does_not_add_vote",
                same.adjustment_id,
            )
            return
        same.qualifying_dates.append(local_date)
        same.qualifying_evidence_ids.append(signal.evidence_id)
        same.updated_at = signal.occurred_at
        prior_status = same.status
        qualified_days = len(same.qualifying_dates)
        if qualified_days >= 3:
            same.status = "active"
            same.valid_until = None
        elif qualified_days >= 2:
            same.status = "trial"
            same.valid_until = _deadline(signal.occurred_at, days=TRIAL_TTL_DAYS)
        else:
            same.status = "candidate"
            same.valid_until = _deadline(signal.occurred_at, days=CANDIDATE_TTL_DAYS)

        if same.status == "candidate":
            self._record_trace(
                "retain_status",
                "one_qualified_day_only",
                same.adjustment_id,
            )
            return

        for conflict in self._live_adjustments(signal.key):
            if conflict.adjustment_id == same.adjustment_id:
                continue
            self._terminate(
                conflict,
                status="superseded",
                reason_code=(
                    "sustained_counterevidence_returns_to_baseline"
                    if same.status == "trial" and conflict.status == "active"
                    else "qualified_direction_supersedes_conflict"
                ),
                now=signal.occurred_at,
            )
        self._record_trace(
            "promote",
            (
                "three_distinct_days_activate"
                if same.status == "active"
                else "two_distinct_days_start_trial"
            ),
            f"{same.adjustment_id}:{prior_status}->{same.status}",
        )

    def _create_adjustment(
        self,
        signal: EvidenceSignal,
        *,
        qualifying: bool,
    ) -> AdjustmentRecord:
        self._adjustment_sequence += 1
        adjustment = AdjustmentRecord(
            adjustment_id=f"adj-{self._adjustment_sequence:03d}",
            relationship_epoch_id=self.relationship_epoch_id,
            dimension=signal.dimension,
            scope=signal.scope,
            value=signal.value,
            status="candidate",
            created_at=signal.occurred_at,
            updated_at=signal.occurred_at,
            valid_until=_deadline(signal.occurred_at, days=CANDIDATE_TTL_DAYS),
            evidence_ids=[signal.evidence_id],
            qualifying_evidence_ids=([signal.evidence_id] if qualifying else []),
            qualifying_dates=([_local_date(signal.occurred_at)] if qualifying else []),
        )
        self.adjustments.append(adjustment)
        return adjustment

    @staticmethod
    def _append_evidence(
        adjustment: AdjustmentRecord,
        evidence_id: str,
        *,
        qualifying: bool,
    ) -> None:
        if evidence_id not in adjustment.evidence_ids:
            adjustment.evidence_ids.append(evidence_id)
        if qualifying and evidence_id in adjustment.qualifying_evidence_ids:
            raise AssertionError("qualifying Evidence was appended twice")

    def _invalidate_evidence_dependents(
        self,
        evidence_id: str,
        *,
        reason_code: str,
        now: str,
    ) -> None:
        decisive_match = False
        for adjustment in tuple(self.adjustments):
            if adjustment.status not in LIVE_ADJUSTMENT_STATUSES:
                continue
            is_decisive = evidence_id in adjustment.qualifying_evidence_ids
            is_only_seed = (
                evidence_id in adjustment.evidence_ids
                and not adjustment.qualifying_evidence_ids
            )
            if is_decisive or is_only_seed:
                decisive_match = True
                self._terminate(
                    adjustment,
                    status="revoked",
                    reason_code=reason_code,
                    now=now,
                )
                self._rebuild_from_remaining_evidence(adjustment, now=now)
            elif evidence_id in adjustment.evidence_ids:
                self._record_trace(
                    "retain_adjustment",
                    "nondecisive_evidence_removed",
                    adjustment.adjustment_id,
                )
        if not decisive_match:
            self._record_trace(
                "invalidate_evidence",
                reason_code,
                evidence_id,
            )

    def _rebuild_from_remaining_evidence(
        self,
        previous: AdjustmentRecord,
        *,
        now: str,
    ) -> AdjustmentRecord | None:
        remaining_ids = [
            evidence_id
            for evidence_id in previous.evidence_ids
            if self.evidence_states.get(evidence_id) == "active"
            and self.evidence_decisions[evidence_id].route != "rejected"
            and self.evidence[evidence_id].relationship_epoch_id
            == self.relationship_epoch_id
        ]
        if not remaining_ids:
            self._record_trace(
                "skip_rebuild",
                "no_remaining_eligible_evidence",
                previous.adjustment_id,
            )
            return None

        remaining_ids.sort(
            key=lambda evidence_id: (
                _aware_datetime(self.evidence[evidence_id].occurred_at),
                evidence_id,
            )
        )
        qualifying_by_date: dict[str, str] = {}
        for evidence_id in remaining_ids:
            if self.evidence_decisions[evidence_id].route != "qualifying":
                continue
            local_date = _local_date(self.evidence[evidence_id].occurred_at)
            qualifying_by_date.setdefault(local_date, evidence_id)
        qualifying_dates = sorted(qualifying_by_date)
        qualifying_evidence_ids = [
            qualifying_by_date[local_date] for local_date in qualifying_dates
        ]

        terminal_reason: str | None = None
        if len(qualifying_dates) >= 3:
            status: AdjustmentStatus = "active"
            valid_until = None
        elif len(qualifying_dates) == 2:
            status = "trial"
            latest = self.evidence[qualifying_evidence_ids[-1]].occurred_at
            valid_until = _deadline(latest, days=TRIAL_TTL_DAYS)
        elif len(qualifying_dates) == 1:
            status = "candidate"
            latest = self.evidence[qualifying_evidence_ids[-1]].occurred_at
            valid_until = _deadline(latest, days=CANDIDATE_TTL_DAYS)
        else:
            status = "candidate"
            first = self.evidence[remaining_ids[0]].occurred_at
            valid_until = _deadline(first, days=CANDIDATE_TTL_DAYS)

        if (
            valid_until is not None
            and _aware_datetime(valid_until) <= _aware_datetime(now)
        ):
            status = "expired"
            valid_until = None
            terminal_reason = "recomputed_window_already_elapsed"

        self._adjustment_sequence += 1
        rebuilt = AdjustmentRecord(
            adjustment_id=f"adj-{self._adjustment_sequence:03d}",
            relationship_epoch_id=self.relationship_epoch_id,
            dimension=previous.dimension,
            scope=previous.scope,
            value=previous.value,
            status=status,
            created_at=now,
            updated_at=now,
            valid_until=valid_until,
            evidence_ids=remaining_ids,
            qualifying_evidence_ids=qualifying_evidence_ids,
            qualifying_dates=qualifying_dates,
            terminal_reason=terminal_reason,
        )
        self.adjustments.append(rebuilt)
        self._record_trace(
            "rebuild_adjustment",
            (
                "remaining_evidence_window_elapsed"
                if status == "expired"
                else "remaining_evidence_recomputed"
            ),
            f"{previous.adjustment_id}->{rebuilt.adjustment_id}:{status}",
        )
        return rebuilt

    def _terminate(
        self,
        adjustment: AdjustmentRecord,
        *,
        status: Literal["superseded", "expired", "revoked"],
        reason_code: str,
        now: str,
    ) -> None:
        if adjustment.status in TERMINAL_ADJUSTMENT_STATUSES:
            return
        adjustment.status = status
        adjustment.updated_at = now
        adjustment.valid_until = None
        adjustment.terminal_reason = reason_code
        self._record_trace(
            f"mark_{status}",
            reason_code,
            adjustment.adjustment_id,
        )

    def _live_adjustments(
        self,
        key: tuple[str, str],
    ) -> list[AdjustmentRecord]:
        return [
            item
            for item in self.adjustments
            if item.relationship_epoch_id == self.relationship_epoch_id
            and item.key == key
            and item.status in LIVE_ADJUSTMENT_STATUSES
        ]

    def _latest_live(
        self,
        key: tuple[str, str],
        *,
        value: str,
    ) -> AdjustmentRecord | None:
        for item in reversed(self._live_adjustments(key)):
            if item.value == value:
                return item
        return None

    def _record_trace(self, action: str, reason_code: str, target: str) -> None:
        self._trace_sequence += 1
        self.trace.append(
            TraceEntry(
                sequence=self._trace_sequence,
                action=action,
                reason_code=reason_code,
                target=target,
            )
        )

    @staticmethod
    def _key_label(key: tuple[str, str]) -> str:
        return f"{key[0]}@{key[1]}"
