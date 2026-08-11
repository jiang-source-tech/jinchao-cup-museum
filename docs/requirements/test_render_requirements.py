from __future__ import annotations

from pathlib import Path
import sys

import pytest


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from render_requirements import (  # noqa: E402
    RequirementsValidationError,
    load_requirements,
    render_document,
)


SOURCE = HERE / "requirements.yaml"
TEMPLATE = HERE / "template.html"
OUTPUT = HERE / "index.html"


def test_requirements_source_is_valid_and_references_exist() -> None:
    data = load_requirements(SOURCE)
    ids = [item["id"] for item in data["requirements"]]
    sequences = [item["sequence"] for item in data["requirements"]]
    assert len(ids) == len(set(ids))
    assert sequences == sorted(sequences)
    assert len(data["requirements"]) == 18


def test_statuses_match_acceptance_completion() -> None:
    data = load_requirements(SOURCE)
    by_id = {item["id"]: item for item in data["requirements"]}
    assert by_id["REQ-003"]["status"] == "done"
    assert by_id["REQ-004"]["status"] == "done"
    assert by_id["REQ-015"]["status"] == "blocked"
    assert by_id["REQ-018"]["status"] == "deferred"


def test_rendered_dashboard_is_deterministic_and_contains_every_requirement() -> None:
    data = load_requirements(SOURCE)
    rendered = render_document(data, source_path=SOURCE, template_path=TEMPLATE)
    current = OUTPUT.read_text(encoding="utf-8")
    assert rendered == current
    for requirement in data["requirements"]:
        assert requirement["id"] in rendered
    assert "data-priority-filter=\"P0\"" in rendered
    assert "id=\"search-input\"" in rendered


def test_invalid_status_is_rejected() -> None:
    data = load_requirements(SOURCE)
    data["requirements"][0]["status"] = "done"
    data["requirements"][0]["acceptance"][0]["status"] = "pending"
    with pytest.raises(RequirementsValidationError):
        from render_requirements import validate_requirements

        validate_requirements(data)
