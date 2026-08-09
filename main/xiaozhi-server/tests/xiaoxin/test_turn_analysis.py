from __future__ import annotations

import pytest

from core.xiaoxin.turn_analysis import (
    current_turn_companion_corrections,
    explicit_companion_feedback_signals,
)


@pytest.mark.parametrize(
    ("user_text", "expected_kind", "expected_outcome"),
    (
        ("你刚才的回答很有帮助。", "accepted_help", "helpful"),
        ("你刚才没帮到我。", "interaction_feedback", "not_helpful"),
        ("你太主动了。", "interaction_feedback", "too_proactive"),
        ("你刚才问得太私人了。", "interaction_feedback", "too_personal"),
    ),
)
def test_direct_voice_companion_feedback_becomes_relationship_evidence(
    user_text,
    expected_kind,
    expected_outcome,
):
    signals = explicit_companion_feedback_signals(user_text)

    assert len(signals) == 1
    assert signals[0]["kind"] == expected_kind
    assert signals[0]["ownership_scope"] == "relationship"
    assert signals[0]["content"] == {"outcome": expected_outcome}
    assert signals[0]["attribution"] == "explicit_user_feedback"
    assert signals[0]["prompt_eligible"] is False


def test_stable_direct_style_feedback_is_structured_but_unsafe_contexts_are_not():
    cases = (
        (
            "这几次相处下来，我平时还是喜欢你回答短一点、少追问、简洁收尾。",
            {
                ("response_length", "short"),
                ("question_frequency", "less"),
                ("closure_style", "concise"),
            },
        ),
        (
            "这几次相处下来，我平时还是喜欢你慢慢展开、照顾感受、温和收尾。",
            {
                ("response_length", "expanded"),
                ("emotional_posture", "supportive"),
                ("closure_style", "warm"),
            },
        ),
    )
    for text, expected in cases:
        signals = tuple(
            signal
            for signal in explicit_companion_feedback_signals(text)
            if signal["kind"] == "preference_feedback"
        )

        assert {
            (signal["content"]["dimension"], signal["content"]["value"])
            for signal in signals
        } == expected
        assert all(
            signal["ownership_scope"] == "relationship"
            and signal["attribution"] == "explicit_user_feedback"
            and signal["content"]["feedback_specificity"]
            == "behavior_and_context"
            for signal in signals
        )

    unsafe_texts = (
        "室友说，我平时喜欢你回答短一点、少追问。",
        "如果你以后回答短一点、少追问，也许会更好。",
        "这次请你回答短一点、少追问。",
    )
    for text in unsafe_texts:
        assert all(
            signal["kind"] != "preference_feedback"
            for signal in explicit_companion_feedback_signals(text)
        )


def test_current_turn_corrections_are_ordered_direct_and_not_persisted_as_evidence():
    text = "这次别追问，简短一点，也别开玩笑，表情收一点。"

    assert current_turn_companion_corrections(text) == (
        "no_follow_up",
        "concise",
        "no_humor",
        "settle_hardware",
    )
    assert explicit_companion_feedback_signals(text) == ()
    assert current_turn_companion_corrections("老师说‘这次别追问’。") == ()


def test_topic_close_stops_follow_up_without_persisting_a_user_fact():
    text = "好啦，这件事翻篇。"

    assert current_turn_companion_corrections(text) == (
        "no_follow_up",
        "concise",
    )
    assert explicit_companion_feedback_signals(text) == ()
    assert current_turn_companion_corrections("室友说，这件事翻篇。") == ()
    assert current_turn_companion_corrections("我该怎么才能翻篇？") == ()


def test_explicit_next_day_followup_request_becomes_meaningful_moment():
    signals = explicit_companion_feedback_signals(
        "你刚才的回答很有帮助。明天你可以主动问问我上台讲得怎么样吗？"
    )

    assert [signal["kind"] for signal in signals] == [
        "accepted_help",
        "meaningful_moment",
    ]
    followup = signals[1]
    assert followup["ownership_scope"] == "relationship"
    assert followup["content"] == {
        "outcome": "followup_worthwhile",
        "followup_time": "next_day",
        "topic": "上台讲得怎么样",
    }
    assert followup["attribution"] == "explicit_user_request"
    assert followup["prompt_eligible"] is True


@pytest.mark.parametrize(
    "user_text",
    (
        "室友说，明天你可以主动问问我上台讲得怎么样吗？",
        "如果我明天上台，明天你可以问问我讲得怎么样吗？",
        "老师说‘明天你可以问问我上台讲得怎么样吗’。",
    ),
)
def test_reported_hypothetical_or_quoted_followup_request_is_not_persisted(user_text):
    assert explicit_companion_feedback_signals(user_text) == ()


@pytest.mark.parametrize(
    "user_text",
    (
        "室友说‘你刚才的回答很有帮助’。",
        "老师说你太主动了。",
        "如果你能帮到我就好了。",
        "假如你别太主动，也许会更好。",
    ),
)
def test_reported_or_hypothetical_companion_feedback_is_not_attributed_to_user(
    user_text,
):
    assert explicit_companion_feedback_signals(user_text) == ()


@pytest.mark.parametrize(
    "user_text",
    (
        "室友说我叫小王。",
        "室友说过我叫小王。",
        "老师说，我来自武汉。",
        "老师曾表示我来自武汉。",
        "他说我喜欢简短回答。",
        "朋友说我终于完成了项目。",
        "朋友跟我说过我完成了项目。",
        "据说我来自武汉。",
        "朋友发消息：‘我叫小王。’",
        '我只是举例："我终于完成了项目。"',
        "我叫小王吗？",
        "我来自武汉吗？",
        "我喜欢简短回答吗？",
        "我完成了项目吗？",
    ),
)
def test_reported_or_quoted_first_person_claims_do_not_become_user_evidence(
    user_text,
):
    assert explicit_companion_feedback_signals(user_text) == ()


@pytest.mark.parametrize(
    "user_text",
    (
        "我梦到我终于完成了项目。",
        "我梦见我完成了项目。",
        "我以为我通过了考试。",
        "我希望我解决了这个问题。",
        "我希望未来我解决了这个问题。",
        "我希望最终我完成了项目。",
        "我但愿我完成了项目。",
        "我想象我完成了项目。",
        "我只是举例我完成了项目。",
        "如果我叫小王。",
        "我假装我来自武汉。",
        "我猜测我喜欢简短回答。",
        "我觉得我喜欢简短回答。",
        "我担心我通过了考试。",
        "我误认为我完成了项目。",
        "我否认我叫小王。",
        "他叫我小王。",
        "老师称呼我为小王。",
        "我否认别人叫我小王。",
        "我不想让你叫我小王。",
        "你叫我小王干嘛。",
        "朋友说以后叫我小王。",
        "朋友说，以后叫我小王。",
        "朋友说以后我叫小王。",
        "朋友说了一句我叫小王。",
        "朋友跟我说了一句我叫小王。",
        "朋友告诉大家我叫小王。",
        "朋友说了一句我完成了项目。",
        "朋友说结果我叫小王。",
        "朋友聊到我叫小王。",
        "朋友聊到，我叫小王。",
        "朋友聊到、我来自武汉。",
        "朋友聊到\n我来自武汉。",
        "朋友聊到、平时喜欢简短回答。",
        "朋友聊到\n平时喜欢简短回答。",
        "朋友聊到：叫我小王。",
        "朋友聊到；我叫小王。",
        "朋友聊到；叫我小王。",
        "朋友聊到;我完成了项目。",
        "朋友聊到，别叫我小林。",
        "朋友说别叫我小林。",
        "我梦到别叫我小林。",
        "朋友聊到，别叫我小王，以后叫我小林。",
        "我梦到别叫我小王，以后叫我小林。",
        "朋友说我完成了项目，但我喜欢简短回答。",
        "老师表示我来自武汉，但我叫小林。",
        "朋友聊到我来自武汉，但我叫小王。",
        "朋友聊到我完成了项目，但我喜欢简短回答。",
        "朋友聊到我来自武汉，但我完成了项目。",
        "朋友提一嘴，以后叫我小王。",
        "我听朋友聊到：叫我小王。",
        "朋友聊到我来自武汉，我叫小王。",
        "朋友聊到我叫小王，我终于完成了项目。",
        "朋友聊到我喜欢简短回答，我来自武汉。",
        "朋友提了一嘴我完成了项目。",
        "我听朋友聊到我叫小王。",
        "朋友说：“我叫小王。",
    ),
)
def test_non_factual_embedded_first_person_claims_do_not_become_user_evidence(
    user_text,
):
    assert explicit_companion_feedback_signals(user_text) == ()


def test_direct_first_person_claims_keep_the_narrow_v2_evidence_contract():
    signals = explicit_companion_feedback_signals(
        "我叫小林，我来自武汉，平时喜欢简短回答，我终于完成了项目。"
    )

    assert [signal["kind"] for signal in signals] == [
        "profile_fact",
        "profile_fact",
        "explicit_preference",
        "user_life_event",
    ]
    assert all(signal["ownership_scope"] == "user" for signal in signals)
    assert all(signal["attribution"] == "explicit_user_statement" for signal in signals)


@pytest.mark.parametrize(
    ("user_text", "expected_kind"),
    (
        ("我喜欢看小说，我叫小林。", "profile_fact"),
        ("我喜欢说唱，我叫小林。", "profile_fact"),
        ("我喜欢听传说，我叫小林。", "profile_fact"),
        ("我参加完讲座。 我来自武汉。", "profile_fact"),
        ("我遇到一个问题。 我终于完成了项目。", "user_life_event"),
        ("昨天我终于完成了项目。", "user_life_event"),
    ),
)
def test_words_containing_reporting_characters_do_not_hide_real_claims(
    user_text,
    expected_kind,
):
    signals = explicit_companion_feedback_signals(user_text)

    assert expected_kind in {signal["kind"] for signal in signals}


@pytest.mark.parametrize(
    ("user_text", "expected_kind"),
    (
        ("天气很好。我来自武汉。", "profile_fact"),
        ("天气很好。平时喜欢简短回答。", "explicit_preference"),
        ("天气很好！我来自武汉。", "profile_fact"),
        ("天气很好？平时喜欢简短回答。", "explicit_preference"),
    ),
)
def test_consumed_sentence_boundary_still_starts_a_direct_claim(
    user_text,
    expected_kind,
):
    signals = explicit_companion_feedback_signals(user_text)

    assert len(signals) == 1
    assert signals[0]["kind"] == expected_kind


@pytest.mark.parametrize(
    "user_text",
    (
        "我叫小林，朋友聊到，我来自上海，我终于完成了项目。",
        "我来自武汉，朋友提了一嘴我喜欢简短回答，我完成了项目。",
    ),
)
def test_rejected_nearby_report_cannot_borrow_an_earlier_direct_fact(user_text):
    signals = explicit_companion_feedback_signals(user_text)

    assert len(signals) == 1
    assert signals[0]["kind"] == "profile_fact"


@pytest.mark.parametrize(
    "user_text",
    (
        "我刚才说我叫小林。",
        "我刚才跟室友说过我叫小林。",
    ),
)
def test_self_reporting_a_direct_claim_is_not_mistaken_for_third_party_report(
    user_text,
):
    signals = explicit_companion_feedback_signals(user_text)

    assert len(signals) == 1
    assert signals[0]["content"] == {
        "fact_key": "preferred_name",
        "value": "小林",
    }


@pytest.mark.parametrize(
    "user_text",
    (
        "叫我小林就好。",
        "以后叫我小林。",
        "你可以叫我小林。",
        "我希望你以后叫我小林。",
        "别叫我小王，以后叫我小林。",
        "我叫小王，以后叫我小林。",
    ),
)
def test_explicit_naming_request_is_not_suppressed_as_a_non_factual_claim(
    user_text,
):
    signals = explicit_companion_feedback_signals(user_text)

    assert len(signals) == 1
    assert signals[0]["content"] == {
        "fact_key": "preferred_name",
        "value": "小林",
    }


@pytest.mark.parametrize(
    "user_text",
    (
        "别再这样称呼我。",
        "我刚才说别再这样称呼我。",
    ),
)
def test_direct_name_withdrawal_keeps_a_tombstone(user_text):
    signals = explicit_companion_feedback_signals(user_text)

    assert len(signals) == 1
    assert signals[0]["content"] == {
        "fact_key": "preferred_name",
        "value": None,
    }


def test_real_claim_after_contrast_is_not_suppressed_by_earlier_non_factual_context():
    signals = explicit_companion_feedback_signals(
        "我曾以为没完成，但我终于完成了项目。"
    )

    assert len(signals) == 1
    assert signals[0]["kind"] == "user_life_event"
    assert signals[0]["content"] == {"event": "终于完成了项目"}


def test_direct_claim_without_terminal_punctuation_is_not_treated_as_a_question():
    signals = explicit_companion_feedback_signals("我叫小林")

    assert len(signals) == 1
    assert signals[0]["content"] == {
        "fact_key": "preferred_name",
        "value": "小林",
    }


def test_long_repeated_claim_chain_fails_closed_without_recursion_error():
    user_text = "，".join(f"我叫用户{index}" for index in range(200))

    signals = explicit_companion_feedback_signals(user_text)

    assert signals == ()
