"""Terminal shell for the companion VA throwaway prototype."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
import sys

from scenarios import SCENARIOS, Scenario, ScenarioRun


BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"


def _digest(run: ScenarioRun) -> str:
    return sha256(run.canonical_json().encode("utf-8")).hexdigest()[:12]


def _failures(scenario: Scenario, run: ScenarioRun) -> list[str]:
    failures = [
        f"{item.label}: expected {item.expected!r}, got {item.actual!r}"
        for item in run.checks
        if not item.passed
    ]
    if scenario.run().canonical_json() != run.canonical_json():
        failures.append("same synthetic timeline produced a different canonical state")
    return failures


def audit_all() -> tuple[list[str], list[str]]:
    passes: list[str] = []
    failures: list[str] = []
    for scenario in SCENARIOS:
        run = scenario.run()
        scenario_failures = _failures(scenario, run)
        if scenario_failures:
            failures.extend(
                f"{scenario.scenario_id}: {failure}" for failure in scenario_failures
            )
        else:
            passes.append(f"{scenario.scenario_id} [{_digest(run)}]")
    return passes, failures


def _render(index: int, message: str = "") -> None:
    scenario = SCENARIOS[index]
    run = scenario.run()
    failures = _failures(scenario, run)
    os.system("cls" if os.name == "nt" else "clear")
    print(f"{BOLD}COMPANION VA V1 - THROWAWAY{RESET}")
    print(
        f"{DIM}{index + 1}/{len(SCENARIOS)} {scenario.scenario_id} "
        f"digest={_digest(run)}{RESET}"
    )
    print(f"{BOLD}{scenario.title}{RESET}")
    print(scenario.lesson)
    print()
    print(f"{BOLD}Timeline{RESET}")
    for item in run.timeline:
        va = item["va"]
        print(
            f"- {item['label']} @ {item['at']} "
            f"V={va['valence']:+.3f} A={va['arousal']:+.3f}"
        )
        if "projection" in item:
            projection = item["projection"]
            print(
                "  projection="
                f"{projection['emotional_posture']} / "
                f"{projection['hardware_expression']['kind']}"
            )
        if "restore_status" in item:
            print(f"  restore={item['restore_status']}")
    print()
    print(f"{BOLD}Assertions{RESET}")
    for check in run.checks:
        marker = "PASS" if check.passed else "FAIL"
        print(f"- {marker}: {check.label}")
    verdict = "PASS" if not failures else "FAIL: " + "; ".join(failures)
    suffix = f" | {message}" if message else ""
    print(f"{BOLD}Verdict:{RESET} {verdict}{suffix}")
    print(
        f"{BOLD}[n]{RESET} next  {BOLD}[p]{RESET} previous  "
        f"{BOLD}[r]{RESET} replay  {BOLD}[a]{RESET} audit all  "
        f"{BOLD}[j]{RESET} JSON  {BOLD}[q]{RESET} quit"
    )


def _run_interactive(start_index: int) -> int:
    index = start_index
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
            first = SCENARIOS[index].run()
            second = SCENARIOS[index].run()
            message = (
                "deterministic replay matched"
                if first.canonical_json() == second.canonical_json()
                else "deterministic replay FAILED"
            )
        elif command == "a":
            passes, failures = audit_all()
            message = f"audit: {len(passes)} passed, {len(failures)} failed"
        elif command == "j":
            print(json.dumps(SCENARIOS[index].run().timeline, ensure_ascii=False, indent=2))
            input("press Enter to continue")
            message = "rendered canonical timeline"
        else:
            message = "unknown command"


def _scenario_index(scenario_id: str | None) -> int:
    if scenario_id is None:
        return 0
    for index, scenario in enumerate(SCENARIOS):
        if scenario.scenario_id == scenario_id:
            return index
    choices = ", ".join(item.scenario_id for item in SCENARIOS)
    raise SystemExit(f"unknown scenario {scenario_id!r}; choose one of: {choices}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--scenario")
    args = parser.parse_args()
    if args.audit:
        passes, failures = audit_all()
        for item in passes:
            print(f"PASS {item}")
        for item in failures:
            print(f"FAIL {item}")
        print(f"summary: {len(passes)} passed, {len(failures)} failed")
        return 1 if failures else 0
    return _run_interactive(_scenario_index(args.scenario))


if __name__ == "__main__":
    raise SystemExit(main())
