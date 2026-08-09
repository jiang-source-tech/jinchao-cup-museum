from __future__ import annotations

from datetime import datetime, time, timedelta

from core.xiaoxin.companion.initiative import (
    InitiativeDeliveryEligibility,
    InitiativeDeliveryRequest,
    InitiativeDeliveryResult,
)
from core.xiaoxin.companion.store import DueInitiativeOpportunity
from core.xiaoxin.control_types import XiaoxinDeliveryState
from core.xiaoxin.identity.models import DEVICE_BOUND


class XiaoxinInitiativeDeliveryPort:
    def __init__(
        self,
        *,
        identity_store,
        registry,
        delivery_store,
        companion_store=None,
        dispatcher,
        delivery_enabled: bool,
        quiet_hours_start: str = "22:30",
        quiet_hours_end: str = "07:30",
    ) -> None:
        self._identity_store = identity_store
        self._registry = registry
        self._delivery_store = delivery_store
        self._companion_store = companion_store
        self._dispatcher = dispatcher
        self._delivery_enabled = bool(delivery_enabled)
        self._quiet_hours_start = _parse_local_time(quiet_hours_start)
        self._quiet_hours_end = _parse_local_time(quiet_hours_end)

    def _quiet_hours_window(
        self,
        opportunity: DueInitiativeOpportunity | InitiativeDeliveryRequest,
    ) -> tuple[time, time] | None:
        setting = None
        if self._companion_store is not None:
            setting = self._companion_store.load_initiative_quiet_hours(
                owner_user_id=opportunity.owner_user_id,
                pet_id=opportunity.pet_id,
                memory_subject_id=opportunity.memory_subject_id,
            )
        if setting is None:
            return self._quiet_hours_start, self._quiet_hours_end
        if setting["enabled"] is not True:
            return None
        return (
            _parse_local_time(str(setting["start"])),
            _parse_local_time(str(setting["end"])),
        )

    async def check_eligibility(
        self,
        opportunity: DueInitiativeOpportunity,
        *,
        now: str,
    ) -> InitiativeDeliveryEligibility:
        if not self._delivery_enabled:
            return InitiativeDeliveryEligibility(False, "dry_run")
        device_id = self._resolve_owned_device(
            memory_subject_id=opportunity.memory_subject_id,
            owner_user_id=opportunity.owner_user_id,
        )
        if device_id is None:
            return InitiativeDeliveryEligibility(
                False,
                "device_unavailable",
                retry_at=(datetime.fromisoformat(now) + timedelta(minutes=5)).isoformat(),
            )
        if (
            opportunity.opportunity_kind == "connection_bid"
            and self._companion_store is not None
            and not self._companion_store.has_active_presence_lease(
                owner_user_id=opportunity.owner_user_id,
                pet_id=opportunity.pet_id,
                memory_subject_id=opportunity.memory_subject_id,
                relationship_epoch_id=opportunity.relationship_epoch_id,
                now=now,
            )
        ):
            return InitiativeDeliveryEligibility(False, "owner_presence_unknown")
        quiet_hours = self._quiet_hours_window(opportunity)
        if quiet_hours is not None and _is_quiet_time(
            datetime.fromisoformat(now).timetz().replace(tzinfo=None),
            start=quiet_hours[0],
            end=quiet_hours[1],
        ):
            return InitiativeDeliveryEligibility(
                False,
                "quiet_hours",
                retry_at=_next_quiet_hours_end(
                    datetime.fromisoformat(now),
                    start=quiet_hours[0],
                    end=quiet_hours[1],
                ).isoformat(),
            )
        if self._has_higher_priority_pending(device_id):
            return InitiativeDeliveryEligibility(
                False,
                "higher_priority_notification",
                retry_at=(datetime.fromisoformat(now) + timedelta(minutes=2)).isoformat(),
            )
        mode = {
            "celebration": "celebration",
            "checkin": "quiet_checkin",
            "reminder_result": "supportive",
            "goal_progress": "supportive",
            "future_event": "warm",
            "followup": "warm",
            "connection_bid": "quiet_checkin",
            "boot_checkin": "quiet_checkin",
        }[opportunity.opportunity_kind]
        return InitiativeDeliveryEligibility(
            True,
            "eligible",
            {"mode": mode, "intensity": "low"},
        )

    async def deliver(
        self,
        request: InitiativeDeliveryRequest,
    ) -> InitiativeDeliveryResult:
        device_id = self._resolve_owned_device(
            memory_subject_id=request.memory_subject_id,
            owner_user_id=request.owner_user_id,
        )
        attempted_at = datetime.fromisoformat(request.attempted_at)
        if device_id is None:
            return InitiativeDeliveryResult(
                status="deferred",
                failure_reason="device_unavailable",
                retry_at=(attempted_at + timedelta(minutes=5)).isoformat(),
            )
        quiet_hours = self._quiet_hours_window(request)
        if quiet_hours is not None and _is_quiet_time(
            attempted_at.timetz().replace(tzinfo=None),
            start=quiet_hours[0],
            end=quiet_hours[1],
        ):
            return InitiativeDeliveryResult(
                status="deferred",
                failure_reason="quiet_hours",
                retry_at=_next_quiet_hours_end(
                    attempted_at,
                    start=quiet_hours[0],
                    end=quiet_hours[1],
                ).isoformat(),
            )
        if self._has_higher_priority_pending(device_id):
            return InitiativeDeliveryResult(
                status="deferred",
                failure_reason="higher_priority_notification",
                retry_at=(attempted_at + timedelta(minutes=2)).isoformat(),
            )
        record = await self._dispatcher.submit_companion_initiative(
            device_id,
            {
                "eligible": True,
                "decision_id": request.decision_id,
                "content_brief": request.content,
                "hardware_expression": dict(request.hardware_expression),
            },
        )
        await self._dispatcher.wait_for_delivery_task(record.delivery_id)
        final = self._delivery_store.get(record.delivery_id)
        if final is not None and final.state == XiaoxinDeliveryState.DONE:
            return InitiativeDeliveryResult(
                status="delivered",
                delivery_id=record.delivery_id,
            )
        return InitiativeDeliveryResult(
            status="delivery_failed",
            delivery_id=record.delivery_id,
            failure_reason=(
                final.failure_reason.value
                if final is not None and final.failure_reason is not None
                else "delivery_incomplete"
            ),
        )

    def _resolve_owned_device(
        self,
        *,
        memory_subject_id: str,
        owner_user_id: str,
    ) -> str | None:
        subject = self._identity_store.get_memory_subject(memory_subject_id)
        if (
            subject is None
            or subject.owner_user_id != owner_user_id
            or not subject.device_id
            or getattr(subject, "merged_into_subject_id", None) is not None
        ):
            return None
        device = self._identity_store.get_device_by_device_id(subject.device_id)
        if (
            device is None
            or device.owner_user_id != owner_user_id
            or device.bind_status != DEVICE_BOUND
        ):
            return None
        return str(device.device_id)

    def _has_higher_priority_pending(self, device_id: str) -> bool:
        return any(
            record.device_id == device_id
            and record.request.priority > 1
            and record.state
            not in {XiaoxinDeliveryState.DONE, XiaoxinDeliveryState.FAILED}
            for record in self._delivery_store.list_recent()
        )


def _parse_local_time(value: str) -> time:
    try:
        parsed = time.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError("initiative quiet hour must be HH:MM") from exc
    return parsed.replace(tzinfo=None)


def _is_quiet_time(value: time, *, start: time, end: time) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= value < end
    return value >= start or value < end


def _next_quiet_hours_end(value: datetime, *, start: time, end: time) -> datetime:
    retry_at = value.replace(
        hour=end.hour,
        minute=end.minute,
        second=end.second,
        microsecond=0,
    )
    if start > end and value.timetz().replace(tzinfo=None) >= start:
        retry_at += timedelta(days=1)
    return retry_at
