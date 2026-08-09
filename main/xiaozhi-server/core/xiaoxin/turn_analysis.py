from __future__ import annotations

import re


PREFERRED_NAME_PATTERN = re.compile(
    r"(?:我叫|叫我|称呼我(?:为|作)?)"
    r"(?P<name>[\u4e00-\u9fffA-Za-z0-9·]{1,16}?)"
    r"(?=$|[，。！？、,.!?\s]|就好|就行|好了|吧)"
)
PREFERRED_NAME_WITHDRAWAL_PATTERN = re.compile(
    r"(?:别|不要|不想)(?:再)?(?:这样|用这个名字)?(?:叫|称呼)我"
    r"|(?:别|不要|不想)(?:再)?用[^，。！？、,.!?]{1,16}称呼我"
)
USER_LIFE_EVENT_PATTERN = re.compile(
    r"我(?P<modifier>终于|已经|刚刚|刚|也)?"
    r"(?P<verb>完成了|通过了|学会了|解决了|做到了|拿到了|获得了|实现了)"
    r"(?P<detail>[^，。！？,.!?]{1,64})"
    r"(?=$|[，。！？,.!?])"
)
USER_ORIGIN_PATTERN = re.compile(
    r"(?:^|[，。！？、,.!?\s])我来自"
    r"(?P<value>[^，。！？、,.!?]{1,32})"
    r"(?=$|[，。！？、,.!?])"
)
EXPLICIT_PREFERENCE_PATTERNS = (
    re.compile(
        r"我(?:平时|通常|一般)?"
        r"(?P<polarity>不喜欢|更喜欢|喜欢|偏好)"
        r"(?P<value>[^，。！？、,.!?]{1,48})"
        r"(?=$|[，。！？、,.!?])"
    ),
    re.compile(
        r"(?:^|[，。！？、,.!?\s])(?:平时|通常|一般)"
        r"(?P<polarity>不喜欢|更喜欢|喜欢|偏好)"
        r"(?P<value>[^，。！？、,.!?]{1,48})"
        r"(?=$|[，。！？、,.!?])"
    ),
)
QUOTED_TEXT_PATTERN = re.compile(
    r"“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|\"[^\"]*\"|'[^']*'"
)
REPORTING_CONTEXT_PATTERN = re.compile(
    r"(?P<reporter>[^。！？!?；;]{0,40}?)"
    r"(?:(?<!小)说(?!明|服|法)(?:过|了)?|表示(?:过|了)?|提到(?:过|了)?|"
    r"声称(?:过|了)?|宣称(?:过|了)?|认为(?:过)?|觉得(?:过)?|"
    r"写道|写着|问(?!题|卷|号|候|世)(?:过|了)?|告诉(?:过|了)?|"
    r"发消息(?:说(?:过|了)?)?|讲(?!座|义|稿|堂)(?:过|了)?|"
    r"提及(?:过|了)?|回复(?:过|了)?|转述(?:过|了)?|"
    r"描述(?:过|了)?|强调(?:过|了)?)"
    r"[^。！？!?；;]{0,40}$"
)
SELF_REPORTER_PATTERN = re.compile(
    r"(?:^|[，,]|然后|后来|接着)"
    r"(?:今天|刚才|刚刚|之前|已经|也|又|曾经)?"
    r"(?:我本人|我|本人)"
    r"(?:刚才|刚刚|明确|亲口|也|又|曾经)?"
    r"(?:(?:对|跟|向)[^，,。！？!?；;：:]{1,16})?$"
)
REPORTED_OR_UNCERTAIN_SUFFIX_PATTERN = re.compile(
    r"(?:据说|听说|传闻|有人说|别人说|好像|可能|也许|或许)"
    r"[，,：:\s]*$"
)
CONTRAST_CONTEXT_PATTERN = re.compile(
    r"(?:^|[，,])"
    r"(?:但(?!愿)(?:是)?|不过|然而|可是|其实|实际(?:上)?)"
    r"[，,]*"
)
NON_FACTUAL_CONTEXT_PATTERN = re.compile(
    r"(?:梦到|梦见|做梦|以为|误以为|希望|期待|但愿|想象|幻想|假装|"
    r"假设|设想|假定|假想|如果|假如|要是|万一|举例|比如|例如|怀疑|"
    r"猜测|猜想|感觉|觉得|认为|担心|误认为|似乎|好像|否认|并非|不是)"
    r"[^，,。！？!?；;]{0,24}$"
)
DIRECT_NAMING_REQUEST_PREFIX_PATTERN = re.compile(
    r"(?:"
    r"(?:请(?:你)?|你可以|你就|你(?=(?:以后|今后|往后|之后))|"
    r"我(?:希望|想让|想请)(?:你)?|希望(?:你)?)?"
    r"(?:以后|今后|往后|之后|从现在(?:起|开始))?"
    r"(?:都|就|请)?"
    r")$"
)
DIRECT_ASSERTION_LEAD_PATTERN = re.compile(
    r"(?:今天|昨天|前天|刚才|刚刚|现在|最近|这次|这回|今年|上周|上个月|"
    r"对了|顺便说|另外|还有|说真的|老实说|坦白说)?$"
)
DIRECT_NON_FACTUAL_SUBJECT_PATTERN = re.compile(
    r"(?:今天|昨天|前天|刚才|刚刚|现在|最近|这次|这回|今年|上周|上个月|"
    r"对了|顺便说|另外|还有|说真的|老实说|坦白说)?"
    r"(?:我|本人)"
    r"(?:曾经|曾|一直|之前|过去|原来|一度|只是)?$"
)
INVALID_PREFERRED_NAMES = {"什么", "啥", "谁", "名字", "姓名"}
INVALID_EVENT_DETAILS = {"什么", "啥", "哪件事", "哪个目标"}
INVALID_FACT_VALUES = {"什么", "哪里", "哪儿", "啥", "哪个"}
HYPOTHETICAL_PREFIXES = ("如果", "假如", "要是", "万一")
NEGATED_NAME_PREFIXES = ("别", "不要", "不再", "别再")
MAX_DIRECT_ASSERTION_DEPTH = 16
MAX_FACT_MATCHES_PER_KIND = 32
CLAIM_LEADING_BOUNDARY_CHARS = "，,。！？!?；;：:、 \t\r\n"
RELATIONSHIP_FEEDBACK_MARKERS = (
    (
        "helpful",
        "accepted_help",
        ("你刚才的回答很有帮助", "你刚才帮到我了", "这个建议很有用"),
        "用户明确表示刚才的陪伴有帮助。",
    ),
    (
        "not_helpful",
        "interaction_feedback",
        ("你刚才没帮到我", "这个建议没用", "这次没有帮到我"),
        "用户明确表示刚才的陪伴没有帮助。",
    ),
    (
        "too_proactive",
        "interaction_feedback",
        ("你太主动了", "你刚才太主动了", "别老主动找我"),
        "用户明确表示主动陪伴过多。",
    ),
    (
        "too_personal",
        "interaction_feedback",
        ("你刚才问得太私人了", "这个问题太私人了", "你问得太私人了"),
        "用户明确表示刚才的互动过于私人。",
    ),
)

CURRENT_TURN_CORRECTION_MARKERS = (
    (
        "no_follow_up",
        (
            "这次别追问",
            "先别追问",
            "不要追问",
            "别再问了",
            "这件事翻篇",
            "这事翻篇",
            "先翻篇吧",
            "先翻篇",
            "好啦，翻篇",
            "好了，翻篇",
        ),
    ),
    (
        "concise",
        (
            "这次简短点",
            "简短一点",
            "说短点",
            "简单说",
            "这件事翻篇",
            "这事翻篇",
            "先翻篇吧",
            "先翻篇",
            "好啦，翻篇",
            "好了，翻篇",
        ),
    ),
    (
        "no_humor",
        ("这次别开玩笑", "先别开玩笑", "不要开玩笑", "别开玩笑", "别搞笑"),
    ),
    (
        "no_memory_reference",
        ("这次别提以前", "别提之前", "不要提过往", "先别提记忆"),
    ),
    (
        "settle_hardware",
        ("表情收一点", "动作收一点", "别太激动", "安静一点"),
    ),
)

STABLE_STYLE_FEEDBACK_MARKERS = (
    "平时",
    "通常",
    "一直",
    "这几次相处下来",
    "以后",
    "今后",
    "往后",
    "请记住",
    "确实喜欢",
    "还是喜欢",
)
DIRECT_STYLE_FEEDBACK_ANCHORS = (
    "喜欢你",
    "希望你",
    "你回答",
    "请你",
    "请记住",
)
STYLE_FEEDBACK_SPECS = (
    (
        "response_length",
        "short",
        "response_length",
        "decrease",
        re.compile(
            r"(?:回答[^，。！？,.!?]{0,10})?(?:短|简短|精简)一点"
            r"|(?:短|简短|精简)一点[^，。！？,.!?]{0,8}回答"
            r"|少说一点"
        ),
        "用户明确表示在日常对话中希望小芯回答更简短。",
    ),
    (
        "response_length",
        "expanded",
        "response_length",
        "increase",
        re.compile(r"(?:多|慢慢)展开(?:一点)?|回答(?:详细|长)一点"),
        "用户明确表示在日常对话中希望小芯适当展开回答。",
    ),
    (
        "question_frequency",
        "less",
        "follow_up_question",
        "decrease",
        re.compile(r"少追问|少问(?:我)?(?:一点)?|别连续(?:问|追问)|不要连续(?:问|追问)"),
        "用户明确表示在日常对话中希望小芯减少追问。",
    ),
    (
        "closure_style",
        "concise",
        "conversation_closure",
        "decrease",
        re.compile(r"(?:简洁|干脆)收尾"),
        "用户明确表示在日常对话中希望小芯简洁收尾。",
    ),
    (
        "closure_style",
        "warm",
        "conversation_closure",
        "increase",
        re.compile(r"(?:温和|温柔)收尾"),
        "用户明确表示在日常对话中希望小芯温和收尾。",
    ),
    (
        "emotional_posture",
        "supportive",
        "emotional_posture",
        "increase",
        re.compile(r"照顾(?:我的)?感受|陪我(?:慢慢)?理清感受|先陪我理清感受"),
        "用户明确表示在日常对话中希望小芯更多照顾感受。",
    ),
)
CURRENT_TURN_REPORTED_PREFIX_PATTERN = re.compile(
    r"(?:老师|朋友|室友|同学|家人|他|她|别人)"
    r"[^，。！？,.!?]{0,12}(?:说|表示|提到|认为|觉得)"
    r"[，,：:\s]*$"
)
LOW_MOOD_PATTERN = re.compile(
    r"(?:撑不住|扛不住|很难受|好难受|有点低落|情绪低落|心情很差|"
    r"脑子(?:很|还是|有点|一片)?乱|什么都不想做|不想动|不想继续|"
    r"很崩溃|快崩溃)"
)
LOW_MOOD_CONTRACT_SCOPE_MARKERS = (
    "低落时",
    "低落的时候",
    "难受时",
    "难受的时候",
    "撑不住时",
    "撑不住的时候",
)
STABLE_REQUEST_MARKERS = ("以后", "今后", "往后", "从现在起")
CONCISE_CONTRACT_MARKERS = ("少说一点", "少说点", "简短一点", "说短一点")
NO_QUESTION_CONTRACT_MARKERS = (
    "不要连续追问",
    "别连续追问",
    "不要追问",
    "别追问",
    "少问一点",
)


def companion_context(user_text: str) -> str:
    """Classify only explicit current-turn context; do not infer a personality."""

    analyzable_text = QUOTED_TEXT_PATTERN.sub(
        lambda match: " " * len(match.group(0)),
        str(user_text or ""),
    )
    for match in LOW_MOOD_PATTERN.finditer(analyzable_text):
        prefix = analyzable_text[max(0, match.start() - 24) : match.start()]
        if CURRENT_TURN_REPORTED_PREFIX_PATTERN.search(prefix):
            continue
        if any(value in prefix for value in HYPOTHETICAL_PREFIXES):
            continue
        return "user_low_mood"
    return "ordinary"


def explicit_companion_contract_requests(
    user_text: str,
) -> tuple[dict[str, str], ...]:
    """Extract direct, durable expression contracts with a narrow context scope."""

    analyzable_text = QUOTED_TEXT_PATTERN.sub(
        lambda match: " " * len(match.group(0)),
        str(user_text or ""),
    )
    if not any(marker in analyzable_text for marker in STABLE_REQUEST_MARKERS):
        return ()
    if not any(marker in analyzable_text for marker in LOW_MOOD_CONTRACT_SCOPE_MARKERS):
        return ()
    if any(value in analyzable_text[:24] for value in HYPOTHETICAL_PREFIXES):
        return ()

    requests: list[dict[str, str]] = []
    if any(marker in analyzable_text for marker in CONCISE_CONTRACT_MARKERS):
        requests.append(
            {
                "dimension": "response_length",
                "value": "short",
                "scope": "user_low_mood",
                "safe_label": "低落时回答更精简",
                "safe_scope": "低落时",
            }
        )
    if any(marker in analyzable_text for marker in NO_QUESTION_CONTRACT_MARKERS):
        requests.append(
            {
                "dimension": "question_frequency",
                "value": "never",
                "scope": "user_low_mood",
                "safe_label": "低落时不连续追问",
                "safe_scope": "低落时",
            }
        )
    return tuple(requests)


def current_turn_companion_corrections(user_text: str) -> tuple[str, ...]:
    """Extract direct, ephemeral expression corrections from the current turn."""

    analyzable_text = QUOTED_TEXT_PATTERN.sub(
        lambda match: " " * len(match.group(0)),
        str(user_text or ""),
    )
    corrections: list[str] = []
    for correction, markers in CURRENT_TURN_CORRECTION_MARKERS:
        for marker in markers:
            index = analyzable_text.find(marker)
            if index < 0:
                continue
            prefix = analyzable_text[max(0, index - 24) : index]
            if CURRENT_TURN_REPORTED_PREFIX_PATTERN.search(prefix):
                continue
            if any(value in prefix for value in HYPOTHETICAL_PREFIXES):
                continue
            corrections.append(correction)
            break
    return tuple(corrections)


EXPLICIT_NEXT_DAY_FOLLOWUP_PATTERN = re.compile(
    r"(?:你)?明天(?:你)?"
    r"(?:(?:可以|能不能|能|记得)(?:主动)?)?"
    r"(?:来)?(?:问问|问一下|关心一下)我"
    r"(?P<topic>[^，。！？、,.!?]{1,48})"
    r"(?=$|[，。！？、,.!?])"
)


def explicit_companion_feedback_signals(
    user_text: str,
) -> tuple[dict[str, object], ...]:
    """Extract explicit facts and direct feedback without inferring user state."""

    analyzable_text = QUOTED_TEXT_PATTERN.sub(
        lambda match: " " * len(match.group(0)),
        str(user_text or ""),
    )
    signals: list[dict[str, object]] = []
    relationship_feedback = _direct_relationship_feedback(analyzable_text)
    if relationship_feedback is not None:
        outcome, kind, safe_summary = relationship_feedback
        signals.append(
            {
                "kind": kind,
                "ownership_scope": "relationship",
                "content": {"outcome": outcome},
                "source_summary": safe_summary,
                "attribution": "explicit_user_feedback",
                "confidence": 1.0,
                "retention": "long_term",
                "prompt_eligible": False,
            }
        )
    signals.extend(_explicit_style_feedback_signals(analyzable_text))
    next_day_followup = _explicit_next_day_followup_request(analyzable_text)
    if next_day_followup is not None:
        signals.append(
            {
                "kind": "meaningful_moment",
                "ownership_scope": "relationship",
                "content": {
                    "outcome": "followup_worthwhile",
                    "followup_time": "next_day",
                    "topic": next_day_followup,
                },
                "source_summary": (
                    f"用户明确希望小芯明天问问自己{next_day_followup}。"
                ),
                "attribution": "explicit_user_request",
                "confidence": 1.0,
                "retention": "long_term",
                "prompt_eligible": True,
            }
        )
    preferred_name = _preferred_name(analyzable_text)
    if preferred_name is not None:
        signals.append(
            {
                "kind": "profile_fact",
                "ownership_scope": "user",
                "content": {
                    "fact_key": "preferred_name",
                    "value": preferred_name,
                },
                "source_summary": f"用户明确希望被称作{preferred_name}。",
                "attribution": "explicit_user_statement",
                "confidence": 1.0,
                "retention": "persistent",
                "prompt_eligible": True,
            }
        )
    elif _direct_name_withdrawal_match(analyzable_text) is not None:
        signals.append(
            {
                "kind": "profile_fact",
                "ownership_scope": "user",
                "content": {"fact_key": "preferred_name", "value": None},
                "source_summary": "用户明确要求不再使用之前的称呼。",
                "attribution": "explicit_user_statement",
                "confidence": 1.0,
                "retention": "persistent",
                "prompt_eligible": True,
            }
        )

    origin = _user_origin(analyzable_text)
    if origin is not None:
        signals.append(
            {
                "kind": "profile_fact",
                "ownership_scope": "user",
                "content": {"fact_key": "origin", "value": origin},
                "source_summary": f"用户明确表示自己来自{origin}。",
                "attribution": "explicit_user_statement",
                "confidence": 1.0,
                "retention": "persistent",
                "prompt_eligible": True,
            }
        )

    preference = _explicit_preference(analyzable_text)
    if preference is not None:
        value, polarity = preference
        preference_text = "不喜欢" if polarity == "dislike" else "喜欢"
        signals.append(
            {
                "kind": "explicit_preference",
                "ownership_scope": "user",
                "content": {"preference": value, "polarity": polarity},
                "source_summary": (
                    f"用户明确表示平时{preference_text}{value}。"
                ),
                "attribution": "explicit_user_statement",
                "confidence": 1.0,
                "retention": "persistent",
                "prompt_eligible": True,
            }
        )

    life_event = _user_life_event(analyzable_text)
    if life_event is not None:
        signals.append(
            {
                "kind": "user_life_event",
                "ownership_scope": "user",
                "content": {"event": life_event},
                "source_summary": f"用户明确表示自己{life_event}。",
                "attribution": "explicit_user_statement",
                "confidence": 1.0,
                "retention": "persistent",
                "prompt_eligible": True,
            }
        )
    return tuple(signals)


def _explicit_style_feedback_signals(
    user_text: str,
) -> tuple[dict[str, object], ...]:
    if not any(marker in user_text for marker in STABLE_STYLE_FEEDBACK_MARKERS):
        return ()
    if (
        _direct_relationship_feedback(user_text) is None
        and not _has_direct_style_feedback_anchor(user_text)
    ):
        return ()

    matched_by_dimension: dict[str, list[tuple[str, str, str, str]]] = {}
    for dimension, value, behavior_key, direction, pattern, summary in (
        STYLE_FEEDBACK_SPECS
    ):
        if pattern.search(user_text) is None:
            continue
        matched_by_dimension.setdefault(dimension, []).append(
            (value, behavior_key, direction, summary)
        )

    signals: list[dict[str, object]] = []
    for dimension, matches in matched_by_dimension.items():
        unique_matches = tuple(dict.fromkeys(matches))
        if len(unique_matches) != 1:
            continue
        value, behavior_key, direction, summary = unique_matches[0]
        signals.append(
            {
                "kind": "preference_feedback",
                "ownership_scope": "relationship",
                "content": {
                    "outcome": "explicit_style_preference",
                    "dimension": dimension,
                    "value": value,
                    "scope": "conversation",
                    "behavior_key": behavior_key,
                    "context_scope": "conversation",
                    "direction": direction,
                    "feedback_specificity": "behavior_and_context",
                    "source_reliability": "explicit_user_feedback",
                    "claim_context": "direct",
                    "temporal_scope": "behavior_pattern",
                },
                "source_summary": summary,
                "attribution": "explicit_user_feedback",
                "confidence": 1.0,
                "retention": "long_term",
                "prompt_eligible": True,
            }
        )
    return tuple(signals)


def _has_direct_style_feedback_anchor(user_text: str) -> bool:
    for marker in DIRECT_STYLE_FEEDBACK_ANCHORS:
        marker_index = user_text.find(marker)
        if marker_index < 0:
            continue
        sentence_start = max(
            (
                user_text.rfind(separator, 0, marker_index)
                for separator in "。！？!?；;"
            ),
            default=-1,
        )
        prefix = user_text[sentence_start + 1 : marker_index]
        if any(value in prefix for value in HYPOTHETICAL_PREFIXES):
            continue
        reporting_context = REPORTING_CONTEXT_PATTERN.search(prefix)
        if reporting_context is not None and prefix.strip() not in {
            "我觉得",
            "我认为",
            "我感觉",
            "我平时",
            "我通常",
            "我一直",
            "我确实",
            "我还是",
            "这几次相处下来，我平时还是",
        }:
            continue
        return True
    return False


def _explicit_next_day_followup_request(user_text: str) -> str | None:
    for match in EXPLICIT_NEXT_DAY_FOLLOWUP_PATTERN.finditer(user_text):
        sentence_start = max(
            (
                user_text.rfind(separator, 0, match.start())
                for separator in "。！？!?；;"
            ),
            default=-1,
        )
        prefix = user_text[sentence_start + 1 : match.start()]
        if any(value in prefix for value in HYPOTHETICAL_PREFIXES):
            continue
        if REPORTING_CONTEXT_PATTERN.search(prefix) is not None:
            continue
        topic = match.group("topic").strip().rstrip("吗吧呢呀啊")
        if topic:
            return topic
    return None


def _direct_relationship_feedback(
    user_text: str,
) -> tuple[str, str, str] | None:
    for outcome, kind, markers, safe_summary in RELATIONSHIP_FEEDBACK_MARKERS:
        for marker in markers:
            marker_index = user_text.find(marker)
            if marker_index < 0:
                continue
            suffix = user_text[marker_index + len(marker) :]
            if suffix and suffix[0] not in "，,。！？!?；;：:、 \t\r\n":
                continue
            sentence_start = max(
                (
                    user_text.rfind(separator, 0, marker_index)
                    for separator in "。！？!?；;"
                ),
                default=-1,
            )
            prefix = user_text[sentence_start + 1 : marker_index]
            if any(value in prefix for value in HYPOTHETICAL_PREFIXES):
                continue
            reporting_context = REPORTING_CONTEXT_PATTERN.search(prefix)
            if reporting_context is not None and prefix.strip() not in {
                "我觉得",
                "我认为",
                "我感觉",
            }:
                continue
            return outcome, kind, safe_summary
    return None


def _preferred_name(user_text: str) -> str | None:
    preferred_name = None
    matches = _bounded_pattern_matches(PREFERRED_NAME_PATTERN, user_text)
    if matches is None:
        return None
    for match in matches:
        if not _is_direct_user_claim(user_text, match):
            continue
        claim_text = match.group(0).lstrip(CLAIM_LEADING_BOUNDARY_CHARS)
        if not claim_text.startswith("我"):
            if not _is_explicit_naming_request(user_text, match):
                continue
        prefix = user_text[max(0, match.start() - 3) : match.start()]
        candidate = match.group("name").strip()
        if prefix.endswith(NEGATED_NAME_PREFIXES):
            continue
        if candidate in INVALID_PREFERRED_NAMES:
            continue
        preferred_name = candidate
    return preferred_name


def _is_explicit_naming_request(user_text: str, match: re.Match[str]) -> bool:
    sentence_start = max(
        (user_text.rfind(separator, 0, match.start()) for separator in "。！？!?"),
        default=-1,
    )
    compact_prefix = "".join(user_text[sentence_start + 1 : match.start()].split())
    parts = re.split(r"[，,：:、]", compact_prefix)
    local_prefix = parts[-1]
    if DIRECT_NAMING_REQUEST_PREFIX_PATTERN.fullmatch(local_prefix) is None:
        return False
    if len(parts) == 1:
        return True

    prior_context = "，".join(parts[:-1])
    if not prior_context:
        return True
    if DIRECT_ASSERTION_LEAD_PATTERN.fullmatch(prior_context) is not None:
        return True
    if _contains_prior_direct_user_fact(prior_context, depth=0):
        return True
    return _direct_name_withdrawal_match(prior_context) is not None


def _user_life_event(user_text: str) -> str | None:
    matches = _bounded_pattern_matches(USER_LIFE_EVENT_PATTERN, user_text)
    if matches is None:
        return None
    for match in matches:
        if not _is_direct_user_claim(user_text, match):
            continue
        prefix = user_text[max(0, match.start() - 4) : match.start()]
        if prefix.endswith(HYPOTHETICAL_PREFIXES):
            continue
        detail = match.group("detail").strip()
        if detail in INVALID_EVENT_DETAILS:
            continue
        return "".join(
            (
                match.group("modifier") or "",
                match.group("verb"),
                detail,
            )
        )
    return None


def _user_origin(user_text: str) -> str | None:
    matches = _bounded_pattern_matches(USER_ORIGIN_PATTERN, user_text)
    if matches is None:
        return None
    for match in matches:
        if not _is_direct_user_claim(user_text, match):
            continue
        value = match.group("value").strip()
        if value not in INVALID_FACT_VALUES:
            return value
    return None


def _explicit_preference(user_text: str) -> tuple[str, str] | None:
    for pattern in EXPLICIT_PREFERENCE_PATTERNS:
        matches = _bounded_pattern_matches(pattern, user_text)
        if matches is None:
            return None
        for match in matches:
            if not _is_direct_user_claim(user_text, match):
                continue
            value = match.group("value").strip()
            if value in INVALID_FACT_VALUES:
                continue
            polarity = "dislike" if match.group("polarity") == "不喜欢" else "like"
            return value, polarity
    return None


def _direct_match(pattern: re.Pattern[str], user_text: str) -> re.Match[str] | None:
    matches = _bounded_pattern_matches(pattern, user_text)
    if matches is None:
        return None
    return next(
        (
            match
            for match in matches
            if _is_direct_user_claim(user_text, match)
        ),
        None,
    )


def _direct_name_withdrawal_match(user_text: str) -> re.Match[str] | None:
    matches = _bounded_pattern_matches(PREFERRED_NAME_WITHDRAWAL_PATTERN, user_text)
    if matches is None:
        return None
    for match in matches:
        if not _is_direct_user_claim(user_text, match):
            continue
        if _has_direct_withdrawal_context(user_text, match):
            return match
    return None


def _has_direct_withdrawal_context(
    user_text: str,
    match: re.Match[str],
) -> bool:
    sentence_start = max(
        (user_text.rfind(separator, 0, match.start()) for separator in "。！？!?"),
        default=-1,
    )
    compact_prefix = "".join(user_text[sentence_start + 1 : match.start()].split())
    local_prefix = _local_prefix_after_direct_non_factual_contrast(compact_prefix)
    if NON_FACTUAL_CONTEXT_PATTERN.search(local_prefix):
        return False

    reporting = REPORTING_CONTEXT_PATTERN.search(compact_prefix)
    self_report = False
    if reporting is not None:
        reporter = reporting.group("reporter")
        self_report = (
            SELF_REPORTER_PATTERN.search("".join(reporter.split())) is not None
        )
    return _has_direct_assertion_context(
        local_prefix,
        match,
        self_report=self_report,
        depth=0,
    )


def _bounded_pattern_matches(
    pattern: re.Pattern[str],
    text: str,
) -> tuple[re.Match[str], ...] | None:
    matches: list[re.Match[str]] = []
    for match in pattern.finditer(text):
        matches.append(match)
        if len(matches) > MAX_FACT_MATCHES_PER_KIND:
            return None
    return tuple(matches)


def _is_direct_user_claim(
    user_text: str,
    match: re.Match[str],
    *,
    _depth: int = 0,
) -> bool:
    if _depth > MAX_DIRECT_ASSERTION_DEPTH:
        return False
    sentence_start = _sentence_start_before_match(user_text, match)
    prefix = user_text[sentence_start + 1 : match.start()]
    compact_prefix = "".join(prefix.split())
    if REPORTED_OR_UNCERTAIN_SUFFIX_PATTERN.search(compact_prefix):
        return False

    local_prefix = _local_prefix_after_direct_non_factual_contrast(compact_prefix)

    reporting = REPORTING_CONTEXT_PATTERN.search(compact_prefix)
    self_report = False
    if reporting is not None:
        reporter = reporting.group("reporter")
        self_report = (
            SELF_REPORTER_PATTERN.search("".join(reporter.split())) is not None
        )

    claim_text = match.group(0).lstrip(CLAIM_LEADING_BOUNDARY_CHARS)
    if claim_text.startswith(("我", "平时", "通常", "一般")):
        if NON_FACTUAL_CONTEXT_PATTERN.search(local_prefix):
            return False
        if not _has_direct_assertion_context(
            local_prefix,
            match,
            self_report=self_report,
            depth=_depth,
        ):
            return False

    sentence_end = len(user_text)
    for separator in "。！？!?":
        found = user_text.find(separator, match.end())
        if found >= 0:
            sentence_end = min(sentence_end, found)
    terminal = user_text[sentence_end] if sentence_end < len(user_text) else ""
    if terminal and terminal in "？?":
        return False
    return True


def _local_prefix_after_direct_non_factual_contrast(
    compact_prefix: str,
) -> str:
    local_prefix = compact_prefix
    for contrast in CONTRAST_CONTEXT_PATTERN.finditer(compact_prefix):
        prior_context = compact_prefix[: contrast.start()]
        non_factual = NON_FACTUAL_CONTEXT_PATTERN.search(prior_context)
        if non_factual is None:
            continue
        if DIRECT_NON_FACTUAL_SUBJECT_PATTERN.fullmatch(
            prior_context[: non_factual.start()]
        ) is None:
            continue
        local_prefix = compact_prefix[contrast.end() :]
    return local_prefix


def _sentence_start_before_match(
    user_text: str,
    match: re.Match[str],
) -> int:
    if match.group(0)[:1] in "。！？!?":
        return match.start()
    return max(
        (user_text.rfind(separator, 0, match.start()) for separator in "。！？!?"),
        default=-1,
    )


def _has_direct_assertion_context(
    compact_prefix: str,
    match: re.Match[str],
    *,
    self_report: bool,
    depth: int,
) -> bool:
    if self_report or not compact_prefix:
        return True
    if DIRECT_ASSERTION_LEAD_PATTERN.fullmatch(compact_prefix) is not None:
        return True

    claim_text = match.group(0)
    if claim_text[:1] in "，,：:、\t\r\n ":
        prior_context = compact_prefix
        clause_prefix = ""
    else:
        parts = re.split(r"[，,：:、]", compact_prefix)
        if len(parts) == 1:
            return False
        prior_context = "，".join(parts[:-1])
        clause_prefix = parts[-1]
    if DIRECT_ASSERTION_LEAD_PATTERN.fullmatch(clause_prefix) is None:
        return False
    return _contains_prior_direct_user_fact(prior_context, depth=depth)


def _contains_prior_direct_user_fact(text: str, *, depth: int) -> bool:
    patterns = (
        PREFERRED_NAME_PATTERN,
        USER_ORIGIN_PATTERN,
        USER_LIFE_EVENT_PATTERN,
        *EXPLICIT_PREFERENCE_PATTERNS,
    )
    candidates: list[re.Match[str]] = []
    for pattern in patterns:
        matches = _bounded_pattern_matches(pattern, text)
        if matches is None:
            return False
        for prior_match in matches:
            claim_text = prior_match.group(0).lstrip(CLAIM_LEADING_BOUNDARY_CHARS)
            if not claim_text.startswith(("我", "平时", "通常", "一般")):
                continue
            candidates.append(prior_match)
    if not candidates:
        return False
    latest = max(candidates, key=lambda item: (item.end(), item.start()))
    if text[latest.end() :].strip("，,：: "):
        return False
    return _is_direct_user_claim(text, latest, _depth=depth + 1)
