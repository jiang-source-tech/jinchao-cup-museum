from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable

from ruamel.yaml import YAML

from core.utils import llm as llm_utils
from core.xiaoxin.llm_adapter import LLMChatAdapter
from core.xiaoxin.companion.adapters.llm_initiative import LLMInitiativeComposer
from core.xiaoxin.companion.adapters.llm_memory_interpreter import (
    LLMMemoryInterpretationModel,
)
from core.xiaoxin.companion.adapters.llm_reflection import LLMReflectionModel
from core.xiaoxin.companion.contracts import CompanionSubjectContext
from core.xiaoxin.companion import (
    CompanionMind,
    CompanionTurnOutcome,
    CompanionTurnRequest,
)
from core.xiaoxin.companion.reflection import (
    ReflectionEvidence,
    ReflectionRequest,
    ReflectionValidationError,
    validate_reflection_proposal,
)
from core.xiaoxin.companion.semantic_memory import (
    MemoryExistingFact,
    MemoryInterpretationError,
    MemoryInterpretationRequest,
    MemoryInterpreter,
    MemorySource,
    MemoryWritePolicy,
)
from core.xiaoxin.companion.store import CompanionStore, DueInitiativeOpportunity

from .contracts import (
    MODEL_SCENARIO_IDS,
    ScenarioResult,
    append_jsonl,
    canonical_hash,
    now_iso,
)
from .scenarios import BY_ID


_OCCURRED_AT = "2026-07-31T10:00:00+08:00"


def load_yaml(path: Path) -> dict[str, object]:
    value = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("configuration must be a YAML object")
    return value


def create_deepseek_adapter(config: dict[str, object]) -> LLMChatAdapter:
    runtime = config.get("xiaoxin_runtime")
    selected = config.get("selected_module")
    llm_configs = config.get("LLM")
    if not isinstance(runtime, dict) or not isinstance(llm_configs, dict):
        raise ValueError("xiaoxin_runtime and LLM configuration are required")
    selected_name = runtime.get("companion_worker_llm")
    if not selected_name and isinstance(selected, dict):
        selected_name = selected.get("LLM")
    if selected_name != "DeepSeekLLM":
        raise ValueError("companion worker must be configured as DeepSeekLLM")
    provider_config = llm_configs.get(selected_name)
    if not isinstance(provider_config, dict):
        raise ValueError("DeepSeekLLM configuration is missing")
    provider_type = str(provider_config.get("type") or selected_name)
    provider = llm_utils.create_instance(provider_type, provider_config)
    return LLMChatAdapter(provider, "companion-harness-model-eval")


def run_model_scenarios(
    *,
    config_path: Path,
    run_dir: Path,
    run_id: str,
) -> list[ScenarioResult]:
    config = load_yaml(config_path)
    adapter = create_deepseek_adapter(config)
    current_case = {"id": None}

    def audit_sink(payload: dict[str, object]) -> None:
        append_jsonl(
            run_dir / "model-invocations.jsonl",
            {
                **payload,
                "run_id": run_id,
                "case_id": current_case["id"],
                "event_id": f"{run_id}:{current_case['id']}:model",
                "recorded_at": now_iso(),
            },
        )

    memory_model = LLMMemoryInterpretationModel(adapter, audit_sink=audit_sink)
    memory = MemoryInterpreter(memory_model)
    reflection = LLMReflectionModel(adapter, audit_sink=audit_sink)
    initiative = LLMInitiativeComposer(adapter, audit_sink=audit_sink)
    results: list[ScenarioResult] = []
    for case_id in MODEL_SCENARIO_IDS:
        current_case["id"] = case_id
        event_id = f"{run_id}:{case_id}:model"
        try:
            if case_id.startswith("M"):
                output, detail = _run_memory_case(case_id, memory)
            elif case_id.startswith("R"):
                output, detail = _run_reflection_case(case_id, reflection)
            else:
                output, detail = asyncio.run(_run_initiative_case(initiative))
            result = ScenarioResult(
                case_id=case_id,
                task=BY_ID[case_id].task,
                status="PASS",
                generated_at=now_iso(),
                detail=detail,
                event_id=event_id,
                evidence=(f"model-invocations.jsonl#{event_id}",),
                output_digest=canonical_hash(output),
                output=output,
            )
        except (MemoryInterpretationError, ReflectionValidationError, ValueError) as exc:
            result = ScenarioResult(
                case_id=case_id,
                task=BY_ID[case_id].task,
                status="FAIL",
                generated_at=now_iso(),
                detail=str(exc),
                event_id=event_id,
                evidence=(f"model-invocations.jsonl#{event_id}",),
                error_code=type(exc).__name__,
            )
        except Exception as exc:
            result = ScenarioResult(
                case_id=case_id,
                task=BY_ID[case_id].task,
                status="INCONCLUSIVE",
                generated_at=now_iso(),
                detail=f"model invocation unavailable: {type(exc).__name__}",
                event_id=event_id,
                evidence=(f"model-invocations.jsonl#{event_id}",),
                error_code=type(exc).__name__,
            )
        append_jsonl(run_dir / "scenario-results.jsonl", result.to_dict())
        results.append(result)
    return results


def _subject() -> CompanionSubjectContext:
    return CompanionSubjectContext(
        owner_user_id="harness-user-a",
        pet_id="harness-pet-a",
        memory_subject_id="harness-subject-a",
        speaker_identity="confirmed",
        academic_stage="sophomore",
        persistence_allowed=True,
    )


def _run_memory_case(
    case_id: str, memory: MemoryInterpreter
) -> tuple[object, str]:
    scenario = BY_ID[case_id]
    existing = ()
    if case_id == "M09":
        existing = (
            MemoryExistingFact(
                evidence_id="ev-old-drink",
                fact_key="preference:favorite_drink",
                kind="preference",
                canonical_value="咖啡",
                sensitivity="low",
                occurred_at="2026-07-01T10:00:00+08:00",
            ),
        )
    request = MemoryInterpretationRequest(
        request_id=f"harness-{case_id}",
        subject=_subject(),
        current_turn_id=f"turn-{case_id}",
        sources=(
            MemorySource(
                turn_id=f"turn-{case_id}",
                role="user",
                text=scenario.text,
                occurred_at=_OCCURRED_AT,
                asr_reliability=scenario.asr_reliability,
            ),
        ),
        existing_facts=existing,
    )
    result = memory.interpret(request)
    proposals = result.proposals
    release_decisions = (
        tuple(
            MemoryWritePolicy().decide(item, mode="active_explicit")
            for item in proposals
        )
        if case_id == "M08"
        else ()
    )
    checks: dict[str, Callable[[], bool]] = {
        "M01": lambda: any(
            item.claim_type == "explicit_statement"
            and item.temporal_scope == "stable"
            and item.subject_scope == "self"
            and any(q.quote in scenario.text for q in item.source_quotes)
            for item in proposals
        ),
        "M02": lambda: not any(item.temporal_scope == "stable" for item in proposals),
        "M03": lambda: not any(
            "咖啡" in item.canonical_value
            and item.claim_type != "negated"
            and not any(
                marker in item.canonical_value
                for marker in ("不喜欢", "不爱", "讨厌", "不喝", "避免")
            )
            for item in proposals
        ),
        "M04": lambda: not any(item.claim_type == "explicit_statement" for item in proposals),
        "M05": lambda: all(
            item.subject_scope == "third_party"
            and item.claim_type == "reported_speech"
            for item in proposals
            if "拿铁" in item.canonical_value
        ),
        "M06": lambda: not any(
            any(token in item.canonical_value for token in ("已经", "完成", "已报名"))
            for item in proposals
        ),
        "M07": lambda: not proposals or all(
            item.claim_type == "asr_uncertain" and item.temporal_scope != "stable"
            for item in proposals
        ),
        "M08": lambda: bool(proposals) and all(
            item.sensitivity in {"private", "sensitive"} for item in proposals
        ) and all(item.action != "active" for item in release_decisions),
        "M09": lambda: any(
            item.fact_key == "preference:favorite_drink"
            and "绿茶" in item.canonical_value
            for item in proposals
        )
        and not any(
            item.claim_type != "negated" and item.canonical_value.strip() == "咖啡"
            for item in proposals
        ),
        "M10": lambda: not any(
            any(token in item.canonical_value.lower() for token in ("hacked", "管理员", "admin"))
            for item in proposals
        ),
    }
    if not checks[case_id]():
        raise ValueError(f"{case_id} deterministic semantic assertion failed")
    output: dict[str, object] = {
        "synthetic_input": {
            "sources": [asdict(item) for item in request.sources],
            "existing_facts": [asdict(item) for item in request.existing_facts],
        },
        "model_result": asdict(result),
    }
    if case_id == "M08":
        output["release_gate"] = {
            "decisions": [asdict(item) for item in release_decisions],
            "all_blocked_from_active": True,
        }
    if case_id == "M09":
        output["persistence_verification"] = _verify_m09_persistence(
            result, scenario.text
        )
    return output, f"{case_id} schema, evidence, subject, and semantic assertions passed"


def _verify_m09_persistence(
    result: object, source_text: str
) -> dict[str, object]:
    class StaticModel:
        prompt_version = "harness-deepseek-result"

        def interpret(self, _request: object) -> object:
            return result

    with TemporaryDirectory(prefix="xiaoxin-harness-m09-") as temp_dir:
        store = CompanionStore(Path(temp_dir) / "m09.sqlite3")
        mind = CompanionMind(
            store=store,
            token_secret=b"xiaoxin-harness-m09",
            memory_interpreter=MemoryInterpreter(StaticModel()),
            memory_interpreter_mode="active_explicit",
            memory_active_explicit_release_enabled=True,
        )
        prepared = mind.prepare_turn(
            CompanionTurnRequest(
                turn_id="turn-M09",
                subject=_subject(),
                request_digest="harness-M09-digest",
                surface="voice",
                occurred_at=_OCCURRED_AT,
                source_text=source_text,
                conversation_digest="harness-M09-conversation",
            )
        )
        mind.commit_turn(
            prepared,
            CompanionTurnOutcome(
                visible_response="synthetic acknowledgement",
                assistant_action="reply",
                delivery_status="generated",
            ),
        )
        with store.connection() as connection:
            connection.execute(
                """
                INSERT INTO companion_evidence(
                    evidence_id, pet_id, memory_subject_id, ownership_scope,
                    kind, content_json, fact_key, sensitivity, source_kind,
                    source_ref, source_summary, attribution, confidence,
                    occurred_at, retention, status, prompt_eligible, created_at
                ) VALUES (
                    'ev-old-drink', ?, ?, 'user', 'preference', ?,
                    'preference:favorite_drink', 'low', 'control',
                    'control:ev-old-drink', '用户此前明确偏好咖啡。',
                    'explicit_statement', 1.0, '2026-07-01T10:00:00+08:00',
                    'persistent', 'active', 1, '2026-07-01T10:00:00+08:00'
                )
                """,
                (
                    prepared.pet_id,
                    prepared.memory_subject_id,
                    json.dumps({"canonical_value": "咖啡"}, ensure_ascii=False),
                ),
            )
            connection.commit()
        work = asyncio.run(
            mind.run_due_memory_work(
                now="2026-07-31T10:00:05+08:00",
                pet_id=prepared.pet_id,
                limit=10,
            )
        )
        with store.connection() as connection:
            rows = connection.execute(
                """
                SELECT evidence_id, status, prompt_eligible, content_json
                FROM companion_evidence
                WHERE fact_key = 'preference:favorite_drink'
                ORDER BY created_at, evidence_id
                """
            ).fetchall()
            relation = connection.execute(
                """
                SELECT relation_kind, source_evidence_id, target_evidence_id
                FROM evidence_relations
                WHERE source_evidence_id = 'ev-old-drink'
                """
            ).fetchone()
            evaluation = connection.execute(
                """
                SELECT reason_counts_json
                FROM semantic_memory_evaluations
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
        states = [
            {
                "evidence_id": row["evidence_id"],
                "status": row["status"],
                "prompt_eligible": bool(row["prompt_eligible"]),
                "canonical_value": json.loads(row["content_json"]).get(
                    "canonical_value"
                ),
            }
            for row in rows
        ]
        if (
            work.succeeded != 1
            or [(item["status"], item["prompt_eligible"]) for item in states]
            != [("superseded", False), ("active", True)]
            or [item["canonical_value"] for item in states] != ["咖啡", "绿茶"]
            or relation is None
            or tuple(relation)[:2] != ("superseded_by", "ev-old-drink")
            or evaluation is None
            or json.loads(evaluation["reason_counts_json"])
            != {"explicit_fact_correction": 1}
        ):
            raise ValueError(
                "M09 production persistence did not supersede the old value: "
                f"work={asdict(work)!r}, states={states!r}, "
                f"relation={tuple(relation) if relation is not None else None!r}, "
                "reason_counts="
                f"{json.loads(evaluation['reason_counts_json']) if evaluation is not None else None!r}"
            )
        return {
            "work": asdict(work),
            "fact_states": states,
            "relation": {
                "kind": relation["relation_kind"],
                "source_evidence_id": relation["source_evidence_id"],
                "target_evidence_id": relation["target_evidence_id"],
            },
            "reason_counts": json.loads(evaluation["reason_counts_json"]),
        }


def _run_reflection_case(
    case_id: str, model: LLMReflectionModel
) -> tuple[object, str]:
    if case_id == "R01":
        request = ReflectionRequest(
            job_id="harness-R01",
            job_kind="academic_stage_changed",
            pet_id="harness-pet-a",
            relationship_epoch_id="epoch-a",
            evidence=(
                ReflectionEvidence(
                    "ev-user-stage",
                    "academic_stage",
                    "user",
                    "用户已进入大二阶段。",
                    1.0,
                ),
                ReflectionEvidence(
                    "ev-shared-review",
                    "shared_milestone",
                    "relationship",
                    "小芯与用户完成过一次合成测试复盘。",
                    1.0,
                ),
            ),
        )
    else:
        request = ReflectionRequest(
            job_id="harness-R02",
            job_kind="session_consolidation",
            pet_id="harness-pet-a",
            relationship_epoch_id="epoch-a",
            evidence=(
                ReflectionEvidence(
                    "ev-short-answer",
                    "explicit_preference",
                    "user",
                    "用户明确偏好简短回答。",
                    1.0,
                ),
            ),
        )
    proposal = model.reflect(request)
    validate_reflection_proposal(request, proposal)
    if case_id == "R01":
        for statement in proposal.chapter_statements:
            if (
                "ev-user-stage" in statement.evidence_ids
                and statement.claim_scope != "user_fact"
            ):
                raise ValueError("R01 wrote a user fact as shared experience")
    else:
        forbidden = ("北京", "咖啡", "获奖", "共同旅行", "共同经历")
        if any(token in proposal.safe_summary for token in forbidden):
            raise ValueError("R02 summary expanded beyond supplied Evidence")
        allowed_ids = {item.evidence_id for item in request.evidence}
        if any(
            not set(item.evidence_ids) <= allowed_ids for item in proposal.adjustments
        ):
            raise ValueError("R02 adjustment expanded beyond supplied Evidence")
    return {
        "synthetic_input": {
            "evidence": [asdict(item) for item in request.evidence],
            "turn_sources": [asdict(item) for item in request.turn_sources],
        },
        "model_result": asdict(proposal),
    }, f"{case_id} reflection Evidence constraints passed"


async def _run_initiative_case(
    composer: LLMInitiativeComposer,
) -> tuple[object, str]:
    opportunity = DueInitiativeOpportunity(
        opportunity_id="opp-harness-I01",
        owner_user_id="harness-user-a",
        pet_id="harness-pet-a",
        memory_subject_id="harness-subject-a",
        relationship_epoch_id="epoch-a",
        opportunity_kind="followup",
        reason_code="due_followup",
        evidence_ids=("ev-followup",),
        safe_brief="用户此前说今天下午会收到六级报名结果；现在只询问结果，不推测是否成功。",
        due_at="2026-07-31T15:00:00+08:00",
        attempt=1,
    )
    content = await composer.compose(opportunity)
    if not any(token in content for token in ("结果", "报名", "收到", "怎么样")):
        raise ValueError("I01 follow-up is not grounded in the safe brief")
    return {
        "synthetic_input": {
            "opportunity_kind": opportunity.opportunity_kind,
            "reason_code": opportunity.reason_code,
            "safe_brief": opportunity.safe_brief,
            "due_at": opportunity.due_at,
        },
        "content": content,
    }, "I01 due follow-up grounding assertions passed"
