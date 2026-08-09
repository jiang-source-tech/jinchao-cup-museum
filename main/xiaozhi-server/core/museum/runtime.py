from __future__ import annotations

from time import perf_counter

from core.conversation_runtime import TurnOutcome, TurnRequest
from core.museum.answering import GroundedAnswerService
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

    def handle_turn(self, request: TurnRequest) -> TurnOutcome:
        started = perf_counter()
        if not request.device_id:
            return self._missing_context_outcome(
                request=request,
                started=started,
                reason="device_id_missing",
            )

        if self._auto_assign_unknown_devices:
            self._store.ensure_demo_placement(request.device_id, request.occurred_at)

        context_started = perf_counter()
        resolved = self._store.resolve_or_create_session(
            device_id=request.device_id,
            occurred_at=request.occurred_at,
            requested_session_id=request.visitor_session_id,
            explicit_exhibit_id=_metadata_id(request, "selected_exhibit_id"),
            route_exhibit_id=_metadata_id(request, "route_exhibit_id"),
        )
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
            question=request.user_text,
        )
        duration_ms = _duration_ms(started)
        unanswered_reason = (
            "no_published_fact_match"
            if answer.knowledge_status == "unsupported"
            else None
        )
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
            guard_result=(
                "published_facts_only"
                if answer.knowledge_status == "grounded"
                else "unsupported_fallback"
            ),
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
        display_state = {
            "version": 1,
            "request_id": request.request_id,
            "session_id": session.id,
            "context": {
                "museum_id": context.museum_id,
                "zone_id": context.zone_id,
                "exhibit_id": context.exhibit_id,
                "exhibit_name": context.exhibit_name,
                "source": context.context_source,
            },
            "visitor_mode": session.visitor_mode,
            "prompt": {
                "title": "像现代杯子的古代水晶杯",
                "body": "观察杯口、杯壁和圈足，找找它与现代玻璃杯相似的地方。",
            },
            "grounding": {
                "status": answer.knowledge_status,
                "source_count": len(source_ids),
                "content_version": content_version,
            },
        }
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

    def _missing_context_outcome(
        self,
        *,
        request: TurnRequest,
        started: float,
        reason: str,
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
        )
        return TurnOutcome(
            handled=True,
            spoken_text=text,
            knowledge_status="missing_context",
            museum_state={
                "version": 1,
                "request_id": request.request_id,
                "grounding": {
                    "status": "missing_context",
                    "source_count": 0,
                    "content_version": None,
                },
            },
            audit_id=trace_id,
            display_state={
                "version": 1,
                "request_id": request.request_id,
                "grounding": {
                    "status": "missing_context",
                    "source_count": 0,
                    "content_version": None,
                },
            },
            audit_record={
                "trace_id": trace_id,
                "knowledge_status": "missing_context",
                "fact_ids": [],
                "source_ids": [],
                "duration_ms": duration_ms,
            },
            error_code=reason,
        )


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _metadata_id(request: TurnRequest, key: str) -> str | None:
    value = request.metadata.get(key)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
