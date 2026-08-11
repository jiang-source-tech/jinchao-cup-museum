from __future__ import annotations

import json
import re

from core.museum.contracts import ExhibitResolution
from core.museum.store import MuseumStore


class ExhibitResolver:
    """Resolve a spoken exhibit reference without allowing model guessing."""

    def __init__(self, store: MuseumStore):
        self._store = store

    def resolve(
        self,
        *,
        question: str,
        current_exhibit_id: str | None,
    ) -> ExhibitResolution:
        exhibits = self._store.active_exhibits()
        normalized_question = _normalize_text(question)
        matches: list[tuple[str, str, str, int, int]] = []
        for exhibit_id, exhibit_name, aliases_json in exhibits:
            names = [(exhibit_name, 1)]
            names.extend((alias, 0) for alias in json.loads(aliases_json))
            for mention, canonical_score in names:
                normalized_mention = _normalize_text(mention)
                if not normalized_mention or normalized_mention not in normalized_question:
                    continue
                matches.append(
                    (
                        exhibit_id,
                        exhibit_name,
                        mention,
                        len(normalized_mention),
                        canonical_score,
                    )
                )

        if matches:
            candidate_ids = tuple(dict.fromkeys(match[0] for match in matches))
            if len(candidate_ids) > 1:
                return ExhibitResolution(
                    status="ambiguous",
                    matched_text=None,
                    candidate_ids=candidate_ids,
                    context_source="ambiguous",
                )
            best = max(matches, key=lambda match: (match[3], match[4]))
            return ExhibitResolution(
                status="explicit",
                exhibit_id=best[0],
                exhibit_name=best[1],
                matched_text=best[2],
                candidate_ids=candidate_ids,
                context_source="explicit_mention",
            )

        if _looks_like_new_exhibit_reference(
            normalized_question,
            current_exhibit_id=current_exhibit_id,
        ):
            return ExhibitResolution(
                status="not_found",
                matched_text=_unknown_reference_text(normalized_question),
                context_source="not_found",
            )

        if current_exhibit_id:
            for exhibit_id, exhibit_name, _aliases_json in exhibits:
                if exhibit_id == current_exhibit_id:
                    return ExhibitResolution(
                        status="inherited",
                        exhibit_id=exhibit_id,
                        exhibit_name=exhibit_name,
                        candidate_ids=(exhibit_id,),
                        context_source="inherited_session",
                    )

        return ExhibitResolution(status="missing", context_source="missing")


def _normalize_text(value: str) -> str:
    return re.sub(r"[\s，。！？、；：,.!?;:]", "", value).lower()


_PRONOUN_PREFIXES = (
    "它",
    "这件",
    "这个",
    "这把",
    "这只",
    "这枚",
    "该展品",
    "刚才那个杯子",
    "刚才那件展品",
    "前面那个杯子",
    "前面那件展品",
)
_QUESTION_MARKERS = (
    "是什么",
    "是啥",
    "为什么",
    "为何",
    "怎么",
    "如何",
    "哪里",
    "哪儿",
    "哪個",
    "的材质",
    "的材料",
    "的年代",
    "的历史",
    "的工艺",
    "的制作",
    "的价格",
    "的尺寸",
    "的外形",
    "的出土地",
)
_SWITCH_CUES = ("换成", "换到", "改问", "改聊", "另一个", "另一件", "另外一件", "关于")
_POLITE_PREFIXES = ("请问", "我想知道", "我想了解", "能不能告诉我", "能否告诉我")
_FOLLOW_UP_REFERENCE_PHRASES = (
    "刚才那个杯子",
    "刚才那件展品",
    "前面那个杯子",
    "前面那件展品",
)
_CONTEXTUAL_OPENERS = ("这么", "那么", "刚才", "刚刚", "前面")
_FOLLOW_UP_PRONOUNS = ("它", "这个", "这件", "这把", "这只", "这枚")
_FOLLOW_UP_DIALOGUE_PREFIXES = (
    "你能讲讲",
    "能说说",
    "说说",
    "请介绍",
    "请问",
    "我想了解",
    "我想知道",
    "介绍一下",
    "讲讲",
)


def _looks_like_new_exhibit_reference(
    question: str,
    current_exhibit_id: str | None = None,
) -> bool:
    """Detect likely exhibit switching without guessing from arbitrary nouns."""
    for cue in _SWITCH_CUES:
        if cue not in question:
            continue
        tail = question.split(cue, 1)[1]
        if tail and not tail.startswith(_PRONOUN_PREFIXES):
            return True

    if question.startswith(_PRONOUN_PREFIXES) or question.startswith(_QUESTION_MARKERS):
        return False
    if current_exhibit_id and any(
        phrase in question for phrase in _FOLLOW_UP_REFERENCE_PHRASES
    ):
        return False
    if current_exhibit_id and question.startswith(_CONTEXTUAL_OPENERS):
        if any(pronoun in question for pronoun in _FOLLOW_UP_PRONOUNS):
            return False
    if current_exhibit_id and question.startswith(_FOLLOW_UP_DIALOGUE_PREFIXES):
        if any(pronoun in question for pronoun in _FOLLOW_UP_PRONOUNS):
            return False
    marker_positions = [question.find(marker) for marker in _QUESTION_MARKERS]
    marker_positions = [position for position in marker_positions if position > 1]
    if not marker_positions:
        return False
    subject = question[: min(marker_positions)]
    subject = _strip_polite_prefix(subject)
    return len(subject) >= 2


def _unknown_reference_text(question: str) -> str | None:
    for cue in _SWITCH_CUES:
        if cue in question:
            tail = question.split(cue, 1)[1]
            if tail and not tail.startswith(_PRONOUN_PREFIXES):
                return tail[:24]
    for marker in _QUESTION_MARKERS:
        if marker in question:
            subject = question.split(marker, 1)[0]
            subject = _strip_polite_prefix(subject)
            if len(subject) >= 2:
                return subject[-24:]
    return None


def _strip_polite_prefix(value: str) -> str:
    for prefix in _POLITE_PREFIXES:
        if value.startswith(prefix):
            return value[len(prefix):]
    return value
