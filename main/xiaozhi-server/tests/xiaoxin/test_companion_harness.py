import json

import pytest

from core.xiaoxin.companion.model_harness import StructuredJsonHarness, StructuredOutputError
from core.xiaoxin.companion.prompt_specs import memory_interpretation_prompt, prompt_manifest
from scripts.xiaoxin_companion_harness import (
    ControlClient,
    _validate_deterministic_report,
    _validate_judge_report,
    build_parser,
)
from tools.companion_harness.contracts import (
    CONTRACT_VERSION,
    EVIDENCE_FILES,
    SCENARIO_IDS,
    append_jsonl,
    canonical_hash,
    write_json,
)
from tools.companion_harness.scenarios import SCENARIOS


def test_prompt_specs_are_three_hashed_json_contracts():
    manifest = prompt_manifest()

    assert [item["semantic_version"] for item in manifest] == [
        "companion-memory-interpretation-v8",
        "companion-reflection-v2",
        "companion-initiative-v3",
    ]
    assert all(len(item["prompt_hash"]) == 64 for item in manifest)
    assert all(item["response_format"] == {"type": "json_object"} for item in manifest)


def test_structural_repair_runs_once_at_zero_temperature_and_is_audited():
    class Adapter:
        model_name = "deepseek-test"

        def __init__(self):
            self.llm = self
            self.calls = []

        def complete_chat(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            return "not-json" if len(self.calls) == 1 else '{"value":"fixed"}'

    def parser(raw):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StructuredOutputError("invalid_json", "invalid JSON") from exc
        if set(value) != {"value"}:
            raise StructuredOutputError("invalid_shape", "invalid shape")
        return value

    adapter = Adapter()
    audits = []
    completion = StructuredJsonHarness(adapter, audit_sink=audits.append).complete(
        spec=memory_interpretation_prompt(timeout_seconds=1),
        user_payload={"synthetic": True},
        parser=parser,
    )

    assert completion.value == {"value": "fixed"}
    assert completion.repair_count == 1
    assert len(adapter.calls) == 2
    assert adapter.calls[1][1]["temperature"] == 0.0
    assert adapter.calls[1][1]["response_format"] == {"type": "json_object"}
    repair_payload = json.loads(adapter.calls[1][0][-1]["content"])
    assert repair_payload["validation_error"] == "invalid_json"
    assert repair_payload["validation_message"] == "invalid JSON"
    assert [item["attempt"] for item in audits] == [
        "initial",
        "repair",
        "final_validation",
    ]
    assert audits[-1]["outcome"] == "succeeded"
    assert audits[-1]["error_code"] == "invalid_json"
    assert "not-json" not in json.dumps(audits)


def test_frozen_scenarios_cli_and_evidence_bundle_contract_are_complete(
    tmp_path, monkeypatch
):
    assert len(SCENARIOS) == 15
    assert tuple(item.case_id for item in SCENARIOS) == SCENARIO_IDS
    assert EVIDENCE_FILES == (
        "manifest.json",
        "prompt-manifest.json",
        "scenario-results.jsonl",
        "model-invocations.jsonl",
        "events.jsonl",
        "serial-a.jsonl",
        "serial-b.jsonl",
        "server.jsonl",
        "network.jsonl",
        "database-audit.json",
        "deterministic-report.json",
        "codex-review-packet.json",
        "codex-judge-report.json",
        "final-report.json",
        "restore-report.json",
    )
    parser = build_parser()
    assert set(parser._subparsers._group_actions[0].choices) == {
        "prepare",
        "deploy",
        "model-eval",
        "hil-run",
        "collect",
        "review-packet",
        "finalize",
        "restore",
        "promote",
    }
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

        def read(self):
            return b'{"success":true}'

    def fake_urlopen(request, timeout):
        captured["request"] = request
        return Response()

    monkeypatch.setattr(
        "scripts.xiaoxin_companion_harness.urlrequest.urlopen",
        fake_urlopen,
    )
    client = ControlClient("http://127.0.0.1:8003", "test-session", tmp_path)
    client.post("/csrf-contract", {}, "csrf-contract")
    assert captured["request"].get_header("X-xiaoxin-csrf") == client.csrf
    assert captured["request"].get_header("X-csrf-token") is None
    deterministic = {
        "contract_version": CONTRACT_VERSION,
        "generated_at": "2026-07-31T00:00:00+08:00",
        "status": "PASS",
        "checks": [
            {
                "case_id": case_id,
                "status": "PASS",
                "detail": "synthetic evidence passed",
                "event_id": f"event-{case_id}",
                "evidence": ["scenario-results.jsonl"],
            }
            for case_id in SCENARIO_IDS
        ],
        "hard_gate_policy": "FAIL cannot be overridden; missing evidence is INCONCLUSIVE",
    }
    deterministic["digest"] = canonical_hash(deterministic)
    assert _validate_deterministic_report(deterministic) == deterministic
    write_json(tmp_path / "deterministic-report.json", deterministic)
    for case_id in SCENARIO_IDS:
        append_jsonl(
            tmp_path / "scenario-results.jsonl",
            {"case_id": case_id, "event_id": f"event-{case_id}"},
        )
        append_jsonl(
            tmp_path / "events.jsonl",
            {"case_id": case_id, "event_id": f"event-{case_id}"},
        )
    judge = {
        "status": "PASS",
        "items": [
            {
                "case_id": case_id,
                "status": "PASS",
                "detail": "evidence reviewed",
                "evidence": [
                    {"file": "events.jsonl", "event_id": f"event-{case_id}"}
                ],
            }
            for case_id in SCENARIO_IDS
        ],
    }
    assert _validate_judge_report(tmp_path, judge) == judge
    judge["items"][1]["evidence"][0]["event_id"] = "event-M01"
    with pytest.raises(ValueError, match="another scenario"):
        _validate_judge_report(tmp_path, judge)
    deterministic["status"] = "UNKNOWN"
    with pytest.raises(ValueError, match="status or checks"):
        _validate_deterministic_report(deterministic)
