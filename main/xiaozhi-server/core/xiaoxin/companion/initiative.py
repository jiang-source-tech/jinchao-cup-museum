from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Mapping, Protocol

from .contracts import CompanionWorkResult
from .store import (
    CompanionJobLeaseLostError,
    CompanionStore,
    DueInitiativeOpportunity,
)


@dataclass(frozen=True)
class InitiativeDeliveryEligibility:
    eligible: bool
    reason_code: str
    hardware_expression: Mapping[str, object] = field(default_factory=dict)
    retry_at: str | None = None

    def __post_init__(self) -> None:
        if self.eligible and self.retry_at is not None:
            raise ValueError("eligible initiative cannot define retry_at")
        if self.retry_at is not None:
            datetime.fromisoformat(self.retry_at)


@dataclass(frozen=True)
class InitiativeDeliveryRequest:
    opportunity_id: str
    decision_id: str
    owner_user_id: str
    pet_id: str
    memory_subject_id: str
    opportunity_kind: str
    reason_code: str
    content: str
    hardware_expression: Mapping[str, object]
    attempted_at: str


@dataclass(frozen=True)
class InitiativeDeliveryResult:
    status: str
    delivery_id: str | None = None
    failure_reason: str | None = None
    retry_at: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"delivered", "deferred", "delivery_failed"}:
            raise ValueError("initiative delivery status is invalid")
        if self.status == "delivered" and not self.delivery_id:
            raise ValueError("delivered initiative requires delivery_id")
        if self.status == "deferred":
            if self.retry_at is None:
                raise ValueError("deferred initiative requires retry_at")
            datetime.fromisoformat(self.retry_at)
        elif self.retry_at is not None:
            raise ValueError("only deferred initiative can define retry_at")


class InitiativeComposer(Protocol):
    async def compose(self, opportunity: DueInitiativeOpportunity) -> str: ...


class InitiativeDeliveryPort(Protocol):
    async def check_eligibility(
        self,
        opportunity: DueInitiativeOpportunity,
        *,
        now: str,
    ) -> InitiativeDeliveryEligibility: ...

    async def deliver(
        self,
        request: InitiativeDeliveryRequest,
    ) -> InitiativeDeliveryResult: ...


class SafeInitiativeComposer:
    """Deterministic first-stage composer that consumes only a safe brief."""

    async def compose(self, opportunity: DueInitiativeOpportunity) -> str:
        prefix = {
            "followup": "还记得这件事吗：",
            "reminder_result": "刚才的提醒还顺利吗：",
            "goal_progress": "想轻轻问问这件事的进展：",
            "future_event": "提前和你确认一下：",
            "celebration": "想和你一起庆祝：",
            "checkin": "来做一个你设置的小小 check-in：",
            "connection_bid": "只是想来和你说句话：",
            "boot_checkin": "我刚刚开机啦，想先和你打个招呼：",
        }[opportunity.opportunity_kind]
        return f"{prefix}{opportunity.safe_brief}"


class InitiativeScheduler:
    def __init__(
        self,
        *,
        store: CompanionStore,
        composer: InitiativeComposer,
        delivery_port: InitiativeDeliveryPort,
        capability_allowed: Callable[[str, str], bool] | None = None,
        connection_feedback_window_seconds: int = 1800,
        boot_checkin_delivery_window_seconds: int = 600,
    ) -> None:
        self._store = store
        self._composer = composer
        self._delivery_port = delivery_port
        self._capability_allowed = capability_allowed
        self._connection_feedback_window_seconds = max(
            int(connection_feedback_window_seconds), 1
        )
        self._boot_checkin_delivery_window_seconds = max(
            int(boot_checkin_delivery_window_seconds), 1
        )

    def _initiative_allowed(self, owner_user_id: str) -> bool:
        if self._capability_allowed is None:
            return True
        try:
            return bool(
                self._capability_allowed(
                    owner_user_id,
                    "COMPANION_INITIATIVE",
                )
            )
        except Exception:
            return False

    async def run_due_work(self, *, now: str, limit: int) -> CompanionWorkResult:
        self._store.expire_connection_feedback(
            now=now,
            feedback_window_seconds=self._connection_feedback_window_seconds,
            limit=limit,
        )
        self._store.expire_boot_checkins(
            now=now,
            feedback_window_seconds=self._connection_feedback_window_seconds,
            limit=limit,
        )
        self._store.expire_stale_boot_checkins(
            now=now,
            delivery_window_seconds=self._boot_checkin_delivery_window_seconds,
            limit=limit,
        )
        self._store.materialize_due_connection_bids(now=now, limit=limit)
        opportunities = self._store.list_due_initiative_opportunities(
            now=now,
            limit=limit,
        )
        succeeded = 0
        retried = 0
        failed = 0
        for opportunity in opportunities:
            if not self._initiative_allowed(opportunity.owner_user_id):
                self._store.block_initiative_opportunity(
                    opportunity_id=opportunity.opportunity_id,
                    reason_code="compliance_initiative_disabled",
                    now=now,
                )
                succeeded += 1
                continue
            internal_reason = self._store.validate_initiative_opportunity(
                opportunity_id=opportunity.opportunity_id,
                now=now,
                boot_checkin_delivery_window_seconds=(
                    self._boot_checkin_delivery_window_seconds
                ),
            )
            if internal_reason != "eligible":
                self._store.block_initiative_opportunity(
                    opportunity_id=opportunity.opportunity_id,
                    reason_code=internal_reason,
                    now=now,
                )
                succeeded += 1
                continue
            eligibility = await self._delivery_port.check_eligibility(
                opportunity,
                now=now,
            )
            if not eligibility.eligible:
                if eligibility.retry_at is not None:
                    self._store.defer_initiative_opportunity(
                        opportunity_id=opportunity.opportunity_id,
                        reason_code=eligibility.reason_code,
                        retry_at=eligibility.retry_at,
                        now=now,
                    )
                    retried += 1
                else:
                    self._store.block_initiative_opportunity(
                        opportunity_id=opportunity.opportunity_id,
                        reason_code=eligibility.reason_code,
                        now=now,
                    )
                    succeeded += 1
                continue
            claimed = self._store.claim_initiative_opportunity(
                opportunity_id=opportunity.opportunity_id,
                hardware_expression=eligibility.hardware_expression,
                now=now,
                boot_checkin_delivery_window_seconds=(
                    self._boot_checkin_delivery_window_seconds
                ),
            )
            if claimed is None:
                continue
            try:
                content = await self._composer.compose(claimed)
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("initiative composer returned empty content")
            except Exception as exc:
                self._store.retry_initiative_composition(
                    opportunity=claimed,
                    error_code=type(exc).__name__,
                    now=now,
                )
                retried += 1
                continue
            try:
                request = self._store.begin_initiative_delivery(
                    opportunity=claimed,
                    content=content.strip(),
                    now=now,
                )
            except CompanionJobLeaseLostError:
                succeeded += 1
                continue
            try:
                result = await self._delivery_port.deliver(request)
            except Exception as exc:
                result = InitiativeDeliveryResult(
                    status="delivery_failed",
                    failure_reason=type(exc).__name__,
                )
            if result.status == "deferred":
                self._store.defer_initiative_opportunity(
                    opportunity_id=claimed.opportunity_id,
                    reason_code=result.failure_reason or "delivery_deferred",
                    retry_at=result.retry_at,
                    now=now,
                )
                retried += 1
                continue
            should_retry_delivery = self._store.should_retry_initiative_delivery(
                opportunity=claimed,
                status=result.status,
            )
            if (
                result.status == "delivery_failed"
                and not should_retry_delivery
                and claimed.opportunity_kind != "boot_checkin"
            ):
                self._store.record_initiative_feedback(
                    owner_user_id=claimed.owner_user_id,
                    pet_id=claimed.pet_id,
                    memory_subject_id=claimed.memory_subject_id,
                    decision_id=request.decision_id,
                    outcome="delivery_failed",
                    now=now,
                    idempotency_key=(
                        "initiative-delivery-failed:"
                        f"{request.decision_id}:{result.delivery_id or 'submit'}"
                    ),
                )
            delivery_retried = self._store.finish_initiative_delivery(
                opportunity=claimed,
                result=result,
                now=now,
            )
            if delivery_retried:
                retried += 1
                continue
            if result.status == "delivered":
                succeeded += 1
            else:
                failed += 1
        return CompanionWorkResult(
            claimed=len(opportunities),
            succeeded=succeeded,
            retried=retried,
            failed=failed,
        )
