from __future__ import annotations

from dataclasses import replace
from time import perf_counter

from core.conversation_runtime import TurnOutcome, TurnRequest
from core.museum.answering import GroundedAnswerService
from core.museum.contracts import AnswerResult, ExhibitResolution
from core.museum.exhibit_resolver import ExhibitResolver
from core.museum.query_understanding import understand_question
from core.museum.store import MuseumStore
from core.museum.retrieval import EvidenceRetriever


class MuseumRuntime:
    def __init__(
        self,
        store: MuseumStore,
        *,
        auto_assign_unknown_devices: bool = False,
        exhibit_context_mode: str = "explicit",
        retriever: EvidenceRetriever | None = None,
    ):
        self._store = store
        self._answering = GroundedAnswerService(store, retriever)
        self._auto_assign_unknown_devices = auto_assign_unknown_devices
        self._exhibit_context_mode = exhibit_context_mode
        self._exhibit_resolver = ExhibitResolver(store)

    def get_interaction_trace_by_request_id(self, request_id: str):
        return self._store.get_interaction_trace_by_request_id(request_id)

    def open_session(self, request: TurnRequest) -> TurnOutcome:
        started = perf_counter()
        resolved = self._resolve_context(request)
        if resolved is None:
            return self._missing_context_outcome(
                request=request,
                started=started,
                reason="current_exhibit_missing",
                record_trace=False,
            )
        session, context = resolved
        content_version = self._store.published_content_version(context.exhibit_id)
        state = self._build_state(
            request_id=request.request_id,
            session=session,
            context=context,
            knowledge_status="ready",
            source_count=0,
            content_version=content_version,
        )
        return TurnOutcome(
            handled=True,
            knowledge_status="ready",
            content_version=content_version,
            museum_state=state,
            display_state=state,
            audit_record={"visitor_session_id": session.id},
        )

    def handle_turn(self, request: TurnRequest) -> TurnOutcome:
        started = perf_counter()
        conversational_answer = self._answering.answer_conversational(
            request.user_text
        )
        if conversational_answer is not None:
            resolved = self._resolve_context(request) if request.device_id else None
            resolution = self._resolve_audit_resolution(request, resolved)
            return self._conversational_outcome(
                request=request,
                started=started,
                answer=conversational_answer,
                resolved=resolved,
                resolution=resolution,
            )
        if not request.device_id:
            return self._missing_context_outcome(
                request=request,
                started=started,
                reason="device_id_missing",
            )

        context_started = perf_counter()
        resolved = self._resolve_context(request)
        context_ms = _duration_ms(context_started)
        current_exhibit_id = resolved[0].current_exhibit_id if resolved else None
        resolution = self._exhibit_resolver.resolve(
            question=request.user_text,
            current_exhibit_id=current_exhibit_id,
        )
        if resolution.status == "explicit":
            resolved = self._resolve_context(
                request,
                explicit_exhibit_id=resolution.exhibit_id,
                context_source="explicit_mention",
            )
        elif resolution.status == "inherited" and resolved is not None:
            session, context = resolved
            resolved = (
                session,
                replace(context, context_source="inherited_session"),
            )
        elif resolution.status != "inherited":
            return self._missing_context_outcome(
                request=request,
                started=started,
                reason="exhibit_reference_missing",
                resolution=resolution,
            )
        if resolved is None:
            return self._missing_context_outcome(
                request=request,
                started=started,
                reason="exhibit_reference_missing",
                resolution=resolution,
            )

        session, context = resolved
        answer = self._answering.answer(
            exhibit_id=context.exhibit_id,
            exhibit_name=context.exhibit_name,
            question=request.user_text,
            llm=request.llm,
            session_id=request.transport_session_id,
            history=request.history,
        )
        duration_ms = _duration_ms(started)
        unanswered_reason = (
            "no_published_fact_match"
            if answer.knowledge_status == "unsupported"
            else None
        )
        guard_result = answer.guard_result or {
            "grounded": "published_facts_only",
            "conversational": "conversational_scope",
            "unsupported": "unsupported_fallback",
        }.get(answer.knowledge_status, "not_evaluated")
        trace_id = self._store.record_interaction(
            request_id=request.request_id,
            visitor_session_id=session.id,
            device_id=request.device_id,
            exhibit_id=context.exhibit_id,
            user_text=request.user_text,
            grounding_status=answer.knowledge_status,
            evidence=answer.evidence,
            answer_text=answer.spoken_text,
            unanswered_reason=unanswered_reason,
            coarse_intent=answer.coarse_intent,
            fine_intent=answer.fine_intent,
            intent_confidence=answer.intent_confidence,
            guard_result=guard_result,
            llm_invoked=answer.llm_invoked,
            llm_model=answer.llm_model,
            llm_prompt_version=answer.llm_prompt_version,
            llm_result=answer.llm_result,
            llm_response_summary=answer.llm_response_summary,
            stage_latency={
                "context_ms": context_ms,
                "retrieval_ms": answer.retrieval_ms,
                "composition_ms": answer.composition_ms,
                "total_ms": duration_ms,
            },
            duration_ms=duration_ms,
            occurred_at=request.occurred_at,
            resolution_status=resolution.status,
            context_source=context.context_source,
            matched_exhibit_text=resolution.matched_text,
            candidate_exhibit_ids=resolution.candidate_ids,
            retrieval_trace=answer.retrieval_trace,
        )
        fact_ids = list(answer.evidence.fact_ids) if answer.evidence else []
        source_ids = list(answer.evidence.source_ids) if answer.evidence else []
        content_version = answer.evidence.content_version if answer.evidence else None
        display_knowledge_status = answer.knowledge_status
        if answer.knowledge_status == "conversational":
            display_knowledge_status = "ready"
            content_version = self._store.published_content_version(
                context.exhibit_id
            )
        display_state = self._build_state(
            request_id=request.request_id,
            session=session,
            context=context,
            knowledge_status=display_knowledge_status,
            source_count=len(source_ids),
            content_version=content_version,
        )
        return TurnOutcome(
            handled=True,
            spoken_text=answer.spoken_text,
            knowledge_status=answer.knowledge_status,
            fact_ids=tuple(fact_ids),
            source_ids=tuple(source_ids),
            content_version=content_version,
            museum_state=display_state,
            audit_id=trace_id,
            display_state=display_state,
            audit_record={
                "request_id": request.request_id,
                "trace_id": trace_id,
                "visitor_session_id": session.id,
                "knowledge_status": answer.knowledge_status,
                "resolution_status": resolution.status,
                "context_source": context.context_source,
                "matched_exhibit_text": resolution.matched_text,
                "candidate_exhibit_ids": list(resolution.candidate_ids),
                "fact_ids": fact_ids,
                "source_ids": source_ids,
                "content_version": content_version,
                "coarse_intent": answer.coarse_intent,
                "fine_intent": answer.fine_intent,
                "intent_confidence": answer.intent_confidence,
                "guard_result": guard_result,
                "llm_invoked": answer.llm_invoked,
                "llm_model": answer.llm_model,
                "llm_prompt_version": answer.llm_prompt_version,
                "llm_result": answer.llm_result,
                "llm_response_summary": answer.llm_response_summary,
                "duration_ms": duration_ms,
                "stage_latency": {
                    "context_ms": context_ms,
                    "retrieval_ms": answer.retrieval_ms,
                    "composition_ms": answer.composition_ms,
                    "total_ms": duration_ms,
                },
                "retrieval_trace": answer.retrieval_trace,
            },
        )

    def _resolve_context(
        self,
        request: TurnRequest,
        *,
        explicit_exhibit_id: str | None = None,
        context_source: str | None = None,
    ):
        if not request.device_id:
            return None
        if self._auto_assign_unknown_devices and self._exhibit_context_mode == "demo_placement":
            self._store.ensure_demo_placement(request.device_id, request.occurred_at)
        resolved = self._store.resolve_or_create_session(
            device_id=request.device_id,
            occurred_at=request.occurred_at,
            requested_session_id=request.visitor_session_id,
            explicit_exhibit_id=(
                explicit_exhibit_id
                or _metadata_id(request, "selected_exhibit_id")
            ),
            route_exhibit_id=_metadata_id(request, "route_exhibit_id"),
            allow_device_placement=self._exhibit_context_mode == "demo_placement",
        )
        if resolved is None or context_source is None:
            return resolved
        session, context = resolved
        return session, replace(context, context_source=context_source)

    @staticmethod
    def _build_state(
        *,
        request_id,
        session,
        context,
        knowledge_status,
        source_count,
        content_version,
    ):
        return {
            "type": "museum_state",
            "version": 1,
            "request_id": request_id,
            "session_id": session.id,
            "context": {
                "museum_id": context.museum_id,
                "zone_id": context.zone_id,
                "exhibit_id": context.exhibit_id,
                "exhibit_name": context.exhibit_name,
                "source": context.context_source,
            },
            "visitor_mode": session.visitor_mode,
            "journey": {
                "route_id": "",
                "current_stop": 1,
                "total_stops": 1,
                "next_exhibit_name": "",
            },
            "prompt": {
                "title": context.exhibit_name,
                "body": "你可以直接问我这件展品的年代、材质或制作方式。",
            },
            "grounding": {
                "status": knowledge_status,
                "source_count": source_count,
                "content_version": content_version,
            },
            "navigation": {
                "can_previous": False,
                "can_next": False,
                "can_end": True,
            },
        }

    def _missing_context_outcome(
        self,
        *,
        request: TurnRequest,
        started: float,
        reason: str,
        record_trace: bool = True,
        resolution: ExhibitResolution | None = None,
    ) -> TurnOutcome:
        if resolution is not None and resolution.status == "not_found":
            text = "我还没有收录你说的那件展品。请换一个馆内展品名称，或者先说出完整展品名。"
        elif resolution is not None and resolution.status == "ambiguous":
            text = "这个称呼可能对应多件展品。请说出更完整的展品名称。"
        else:
            text = "你想了解哪件展品？请先说出展品名称，再问你想知道的内容。"
        understanding = understand_question(request.user_text)
        duration_ms = _duration_ms(started)
        trace_id = self._store.record_interaction(
            request_id=request.request_id,
            visitor_session_id=request.visitor_session_id,
            device_id=request.device_id,
            exhibit_id=None,
            user_text=request.user_text,
            grounding_status="missing_context",
            evidence=None,
            answer_text=text,
            unanswered_reason=reason,
            coarse_intent=understanding.coarse_intent,
            fine_intent=understanding.fine_intent,
            intent_confidence=understanding.confidence,
            guard_result="missing_context",
            stage_latency={"total_ms": duration_ms},
            duration_ms=duration_ms,
            occurred_at=request.occurred_at,
            resolution_status=resolution.status if resolution else "missing",
            context_source=resolution.context_source if resolution else "missing",
            matched_exhibit_text=resolution.matched_text if resolution else None,
            candidate_exhibit_ids=resolution.candidate_ids if resolution else (),
        ) if record_trace else None
        state = {
            "type": "museum_state",
            "version": 1,
            "request_id": request.request_id,
            "session_id": request.visitor_session_id or "",
            "context": {
                "museum_id": "",
                "zone_id": "",
                "exhibit_id": "",
                "exhibit_name": "请说出展品名称",
                "source": "missing",
            },
            "visitor_mode": "general",
            "journey": {
                "route_id": "",
                "current_stop": 0,
                "total_stops": 0,
                "next_exhibit_name": "",
            },
            "prompt": {"title": "等待选择展品", "body": text},
            "grounding": {
                "status": "missing_context",
                "source_count": 0,
                "content_version": None,
            },
            "navigation": {
                "can_previous": False,
                "can_next": False,
                "can_end": True,
            },
        }
        return TurnOutcome(
            handled=True,
            spoken_text=text,
            knowledge_status="missing_context",
            museum_state=state,
            audit_id=trace_id,
            display_state=state,
            audit_record={
                "request_id": request.request_id,
                "trace_id": trace_id,
                "knowledge_status": "missing_context",
                "resolution_status": resolution.status if resolution else "missing",
                "context_source": resolution.context_source
                if resolution
                else "missing",
                "candidate_exhibit_ids": list(resolution.candidate_ids)
                if resolution
                else [],
                "matched_exhibit_text": resolution.matched_text if resolution else None,
                "fact_ids": [],
                "source_ids": [],
                "coarse_intent": understanding.coarse_intent,
                "fine_intent": understanding.fine_intent,
                "intent_confidence": understanding.confidence,
                "guard_result": "missing_context",
                "llm_invoked": False,
                "llm_model": "",
                "llm_prompt_version": "",
                "llm_result": "not_called",
                "llm_response_summary": "{}",
                "duration_ms": duration_ms,
            },
            error_code=reason,
        )

    def _conversational_outcome(
        self,
        *,
        request: TurnRequest,
        started: float,
        answer: AnswerResult,
        resolved,
        resolution: ExhibitResolution | None,
    ) -> TurnOutcome:
        duration_ms = _duration_ms(started)
        session = None
        context = None
        if resolved is not None:
            session, context = resolved

        trace_id = self._store.record_interaction(
            request_id=request.request_id,
            visitor_session_id=session.id if session is not None else None,
            device_id=request.device_id,
            exhibit_id=context.exhibit_id if context is not None else None,
            user_text=request.user_text,
            grounding_status="conversational",
            evidence=None,
            answer_text=answer.spoken_text,
            unanswered_reason=None,
            coarse_intent=answer.coarse_intent,
            fine_intent=answer.fine_intent,
            intent_confidence=answer.intent_confidence,
            guard_result="conversational_scope",
            llm_invoked=answer.llm_invoked,
            llm_model=answer.llm_model,
            llm_prompt_version=answer.llm_prompt_version,
            llm_result=answer.llm_result,
            llm_response_summary=answer.llm_response_summary,
            stage_latency={
                "retrieval_ms": answer.retrieval_ms,
                "composition_ms": answer.composition_ms,
                "total_ms": duration_ms,
            },
            duration_ms=duration_ms,
            occurred_at=request.occurred_at,
            resolution_status=resolution.status if resolution else "missing",
            context_source=(
                resolution.context_source
                if resolution
                else context.context_source
                if context is not None
                else "missing"
            ),
            matched_exhibit_text=resolution.matched_text if resolution else None,
            candidate_exhibit_ids=resolution.candidate_ids if resolution else (),
        )
        if session is not None and context is not None:
            content_version = self._store.published_content_version(
                context.exhibit_id
            )
            display_state = self._build_state(
                request_id=request.request_id,
                session=session,
                context=context,
                knowledge_status="ready",
                source_count=0,
                content_version=content_version,
            )
        else:
            content_version = None
            display_state = {
                "type": "museum_state",
                "version": 1,
                "request_id": request.request_id,
                "session_id": request.visitor_session_id or "",
                "context": {
                    "museum_id": "",
                    "zone_id": "",
                    "exhibit_id": "",
                    "exhibit_name": "金潮杯博物馆",
                    "source": "unassigned",
                },
                "visitor_mode": "general",
                "journey": {
                    "route_id": "",
                    "current_stop": 0,
                    "total_stops": 0,
                    "next_exhibit_name": "",
                },
                "prompt": {
                    "title": "语音讲解助手",
                    "body": answer.spoken_text,
                },
                "grounding": {
                    "status": "ready",
                    "source_count": 0,
                    "content_version": None,
                },
                "navigation": {
                    "can_previous": False,
                    "can_next": False,
                    "can_end": True,
                },
            }
        return TurnOutcome(
            handled=True,
            spoken_text=answer.spoken_text,
            knowledge_status="conversational",
            fact_ids=(),
            source_ids=(),
            content_version=content_version,
            museum_state=display_state,
            audit_id=trace_id,
            display_state=display_state,
            audit_record={
                "request_id": request.request_id,
                "trace_id": trace_id,
                "visitor_session_id": session.id if session is not None else None,
                "knowledge_status": "conversational",
                "resolution_status": resolution.status if resolution else "missing",
                "context_source": (
                    resolution.context_source
                    if resolution
                    else context.context_source
                    if context is not None
                    else "missing"
                ),
                "matched_exhibit_text": (
                    resolution.matched_text if resolution else None
                ),
                "candidate_exhibit_ids": (
                    list(resolution.candidate_ids) if resolution else []
                ),
                "fact_ids": [],
                "source_ids": [],
                "content_version": content_version,
                "coarse_intent": answer.coarse_intent,
                "fine_intent": answer.fine_intent,
                "intent_confidence": answer.intent_confidence,
                "guard_result": "conversational_scope",
                "llm_invoked": answer.llm_invoked,
                "llm_model": answer.llm_model,
                "llm_prompt_version": answer.llm_prompt_version,
                "llm_result": answer.llm_result,
                "llm_response_summary": answer.llm_response_summary,
                "duration_ms": duration_ms,
            },
        )

    def _resolve_audit_resolution(
        self,
        request: TurnRequest,
        resolved,
    ) -> ExhibitResolution | None:
        if not request.device_id:
            return None
        current_exhibit_id = resolved[0].current_exhibit_id if resolved else None
        return self._exhibit_resolver.resolve(
            question=request.user_text,
            current_exhibit_id=current_exhibit_id,
        )


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _metadata_id(request: TurnRequest, key: str) -> str | None:
    value = request.metadata.get(key)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
