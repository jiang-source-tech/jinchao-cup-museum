from __future__ import annotations

from dataclasses import dataclass
import hashlib
import inspect
import json
from time import perf_counter
from typing import Any

from core.museum.contracts import EvidenceSnapshot
from core.museum.query_understanding import QuestionUnderstanding


MUSEUM_LLM_PROMPT_VERSION = "museum-grounded-router-v1"
_VALID_STATUSES = {"grounded", "unsupported", "conversational"}
_VALID_SOCIAL_INTENTS = {
    "greeting",
    "identity",
    "capability",
    "thanks",
    "farewell",
}


@dataclass(frozen=True)
class MuseumLlmDecision:
    status: str
    fact_ids: tuple[str, ...] = ()
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
    candidates: EvidenceSnapshot,
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
            prompt_version=MUSEUM_LLM_PROMPT_VERSION,
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
    )
    result = "parsed" if decision is not None else "invalid_response"
    return MuseumLlmCall(
        decision=decision,
        invoked=True,
        model_name=model_name,
        prompt_version=MUSEUM_LLM_PROMPT_VERSION,
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
    candidates: EvidenceSnapshot,
    history: tuple | list,
    understanding: QuestionUnderstanding,
) -> tuple[str, str]:
    facts = "\n".join(
        f"- {fact.id}: {fact.statement}" for fact in candidates.facts
    )
    recent_history = json.dumps(
        list(history[-4:]) if history else [],
        ensure_ascii=False,
    )
    grounded_contract = (
        "详细讲解模式下选择覆盖主要方面的事实，最多5个，answer用中文回答4至8句；"
        "普通模式选择最少且不超过3个给定事实ID，answer用中文回答1至4句。"
        if understanding.answer_depth == "detailed"
        else "fact_ids选择最少且不超过3个给定事实ID，answer用中文回答1至4句。"
    )
    system_prompt = (
        "你是博物馆语音对话的受限事实路由器。"
        "你只能依据本次输入中的当前展品事实，不能使用外部常识或自行推测。"
        "只输出一个JSON对象，不要输出Markdown、代码围栏或解释。"
        "JSON字段必须包含status、fact_ids、social_intent、answer。"
        "status只能是grounded、unsupported、conversational之一。"
        "grounded表示一个或多个给定事实可以直接回答问题；"
        + grounded_contract
        +
        "如果游客要求一句话、简短说明或讲给小朋友听，必须遵守该表达要求，"
        "不得增加事实之外的数字、人物、地点、年代、因果、用途或传说。"
        "unsupported表示给定事实不能直接回答，fact_ids必须是空数组，"
        "social_intent和answer必须是空字符串。"
        "conversational只允许问候、身份、能力、感谢或告别；"
        "social_intent只能是greeting、identity、capability、thanks、farewell之一，"
        "fact_ids必须是空数组，answer必须是空字符串。"
    )
    user_prompt = (
        f"提示版本：{MUSEUM_LLM_PROMPT_VERSION}\n"
        f"当前展品：{exhibit_name}\n"
        f"问题粗分类：{understanding.coarse_intent}\n"
        f"问题细分类：{understanding.fine_intent}\n"
        f"最近对话：{recent_history}\n"
        f"游客本轮输入：{question}\n"
        "回答表达：优先遵守游客对篇幅、受众和通俗程度的明确要求。\n"
        f"当前发布且已审核的事实：\n{facts or '（无）'}"
    )
    return system_prompt, user_prompt


def parse_museum_llm_decision(
    raw_decision: Any,
    *,
    max_fact_ids: int = 3,
) -> MuseumLlmDecision | None:
    text = str(raw_decision or "").strip()
    if not text:
        return None
    payload = _json_object(text)
    if payload is None:
        return None
    if not {"status", "fact_ids", "answer"}.issubset(payload):
        return None

    status_value = payload.get("status")
    if not isinstance(status_value, str):
        return None
    status = status_value.strip().lower()
    if status not in _VALID_STATUSES:
        return None

    raw_fact_ids = payload.get("fact_ids")
    if not isinstance(raw_fact_ids, list) or any(
        not isinstance(fact_id, str) for fact_id in raw_fact_ids
    ):
        return None
    fact_ids = tuple(
        dict.fromkeys(fact_id.strip() for fact_id in raw_fact_ids if fact_id.strip())
    )
    answer_value = payload.get("answer")
    if not isinstance(answer_value, str):
        return None
    answer = answer_value.strip()
    social_value = payload.get("social_intent", "")
    if not isinstance(social_value, str):
        return None
    social_intent = social_value.strip().lower()

    if status == "grounded":
        if not fact_ids or len(fact_ids) > max_fact_ids or not answer or social_intent:
            return None
    elif status == "unsupported":
        if fact_ids or answer or social_intent:
            return None
    else:
        if fact_ids or answer or social_intent not in _VALID_SOCIAL_INTENTS:
            return None

    return MuseumLlmDecision(
        status=status,
        fact_ids=fact_ids,
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
