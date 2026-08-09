from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


QUERY_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]+")


class KnowledgeBase:
    def __init__(self, knowledge_dir: str | Path):
        self.knowledge_dir = Path(knowledge_dir)

    def load_json(self, name: str) -> dict[str, Any] | list[Any]:
        path = self.knowledge_dir / name
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def grounding_context(
        self, user_text: str, route: dict[str, Any]
    ) -> dict[str, str] | None:
        if route.get("reply_mode") != "knowledge_grounded":
            return None

        query = user_text or ""
        chunks: list[str] = []
        for filename in (
            "campus_life.json",
            "campus_directory.json",
            "student_affairs_qa.json",
            "college_companion_facts.json",
        ):
            data = self.load_json(filename)
            chunks.extend(_collect_matching_strings(data, query))

        facts = "\n".join(dict.fromkeys(chunk for chunk in chunks if chunk))
        if not facts:
            facts = "本地知识库没有命中可靠事实。"
        return {
            "facts": facts,
            "preferred_fallback": "我这里没有可靠资料，别让我瞎编；你可以看学院官网、官方通知或问辅导员确认。",
        }


def _collect_matching_strings(value: Any, query: str) -> list[str]:
    query_terms = _extract_query_terms(query)
    matches: list[tuple[int, str]] = []
    _walk_value(value, query_terms, matches)
    matches.sort(key=lambda item: item[0], reverse=True)

    deduped: list[str] = []
    seen: set[str] = set()
    for _, text in matches:
        if text in seen:
            continue
        deduped.append(text)
        seen.add(text)
        if len(deduped) >= 20:
            break
    return deduped


def _walk_value(
    value: Any, query_terms: list[str], matches: list[tuple[int, str]]
) -> None:
    if isinstance(value, dict):
        record_text = _record_to_text(value)
        score = _match_score(record_text, query_terms)
        if score:
            matches.append((score, record_text))
        for item in value.values():
            _walk_value(item, query_terms, matches)
        return

    if isinstance(value, list):
        for item in value:
            _walk_value(item, query_terms, matches)
        return

    if isinstance(value, str):
        score = _match_score(value, query_terms)
        if score:
            matches.append((score, value))


def _record_to_text(record: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("question", "answer", "name", "title", "location", "office"):
        value = record.get(key)
        if isinstance(value, str) and value:
            parts.append(value.strip())

    keywords = record.get("keywords")
    if isinstance(keywords, list):
        keyword_text = "、".join(str(item).strip() for item in keywords if str(item).strip())
        if keyword_text:
            parts.append(f"关键词：{keyword_text}")

    if not parts:
        scalar_parts = [
            str(item).strip()
            for item in record.values()
            if isinstance(item, (str, int, float)) and str(item).strip()
        ]
        parts.extend(scalar_parts)

    return "\n".join(parts)


def _extract_query_terms(query: str) -> list[str]:
    clean = (query or "").strip()
    if not clean:
        return []

    terms: list[str] = []
    for token in QUERY_TOKEN_PATTERN.findall(clean):
        normalized = token.strip()
        if len(normalized) >= 2:
            terms.append(normalized)

    marker_terms = (
        "学工办",
        "信电学工办",
        "心理咨询",
        "心理中心",
        "心理预约",
        "预约",
        "辅导员",
        "教学办",
        "食堂",
        "北秀",
        "南秀",
    )
    terms.extend(term for term in marker_terms if term in clean)

    # Prefer longer, more specific terms first.
    return sorted(set(terms), key=len, reverse=True)


def _match_score(text: str, query_terms: list[str]) -> int:
    if not text or not query_terms:
        return 0
    return sum(1 for term in query_terms if term in text)
