from __future__ import annotations

from scripts.evaluate_museum_generalization import evaluate_fixture


def test_generalization_evaluator_tracks_mismatches_and_confusion() -> None:
    fixture = {
        "version": 1,
        "cases": [
            {
                "id": "intent-check",
                "category": "unseen_paraphrase",
                "turns": [
                    {
                        "text": "它是什么做的？",
                        "expected": {
                            "knowledge_status": "grounded",
                            "fine_intent": "material",
                            "fact_ids": ["fact-material"],
                        },
                    }
                ],
            }
        ],
    }

    payload = evaluate_fixture(
        fixture,
        ask=lambda _text: {
            "knowledge_status": "unsupported",
            "fine_intent": "unknown",
            "fact_ids": [],
        },
        reset=lambda: None,
    )

    assert payload["summary"] == {
        "turn_count": 1,
        "passed_turn_count": 0,
        "failed_turn_count": 1,
        "pass_rate": 0.0,
    }
    assert payload["intent_confusion"] == {"material": {"unknown": 1}}
    assert payload["turns"][0]["mismatches"] == [
        "knowledge_status: expected=['grounded'] actual='unsupported'",
        "fine_intent: expected=['material'] actual='unknown'",
        "fact_ids missing=['fact-material']",
    ]


def test_generalization_evaluator_resets_each_case_and_preserves_turn_context() -> None:
    resets = []
    questions = []
    fixture = {
        "version": 1,
        "cases": [
            {
                "id": "two-turns",
                "turns": [
                    {
                        "text": "介绍一下它",
                        "expected": {"fine_intent": "overview"},
                    },
                    {
                        "text": "那它是什么材质？",
                        "expected": {"fine_intent": "material"},
                    },
                ],
            },
            {
                "id": "new-session",
                "turns": [
                    {
                        "text": "你好",
                        "expected": {"fine_intent": "social"},
                    }
                ],
            },
        ],
    }

    def ask(text: str) -> dict:
        questions.append(text)
        return {
            "fine_intent": {
                "介绍一下它": "overview",
                "那它是什么材质？": "material",
                "你好": "social",
            }[text],
            "fact_ids": [],
        }

    payload = evaluate_fixture(
        fixture,
        ask=ask,
        reset=lambda: resets.append(True),
    )

    assert payload["summary"]["failed_turn_count"] == 0
    assert len(resets) == 2
    assert questions == ["介绍一下它", "那它是什么材质？", "你好"]
