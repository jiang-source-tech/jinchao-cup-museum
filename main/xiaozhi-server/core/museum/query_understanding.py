from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class QuestionUnderstanding:
    """A bounded, auditable interpretation of one visitor question."""

    coarse_intent: str
    fine_intent: str
    fact_types: tuple[str, ...] = ()
    query_terms: tuple[str, ...] = ()
    confidence: float = 0.0
    source: str = "rules"

    def __post_init__(self) -> None:
        if self.coarse_intent not in {
            "social",
            "exhibit_knowledge",
            "comparison",
            "unsupported",
            "unclear",
        }:
            raise ValueError(f"unsupported coarse intent: {self.coarse_intent}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("question understanding confidence must be between 0 and 1")


_SOCIAL_IDENTITY_TERMS = (
    "你是谁",
    "你能做什么",
)
_SOCIAL_CAPABILITY_TERMS = (
    "你能帮我做什么",
    "你可以帮我做什么",
    "能帮我做什么",
)
_SOCIAL_GREETINGS = {"你好", "您好", "嗨", "哈喽", "hello", "hi", "在吗"}
_SOCIAL_THANKS = ("谢谢", "感谢", "多谢", "辛苦了")
_SOCIAL_FAREWELLS = ("再见", "拜拜", "回头见", "下次见")

_COMPARISON_TERMS = (
    "比较",
    "区别",
    "不同",
    "相比",
    "哪个更",
    "和它比",
    "跟它比",
)

# Ordered from specific phrases to broad words so a longer phrase wins.
_INTENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "price",
        ("多少钱", "价格", "售价", "卖了多少", "值多少钱", "市场价"),
    ),
    (
        "dimensions",
        ("有多高", "多高", "多大", "尺寸", "口径", "底径", "厘米", "大小"),
    ),
    (
        "excavation",
        ("在哪里出土", "哪里出土", "出土地点", "出土", "发现在哪里", "发现"),
    ),
    (
        "craft",
        (
            "怎么做出来",
            "怎样做出来",
            "如何做出来",
            "咋做出来",
            "怎么把它做出来",
            "怎样把它做出来",
            "如何把它做出来",
            "咋把它做出来",
            "怎么制作",
            "怎样制作",
            "如何制作",
            "咋制作",
            "怎么绣出来",
            "怎样绣出来",
            "如何绣出来",
            "怎么绣制",
            "怎样绣制",
            "如何绣制",
            "制作工艺",
            "工艺流程",
            "制作方法",
            "如何加工",
            "怎么加工",
            "怎样加工",
            "咋加工",
            "怎么做",
            "制作",
            "工艺",
            "加工",
        ),
    ),
    (
        "material",
        (
            "什么材质",
            "什么材料",
            "用什么做",
            "拿什么做",
            "用啥做",
            "拿啥做",
            "什么做成",
            "是什么做的",
            "是水晶吗",
            "材质",
            "材料",
        ),
    ),
    (
        "era",
        ("多少年历史", "有多久历史", "什么时候的", "哪个时期", "年代", "历史", "时期"),
    ),
    (
        "appearance",
        (
            "长什么样",
            "什么样子",
            "外形",
            "样子",
            "看起来",
            "为什么像",
            "透明",
            "透亮",
            "通透",
        ),
    ),
    (
        "overview",
        ("有什么特点", "有什么特别", "有什么看点", "介绍一下", "讲讲", "介绍", "概况"),
    ),
)

_FACT_TYPES_BY_INTENT = {
    "dimensions": ("dimensions",),
    "excavation": ("excavation",),
    "craft": ("craft", "research_limit"),
    "material": ("material",),
    "era": ("era",),
    "appearance": ("appearance",),
    "price": ("price",),
}

_RETRIEVAL_TERMS_BY_INTENT = {
    # Normalize colloquial ways of asking about making an object into the
    # vocabulary used by the published research-limit fact.
    "craft": ("工艺", "制作", "怎么做"),
}


def understand_question(question: str) -> QuestionUnderstanding:
    normalized = _normalize_text(question)
    if not normalized:
        return QuestionUnderstanding(
            coarse_intent="unclear",
            fine_intent="unknown",
            confidence=1.0,
        )

    if _is_social_question(normalized):
        return QuestionUnderstanding(
            coarse_intent="social",
            fine_intent="social",
            confidence=0.95,
        )

    if any(term in normalized for term in _COMPARISON_TERMS):
        return QuestionUnderstanding(
            coarse_intent="comparison",
            fine_intent="comparison",
            confidence=0.92,
        )

    matches: list[tuple[int, str, tuple[str, ...]]] = []
    for fine_intent, terms in _INTENT_RULES:
        matched_terms = tuple(term for term in terms if term in normalized)
        matched_lengths = [len(term) for term in matched_terms]
        if matched_lengths:
            matches.append((max(matched_lengths), fine_intent, matched_terms))

    if not matches:
        return QuestionUnderstanding(
            coarse_intent="exhibit_knowledge",
            fine_intent="unknown",
            confidence=0.35,
        )

    specific_matches = [match for match in matches if match[1] != "overview"]
    best_length, fine_intent, query_terms = max(
        specific_matches or matches,
        key=lambda item: item[0],
    )
    fact_types = _FACT_TYPES_BY_INTENT.get(fine_intent, ())
    retrieval_terms = tuple(
        dict.fromkeys(
            (*query_terms, *_RETRIEVAL_TERMS_BY_INTENT.get(fine_intent, ()))
        )
    )
    return QuestionUnderstanding(
        coarse_intent="exhibit_knowledge",
        fine_intent=fine_intent,
        fact_types=fact_types,
        query_terms=retrieval_terms,
        confidence=min(0.99, 0.70 + (best_length / 100)),
    )


def _normalize_text(value: str) -> str:
    return re.sub(r"[\s，。！？、；：,.!?;:]", "", value).lower()


def _is_social_question(normalized: str) -> bool:
    if normalized in _SOCIAL_GREETINGS:
        return True
    if any(term in normalized for term in _SOCIAL_CAPABILITY_TERMS):
        return True
    if any(term in normalized for term in _SOCIAL_IDENTITY_TERMS):
        return True
    if len(normalized) <= 12 and any(term in normalized for term in _SOCIAL_THANKS):
        return True
    return len(normalized) <= 12 and any(
        term in normalized for term in _SOCIAL_FAREWELLS
    )
