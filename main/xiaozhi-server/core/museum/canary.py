from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable
import uuid

from core.conversation_runtime import TurnRequest


@dataclass(frozen=True)
class CanaryCase:
    id: str
    question: str
    knowledge_status: str
    exhibit_id: str
    fact_ids: tuple[str, ...]


DEFAULT_CANARY_CASES = (
    CanaryCase(
        id="jade-trident-material",
        question="玉三叉形器是什么材质？",
        knowledge_status="grounded",
        exhibit_id="liangzhu-jade-trident",
        fact_ids=("fact-liangzhu-trident-material",),
    ),
    CanaryCase(
        id="bagua-lid-dimensions",
        question="南宋官窑青瓷八卦熏炉盖有多高？",
        knowledge_status="grounded",
        exhibit_id="southern-song-guan-bagua-incense-lid",
        fact_ids=("fact-west-lake-bagua-dimensions",),
    ),
    CanaryCase(
        id="butterfly-fabric-era",
        question="清玄色地团花蝴蝶纹袍料是什么年代的？",
        knowledge_status="grounded",
        exhibit_id="qing-butterfly-medallion-robe-fabric",
        fact_ids=("fact-china-silk-butterfly-era",),
    ),
    CanaryCase(
        id="jade-yue-price-rejection",
        question="玉钺组合现在市场价多少钱？",
        knowledge_status="unsupported",
        exhibit_id="liangzhu-jade-yue-set",
        fact_ids=(),
    ),
)


def run_canary(
    runtime,
    *,
    llm: Any | None,
    cases: Iterable[CanaryCase] = DEFAULT_CANARY_CASES,
    run_id: str | None = None,
    maximum_duration_ms: int = 3000,
) -> dict[str, Any]:
    stable_run_id = run_id or uuid.uuid4().hex
    results: list[dict[str, Any]] = []
    for case in cases:
        request_id = f"museum-canary-{stable_run_id}-{case.id}"
        outcome = runtime.handle_turn(
            TurnRequest(
                request_id=request_id,
                transport_session_id=f"transport-{stable_run_id}-{case.id}",
                visitor_session_id=None,
                device_id=f"museum-canary-{stable_run_id}-{case.id}",
                user_text=case.question,
                history=(),
                occurred_at=datetime.now().astimezone(),
                llm=llm,
                metadata={"client": "museum_canary", "canary_id": case.id},
            )
        )
        context = outcome.display_state.get("context", {})
        actual_fact_ids = tuple(outcome.fact_ids)
        duration_ms = int(outcome.audit_record.get("duration_ms", 0))
        failures: list[str] = []
        if outcome.knowledge_status != case.knowledge_status:
            failures.append(
                f"knowledge_status expected={case.knowledge_status} "
                f"actual={outcome.knowledge_status}"
            )
        if context.get("exhibit_id", "") != case.exhibit_id:
            failures.append(
                f"exhibit_id expected={case.exhibit_id} "
                f"actual={context.get('exhibit_id', '')}"
            )
        if actual_fact_ids != case.fact_ids:
            failures.append(
                f"fact_ids expected={list(case.fact_ids)} "
                f"actual={list(actual_fact_ids)}"
            )
        if duration_ms > maximum_duration_ms:
            failures.append(
                f"duration_ms expected<={maximum_duration_ms} actual={duration_ms}"
            )
        persisted = runtime.get_interaction_trace_by_request_id(request_id)
        if persisted is None:
            failures.append("interaction_trace missing")
        results.append(
            {
                "id": case.id,
                "question": case.question,
                "request_id": request_id,
                "knowledge_status": outcome.knowledge_status,
                "exhibit_id": context.get("exhibit_id", ""),
                "fact_ids": list(actual_fact_ids),
                "source_ids": list(outcome.source_ids),
                "guard_result": outcome.audit_record.get("guard_result", ""),
                "duration_ms": duration_ms,
                "stage_latency": outcome.audit_record.get("stage_latency", {}),
                "passed": not failures,
                "failures": failures,
            }
        )
    return {
        "run_id": stable_run_id,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "maximum_duration_ms": maximum_duration_ms,
        "case_count": len(results),
        "failed_case_count": sum(not result["passed"] for result in results),
        "passed": all(result["passed"] for result in results),
        "cases": results,
    }
