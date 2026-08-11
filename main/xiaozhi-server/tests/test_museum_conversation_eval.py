from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from core.business_runtime_factory import create_conversation_runtime
from core.conversation_runtime import TurnRequest


FIXTURE = Path(__file__).parent / "fixtures" / "museum_conversation_eval.json"


def _request(*, text: str, case_id: str, turn_index: int, device_id: str) -> TurnRequest:
    return TurnRequest(
        request_id=f"eval-{case_id}-{turn_index}",
        transport_session_id=f"eval-transport-{case_id}",
        visitor_session_id=None,
        device_id=device_id,
        user_text=text,
        history=(),
        occurred_at=datetime.now().astimezone(),
        llm=None,
    )


def test_natural_language_conversation_fixture(tmp_path):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["version"] == 1

    for case in fixture["cases"]:
        runtime = create_conversation_runtime(
            {
                "business_runtime": {
                    "type": "museum",
                    "database_path": str(tmp_path / f"{case['id']}.db"),
                    "exhibit_context_mode": "explicit",
                }
            }
        )
        device_id = f"eval-device-{case['id']}"
        for turn_index, turn in enumerate(case["turns"], start=1):
            outcome = runtime.handle_turn(
                _request(
                    text=turn["text"],
                    case_id=case["id"],
                    turn_index=turn_index,
                    device_id=device_id,
                )
            )
            expected = turn["expected"]
            assert outcome.knowledge_status == expected["knowledge_status"], (
                case["id"],
                turn_index,
                turn["text"],
                outcome.spoken_text,
            )
            if "fact_ids" in expected:
                assert list(outcome.fact_ids) == expected["fact_ids"]
            context = outcome.display_state["context"]
            if "context_exhibit_id" in expected:
                assert context["exhibit_id"] == expected["context_exhibit_id"]
            if "context_source" in expected:
                assert context["source"] == expected["context_source"]
            if "resolution_status" in expected:
                assert outcome.audit_record["resolution_status"] == expected[
                    "resolution_status"
                ]
            if "coarse_intent" in expected:
                assert outcome.audit_record["coarse_intent"] == expected[
                    "coarse_intent"
                ]
            if "fine_intent" in expected:
                assert outcome.audit_record["fine_intent"] == expected["fine_intent"]
            if "contains" in expected:
                assert expected["contains"] in outcome.spoken_text
