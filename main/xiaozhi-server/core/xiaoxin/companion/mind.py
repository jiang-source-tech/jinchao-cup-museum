from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
from collections.abc import Callable, Mapping
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from time import perf_counter
from uuid import NAMESPACE_URL, uuid5

from .contracts import (
    CompanionCommitResult,
    CompanionContractError,
    CompanionControlCommand,
    CompanionControlResult,
    CompanionEvidence,
    CompanionObservation,
    CompanionObserveResult,
    CompanionPolicy,
    CompanionProjection,
    CompanionProjectionRequest,
    CompanionSubjectContext,
    CompanionTurnOutcome,
    CompanionTurnRequest,
    CompanionUnavailableError,
    CompanionVAEvent,
    CompanionVAEventResult,
    CompanionWorkResult,
    PreparedCompanionTurn,
    TurnBehaviorPlan,
)
from .controls import CompanionControls
from .policy import (
    CompanionPolicyInputs,
    RelationshipQualityMetrics,
    build_companion_policy,
    policy_inputs_from_evidence,
    relationship_quality_snapshot,
    relationship_stage_progress,
    relationship_stage_reason_codes,
)
from .reflection import ReflectionModel
from .semantic_memory import (
    MemoryInterpreter,
    MemoryRecallPlanner,
    MemoryRecallPlanningError,
    MemoryRecallRequest,
    memory_fact_key_storage_aliases,
)
from .initiative import (
    InitiativeComposer,
    InitiativeDeliveryPort,
    InitiativeScheduler,
)
from .initiative_timing import connection_bid_delay
from .store import (
    CompanionStore,
    PendingConnectionNeedUpdate,
    PendingCompanionEvidence,
    PendingCompanionJob,
    PendingInitiativeOpportunity,
)
from .temperament import temperament_dimensions_for_pet
from .turn_behavior import TurnBehaviorPlanningInputs, plan_turn_behavior
from .worker import CompanionWorker


LOGGER = logging.getLogger(__name__)
MAX_RETRIEVAL_PROMPT_SUMMARY_CHARS = 240
_MEMORY_EXPANDING_CONTROL_ACTIONS = {
    "confirm_candidate",
    "correct_evidence",
    "sync_academic_stage",
}

_SOFT_MEMORY_USAGE_HINTS = {
    "preference:task_start_strategy": (
        "若本轮涉及开始陌生或复杂任务，优先降低启动难度；可以给简短顺序，"
        "也可以只确定一个最小起点，不必固定条数或复述这条记忆，只有缺少"
        "继续回答所必需的信息时才追问。"
    ),
    "preference:emotional_support_style": (
        "压力、低落或受挫时，以这条近期明确偏好决定回应节奏；若与一般表达习惯"
        "冲突，以此为准。自然回应，不复述规则。"
    ),
}

_RELATIONSHIP_LABELS = {
    "first_meeting": "重新认识中",
    "familiar": "已经熟悉",
    "attuned": "相处默契",
    "long_term_companion": "长期陪伴",
}
_ADJUSTMENT_LABELS = {
    ("response_length", "short"): "回答更精简",
    ("response_length", "standard"): "回答保持适中",
    ("response_length", "expanded"): "需要时多展开一些",
    ("question_frequency", "never"): "不连续追问",
    ("question_frequency", "less"): "少一点追问",
    ("question_frequency", "often"): "适当多确认你的想法",
    ("initiative_level", "disabled"): "不主动发起话题",
    ("initiative_level", "low"): "减少主动打扰",
    ("initiative_level", "medium"): "适度主动关心",
    ("memory_reference_depth", "never"): "不主动提起过往",
    ("memory_reference_depth", "shallow"): "少量联系过往",
    ("memory_reference_depth", "moderate"): "适度联系共同经历",
    ("memory_reference_depth", "deep"): "需要时深入联系共同经历",
    ("emotional_posture", "neutral"): "表达更克制",
    ("emotional_posture", "warm"): "表达更温和",
    ("emotional_posture", "supportive"): "多一点支持",
    ("emotional_posture", "attuned"): "更贴合当下情绪",
    ("humor_level", "none"): "严肃话题不开玩笑",
    ("humor_level", "low"): "少一点玩笑",
    ("humor_level", "medium"): "适度轻松一些",
    ("closure_style", "concise"): "简洁收尾",
    ("closure_style", "warm"): "温和收尾",
    ("closure_style", "relational"): "照顾相处感受后收尾",
    ("closure_style", "familiar"): "用熟悉的方式收尾",
    ("hardware_expression_intensity", "low"): "设备动作更克制",
    ("hardware_expression_intensity", "neutral"): "设备动作保持平稳",
    ("hardware_expression_intensity", "medium"): "设备动作适度明显",
    ("hardware_expression_intensity", "high"): "设备动作更有活力",
}
_DEFAULT_INITIATIVE_QUIET_HOURS = {
    "enabled": True,
    "start": "22:30",
    "end": "07:30",
}
_SCOPE_LABELS = {
    "all": "所有场景",
    "voice": "语音对话",
    "miniprogram": "小程序对话",
    "hardware": "设备表现",
    "initiative": "主动关心",
    "operator": "管理操作",
    "conversation": "日常对话",
    "general_qa": "一般问答",
    "explicit_recall": "主动回忆",
    "reminder": "提醒场景",
    "device_action": "设备操作",
}


def _safe_miniprogram_payload(
    *,
    policy: CompanionPolicy,
    projection_state: Mapping[str, object],
    growth_moment: Mapping[str, object] | None,
    boundaries: tuple[Mapping[str, object], ...] = (),
) -> Mapping[str, object]:
    contracts_by_dimension = {
        str(item.get("dimension")): item
        for item in projection_state.get("interaction_contracts", ())
        if isinstance(item, Mapping)
    }
    proactive_contract = contracts_by_dimension.get("initiative_level")
    memory_contract = contracts_by_dimension.get("memory_reference_depth")
    quiet_hours_contract = contracts_by_dimension.get("initiative_quiet_hours")
    proactive_value = (
        str(proactive_contract.get("value"))
        if proactive_contract is not None
        else policy.initiative_level
    )
    memory_value = (
        str(memory_contract.get("value"))
        if memory_contract is not None
        else None
    )
    if memory_value == "never":
        past_reference_mode = "never"
    elif memory_value == "shallow":
        past_reference_mode = "occasional"
    elif memory_value in {"moderate", "deep"}:
        past_reference_mode = "natural"
    elif policy.memory_reference_budget == 0:
        past_reference_mode = "never"
    elif policy.memory_reference_budget == 1:
        past_reference_mode = "occasional"
    else:
        past_reference_mode = "natural"
    quiet_hours_value = (
        quiet_hours_contract.get("value")
        if quiet_hours_contract is not None
        else None
    )
    if (
        not isinstance(quiet_hours_value, Mapping)
        or not isinstance(quiet_hours_value.get("enabled"), bool)
        or not isinstance(quiet_hours_value.get("start"), str)
        or not isinstance(quiet_hours_value.get("end"), str)
    ):
        quiet_hours_value = _DEFAULT_INITIATIVE_QUIET_HOURS
    learned_behaviors = tuple(
        {
            "adjustment_id": item["adjustment_id"],
            "label": _ADJUSTMENT_LABELS.get(
                (str(item.get("dimension")), str(item.get("value"))),
                "调整当前表达方式",
            ),
            "scope": _SCOPE_LABELS.get(str(item.get("scope")), "特定场景"),
            "source": "相处中学会",
        }
        for item in projection_state.get("active_adjustments", ())
        if isinstance(item, Mapping)
    )
    explicit_contracts = tuple(
        {
            "contract_id": item["contract_id"],
            "label": item["safe_label"],
            "scope": item["safe_scope"],
            "source": "你明确设置",
        }
        for item in projection_state.get("interaction_contracts", ())
        if isinstance(item, Mapping)
    )
    explicit_boundaries = tuple(
        {
            "setting_id": item["evidence_id"],
            "label": item["source_summary"],
            "scope": "所有场景",
            "source": "你明确设置",
        }
        for item in boundaries
    )
    explicit_settings = explicit_contracts + explicit_boundaries
    has_personalized_style = bool(learned_behaviors or explicit_settings)
    payload: dict[str, object] = {
        "companion_summary": {
            "headline": (
                "会按你们已经确认的方式回应"
                if has_personalized_style
                else "按原来的方式陪你"
            ),
            "relationship": _RELATIONSHIP_LABELS.get(
                policy.relationship_stage,
                "正在相处",
            ),
        },
        "learned_behaviors": learned_behaviors,
        "explicit_settings": explicit_settings,
        "growth_moments_enabled": bool(
            projection_state.get("growth_moments_enabled", True)
        ),
        "companion_preferences": {
            "proactive_companionship": {
                "enabled": proactive_value != "disabled",
                "pace": (
                    "quiet" if proactive_value == "low" else "natural"
                ),
                "setting_id": (
                    proactive_contract.get("contract_id")
                    if proactive_contract is not None
                    else None
                ),
            },
            "past_reference": {
                "mode": past_reference_mode,
                "setting_id": (
                    memory_contract.get("contract_id")
                    if memory_contract is not None
                    else None
                ),
            },
            "quiet_hours": {
                **quiet_hours_value,
                "setting_id": (
                    quiet_hours_contract.get("contract_id")
                    if quiet_hours_contract is not None
                    else None
                ),
            },
        },
        "available_controls": (
            "revoke_adjustment",
            "revoke_boundary",
            "set_interaction_contract",
            "set_initiative_quiet_hours",
            "revoke_interaction_contract",
            "restore_default_expression",
            "set_growth_moments_enabled",
            "reset_relationship",
            "purge_personal_memory",
        ),
    }
    if growth_moment is not None:
        payload["growth_moment"] = growth_moment
    return payload


def _retrieval_prompt_summary(
    evidence,
    *,
    include_semantic_label: bool = False,
) -> str:
    summary = ""
    if evidence.source_kind == "conversation_candidate":
        for key in ("canonical_value", "value", "preference", "event", "goal"):
            canonical_value = evidence.content.get(key)
            if isinstance(canonical_value, str) and canonical_value.strip():
                summary = canonical_value[:MAX_RETRIEVAL_PROMPT_SUMMARY_CHARS]
                break
    if not summary:
        summary = evidence.source_summary[:MAX_RETRIEVAL_PROMPT_SUMMARY_CHARS]
    usage_hint = _SOFT_MEMORY_USAGE_HINTS.get(evidence.fact_key or "")
    if usage_hint and not include_semantic_label:
        projection = {
            "fact": summary,
            "fact_key": evidence.fact_key,
            "usage_hint": usage_hint,
        }
        rendered = json.dumps(
            projection,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        while len(rendered) > MAX_RETRIEVAL_PROMPT_SUMMARY_CHARS and projection[
            "fact"
        ]:
            overflow = len(rendered) - MAX_RETRIEVAL_PROMPT_SUMMARY_CHARS
            projection["fact"] = projection["fact"][: -max(1, overflow)]
            rendered = json.dumps(
                projection,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        return rendered
    if not include_semantic_label:
        return summary
    return json.dumps(
        {
            "fact": summary,
            "fact_key": evidence.fact_key or "",
            "kind": evidence.kind,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _apply_va_projection(
    policy: CompanionPolicy,
    projection: Mapping[str, object],
    *,
    surface: str,
    device_state: str = "normal",
) -> CompanionPolicy:
    if surface == "initiative":
        return policy
    posture = str(projection.get("emotional_posture", "warm_neutral"))
    current_safety_gate = bool(
        {
            "low_mood_support",
            "negative_feedback_initiative_stop",
            "reunion_cautious_cap",
            "repairing_cap",
        }
        & set(policy.reason_codes)
    )
    updates: dict[str, object] = {}
    prohibited = list(policy.prohibited_behaviors)
    expression_style = policy.expression_style
    hardware_expression = dict(policy.hardware_expression)
    va_hardware = projection.get("hardware_expression")
    if isinstance(va_hardware, Mapping):
        kind = va_hardware.get("kind")
        if kind in {
            "bright_pulse",
            "quiet_warm",
            "attentive_still",
        }:
            hardware_expression["kind"] = kind
        intensity = va_hardware.get("intensity")
        if intensity in {"low", "medium"}:
            hardware_expression["intensity"] = intensity

    if not current_safety_gate and posture == "supportive_settled":
        updates.update(emotional_posture="supportive", question_budget=0)
        expression_style = replace(expression_style, humor_level="none")
        prohibited.extend(("humor", "celebration"))
    elif not current_safety_gate and posture == "receptive_brief":
        updates.update(
            emotional_posture="neutral",
            response_length="short",
            question_budget=0,
            closure_style="concise",
        )
        expression_style = replace(expression_style, humor_level="none")
        prohibited.extend(("self_pity", "comfort_seeking"))
    elif not current_safety_gate and posture in {"bright_warm", "gentle_warm"}:
        if policy.emotional_posture == "neutral":
            updates["emotional_posture"] = "warm"

    if device_state == "low_battery":
        hardware_expression["kind"] = "low_power"
        hardware_expression["intensity"] = "low"
        hardware_expression["cadence"] = "restrained_single"
    updates["expression_style"] = expression_style
    updates["prohibited_behaviors"] = tuple(dict.fromkeys(prohibited))
    updates["hardware_expression"] = hardware_expression
    return replace(policy, **updates)


def _apply_current_turn_corrections(
    policy: CompanionPolicy,
    corrections: tuple[str, ...],
) -> CompanionPolicy:
    updates: dict[str, object] = {}
    if "no_follow_up" in corrections:
        updates["question_budget"] = 0
    if "concise" in corrections:
        updates.update(response_length="short", closure_style="concise")
    if "no_memory_reference" in corrections:
        updates["memory_reference_budget"] = 0
    if "no_humor" in corrections:
        updates["expression_style"] = replace(
            policy.expression_style,
            humor_level="none",
        )
    if "settle_hardware" in corrections:
        updates["hardware_expression"] = {
            **policy.hardware_expression,
            "intensity": "low",
            "cadence": "restrained_single",
        }
    return replace(policy, **updates) if updates else policy


class CompanionMind:
    """The only external seam for companion memory and behaviour."""

    @property
    def semantic_memory_mode(self) -> str:
        return self._memory_interpreter_mode

    @property
    def uses_semantic_user_facts(self) -> bool:
        return self._memory_interpreter_mode in {
            "shadow",
            "candidate",
            "active_explicit",
        }

    def __init__(
        self,
        *,
        store: CompanionStore | None = None,
        token_secret: bytes | None = None,
        reflection_model: ReflectionModel | None = None,
        memory_interpreter: MemoryInterpreter | None = None,
        memory_interpreter_mode: str = "off",
        memory_active_explicit_release_enabled: bool = False,
        turn_behavior_plan_mode: str = "off",
        initiative_composer: InitiativeComposer | None = None,
        initiative_delivery_port: InitiativeDeliveryPort | None = None,
        capability_allowed: Callable[[str, str], bool] | None = None,
        initiative_followup_delay_minutes: float = 240,
        connection_bid_delays_minutes: Mapping[str, float] | None = None,
        connection_feedback_window_minutes: float = 30,
        boot_checkin_delivery_window_seconds: float = 600,
        presence_window_minutes: float = 45,
    ) -> None:
        self._store = store
        self._controls = CompanionControls(store) if store is not None else None
        self._capability_allowed = capability_allowed
        self._token_secret = token_secret or secrets.token_bytes(32)
        if memory_interpreter_mode not in {
            "off",
            "shadow",
            "candidate",
            "active_explicit",
        }:
            raise ValueError("memory_interpreter_mode is invalid")
        if not isinstance(memory_active_explicit_release_enabled, bool):
            raise ValueError("memory_active_explicit_release_enabled must be boolean")
        if turn_behavior_plan_mode not in {"off", "shadow", "active"}:
            raise ValueError("turn_behavior_plan_mode is invalid")
        self._turn_behavior_plan_mode = turn_behavior_plan_mode
        if isinstance(initiative_followup_delay_minutes, bool):
            raise ValueError("initiative_followup_delay_minutes must be positive")
        try:
            followup_delay_minutes = float(initiative_followup_delay_minutes)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "initiative_followup_delay_minutes must be positive"
            ) from exc
        if followup_delay_minutes <= 0:
            raise ValueError("initiative_followup_delay_minutes must be positive")
        self._initiative_followup_delay = timedelta(minutes=followup_delay_minutes)
        configured_connection_delays = connection_bid_delays_minutes or {
            "reserved": 4320.0,
            "timely": 2880.0,
            "proactive": 1440.0,
        }
        if set(configured_connection_delays) != {
            "reserved",
            "timely",
            "proactive",
        }:
            raise ValueError("connection bid delays must define all initiative biases")
        self._connection_bid_delays: dict[str, timedelta] = {}
        for bias, raw_minutes in configured_connection_delays.items():
            if isinstance(raw_minutes, bool):
                raise ValueError("connection bid delays must be positive")
            try:
                minutes = float(raw_minutes)
            except (TypeError, ValueError) as exc:
                raise ValueError("connection bid delays must be positive") from exc
            if minutes <= 0:
                raise ValueError("connection bid delays must be positive")
            self._connection_bid_delays[bias] = timedelta(minutes=minutes)
        if isinstance(connection_feedback_window_minutes, bool):
            raise ValueError("connection feedback window must be positive")
        try:
            feedback_window_minutes = float(connection_feedback_window_minutes)
        except (TypeError, ValueError) as exc:
            raise ValueError("connection feedback window must be positive") from exc
        if feedback_window_minutes <= 0:
            raise ValueError("connection feedback window must be positive")
        self._connection_feedback_window = timedelta(
            minutes=feedback_window_minutes
        )
        if isinstance(boot_checkin_delivery_window_seconds, bool):
            raise ValueError("boot checkin delivery window must be positive")
        try:
            boot_delivery_window_seconds = float(
                boot_checkin_delivery_window_seconds
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("boot checkin delivery window must be positive") from exc
        if boot_delivery_window_seconds <= 0:
            raise ValueError("boot checkin delivery window must be positive")
        self._boot_checkin_delivery_window = timedelta(
            seconds=boot_delivery_window_seconds
        )
        if isinstance(presence_window_minutes, bool):
            raise ValueError("presence window must be positive")
        try:
            presence_minutes = float(presence_window_minutes)
        except (TypeError, ValueError) as exc:
            raise ValueError("presence window must be positive") from exc
        if presence_minutes <= 0:
            raise ValueError("presence window must be positive")
        self._presence_window = timedelta(minutes=presence_minutes)
        self._memory_interpreter_mode = (
            "candidate"
            if memory_interpreter_mode == "active_explicit"
            and not memory_active_explicit_release_enabled
            else memory_interpreter_mode
        )
        self._worker = (
            CompanionWorker(
                store=store,
                reflection_model=reflection_model,
                memory_interpreter=memory_interpreter,
                memory_interpreter_mode=self._memory_interpreter_mode,
            )
            if store is not None
            and (reflection_model is not None or memory_interpreter is not None)
            else None
        )
        if (initiative_composer is None) != (initiative_delivery_port is None):
            raise ValueError(
                "initiative composer and delivery port must be configured together"
            )
        self._initiative_scheduler = (
            InitiativeScheduler(
                store=store,
                composer=initiative_composer,
                delivery_port=initiative_delivery_port,
                capability_allowed=capability_allowed,
                connection_feedback_window_seconds=max(
                    int(self._connection_feedback_window.total_seconds()), 1
                ),
                boot_checkin_delivery_window_seconds=max(
                    int(self._boot_checkin_delivery_window.total_seconds()), 1
                ),
            )
            if store is not None
            and initiative_composer is not None
            and initiative_delivery_port is not None
            else None
        )

    def _allows(self, owner_user_id: str, capability: str) -> bool:
        if self._capability_allowed is None:
            return True
        try:
            return bool(self._capability_allowed(owner_user_id, capability))
        except Exception:
            LOGGER.exception(
                "Companion compliance capability check failed",
                extra={
                    "companion_owner_user_id": owner_user_id,
                    "companion_capability": capability,
                },
            )
            return False

    def _prepared_token(
        self,
        *,
        turn_id: str,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str | None,
        academic_stage: str,
        surface: str,
        interaction_kind: str,
        request_digest: str,
        occurred_at: str,
        policy: CompanionPolicy,
        persistence_allowed: bool,
        prompt_context: tuple[str, ...],
        used_evidence_ids: tuple[str, ...],
        source_text: str | None,
        conversation_digest: str | None,
        growth_moment: Mapping[str, object] | None,
        behavior_plan: TurnBehaviorPlan | None,
        behavior_plan_active: bool,
    ) -> str:
        payload = json.dumps(
            {
                "turn_id": turn_id,
                "owner_user_id": owner_user_id,
                "pet_id": pet_id,
                "memory_subject_id": memory_subject_id,
                "relationship_epoch_id": relationship_epoch_id,
                "academic_stage": academic_stage,
                "surface": surface,
                "interaction_kind": interaction_kind,
                "request_digest": request_digest,
                "occurred_at": occurred_at,
                "policy": asdict(policy),
                "persistence_allowed": persistence_allowed,
                "prompt_context": prompt_context,
                "used_evidence_ids": used_evidence_ids,
                "source_text_digest": (
                    hashlib.sha256(source_text.encode("utf-8")).hexdigest()
                    if source_text is not None
                    else None
                ),
                "conversation_digest": conversation_digest,
                "growth_moment": growth_moment,
                "behavior_plan": (
                    asdict(behavior_plan) if behavior_plan is not None else None
                ),
                "behavior_plan_active": behavior_plan_active,
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(self._token_secret, payload, hashlib.sha256).hexdigest()

    def _build_turn_behavior_plan(
        self,
        *,
        request: CompanionTurnRequest,
        policy: CompanionPolicy,
        turn_count: int,
    ) -> TurnBehaviorPlan | None:
        if self._turn_behavior_plan_mode == "off":
            return None
        return plan_turn_behavior(
            TurnBehaviorPlanningInputs(
                policy=policy,
                pet_id=request.subject.pet_id,
                turn_id=request.turn_id,
                turn_count=turn_count,
                context=request.context,
                interaction_kind=request.interaction_kind,
            )
        )

    def prepare_turn(self, request: CompanionTurnRequest) -> PreparedCompanionTurn:
        started_at = perf_counter()
        source_text = (
            request.source_text
            if request.interaction_kind == "conversation"
            else None
        )
        if (
            request.subject.speaker_identity != "confirmed"
            or not request.subject.persistence_allowed
            or not self._allows(
                request.subject.owner_user_id,
                "COMPANION_MEMORY_READ",
            )
        ):
            neutral_policy = build_companion_policy(
                CompanionPolicyInputs(
                    speaker_identity=request.subject.speaker_identity,
                    surface=request.surface,
                    academic_stage=request.subject.academic_stage,
                    interaction_kind=request.interaction_kind,
                    context=request.context,
                )
            )
            behavior_plan = self._build_turn_behavior_plan(
                request=request,
                policy=neutral_policy,
                turn_count=0,
            )
            prepared = PreparedCompanionTurn(
                turn_id=request.turn_id,
                owner_user_id=request.subject.owner_user_id,
                pet_id=request.subject.pet_id,
                memory_subject_id=request.subject.memory_subject_id,
                relationship_epoch_id=None,
                request_digest=request.request_digest,
                occurred_at=request.occurred_at,
                prepared_token=self._prepared_token(
                    turn_id=request.turn_id,
                    owner_user_id=request.subject.owner_user_id,
                    pet_id=request.subject.pet_id,
                    memory_subject_id=request.subject.memory_subject_id,
                    relationship_epoch_id=None,
                    academic_stage=request.subject.academic_stage,
                    surface=request.surface,
                    interaction_kind=request.interaction_kind,
                    request_digest=request.request_digest,
                    occurred_at=request.occurred_at,
                    policy=neutral_policy,
                    persistence_allowed=False,
                    prompt_context=(),
                    used_evidence_ids=(),
                    source_text=None,
                    conversation_digest=None,
                    growth_moment=None,
                    behavior_plan=behavior_plan,
                    behavior_plan_active=self._turn_behavior_plan_mode == "active",
                ),
                policy=neutral_policy,
                persistence_allowed=False,
                behavior_plan=behavior_plan,
                behavior_plan_active=self._turn_behavior_plan_mode == "active",
                academic_stage=request.subject.academic_stage,
                surface=request.surface,
                interaction_kind=request.interaction_kind,
                source_text=None,
                conversation_digest=None,
            )
            return self._record_prepared_turn(prepared, started_at=started_at)
        if self._store is None:
            raise CompanionUnavailableError("CompanionStore is not configured")
        academic_state = self._store.get_academic_state(
            owner_user_id=request.subject.owner_user_id,
            pet_id=request.subject.pet_id,
            memory_subject_id=request.subject.memory_subject_id,
        )
        academic_stage = (
            request.subject.academic_stage
            if academic_state is None
            else str(academic_state["academic_stage"])
        )
        epoch = self._store.get_active_epoch(
            owner_user_id=request.subject.owner_user_id,
            pet_id=request.subject.pet_id,
        )
        if epoch is not None:
            self._store.ensure_anniversary_boundaries(
                owner_user_id=request.subject.owner_user_id,
                pet_id=request.subject.pet_id,
                memory_subject_id=request.subject.memory_subject_id,
                relationship_epoch_id=epoch.epoch_id,
                academic_stage=academic_stage,
                now=request.occurred_at,
            )
        evidence = ()
        birth_temperament = (
            self._store.get_birth_temperament(
                owner_user_id=request.subject.owner_user_id,
                pet_id=request.subject.pet_id,
            )
            if epoch is not None
            else None
        )
        policy_inputs = CompanionPolicyInputs(
            speaker_identity=request.subject.speaker_identity,
            surface=request.surface,
            academic_stage=academic_stage,
            interaction_kind=request.interaction_kind,
            birth_temperament=birth_temperament,
            relationship=RelationshipQualityMetrics(),
            context=request.context,
        )
        if epoch is not None:
            material = self._store.load_policy_material(
                owner_user_id=request.subject.owner_user_id,
                pet_id=request.subject.pet_id,
                memory_subject_id=request.subject.memory_subject_id,
                relationship_epoch_id=epoch.epoch_id,
                now=request.occurred_at,
                surface=request.surface,
                interaction_kind=request.interaction_kind,
                context=request.context,
            )
            policy_inputs = policy_inputs_from_evidence(
                speaker_identity=request.subject.speaker_identity,
                surface=request.surface,
                academic_stage=academic_stage,
                interaction_kind=request.interaction_kind,
                turn_count=material.turn_count,
                distinct_interaction_days=material.distinct_interaction_days,
                evidence=material.evidence,
                active_adjustments=material.active_adjustments,
                behavior_adjustments=material.behavior_adjustments,
                context=request.context,
                birth_temperament=birth_temperament,
                relationship_started_at=material.relationship_started_at,
                interaction_dates=material.interaction_dates,
                historical_stage=material.historical_stage,
                relationship_stage_history=material.relationship_stage_history,
                now=request.occurred_at,
            )
        policy = build_companion_policy(policy_inputs)
        if epoch is not None:
            policy = _apply_va_projection(
                policy,
                self._load_va_projection_safely(
                    subject=request.subject,
                    relationship_epoch_id=epoch.epoch_id,
                    now=request.occurred_at,
                    policy=policy,
                ),
                surface=request.surface,
            )
        policy = _apply_current_turn_corrections(
            policy,
            request.current_turn_corrections,
        )
        behavior_plan = self._build_turn_behavior_plan(
            request=request,
            policy=policy,
            turn_count=policy_inputs.relationship.turn_count,
        )
        if epoch is not None:
            self._record_relationship_stage_event_safely(
                owner_user_id=request.subject.owner_user_id,
                pet_id=request.subject.pet_id,
                memory_subject_id=request.subject.memory_subject_id,
                relationship_epoch_id=epoch.epoch_id,
                relationship_stage=policy.relationship_stage,
                quality=relationship_quality_snapshot(policy_inputs.relationship),
                reason_codes=relationship_stage_reason_codes(
                    policy_inputs.relationship
                ),
                policy_version=policy.version,
                now=request.occurred_at,
            )
        if (
            epoch is not None
            and policy.memory_reference_budget > 0
            and not self.uses_semantic_user_facts
        ):
            retrieval_hints = dict(request.retrieval_hints)
            if request.surface == "initiative":
                excluded = tuple(
                    dict.fromkeys(
                        (
                            *retrieval_hints.get("exclude_sensitivities", ()),
                            "sensitive",
                        )
                    )
                )
                retrieval_hints["exclude_sensitivities"] = excluded
            evidence = self._store.recall_evidence(
                owner_user_id=request.subject.owner_user_id,
                pet_id=request.subject.pet_id,
                memory_subject_id=request.subject.memory_subject_id,
                relationship_epoch_id=epoch.epoch_id,
                turn_id=request.turn_id,
                interaction_kind=request.interaction_kind,
                now=request.occurred_at,
                retrieval_query=request.retrieval_query,
                retrieval_hints=retrieval_hints,
                limit=policy.memory_reference_budget,
            )
        selected_evidence = evidence
        prompt_context = tuple(
            _retrieval_prompt_summary(
                item,
                include_semantic_label=request.interaction_kind == "explicit_recall",
            )
            for item in selected_evidence
        )
        used_evidence_ids = tuple(item.evidence_id for item in selected_evidence)
        growth_moment = (
            self._store.claim_growth_moment(
                owner_user_id=request.subject.owner_user_id,
                pet_id=request.subject.pet_id,
                memory_subject_id=request.subject.memory_subject_id,
                relationship_epoch_id=epoch.epoch_id,
                academic_stage=academic_stage,
                turn_id=request.turn_id,
                now=request.occurred_at,
            )
            if (
                epoch is not None
                and request.surface == "voice"
                and request.interaction_kind == "conversation"
                and policy.relationship_posture == "steady"
            )
            else None
        )
        prepared = PreparedCompanionTurn(
            turn_id=request.turn_id,
            owner_user_id=request.subject.owner_user_id,
            pet_id=request.subject.pet_id,
            memory_subject_id=request.subject.memory_subject_id,
            relationship_epoch_id=epoch.epoch_id if epoch is not None else None,
            request_digest=request.request_digest,
            occurred_at=request.occurred_at,
            prepared_token=self._prepared_token(
                turn_id=request.turn_id,
                owner_user_id=request.subject.owner_user_id,
                pet_id=request.subject.pet_id,
                memory_subject_id=request.subject.memory_subject_id,
                relationship_epoch_id=epoch.epoch_id if epoch is not None else None,
                academic_stage=academic_stage,
                surface=request.surface,
                interaction_kind=request.interaction_kind,
                request_digest=request.request_digest,
                occurred_at=request.occurred_at,
                policy=policy,
                persistence_allowed=True,
                prompt_context=prompt_context,
                used_evidence_ids=used_evidence_ids,
                source_text=source_text,
                conversation_digest=request.conversation_digest,
                growth_moment=growth_moment,
                behavior_plan=behavior_plan,
                behavior_plan_active=self._turn_behavior_plan_mode == "active",
            ),
            policy=policy,
            persistence_allowed=True,
            behavior_plan=behavior_plan,
            behavior_plan_active=self._turn_behavior_plan_mode == "active",
            prompt_context=prompt_context,
            used_evidence_ids=used_evidence_ids,
            academic_stage=academic_stage,
            surface=request.surface,
            interaction_kind=request.interaction_kind,
            source_text=source_text,
            conversation_digest=request.conversation_digest,
            growth_moment=growth_moment,
        )
        return self._record_prepared_turn(prepared, started_at=started_at)

    def _recall_companion_memory(
        self,
        prepared: PreparedCompanionTurn,
        *,
        query: object,
        fact_keys: object = (),
        kinds: object = (),
        exclude_sensitivities: object = (),
        occurred_after: object = None,
        occurred_before: object = None,
        minimum_memory_reference_budget: object = 0,
    ) -> tuple[PreparedCompanionTurn, Mapping[str, object]]:
        """Execute the single internal memory tool under CompanionMind policy."""
        if not isinstance(query, str):
            raise MemoryRecallPlanningError("memory recall query is invalid")
        if (
            isinstance(minimum_memory_reference_budget, bool)
            or not isinstance(minimum_memory_reference_budget, int)
            or minimum_memory_reference_budget < 0
            or minimum_memory_reference_budget > 3
        ):
            raise MemoryRecallPlanningError("minimum memory recall budget is invalid")
        for name, value in (
            ("fact_keys", fact_keys),
            ("kinds", kinds),
            ("exclude_sensitivities", exclude_sensitivities),
        ):
            if not isinstance(value, (list, tuple)) or any(
                not isinstance(item, str) for item in value
            ):
                raise MemoryRecallPlanningError(
                    f"memory recall {name} must be a text list"
                )
        for name, value in (
            ("occurred_after", occurred_after),
            ("occurred_before", occurred_before),
        ):
            if value is not None and not isinstance(value, str):
                raise MemoryRecallPlanningError(
                    f"memory recall {name} must be text or null"
                )
        effective_memory_reference_budget = max(
            prepared.policy.memory_reference_budget,
            minimum_memory_reference_budget,
        )
        effective_policy = (
            prepared.policy
            if effective_memory_reference_budget
            == prepared.policy.memory_reference_budget
            else replace(
                prepared.policy,
                memory_reference_budget=effective_memory_reference_budget,
            )
        )
        request = MemoryRecallRequest(
            request_id=f"{prepared.turn_id}:recall_companion_memory",
            subject=CompanionSubjectContext(
                owner_user_id=prepared.owner_user_id,
                pet_id=prepared.pet_id,
                memory_subject_id=prepared.memory_subject_id,
                speaker_identity=(
                    "confirmed" if prepared.persistence_allowed else "unknown"
                ),
                academic_stage=prepared.academic_stage,
                persistence_allowed=prepared.persistence_allowed,
            ),
            interaction_kind=prepared.interaction_kind,
            surface=prepared.surface,
            query=query,
            memory_reference_budget=effective_memory_reference_budget,
            requested_fact_keys=tuple(fact_keys),
            requested_kinds=tuple(kinds),
            exclude_sensitivities=tuple(exclude_sensitivities),
            occurred_after=occurred_after,
            occurred_before=occurred_before,
        )
        plan = MemoryRecallPlanner().plan(request)
        if (
            not plan.should_recall
            or prepared.relationship_epoch_id is None
            or self._store is None
        ):
            return prepared, {
                "memories": (),
                "reason_code": plan.reason_code,
            }
        hints: dict[str, object] = {
            "fact_keys": tuple(
                dict.fromkeys(
                    alias
                    for fact_key in plan.fact_keys
                    for alias in memory_fact_key_storage_aliases(fact_key)
                )
            ),
            "kinds": plan.kinds,
            "exclude_sensitivities": plan.exclude_sensitivities,
        }
        if plan.occurred_after is not None:
            hints["time_from"] = plan.occurred_after
        if plan.occurred_before is not None:
            hints["time_to"] = plan.occurred_before
        evidence = self._store.recall_evidence(
            owner_user_id=prepared.owner_user_id,
            pet_id=prepared.pet_id,
            memory_subject_id=prepared.memory_subject_id,
            relationship_epoch_id=prepared.relationship_epoch_id,
            turn_id=prepared.turn_id,
            interaction_kind=prepared.interaction_kind,
            now=prepared.occurred_at,
            retrieval_query=plan.query,
            retrieval_hints=hints,
            limit=plan.limit,
        )
        if not evidence:
            return prepared, {
                "memories": (),
                "reason_code": plan.reason_code,
            }
        prompt_context = tuple(
            _retrieval_prompt_summary(
                item,
                include_semantic_label=prepared.interaction_kind == "explicit_recall",
            )
            for item in evidence
        )
        used_evidence_ids = tuple(item.evidence_id for item in evidence)
        updated = replace(
            prepared,
            policy=effective_policy,
            prompt_context=prompt_context,
            used_evidence_ids=used_evidence_ids,
            prepared_token=self._prepared_token(
                turn_id=prepared.turn_id,
                owner_user_id=prepared.owner_user_id,
                pet_id=prepared.pet_id,
                memory_subject_id=prepared.memory_subject_id,
                relationship_epoch_id=prepared.relationship_epoch_id,
                academic_stage=prepared.academic_stage,
                surface=prepared.surface,
                interaction_kind=prepared.interaction_kind,
                request_digest=prepared.request_digest,
                occurred_at=prepared.occurred_at,
                policy=effective_policy,
                persistence_allowed=prepared.persistence_allowed,
                prompt_context=prompt_context,
                used_evidence_ids=used_evidence_ids,
                source_text=prepared.source_text,
                conversation_digest=prepared.conversation_digest,
                growth_moment=prepared.growth_moment,
                behavior_plan=prepared.behavior_plan,
                behavior_plan_active=prepared.behavior_plan_active,
            ),
        )
        return updated, {
            "memories": prompt_context,
            "reason_code": plan.reason_code,
        }

    @staticmethod
    def _record_prepared_turn(
        prepared: PreparedCompanionTurn,
        *,
        started_at: float,
    ) -> PreparedCompanionTurn:
        if prepared.behavior_plan is not None:
            LOGGER.info(
                "Companion turn behavior plan %s",
                json.dumps(
                    {
                        "active": prepared.behavior_plan_active,
                        "event": "companion_turn_behavior_plan",
                        "pet_id": prepared.pet_id,
                        "plan": asdict(prepared.behavior_plan),
                        "turn_id": prepared.turn_id,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
        LOGGER.info(
            "Companion turn prepared",
            extra={
                "companion_turn_id": prepared.turn_id,
                "companion_pet_id": prepared.pet_id,
                "companion_memory_subject_id": prepared.memory_subject_id,
                "companion_persistence_allowed": prepared.persistence_allowed,
                "companion_evidence_ids": prepared.used_evidence_ids,
                "companion_evidence_count": len(prepared.used_evidence_ids),
                "companion_relationship_stage": prepared.policy.relationship_stage,
                "companion_policy_version": prepared.policy.version,
                "companion_turn_behavior_plan_version": (
                    prepared.behavior_plan.version
                    if prepared.behavior_plan is not None
                    else None
                ),
                "companion_turn_behavior_primary_move": (
                    prepared.behavior_plan.primary_move
                    if prepared.behavior_plan is not None
                    else None
                ),
                "companion_turn_behavior_salient_traits": (
                    prepared.behavior_plan.salient_traits
                    if prepared.behavior_plan is not None
                    else ()
                ),
                "companion_turn_behavior_active": prepared.behavior_plan_active,
                "companion_surface": prepared.surface,
                "companion_interaction_kind": prepared.interaction_kind,
                "companion_prepare_duration_ms": max(
                    (perf_counter() - started_at) * 1000,
                    0.0,
                ),
            },
        )
        return prepared

    def commit_turn(
        self,
        prepared: PreparedCompanionTurn,
        outcome: CompanionTurnOutcome,
    ) -> CompanionCommitResult:
        expected_token = self._prepared_token(
            turn_id=prepared.turn_id,
            owner_user_id=prepared.owner_user_id,
            pet_id=prepared.pet_id,
            memory_subject_id=prepared.memory_subject_id,
            relationship_epoch_id=prepared.relationship_epoch_id,
            academic_stage=prepared.academic_stage,
            surface=prepared.surface,
            interaction_kind=prepared.interaction_kind,
            request_digest=prepared.request_digest,
            occurred_at=prepared.occurred_at,
            policy=prepared.policy,
            persistence_allowed=prepared.persistence_allowed,
            prompt_context=prepared.prompt_context,
            used_evidence_ids=prepared.used_evidence_ids,
            source_text=prepared.source_text,
            conversation_digest=prepared.conversation_digest,
            growth_moment=prepared.growth_moment,
            behavior_plan=prepared.behavior_plan,
            behavior_plan_active=prepared.behavior_plan_active,
        )
        if not hmac.compare_digest(prepared.prepared_token, expected_token):
            raise CompanionContractError(
                "prepared token does not match the request digest"
            )
        if not prepared.persistence_allowed:
            return CompanionCommitResult(
                turn_id=prepared.turn_id,
                status="not_persisted",
            )
        if not self._allows(
            prepared.owner_user_id,
            "COMPANION_MEMORY_WRITE",
        ):
            return CompanionCommitResult(
                turn_id=prepared.turn_id,
                status="not_persisted",
            )
        if self._store is None:
            raise CompanionUnavailableError("CompanionStore is not configured")
        pending_evidence = self._pending_evidence(prepared, outcome)
        connection_need_update = self._connection_need_update(
            prepared,
            pending_evidence,
        )
        opportunities = self._turn_initiative_opportunities(
            prepared,
            pending_evidence,
        )
        jobs: tuple[PendingCompanionJob, ...] = ()
        consolidation_evidence = tuple(
            item for item in pending_evidence if item.kind != "recent_conversation"
        )
        if consolidation_evidence:
            job_key = f"session-consolidation:{prepared.pet_id}:{prepared.turn_id}"
            jobs = (
                PendingCompanionJob(
                    job_id=str(uuid5(NAMESPACE_URL, job_key)),
                    pet_id=prepared.pet_id,
                    relationship_epoch_id=prepared.relationship_epoch_id,
                    job_kind="session_consolidation",
                    idempotency_key=job_key,
                    payload={
                        "turn_id": prepared.turn_id,
                        "memory_subject_id": prepared.memory_subject_id,
                        "evidence_ids": [
                            item.evidence_id for item in consolidation_evidence
                        ],
                    },
                    due_at=prepared.occurred_at,
                    schema_version="companion-session-v1",
                ),
            )
        if prepared.source_text is not None and self._worker is not None:
            candidate_job_key = (
                f"memory-candidate-extraction:{prepared.pet_id}:{prepared.turn_id}"
            )
            jobs = (
                *jobs,
                PendingCompanionJob(
                    job_id=str(uuid5(NAMESPACE_URL, candidate_job_key)),
                    pet_id=prepared.pet_id,
                    relationship_epoch_id=prepared.relationship_epoch_id,
                    job_kind="memory_candidate_extraction",
                    idempotency_key=candidate_job_key,
                    payload={
                        "turn_id": prepared.turn_id,
                        "memory_subject_id": prepared.memory_subject_id,
                        "conversation_digest": prepared.conversation_digest,
                        "legacy_fact_keys": list(outcome.legacy_memory_fact_keys),
                    },
                    due_at=prepared.occurred_at,
                    schema_version="companion-memory-candidate-v1",
                ),
            )
        result = self._store.commit_turn(
            prepared,
            outcome,
            academic_stage=prepared.academic_stage,
            pending_evidence=pending_evidence,
            jobs=jobs,
            opportunities=opportunities,
            connection_need_update=connection_need_update,
        )
        self._record_current_relationship_stage(
            owner_user_id=prepared.owner_user_id,
            pet_id=prepared.pet_id,
            memory_subject_id=prepared.memory_subject_id,
            academic_stage=prepared.academic_stage,
            now=prepared.occurred_at,
        )
        return result

    def _connection_need_update(
        self,
        prepared: PreparedCompanionTurn,
        evidence: tuple[PendingCompanionEvidence, ...],
    ) -> PendingConnectionNeedUpdate | None:
        if prepared.interaction_kind != "conversation" or prepared.source_text is None:
            return None
        source = next(
            (item for item in evidence if item.kind == "recent_conversation"),
            None,
        )
        if source is None:
            return None
        feedback_outcome = "connection_responded"
        if any(
            item.kind == "interaction_feedback"
            and item.content.get("outcome") == "too_proactive"
            for item in evidence
        ):
            feedback_outcome = "rejected"
        initiative_bias = (
            temperament_dimensions_for_pet(prepared.pet_id)["companion_initiative"]
            if prepared.relationship_epoch_id is None
            else prepared.policy.expression_style.initiative_bias
        )
        initiative_level = self._store.load_active_initiative_contract_level(
            pet_id=prepared.pet_id,
            memory_subject_id=prepared.memory_subject_id,
        ) or prepared.policy.initiative_level
        delay = connection_bid_delay(
            self._connection_bid_delays[initiative_bias],
            relationship_stage=prepared.policy.relationship_stage,
            initiative_level=initiative_level,
        )
        occurred_at = datetime.fromisoformat(prepared.occurred_at)
        return PendingConnectionNeedUpdate(
            turn_id=prepared.turn_id,
            source_evidence_id=source.evidence_id,
            last_meaningful_interaction_at=prepared.occurred_at,
            next_eligible_at=(occurred_at + delay).isoformat(),
            initiative_bias=initiative_bias,
            relationship_stage=prepared.policy.relationship_stage,
            initiative_level=initiative_level,
            threshold_seconds=max(int(delay.total_seconds()), 1),
            feedback_window_seconds=max(
                int(self._connection_feedback_window.total_seconds()), 1
            ),
            feedback_outcome=feedback_outcome,
            presence_window_seconds=max(int(self._presence_window.total_seconds()), 1),
        )

    def _turn_initiative_opportunities(
        self,
        prepared: PreparedCompanionTurn,
        evidence: tuple[PendingCompanionEvidence, ...],
    ) -> tuple[PendingInitiativeOpportunity, ...]:
        opportunities: list[PendingInitiativeOpportunity] = []
        for item in evidence:
            if item.kind == "meaningful_moment":
                opportunity_kind = "followup"
                reason_code = "evidence_backed_followup"
                occurred_at = datetime.fromisoformat(prepared.occurred_at)
                followup_at = item.content.get("followup_at")
                if isinstance(followup_at, str) and followup_at.strip():
                    try:
                        due_at = max(
                            datetime.fromisoformat(followup_at), occurred_at
                        ).isoformat()
                    except (TypeError, ValueError):
                        due_at = (
                            occurred_at + self._initiative_followup_delay
                        ).isoformat()
                elif item.content.get("followup_time") == "next_day":
                    due_at = (occurred_at + timedelta(days=1)).isoformat()
                else:
                    due_at = (
                        occurred_at + self._initiative_followup_delay
                    ).isoformat()
                safe_brief = item.source_summary
                if item.content.get("followup_time") == "next_day":
                    topic = str(item.content.get("topic") or "").strip()
                    if topic:
                        safe_brief = f"现在按约定直接问用户：{topic}？"
            elif item.kind == "accepted_help":
                # A positive reaction is a safe, low-pressure cue to check back
                # later. Daily limits and quiet hours remain the final guards.
                opportunity_kind = "followup"
                reason_code = "helpful_response_followup"
                due_at = (
                    datetime.fromisoformat(prepared.occurred_at)
                    + self._initiative_followup_delay
                ).isoformat()
                safe_brief = item.source_summary
            elif item.kind == "followup_completed":
                opportunity_kind = "celebration"
                reason_code = "followup_completed"
                due_at = prepared.occurred_at
                safe_brief = item.source_summary
            else:
                continue
            opportunities.append(
                PendingInitiativeOpportunity(
                    opportunity_id=str(
                        uuid5(
                            NAMESPACE_URL,
                            "xiaoxin:initiative-opportunity:"
                            f"{item.evidence_id}:{opportunity_kind}",
                        )
                    ),
                    opportunity_kind=opportunity_kind,
                    reason_code=reason_code,
                    evidence_ids=(item.evidence_id,),
                    safe_brief=safe_brief,
                    due_at=due_at,
                )
            )
        return tuple(opportunities)

    def observe(
        self,
        observation: CompanionObservation | CompanionVAEvent,
    ) -> CompanionObserveResult | CompanionVAEventResult:
        if isinstance(observation, CompanionVAEvent):
            return self._apply_va_event(observation)
        observation_id = str(
            uuid5(
                NAMESPACE_URL,
                "companion-observation:"
                f"{observation.subject.pet_id}:{observation.idempotency_key}",
            )
        )
        if (
            observation.subject.speaker_identity != "confirmed"
            or not observation.subject.persistence_allowed
            or not self._allows(
                observation.subject.owner_user_id,
                "COMPANION_MEMORY_WRITE",
            )
        ):
            return CompanionObserveResult(
                observation_id=observation_id,
                status="not_persisted",
            )
        if self._store is None:
            raise CompanionUnavailableError("CompanionStore is not configured")
        evidence = self._observation_evidence(observation, observation_id)
        opportunities = self._observation_initiative_opportunities(
            observation,
            evidence,
        )
        result = self._store.record_observation(
            observation,
            observation_id=observation_id,
            evidence=evidence,
            opportunities=opportunities,
        )
        self._record_current_relationship_stage(
            owner_user_id=observation.subject.owner_user_id,
            pet_id=observation.subject.pet_id,
            memory_subject_id=observation.subject.memory_subject_id,
            academic_stage=observation.subject.academic_stage,
            now=observation.occurred_at,
        )
        LOGGER.info(
            "Companion observation recorded",
            extra={
                "companion_observation_id": result.observation_id,
                "companion_observation_kind": observation.kind,
                "companion_observation_status": result.status,
                "companion_pet_id": observation.subject.pet_id,
                "companion_memory_subject_id": (observation.subject.memory_subject_id),
                "companion_evidence_ids": result.evidence_ids,
                "companion_evidence_count": len(result.evidence_ids),
            },
        )
        return result

    def _apply_va_event(self, event: CompanionVAEvent) -> CompanionVAEventResult:
        if self._store is None:
            raise CompanionUnavailableError("CompanionStore is not configured")
        current = self.project(
            CompanionProjectionRequest(
                subject=event.subject,
                surface="voice",
                now=event.occurred_at,
            )
        )
        return self._store.apply_va_event(
            event=event,
            xiaoxin_age=current.xiaoxin_age,
            relationship_stage=current.relationship_stage,
        )

    def _load_va_projection_safely(
        self,
        *,
        subject: CompanionSubjectContext,
        relationship_epoch_id: str,
        now: str,
        policy: CompanionPolicy,
    ) -> Mapping[str, object]:
        if self._store is None:
            return {}
        try:
            return self._store.load_va_projection(
                owner_user_id=subject.owner_user_id,
                pet_id=subject.pet_id,
                memory_subject_id=subject.memory_subject_id,
                relationship_epoch_id=relationship_epoch_id,
                now=now,
                xiaoxin_age=policy.xiaoxin_age,
                relationship_stage=policy.relationship_stage,
            )
        except Exception as exc:
            LOGGER.warning(
                "Companion VA projection failed closed",
                extra={
                    "companion_pet_id": subject.pet_id,
                    "companion_memory_subject_id": subject.memory_subject_id,
                    "companion_error_type": type(exc).__name__,
                },
            )
            return {}

    @staticmethod
    def _observation_initiative_opportunities(
        observation: CompanionObservation,
        evidence: tuple[CompanionEvidence, ...],
    ) -> tuple[PendingInitiativeOpportunity, ...]:
        opportunities: list[PendingInitiativeOpportunity] = []
        for item in evidence:
            opportunity_kind: str | None = None
            reason_code: str | None = None
            due_at = observation.occurred_at
            if item.kind in {"goal_completed", "followup_completed"}:
                opportunity_kind = "celebration"
                reason_code = item.kind
            elif item.kind == "goal":
                opportunity_kind = "goal_progress"
                reason_code = "goal_progress_checkin"
                target_at = item.content.get("target_at")
                if isinstance(target_at, str) and target_at.strip():
                    candidate = datetime.fromisoformat(target_at) - timedelta(days=1)
                    due_at = max(
                        candidate,
                        datetime.fromisoformat(observation.occurred_at),
                    ).isoformat()
                else:
                    due_at = (
                        datetime.fromisoformat(observation.occurred_at)
                        + timedelta(days=7)
                    ).isoformat()
            elif item.kind == "future_event":
                opportunity_kind = "future_event"
                reason_code = "future_event_upcoming"
                scheduled_at = item.content.get("scheduled_at") or item.content.get(
                    "due_at"
                )
                if isinstance(scheduled_at, str) and scheduled_at.strip():
                    candidate = datetime.fromisoformat(scheduled_at) - timedelta(
                        hours=2
                    )
                    due_at = max(
                        candidate,
                        datetime.fromisoformat(observation.occurred_at),
                    ).isoformat()
            elif (
                item.kind == "boundary"
                and item.content.get("boundary_key")
                in {"initiative_frequency", "checkin_frequency"}
                and str(item.content.get("value", "")).lower()
                not in {"disabled", "off", "never"}
            ):
                opportunity_kind = "checkin"
                reason_code = "user_configured_checkin"
                due_at = (
                    datetime.fromisoformat(observation.occurred_at) + timedelta(days=1)
                ).isoformat()
            if opportunity_kind is None or reason_code is None:
                continue
            opportunities.append(
                PendingInitiativeOpportunity(
                    opportunity_id=str(
                        uuid5(
                            NAMESPACE_URL,
                            "xiaoxin:initiative-opportunity:"
                            f"{item.evidence_id}:{opportunity_kind}",
                        )
                    ),
                    opportunity_kind=opportunity_kind,
                    reason_code=reason_code,
                    evidence_ids=(item.evidence_id,),
                    safe_brief=item.source_summary,
                    due_at=due_at,
                )
            )
        return tuple(opportunities)

    def _defer_observation(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        idempotency_key: str,
        kind: str,
        source_kind: str,
        source_ref: str,
        occurred_at: str,
        payload: Mapping[str, object],
        safe_summary: str,
        queued_reason: str,
    ) -> CompanionObserveResult:
        if self._store is None:
            raise CompanionUnavailableError("CompanionStore is not configured")
        return self._store.defer_observation(
            owner_user_id=owner_user_id,
            pet_id=pet_id,
            idempotency_key=idempotency_key,
            kind=kind,
            source_kind=source_kind,
            source_ref=source_ref,
            occurred_at=occurred_at,
            payload=payload,
            safe_summary=safe_summary,
            queued_reason=queued_reason,
        )

    def _flush_deferred_observations(
        self,
        subject: CompanionSubjectContext,
    ) -> tuple[CompanionObserveResult, ...]:
        if subject.speaker_identity != "confirmed" or not subject.persistence_allowed:
            return ()
        if self._store is None:
            raise CompanionUnavailableError("CompanionStore is not configured")
        pending = self._store.list_pending_observations(
            owner_user_id=subject.owner_user_id,
            pet_id=subject.pet_id,
        )
        results: list[CompanionObserveResult] = []
        for item in pending:
            try:
                result = self.observe(
                    CompanionObservation(
                        idempotency_key=str(item["idempotency_key"]),
                        subject=subject,
                        kind=str(item["kind"]),
                        source_kind=str(item["source_kind"]),
                        source_ref=str(item["source_ref"]),
                        occurred_at=str(item["occurred_at"]),
                        payload=item["payload"],
                        safe_summary=str(item["safe_summary"]),
                    )
                )
            except Exception as exc:
                error_code = type(exc).__name__
                self._store.mark_pending_observation_failure(
                    observation_id=str(item["observation_id"]),
                    pending_digest=str(item["pending_digest"]),
                    error_code=error_code,
                )
                LOGGER.warning(
                    "Companion pending observation backfill failed",
                    extra={
                        "companion_observation_id": item["observation_id"],
                        "companion_pet_id": subject.pet_id,
                        "companion_memory_subject_id": subject.memory_subject_id,
                        "companion_error_code": error_code,
                    },
                )
                continue
            self._store.delete_pending_observation(
                observation_id=str(item["observation_id"]),
                pending_digest=str(item["pending_digest"]),
            )
            results.append(result)
        return tuple(results)

    @staticmethod
    def _observation_evidence(
        observation: CompanionObservation,
        observation_id: str,
    ) -> tuple[CompanionEvidence, ...]:
        if observation.kind in {
            "reminder_delivered",
            "reminder_tts_completed",
            "reminder_delivery_failed",
        }:
            delivery_id = observation.payload.get("delivery_id")
            if not isinstance(delivery_id, str) or not delivery_id.strip():
                raise CompanionContractError(
                    "reminder observation requires delivery_id"
                )
            if delivery_id != observation.source_ref:
                raise CompanionContractError(
                    "reminder observation source_ref must match delivery_id"
                )
            if observation.kind == "reminder_delivery_failed":
                if observation.payload.get("delivery_status") != "failed":
                    raise CompanionContractError(
                        "failed reminder observation status must be failed"
                    )
                failure_reason = observation.payload.get("failure_reason")
                if not isinstance(failure_reason, str) or not failure_reason.strip():
                    raise CompanionContractError(
                        "failed reminder observation requires failure_reason"
                    )
            return ()
        if observation.kind in {"goal_set", "goal_completed"}:
            goal_id = observation.payload.get("goal_id")
            title = observation.payload.get("title")
            expected_status = (
                "completed" if observation.kind == "goal_completed" else "active"
            )
            if not isinstance(goal_id, str) or not goal_id.strip():
                raise CompanionContractError("goal observation requires goal_id")
            if goal_id != observation.source_ref:
                raise CompanionContractError(
                    "goal observation source_ref must match goal_id"
                )
            if not isinstance(title, str) or not title.strip():
                raise CompanionContractError("goal observation requires title")
            if observation.payload.get("status") != expected_status:
                raise CompanionContractError(
                    f"{observation.kind} status must be {expected_status}"
                )
            fact_key = f"goal:{goal_id}"
            completed = observation.kind == "goal_completed"
            return (
                CompanionEvidence(
                    evidence_id=str(
                        uuid5(
                            NAMESPACE_URL,
                            f"observation-evidence:{observation_id}:0",
                        )
                    ),
                    pet_id=observation.subject.pet_id,
                    memory_subject_id=observation.subject.memory_subject_id,
                    ownership_scope="user",
                    relationship_epoch_id=None,
                    kind="goal_completed" if completed else "goal",
                    content={**dict(observation.payload), "fact_key": fact_key},
                    source_kind=observation.source_kind,
                    source_ref=observation.source_ref,
                    source_summary=observation.safe_summary,
                    attribution="explicit_user_input",
                    confidence=1.0,
                    occurred_at=observation.occurred_at,
                    retention="long_term" if completed else "until_resolved",
                    status="active",
                    prompt_eligible=True,
                    fact_key=fact_key,
                    importance=0.85,
                    sensitivity="private",
                    valid_from=observation.occurred_at,
                    valid_until=(
                        None
                        if completed
                        else _optional_aware_datetime(
                            observation.payload.get("target_at"),
                            field="goal target_at",
                        )
                    ),
                ),
            )
        if observation.kind in {"future_event_set", "future_event_cancelled"}:
            event_id = observation.payload.get("event_id")
            title = observation.payload.get("title")
            scheduled_at = observation.payload.get("scheduled_at")
            cancelled = observation.kind == "future_event_cancelled"
            expected_status = "cancelled" if cancelled else "planned"
            if not isinstance(event_id, str) or not event_id.strip():
                raise CompanionContractError(
                    "future event observation requires event_id"
                )
            if event_id != observation.source_ref:
                raise CompanionContractError(
                    "future event source_ref must match event_id"
                )
            if not isinstance(title, str) or not title.strip():
                raise CompanionContractError("future event observation requires title")
            scheduled_at = _required_aware_datetime(
                scheduled_at,
                field="future event scheduled_at",
            )
            if observation.payload.get("status") != expected_status:
                raise CompanionContractError(
                    f"{observation.kind} status must be {expected_status}"
                )
            fact_key = f"event:{event_id}"
            return (
                CompanionEvidence(
                    evidence_id=str(
                        uuid5(
                            NAMESPACE_URL,
                            f"observation-evidence:{observation_id}:0",
                        )
                    ),
                    pet_id=observation.subject.pet_id,
                    memory_subject_id=observation.subject.memory_subject_id,
                    ownership_scope="user",
                    relationship_epoch_id=None,
                    kind=("future_event_cancelled" if cancelled else "future_event"),
                    content={**dict(observation.payload), "fact_key": fact_key},
                    source_kind=observation.source_kind,
                    source_ref=observation.source_ref,
                    source_summary=observation.safe_summary,
                    attribution="explicit_user_input",
                    confidence=1.0,
                    occurred_at=observation.occurred_at,
                    retention="short_term" if cancelled else "until_resolved",
                    status="active",
                    prompt_eligible=not cancelled,
                    fact_key=fact_key,
                    importance=0.7,
                    sensitivity="private",
                    valid_from=observation.occurred_at,
                    valid_until=None if cancelled else scheduled_at,
                ),
            )
        if observation.kind == "boundary_set":
            boundary_key = observation.payload.get("boundary_key")
            if not isinstance(boundary_key, str) or not boundary_key.strip():
                raise CompanionContractError(
                    "boundary observation requires boundary_key"
                )
            if boundary_key != observation.source_ref:
                raise CompanionContractError(
                    "boundary source_ref must match boundary_key"
                )
            if "value" not in observation.payload:
                raise CompanionContractError("boundary observation requires value")
            fact_key = f"boundary:{boundary_key}"
            return (
                CompanionEvidence(
                    evidence_id=str(
                        uuid5(
                            NAMESPACE_URL,
                            f"observation-evidence:{observation_id}:0",
                        )
                    ),
                    pet_id=observation.subject.pet_id,
                    memory_subject_id=observation.subject.memory_subject_id,
                    ownership_scope="user",
                    relationship_epoch_id=None,
                    kind="boundary",
                    content={**dict(observation.payload), "fact_key": fact_key},
                    source_kind=observation.source_kind,
                    source_ref=observation.source_ref,
                    source_summary=observation.safe_summary,
                    attribution="explicit_user_input",
                    confidence=1.0,
                    occurred_at=observation.occurred_at,
                    retention="long_term",
                    status="active",
                    prompt_eligible=True,
                    fact_key=fact_key,
                    importance=1.0,
                    sensitivity="private",
                    valid_from=observation.occurred_at,
                    valid_until=None,
                ),
            )
        if observation.kind == "companion_feedback":
            feedback_id = observation.payload.get("feedback_id")
            signal = observation.payload.get("signal")
            interaction_ref = observation.payload.get("interaction_ref")
            if not isinstance(feedback_id, str) or not feedback_id.strip():
                raise CompanionContractError("companion feedback requires feedback_id")
            if feedback_id != observation.source_ref:
                raise CompanionContractError(
                    "companion feedback source_ref must match feedback_id"
                )
            if signal not in {
                "helpful",
                "not_helpful",
                "too_proactive",
                "too_personal",
            }:
                raise CompanionContractError("companion feedback signal is invalid")
            if not isinstance(interaction_ref, str) or not interaction_ref.strip():
                raise CompanionContractError(
                    "companion feedback requires interaction_ref"
                )
            fact_key = f"companion_feedback:{feedback_id}"
            helpful = signal == "helpful"
            return (
                CompanionEvidence(
                    evidence_id=str(
                        uuid5(
                            NAMESPACE_URL,
                            f"observation-evidence:{observation_id}:0",
                        )
                    ),
                    pet_id=observation.subject.pet_id,
                    memory_subject_id=observation.subject.memory_subject_id,
                    ownership_scope="relationship",
                    relationship_epoch_id="pending-relationship-epoch",
                    kind="accepted_help" if helpful else "interaction_feedback",
                    content={
                        **dict(observation.payload),
                        "outcome": "accepted" if helpful else signal,
                        "fact_key": fact_key,
                    },
                    source_kind=observation.source_kind,
                    source_ref=observation.source_ref,
                    source_summary=observation.safe_summary,
                    attribution="explicit_user_feedback",
                    confidence=1.0,
                    occurred_at=observation.occurred_at,
                    retention="long_term",
                    status="active",
                    prompt_eligible=True,
                    fact_key=fact_key,
                    importance=0.9,
                    sensitivity="private",
                    valid_from=observation.occurred_at,
                    valid_until=None,
                ),
            )
        if observation.kind in {
            "course_created",
            "course_updated",
            "course_deleted",
        }:
            course_id = observation.payload.get("course_id")
            title = observation.payload.get("title")
            weekday = observation.payload.get("weekday")
            start_section = observation.payload.get("start_section")
            end_section = observation.payload.get("end_section")
            if not isinstance(course_id, str) or not course_id.strip():
                raise CompanionContractError("course observation requires course_id")
            if course_id != observation.source_ref:
                raise CompanionContractError(
                    "course observation source_ref must match course_id"
                )
            if not isinstance(title, str) or not title.strip():
                raise CompanionContractError("course observation requires title")
            if type(weekday) is not int or not 1 <= weekday <= 7:
                raise CompanionContractError("course observation weekday must be 1-7")
            if (
                type(start_section) is not int
                or type(end_section) is not int
                or start_section < 1
                or end_section < start_section
            ):
                raise CompanionContractError("course observation sections are invalid")
            deleted = observation.kind == "course_deleted"
            fact_key = f"course:{course_id}"
            evidence_id = str(
                uuid5(NAMESPACE_URL, f"observation-evidence:{observation_id}:0")
            )
            return (
                CompanionEvidence(
                    evidence_id=evidence_id,
                    pet_id=observation.subject.pet_id,
                    memory_subject_id=observation.subject.memory_subject_id,
                    ownership_scope="user",
                    relationship_epoch_id=None,
                    kind=("future_event_cancelled" if deleted else "future_event"),
                    content={**dict(observation.payload), "fact_key": fact_key},
                    source_kind=observation.source_kind,
                    source_ref=observation.source_ref,
                    source_summary=observation.safe_summary,
                    attribution="observed_business_event",
                    confidence=1.0,
                    occurred_at=observation.occurred_at,
                    retention=("short_term" if deleted else "until_resolved"),
                    status="active",
                    prompt_eligible=not deleted,
                    fact_key=fact_key,
                    importance=0.65,
                    sensitivity="private",
                    valid_from=observation.occurred_at,
                    valid_until=None,
                ),
            )
        if observation.kind not in {
            "todo_created",
            "todo_updated",
            "todo_completed",
            "todo_deleted",
        }:
            raise CompanionContractError(
                f"unsupported observation kind: {observation.kind}"
            )
        todo_id = observation.payload.get("todo_id")
        title = observation.payload.get("title")
        due_at = observation.payload.get("due_at")
        status = observation.payload.get("status")
        if not isinstance(todo_id, str) or not todo_id.strip():
            raise CompanionContractError("todo observation requires todo_id")
        if todo_id != observation.source_ref:
            raise CompanionContractError(
                "todo observation source_ref must match todo_id"
            )
        if not isinstance(title, str) or not title.strip():
            raise CompanionContractError("todo observation requires title")
        if not isinstance(due_at, str) or not due_at.strip():
            raise CompanionContractError("todo observation requires due_at")
        expected_status = {
            "todo_completed": "done",
            "todo_deleted": "deleted",
        }.get(observation.kind, "pending")
        if status != expected_status:
            raise CompanionContractError(
                f"{observation.kind} observation status must be {expected_status}"
            )
        if (
            observation.kind == "todo_completed"
            and observation.payload.get("completion_source") != "explicit_user_action"
        ):
            raise CompanionContractError(
                "todo completion requires explicit user action"
            )
        if (
            observation.kind == "todo_deleted"
            and observation.payload.get("previous_status") == "done"
        ):
            return ()
        fact_key = f"todo:{todo_id}"
        evidence_id = str(
            uuid5(NAMESPACE_URL, f"observation-evidence:{observation_id}:0")
        )
        completed = observation.kind == "todo_completed"
        deleted = observation.kind == "todo_deleted"
        return (
            CompanionEvidence(
                evidence_id=evidence_id,
                pet_id=observation.subject.pet_id,
                memory_subject_id=observation.subject.memory_subject_id,
                ownership_scope="relationship" if completed else "user",
                relationship_epoch_id=(
                    "pending-relationship-epoch" if completed else None
                ),
                kind=(
                    "followup_completed"
                    if completed
                    else "future_event_cancelled" if deleted else "future_event"
                ),
                content={
                    **dict(observation.payload),
                    "fact_key": fact_key,
                },
                source_kind=observation.source_kind,
                source_ref=observation.source_ref,
                source_summary=observation.safe_summary,
                attribution="observed_business_event",
                confidence=1.0,
                occurred_at=observation.occurred_at,
                retention=(
                    "long_term"
                    if completed
                    else "short_term" if deleted else "until_resolved"
                ),
                status="active",
                prompt_eligible=not deleted,
                fact_key=fact_key,
                importance=0.7,
                sensitivity="private",
                valid_from=observation.occurred_at,
                valid_until=None if completed or deleted else due_at,
            ),
        )

    @staticmethod
    def _pending_evidence(
        prepared: PreparedCompanionTurn,
        outcome: CompanionTurnOutcome,
    ) -> tuple[PendingCompanionEvidence, ...]:
        pending: list[PendingCompanionEvidence] = []
        for index, signal in enumerate(outcome.feedback_signals):
            if not isinstance(signal, Mapping):
                raise CompanionContractError("feedback signal must be a mapping")
            ownership_scope = signal.get("ownership_scope")
            kind = signal.get("kind")
            content = signal.get("content")
            source_summary = signal.get("source_summary")
            attribution = signal.get("attribution")
            confidence = signal.get("confidence")
            retention = signal.get("retention")
            prompt_eligible = signal.get("prompt_eligible")
            expires_at = signal.get("expires_at")
            if ownership_scope not in {"user", "relationship"}:
                raise CompanionContractError(
                    "feedback signal ownership_scope is invalid"
                )
            if not isinstance(kind, str) or not kind.strip():
                raise CompanionContractError("feedback signal kind is required")
            if not isinstance(content, Mapping):
                raise CompanionContractError(
                    "feedback signal content must be a mapping"
                )
            if not isinstance(source_summary, str) or not source_summary.strip():
                raise CompanionContractError(
                    "feedback signal source_summary is required"
                )
            if not isinstance(attribution, str) or not attribution.strip():
                raise CompanionContractError("feedback signal attribution is required")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise CompanionContractError("feedback signal confidence is invalid")
            if not isinstance(retention, str) or not retention.strip():
                raise CompanionContractError("feedback signal retention is required")
            if not isinstance(prompt_eligible, bool):
                raise CompanionContractError(
                    "feedback signal prompt_eligible must be boolean"
                )
            try:
                signal_json = json.dumps(
                    dict(signal),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError) as exc:
                raise CompanionContractError(
                    "feedback signal must be JSON serializable"
                ) from exc
            evidence_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"turn-evidence:{prepared.pet_id}:{prepared.turn_id}:"
                    f"{index}:{hashlib.sha256(signal_json.encode('utf-8')).hexdigest()}",
                )
            )
            CompanionEvidence(
                evidence_id=evidence_id,
                pet_id=prepared.pet_id,
                memory_subject_id=prepared.memory_subject_id,
                ownership_scope=ownership_scope,
                relationship_epoch_id=(
                    "pending-relationship-epoch"
                    if ownership_scope == "relationship"
                    else None
                ),
                kind=kind,
                content=dict(content),
                source_kind="turn",
                source_ref=prepared.turn_id,
                source_summary=source_summary,
                attribution=attribution,
                confidence=float(confidence),
                occurred_at=prepared.occurred_at,
                retention=retention,
                status="active",
                prompt_eligible=prompt_eligible,
                expires_at=expires_at if isinstance(expires_at, str) else None,
            )
            pending.append(
                PendingCompanionEvidence(
                    evidence_id=evidence_id,
                    ownership_scope=ownership_scope,
                    kind=kind,
                    content=dict(content),
                    source_summary=source_summary,
                    attribution=attribution,
                    confidence=float(confidence),
                    retention=retention,
                    prompt_eligible=prompt_eligible,
                    expires_at=expires_at if isinstance(expires_at, str) else None,
                )
            )
        if (
            prepared.source_text is not None
            and prepared.interaction_kind == "conversation"
        ):
            continuity_text = prepared.source_text[:500]
            source_summary = f"用户此前说：{continuity_text}"
            expires_at = (
                datetime.fromisoformat(prepared.occurred_at) + timedelta(days=7)
            ).isoformat()
            evidence_id = str(
                uuid5(
                    NAMESPACE_URL,
                    "xiaoxin:recent-conversation:"
                    f"{prepared.pet_id}:{prepared.memory_subject_id}:{prepared.turn_id}",
                )
            )
            pending.append(
                PendingCompanionEvidence(
                    evidence_id=evidence_id,
                    ownership_scope="user",
                    kind="recent_conversation",
                    content={
                        "canonical_value": source_summary,
                        "turn_id": prepared.turn_id,
                    },
                    source_summary=source_summary,
                    attribution="explicit_user_statement",
                    confidence=1.0,
                    retention="short_term",
                    prompt_eligible=True,
                    expires_at=expires_at,
                )
            )
        return tuple(pending)

    def apply_control(self, command: CompanionControlCommand) -> CompanionControlResult:
        if self._controls is None:
            raise CompanionUnavailableError("CompanionStore is not configured")
        if command.action in _MEMORY_EXPANDING_CONTROL_ACTIONS and not self._allows(
            command.subject.owner_user_id,
            "COMPANION_MEMORY_WRITE",
        ):
            raise PermissionError("companion memory write is not allowed")
        result = self._controls.apply(command)
        now = command.payload.get("now")
        if (
            command.action != "purge_personal_memory"
            and isinstance(now, str)
            and command.subject.speaker_identity == "confirmed"
            and command.subject.persistence_allowed
            and self._allows(
                command.subject.owner_user_id,
                "COMPANION_MEMORY_READ",
            )
        ):
            self._record_current_relationship_stage(
                owner_user_id=command.subject.owner_user_id,
                pet_id=command.subject.pet_id,
                memory_subject_id=command.subject.memory_subject_id,
                academic_stage=command.subject.academic_stage,
                now=now,
            )
        return result

    def _record_current_relationship_stage(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        academic_stage: str,
        now: str,
    ) -> None:
        try:
            if self._store is None:
                return
            epoch = self._store.get_active_epoch(
                owner_user_id=owner_user_id,
                pet_id=pet_id,
            )
            if epoch is None:
                return
            material = self._store.load_policy_material(
                owner_user_id=owner_user_id,
                pet_id=pet_id,
                memory_subject_id=memory_subject_id,
                relationship_epoch_id=epoch.epoch_id,
                now=now,
                surface="voice",
                interaction_kind="conversation",
            )
            inputs = policy_inputs_from_evidence(
                speaker_identity="confirmed",
                surface="voice",
                academic_stage=academic_stage,
                interaction_kind="conversation",
                turn_count=material.turn_count,
                distinct_interaction_days=material.distinct_interaction_days,
                evidence=material.evidence,
                active_adjustments=material.active_adjustments,
                relationship_started_at=material.relationship_started_at,
                interaction_dates=material.interaction_dates,
                historical_stage=material.historical_stage,
                relationship_stage_history=material.relationship_stage_history,
                now=now,
            )
            policy = build_companion_policy(inputs)
            self._record_relationship_stage_event_safely(
                owner_user_id=owner_user_id,
                pet_id=pet_id,
                memory_subject_id=memory_subject_id,
                relationship_epoch_id=epoch.epoch_id,
                relationship_stage=policy.relationship_stage,
                quality=relationship_quality_snapshot(inputs.relationship),
                reason_codes=relationship_stage_reason_codes(inputs.relationship),
                policy_version=policy.version,
                now=now,
            )
        except Exception as exc:
            LOGGER.warning(
                "Companion relationship stage refresh failed",
                extra={
                    "companion_pet_id": pet_id,
                    "companion_memory_subject_id": memory_subject_id,
                    "companion_error_type": type(exc).__name__,
                },
            )

    def _record_relationship_stage_event_safely(
        self,
        *,
        owner_user_id: str,
        pet_id: str,
        memory_subject_id: str,
        relationship_epoch_id: str,
        relationship_stage: str,
        quality: Mapping[str, int],
        reason_codes: tuple[str, ...],
        policy_version: str,
        now: str,
    ) -> None:
        if self._store is None:
            return
        try:
            self._store.record_relationship_stage_event(
                owner_user_id=owner_user_id,
                pet_id=pet_id,
                memory_subject_id=memory_subject_id,
                relationship_epoch_id=relationship_epoch_id,
                relationship_stage=relationship_stage,
                quality=quality,
                reason_codes=reason_codes,
                policy_version=policy_version,
                now=now,
            )
        except Exception as exc:
            LOGGER.warning(
                "Companion relationship stage audit failed",
                extra={
                    "companion_pet_id": pet_id,
                    "companion_memory_subject_id": memory_subject_id,
                    "companion_relationship_epoch_id": relationship_epoch_id,
                    "companion_error_type": type(exc).__name__,
                },
            )

    def project(self, request: CompanionProjectionRequest) -> CompanionProjection:
        if (
            request.subject.speaker_identity != "confirmed"
            or not self._allows(
                request.subject.owner_user_id,
                "COMPANION_MEMORY_READ",
            )
        ):
            policy = build_companion_policy(
                CompanionPolicyInputs(
                    speaker_identity=request.subject.speaker_identity,
                    surface=request.surface,
                    academic_stage=request.subject.academic_stage,
                    interaction_kind="conversation",
                    device_state=request.device_state,
                )
            )
            return CompanionProjection(
                surface=request.surface,
                xiaoxin_age=policy.xiaoxin_age,
                relationship_stage=policy.relationship_stage,
                payload=(
                    {"hardware_expression": dict(policy.hardware_expression)}
                    if request.surface == "hardware"
                    else (
                        _safe_miniprogram_payload(
                            policy=policy,
                            projection_state={},
                            growth_moment=None,
                            boundaries=(),
                        )
                        if request.surface == "miniprogram"
                        else {"policy": asdict(policy)}
                    )
                ),
            )
        if self._store is None:
            raise CompanionUnavailableError("CompanionStore is not configured")
        academic_state = self._store.get_academic_state(
            owner_user_id=request.subject.owner_user_id,
            pet_id=request.subject.pet_id,
            memory_subject_id=request.subject.memory_subject_id,
        )
        academic_stage = (
            str(academic_state["academic_stage"])
            if academic_state is not None
            else request.subject.academic_stage
        )
        epoch = self._store.get_active_epoch(
            owner_user_id=request.subject.owner_user_id,
            pet_id=request.subject.pet_id,
        )
        if epoch is not None:
            self._store.ensure_anniversary_boundaries(
                owner_user_id=request.subject.owner_user_id,
                pet_id=request.subject.pet_id,
                memory_subject_id=request.subject.memory_subject_id,
                relationship_epoch_id=epoch.epoch_id,
                academic_stage=academic_stage,
                now=request.now,
            )
        birth_temperament = (
            self._store.get_birth_temperament(
                owner_user_id=request.subject.owner_user_id,
                pet_id=request.subject.pet_id,
            )
            if epoch is not None
            else None
        )
        evidence: tuple[CompanionEvidence, ...] = ()
        inputs = CompanionPolicyInputs(
            speaker_identity="confirmed",
            surface=request.surface,
            academic_stage=academic_stage,
            interaction_kind="conversation",
            birth_temperament=birth_temperament,
        )
        if epoch is not None:
            material = self._store.load_policy_material(
                owner_user_id=request.subject.owner_user_id,
                pet_id=request.subject.pet_id,
                memory_subject_id=request.subject.memory_subject_id,
                relationship_epoch_id=epoch.epoch_id,
                now=request.now,
                surface=request.surface,
                interaction_kind="conversation",
            )
            evidence = material.evidence
            inputs = policy_inputs_from_evidence(
                speaker_identity="confirmed",
                surface=request.surface,
                academic_stage=academic_stage,
                interaction_kind="conversation",
                turn_count=material.turn_count,
                distinct_interaction_days=material.distinct_interaction_days,
                evidence=evidence,
                active_adjustments=material.active_adjustments,
                birth_temperament=birth_temperament,
                relationship_started_at=material.relationship_started_at,
                interaction_dates=material.interaction_dates,
                historical_stage=material.historical_stage,
                relationship_stage_history=material.relationship_stage_history,
                now=request.now,
            )
        policy = build_companion_policy(
            replace(inputs, device_state=request.device_state)
        )
        if epoch is not None:
            policy = _apply_va_projection(
                policy,
                self._load_va_projection_safely(
                    subject=request.subject,
                    relationship_epoch_id=epoch.epoch_id,
                    now=request.now,
                    policy=policy,
                ),
                surface=request.surface,
                device_state=request.device_state,
            )
        growth_moment = (
            self._store.load_growth_moment(
                owner_user_id=request.subject.owner_user_id,
                pet_id=request.subject.pet_id,
                memory_subject_id=request.subject.memory_subject_id,
                relationship_epoch_id=epoch.epoch_id,
                academic_stage=academic_stage,
                now=request.now,
            )
            if epoch is not None
            else None
        )
        projection_state: Mapping[str, object] = {
            "active_adjustments": (),
            "interaction_contracts": (),
            "chapters": (),
            "jobs": (),
            "pending_memory_candidates": (),
            "growth_moments_enabled": True,
        }
        if epoch is not None and request.surface in {"miniprogram", "operator"}:
            projection_state = self._store.load_projection_state(
                owner_user_id=request.subject.owner_user_id,
                pet_id=request.subject.pet_id,
                memory_subject_id=request.subject.memory_subject_id,
                relationship_epoch_id=epoch.epoch_id,
                now=request.now,
            )
        safe_evidence = tuple(
            {
                "evidence_id": item.evidence_id,
                "kind": item.kind,
                "source_summary": item.source_summary,
                "status": item.status,
            }
            for item in evidence
            if not (
                item.kind == "system_event"
                and item.source_ref == "identity:student_profile"
            )
        )
        if request.surface == "hardware":
            payload = {
                "hardware_expression": dict(policy.hardware_expression),
                **(
                    {"growth_moment": growth_moment}
                    if growth_moment is not None
                    else {}
                ),
            }
        elif request.surface == "initiative":
            if request.initiative_decision_id is not None:
                claimed = self._store.claim_initiative_delivery(
                    owner_user_id=request.subject.owner_user_id,
                    pet_id=request.subject.pet_id,
                    memory_subject_id=request.subject.memory_subject_id,
                    decision_id=request.initiative_decision_id,
                    now=request.now,
                )
                payload = claimed or {
                    "eligible": False,
                    "reason_code": "stale_decision",
                }
            elif (
                not request.initiative_enabled or policy.initiative_level == "disabled"
            ):
                payload = {
                    "eligible": False,
                    "reason_code": "disabled",
                }
            elif request.quiet_hours_active:
                payload = {
                    "eligible": False,
                    "reason_code": "quiet_hours",
                }
            elif not request.device_available:
                payload = {
                    "eligible": False,
                    "reason_code": "device_unavailable",
                }
            elif request.higher_priority_pending:
                payload = {
                    "eligible": False,
                    "reason_code": "higher_priority_notification",
                }
            elif epoch is None:
                payload = {"eligible": False, "reason_code": "no_relationship"}
            else:
                eligible = tuple(
                    item
                    for item in evidence
                    if item.prompt_eligible
                    and item.kind
                    in {
                        "accepted_help",
                        "followup_completed",
                        "interaction_feedback",
                        "meaningful_moment",
                    }
                )
                selected = eligible[-1:] if eligible else ()
                payload = self._store.decide_initiative(
                    pet_id=request.subject.pet_id,
                    memory_subject_id=request.subject.memory_subject_id,
                    relationship_epoch_id=epoch.epoch_id,
                    now=request.now,
                    evidence_ids=tuple(item.evidence_id for item in selected),
                    content_brief=(selected[0].source_summary if selected else ""),
                    hardware_expression=policy.hardware_expression,
                )
        elif request.surface == "miniprogram":
            payload = _safe_miniprogram_payload(
                policy=policy,
                projection_state=projection_state,
                growth_moment=growth_moment,
                boundaries=tuple(
                    item
                    for item in safe_evidence
                    if item["kind"] in {"explicit_boundary", "boundary"}
                ),
            )
        elif request.surface == "operator":
            diagnostics: Mapping[str, object] = {
                "evidence_timeline": (),
                "observations": (),
                "retrieval_audits": (),
                "semantic_memory_evaluations": (),
                "relationship_stage_events": (),
                "connection_need": None,
                "initiative_opportunities": (),
                "pending_observations": (),
                "epochs": (),
                "relations": (),
                "capsules": (),
                "adjustments": (),
                "health": {
                    "evidence_by_status": {},
                    "jobs_by_status": {},
                    "observations": 0,
                    "temporary_turn_sources": 0,
                    "temporary_context_messages": 0,
                    "temporary_context_pins": 0,
                    "semantic_memory_evaluations": 0,
                    "retrieval_audits": 0,
                    "relationship_stage_events": 0,
                    "initiative_opportunities_by_status": {},
                    "pending_observations": 0,
                    "pending_observations_by_status": {},
                },
            }
            if epoch is not None:
                diagnostics = self._store.load_operator_diagnostics(
                    owner_user_id=request.subject.owner_user_id,
                    pet_id=request.subject.pet_id,
                    memory_subject_id=request.subject.memory_subject_id,
                    relationship_epoch_id=epoch.epoch_id,
                    now=request.now,
                )
            else:
                pending_diagnostics = self._store.load_pending_observation_diagnostics(
                    owner_user_id=request.subject.owner_user_id,
                    pet_id=request.subject.pet_id,
                    now=request.now,
                )
                diagnostics = {
                    **diagnostics,
                    "pending_observations": pending_diagnostics["pending_observations"],
                    "health": {
                        **diagnostics["health"],
                        "pending_observations": pending_diagnostics[
                            "pending_observations_by_status"
                        ].get("pending", 0),
                        "pending_observations_by_status": pending_diagnostics[
                            "pending_observations_by_status"
                        ],
                    },
                }
            diagnostics = {
                **diagnostics,
                "relationship_stage_progress": relationship_stage_progress(
                    inputs.relationship
                ),
                "lineage": {
                    "capsules": diagnostics["capsules"],
                    "adjustments": diagnostics["adjustments"],
                    "chapters": projection_state["chapters"],
                    "relations": diagnostics["relations"],
                },
            }
            payload = {
                "policy": asdict(policy),
                **(
                    {"growth_moment": growth_moment}
                    if growth_moment is not None
                    else {}
                ),
                "evidence": safe_evidence,
                "active_adjustments": projection_state["active_adjustments"],
                "chapters": projection_state["chapters"],
                "jobs": projection_state["jobs"],
                "pet_id": request.subject.pet_id,
                "memory_subject_id": request.subject.memory_subject_id,
                "relationship_epoch_id": (
                    epoch.epoch_id if epoch is not None else None
                ),
                "diagnostics": diagnostics,
            }
        else:
            payload = {
                "policy": asdict(policy),
                **(
                    {"growth_moment": growth_moment}
                    if growth_moment is not None
                    else {}
                ),
            }
        return CompanionProjection(
            surface=request.surface,
            xiaoxin_age=policy.xiaoxin_age,
            relationship_stage=policy.relationship_stage,
            payload=payload,
        )

    def _admin_subject_counts(
        self,
        memory_subject_ids: tuple[str, ...],
    ) -> Mapping[str, Mapping[str, object]]:
        if self._store is None:
            raise CompanionUnavailableError("CompanionStore is not configured")
        return self._store.load_admin_subject_counts(memory_subject_ids)

    async def run_due_work(
        self,
        *,
        now: str,
        limit: int = 20,
    ) -> CompanionWorkResult:
        if self._worker is None and self._initiative_scheduler is None:
            raise CompanionUnavailableError(
                "background companion work is not configured"
            )

        async def run_reflection() -> CompanionWorkResult:
            if self._worker is None:
                return CompanionWorkResult()
            return await asyncio.to_thread(
                self._worker.run_due_work,
                now=now,
                limit=limit,
            )

        async def run_initiatives() -> CompanionWorkResult:
            if self._initiative_scheduler is None:
                return CompanionWorkResult()
            return await self._initiative_scheduler.run_due_work(
                now=now,
                limit=limit,
            )

        reflection_result, initiative_result = await asyncio.gather(
            run_reflection(),
            run_initiatives(),
        )
        return CompanionWorkResult(
            claimed=reflection_result.claimed + initiative_result.claimed,
            succeeded=reflection_result.succeeded + initiative_result.succeeded,
            retried=reflection_result.retried + initiative_result.retried,
            failed=reflection_result.failed + initiative_result.failed,
        )

    async def run_due_memory_work(
        self,
        *,
        now: str,
        pet_id: str,
        limit: int = 20,
    ) -> CompanionWorkResult:
        if self._worker is None:
            raise CompanionUnavailableError(
                "background companion memory work is not configured"
            )
        return await asyncio.to_thread(
            self._worker.run_due_work,
            now=now,
            limit=limit,
            pet_id=pet_id,
        )


def _required_aware_datetime(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CompanionContractError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CompanionContractError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CompanionContractError(f"{field} must include timezone")
    return value


def _optional_aware_datetime(value: object, *, field: str) -> str | None:
    if value is None or value == "":
        return None
    return _required_aware_datetime(value, field=field)
