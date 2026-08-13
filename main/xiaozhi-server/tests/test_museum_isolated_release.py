from __future__ import annotations

from datetime import datetime
from pathlib import Path

from scripts.build_museum_isolated_release import build_isolated_release


CONTENT_DIRECTORY = Path(__file__).parents[1] / "content" / "museum"


def test_isolated_release_publishes_all_content_and_records_manifest(tmp_path):
    result = build_isolated_release(
        content_directory=CONTENT_DIRECTORY,
        database_path=tmp_path / "stage3-isolated.db",
        reviewer="test-reviewer",
        publisher="test-publisher",
        occurred_at=datetime.fromisoformat("2026-08-12T12:00:00+00:00"),
    )

    assert result["exhibit_count"] == 100
    assert result["published_revision_count"] == 100
    assert result["published_fact_count"] == 171
    assert result["sqlite_integrity"] == "ok"
    assert str(result["collection"]) == "museum_facts_stage3_isolated_v1"
