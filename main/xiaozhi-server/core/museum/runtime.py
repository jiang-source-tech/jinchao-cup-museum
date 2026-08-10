from __future__ import annotations

from time import perf_counter

from core.conversation_runtime import TurnOutcome, TurnRequest
from core.museum.answering import GroundedAnswerService
from core.museum.contracts import AnswerResult
from core.museum.store import MuseumStore


class MuseumRuntime:
    def __init__(
        self,
        store: MuseumStore,
        *,
        auto_assign_unknown_devices: bool = False,
    ):
        self._store = store
        self._answering = GroundedAnswerService(store)
        self._auto_assign_unknown_devices = auto_assign_unknown_devices

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
            return self._conversational_outcome(
                request=request,
                started=started,
                answer=conversational_answer,
                resolved=resolved,
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
        if resolved is None:
            return self._missing_context_outcome(
                request=request,
                started=started,
                reason="current_exhibit_missing",
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
        guard_result = {
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
            guard_result=guard_result,
            stage_latency={
                "context_ms": context_ms,
                "retrieval_ms": answer.retrieval_ms,
                "composition_ms": answer.composition_ms,
                "total_ms": duration_ms,
            },
            duration_ms=duration_ms,
            occurred_at=request.occurred_at,
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
                "trace_id": trace_id,
                "visitor_session_id": session.id,
                "knowledge_status": answer.knowledge_status,
                "fact_ids": fact_ids,
                "source_ids": source_ids,
                "content_version": content_version,
                "duration_ms": duration_ms,
                "stage_latency": {
                    "context_ms": context_ms,
                    "retrieval_ms": answer.retrieval_ms,
                    "composition_ms": answer.composition_ms,
                    "total_ms": duration_ms,
                },
            },
        )

    def _resolve_context(self, request: TurnRequest):
        if not request.device_id:
            return None
        if self._auto_assign_unknown_devices:
            self._store.ensure_demo_placement(request.device_id, request.occurred_at)
        return self._store.resolve_or_create_session(
            device_id=request.device_id,
            occurred_at=request.occurred_at,
            requested_session_id=request.visitor_session_id,
            explicit_exhibit_id=_metadata_id(request, "selected_exhibit_id"),
            route_exhibit_id=_metadata_id(request, "route_exhibit_id"),
        )

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
                "title": "像现代杯子的古代水晶杯",
                "body": "观察杯口、杯壁和圈足，找找它与现代玻璃杯相似的地方。",
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
    ) -> TurnOutcome:
        text = "我还不知道你现在站在哪件展品前，请先在设备上选择展品。"
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
            guard_result="missing_context",
            stage_latency={"total_ms": duration_ms},
            duration_ms=duration_ms,
            occurred_at=request.occurred_at,
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
                "exhibit_name": "请先选择展品",
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
                "trace_id": trace_id,
                "knowledge_status": "missing_context",
                "fact_ids": [],
                "source_ids": [],
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
            guard_result="conversational_scope",
            stage_latency={
                "retrieval_ms": answer.retrieval_ms,
                "composition_ms": answer.composition_ms,
                "total_ms": duration_ms,
            },
            duration_ms=duration_ms,
            occurred_at=request.occurred_at,
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
                "trace_id": trace_id,
                "visitor_session_id": session.id if session is not None else None,
                "knowledge_status": "conversational",
                "fact_ids": [],
                "source_ids": [],
                "content_version": content_version,
                "duration_ms": duration_ms,
            },
        )


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _metadata_id(request: TurnRequest, key: str) -> str | None:
    value = request.metadata.get(key)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
