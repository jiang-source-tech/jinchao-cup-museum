from __future__ import annotations

from dataclasses import dataclass
import hashlib
import inspect
import json
from time import perf_counter
from typing import Any

from core.museum.contracts import EvidencePack, EvidenceSnapshot
from core.museum.query_understanding import QuestionUnderstanding


MUSEUM_LLM_PROMPT_VERSION = "museum-grounded-router-v1"
MUSEUM_EVIDENCE_LLM_PROMPT_VERSION = "museum-evidence-router-v1"
_VALID_STATUSES = {
    "grounded",
    "partial",
    "conflicting",
    "unsupported",
    "conversational",
}
_VALID_SOCIAL_INTENTS = {
    "greeting",
    "identity",
    "capability",
    "thanks",
    "farewell",
}


@dataclass(frozen=True)
class MuseumLlmClaim:
    text: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class MuseumLlmDecision:
    status: str
    fact_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    claims: tuple[MuseumLlmClaim, ...] = ()
    social_intent: str = ""
    answer: str = ""


@dataclass(frozen=True)
class MuseumLlmCall:
    decision: MuseumLlmDecision | None
    invoked: bool
    model_name: str = ""
    prompt_version: str = ""
    result: str = "not_called"
    response_summary: str = "{}"
    duration_ms: int = 0

    @classmethod
    def not_called(cls) -> "MuseumLlmCall":
        return cls(decision=None, invoked=False)


def decide_with_museum_llm(
    *,
    exhibit_name: str,
    question: str,
    candidates: EvidenceSnapshot | EvidencePack,
    llm: Any,
    session_id: str,
    history: tuple | list,
    understanding: QuestionUnderstanding,
) -> MuseumLlmCall:
    started = perf_counter()
    system_prompt, user_prompt = build_museum_llm_prompts(
        exhibit_name=exhibit_name,
        question=question,
        candidates=candidates,
        history=history,
        understanding=understanding,
    )
    model_name = str(
        getattr(llm, "model_name", "") or llm.__class__.__name__
    )
    prompt_version = (
        MUSEUM_EVIDENCE_LLM_PROMPT_VERSION
        if isinstance(candidates, EvidencePack)
        else MUSEUM_LLM_PROMPT_VERSION
    )
    try:
        raw_decision = _invoke_json_response(
            llm=llm,
            session_id=session_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
    except Exception as exc:
        return MuseumLlmCall(
            decision=None,
            invoked=True,
            model_name=model_name,
            prompt_version=prompt_version,
            result="request_failed",
            response_summary=json.dumps(
                {
                    "parse_status": "request_failed",
                    "error_type": type(exc).__name__,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            duration_ms=_duration_ms(started),
        )

    decision = parse_museum_llm_decision(
        raw_decision,
        max_fact_ids=5 if understanding.answer_depth == "detailed" else 3,
        max_evidence_ids=8 if understanding.answer_depth == "detailed" else 5,
        require_evidence=isinstance(candidates, EvidencePack),
    )
    result = "parsed" if decision is not None else "invalid_response"
    return MuseumLlmCall(
        decision=decision,
        invoked=True,
        model_name=model_name,
        prompt_version=prompt_version,
        result=result,
        response_summary=summarize_museum_llm_response(
            raw_decision,
            decision=decision,
            parse_status=result,
        ),
        duration_ms=_duration_ms(started),
    )


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def build_museum_llm_prompts(
    *,
    exhibit_name: str,
    question: str,
    candidates: EvidenceSnapshot | EvidencePack,
    history: tuple | list,
    understanding: QuestionUnderstanding,
) -> tuple[str, str]:
    evidence_mode = isinstance(candidates, EvidencePack)
    facts = (
        _format_evidence_pack(candidates)
        if evidence_mode
        else "\n".join(
            f"- {fact.id}: {fact.statement}" for fact in candidates.facts
        )
    )
    recent_history = json.dumps(
        list(history[-4:]) if history else [],
        ensure_ascii=False,
    )
    if evidence_mode:
        grounded_contract = (
            "详细讲解模式下最多选择8个evidence_ids，并为每条事实性声明填写claims及其引用，"
            "answer用中文回答4至8句；"
            if understanding.answer_depth == "detailed"
            else "普通模式最多选择5个evidence_ids，并为每条事实性声明填写claims及其引用，"
            "answer用中文回答1至4句。"
        )
        fields_contract = (
            "JSON字段必须包含status、evidence_ids、claims、fact_ids、social_intent、answer。"
        )
        status_contract = (
            "status只能是grounded、partial、conflicting、unsupported、conversational之一。"
        )
    else:
        grounded_contract = (
            "详细讲解模式下选择覆盖主要方面的事实，最多5个，answer用中文回答4至8句；"
            "普通模式选择最少且不超过3个给定事实ID，answer用中文回答1至4句。"
            if understanding.answer_depth == "detailed"
            else "fact_ids选择最少且不超过3个给定事实ID，answer用中文回答1至4句。"
        )
        fields_contract = "JSON字段必须包含status、fact_ids、social_intent、answer。"
        status_contract = "status只能是grounded、unsupported、conversational之一。"
    evidence_status_rules = (
        "partial表示只有部分问题能由证据回答；conflicting表示证据之间存在冲突，"
        "必须在claims中保留冲突涉及的引用，不得自行选边。"
        if evidence_mode
        else ""
    )
    system_prompt = (
        "你是博物馆语音对话的受限事实路由器。"
        "你只能依据本次输入中的当前展品事实，不能使用外部常识或自行推测。"
        "只输出一个JSON对象，不要输出Markdown、代码围栏或解释。"
        + fields_contract
        + status_contract
        + "grounded表示一个或多个给定事实可以直接回答问题；"
        + grounded_contract
        +
        "如果游客要求一句话、简短说明或讲给小朋友听，必须遵守该表达要求，"
        "不得增加事实之外的数字、人物、地点、年代、因果、用途或传说。"
        + evidence_status_rules
        + "unsupported表示给定证据不能直接回答，fact_ids和evidence_ids必须是空数组，"
        "claims必须是空数组，"
        "social_intent和answer必须是空字符串。"
        "conversational只允许问候、身份、能力、感谢或告别；"
        "social_intent只能是greeting、identity、capability、thanks、farewell之一，"
        "fact_ids和evidence_ids必须是空数组，claims必须是空数组，answer必须是空字符串。"
    )
    user_prompt = (
        f"提示版本：{MUSEUM_EVIDENCE_LLM_PROMPT_VERSION if evidence_mode else MUSEUM_LLM_PROMPT_VERSION}\n"
        f"当前展品：{exhibit_name}\n"
        f"问题粗分类：{understanding.coarse_intent}\n"
        f"问题细分类：{understanding.fine_intent}\n"
        f"最近对话：{recent_history}\n"
        f"游客本轮输入：{question}\n"
        "回答表达：优先遵守游客对篇幅、受众和通俗程度的明确要求。\n"
        + (
            "当前检索到的证据片段：\n"
            if evidence_mode
            else "当前发布且已审核的事实：\n"
        )
        + (facts or "（无）")
    )
    return system_prompt, user_prompt


def parse_museum_llm_decision(
    raw_decision: Any,
    *,
    max_fact_ids: int = 3,
    max_evidence_ids: int = 5,
    require_evidence: bool = False,
) -> MuseumLlmDecision | None:
    text = str(raw_decision or "").strip()
    if not text:
        return None
    payload = _json_object(text)
    if payload is None:
        return None
    required = {"status", "answer", "social_intent"}
    required.add("evidence_ids" if require_evidence else "fact_ids")
    if require_evidence:
        required.add("claims")
    if not required.issubset(payload):
        return None

    status_value = payload.get("status")
    if not isinstance(status_value, str):
        return None
    status = status_value.strip().lower()
    if status not in _VALID_STATUSES:
        return None
    if not require_evidence and status in {"partial", "conflicting"}:
        return None

    raw_fact_ids = payload.get("fact_ids", [])
    if not isinstance(raw_fact_ids, list) or any(
        not isinstance(fact_id, str) for fact_id in raw_fact_ids
    ):
        return None
    fact_ids = tuple(
        dict.fromkeys(fact_id.strip() for fact_id in raw_fact_ids if fact_id.strip())
    )
    if len(fact_ids) > max_fact_ids:
        return None
    raw_evidence_ids = payload.get("evidence_ids", [])
    if not isinstance(raw_evidence_ids, list) or any(
        not isinstance(evidence_id, str) for evidence_id in raw_evidence_ids
    ):
        return None
    evidence_ids = tuple(
        dict.fromkeys(
            evidence_id.strip()
            for evidence_id in raw_evidence_ids
            if evidence_id.strip()
        )
    )
    if len(evidence_ids) > max_evidence_ids:
        return None
    raw_claims = payload.get("claims", [])
    if not isinstance(raw_claims, list):
        return None
    claims: list[MuseumLlmClaim] = []
    for raw_claim in raw_claims:
        if not isinstance(raw_claim, dict):
            return None
        claim_text = raw_claim.get("text")
        claim_evidence_ids = raw_claim.get("evidence_ids")
        if not isinstance(claim_text, str) or not isinstance(
            claim_evidence_ids, list
        ):
            return None
        if any(not isinstance(value, str) for value in claim_evidence_ids):
            return None
        normalized_claim_ids = tuple(
            dict.fromkeys(
                value.strip() for value in claim_evidence_ids if value.strip()
            )
        )
        if not claim_text.strip() or not normalized_claim_ids:
            return None
        claims.append(
            MuseumLlmClaim(
                text=claim_text.strip(),
                evidence_ids=normalized_claim_ids,
            )
        )
    answer_value = payload.get("answer")
    if not isinstance(answer_value, str):
        return None
    answer = answer_value.strip()
    social_value = payload.get("social_intent", "")
    if not isinstance(social_value, str):
        return None
    social_intent = social_value.strip().lower()

    if status in {"grounded", "partial", "conflicting"}:
        if require_evidence:
            if not evidence_ids or not claims or not answer or social_intent:
                return None
        elif not fact_ids or not answer or social_intent:
            return None
    elif status == "unsupported":
        if fact_ids or evidence_ids or claims or answer or social_intent:
            return None
    else:
        if (
            fact_ids
            or evidence_ids
            or claims
            or answer
            or social_intent not in _VALID_SOCIAL_INTENTS
        ):
            return None

    return MuseumLlmDecision(
        status=status,
        fact_ids=fact_ids,
        evidence_ids=evidence_ids,
        claims=tuple(claims),
        social_intent=social_intent,
        answer=answer,
    )


def summarize_museum_llm_response(
    raw_decision: Any,
    *,
    decision: MuseumLlmDecision | None,
    parse_status: str,
) -> str:
    text = str(raw_decision or "")
    payload: dict[str, Any] = {
        "chars": len(text),
        "parse_status": parse_status,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    if decision is not None:
        payload["status"] = decision.status
        payload["fact_ids"] = list(decision.fact_ids)
        payload["evidence_ids"] = list(decision.evidence_ids)
        payload["claim_count"] = len(decision.claims)
        payload["claim_evidence_ids"] = [
            list(claim.evidence_ids) for claim in decision.claims
        ]
        payload["claim_hashes"] = [
            hashlib.sha256(claim.text.encode("utf-8")).hexdigest()
            for claim in decision.claims
        ]
        if decision.social_intent:
            payload["social_intent"] = decision.social_intent
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _invoke_json_response(
    *,
    llm: Any,
    session_id: str,
    system_prompt: str,
    user_prompt: str,
) -> Any:
    response_no_stream = getattr(llm, "response_no_stream", None)
    if callable(response_no_stream):
        if _accepts_keyword(response_no_stream, "response_format"):
            return response_no_stream(
                system_prompt,
                user_prompt,
                response_format={"type": "json_object"},
            )
        return response_no_stream(system_prompt, user_prompt)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    response = getattr(llm, "response")
    kwargs = (
        {"response_format": {"type": "json_object"}}
        if _accepts_keyword(response, "response_format")
        else {}
    )
    return "".join(str(part) for part in response(session_id, messages, **kwargs))


def _accepts_keyword(callable_value: Any, keyword: str) -> bool:
    try:
        parameters = inspect.signature(callable_value).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        or parameter.name == keyword
        for parameter in parameters
    )


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def _format_evidence_pack(pack: EvidencePack) -> str:
    lines: list[str] = []
    for item in pack.items:
        lines.extend(
            (
                f"[EVIDENCE {item.id}]",
                f"来源：{item.source_title or item.source_id}",
                f"定位：{item.locator or '未提供'}",
                f"可信等级：{item.source_level or '未标注'}",
                f"原文：{item.text}",
                "[/EVIDENCE]",
            )
        )
    if pack.claims:
        lines.append("已发布声明参考：")
        lines.extend(
            f"- {claim.id}: {claim.statement}"
            for claim in pack.claims
        )
    if pack.conflict_groups:
        lines.append(
            "冲突证据组（同组证据存在不一致；若只显示一个ID，表示另一侧不可用，必须拒答）："
        )
        lines.extend(
            f"- {', '.join(group)}"
            for group in pack.conflict_groups
        )
    return "\n".join(lines)
