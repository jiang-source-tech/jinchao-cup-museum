from __future__ import annotations

from pathlib import Path

from scripts.evaluate_museum_scale import evaluate_scale


def test_scale_evaluation_is_isolated_and_covers_hybrid_pipeline():
    result = evaluate_scale(fact_count=40, query_count=20, embedding_dimension=64)

    assert result["dataset"] == {
        "synthetic": True,
        "exhibit_count": 2,
        "fact_count": 40,
        "source_count": 1,
        "query_count": 20,
    }
    assert result["index"]["indexed_point_count"] == 40
    assert result["recall_at_3"] == {
        "lexical": 1.0,
        "dense": 1.0,
        "hybrid": 1.0,
        "selected": 1.0,
    }
    assert result["sample_failures"] == []
    assert result["isolation"]["external_embedding_calls"] == 0
    assert result["isolation"]["production_database_touched"] is False
    assert result["isolation"]["cleanup_performed"] is True
    assert not Path(result["isolation"]["temporary_workspace"]).exists()
