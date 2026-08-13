from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping


def summarize_interaction_traces(
    database_path: str | Path,
    *,
    since: datetime | None = None,
) -> dict[str, Any]:
    path = Path(database_path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    uri = f"file:{path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("interaction_trace",),
        ).fetchone():
            raise ValueError("数据库缺少 interaction_trace 表")
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(interaction_trace)")
        }
        if since is not None and "created_at" not in columns:
            raise ValueError("interaction_trace 缺少 created_at，无法按时间窗口汇总")
        selected = [
            _select_column(columns, "id", "0"),
            _select_column(columns, "created_at", "''"),
            _select_column(columns, "grounding_status", "'unknown'"),
            _select_column(columns, "guard_result", "''"),
            _select_column(columns, "duration_ms", "NULL"),
            _select_column(columns, "stage_latency_json", "'{}'"),
            _select_column(columns, "retrieval_trace_json", "'{}'"),
        ]
        query = f"SELECT {', '.join(selected)} FROM interaction_trace"
        parameters: tuple[str, ...] = ()
        if since is not None:
            query += " WHERE julianday(created_at) >= julianday(?)"
            parameters = (since.astimezone().isoformat(),)
        order = [name for name in ("created_at", "id") if name in columns]
        if order:
            query += f" ORDER BY {', '.join(order)}"
        rows = connection.execute(query, parameters).fetchall()

    statuses = Counter(str(row["grounding_status"]) for row in rows)
    guards = Counter(str(row["guard_result"]) for row in rows)
    failures = sum(
        statuses.get(status, 0)
        for status in {"temporary_failure", "retrieval_failure", "system_error"}
    )
    dense_fallbacks = 0
    stage_samples: dict[str, list[float]] = {"total_ms": []}
    for row in rows:
        if row["duration_ms"] is not None:
            stage_samples["total_ms"].append(float(row["duration_ms"]))
        stages = _json_object(row["stage_latency_json"])
        for name, value in stages.items():
            if isinstance(value, (int, float)):
                stage_samples.setdefault(str(name), []).append(float(value))
        retrieval = _json_object(row["retrieval_trace_json"])
        if retrieval.get("fallback_reason"):
            dense_fallbacks += 1
        retrieval_stages = retrieval.get("stage_latency_ms", {})
        if isinstance(retrieval_stages, Mapping):
            for name, value in retrieval_stages.items():
                if isinstance(value, (int, float)):
                    stage_samples.setdefault(
                        f"retrieval_{name}_ms", []
                    ).append(float(value))

    count = len(rows)
    guard_rejections = sum(
        count_value
        for guard, count_value in guards.items()
        if guard.startswith("model_") and guard != "model_answer_accepted"
    )
    return {
        "database": str(path),
        "since": since.astimezone().isoformat() if since else None,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "schema": {
            "available_columns": sorted(columns),
            "missing_observability_columns": sorted(
                {
                    "guard_result",
                    "duration_ms",
                    "stage_latency_json",
                    "retrieval_trace_json",
                }
                - columns
            ),
        },
        "request_count": count,
        "status_counts": dict(sorted(statuses.items())),
        "guard_counts": dict(sorted(guards.items())),
        "failure_count": failures,
        "failure_rate": failures / count if count else 0.0,
        "dense_fallback_count": dense_fallbacks,
        "dense_fallback_rate": dense_fallbacks / count if count else 0.0,
        "guard_rejection_count": guard_rejections,
        "guard_rejection_rate": guard_rejections / count if count else 0.0,
        "latency": {
            name: {
                "count": len(values),
                "p50": _percentile(values, 50),
                "p95": _percentile(values, 95),
            }
            for name, values in sorted(stage_samples.items())
            if values
        },
    }


def _select_column(columns: set[str], name: str, fallback_sql: str) -> str:
    return name if name in columns else f"{fallback_sql} AS {name}"


def _json_object(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _percentile(values: list[float], percentile: int) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(
        ordered[lower] + (ordered[upper] - ordered[lower]) * fraction,
        2,
    )
