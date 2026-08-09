from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


AcademicStage = Literal["freshman", "sophomore", "junior", "senior", "unknown"]
AcademicStatus = Literal["active", "leave", "graduated", "unknown"]
AcademicTransitionKind = Literal[
    "initialized",
    "advance",
    "skip_advance",
    "same_stage",
    "regression",
    "correction",
    "leave",
    "resume",
    "graduation",
    "major_change",
    "explicit_clear",
    "migration",
]

ACADEMIC_STAGES = frozenset(
    {"freshman", "sophomore", "junior", "senior", "unknown"}
)
ACADEMIC_STATUSES = frozenset({"active", "leave", "graduated", "unknown"})
ACADEMIC_TRANSITION_KINDS = frozenset(
    {
        "initialized",
        "advance",
        "skip_advance",
        "same_stage",
        "regression",
        "correction",
        "leave",
        "resume",
        "graduation",
        "major_change",
        "explicit_clear",
        "migration",
    }
)
_STAGE_ORDER = {"freshman": 1, "sophomore": 2, "junior": 3, "senior": 4}


@dataclass(frozen=True)
class AcademicState:
    stage: AcademicStage
    status: AcademicStatus
    effective_at: str
    source_revision: int

    def __post_init__(self) -> None:
        if self.stage not in ACADEMIC_STAGES:
            raise ValueError("academic stage is invalid")
        if self.status not in ACADEMIC_STATUSES:
            raise ValueError("academic status is invalid")
        _require_aware_datetime("effective_at", self.effective_at)
        if isinstance(self.source_revision, bool) or self.source_revision < 0:
            raise ValueError("source_revision must be a non-negative integer")


@dataclass(frozen=True)
class AcademicTransition:
    previous: AcademicState | None
    current: AcademicState
    kind: AcademicTransitionKind
    growth_eligible: bool


def resolve_academic_transition(
    *,
    previous: AcademicState | None,
    stage: AcademicStage,
    status: AcademicStatus,
    effective_at: str,
    source_revision: int,
    requested_kind: AcademicTransitionKind | None = None,
    clear_stage: bool = False,
) -> AcademicTransition:
    """Resolve one authoritative profile revision without inventing missing stages."""
    if stage not in ACADEMIC_STAGES:
        raise ValueError("academic stage is invalid")
    if status not in ACADEMIC_STATUSES:
        raise ValueError("academic status is invalid")
    if requested_kind is not None and requested_kind not in ACADEMIC_TRANSITION_KINDS:
        raise ValueError("academic transition kind is invalid")
    if clear_stage and stage != "unknown":
        raise ValueError("clear_stage requires an unknown target stage")

    effective_stage: AcademicStage = stage
    if previous is not None and stage == "unknown" and not clear_stage:
        effective_stage = previous.stage
    if (
        previous is not None
        and status in {"leave", "graduated"}
        and effective_stage == "unknown"
    ):
        effective_stage = previous.stage

    current = AcademicState(
        stage=effective_stage,
        status=status,
        effective_at=effective_at,
        source_revision=source_revision,
    )
    inferred_kind = _infer_transition_kind(
        previous=previous,
        current=current,
        clear_stage=clear_stage,
    )
    kind = requested_kind or inferred_kind
    if (
        requested_kind is not None
        and requested_kind not in {"correction", "migration", "major_change"}
        and requested_kind != inferred_kind
    ):
        raise ValueError("academic transition kind conflicts with state change")
    if (
        previous is not None
        and previous.status == "graduated"
        and current.status != "graduated"
        and kind not in {"correction", "migration"}
    ):
        raise ValueError("graduated academic state requires correction or migration")
    _validate_requested_kind(previous=previous, current=current, kind=kind)
    return AcademicTransition(
        previous=previous,
        current=current,
        kind=kind,
        growth_eligible=(
            kind in {"advance", "skip_advance", "resume"}
            and previous is not None
            and previous.stage in _STAGE_ORDER
            and current.stage in _STAGE_ORDER
            and _STAGE_ORDER[current.stage] > _STAGE_ORDER[previous.stage]
            and current.status == "active"
        ),
    )


def require_academic_migration_selection(
    *,
    candidate_pet_ids: tuple[str, ...],
    selected_pet_id: str | None,
    same_person_verified: bool,
    has_academic_conflict: bool,
) -> str:
    """Fail closed before an account workflow is allowed to transfer one pet."""
    candidates = tuple(dict.fromkeys(candidate_pet_ids))
    if not same_person_verified:
        raise PermissionError("account migration requires same-person verification")
    if not isinstance(selected_pet_id, str) or not selected_pet_id.strip():
        raise ValueError("account migration requires an explicit pet selection")
    if selected_pet_id not in candidates:
        raise ValueError("selected pet is not owned by either migration account")
    if has_academic_conflict:
        raise ValueError("academic profile conflict must be resolved before migration")
    return selected_pet_id


def _infer_transition_kind(
    *,
    previous: AcademicState | None,
    current: AcademicState,
    clear_stage: bool,
) -> AcademicTransitionKind:
    if previous is None:
        return "explicit_clear" if clear_stage else "initialized"
    if clear_stage:
        return "explicit_clear"
    if current.status == "graduated" and previous.status != "graduated":
        return "graduation"
    if current.status == "leave" and previous.status != "leave":
        return "leave"
    if previous.status == "leave" and current.status == "active":
        return "resume"
    if current.stage == previous.stage:
        return "same_stage"
    if current.stage == "unknown" or previous.stage == "unknown":
        return "correction"
    delta = _STAGE_ORDER[current.stage] - _STAGE_ORDER[previous.stage]
    if delta == 1:
        return "advance"
    if delta > 1:
        return "skip_advance"
    return "regression"


def _validate_requested_kind(
    *,
    previous: AcademicState | None,
    current: AcademicState,
    kind: AcademicTransitionKind,
) -> None:
    if previous is None and kind not in {"initialized", "correction", "migration"}:
        raise ValueError("initial academic state has an incompatible transition kind")
    if kind in {"correction", "migration"}:
        return
    if kind == "major_change" and (
        previous is None
        or current.stage != previous.stage
        or current.status != previous.status
    ):
        raise ValueError("major_change cannot change academic stage or status")
    if kind == "leave" and current.status != "leave":
        raise ValueError("leave transition requires leave status")
    if kind == "resume" and (
        previous is None or previous.status != "leave" or current.status != "active"
    ):
        raise ValueError("resume transition requires leave to active")
    if kind == "graduation" and current.status != "graduated":
        raise ValueError("graduation transition requires graduated status")
    if kind == "explicit_clear" and current.stage != "unknown":
        raise ValueError("explicit_clear requires unknown academic stage")
    if previous is None:
        return
    if kind == "same_stage" and current.stage != previous.stage:
        raise ValueError("same_stage cannot change academic stage")
    if kind in {"advance", "skip_advance", "regression"}:
        if previous.stage not in _STAGE_ORDER or current.stage not in _STAGE_ORDER:
            raise ValueError(f"{kind} requires two known academic stages")
        delta = _STAGE_ORDER[current.stage] - _STAGE_ORDER[previous.stage]
        expected = (
            "advance" if delta == 1 else "skip_advance" if delta > 1 else "regression"
        )
        if kind != expected:
            raise ValueError("academic transition kind conflicts with stage direction")


def _require_aware_datetime(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be an ISO-8601 datetime with timezone")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 datetime with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be an ISO-8601 datetime with timezone")
