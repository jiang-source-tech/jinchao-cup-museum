from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.http import models

from core.museum.evidence_index import QdrantEvidenceIndex


def _records():
    return [
        {
            "segment_id": "segment-cup-material",
            "source_id": "source-catalog",
            "exhibit_ids": ["exhibit-cup"],
            "source_level": "demo_curated",
            "text_hash": "hash-material",
        },
        {
            "segment_id": "segment-cup-era",
            "source_id": "source-catalog",
            "exhibit_ids": ["exhibit-cup"],
            "source_level": "secondary_public_source",
            "text_hash": "hash-era",
        },
        {
            "segment_id": "segment-vase-material",
            "source_id": "source-vase",
            "exhibit_ids": ["exhibit-vase"],
            "source_level": "demo_curated",
            "text_hash": "hash-vase",
        },
    ]


def test_evidence_index_build_search_and_payload_validation():
    client = QdrantClient(location=":memory:")
    index = QdrantEvidenceIndex(
        client=client,
        url="http://unused.local",
        collection_name="museum-evidence-test",
        dimension=3,
    )
    records = _records()
    vectors = [
        [1.0, 0.0, 0.0],
        [0.8, 0.2, 0.0],
        [0.0, 1.0, 0.0],
    ]
    assert index.create_physical_collection(records, vectors) == 3
    validation = index.validate(expected_count=3)
    assert validation.ok

    hits = index.search(
        vector=[1.0, 0.0, 0.0],
        exhibit_ids=("exhibit-cup",),
        limit=5,
    )
    assert [hit.segment_id for hit in hits] == [
        "segment-cup-material",
        "segment-cup-era",
    ]
    assert all(hit.payload["source_id"] == "source-catalog" for hit in hits)


def test_evidence_index_rejects_duplicate_ids_and_dimension_mismatch():
    client = QdrantClient(location=":memory:")
    index = QdrantEvidenceIndex(
        client=client,
        url="http://unused.local",
        collection_name="museum-evidence-invalid",
        dimension=2,
    )
    records = [
        {"segment_id": "same"},
        {"segment_id": "same"},
    ]
    try:
        index.create_physical_collection(records, [[1.0, 0.0], [0.0, 1.0]])
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("duplicate segment IDs must be rejected")

    try:
        index.create_physical_collection(
            [{"segment_id": "wrong-dimension"}],
            [[1.0, 0.0, 0.0]],
        )
    except ValueError as exc:
        assert "dimension" in str(exc)
    else:
        raise AssertionError("wrong vector dimensions must be rejected")


def test_rebuild_retains_only_current_and_previous_for_safe_rollback():
    client = QdrantClient(location=":memory:")
    index = QdrantEvidenceIndex(
        client=client,
        url="http://unused.local",
        collection_name="museum-evidence-atomic-alias",
        dimension=3,
    )
    records = _records()
    vectors = [
        [1.0, 0.0, 0.0],
        [0.8, 0.2, 0.0],
        [0.0, 1.0, 0.0],
    ]
    targets = []
    for record_count in (3, 2, 1, 3):
        assert (
            index.rebuild(records[:record_count], vectors[:record_count])
            == record_count
        )
        targets.append(
            next(
                alias.collection_name
                for alias in client.get_aliases().aliases
                if alias.alias_name == index.collection_name
            )
        )

    retained_collections = {
        collection.name
        for collection in client.get_collections().collections
        if collection.name.startswith(f"{index.collection_name}__build_")
    }
    assert retained_collections == set(targets[-2:])
    assert not client.collection_exists(targets[0])
    assert not client.collection_exists(targets[1])
    assert index.count() == 3


def test_rebuild_cleanup_does_not_delete_an_unpublished_concurrent_build():
    client = QdrantClient(location=":memory:")
    index = QdrantEvidenceIndex(
        client=client,
        url="http://unused.local",
        collection_name="museum-evidence-concurrent-build",
        dimension=3,
    )
    unpublished_build = f"{index.collection_name}__build_in_progress"
    client.create_collection(
        collection_name=unpublished_build,
        vectors_config=models.VectorParams(
            size=3,
            distance=models.Distance.COSINE,
        ),
    )
    records = _records()
    vectors = [
        [1.0, 0.0, 0.0],
        [0.8, 0.2, 0.0],
        [0.0, 1.0, 0.0],
    ]

    for record_count in (3, 2, 1):
        assert (
            index.rebuild(records[:record_count], vectors[:record_count])
            == record_count
        )

    assert client.collection_exists(unpublished_build)
    lock_name = f"{index.collection_name}__rebuild_lock"
    assert not client.collection_exists(lock_name)
    current_target = next(
        alias.collection_name
        for alias in client.get_aliases().aliases
        if alias.alias_name == index.collection_name
    )
    lock_owner = index._acquire_rebuild_lock(lock_name)
    try:
        index.rebuild(records, vectors)
    except RuntimeError as exc:
        assert "already in progress" in str(exc)
    else:
        raise AssertionError("a concurrent evidence rebuild must be rejected")
    assert next(
        alias.collection_name
        for alias in client.get_aliases().aliases
        if alias.alias_name == index.collection_name
    ) == current_target
    index._release_rebuild_lock(lock_name, lock_owner)


def test_rebuild_retries_stale_collection_cleanup_after_transient_failure(
    monkeypatch,
):
    client = QdrantClient(location=":memory:")
    index = QdrantEvidenceIndex(
        client=client,
        url="http://unused.local",
        collection_name="museum-evidence-cleanup-retry",
        dimension=3,
    )
    records = _records()
    vectors = [
        [1.0, 0.0, 0.0],
        [0.8, 0.2, 0.0],
        [0.0, 1.0, 0.0],
    ]
    targets = []
    for record_count in (3, 2):
        assert (
            index.rebuild(records[:record_count], vectors[:record_count])
            == record_count
        )
        targets.append(
            next(
                alias.collection_name
                for alias in client.get_aliases().aliases
                if alias.alias_name == index.collection_name
            )
        )

    original_delete = client.delete_collection
    failed_once = False

    def flaky_delete(collection_name, *args, **kwargs):
        nonlocal failed_once
        if collection_name == targets[0] and not failed_once:
            failed_once = True
            raise RuntimeError("temporary Qdrant delete failure")
        return original_delete(collection_name, *args, **kwargs)

    monkeypatch.setattr(client, "delete_collection", flaky_delete)
    assert index.rebuild(records[:1], vectors[:1]) == 1
    assert client.collection_exists(targets[0])
    assert any(
        alias.alias_name.startswith(f"{index.collection_name}__history_")
        and alias.collection_name == targets[0]
        for alias in client.get_aliases().aliases
    )

    assert index.rebuild(records, vectors) == 3
    assert not client.collection_exists(targets[0])

    release_client = QdrantClient(location=":memory:")
    release_index = QdrantEvidenceIndex(
        client=release_client,
        url="http://unused.local",
        collection_name="museum-evidence-lock-recovery",
        dimension=3,
        rebuild_lock_timeout_seconds=1.0,
        rebuild_lock_empty_grace_seconds=0.0,
    )
    release_lock_name = f"{release_index.collection_name}__rebuild_lock"
    original_release_delete = release_client.delete_collection

    def fail_lock_release(collection_name, *args, **kwargs):
        if collection_name == release_lock_name:
            raise RuntimeError("persistent lock cleanup failure")
        return original_release_delete(collection_name, *args, **kwargs)

    monkeypatch.setattr(release_client, "delete_collection", fail_lock_release)
    assert release_index.rebuild(records[:1], vectors[:1]) == 1
    assert release_client.collection_exists(release_lock_name)
    assert release_index.count() == 1

    monkeypatch.setattr(
        release_client,
        "delete_collection",
        original_release_delete,
    )
    lock_points, _offset = release_client.scroll(
        collection_name=release_lock_name,
        limit=1,
        with_payload=True,
        with_vectors=False,
    )
    stale_payload = dict(lock_points[0].payload or {})
    stale_payload["heartbeat_at"] = 0.0
    release_client.upsert(
        collection_name=release_lock_name,
        points=[
            models.PointStruct(
                id=lock_points[0].id,
                vector=[0.0, 0.0, 0.0],
                payload=stale_payload,
            )
        ],
        wait=True,
    )
    assert release_index.rebuild(records[:2], vectors[:2]) == 2
    assert not release_client.collection_exists(release_lock_name)
    assert release_index.count() == 2
