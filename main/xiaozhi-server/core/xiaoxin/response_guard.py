from __future__ import annotations

import re

from . import boundary_guard as guard


_CAMPUS_CONTEXT_TERMS = (
    "图书馆",
    "借书证",
    "食堂",
    "宿舍",
    "辅导员",
    "学工办",
    "教务",
    "校园卡",
)
_MEMORY_WRITE_CLAIM_PATTERN = re.compile(
    r"(?:已经|已|稳稳)?(?:存进|存入|写入|放进|记到)(?:了)?"
    r"(?:你的|小芯的)?(?:长期记忆|永久记忆|记忆区域)"
    r"|(?:长期记忆|永久记忆)(?:已经|已)(?:保存|写入|生效)"
)
_INTERNAL_MEMORY_MECHANICS_PATTERN = re.compile(
    r"(?:记忆|长期记忆)(?:写入|保存)(?:是|会)?异步"
    r"|异步(?:写入|保存|处理)(?:记忆)?"
    r"|(?:系统|后台)(?:提交|确认|处理)(?:记忆|写入)"
)
_FUTURE_PLAN_PATTERN = re.compile(
    r"(?:准备|计划|打算|将要|之后会|明天(?:要|去|准备)|"
    r"今天[^。！？]{0,24}(?:要去|要办|要复习)|(?:下午|晚上)(?:要|准备))"
)
_COMPLETED_ACTION_PATTERN = re.compile(
    r"(?:已经|都|其实)?[^。！？]{0,32}"
    r"(?:办了|复习了|去了|做完了|完成了|参加了|提交了|拿到了)"
)
_MEMORY_PREMISE_INTRO_PATTERN = re.compile(
    r"(?:你之前说|你说过|你还记得|你记得)(?:我|我的)"
)
_MEMORY_PREMISE_CLAIM_PATTERNS = (
    re.compile(r"(?:最近|现在|当前)?(?:在)?(?:准备|推进|做)([^，。！？、]{2,32})"),
    re.compile(r"(?:通常)?(?:喜欢|习惯)([^，。！？、]{2,32})"),
)


def is_fragmented_reply(reply: str) -> bool:
    clean = (reply or "").strip()
    return not clean or clean.endswith(("，", "、", "但", "因为", "如果"))


def reply_exceeds_knowledge_scope(
    route: dict,
    reply: str,
    knowledge_context: dict | None,
    *,
    user_text: str = "",
) -> bool:
    should_guard = route.get("reply_mode") == "knowledge_grounded" or any(
        term in (user_text or "") for term in _CAMPUS_CONTEXT_TERMS
    )
    if not should_guard:
        return False
    clean = guard.strip_expression(reply) if hasattr(guard, "strip_expression") else reply
    facts = str((knowledge_context or {}).get("facts", ""))
    unsupported_terms = (
        "身份证",
        "学生证",
        "办公时间",
        "营业时间",
        "价格",
        "电话",
        "联系方式",
        "源文件",
    )
    uncertainty_markers = ("没有写明", "没有可靠", "未说明", "不清楚", "查不到")
    for term in unsupported_terms:
        if term not in clean:
            continue
        if term in (user_text or ""):
            continue
        if term not in facts:
            return True
        if any(marker in facts for marker in uncertainty_markers) and not any(
            marker in clean for marker in uncertainty_markers
        ):
            return True
    return False


def reply_claims_unconfirmed_memory_write(reply: str) -> bool:
    """Reject persistence claims made before the async memory commit succeeds."""

    return _MEMORY_WRITE_CLAIM_PATTERN.search(reply or "") is not None


def reply_exposes_internal_memory_mechanics(reply: str) -> bool:
    return _INTERNAL_MEMORY_MECHANICS_PATTERN.search(reply or "") is not None


def reply_changes_future_plan_to_completed(user_text: str, reply: str) -> bool:
    """Reject replies that turn a stated future plan into a completed event."""

    if _FUTURE_PLAN_PATTERN.search(user_text or "") is None:
        return False
    for match in _COMPLETED_ACTION_PATTERN.finditer(reply or ""):
        prefix = (reply or "")[max(0, match.start() - 4) : match.start()]
        if not any(marker in prefix for marker in ("如果", "等", "等到", "以后")):
            return True
    return False


def reply_exceeds_question_budget(reply: str, question_budget: int) -> bool:
    if question_budget < 0:
        return True
    return sum((reply or "").count(marker) for marker in ("?", "？")) > question_budget


def memory_premise_is_unsupported(
    user_text: str,
    prompt_context: tuple[str, ...],
) -> bool:
    """Reject claimed prior memories that current-subject evidence does not support."""

    intro = _MEMORY_PREMISE_INTRO_PATTERN.search(user_text or "")
    if intro is None:
        return False
    premise_text = (user_text or "")[intro.end() :]
    claims: list[str] = []
    for pattern in _MEMORY_PREMISE_CLAIM_PATTERNS:
        for match in pattern.finditer(premise_text):
            value = match.group(1).strip()
            qualifier = premise_text[max(0, match.start() - 4) : match.start()]
            if any(
                marker in f"{qualifier}{value}"
                for marker in ("什么", "怎么", "哪", "是否", "几")
            ):
                continue
            claims.append(value)
    if not claims:
        return False
    normalized_context = re.sub(r"\s+", "", "\n".join(prompt_context))
    return any(
        re.sub(r"\s+", "", claim).rstrip("吗呢吧呀来着") not in normalized_context
        for claim in claims
    )
