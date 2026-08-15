import json
from pathlib import Path

import pytest

from core.museum.evaluation import prepare_evaluation_runtime
from core.museum.query_understanding import understand_question
from core.museum.store import DEMO_EXHIBIT_ID, MuseumStore


def test_understanding_uses_coarse_and_fine_intents():
    result = understand_question("请问这个杯子是用什么做成的？")

    assert result.coarse_intent == "exhibit_knowledge"
    assert result.fine_intent == "material"
    assert result.fact_types == ("material",)
    assert result.confidence > 0.7


def test_material_intent_accepts_colloquial_material_question():
    result = understand_question("它到底是拿什么做的？")

    assert result.fine_intent == "material"
    assert result.fact_types == ("material",)


def test_craft_intent_maps_to_published_fact_type():
    result = understand_question("它的制作工艺复杂吗？")

    assert result.fine_intent == "craft"
    assert result.fact_types == ("craft",)


def test_craft_intent_accepts_colloquial_phrasing():
    result = understand_question("这么硬的水晶，当时的人是咋做出来的？")

    assert result.fine_intent == "craft"
    assert result.fact_types == ("craft",)


def test_craft_intent_accepts_object_inserted_before_verb():
    result = understand_question("这么硬，古人当时是怎么把它做出来的？")

    assert result.fine_intent == "craft"
    assert result.fact_types == ("craft",)


def test_price_intent_wins_over_incidental_era_word():
    result = understand_question("战国水晶杯在战国时期卖了多少钱？")

    assert result.fine_intent == "price"
    assert result.fact_types == ("price",)


def test_overview_and_social_are_separate_from_fact_lookup():
    overview = understand_question("你能讲讲这件展品有什么看点吗？")
    social = understand_question("你好，你是谁？")

    assert overview.fine_intent == "overview"
    assert overview.fact_types == ()
    assert social.coarse_intent == "social"
    assert social.fine_intent == "social"


def test_open_ended_exhibit_questions_map_to_overview():
    for question in (
        "那什么是战国水晶杯呢？",
        "这是什么？",
        "给我讲讲它。",
    ):
        result = understand_question(question)

        assert result.coarse_intent == "exhibit_knowledge"
        assert result.fine_intent == "overview"
        assert result.answer_depth == "guided"


def test_detailed_explanation_request_sets_detailed_answer_depth():
    for question in (
        "请详细介绍一下战国水晶杯",
        "展开讲讲它的来历和特点",
        "给我做一个完整讲解",
    ):
        result = understand_question(question)

        assert result.coarse_intent == "exhibit_knowledge"
        assert result.fine_intent == "overview"
        assert result.answer_depth == "detailed"


def test_greeting_to_guide_is_social():
    result = understand_question("你好讲解员。")
    time_greeting = understand_question("讲解员，早上好")

    assert result.coarse_intent == "social"
    assert result.fine_intent == "social"
    assert time_greeting.coarse_intent == "social"
    assert time_greeting.fine_intent == "social"


def test_plain_statement_is_unclear_instead_of_unknown_exhibit_question():
    result = understand_question("他独自坐地铁去面试。")
    appearance_statement = understand_question("这个颜色看起来挺漂亮的")

    assert result.coarse_intent == "unclear"
    assert result.fine_intent == "unknown"
    assert appearance_statement.coarse_intent == "unclear"
    assert appearance_statement.fine_intent == "unknown"


def test_history_listing_questions_map_to_history_fact_type():
    result = understand_question("这个展品公开叫什么？")

    assert result.fine_intent == "history"
    assert result.fact_types == ("history",)


def test_polite_social_words_do_not_hide_an_exhibit_question():
    result = understand_question("你好，请讲讲这件展品的历史")

    assert result.coarse_intent == "exhibit_knowledge"
    assert result.fine_intent == "era"


def test_specific_fact_intent_wins_over_overview_wording():
    result = understand_question("请介绍一下它的历史")
    compound = understand_question("它是什么材质，又是在哪里出土的？")

    assert result.coarse_intent == "exhibit_knowledge"
    assert result.fine_intent == "era"
    assert set(compound.fact_types) == {"material", "excavation"}


@pytest.mark.parametrize(
    ("question", "expected_intent", "expected_fact_types"),
    (
        ("古人拿哪种矿物琢成了这个杯子？", "material", ("material",)),
        ("这套东西是不是代表了主人的权力？", "usage", ("usage",)),
        ("它原来戴在身体哪个位置？", "usage", ("usage",)),
        ("上面的洞除了好看还有什么用？", "usage", ("usage",)),
        ("这件袍子原来是什么场合穿的？", "usage", ("usage",)),
        ("垫饼上写了什么字？", "craft", ("craft",)),
    ),
)
def test_understanding_covers_natural_museum_usage_questions(
    question,
    expected_intent,
    expected_fact_types,
):
    result = understand_question(question)

    assert result.coarse_intent == "exhibit_knowledge"
    assert result.fine_intent == expected_intent
    assert result.fact_types == expected_fact_types


@pytest.mark.parametrize(
    ("exhibit_id", "question", "expected_fact_id"),
    (
        (
            "warring-states-crystal-cup",
            "古人拿哪种矿物琢成了这个杯子？",
            "fact-crystal-cup-material",
        ),
        (
            "liangzhu-jade-yue-set",
            "这套东西是不是代表了主人的权力？",
            "fact-liangzhu-yue-usage-r2",
        ),
        (
            "liangzhu-jade-trident",
            "它原来戴在身体哪个位置？",
            "fact-liangzhu-trident-usage-r2",
        ),
        (
            "southern-song-guan-bagua-incense-lid",
            "上面的洞除了好看还有什么用？",
            "fact-west-lake-bagua-usage",
        ),
    ),
)
def test_natural_questions_reach_published_facts_through_lexical_fallback(
    tmp_path,
    exhibit_id,
    question,
    expected_fact_id,
):
    server_root = Path(__file__).resolve().parents[1]
    if exhibit_id == DEMO_EXHIBIT_ID:
        store = MuseumStore(tmp_path / "museum.db")
        store.seed_demo_content()
    else:
        fixture = json.loads(
            (
                server_root / "tests/fixtures/museum_conversation_eval.json"
            ).read_text(encoding="utf-8")
        )
        runtime = prepare_evaluation_runtime(
            database_path=tmp_path / "museum.db",
            server_root=server_root,
            fixture=fixture,
        )
        store = runtime._store
    understanding = understand_question(question)

    candidates = store.lexical_fact_candidates(
        exhibit_id=exhibit_id,
        question=question,
        fact_types=understanding.fact_types,
        query_terms=understanding.query_terms,
    )

    assert candidates[0][0] == expected_fact_id
