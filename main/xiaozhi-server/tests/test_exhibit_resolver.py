from __future__ import annotations

from datetime import datetime
from pathlib import Path

from core.museum.exhibit_resolver import ExhibitResolver
from core.museum.content_import import (
    import_draft_content,
    load_content_package,
    publish_revision,
    review_revision,
)
from core.museum.store import MuseumStore


def _resolver(tmp_path) -> ExhibitResolver:
    store = MuseumStore(tmp_path / "museum.db")
    store.seed_demo_content()
    return ExhibitResolver(store)


def test_resolves_canonical_name_and_alias_as_explicit(tmp_path):
    resolver = _resolver(tmp_path)

    canonical = resolver.resolve(
        question="战国水晶杯是什么材质？",
        current_exhibit_id=None,
    )
    alias = resolver.resolve(
        question="水晶杯是怎么做出来的？",
        current_exhibit_id=None,
    )

    assert canonical.status == "explicit"
    assert canonical.exhibit_id == "warring-states-crystal-cup"
    assert alias.status == "explicit"
    assert alias.exhibit_id == "warring-states-crystal-cup"


def test_inherits_current_exhibit_only_without_a_new_reference(tmp_path):
    resolver = _resolver(tmp_path)

    result = resolver.resolve(
        question="它为什么这么透明？",
        current_exhibit_id="warring-states-crystal-cup",
    )

    assert result.status == "inherited"
    assert result.exhibit_id == "warring-states-crystal-cup"

    colloquial = resolver.resolve(
        question="这么硬，古人当时是怎么把它做出来的？",
        current_exhibit_id="warring-states-crystal-cup",
    )
    assert colloquial.status == "inherited"
    assert colloquial.exhibit_id == "warring-states-crystal-cup"

    recent_reference = resolver.resolve(
        question="刚才那个杯子为什么这么透明？",
        current_exhibit_id="warring-states-crystal-cup",
    )
    assert recent_reference.status == "inherited"
    assert recent_reference.exhibit_id == "warring-states-crystal-cup"

    cloth_reference = resolver.resolve(
        question="这块衣料上的图案是怎么做上去的？",
        current_exhibit_id="warring-states-crystal-cup",
    )
    assert cloth_reference.status == "inherited"
    assert cloth_reference.exhibit_id == "warring-states-crystal-cup"

    continued_question = resolver.resolve(
        question="那又是从哪儿挖出来的？",
        current_exhibit_id="warring-states-crystal-cup",
    )
    assert continued_question.status == "inherited"
    assert continued_question.exhibit_id == "warring-states-crystal-cup"


def test_missing_reference_does_not_guess_without_session_context(tmp_path):
    resolver = _resolver(tmp_path)

    result = resolver.resolve(
        question="它为什么这么透明？",
        current_exhibit_id=None,
    )

    assert result.status == "missing"
    assert result.exhibit_id is None


def test_unlisted_exhibit_reference_does_not_inherit_old_context(tmp_path):
    resolver = _resolver(tmp_path)

    result = resolver.resolve(
        question="换成越王勾践剑，它是什么材质？",
        current_exhibit_id="warring-states-crystal-cup",
    )

    assert result.status == "not_found"
    assert result.exhibit_id is None

    polite = resolver.resolve(
        question="我想知道越王勾践剑是什么材质？",
        current_exhibit_id="warring-states-crystal-cup",
    )
    assert polite.status == "not_found"

    possessive = resolver.resolve(
        question="那越王勾践剑的材质呢？",
        current_exhibit_id="warring-states-crystal-cup",
    )
    assert possessive.status == "not_found"

    introductory = resolver.resolve(
        question="介绍一下不存在的测试展品",
        current_exhibit_id=None,
    )
    assert introductory.status == "not_found"


def test_ambiguous_alias_does_not_bind_a_random_exhibit(tmp_path):
    store = MuseumStore(tmp_path / "museum.db")
    store.seed_demo_content()
    with store.connection() as connection:
        connection.execute(
            """
            UPDATE exhibit
            SET aliases_json = ?
            WHERE id = ?
            """,
            ('["水晶杯", "战国时期水晶杯", "杯子"]', "warring-states-crystal-cup"),
        )
        connection.execute(
            """
            INSERT INTO exhibit(id, zone_id, name, aliases_json, image_uri, status)
            VALUES (?, ?, ?, ?, NULL, 'active')
            """,
            (
                "demo-glass-cup",
                "hangzhou-history-demo-zone",
                "玻璃杯",
                '["杯子"]',
            ),
        )
        connection.execute(
            """
            INSERT INTO content_revision(
                id, exhibit_id, revision_no, status,
                reviewed_by, reviewed_at, published_at
            ) VALUES (?, ?, 1, 'published', 'test-reviewer', ?, ?)
            """,
            (
                "demo-glass-cup-r1",
                "demo-glass-cup",
                "2026-08-11T00:00:00+00:00",
                "2026-08-11T00:00:00+00:00",
            ),
        )
    resolver = ExhibitResolver(store)

    result = resolver.resolve(
        question="杯子是什么材质？",
        current_exhibit_id=None,
    )

    assert result.status == "ambiguous"
    assert set(result.candidate_ids) == {
        "warring-states-crystal-cup",
        "demo-glass-cup",
    }


def test_structured_ambiguous_alias_returns_all_published_candidates(tmp_path):
    store = MuseumStore(tmp_path / "museum.db")
    package_path = (
        Path(__file__).parents[1]
        / "content"
        / "museum"
        / "china-national-silk-museum-stage3-catalog.json"
    )
    package = load_content_package(package_path)
    import_draft_content(store, package)
    occurred_at = datetime.fromisoformat("2026-08-12T12:00:00+00:00")
    for exhibit in package.exhibits:
        review_revision(
            store,
            revision_id=exhibit.revision.id,
            reviewed_by="test-reviewer",
            reviewed_at=occurred_at,
        )
        publish_revision(
            store,
            revision_id=exhibit.revision.id,
            published_by="test-publisher",
            published_at=occurred_at,
        )

    result = ExhibitResolver(store).resolve(
        question="介绍一下碗",
        current_exhibit_id=None,
    )

    assert result.status == "ambiguous"
    assert result.matched_text == "碗"
    assert set(result.candidate_ids) == {
        "china-silk-catalog-3287",
        "china-silk-catalog-3288",
        "china-silk-catalog-3289",
        "china-silk-catalog-3290",
        "china-silk-catalog-3291",
        "china-silk-catalog-3292",
        "china-silk-catalog-3293",
    }
