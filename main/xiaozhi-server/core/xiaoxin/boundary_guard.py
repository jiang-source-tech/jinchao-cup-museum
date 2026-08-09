from __future__ import annotations

import re


EXPRESSION_PATTERN = re.compile(
    r"\\?\[(smile|soft_smile|cheer|think|proud|wink|wave|surprise|love|sweat|sad)\\?\]"
)


def contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    clean = text or ""
    return any(phrase in clean for phrase in phrases)


def strip_expression(text: str) -> str:
    return EXPRESSION_PATTERN.sub("", text or "").strip()


def classify_message(user_text: str) -> str:
    text = user_text or ""

    crisis_markers = (
        "不想活",
        "想死",
        "自杀",
        "轻生",
        "活不下去",
        "伤害自己",
    )
    if contains_any(text, crisis_markers):
        return "crisis"

    sensitive_people = (
        "老师",
        "辅导员",
        "学长",
        "学姐",
        "同学",
        "负责人",
        "教师",
    )
    contact_markers = (
        "帮我联系",
        "替我联系",
        "联系老师",
        "联系学长",
        "联系学姐",
        "要一下电话",
        "要电话",
        "联系方式",
        "打电话给",
        "呼叫",
        "连线",
    )
    if contains_any(text, contact_markers) and contains_any(text, sensitive_people):
        return "official_contact"

    drafting_verbs = (
        "帮我写",
        "帮我起草",
        "写一段",
        "写个",
        "写一封",
        "写条",
        "润色",
        "整理成",
    )
    drafting_formats = (
        "消息",
        "短信",
        "微信",
        "邮件",
        "申请",
        "申请文本",
        "文本",
        "请假条",
        "回复",
        "内容",
    )
    drafting_targets = ("发给", "给老师", "给辅导员", "给学长", "给学姐")
    if contains_any(text, drafting_verbs) and (
        contains_any(text, drafting_formats)
        or (
            contains_any(text, sensitive_people)
            and contains_any(text, drafting_targets)
        )
    ):
        return "message_drafting"

    competition_domain_markers = (
        "竞赛",
        "智能车",
        "电子设计",
        "比赛",
    )
    restricted_resource_markers = (
        "联系学长",
        "联系学姐",
        "源文件",
        "私下资料",
        "内部资料",
        "资料",
        "代码",
    )
    if contains_any(text, competition_domain_markers) and contains_any(
        text,
        restricted_resource_markers,
    ):
        return "competition_resources"

    campus_knowledge_markers = (
        "食堂",
        "北秀",
        "南秀",
        "爱城院",
        "城院通",
        "打印",
        "热水",
        "空调",
        "专业",
        "课程群",
        "辅导员",
        "实验室",
        "学工办",
        "教学办",
        "心理咨询",
        "心理中心",
        "心理预约",
        "预约",
    )
    if contains_any(text, campus_knowledge_markers):
        return "campus_knowledge"

    return "open_chat"


def _template_reply_with_expression_tags(user_text: str) -> str | None:
    category = classify_message(user_text)

    if category == "crisis":
        return (
            "你现在的安全最重要。请先不要一个人扛着，马上联系你身边信任的人陪你；"
            "如果已经有伤害自己的风险，请立刻拨打 120 或 110 求助；"
            "也可以马上去学校值班点、医院急诊或找辅导员当面求助。"
        )

    if category == "competition_resources":
        return (
            "这个我不能替你联系上届学长，也不能给你源文件或私下资料。"
            "想找公开信息的话，建议看学院官网、竞赛通知或问竞赛负责老师；"
            "如果你要准备比赛方向，我可以陪你一起拆任务。[think]"
        )

    if category == "official_contact":
        return (
            "这个我不能替你联系，也不能帮你拿具体电话或其他私人联系方式。"
            "最好还是你自己看学院官网、官方通知，或者直接问辅导员确认；"
            "如果你要发消息，我可以帮你把问题整理清楚一点。[think]"
        )

    return None


def template_reply(user_text: str) -> str | None:
    reply = _template_reply_with_expression_tags(user_text)
    if reply is None:
        return None
    return strip_expression(reply)
