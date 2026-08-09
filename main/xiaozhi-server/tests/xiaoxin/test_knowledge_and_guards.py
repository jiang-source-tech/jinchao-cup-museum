from pathlib import Path

from core.xiaoxin.boundary_guard import template_reply
from core.xiaoxin.knowledge import KnowledgeBase
from core.xiaoxin.response_guard import (
    reply_changes_future_plan_to_completed,
    reply_claims_unconfirmed_memory_write,
    reply_exposes_internal_memory_mechanics,
    reply_exceeds_knowledge_scope,
    reply_exceeds_question_budget,
)
from core.xiaoxin.semantic_router import is_existing_tool_turn, route_message


PROJECT_DIR = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = PROJECT_DIR / "data" / "xiaoxin_knowledge"


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content):
        self._content = content
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, content):
        self.completions = _FakeCompletions(content)


class _FakeClient:
    def __init__(self, content):
        self.chat = _FakeChat(content)


def test_existing_tool_turns_bypass_xiaoxin_runtime():
    assert is_existing_tool_turn("\u62dc\u62dc")
    assert is_existing_tool_turn("\u653e\u4e00\u9996\u6b4c")
    assert is_existing_tool_turn("\u628a\u5ba2\u5385\u706f\u5173\u6389")
    assert not is_existing_tool_turn(
        "\u5e2e\u6211\u8054\u7cfb\u8001\u5e08\u8981\u5b9e\u9a8c\u5ba4\u7535\u8bdd"
    )


def test_registered_tool_categories_bypass_xiaoxin_runtime():
    tool_phrases = [
        "\u64ad\u653e\u4e00\u9996\u6b4c",
        "\u6765\u70b9\u97f3\u4e50",
        "\u628a\u5ba2\u5385\u706f\u6253\u5f00",
        "\u5173\u95ed\u5367\u5ba4\u706f",
        "\u7a7a\u8c03\u8c03\u523026\u5ea6",
        "\u97f3\u91cf\u8c03\u5c0f\u4e00\u70b9",
        "\u676d\u5dde\u5929\u6c14\u600e\u4e48\u6837",
        "\u64ad\u62a5\u4eca\u5929\u65b0\u95fb",
        "\u5e2e\u6211\u641c\u7d22\u4e00\u4e0b\u6821\u56ed\u7f51",
        "\u73b0\u5728\u51e0\u70b9",
        "\u4eca\u5929\u519c\u5386\u591a\u5c11",
        "\u6253\u5f00\u65e5\u5386",
        "\u4eca\u5929\u51e0\u53f7",
        "\u5207\u6362\u6210\u82f1\u8bed\u8001\u5e08\u89d2\u8272",
        "\u547c\u53eb\u5c0f\u9648",
        "\u6253\u7535\u8bdd\u7ed9\u5988\u5988",
        "\u62dc\u62dc",
        "\u9000\u51fa",
    ]

    for phrase in tool_phrases:
        assert is_existing_tool_turn(phrase), phrase


def test_private_contact_boundary_does_not_bypass_as_device_tool():
    assert not is_existing_tool_turn(
        "\u5e2e\u6211\u8054\u7cfb\u8001\u5e08\u8981\u7535\u8bdd"
    )


def test_hard_boundary_routes_without_llm():
    route = route_message(
        "\u667a\u80fd\u8f66\u7ade\u8d5b\u4f60\u80fd\u5e2e\u6211\u8054\u7cfb\u4e0a\u5c4a\u5b66\u957f\uff0c\u7ed9\u6211\u6e90\u6587\u4ef6\u5417\uff1f",
        [],
        client=None,
        model="fake",
    )

    assert route["reply_mode"] == "hard_template"
    assert route["source"] == "hard_boundary"


def test_personal_data_phrase_does_not_trigger_competition_resource_boundary():
    route = route_message(
        "今天事情堆在一起，我有点烦。你别复述我的资料，先陪我迈出第一步。",
        [],
        client=None,
        model=None,
    )

    assert route["intent"] == "open_chat"
    assert route["reply_mode"] == "free_chat"


def test_message_drafting_routes_without_llm():
    route = route_message(
        "\u5e2e\u6211\u5199\u4e00\u6bb5\u7ed9\u8001\u5e08\u8be2\u95ee\u60c5\u51b5\u7684\u6d88\u606f",
        [],
        client=None,
        model=None,
    )

    assert route["intent"] == "message_drafting"
    assert route["reply_mode"] == "message_drafting"
    assert route["source"] == "fallback"


def test_private_contact_request_stays_hard_boundary_not_message_drafting():
    route = route_message(
        "\u5e2e\u6211\u8054\u7cfb\u8001\u5e08\uff0c\u6211\u60f3\u8981\u8001\u5e08\u7535\u8bdd",
        [],
        client=None,
        model=None,
    )

    assert route["reply_mode"] == "hard_template"
    assert route["intent"] == "official_contact"


def test_template_reply_refuses_private_contact_request():
    reply = template_reply("\u5e2e\u6211\u8054\u7cfb\u8001\u5e08\u8981\u4e00\u4e0b\u7535\u8bdd")

    assert reply
    assert (
        "\u4e0d\u80fd\u66ff\u4f60\u8054\u7cfb" in reply
        or "\u4e0d\u80fd\u5e2e\u4f60\u8054\u7cfb" in reply
    )
    assert (
        "\u5b98\u7f51" in reply
        or "\u5b98\u65b9" in reply
        or "\u8f85\u5bfc\u5458" in reply
    )


def test_template_reply_strips_expression_tags_from_private_contact_request():
    reply = template_reply("\u5e2e\u6211\u8054\u7cfb\u8001\u5e08\u8981\u7535\u8bdd")

    assert reply
    assert "[think]" not in reply
    assert not any(tag in reply for tag in ("[smile]", "[soft_smile]", "[cheer]"))
    assert not any(part.startswith("[") and part.endswith("]") for part in reply.split())


def test_knowledge_base_loads_campus_life_and_returns_grounding():
    kb = KnowledgeBase(KNOWLEDGE_DIR)
    route = {"reply_mode": "knowledge_grounded", "intent": "campus_knowledge"}

    context = kb.grounding_context(
        "\u5317\u79c0\u98df\u5802\u6709\u4ec0\u4e48\u5403\u7684\uff1f",
        route,
    )

    assert context is not None
    assert "facts" in context
    assert "\u5317\u79c0" in context["facts"]
    assert "preferred_fallback" in context


def test_knowledge_base_matches_student_affairs_terms_from_dataset():
    kb = KnowledgeBase(KNOWLEDGE_DIR)
    route = route_message("\u5b66\u5de5\u529e\u5728\u54ea\uff1f", [], client=None, model=None)

    context = kb.grounding_context("\u5b66\u5de5\u529e\u5728\u54ea\uff1f", route)

    assert route["reply_mode"] == "knowledge_grounded"
    assert context is not None
    assert "\u7406\u4e94B307" in context["facts"] or "\u7406\u5de5\u79d1\u697c5B-307" in context["facts"]


def test_knowledge_base_matches_psychological_counseling_booking_query():
    kb = KnowledgeBase(KNOWLEDGE_DIR)
    route = route_message(
        "\u6211\u8be5\u5982\u4f55\u9884\u7ea6\u5b66\u6821\u7684\u5fc3\u7406\u54a8\u8be2\uff1f",
        [],
        client=None,
        model=None,
    )

    context = kb.grounding_context(
        "\u6211\u8be5\u5982\u4f55\u9884\u7ea6\u5b66\u6821\u7684\u5fc3\u7406\u54a8\u8be2\uff1f",
        route,
    )

    assert route["reply_mode"] == "knowledge_grounded"
    assert context is not None
    assert "88296000" in context["facts"]
    assert "\u7406\u56db114" in context["facts"]


def test_template_reply_returns_crisis_safe_response():
    reply = template_reply("\u6211\u6709\u70b9\u4e0d\u60f3\u6d3b\u4e86")

    assert reply
    assert "120" in reply or "110" in reply
    assert "\u4e00\u4e2a\u4eba" in reply or "\u9a6c\u4e0a" in reply


def test_response_guard_detects_unsupported_specific_claim():
    route = {"reply_mode": "knowledge_grounded", "intent": "campus_knowledge"}
    context = {
        "facts": "\u5317\u79c0\u98df\u5802\u6709\u82e5\u5e72\u9910\u996e\u7a97\u53e3\uff0c\u4f46\u6ca1\u6709\u5199\u660e\u8425\u4e1a\u65f6\u95f4\u3002",
        "preferred_fallback": "\u6211\u8fd9\u91cc\u6ca1\u6709\u53ef\u9760\u8425\u4e1a\u65f6\u95f4\u3002",
    }

    assert reply_exceeds_knowledge_scope(
        route,
        "\u5317\u79c0\u98df\u5802\u8425\u4e1a\u65f6\u95f4\u662f\u65e9\u4e0a\u4e03\u70b9\u5230\u665a\u4e0a\u4e5d\u70b9\u3002",
        context,
    )
    assert not reply_exceeds_knowledge_scope(
        route,
        "\u5317\u79c0\u98df\u5802\u7684\u4fe1\u606f\u6211\u8fd9\u91cc\u6709\u4e00\u4e9b\uff0c\u4f46\u8425\u4e1a\u65f6\u95f4\u6211\u8fd9\u91cc\u6ca1\u6709\u53ef\u9760\u8d44\u6599\u3002",
        context,
    )


def test_reply_truth_guard_catches_real_control_chat_failures():
    free_chat_route = {"reply_mode": "free_chat"}

    assert reply_exceeds_knowledge_scope(
        free_chat_route,
        "下午办借书证，记得带身份证。",
        None,
        user_text="下午去图书馆办借书证，晚上准备复习高数。",
    )
    assert reply_claims_unconfirmed_memory_write(
        "已经稳稳存进长期记忆区域了。"
    )
    assert reply_exposes_internal_memory_mechanics(
        "记忆写入是异步的，我现在还不能保证。"
    )
    assert reply_changes_future_plan_to_completed(
        "请记住：我今天下午要去图书馆办借书证，晚上准备复习高数。",
        "你下午去图书馆办了借书证，晚上复习了高数，这两件事都完成了。",
    )
    assert reply_exceeds_question_budget("先喝口水，好吗？", 0)


def test_route_message_parses_json_string_from_llm_client():
    client = _FakeClient(
        '{"intent":"campus_knowledge","reply_mode":"knowledge_grounded","knowledge_domains":["student_affairs"],"source":"llm"}'
    )

    route = route_message(
        "\u6211\u8be5\u5982\u4f55\u9884\u7ea6\u5b66\u6821\u7684\u5fc3\u7406\u54a8\u8be2\uff1f",
        [],
        client=client,
        model="fake-model",
    )

    assert route["intent"] == "campus_knowledge"
    assert route["reply_mode"] == "knowledge_grounded"
    assert route["source"] == "llm"
    assert client.chat.completions.last_kwargs is not None
    assert client.chat.completions.last_kwargs["messages"]


def test_route_message_keeps_message_drafting_reply_mode_from_llm_client():
    client = _FakeClient(
        '{"intent":"message_drafting","reply_mode":"message_drafting","knowledge_domains":[],"source":"llm"}'
    )

    route = route_message(
        "\u5e2e\u6211\u5199\u4e00\u6bb5\u53d1\u7ed9\u8f85\u5bfc\u5458\u7684\u8be2\u95ee\u6d88\u606f",
        [],
        client=client,
        model="fake-model",
    )

    assert route["intent"] == "message_drafting"
    assert route["reply_mode"] == "message_drafting"
    assert route["source"] == "llm"
