from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    import yaml
except ImportError:  # pragma: no cover - depends on local Python environment.
    yaml = None


ROOT = Path(__file__).resolve().parent
YAML_PATH = ROOT / "requirements.yaml"
HTML_PATH = ROOT / "requirements.html"

REQUIRED_TOP_LEVEL = (
    "meta",
    "taxonomy",
    "repositories",
    "modules",
    "milestones",
    "items",
    "risks",
    "decisions",
)

REQUIRED_ITEM_FIELDS = (
    "id",
    "title",
    "area",
    "module",
    "milestone",
    "status",
    "priority",
    "kind",
    "summary",
    "implemented",
    "remaining",
    "optimizations",
    "acceptance",
    "evidence",
    "related",
    "confidence",
)


def json_ready(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    return value


def load_requirements() -> dict[str, Any]:
    if yaml is None:
        return {
            "ok": False,
            "errors": ["未安装 PyYAML。请执行：python -m pip install pyyaml"],
            "data": None,
        }

    try:
        raw = YAML_PATH.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
    except Exception as exc:
        return {
            "ok": False,
            "errors": [f"解析 requirements.yaml 失败：{exc}"],
            "data": None,
        }

    errors = validate_requirements(data)
    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "data": json_ready(data),
    }


def validate_requirements(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["YAML 根节点必须是映射对象。"]

    for key in REQUIRED_TOP_LEVEL:
        if key not in data:
            errors.append(f"缺少顶层字段：{key}")
    if errors:
        return errors

    taxonomy = data.get("taxonomy") or {}
    repositories = data.get("repositories") or []
    modules = data.get("modules") or []
    milestones = data.get("milestones") or []
    items = data.get("items") or []
    risks = data.get("risks") or []
    decisions = data.get("decisions") or []

    taxonomy_sets = collect_taxonomy_sets(taxonomy, errors)
    repository_ids = collect_ids(repositories, "id", "repositories", errors)
    module_ids = collect_ids(modules, "id", "modules", errors)
    milestone_ids = collect_ids(milestones, "id", "milestones", errors)
    item_ids = collect_ids(items, "id", "items", errors)
    risk_ids = collect_ids(risks, "id", "risks", errors)
    collect_ids(decisions, "id", "decisions", errors)

    validate_modules(modules, taxonomy_sets, errors)
    validate_milestones(milestones, taxonomy_sets, errors)
    validate_items(items, taxonomy_sets, module_ids, milestone_ids, errors)
    validate_risks(risks, item_ids, errors)
    validate_decisions(decisions, item_ids, errors)
    validate_requirement_section(
        "integrated_companion_requirements",
        data.get("integrated_companion_requirements"),
        taxonomy_sets,
        item_ids,
        errors,
    )
    validate_requirement_section(
        "mini_program_requirements",
        data.get("mini_program_requirements"),
        taxonomy_sets,
        item_ids,
        errors,
    )
    validate_requirement_section(
        "hardware_requirements",
        data.get("hardware_requirements"),
        taxonomy_sets,
        item_ids,
        errors,
    )
    validate_requirement_section(
        "companion_growth_requirements",
        data.get("companion_growth_requirements"),
        taxonomy_sets,
        item_ids,
        errors,
    )
    validate_requirement_section(
        "companion_productization_requirements",
        data.get("companion_productization_requirements"),
        taxonomy_sets,
        item_ids,
        errors,
    )

    if not repository_ids:
        errors.append("repositories 至少需要包含一个仓库。")
    if not module_ids:
        errors.append("modules 至少需要包含一个模块。")
    if not milestone_ids:
        errors.append("milestones 至少需要包含一个里程碑。")
    if len(item_ids) < 12:
        errors.append("items 至少需要包含 12 个项目条目。")
    if not risk_ids:
        errors.append("risks 至少需要包含一个风险。")

    return errors


def collect_taxonomy_sets(taxonomy: Any, errors: list[str]) -> dict[str, set[str]]:
    taxonomy_sets: dict[str, set[str]] = {}
    if not isinstance(taxonomy, dict):
        errors.append("taxonomy 必须是映射对象。")
        return taxonomy_sets

    for key in ("statuses", "areas", "kinds", "priorities", "confidence"):
        values = taxonomy.get(key)
        if not isinstance(values, dict) or not values:
            errors.append(f"taxonomy.{key} 必须是非空映射对象。")
            taxonomy_sets[key] = set()
        else:
            taxonomy_sets[key] = {str(value) for value in values.keys()}
    return taxonomy_sets


def collect_ids(items: Any, key: str, label: str, errors: list[str]) -> set[str]:
    ids: set[str] = set()
    if not isinstance(items, list):
        errors.append(f"{label} 必须是列表。")
        return ids

    duplicates: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{label}[{index}] 必须是映射对象。")
            continue
        value = item.get(key)
        if not value:
            errors.append(f"{label}[{index}] 缺少 {key}。")
            continue
        text = str(value)
        if text in ids:
            duplicates.add(text)
        ids.add(text)

    for value in sorted(duplicates):
        errors.append(f"{label} 包含重复的 {key}：{value}")
    return ids


def validate_modules(
    modules: Any, taxonomy_sets: dict[str, set[str]], errors: list[str]
) -> None:
    if not isinstance(modules, list):
        return
    areas = taxonomy_sets.get("areas", set())
    for module in modules:
        if not isinstance(module, dict):
            continue
        label = str(module.get("id") or "module")
        area = module.get("area")
        if area not in areas:
            errors.append(f"{label}: area={area!r} 未在 taxonomy.areas 中定义。")


def validate_milestones(
    milestones: Any, taxonomy_sets: dict[str, set[str]], errors: list[str]
) -> None:
    if not isinstance(milestones, list):
        return
    statuses = taxonomy_sets.get("statuses", set())
    for milestone in milestones:
        if not isinstance(milestone, dict):
            continue
        label = str(milestone.get("id") or "milestone")
        status = milestone.get("status")
        if status not in statuses:
            errors.append(
                f"{label}: status={status!r} 未在 taxonomy.statuses 中定义。"
            )


def validate_items(
    items: Any,
    taxonomy_sets: dict[str, set[str]],
    module_ids: set[str],
    milestone_ids: set[str],
    errors: list[str],
) -> None:
    if not isinstance(items, list):
        return

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        label = str(item.get("id") or f"items[{index}]")

        for field in REQUIRED_ITEM_FIELDS:
            if field not in item:
                errors.append(f"{label}: 缺少必填字段 {field}")

        validate_taxonomy_ref(item, "area", "areas", taxonomy_sets, label, errors)
        validate_taxonomy_ref(item, "status", "statuses", taxonomy_sets, label, errors)
        validate_taxonomy_ref(item, "priority", "priorities", taxonomy_sets, label, errors)
        validate_taxonomy_ref(item, "kind", "kinds", taxonomy_sets, label, errors)
        validate_taxonomy_ref(
            item, "confidence", "confidence", taxonomy_sets, label, errors
        )

        module = item.get("module")
        if module is not None and str(module) not in module_ids:
            errors.append(f"{label}: module={module!r} 未在 modules 中定义。")

        milestone = item.get("milestone")
        if milestone is not None and str(milestone) not in milestone_ids:
            errors.append(
                f"{label}: milestone={milestone!r} 未在 milestones 中定义。"
            )

        for field in (
            "implemented",
            "remaining",
            "optimizations",
            "acceptance",
            "evidence",
            "related",
        ):
            if field in item and not isinstance(item[field], list):
                errors.append(f"{label}: {field} 必须是列表。")

        if "acceptance" in item and not item["acceptance"]:
            errors.append(f"{label}: acceptance 至少需要包含一项。")


def validate_requirement_section(
    section_key: str,
    section: Any,
    taxonomy_sets: dict[str, set[str]],
    item_ids: set[str],
    errors: list[str],
) -> None:
    if section is None:
        return
    if not isinstance(section, dict):
        errors.append(f"{section_key} 必须是映射对象。")
        return

    columns = section.get("columns")
    if not isinstance(columns, list):
        errors.append(f"{section_key}.columns 必须是列表。")
        return

    statuses = taxonomy_sets.get("statuses", set())
    priorities = taxonomy_sets.get("priorities", set())
    seen_ids: set[str] = set()
    duplicates: set[str] = set()

    for index, column in enumerate(columns):
        if not isinstance(column, dict):
            errors.append(f"{section_key}.columns[{index}] 必须是映射对象。")
            continue

        column_id = str(column.get("id") or f"columns[{index}]")
        label = f"{section_key}.{column_id}"

        for field in ("id", "title", "priority", "status"):
            if field not in column:
                errors.append(f"{label}: 缺少必填字段 {field}")

        if column_id in seen_ids:
            duplicates.add(column_id)
        seen_ids.add(column_id)

        status = column.get("status")
        if status not in statuses:
            errors.append(
                f"{label}: status={status!r} 未在 taxonomy.statuses 中定义。"
            )

        priority = column.get("priority")
        if priority not in priorities:
            errors.append(
                f"{label}: priority={priority!r} 未在 taxonomy.priorities 中定义。"
            )

        for field in (
            "implemented",
            "requirements",
            "implementation_plan",
            "remaining",
            "acceptance",
            "related_items",
        ):
            if field in column and not isinstance(column[field], list):
                errors.append(f"{label}: {field} 必须是列表。")

        for ref in column.get("related_items", []) or []:
            if str(ref) not in item_ids:
                errors.append(f"{label}: related_items 引用了未知条目 {ref!r}。")

    for column_id in sorted(duplicates):
        errors.append(f"{section_key}.columns 包含重复的 id：{column_id}")


def validate_taxonomy_ref(
    item: dict[str, Any],
    field: str,
    taxonomy_key: str,
    taxonomy_sets: dict[str, set[str]],
    label: str,
    errors: list[str],
) -> None:
    value = item.get(field)
    if value is not None and str(value) not in taxonomy_sets.get(taxonomy_key, set()):
        errors.append(f"{label}: {field}={value!r} 未在 taxonomy.{taxonomy_key} 中定义。")


def validate_risks(risks: Any, item_ids: set[str], errors: list[str]) -> None:
    if not isinstance(risks, list):
        return
    for risk in risks:
        if not isinstance(risk, dict):
            continue
        label = str(risk.get("id") or "risk")
        for ref in risk.get("related_items", []) or []:
            if str(ref) not in item_ids:
                errors.append(f"{label}: related_items 引用了未知条目 {ref!r}。")


def validate_decisions(decisions: Any, item_ids: set[str], errors: list[str]) -> None:
    if not isinstance(decisions, list):
        return
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        label = str(decision.get("id") or "decision")
        for ref in decision.get("related_items", []) or []:
            if str(ref) not in item_ids:
                errors.append(f"{label}: related_items 引用了未知条目 {ref!r}。")


class RequirementsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/requirements", "/requirements.html"):
            self.send_file(HTML_PATH, "text/html; charset=utf-8")
            return
        if path == "/requirements.json":
            self.send_json(load_requirements())
            return
        if path == "/requirements.yaml":
            self.send_file(YAML_PATH, "text/yaml; charset=utf-8")
            return
        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self.send_error(404, "未找到")

    def send_file(self, path: Path, content_type: str) -> None:
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            self.send_error(404, f"缺少文件：{path.name}")
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[requirements] {self.address_string()} - {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="启动小芯项目状态工作台本地服务。")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), RequirementsHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"小芯项目状态工作台已启动：{url}")
    print("修改 docs/requirements/requirements.yaml 后刷新浏览器即可。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止小芯项目状态工作台。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
