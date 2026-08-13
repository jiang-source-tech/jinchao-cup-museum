from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.museum.content_import import (  # noqa: E402
    ContentPackageValidationError,
    audit_content_batch,
    import_draft_content,
    load_content_package,
    publish_revision,
    review_revision,
)
from core.museum.knowledge_release import build_knowledge_release_manifest  # noqa: E402
from core.museum.store import MuseumStore  # noqa: E402


def build_isolated_release(
    *,
    content_directory: Path,
    database_path: Path,
    reviewer: str,
    publisher: str,
    occurred_at: datetime,
    minimum_exhibits: int = 100,
) -> dict[str, object]:
    paths = tuple(
        sorted(
            path
            for path in content_directory.iterdir()
            if path.suffix.lower() in {".yaml", ".yml", ".json"}
        )
    )
    audit = audit_content_batch(paths)
    if not audit.ok:
        raise ContentPackageValidationError(audit.issues)

    packages = tuple(load_content_package(path) for path in paths)
    if len(audit.exhibit_ids) < minimum_exhibits:
        raise RuntimeError(
            f"isolated release contains {len(audit.exhibit_ids)} exhibits; "
            f"minimum required is {minimum_exhibits}"
        )
    if database_path.exists():
        raise FileExistsError(
            f"refusing to overwrite existing isolated database: {database_path}"
        )

    database_path.parent.mkdir(parents=True, exist_ok=True)
    store = MuseumStore(database_path)
    imported_revisions: list[str] = []
    for package in packages:
        import_draft_content(store, package)
        imported_revisions.extend(
            exhibit.revision.id for exhibit in package.exhibits
        )

    lifecycle: list[dict[str, str]] = []
    for revision_id in imported_revisions:
        reviewed = review_revision(
            store,
            revision_id=revision_id,
            reviewed_by=reviewer,
            reviewed_at=occurred_at,
        )
        published = publish_revision(
            store,
            revision_id=revision_id,
            published_by=publisher,
            published_at=occurred_at,
        )
        lifecycle.append(
            {
                "revision_id": revision_id,
                "reviewed_by": reviewer,
                "published_by": publisher,
                "status": published.status,
            }
        )

    records = store.published_fact_index_records()
    manifest = build_knowledge_release_manifest(
        records,
        embedding_model="text-embedding-v4",
        embedding_dimension=1024,
        collection="museum_facts_stage3_isolated_v1",
    )
    with sqlite3.connect(database_path) as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])

    return {
        "database_path": str(database_path),
        "package_count": audit.package_count,
        "museum_count": len(audit.museum_ids),
        "exhibit_count": len(audit.exhibit_ids),
        "published_revision_count": len(imported_revisions),
        "published_fact_count": len(records),
        "sqlite_integrity": integrity,
        "release_id": manifest["release_id"],
        "collection": manifest["collection"],
        "lifecycle": lifecycle,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="在隔离 SQLite 中导入、审核并发布博物馆内容包"
    )
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--reviewer", default="stage3-reviewer")
    parser.add_argument("--publisher", default="stage3-publisher")
    parser.add_argument("--occurred-at", default="")
    parser.add_argument("--minimum-exhibits", type=int, default=100)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    occurred_at = (
        datetime.fromisoformat(args.occurred_at)
        if args.occurred_at
        else datetime.now(timezone.utc)
    )
    try:
        result = build_isolated_release(
            content_directory=args.directory,
            database_path=args.database,
            reviewer=args.reviewer,
            publisher=args.publisher,
            occurred_at=occurred_at,
            minimum_exhibits=args.minimum_exhibits,
        )
    except (ContentPackageValidationError, FileExistsError, RuntimeError) as exc:
        payload = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        print(json.dumps(payload, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
