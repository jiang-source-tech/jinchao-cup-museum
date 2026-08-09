"""Single-screen terminal shell for the throwaway policy prototype."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Mapping

from policy_model import PolicyDecision, compose_policy
from scenarios import SCENARIOS, Scenario


BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"


def _read_path(value: Mapping[str, object], path: str) -> object:
    contains = path.endswith(".contains")
    parts = path.removesuffix(".contains").split(".")
    current: object = value
    for part in parts:
        if not isinstance(current, Mapping):
            raise KeyError(path)
        current = current[part]
    if contains:
        return current
    return current


def _scenario_failures(scenario: Scenario, decision: PolicyDecision) -> list[str]:
    policy = decision.policy.as_dict()
    failures: list[str] = []
    for path, expected in scenario.expected.items():
        if path.endswith(".contains"):
            actual = _read_path(policy, path)
            if not isinstance(actual, list) or expected not in actual:
                failures.append(f"{path}: expected to contain {expected!r}, got {actual!r}")
            continue
        actual = _read_path(policy, path)
        if actual != expected:
            failures.append(f"{path}: expected {expected!r}, got {actual!r}")
    replay = compose_policy(scenario.inputs)
    if replay.canonical_json() != decision.canonical_json():
        failures.append("same input did not produce the same canonical decision")
    return failures


def audit_all() -> tuple[list[str], list[str]]:
    passes: list[str] = []
    failures: list[str] = []
    for scenario in SCENARIOS:
        decision = compose_policy(scenario.inputs)
        scenario_failures = _scenario_failures(scenario, decision)
        if scenario_failures:
            failures.extend(
                f"{scenario.scenario_id}: {failure}"
                for failure in scenario_failures
            )
        else:
            passes.append(f"{scenario.scenario_id} [{decision.digest}]")
    return passes, failures


def _input_summary(scenario: Scenario) -> dict[str, object]:
    inputs = scenario.inputs
    return {
        "speaker": inputs.speaker_identity,
        "surface": inputs.surface,
        "context": inputs.context,
        "academic_stage": inputs.academic_stage,
        "relationship_stage": inputs.relationship_stage,
        "interaction_kind": inputs.interaction_kind,
        "reliable_user_fact_count": inputs.reliable_user_fact_count,
        "birth_temperament": inputs.temperament.as_dict(),
        "learned_adjustments": [
            f"{item.dimension}={item.value}@{item.scope}"
            for item in inputs.learned_adjustments
        ],
        "interaction_contracts": [
            f"{item.dimension}={item.value}"
            for item in inputs.interaction_contracts
        ],
        "negative_feedback": inputs.negative_feedback,
    }


def _render(scenario_index: int, message: str = "") -> None:
    scenario = SCENARIOS[scenario_index]
    decision = compose_policy(scenario.inputs)
    summary = _input_summary(scenario)
    birth = summary["birth_temperament"]
    policy = decision.policy.as_dict()
    style = policy["expression_style"]
    os.system("cls" if os.name == "nt" else "clear")
    print(f"{BOLD}CompanionPolicy V4 THROWAWAY PROTOTYPE{RESET}")
    print(
        f"{DIM}{scenario_index + 1}/{len(SCENARIOS)}  "
        f"{scenario.scenario_id}  digest={decision.digest}{RESET}"
    )
    print(f"{BOLD}{scenario.title}{RESET}")

    print(f"{BOLD}Inputs{RESET}")
    print(
        f"speaker={summary['speaker']} surface={summary['surface']} "
        f"context={summary['context']} kind={summary['interaction_kind']}"
    )
    print(
        f"age={summary['academic_stage']} relation={summary['relationship_stage']} "
        f"facts={summary['reliable_user_fact_count']}"
    )
    assert isinstance(birth, Mapping)
    print(
        "birth: "
        f"explore={birth['exploration_orientation']} "
        f"energy={birth['expression_energy']} "
        f"organize={birth['thought_organization']}"
    )
    print(
        "       "
        f"play={birth['playfulness']} initiative={birth['companion_initiative']}"
    )
    print(
        f"adjust={summary['learned_adjustments'] or '-'} "
        f"contract={summary['interaction_contracts'] or '-'}"
    )
    print(f"feedback={summary['negative_feedback'] or '-'}")

    print(f"{BOLD}Full policy state{RESET}")
    print(
        f"core={policy['core_identity']} age={policy['xiaoxin_age']}/"
        f"{policy['maturity']} relation={policy['relationship_stage']}"
    )
    print(
        f"reply={policy['response_length']} questions={policy['question_budget']} "
        f"closure={policy['closure_style']}"
    )
    print(
        f"memory={policy['memory_reference_budget']}/"
        f"{policy['memory_reference_depth']} scope={policy['memory_scope']}"
    )
    print(
        f"initiative={policy['initiative_level']} "
        f"posture={policy['emotional_posture']} "
        f"hardware={policy['hardware_expression']['intensity']}"
    )
    assert isinstance(style, Mapping)
    print(
        "style: "
        f"explore={style['exploration_orientation']} "
        f"energy={style['expression_energy']} "
        f"organize={style['thought_organization']}"
    )
    print(
        "       "
        f"initiative_bias={style['initiative_bias']} "
        f"humor={style['humor_level']}"
    )
    print(f"blocked={policy['prohibited_behaviors']}")
    print(f"version={policy['version']}")

    changed_steps = [
        step for step in decision.trace if step.before != step.after
    ]
    print(f"{BOLD}Winning constraint layers{RESET}")
    if not changed_steps:
        print(f"{DIM}(no value-changing constraints){RESET}")
    dimensions_by_layer: dict[str, list[str]] = {}
    aliases = {
        "response_length": "reply",
        "question_budget": "questions",
        "memory_reference_budget": "memory",
        "memory_reference_depth": "depth",
        "memory_scope": "scope",
        "initiative_level": "initiative",
        "emotional_posture": "posture",
        "closure_style": "closure",
        "playfulness": "play",
        "humor_level": "humor",
    }
    for step in changed_steps:
        dimensions_by_layer.setdefault(step.layer, []).append(
            aliases.get(step.dimension, step.dimension)
        )
    for layer, dimensions in dimensions_by_layer.items():
        print(f"- {layer}: {','.join(dimensions)}")

    failures = _scenario_failures(scenario, decision)
    verdict = "PASS" if not failures else "FAIL: " + "; ".join(failures)
    suffix = f" | {message}" if message else ""
    print(f"{BOLD}Verdict:{RESET} {verdict}{suffix}")
    print(
        f"{BOLD}[n]{RESET} next  {BOLD}[p]{RESET} previous  "
        f"{BOLD}[r]{RESET} replay  {BOLD}[a]{RESET} audit all  "
        f"{BOLD}[q]{RESET} quit"
    )


def _run_interactive() -> int:
    index = 0
    message = ""
    while True:
        _render(index, message)
        try:
            command = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if command == "q":
            return 0
        if command == "n":
            index = (index + 1) % len(SCENARIOS)
            message = "moved to next scenario"
        elif command == "p":
            index = (index - 1) % len(SCENARIOS)
            message = "moved to previous scenario"
        elif command == "r":
            first = compose_policy(SCENARIOS[index].inputs)
            second = compose_policy(SCENARIOS[index].inputs)
            message = (
                f"replay stable: {first.digest}"
                if first.canonical_json() == second.canonical_json()
                else "replay mismatch"
            )
        elif command == "a":
            passes, failures = audit_all()
            message = (
                f"all {len(passes)} scenarios passed"
                if not failures
                else f"{len(failures)} audit failures"
            )
        else:
            message = "unknown command"


def _run_audit() -> int:
    passes, failures = audit_all()
    for result in passes:
        print(f"PASS {result}")
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(passes)} passed, {len(failures)} failed")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit",
        action="store_true",
        help="run every scenario non-interactively",
    )
    args = parser.parse_args()
    if args.audit:
        return _run_audit()
    return _run_interactive()


if __name__ == "__main__":
    sys.exit(main())
