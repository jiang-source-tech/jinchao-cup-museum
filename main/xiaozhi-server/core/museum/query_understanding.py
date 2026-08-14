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
    answer_depth: str = "standard"

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
        if self.answer_depth not in {"standard", "detailed"}:
            raise ValueError(f"unsupported answer depth: {self.answer_depth}")


_SOCIAL_IDENTITY_TERMS = (
    "你是谁",
    "你能做什么",
)
_SOCIAL_CAPABILITY_TERMS = (
    "你能帮我做什么",
    "你可以帮我做什么",
    "能帮我做什么",
)
_SOCIAL_GREETINGS = {
    "你好",
    "您好",
    "早上好",
    "上午好",
    "下午好",
    "晚上好",
    "嗨",
    "哈喽",
    "hello",
    "hi",
    "在吗",
}
_SOCIAL_ADDRESSES = {"讲解员", "导览员", "讲解助手", "助手"}
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

_DETAILED_EXPLANATION_TERMS = (
    "详细介绍",
    "详细讲解",
    "详细说说",
    "说详细点",
    "讲详细点",
    "细说一下",
    "多讲一点",
    "多说一点",
    "多介绍一些",
    "内容多一点",
    "不要太简略",
    "别太简短",
    "展开讲",
    "展开说",
    "全面介绍",
    "全面讲解",
    "完整介绍",
    "完整讲解",
    "深入讲",
    "仔细讲",
    "来历和特点",
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
            "怎么织出来",
            "怎样织出来",
            "如何织出来",
            "怎么织成",
            "怎样织成",
            "如何织成",
            "上面刻了什么",
            "上面刻着什么",
            "刻了什么",
            "刻着什么",
            "写了什么",
            "什么字",
            "为什么说没有做完",
            "为什么没做完",
            "是不是半成品",
            "未完工",
            "半成品",
            "制作工艺",
            "工艺流程",
            "制作方法",
            "如何加工",
            "怎么加工",
            "怎样加工",
            "咋加工",
            "掏空",
            "磨光",
            "抛光",
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
            "哪种矿物",
            "什么矿物",
            "矿物",
            "材质",
            "材料",
        ),
    ),
    (
        "usage",
        (
            "戴在身体哪个位置",
            "戴在身体哪里",
            "原来戴在哪",
            "佩戴在哪里",
            "佩戴位置",
            "有什么用",
            "干什么用",
            "用来干什么",
            "拿来",
            "什么用途",
            "什么作用",
            "什么场合穿",
            "什么时候穿",
            "谁穿的",
            "干什么的",
            "承托什么",
            "用来承托",
            "储物用",
            "日常储物",
            "陪葬用",
            "陪葬器",
            "代表权力",
            "代表了主人的权力",
            "象征权力",
            "权力象征",
            "用途",
            "作用",
            "权力",
            "佩戴",
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
            "为什么像",
            "为什么看上去",
            "透明",
            "透亮",
            "通透",
        ),
    ),
    (
        "overview",
        (
            "有什么特点",
            "有什么特别",
            "有什么看点",
            "介绍一下",
            "讲讲",
            "讲解",
            "介绍",
            "概况",
            "什么是",
            "特点",
            "这是什么",
            "给我讲讲",
            "说说",
        ),
    ),
)

_FACT_TYPES_BY_INTENT = {
    "dimensions": ("dimensions",),
    "excavation": ("excavation",),
    "craft": ("craft", "research_limit"),
    "material": ("material",),
    "era": ("era",),
    "appearance": ("appearance",),
    "usage": ("usage",),
    "price": ("price",),
    "history": ("history",),
}

_RETRIEVAL_TERMS_BY_INTENT = {
    # Normalize colloquial ways of asking about making an object into the
    # vocabulary used by published facts.
    "craft": ("工艺", "制作", "怎么做"),
    "material": ("材质",),
    "usage": ("用途",),
    "history": ("公开名称", "登记"),
}

_COLLOQUIAL_INTENT_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("price", ("多少钱", "价格", "售价", "卖了多少钱")),
    ("dimensions", ("多大", "大小", "尺寸", "尺寸是多少", "多高", "口径")),
    (
        "excavation",
        ("从哪儿找到", "在哪里发现", "挖出来", "出土", "出徒", "出图"),
    ),
    ("craft", ("怎么弄出来", "怎么做出来", "制作手法", "如何加工", "怎么加工")),
    ("material", ("是什么材质", "是什么东西做的", "用的是什么料子", "什么原料", "什么材料")),
    ("usage", ("以前拿来做什么", "原本派什么用场", "是干嘛的", "做什么用", "有什么用")),
    ("era", ("大概是哪会儿", "距今多久", "属于哪个年代", "哪个时期", "什么年代")),
    ("appearance", ("看起来有什么特征", "长得怎么样", "造型", "外形")),
    ("history", ("公开叫什么", "公开名称", "登记的是什么", "馆方藏品中登记")),
)


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

    colloquial_matches = [
        (len(term), intent, (term,))
        for intent, terms in _COLLOQUIAL_INTENT_HINTS
        for term in terms
        if term in question
    ]
    if colloquial_matches and not any(
        cue in normalized for cue in _COMPOUND_INTENT_CUES
    ):
        best_length, fine_intent, query_terms = max(
            colloquial_matches,
            key=lambda item: item[0],
        )
        return QuestionUnderstanding(
            coarse_intent="exhibit_knowledge",
            fine_intent=fine_intent,
            fact_types=_FACT_TYPES_BY_INTENT.get(fine_intent, ()),
            query_terms=tuple(dict.fromkeys(
                (*query_terms, *_RETRIEVAL_TERMS_BY_INTENT.get(fine_intent, ()))
            )),
            confidence=min(0.95, 0.72 + best_length / 100),
            answer_depth=_answer_depth(normalized),
        )

    matches: list[tuple[int, str, tuple[str, ...]]] = list(colloquial_matches)
    for fine_intent, terms in _INTENT_RULES:
        matched_terms = tuple(term for term in terms if term in normalized)
        matched_lengths = [len(term) for term in matched_terms]
        if matched_lengths:
            matches.append((max(matched_lengths), fine_intent, matched_terms))

    if not matches:
        if not _looks_like_question_or_request(normalized):
            return QuestionUnderstanding(
                coarse_intent="unclear",
                fine_intent="unknown",
                confidence=0.75,
                answer_depth=_answer_depth(normalized),
            )
        return QuestionUnderstanding(
            coarse_intent="exhibit_knowledge",
            fine_intent="unknown",
            confidence=0.35,
            answer_depth=_answer_depth(normalized),
        )

    specific_matches = [match for match in matches if match[1] != "overview"]
    best_length, fine_intent, query_terms = max(
        specific_matches or matches,
        key=lambda item: item[0],
    )
    compound_matches = _compound_intent_matches(normalized, specific_matches)
    if compound_matches:
        fact_types = tuple(dict.fromkeys(
            fact_type
            for _length, intent, _terms in compound_matches
            for fact_type in _FACT_TYPES_BY_INTENT.get(intent, ())
        ))
        query_terms = tuple(dict.fromkeys(
            term
            for _length, intent, terms in compound_matches
            for term in (*terms, *_RETRIEVAL_TERMS_BY_INTENT.get(intent, ()))
        ))
    else:
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
        answer_depth=_answer_depth(normalized),
    )


def _normalize_text(value: str) -> str:
    return re.sub(r"[\s，。！？、；：,.!?;:]", "", value).lower()


def _answer_depth(normalized: str) -> str:
    return (
        "detailed"
        if any(term in normalized for term in _DETAILED_EXPLANATION_TERMS)
        else "standard"
    )


def _is_social_question(normalized: str) -> bool:
    if normalized in _SOCIAL_GREETINGS:
        return True
    if any(
        normalized in {f"{greeting}{address}", f"{address}{greeting}"}
        for greeting in _SOCIAL_GREETINGS
        for address in _SOCIAL_ADDRESSES
    ):
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


_QUESTION_LIKE_TERMS = (
    "什么",
    "怎么",
    "怎样",
    "如何",
    "为什么",
    "为何",
    "哪里",
    "哪儿",
    "哪",
    "哪个",
    "哪种",
    "谁",
    "何时",
    "何地",
    "多少",
    "几",
    "是否",
    "是不是",
    "能不能",
    "能否",
    "可不可以",
    "有没有",
    "有无",
    "还是",
    "请",
    "我想知道",
    "我想了解",
    "告诉我",
    "介绍",
    "讲讲",
    "说说",
)


def _looks_like_question_or_request(normalized: str) -> bool:
    if any(term in normalized for term in _QUESTION_LIKE_TERMS):
        return True
    return normalized.endswith(("吗", "呢", "吧", "呀", "啊", "嘛"))


_COMPOUND_INTENT_CUES = ("又", "还", "以及", "同时", "并且", "分别")


def _compound_intent_matches(
    normalized: str,
    matches: list[tuple[int, str, tuple[str, ...]]],
) -> list[tuple[int, str, tuple[str, ...]]]:
    if not any(cue in normalized for cue in _COMPOUND_INTENT_CUES):
        return []
    distinct_intents = {match[1] for match in matches}
    return matches if len(distinct_intents) >= 2 else []
