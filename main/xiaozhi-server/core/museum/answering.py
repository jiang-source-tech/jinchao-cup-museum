from __future__ import annotations

from time import perf_counter

from core.museum.contracts import AnswerResult, EvidenceSnapshot
from core.museum.store import MuseumStore


class GroundedAnswerService:
    def __init__(self, store: MuseumStore):
        self._store = store

    def answer(self, *, exhibit_id: str, question: str) -> AnswerResult:
        retrieval_started = perf_counter()
        evidence = self._store.retrieve_evidence(
            exhibit_id=exhibit_id,
            question=question,
        )
        retrieval_ms = _duration_ms(retrieval_started)
        composition_started = perf_counter()
        if evidence is None:
            return AnswerResult(
                knowledge_status="unsupported",
                spoken_text=(
                    "这件事目前不在我已审核的战国水晶杯资料里，我不能替它补一个答案。"
                    "你可以问我它的年代、材质、出土地、尺寸，或者为什么像现代玻璃杯。"
                ),
                evidence=None,
                retrieval_ms=retrieval_ms,
                composition_ms=_duration_ms(composition_started),
            )
        return AnswerResult(
            knowledge_status="grounded",
            spoken_text=self._compose_grounded_answer(evidence),
            evidence=evidence,
            retrieval_ms=retrieval_ms,
            composition_ms=_duration_ms(composition_started),
        )

    @staticmethod
    def _compose_grounded_answer(evidence: EvidenceSnapshot) -> str:
        statements = "".join(fact.statement for fact in evidence.facts)
        return (
            f"根据已审核资料，{statements}"
            "这轮我只使用了这件展品已经发布的资料，没有补充猜测。"
        )


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
