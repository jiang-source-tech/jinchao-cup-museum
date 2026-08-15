from __future__ import annotations

from time import perf_counter

import re

from core.museum.contracts import (
    AnswerClaim,
    AnswerResult,
    EvidencePack,
    EvidenceSnapshot,
)
from core.museum.query_understanding import (
    QuestionUnderstanding,
    understand_question,
)
from core.museum.llm_contract import (
    MuseumLlmCall,
    MuseumLlmClaim,
    MuseumLlmDecision,
    decide_with_museum_llm,
)
from core.museum.evidence_retrieval import (
    EvidenceSearchRequest,
    EvidenceSearchService,
)
from core.museum.store import MuseumStore
from core.museum.retrieval import (
    EvidenceRetriever,
    RetrievalRequest,
    SqliteEvidenceRetriever,
    dense_fact_types_for_intent,
)


_GUIDE_FACT_TYPES = (
    "history",
    "era",
    "material",
    "appearance",
    "excavation",
    "dimensions",
    "craft",
    "research_limit",
    "usage",
)


class GroundedAnswerService:
    def __init__(
        self,
        store: MuseumStore,
        retriever: EvidenceRetriever | None = None,
        evidence_search: EvidenceSearchService | None = None,
    ):
        self._store = store
        self._retriever = retriever or SqliteEvidenceRetriever(store)
        self._evidence_search = evidence_search

    @staticmethod
    def answer_conversational(question: str) -> AnswerResult | None:
        composition_started = perf_counter()
        social_intent = _local_social_intent(question)
        understanding = understand_question(question)
        if not social_intent and understanding.coarse_intent == "social":
            social_intent = "greeting"
        if not social_intent and understanding.coarse_intent == "unclear":
            social_intent = "unclear"
        if not social_intent:
            return None
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
        query_id: str = "",
    ) -> AnswerResult:
        composition_started = perf_counter()
        understanding = understanding or understand_question(question)
        conversational_answer = self.answer_conversational(question)
        if conversational_answer is not None:
            return conversational_answer

        if self._evidence_search is not None:
            return self._answer_from_evidence_pack(
                exhibit_id=exhibit_id,
                exhibit_name=exhibit_name,
                question=question,
                llm=llm,
                session_id=session_id,
                history=history,
                understanding=understanding,
                query_id=query_id,
            )

        retrieval_started = perf_counter()
        narrated_overview = (
            understanding.answer_depth in {"guided", "detailed"}
            and understanding.fine_intent == "overview"
        )
        retrieval_limit = _retrieval_limit(
            understanding.answer_depth,
            evidence_mode=False,
        )
        semantic_fallback = (
            understanding.fine_intent == "unknown"
            and bool(getattr(self._retriever, "supports_semantic_fallback", False))
        )
        retrieval = self._retriever.retrieve(RetrievalRequest(
            exhibit_id=exhibit_id,
            question=question,
            limit=retrieval_limit,
            fact_types=understanding.fact_types or (),
            query_terms=understanding.query_terms,
            overview=understanding.fine_intent == "overview",
            allow_dense_only=(
                understanding.coarse_intent == "exhibit_knowledge"
                and (
                    understanding.fine_intent != "unknown"
                    or semantic_fallback
                )
                and "price" not in understanding.fact_types
            ),
            dense_fact_types=(
                _GUIDE_FACT_TYPES
                if narrated_overview
                else dense_fact_types_for_intent(
                    understanding.fine_intent,
                    understanding.fact_types,
                    understanding.query_terms,
                )
            ),
            semantic_fallback=semantic_fallback,
            rule_intent=understanding.fine_intent,
            semantic_validation=(
                understanding.coarse_intent == "exhibit_knowledge"
                and bool(getattr(self._retriever, "supports_semantic_fallback", False))
            ),
        ))
        retrieved_evidence = retrieval.evidence
        retrieval_trace = retrieval.diagnostics.as_dict()
        retrieval_trace["answer_depth"] = understanding.answer_depth
        retrieval_trace["retrieval_limit"] = retrieval_limit
        if (
            understanding.fine_intent == "unknown"
            or retrieval_trace.get("semantic_override")
        ):
            understanding = _semantic_understanding_from_trace(
                retrieval_trace,
                fallback=understanding,
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
                    f"关于{exhibit_name}，演示知识库已发布的讲解暂时没有覆盖这个问题，"
                    "我不能替演示资料补写答案。你可以换个角度问问这件展品。"
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
        deterministic_answer = self._compose_grounded_answer(
            evidence,
            exhibit_name=exhibit_name,
            answer_depth=understanding.answer_depth,
        )
        spoken_text = deterministic_answer
        if decision is not None and decision.status == "grounded":
            rejection_reason = _grounded_paraphrase_failure_reason(
                decision.answer,
                "".join(fact.statement for fact in evidence.facts),
                answer_depth=understanding.answer_depth,
                exhibit_name=exhibit_name,
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

    def _answer_from_evidence_pack(
        self,
        *,
        exhibit_id: str,
        exhibit_name: str,
        question: str,
        llm,
        session_id: str,
        history,
        understanding: QuestionUnderstanding,
        query_id: str,
    ) -> AnswerResult:
        retrieval_started = perf_counter()
        narrated_overview = (
            understanding.answer_depth in {"guided", "detailed"}
            and understanding.fine_intent == "overview"
        )
        retrieval_limit = _retrieval_limit(
            understanding.answer_depth,
            evidence_mode=True,
        )
        pack = self._evidence_search.search(
            EvidenceSearchRequest(
                question=question,
                exhibit_ids=(exhibit_id,),
                fact_types=understanding.fact_types or (),
                limit=retrieval_limit,
                query_id=query_id,
            )
        )
        retrieval_ms = _duration_ms(retrieval_started)
        retrieval_trace = dict(pack.retrieval_trace)
        retrieval_trace["backend"] = "evidence_segments"
        retrieval_trace["answer_depth"] = understanding.answer_depth
        retrieval_trace["retrieval_limit"] = retrieval_limit
        composition_started = perf_counter()

        llm_call = MuseumLlmCall.not_called()
        decision = None
        if (
            llm is not None
            and pack.items
            and understanding.coarse_intent != "comparison"
            and "price" not in understanding.fact_types
        ):
            llm_call = decide_with_museum_llm(
                exhibit_name=exhibit_name,
                question=question,
                candidates=pack,
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
                evidence_pack=pack,
                retrieval_ms=retrieval_ms,
                composition_ms=_duration_ms(composition_started),
                coarse_intent=understanding.coarse_intent,
                fine_intent=understanding.fine_intent,
                intent_confidence=understanding.confidence,
                guard_result="conversational_scope",
                retrieval_trace=retrieval_trace,
                **_llm_answer_fields(llm_call),
            )

        unsupported_text = (
            f"关于{exhibit_name}，演示知识库暂时没有覆盖这个问题，"
            "我不能替资料补写答案。你可以换个角度问问这件展品。"
        )
        unsupported_guard = ""
        if not pack.items:
            unsupported_guard = "unsupported_no_evidence"
        elif understanding.coarse_intent == "comparison" or (
            "price" in understanding.fact_types
        ):
            unsupported_guard = "unsupported_intent"
        elif decision is not None and decision.status == "unsupported":
            unsupported_guard = "model_unsupported"
        if unsupported_guard:
            return AnswerResult(
                knowledge_status="unsupported",
                spoken_text=unsupported_text,
                evidence=None,
                evidence_pack=pack,
                retrieval_ms=retrieval_ms,
                composition_ms=_duration_ms(composition_started),
                coarse_intent=understanding.coarse_intent,
                fine_intent=understanding.fine_intent,
                intent_confidence=understanding.confidence,
                guard_result=unsupported_guard,
                retrieval_trace=retrieval_trace,
                **_llm_answer_fields(llm_call),
            )

        deterministic_ids = (
            _lexically_supported_evidence_ids(
                pack,
                answer_depth=understanding.answer_depth,
                overview=narrated_overview,
                allowed_evidence_ids={
                    evidence_id
                    for claim in pack.claims
                    for evidence_id in claim.supporting_evidence_ids
                },
            )
            if understanding.coarse_intent == "exhibit_knowledge"
            and understanding.fine_intent != "unknown"
            else ()
        )
        conflicting_ids = {
            evidence_id
            for group in pack.conflict_groups
            for evidence_id in group
        }
        deterministic_ids = tuple(
            evidence_id
            for evidence_id in deterministic_ids
            if evidence_id not in conflicting_ids
        )
        deterministic_answer = self._compose_segment_answer(
            pack,
            exhibit_name=exhibit_name,
            answer_depth=understanding.answer_depth,
            evidence_ids=deterministic_ids,
        )
        spoken_text = ""
        knowledge_status = "unsupported"
        cited_evidence_ids: tuple[str, ...] = ()
        answer_claims: tuple[AnswerClaim, ...] = ()
        guard_result = (
            "model_conversational_intent_mismatch"
            if conversational_rejected
            else "evidence_segments_lexical_fallback"
        )
        if decision is not None and decision.status in {
            "grounded",
            "partial",
            "conflicting",
        }:
            rejection_reason = _validate_evidence_decision(
                decision,
                pack,
                answer_depth=understanding.answer_depth,
                exhibit_name=exhibit_name,
            )
            if rejection_reason is None:
                spoken_text = decision.answer
                knowledge_status = decision.status
                cited_evidence_ids = decision.evidence_ids
                answer_claims = tuple(
                    AnswerClaim(
                        text=claim.text,
                        evidence_ids=claim.evidence_ids,
                    )
                    for claim in decision.claims
                )
                guard_result = {
                    "grounded": "model_answer_accepted",
                    "partial": "model_partial_answer_accepted",
                    "conflicting": "model_conflict_answer_accepted",
                }[decision.status]
            else:
                guard_result = rejection_reason
        elif decision is None and llm_call.result in {
            "invalid_response",
            "request_failed",
        }:
            guard_result = {
                "invalid_response": "model_response_invalid_fallback",
                "request_failed": "model_request_failed_fallback",
            }[llm_call.result]

        if not spoken_text and deterministic_answer:
            spoken_text = deterministic_answer
            knowledge_status = "grounded"
            cited_evidence_ids = deterministic_ids
            answer_claims = ()
        if not spoken_text:
            spoken_text = unsupported_text
            knowledge_status = "unsupported"
            cited_evidence_ids = ()
            answer_claims = ()

        return AnswerResult(
            knowledge_status=knowledge_status,
            spoken_text=spoken_text,
            evidence=None,
            evidence_pack=pack,
            retrieval_ms=retrieval_ms,
            composition_ms=_duration_ms(composition_started),
            coarse_intent=understanding.coarse_intent,
            fine_intent=understanding.fine_intent,
            intent_confidence=understanding.confidence,
            guard_result=guard_result,
            retrieval_trace=retrieval_trace,
            cited_evidence_ids=cited_evidence_ids,
            answer_claims=answer_claims,
            **_llm_answer_fields(llm_call),
        )

    @staticmethod
    def _compose_grounded_answer(
        evidence: EvidenceSnapshot,
        *,
        exhibit_name: str = "",
        answer_depth: str = "standard",
    ) -> str:
        if answer_depth not in {"guided", "detailed"}:
            return "".join(fact.statement for fact in evidence.facts)
        return _compose_guide_narrative(
            exhibit_name,
            tuple(
                (fact.fact_type, fact.statement)
                for fact in evidence.facts
            ),
        )

    @staticmethod
    def _compose_segment_answer(
        evidence: EvidencePack,
        *,
        exhibit_name: str = "",
        answer_depth: str = "standard",
        evidence_ids: tuple[str, ...] = (),
    ) -> str:
        allowed_ids = set(evidence_ids)
        claims = tuple(
            (claim.fact_type, claim.statement)
            for claim in evidence.claims
            if allowed_ids.intersection(claim.supporting_evidence_ids)
        )
        if claims:
            if answer_depth in {"guided", "detailed"}:
                return _compose_guide_narrative(exhibit_name, claims)
            return "".join(statement for _fact_type, statement in claims)

        texts: list[str] = []
        seen: set[str] = set()
        max_items = {
            "brief": 1,
            "guided": 6,
            "detailed": 8,
        }.get(answer_depth, 2)
        for item in evidence.items:
            if allowed_ids and item.id not in allowed_ids:
                continue
            if not allowed_ids:
                continue
            normalized = re.sub(r"\s+", " ", item.text).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            texts.append(normalized)
            if len(texts) >= max_items:
                break
        if not texts:
            return ""
        prefix = f"关于{exhibit_name}，现有资料显示：" if exhibit_name else "现有资料显示："
        return prefix + " ".join(texts)


def _retrieval_limit(answer_depth: str, *, evidence_mode: bool) -> int:
    if evidence_mode:
        return {
            "brief": 2,
            "guided": 10,
            "detailed": 12,
        }.get(answer_depth, 5)
    return {
        "brief": 1,
        "guided": 6,
        "detailed": 8,
    }.get(answer_depth, 3)


def _compose_guide_narrative(
    exhibit_name: str,
    facts: tuple[tuple[str, str], ...],
) -> str:
    unique_facts: list[tuple[str, str]] = []
    seen_statements: set[str] = set()
    for fact_type, statement in facts:
        normalized = statement.strip()
        if not normalized or normalized in seen_statements:
            continue
        seen_statements.add(normalized)
        unique_facts.append((fact_type, normalized))
    if not unique_facts:
        return ""

    groups = (
        (("appearance",), "可以先看它的外形"),
        (("era", "material"), "再看它的年代与材质"),
        (("dimensions",), "从尺寸信息看"),
        (("excavation",), "关于它的发现经过"),
        (("history",), "公开记录还提供了这条信息"),
        (("usage",), "关于它的用途"),
        (("craft", "research_limit"), "最后看它的制作和仍待研究之处"),
    )
    parts = [
        f"现在看到的是{exhibit_name}。"
        if exhibit_name
        else "现在来看这件展品。"
    ]
    used_indexes: set[int] = set()
    for fact_types, lead in groups:
        statements: list[str] = []
        for index, (fact_type, statement) in enumerate(unique_facts):
            if fact_type not in fact_types or index in used_indexes:
                continue
            used_indexes.add(index)
            statements.append(statement)
        if statements:
            parts.append(f"{lead}：{''.join(statements)}")

    remaining = [
        statement
        for index, (_fact_type, statement) in enumerate(unique_facts)
        if index not in used_indexes
    ]
    if remaining:
        parts.append(f"还有一些补充信息：{''.join(remaining)}")
    if len(unique_facts) >= 3:
        parts.append("把这些信息放在一起，这件展品的基本面貌就更清楚了。")
    return "".join(parts)


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


def _lexically_supported_evidence_ids(
    pack: EvidencePack,
    *,
    answer_depth: str,
    overview: bool,
    allowed_evidence_ids: set[str],
) -> tuple[str, ...]:
    if not allowed_evidence_ids:
        return ()
    limit = {
        "brief": 1,
        "guided": 6,
        "detailed": 8,
    }.get(answer_depth, 2)
    if overview:
        return tuple(
            item.id
            for item in pack.items
            if item.id in allowed_evidence_ids
        )[:limit]
    raw_candidates = pack.retrieval_trace.get("lexical_candidates", ())
    lexical_ids = {
        str(candidate.get("segment_id", ""))
        for candidate in raw_candidates
        if isinstance(candidate, dict)
        and candidate.get("segment_id")
        and float(candidate.get("score", 0.0) or 0.0) >= 1.0
    }
    return tuple(
        item.id
        for item in pack.items
        if item.id in lexical_ids and item.id in allowed_evidence_ids
    )[:limit]


def _semantic_understanding_from_trace(
    trace: dict[str, object],
    *,
    fallback: QuestionUnderstanding,
) -> QuestionUnderstanding:
    fine_intent = str(trace.get("semantic_intent", "") or "")
    if not fine_intent:
        return fallback
    fact_types = {
        "craft": ("craft", "research_limit"),
        "material": ("material",),
        "dimensions": ("dimensions",),
        "excavation": ("excavation",),
        "era": ("era",),
        "appearance": ("appearance",),
        "usage": ("usage",),
        "price": ("price",),
        "history": ("history",),
    }.get(fine_intent, ())
    if not fact_types:
        return fallback
    return QuestionUnderstanding(
        coarse_intent="exhibit_knowledge",
        fine_intent=fine_intent,
        fact_types=fact_types,
        confidence=float(trace.get("semantic_confidence", 0.0) or 0.0),
        source="semantic",
        answer_depth=fallback.answer_depth,
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


def _validate_evidence_decision(
    decision: MuseumLlmDecision,
    pack: EvidencePack,
    *,
    answer_depth: str,
    exhibit_name: str,
) -> str | None:
    allowed_ids = set(pack.evidence_ids)
    selected_ids = set(decision.evidence_ids)
    if not selected_ids or not selected_ids.issubset(allowed_ids):
        return "model_evidence_ids_rejected"
    selected_conflict_groups = tuple(
        set(group)
        for group in pack.conflict_groups
        if selected_ids.intersection(group)
    )
    if decision.status == "conflicting":
        if not selected_conflict_groups or any(
            len(group) < 2 or not group.issubset(selected_ids)
            for group in selected_conflict_groups
        ):
            return "model_conflict_without_complete_evidence"
    if pack.conflict_groups and decision.status != "conflicting":
        conflicting_ids = {
            evidence_id
            for group in pack.conflict_groups
            for evidence_id in group
        }
        if selected_ids & conflicting_ids:
            return "model_conflict_not_disclosed"
    if not decision.claims:
        return "model_claims_missing"
    items_by_id = {item.id: item for item in pack.items}
    claim_cited_ids: set[str] = set()
    for claim in decision.claims:
        claim_ids = set(claim.evidence_ids)
        if not claim_ids or not claim_ids.issubset(selected_ids):
            return "model_claim_evidence_ids_rejected"
        claim_cited_ids.update(claim_ids)
        evidence_text = "".join(
            items_by_id[evidence_id].text
            for evidence_id in claim.evidence_ids
            if evidence_id in items_by_id
        )
        reason = _evidence_claim_failure_reason(
            claim.text,
            evidence_text,
        )
        if reason is not None:
            return reason
    if claim_cited_ids != selected_ids:
        return "model_evidence_claim_set_mismatch"
    if decision.status == "conflicting":
        selected_conflict_ids = {
            evidence_id
            for group in selected_conflict_groups
            for evidence_id in group
        }
        if not selected_conflict_ids.issubset(claim_cited_ids):
            return "model_conflict_claims_incomplete"
        if not _discloses_conflict(decision.answer):
            return "model_conflict_not_disclosed"
        conflict_reason = _conflict_answer_failure_reason(
            decision.answer,
            decision.claims,
            selected_conflict_groups,
        )
        if conflict_reason is not None:
            return conflict_reason
    evidence_text = "".join(
        items_by_id[evidence_id].text
        for evidence_id in decision.evidence_ids
        if evidence_id in items_by_id
    )
    if decision.status == "conflicting":
        evidence_text += (
            "资料存在冲突。资料不一致。存在分歧。"
            "存在不同说法。存在两种说法。尚无定论。无法确定。"
        )
    answer_reason = _grounded_paraphrase_failure_reason(
        decision.answer,
        evidence_text,
        answer_depth=answer_depth,
        exhibit_name=exhibit_name,
    )
    if answer_reason is not None:
        return answer_reason
    claim_coverage_reason = _answer_claim_coverage_failure_reason(
        decision.answer,
        tuple(claim.text for claim in decision.claims),
        answer_depth=answer_depth,
        exhibit_name=exhibit_name,
    )
    if claim_coverage_reason is not None:
        return claim_coverage_reason
    return None


def _evidence_claim_failure_reason(
    claim: str,
    evidence_text: str,
) -> str | None:
    if len(claim) > 480:
        return "model_claim_too_long"
    if not _number_tokens(claim).issubset(_number_tokens(evidence_text)):
        return "model_claim_extra_number"
    if not _measurement_tokens(claim).issubset(_measurement_tokens(evidence_text)):
        return "model_claim_extra_measurement"
    if not _measurement_relations(claim).issubset(
        _measurement_relations(evidence_text)
    ):
        return "model_claim_measurement_relation_mismatch"
    if _claim_negation_mismatch(claim, evidence_text):
        return "model_claim_negation_mismatch"
    sentences = [
        sentence.strip()
        for sentence in re.split(r"[。！？!?；;\n]+", claim)
        if sentence.strip()
    ]
    if not sentences or len(sentences) > 8:
        return "model_claim_shape_rejected"
    cjk_pairs = _cjk_pairs(evidence_text)
    claim_pairs = _cjk_pairs(claim)
    if claim_pairs:
        covered = sum(pair in cjk_pairs for pair in claim_pairs)
        if not cjk_pairs or covered / len(claim_pairs) < 0.6:
            return "model_claim_unsupported_claim"
    tokens = set(re.findall(r"[A-Za-z0-9]+", claim.casefold()))
    evidence_tokens = set(re.findall(r"[A-Za-z0-9]+", evidence_text.casefold()))
    if tokens and not tokens.issubset(evidence_tokens):
        return "model_claim_unsupported_token"
    claim_content_tokens = _conflict_content_tokens(claim)
    evidence_content_tokens = _conflict_content_tokens(evidence_text)
    if claim_content_tokens:
        covered = len(claim_content_tokens & evidence_content_tokens)
        if covered / len(claim_content_tokens) < 0.6:
            return "model_claim_unsupported_claim"
    if not claim_pairs and not tokens:
        return "model_claim_shape_rejected"
    return None


_ARABIC_NUMBER_PATTERN = r"\d+(?:\.\d+)?"
_CHINESE_NUMBER_PATTERN = r"[零〇一二两三四五六七八九十百千万亿兆]+(?:点[零〇一二三四五六七八九]+)?"
_NUMBER_PATTERN = rf"(?:{_ARABIC_NUMBER_PATTERN}|{_CHINESE_NUMBER_PATTERN})"
_MEASUREMENT_UNITS = (
    "毫米|厘米|千米|公里|平方米|立方米|千克|公斤|毫克|克|"
    "世纪|年代|年|月|日|度|米|件|枚|元"
)


def _measurement_tokens(value: str) -> set[str]:
    return {
        f"{_normalize_number_token(match.group('number'))}{match.group('unit')}"
        for match in re.finditer(
            rf"(?P<number>{_NUMBER_PATTERN})\s*(?P<unit>{_MEASUREMENT_UNITS})",
            value,
        )
    }


def _measurement_relations(value: str) -> set[tuple[str, str]]:
    attributes = (
        "通高|残高|高度|长度|宽度|厚度|口径|底径|直径|周长|"
        "重量|面积|容量|高|长|宽|厚|重"
    )
    aliases = {
        "通高": "高",
        "残高": "高",
        "高度": "高",
        "长度": "长",
        "宽度": "宽",
        "厚度": "厚",
        "重量": "重",
    }
    relations = {
        (
            aliases.get(match.group("attribute"), match.group("attribute")),
            _normalize_number_token(match.group("measurement")),
        )
        for match in re.finditer(
            rf"(?P<attribute>{attributes})\s*(?:约|为|是|达|有)?\s*"
            rf"(?P<measurement>{_NUMBER_PATTERN}\s*(?:{_MEASUREMENT_UNITS}))",
            value,
        )
    }
    attribute_separator = r"\s*(?:、|,|，|和|及|与|×|x|X|\*)\s*"
    attribute_list = rf"(?:{attributes})(?:{attribute_separator}(?:{attributes}))+"
    for match in re.finditer(
        rf"(?P<attributes>{attribute_list})\s*(?:分别)?\s*"
        rf"(?:约|为|是|达|有|：|:)?\s*"
        rf"(?P<measurements>{_NUMBER_PATTERN}[^。；;\n]{{0,80}})",
        value,
    ):
        matched_attributes = [
            aliases.get(attribute, attribute)
            for attribute in re.findall(attributes, match.group("attributes"))
        ]
        measurement_text = match.group("measurements")
        numbers = [
            _normalize_number_token(number)
            for number in re.findall(_NUMBER_PATTERN, measurement_text)
        ]
        matched_units = re.findall(_MEASUREMENT_UNITS, measurement_text)
        if len(matched_units) == 1 and len(numbers) > 1:
            matched_units *= len(numbers)
        if len(matched_attributes) != len(numbers) or len(numbers) != len(matched_units):
            continue
        relations.update(
            (attribute, f"{number}{unit}")
            for attribute, number, unit in zip(
                matched_attributes,
                numbers,
                matched_units,
                strict=True,
            )
        )
    return relations


def _number_tokens(value: str) -> set[str]:
    tokens = set(
        re.findall(
            rf"(?<![\d.]){_ARABIC_NUMBER_PATTERN}(?![\d.])",
            value,
        )
    )
    for match in re.finditer(
        rf"(?P<number>{_CHINESE_NUMBER_PATTERN})(?P<qualifier>多|余|来|几)?"
        rf"(?=\s*(?:{_MEASUREMENT_UNITS}|个|位|处|座|次|层|级|批|套|种|份))",
        value,
    ):
        tokens.add(
            _normalize_number_token(
                f"{match.group('number')}{match.group('qualifier') or ''}"
            )
        )
    return tokens


def _normalize_number_token(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("〇", "零").replace("两", "二")


def _claim_negation_mismatch(claim: str, evidence_text: str) -> bool:
    claim_contrasts = _negation_contrasts(claim)
    evidence_contrasts = _negation_contrasts(evidence_text)
    if (
        claim_contrasts
        and evidence_contrasts
        and not claim_contrasts.issubset(evidence_contrasts)
    ):
        return True
    claim_clauses = _claim_clauses(claim)
    evidence_clauses = _claim_clauses(evidence_text)
    if not claim_clauses or not evidence_clauses:
        return False
    for claim_clause in claim_clauses:
        claim_pairs = _cjk_pairs(claim_clause)
        if not claim_pairs:
            continue
        best_clause = max(
            evidence_clauses,
            key=lambda clause: len(claim_pairs & _cjk_pairs(clause)),
        )
        overlap = len(claim_pairs & _cjk_pairs(best_clause)) / len(claim_pairs)
        if overlap >= 0.5 and _has_negation(claim_clause) != _has_negation(best_clause):
            return True
    return False


def _negation_contrasts(value: str) -> set[tuple[str, str]]:
    normalized = re.sub(r"\s+", "", value)
    return {
        (
            match.group("negative").strip("，,。；;"),
            match.group("positive").strip("，,。；;"),
        )
        for match in re.finditer(
            r"(?:不是|并非)(?P<negative>[^，,。；;！？!?]{1,24}?)"
            r"(?:而是|而应是)(?P<positive>[^，,。；;！？!?]{1,24})",
            normalized,
        )
    }


def _claim_clauses(value: str) -> tuple[str, ...]:
    return tuple(
        clause.strip()
        for clause in re.split(r"[。！？!?；;，,\n]+", value)
        if clause.strip()
    )


def _has_negation(value: str) -> bool:
    return bool(
        re.search(
            r"并非|并无|并没有|不是|没有|毫无|未有|尚未|未曾|未能|"
            r"无(?:法|从|证据|资料|记录|记载|依据)|无法|不能|不可|"
            r"不属于|不具备|不支持|未(?!来)|不(?!同|仅)|无",
            value,
        )
    )


def _discloses_conflict(value: str) -> bool:
    return any(
        term in value
        for term in (
            "冲突",
            "不一致",
            "不同说法",
            "两种说法",
            "存在分歧",
            "尚无定论",
            "无法确定",
        )
    )


def _answer_claim_coverage_failure_reason(
    answer: str,
    claim_texts: tuple[str, ...],
    *,
    answer_depth: str = "standard",
    exhibit_name: str = "",
) -> str | None:
    claim_text = "。".join(claim_texts)
    if not _number_tokens(answer).issubset(_number_tokens(claim_text)):
        return "model_answer_claim_number_mismatch"
    if not _measurement_tokens(answer).issubset(_measurement_tokens(claim_text)):
        return "model_answer_claim_measurement_mismatch"
    if not _measurement_relations(answer).issubset(
        _measurement_relations(claim_text)
    ):
        return "model_answer_claim_measurement_relation_mismatch"
    if _claim_negation_mismatch(answer, claim_text):
        return "model_answer_claim_negation_mismatch"

    claim_pairs = _cjk_pairs(claim_text)
    claim_tokens = set(re.findall(r"[A-Za-z0-9]+", claim_text.casefold()))
    for clause in _claim_clauses(answer):
        if _is_conflict_disclosure_clause(clause):
            continue
        grounded_clause = clause
        if answer_depth in {"guided", "detailed"}:
            grounded_clause = _without_guide_scaffolding(
                clause,
                exhibit_name=exhibit_name,
            )
            if not grounded_clause:
                continue
        clause_pairs = _cjk_pairs(grounded_clause)
        if clause_pairs:
            covered = len(clause_pairs & claim_pairs) / len(clause_pairs)
            if covered < 0.6:
                return "model_answer_claim_coverage_rejected"
        clause_content_tokens = _conflict_content_tokens(grounded_clause)
        if clause_content_tokens:
            covered = len(clause_content_tokens & _conflict_content_tokens(claim_text))
            if covered / len(clause_content_tokens) < 0.6:
                return "model_answer_claim_coverage_rejected"
        clause_tokens = set(re.findall(r"[A-Za-z0-9]+", grounded_clause.casefold()))
        if clause_tokens and not clause_tokens.issubset(claim_tokens):
            return "model_answer_claim_coverage_rejected"
    return None


def _conflict_answer_failure_reason(
    answer: str,
    claims: tuple[MuseumLlmClaim, ...],
    conflict_groups: tuple[set[str], ...],
) -> str | None:
    answer_tokens = _conflict_content_tokens(answer)
    for group in conflict_groups:
        side_claims: dict[str, list[str]] = {evidence_id: [] for evidence_id in group}
        for claim in claims:
            cited_sides = set(claim.evidence_ids).intersection(group)
            if len(cited_sides) == 1:
                side_claims[next(iter(cited_sides))].append(claim.text)
        if any(not texts for texts in side_claims.values()):
            return "model_conflict_claims_not_attributed"

        side_tokens = {
            evidence_id: _conflict_content_tokens("。".join(texts))
            for evidence_id, texts in side_claims.items()
        }
        for evidence_id, tokens in side_tokens.items():
            other_tokens = set().union(
                *(
                    candidate_tokens
                    for candidate_id, candidate_tokens in side_tokens.items()
                    if candidate_id != evidence_id
                )
            )
            distinctive = tokens - other_tokens
            if not distinctive:
                return "model_conflict_claims_indistinct"
            if not distinctive.intersection(answer_tokens):
                return "model_conflict_claim_omitted"
    return None


def _conflict_content_tokens(value: str) -> set[str]:
    normalized = re.sub(r"\s+", "", value)
    for term in (
        "另一份资料",
        "一份资料",
        "演示资料",
        "不同来源",
        "来源",
        "资料",
        "文献",
        "记录",
        "说法",
        "声称",
        "显示",
        "认为",
        "指出",
        "记载",
        "表明",
        "这件展品",
        "该展品",
        "这件器物",
        "该器物",
        "展品",
        "器物",
    ):
        normalized = normalized.replace(term, "")
    tokens = {f"zh:{pair}" for pair in _cjk_pairs(normalized)}
    tokens.update(f"num:{token}" for token in _number_tokens(normalized))
    tokens.update(
        f"latin:{token}"
        for token in re.findall(r"[A-Za-z0-9]+", normalized.casefold())
    )
    return tokens


def _is_conflict_disclosure_clause(value: str) -> bool:
    return _discloses_conflict(value) and not (
        _number_tokens(value) or _measurement_tokens(value)
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
        "你好讲解员",
        "您好讲解员",
        "嗨讲解员",
        "哈喽讲解员",
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
            "请说出展品名称和你想了解的内容，我会根据已发布的演示资料回答。"
        ),
        "capability": (
            "我可以讲解你说出的展品，也能围绕同一件展品继续回答追问。"
            "我只使用已经审核并发布的演示资料；没有确认的内容，我会直接告诉你。"
        ),
        "thanks": "不客气。你还可以继续问这件展品，或者说出另一件展品的名称。",
        "farewell": "再见，祝你接下来的参观顺利。",
        "greeting": "你好。请说出你想了解的展品名称。",
        "out_of_scope": (
            "我主要负责讲解馆内展品，暂时不讲笑话。"
            "你可以问展品的年代、材质、外形或制作方式。"
        ),
        "unclear": (
            "我没听清你是在问哪件展品或哪个方面。"
            "请说出展品名称，再问问它的材质、年代、外形或用途。"
        ),
    }.get(intent, "我在。请说出展品名称和你想了解的内容。")


def _is_grounded_paraphrase(answer: str, evidence_text: str) -> bool:
    return _grounded_paraphrase_failure_reason(answer, evidence_text) is None


def _grounded_paraphrase_failure_reason(
    answer: str,
    evidence_text: str,
    *,
    answer_depth: str = "standard",
    exhibit_name: str = "",
) -> str | None:
    max_characters, max_sentences = {
        "brief": (180, 2),
        "guided": (650, 10),
        "detailed": (900, 14),
    }.get(answer_depth, (260, 4))
    if len(answer) > max_characters:
        return "model_answer_too_long"
    if not _number_tokens(answer).issubset(_number_tokens(evidence_text)):
        return "model_answer_extra_number"
    if not _measurement_tokens(answer).issubset(_measurement_tokens(evidence_text)):
        return "model_answer_extra_measurement"
    if not _measurement_relations(answer).issubset(
        _measurement_relations(evidence_text)
    ):
        return "model_answer_measurement_relation_mismatch"
    if _claim_negation_mismatch(answer, evidence_text):
        return "model_answer_negation_mismatch"

    evidence_pairs = _cjk_pairs(evidence_text)
    sentences = [
        sentence.strip()
        for sentence in re.split(r"[。！？!?；;\n]+", answer)
        if sentence.strip()
    ]
    if not 1 <= len(sentences) <= max_sentences:
        return "model_answer_shape_rejected"
    for sentence in sentences:
        grounded_sentence = sentence
        if answer_depth in {"guided", "detailed"}:
            grounded_sentence = _without_guide_scaffolding(
                sentence,
                exhibit_name=exhibit_name,
            )
            if not grounded_sentence:
                continue
        pairs = _cjk_pairs(grounded_sentence)
        if not pairs:
            return "model_answer_shape_rejected"
        covered = sum(pair in evidence_pairs for pair in pairs)
        if covered / len(pairs) < 0.6:
            return "model_answer_unsupported_claim"
    return None


def _is_guide_scaffolding(value: str, *, exhibit_name: str = "") -> bool:
    normalized = re.sub(r"[\s，。！？、；：,.!?;:]", "", value)
    if not normalized:
        return False
    normalized_exhibit = re.sub(r"\s+", "", exhibit_name)
    if normalized_exhibit and normalized in {
        f"现在看到的是{normalized_exhibit}",
        f"现在来看{normalized_exhibit}",
        f"我们现在看到的是{normalized_exhibit}",
    }:
        return True
    if normalized in {
        "现在来看这件展品",
        "可以先看它的外形",
        "再看它的年代与材质",
        "从尺寸信息看",
        "关于它的发现经过",
        "公开记录还提供了这条信息",
        "关于它的用途",
        "最后看它的制作和仍待研究之处",
        "还有一些补充信息",
        "把这些信息放在一起",
        "将这些信息放在一起",
        "讲到这里",
    }:
        return True
    return bool(
        re.fullmatch(
            r"(?:我们|可以)?(?:先|再|接着|然后|最后)?(?:来)?"
            r"(?:看|看看|看一看|说说|讲讲|注意|观察)"
            r"(?:这件展品|这件器物|它)?(?:的)?"
            r"(?:外形|年代|材质|尺寸|出土信息|发现经过|制作工艺|"
            r"制作问题|用途|未解问题|基本信息)",
            normalized,
        )
    )


def _without_guide_scaffolding(value: str, *, exhibit_name: str = "") -> str:
    if _is_guide_scaffolding(value, exhibit_name=exhibit_name):
        return ""
    for separator in ("：", ":", "，", ","):
        prefix, matched, remainder = value.partition(separator)
        if matched and _is_guide_scaffolding(prefix, exhibit_name=exhibit_name):
            return remainder.strip()
    return value


def _cjk_pairs(text: str) -> set[str]:
    characters = re.findall(r"[\u3400-\u9fff]", text)
    return {
        "".join(characters[index : index + 2])
        for index in range(len(characters) - 1)
    }
