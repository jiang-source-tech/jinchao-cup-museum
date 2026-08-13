from __future__ import annotations

from time import perf_counter

import re

from core.museum.contracts import AnswerResult, EvidenceSnapshot
from core.museum.query_understanding import (
    QuestionUnderstanding,
    understand_question,
)
from core.museum.llm_contract import (
    MuseumLlmCall,
    decide_with_museum_llm,
)
from core.museum.store import MuseumStore
from core.museum.retrieval import (
    EvidenceRetriever,
    RetrievalRequest,
    SqliteEvidenceRetriever,
    dense_fact_types_for_intent,
)


class GroundedAnswerService:
    def __init__(
        self,
        store: MuseumStore,
        retriever: EvidenceRetriever | None = None,
    ):
        self._store = store
        self._retriever = retriever or SqliteEvidenceRetriever(store)

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
        retrieval = self._retriever.retrieve(RetrievalRequest(
            exhibit_id=exhibit_id,
            question=question,
            fact_types=understanding.fact_types or (),
            query_terms=understanding.query_terms,
            overview=understanding.fine_intent == "overview",
            allow_dense_only=(
                understanding.coarse_intent == "exhibit_knowledge"
                and understanding.fine_intent != "unknown"
                and "price" not in understanding.fact_types
            ),
            dense_fact_types=dense_fact_types_for_intent(
                understanding.fine_intent,
                understanding.fact_types,
                understanding.query_terms,
            ),
        ))
        retrieved_evidence = retrieval.evidence
        retrieval_trace = retrieval.diagnostics.as_dict()
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

        llm_call = MuseumLlmCall.not_called()
        decision = None
        if (
            llm is not None
            and llm_candidates is not None
            and understanding.coarse_intent != "comparison"
        ):
            llm_call = decide_with_museum_llm(
                exhibit_name=exhibit_name,
                question=question,
                candidates=llm_candidates,
                llm=llm,
                session_id=session_id,
                history=history,
                understanding=understanding,
            )
            decision = llm_call.decision
        conversational_rejected = bool(
            decision is not None
            and decision.status == "conversational"
            and _local_social_intent(question) != decision.social_intent
        )
        if conversational_rejected:
            decision = None
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
                retrieval_trace=retrieval_trace,
                **_llm_answer_fields(llm_call),
            )

        evidence = None
        guard_result = (
            "model_conversational_intent_mismatch"
            if conversational_rejected
            else "published_facts_only"
        )
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
            evidence = retrieved_evidence
            guard_result = (
                "model_unsupported_grounded_fallback"
                if evidence is not None
                else "model_unsupported_fallback"
            )
        if decision is None:
            evidence = retrieved_evidence
            if guard_result == "published_facts_only":
                guard_result = {
                    "invalid_response": "model_response_invalid_fallback",
                    "request_failed": "model_request_failed_fallback",
                }.get(llm_call.result, guard_result)

        if evidence is None:
            return AnswerResult(
                knowledge_status="unsupported",
                spoken_text=(
                    f"关于{exhibit_name}，当前没有可供游客使用的已发布讲解内容，"
                    "我不能替馆方补写答案。你可以换个问题，或者稍后再试。"
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
                retrieval_trace=retrieval_trace,
                **_llm_answer_fields(llm_call),
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
            retrieval_trace=retrieval_trace,
            **_llm_answer_fields(llm_call),
        )

    @staticmethod
    def _compose_grounded_answer(evidence: EvidenceSnapshot) -> str:
        return "".join(fact.statement for fact in evidence.facts)

def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _llm_answer_fields(llm_call: MuseumLlmCall) -> dict[str, object]:
    return {
        "llm_invoked": llm_call.invoked,
        "llm_model": llm_call.model_name,
        "llm_prompt_version": llm_call.prompt_version,
        "llm_result": llm_call.result,
        "llm_response_summary": llm_call.response_summary,
        "llm_ms": llm_call.duration_ms,
    }


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
            "你能帮我做什么",
            "你可以帮我做什么",
            "能帮我做什么",
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
    if any(
        term in normalized
        for term in (
            "讲个笑话",
            "讲笑话",
            "说个笑话",
            "说笑话",
        )
    ):
        return "out_of_scope"
    return None


def _conversational_reply(intent: str) -> str:
    return {
        "identity": (
            "你好，我是金潮杯博物馆讲解助手。"
            "请说出展品名称和你想了解的内容，我会根据已审核资料回答。"
        ),
        "capability": (
            "我可以讲解你说出的展品，也能围绕同一件展品继续回答追问。"
            "我只使用已经审核的资料；没有确认的内容，我会直接告诉你。"
        ),
        "thanks": "不客气。你还可以继续问这件展品，或者说出另一件展品的名称。",
        "farewell": "再见，祝你接下来的参观顺利。",
        "greeting": "你好。请说出你想了解的展品名称。",
        "out_of_scope": (
            "我主要负责讲解馆内展品，暂时不讲笑话。"
            "你可以问展品的年代、材质、外形或制作方式。"
        ),
    }.get(intent, "我在。请说出展品名称和你想了解的内容。")


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
    if not 1 <= len(sentences) <= 4:
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
