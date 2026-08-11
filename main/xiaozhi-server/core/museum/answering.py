from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

import json
import re

from core.museum.contracts import AnswerResult, EvidenceSnapshot
from core.museum.query_understanding import (
    QuestionUnderstanding,
    understand_question,
)
from core.museum.store import MuseumStore


@dataclass(frozen=True)
class _TurnDecision:
    status: str
    fact_ids: tuple[str, ...] = ()
    social_intent: str = ""
    answer: str = ""


class GroundedAnswerService:
    def __init__(self, store: MuseumStore):
        self._store = store

    @staticmethod
    def answer_conversational(question: str) -> AnswerResult | None:
        composition_started = perf_counter()
        social_intent = _local_social_intent(question)
        if not social_intent:
            return None
        understanding = understand_question(question)
        return AnswerResult(
            knowledge_status="conversational",
            spoken_text=_conversational_reply(social_intent),
            evidence=None,
            retrieval_ms=0,
            composition_ms=_duration_ms(composition_started),
            coarse_intent=understanding.coarse_intent,
            fine_intent=understanding.fine_intent,
            intent_confidence=understanding.confidence,
            guard_result="conversational_scope",
        )

    def answer(
        self,
        *,
        exhibit_id: str,
        exhibit_name: str,
        question: str,
        llm=None,
        session_id: str = "",
        history=(),
        understanding: QuestionUnderstanding | None = None,
    ) -> AnswerResult:
        composition_started = perf_counter()
        understanding = understanding or understand_question(question)
        conversational_answer = self.answer_conversational(question)
        if conversational_answer is not None:
            return conversational_answer

        retrieval_started = perf_counter()
        retrieved_evidence = self._store.retrieve_evidence(
            exhibit_id=exhibit_id,
            question=question,
            fact_types=understanding.fact_types,
            query_terms=understanding.query_terms,
            overview=understanding.fine_intent == "overview",
        )
        llm_candidates = retrieved_evidence
        if (
            llm_candidates is None
            and llm is not None
            and understanding.coarse_intent == "exhibit_knowledge"
            and understanding.fine_intent == "unknown"
        ):
            llm_candidates = self._store.published_evidence(exhibit_id)
        retrieval_ms = _duration_ms(retrieval_started)
        composition_started = perf_counter()

        decision = None
        if (
            llm_candidates is not None
            and understanding.coarse_intent != "comparison"
        ):
            decision = self._decide_with_llm(
                exhibit_name=exhibit_name,
                question=question,
                candidates=llm_candidates,
                llm=llm,
                session_id=session_id,
                history=history,
                understanding=understanding,
            )
        if decision is not None and decision.status == "conversational":
            return AnswerResult(
                knowledge_status="conversational",
                spoken_text=_conversational_reply(decision.social_intent),
                evidence=None,
                retrieval_ms=retrieval_ms,
                composition_ms=_duration_ms(composition_started),
                coarse_intent=understanding.coarse_intent,
                fine_intent=understanding.fine_intent,
                intent_confidence=understanding.confidence,
                guard_result="conversational_scope",
            )

        evidence = None
        guard_result = "published_facts_only"
        if decision is not None and decision.status == "grounded":
            evidence = _select_evidence(
                llm_candidates,
                decision.fact_ids,
                allowed_fact_types=understanding.fact_types,
            )
            if evidence is None:
                guard_result = "model_fact_ids_rejected"
                decision = None
        elif decision is not None and decision.status == "unsupported":
            guard_result = "model_unsupported_fallback"
        if decision is None:
            evidence = retrieved_evidence

        if evidence is None:
            return AnswerResult(
                knowledge_status="unsupported",
                spoken_text=(
                    f"关于{exhibit_name}，当前馆方资料还没有确认这一点，"
                    "我不能替它补一个答案。你可以换个角度问，或者让我先介绍一下这件展品。"
                ),
                evidence=None,
                retrieval_ms=retrieval_ms,
                composition_ms=_duration_ms(composition_started),
                coarse_intent=understanding.coarse_intent,
                fine_intent=understanding.fine_intent,
                intent_confidence=understanding.confidence,
                guard_result=(
                    guard_result
                    if guard_result != "published_facts_only"
                    else "unsupported_fallback"
                ),
            )
        deterministic_answer = self._compose_grounded_answer(evidence)
        spoken_text = deterministic_answer
        if decision is not None and decision.status == "grounded":
            rejection_reason = _grounded_paraphrase_failure_reason(
                decision.answer,
                "".join(fact.statement for fact in evidence.facts),
            )
            if rejection_reason is None:
                spoken_text = decision.answer
                guard_result = "model_answer_accepted"
            else:
                guard_result = rejection_reason
        return AnswerResult(
            knowledge_status="grounded",
            spoken_text=spoken_text,
            evidence=evidence,
            retrieval_ms=retrieval_ms,
            composition_ms=_duration_ms(composition_started),
            coarse_intent=understanding.coarse_intent,
            fine_intent=understanding.fine_intent,
            intent_confidence=understanding.confidence,
            guard_result=guard_result,
        )

    @staticmethod
    def _compose_grounded_answer(evidence: EvidenceSnapshot) -> str:
        statements = "".join(fact.statement for fact in evidence.facts)
        return (
            f"根据已审核资料，{statements}"
            "这轮我只使用了这件展品已经发布的资料，没有补充猜测。"
        )

    @staticmethod
    def _decide_with_llm(
        *,
        exhibit_name,
        question,
        candidates,
        llm,
        session_id,
        history,
        understanding,
    ) -> _TurnDecision | None:
        if llm is None:
            return None
        facts = "\n".join(
            f"- {fact.id}: {fact.statement}"
            for fact in (candidates.facts if candidates else ())
        )
        recent_history = json.dumps(
            list(history[-4:]) if history else [],
            ensure_ascii=False,
        )
        system_prompt = (
            "你是博物馆语音对话的受限路由器。只依据给定的当前展品事实判断本轮输入。"
            "只输出一个JSON对象，不要输出Markdown或解释。JSON字段必须为："
            "status、fact_ids、social_intent、answer。"
            "status只能是grounded、unsupported、conversational之一。"
            "grounded表示一个或多个给定事实可以直接回答问题；fact_ids选择最少且不超过3个事实ID，"
            "answer用中文回答2至4句，不得增加给定事实之外的具体信息。"
            "conversational只允许问候、身份、能力、感谢或告别；social_intent只能是"
            "greeting、identity、capability、thanks、farewell之一，fact_ids和answer留空。"
            "需要外部常识、价格、传说、推测或与博物馆无关的问题一律unsupported，其他字段留空。"
        )
        user_prompt = (
            f"当前展品：{exhibit_name}\n"
            f"问题粗分类：{understanding.coarse_intent}\n"
            f"问题细分类：{understanding.fine_intent}\n"
            f"最近对话：{recent_history}\n"
            f"游客本轮输入：{question}\n"
            f"当前发布且已审核的事实：\n{facts or '（无）'}"
        )
        try:
            if hasattr(llm, "response_no_stream"):
                raw_decision = llm.response_no_stream(system_prompt, user_prompt)
            else:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]
                raw_decision = "".join(
                    str(part) for part in llm.response(session_id, messages)
                )
        except Exception:
            return None
        return _parse_turn_decision(raw_decision)


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _parse_turn_decision(raw_decision) -> _TurnDecision | None:
    text = str(raw_decision or "").strip()
    if not text:
        return None
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
    if not isinstance(payload, dict):
        return None

    status = str(payload.get("status", "")).strip().lower()
    if status not in {"grounded", "unsupported", "conversational"}:
        return None
    raw_fact_ids = payload.get("fact_ids", [])
    if not isinstance(raw_fact_ids, list):
        return None
    fact_ids = tuple(
        dict.fromkeys(
            str(fact_id).strip()
            for fact_id in raw_fact_ids
            if str(fact_id).strip()
        )
    )
    if status == "grounded" and (not fact_ids or len(fact_ids) > 3):
        return None
    if status != "grounded":
        fact_ids = ()

    social_intent = str(payload.get("social_intent", "")).strip().lower()
    if status == "conversational" and social_intent not in {
        "greeting",
        "identity",
        "capability",
        "thanks",
        "farewell",
    }:
        social_intent = "greeting"
    return _TurnDecision(
        status=status,
        fact_ids=fact_ids,
        social_intent=social_intent,
        answer=str(payload.get("answer", "") or "").strip(),
    )


def _select_evidence(
    candidates: EvidenceSnapshot | None,
    fact_ids: tuple[str, ...],
    *,
    allowed_fact_types: tuple[str, ...] = (),
) -> EvidenceSnapshot | None:
    if candidates is None or not fact_ids:
        return None
    facts_by_id = {fact.id: fact for fact in candidates.facts}
    if any(fact_id not in facts_by_id for fact_id in fact_ids):
        return None
    if allowed_fact_types and any(
        facts_by_id[fact_id].fact_type not in allowed_fact_types
        for fact_id in fact_ids
    ):
        return None
    return EvidenceSnapshot(
        exhibit_id=candidates.exhibit_id,
        content_revision_id=candidates.content_revision_id,
        content_version=candidates.content_version,
        facts=tuple(facts_by_id[fact_id] for fact_id in fact_ids),
    )


def _local_social_intent(question: str) -> str | None:
    normalized = re.sub(r"[\s，。！？、；：,.!?;:]", "", question).lower()
    if any(
        term in normalized
        for term in (
            "你是谁",
            "你叫什么",
            "你的名字",
            "介绍一下你自己",
            "你是什么助手",
            "你是干什么的",
            "你是做什么的",
        )
    ):
        return "identity"
    if any(
        term in normalized
        for term in (
            "你会什么",
            "你能做什么",
            "你能干什么",
            "怎么用你",
            "可以问你什么",
            "能问你什么",
        )
    ):
        return "capability"
    if len(normalized) <= 12 and any(
        term in normalized for term in ("谢谢", "感谢", "多谢", "辛苦了")
    ):
        return "thanks"
    if len(normalized) <= 12 and any(
        term in normalized for term in ("再见", "拜拜", "回头见", "下次见")
    ):
        return "farewell"
    greetings = {
        "你好",
        "您好",
        "嗨",
        "哈喽",
        "hello",
        "hi",
        "你好啊",
        "您好啊",
        "你好呀",
        "您好呀",
        "哈喽啊",
        "hellothere",
        "hithere",
        "你好吗",
        "在吗",
    }
    if normalized in greetings:
        return "greeting"
    return None


def _conversational_reply(intent: str) -> str:
    return {
        "identity": (
            "你好，我是小芯，金潮杯博物馆的现场语音讲解助手。"
            "你可以直接问我眼前这件展品，我会根据馆方审核资料回答。"
        ),
        "capability": (
            "我可以讲解当前展品的已审核信息，也能接着回答你的追问。"
            "资料没有确认的内容，我会直接告诉你。"
        ),
        "thanks": "不客气，我们继续看这件展品吧。",
        "farewell": "再见，祝你接下来的参观顺利。",
        "greeting": "你好。你想先了解眼前这件展品的什么？",
    }.get(intent, "我在。你可以直接问我眼前这件展品。")


def _is_grounded_paraphrase(answer: str, evidence_text: str) -> bool:
    return _grounded_paraphrase_failure_reason(answer, evidence_text) is None


def _grounded_paraphrase_failure_reason(
    answer: str,
    evidence_text: str,
) -> str | None:
    if len(answer) > 220:
        return "model_answer_too_long"
    if any(
        number not in evidence_text
        for number in re.findall(r"\d+(?:\.\d+)?", answer)
    ):
        return "model_answer_extra_number"

    evidence_pairs = _cjk_pairs(evidence_text)
    sentences = [
        sentence.strip()
        for sentence in re.split(r"[。！？!?；;\n]+", answer)
        if sentence.strip()
    ]
    if not 2 <= len(sentences) <= 4:
        return "model_answer_shape_rejected"
    for sentence in sentences:
        pairs = _cjk_pairs(sentence)
        if not pairs:
            return "model_answer_shape_rejected"
        covered = sum(pair in evidence_pairs for pair in pairs)
        if covered / len(pairs) < 0.6:
            return "model_answer_unsupported_claim"
    return None


def _cjk_pairs(text: str) -> set[str]:
    characters = re.findall(r"[\u3400-\u9fff]", text)
    return {
        "".join(characters[index : index + 2])
        for index in range(len(characters) - 1)
    }
