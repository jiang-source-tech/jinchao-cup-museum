from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
from pathlib import Path
import sys
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE_DIR / "requirements.yaml"
DEFAULT_TEMPLATE = BASE_DIR / "template.html"
DEFAULT_OUTPUT = BASE_DIR / "index.html"

ALLOWED_PRIORITIES = {"P0", "P1", "P2"}
ALLOWED_STATUSES = {"done", "in_progress", "pending", "blocked", "deferred"}
ALLOWED_CRITERION_STATUSES = {"done", "pending", "blocked"}
ALLOWED_CONFIDENCE = {"high", "medium", "low", "unknown"}
STATUS_LABELS = {
    "done": "已完成",
    "in_progress": "进行中",
    "pending": "未开始",
    "blocked": "受阻",
    "deferred": "已后置",
}
STATUS_SHORT_LABELS = {
    "done": "完成",
    "in_progress": "进行中",
    "pending": "待开始",
    "blocked": "阻塞",
    "deferred": "后置",
}
PRIORITY_LABELS = {"P0": "核心交付", "P1": "产品完善", "P2": "后续评估"}
CONFIDENCE_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
    "unknown": "未知",
}
EVIDENCE_KIND_LABELS = {
    "document": "文档",
    "data": "数据",
    "code": "代码",
    "test": "测试",
    "external_code": "外部仓库",
    "manual": "人工记录",
}


class RequirementsValidationError(ValueError):
    """Raised when the requirements source is structurally or semantically invalid."""


def load_requirements(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise RequirementsValidationError("requirements YAML must contain a mapping at the root")
    validate_requirements(data, source_path=path)
    return data


def validate_requirements(data: dict[str, Any], *, source_path: Path | None = None) -> None:
    errors: list[str] = []
    project = data.get("project")
    phases = data.get("phases")
    requirements = data.get("requirements")
    if not isinstance(project, dict):
        errors.append("project must be a mapping")
    if not isinstance(phases, list) or not phases:
        errors.append("phases must be a non-empty list")
    if not isinstance(requirements, list) or not requirements:
        errors.append("requirements must be a non-empty list")
    if errors:
        raise RequirementsValidationError("; ".join(errors))

    phase_ids: set[str] = set()
    phase_sequences: set[int] = set()
    for index, phase in enumerate(phases, start=1):
        if not isinstance(phase, dict):
            errors.append(f"phases[{index}] must be a mapping")
            continue
        phase_id = phase.get("id")
        sequence = phase.get("sequence")
        for key in ("id", "title", "summary"):
            if not isinstance(phase.get(key), str) or not phase[key].strip():
                errors.append(f"phases[{index}].{key} must be a non-empty string")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            errors.append(f"phases[{index}].sequence must be a non-negative integer")
        else:
            if sequence in phase_sequences:
                errors.append(f"duplicate phase sequence: {sequence}")
            phase_sequences.add(sequence)
        if isinstance(phase_id, str):
            if phase_id in phase_ids:
                errors.append(f"duplicate phase id: {phase_id}")
            phase_ids.add(phase_id)

    requirement_ids: set[str] = set()
    requirement_sequences: set[int] = set()
    criterion_ids: set[str] = set()
    for index, requirement in enumerate(requirements, start=1):
        prefix = f"requirements[{index}]"
        if not isinstance(requirement, dict):
            errors.append(f"{prefix} must be a mapping")
            continue
        required_strings = (
            "id",
            "title",
            "kind",
            "phase",
            "owner",
            "confidence",
            "summary",
            "next_action",
        )
        for key in required_strings:
            if not isinstance(requirement.get(key), str) or not requirement[key].strip():
                errors.append(f"{prefix}.{key} must be a non-empty string")

        requirement_id = requirement.get("id")
        if isinstance(requirement_id, str):
            if requirement_id in requirement_ids:
                errors.append(f"duplicate requirement id: {requirement_id}")
            requirement_ids.add(requirement_id)
        sequence = requirement.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            errors.append(f"{prefix}.sequence must be a positive integer")
        elif sequence in requirement_sequences:
            errors.append(f"duplicate requirement sequence: {sequence}")
        else:
            requirement_sequences.add(sequence)

        if requirement.get("priority") not in ALLOWED_PRIORITIES:
            errors.append(f"{prefix}.priority must be one of {sorted(ALLOWED_PRIORITIES)}")
        if requirement.get("status") not in ALLOWED_STATUSES:
            errors.append(f"{prefix}.status must be one of {sorted(ALLOWED_STATUSES)}")
        if requirement.get("confidence") not in ALLOWED_CONFIDENCE:
            errors.append(f"{prefix}.confidence must be one of {sorted(ALLOWED_CONFIDENCE)}")
        if requirement.get("phase") not in phase_ids:
            errors.append(f"{prefix}.phase references unknown phase {requirement.get('phase')!r}")

        acceptance = requirement.get("acceptance")
        if not isinstance(acceptance, list) or not acceptance:
            errors.append(f"{prefix}.acceptance must be a non-empty list")
            acceptance = []
        completed = 0
        has_blocked_criterion = False
        for criterion_index, criterion in enumerate(acceptance, start=1):
            criterion_prefix = f"{prefix}.acceptance[{criterion_index}]"
            if not isinstance(criterion, dict):
                errors.append(f"{criterion_prefix} must be a mapping")
                continue
            criterion_id = criterion.get("id")
            criterion_text = criterion.get("text")
            criterion_status = criterion.get("status")
            if not isinstance(criterion_id, str) or not criterion_id.strip():
                errors.append(f"{criterion_prefix}.id must be a non-empty string")
            elif criterion_id in criterion_ids:
                errors.append(f"duplicate acceptance id: {criterion_id}")
            else:
                criterion_ids.add(criterion_id)
            if not isinstance(criterion_text, str) or not criterion_text.strip():
                errors.append(f"{criterion_prefix}.text must be a non-empty string")
            if criterion_status not in ALLOWED_CRITERION_STATUSES:
                errors.append(
                    f"{criterion_prefix}.status must be one of {sorted(ALLOWED_CRITERION_STATUSES)}"
                )
            elif criterion_status == "done":
                completed += 1
            elif criterion_status == "blocked":
                has_blocked_criterion = True

        status = requirement.get("status")
        total = len(acceptance)
        if status == "done" and completed != total:
            errors.append(f"{prefix}.status=done but {completed}/{total} acceptance criteria are done")
        if status == "pending" and completed != 0:
            errors.append(f"{prefix}.status=pending but it already has completed acceptance criteria")
        if status == "in_progress" and not (0 < completed < total):
            errors.append(f"{prefix}.status=in_progress requires a partial completion count")
        if status == "blocked" and not has_blocked_criterion:
            errors.append(f"{prefix}.status=blocked requires at least one blocked acceptance criterion")
        if status == "deferred" and completed != 0:
            errors.append(f"{prefix}.status=deferred cannot have completed acceptance criteria")

        dependencies = requirement.get("dependencies")
        if not isinstance(dependencies, list) or len(set(dependencies)) != len(dependencies):
            errors.append(f"{prefix}.dependencies must be a list of unique requirement IDs")
        else:
            for dependency in dependencies:
                if not isinstance(dependency, str) or not dependency.strip():
                    errors.append(f"{prefix}.dependencies contains an invalid ID")

        tags = requirement.get("tags")
        if not isinstance(tags, list) or not tags or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
            errors.append(f"{prefix}.tags must be a non-empty list of strings")

        evidence = requirement.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{prefix}.evidence must be a non-empty list")
            evidence = []
        for evidence_index, item in enumerate(evidence, start=1):
            evidence_prefix = f"{prefix}.evidence[{evidence_index}]"
            if not isinstance(item, dict):
                errors.append(f"{evidence_prefix} must be a mapping")
                continue
            if item.get("kind") not in EVIDENCE_KIND_LABELS:
                errors.append(f"{evidence_prefix}.kind is invalid")
            for key in ("label", "note"):
                if not isinstance(item.get(key), str) or not item[key].strip():
                    errors.append(f"{evidence_prefix}.{key} must be a non-empty string")
            has_path = isinstance(item.get("path"), str) and bool(item["path"].strip())
            has_reference = isinstance(item.get("reference"), str) and bool(item["reference"].strip())
            if has_path == has_reference:
                errors.append(f"{evidence_prefix} must contain exactly one of path or reference")
            if has_path and source_path is not None:
                evidence_path = (source_path.parent / item["path"]).resolve()
                if not evidence_path.exists():
                    errors.append(f"{evidence_prefix}.path does not exist: {item['path']}")

        blockers = requirement.get("blockers", [])
        if blockers is not None and (
            not isinstance(blockers, list)
            or any(not isinstance(blocker, str) or not blocker.strip() for blocker in blockers)
        ):
            errors.append(f"{prefix}.blockers must be a list of non-empty strings")

    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        for dependency in requirement.get("dependencies", []):
            if dependency not in requirement_ids:
                errors.append(f"{requirement.get('id')}.dependencies references unknown ID {dependency}")

    _validate_dependency_cycles(requirements, errors)
    if errors:
        location = f" in {source_path}" if source_path else ""
        raise RequirementsValidationError("\n".join(f"- {error}" for error in errors) + location)


def _validate_dependency_cycles(requirements: list[Any], errors: list[str]) -> None:
    graph = {
        requirement["id"]: requirement.get("dependencies", [])
        for requirement in requirements
        if isinstance(requirement, dict) and isinstance(requirement.get("id"), str)
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, trail: list[str]) -> None:
        if node in visiting:
            cycle = " -> ".join(trail + [node])
            errors.append(f"dependency cycle: {cycle}")
            return
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph.get(node, []):
            if dependency in graph:
                visit(dependency, trail + [node])
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node, [])


def prepare_model(data: dict[str, Any], *, source_path: Path) -> dict[str, Any]:
    phase_lookup = {phase["id"]: phase for phase in data["phases"]}
    requirements: list[dict[str, Any]] = []
    for requirement in sorted(data["requirements"], key=lambda item: item["sequence"]):
        acceptance = requirement["acceptance"]
        completed = sum(item["status"] == "done" for item in acceptance)
        total = len(acceptance)
        phase = phase_lookup[requirement["phase"]]
        enriched = dict(requirement)
        enriched["status_label"] = STATUS_LABELS[requirement["status"]]
        enriched["status_short_label"] = STATUS_SHORT_LABELS[requirement["status"]]
        enriched["priority_label"] = PRIORITY_LABELS[requirement["priority"]]
        enriched["confidence_label"] = CONFIDENCE_LABELS[requirement["confidence"]]
        enriched["progress"] = round((completed / total) * 100) if total else 0
        enriched["progress_text"] = f"{completed} / {total}"
        enriched["sequence_label"] = f"{requirement['sequence']:03d}"
        enriched["phase_title"] = phase["title"]
        enriched["phase_sequence"] = phase["sequence"]
        enriched["evidence_count"] = len(requirement["evidence"])
        enriched["has_blockers"] = bool(requirement.get("blockers"))
        enriched["blockers"] = list(requirement.get("blockers", []))
        enriched["search_text"] = " ".join(
            [
                requirement["id"],
                requirement["title"],
                requirement["summary"],
                requirement["owner"],
                requirement["phase"],
                phase["title"],
                " ".join(requirement["tags"]),
            ]
        ).lower()
        for evidence in enriched["evidence"]:
            evidence["kind_label"] = EVIDENCE_KIND_LABELS[evidence["kind"]]
            evidence["href"] = evidence.get("path", "").replace("\\", "/")
        requirements.append(enriched)

    phase_rows: list[dict[str, Any]] = []
    for phase in sorted(data["phases"], key=lambda item: item["sequence"]):
        phase_requirements = [item for item in requirements if item["phase"] == phase["id"]]
        criteria = [criterion for item in phase_requirements for criterion in item["acceptance"]]
        done_criteria = sum(criterion["status"] == "done" for criterion in criteria)
        phase_row = dict(phase)
        phase_row["requirement_count"] = len(phase_requirements)
        phase_row["done_count"] = sum(item["status"] == "done" for item in phase_requirements)
        phase_row["progress"] = round((done_criteria / len(criteria)) * 100) if criteria else 0
        phase_row["progress_text"] = f"{done_criteria} / {len(criteria)} 条验收"
        phase_rows.append(phase_row)

    all_criteria = [criterion for item in requirements for criterion in item["acceptance"]]
    status_counts = Counter(item["status"] for item in requirements)
    completed_criteria = sum(item["status"] == "done" for item in all_criteria)
    stats = {
        "total": len(requirements),
        "done": status_counts["done"],
        "in_progress": status_counts["in_progress"],
        "pending": status_counts["pending"],
        "blocked": status_counts["blocked"],
        "deferred": status_counts["deferred"],
        "criteria_done": completed_criteria,
        "criteria_total": len(all_criteria),
        "progress": round((completed_criteria / len(all_criteria)) * 100) if all_criteria else 0,
    }
    source_digest = sha256(source_path.read_bytes()).hexdigest()[:12]
    return {
        "project": data["project"],
        "phases": phase_rows,
        "requirements": requirements,
        "stats": stats,
        "status_labels": STATUS_LABELS,
        "priority_labels": PRIORITY_LABELS,
        "source_digest": source_digest,
    }


def render_document(
    data: dict[str, Any],
    *,
    source_path: Path,
    template_path: Path = DEFAULT_TEMPLATE,
) -> str:
    model = prepare_model(data, source_path=source_path)
    environment = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = environment.get_template(template_path.name)
    return template.render(**model)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and render the requirements dashboard")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="requirements YAML path")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE, help="HTML template path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="generated HTML path")
    parser.add_argument("--check", action="store_true", help="fail if the generated HTML is stale")
    parser.add_argument("--validate-only", action="store_true", help="validate YAML without writing HTML")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = args.input.resolve()
    template_path = args.template.resolve()
    output_path = args.output.resolve()
    try:
        data = load_requirements(input_path)
        if args.validate_only:
            print(f"VALID: {input_path}")
            return 0
        html = render_document(data, source_path=input_path, template_path=template_path)
        if args.check:
            if not output_path.exists():
                print(f"STALE: generated file does not exist: {output_path}", file=sys.stderr)
                return 1
            if output_path.read_text(encoding="utf-8") != html:
                print(f"STALE: regenerate {output_path}", file=sys.stderr)
                return 1
            print(f"CURRENT: {output_path}")
            return 0
        output_path.write_text(html, encoding="utf-8", newline="\n")
        model = prepare_model(data, source_path=input_path)
        print(
            f"RENDERED: {output_path} | {model['stats']['total']} requirements | "
            f"{model['stats']['progress']}% acceptance completion | source {model['source_digest']}"
        )
        return 0
    except (OSError, RequirementsValidationError, yaml.YAMLError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
