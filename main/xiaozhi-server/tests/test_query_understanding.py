from core.museum.query_understanding import understand_question


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
    assert result.fact_types == ("research_limit",)


def test_craft_intent_accepts_colloquial_phrasing():
    result = understand_question("这么硬的水晶，当时的人是咋做出来的？")

    assert result.fine_intent == "craft"
    assert result.fact_types == ("research_limit",)


def test_craft_intent_accepts_object_inserted_before_verb():
    result = understand_question("这么硬，古人当时是怎么把它做出来的？")

    assert result.fine_intent == "craft"
    assert result.fact_types == ("research_limit",)


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


def test_polite_social_words_do_not_hide_an_exhibit_question():
    result = understand_question("你好，请讲讲这件展品的历史")

    assert result.coarse_intent == "exhibit_knowledge"
    assert result.fine_intent == "era"


def test_specific_fact_intent_wins_over_overview_wording():
    result = understand_question("请介绍一下它的历史")

    assert result.coarse_intent == "exhibit_knowledge"
    assert result.fine_intent == "era"
