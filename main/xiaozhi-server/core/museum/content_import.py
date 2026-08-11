from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

import yaml

from core.museum.store import MuseumStore


_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_FACT_TYPES = {
    "appearance",
    "craft",
    "dimensions",
    "era",
    "excavation",
    "history",
    "material",
    "observation",
    "price",
    "research_limit",
    "usage",
}


class ContentPackageValidationError(ValueError):
    def __init__(self, issues: Sequence[str]):
        unique_issues = tuple(dict.fromkeys(str(issue) for issue in issues if issue))
        self.issues = unique_issues
        message = "内容包校验失败"
        if unique_issues:
            message += ":\n- " + "\n- ".join(unique_issues)
        super().__init__(message)


@dataclass(frozen=True)
class MuseumDefinition:
    id: str
    name: str
    status: str


@dataclass(frozen=True)
class ZoneDefinition:
    id: str
    name: str
    sort_order: int


@dataclass(frozen=True)
class SourceDefinition:
    id: str
    title: str
    source_type: str
    locator: str
    rights_note: str


@dataclass(frozen=True)
class FactDefinition:
    id: str
    fact_type: str
    statement: str
    keywords: tuple[str, ...]
    confidence: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class RevisionDefinition:
    id: str
    number: int
    status: str
    facts: tuple[FactDefinition, ...]


@dataclass(frozen=True)
class ExhibitDefinition:
    id: str
    zone_id: str
    name: str
    aliases: tuple[str, ...]
    status: str
    image_uri: str | None
    revision: RevisionDefinition


@dataclass(frozen=True)
class MuseumContentPackage:
    schema_version: int
    museum: MuseumDefinition
    zones: tuple[ZoneDefinition, ...]
    sources: tuple[SourceDefinition, ...]
    exhibits: tuple[ExhibitDefinition, ...]


@dataclass(frozen=True)
class ContentImportResult:
    museum_id: str
    exhibit_ids: tuple[str, ...]
    revision_ids: tuple[str, ...]
    fact_ids: tuple[str, ...]
    source_ids: tuple[str, ...]

    @property
    def exhibit_count(self) -> int:
        return len(self.exhibit_ids)

    @property
    def revision_count(self) -> int:
        return len(self.revision_ids)

    @property
    def fact_count(self) -> int:
        return len(self.fact_ids)

    @property
    def source_count(self) -> int:
        return len(self.source_ids)


@dataclass(frozen=True)
class RevisionLifecycleResult:
    revision_id: str
    exhibit_id: str
    revision_number: int
    status: str
    previous_published_revision_id: str | None = None


@dataclass(frozen=True)
class RevisionVersionSummary:
    revision_id: str
    revision_number: int
    status: str
    reviewed_by: str | None
    reviewed_at: str | None
    published_at: str | None
    fact_count: int
    source_count: int
    fact_ids: tuple[str, ...]
    added_fact_ids: tuple[str, ...]
    removed_fact_ids: tuple[str, ...]


@dataclass(frozen=True)
class RevisionLifecycleEvent:
    event_id: int
    revision_id: str
    action: str
    from_status: str
    to_status: str
    actor: str
    reason: str
    occurred_at: str


@dataclass(frozen=True)
class ExhibitVersionHistory:
    exhibit_id: str
    exhibit_name: str
    current_published_revision_id: str | None
    revisions: tuple[RevisionVersionSummary, ...]
    events: tuple[RevisionLifecycleEvent, ...]


@dataclass(frozen=True)
class HistoricalFactEvidence:
    fact_id: str
    fact_type: str
    statement: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class HistoricalSourceEvidence:
    source_id: str
    title: str
    source_type: str
    locator: str
    rights_note: str


@dataclass(frozen=True)
class InteractionEvidenceAudit:
    trace_id: str
    request_id: str
    exhibit_id: str | None
    grounding_status: str
    content_revision_id: str | None
    content_version: int | None
    facts: tuple[HistoricalFactEvidence, ...]
    sources: tuple[HistoricalSourceEvidence, ...]
    answer_text: str
    created_at: str


def load_content_package(path: str | Path) -> MuseumContentPackage:
    source_path = Path(path)
    try:
        text = source_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ContentPackageValidationError(
            [f"{source_path}: 文件必须使用 UTF-8 编码"]
        ) from exc
    except OSError as exc:
        raise ContentPackageValidationError(
            [f"{source_path}: 无法读取内容包：{exc}"]
        ) from exc

    try:
        if source_path.suffix.lower() == ".json":
            payload = json.loads(text)
        elif source_path.suffix.lower() in {".yaml", ".yml"}:
            payload = yaml.safe_load(text)
        else:
            raise ContentPackageValidationError(
                [f"{source_path}: 只支持 .yaml、.yml 或 .json"]
            )
    except ContentPackageValidationError:
        raise
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ContentPackageValidationError(
            [f"{source_path}: 解析失败：{exc}"]
        ) from exc
    return parse_content_package(payload)


def parse_content_package(payload: Any) -> MuseumContentPackage:
    issues: list[str] = []
    root = _mapping(payload, "root", issues)
    _check_keys(
        root,
        required={"schema_version", "museum", "zones", "sources", "exhibits"},
        optional=set(),
        path="root",
        issues=issues,
    )
    schema_version = _integer(root.get("schema_version"), "schema_version", issues)
    if schema_version != 1:
        issues.append("schema_version 仅支持 1")

    museum_data = _mapping(root.get("museum"), "museum", issues)
    _check_keys(
        museum_data,
        required={"id", "name", "status"},
        optional=set(),
        path="museum",
        issues=issues,
    )
    museum = MuseumDefinition(
        id=_identifier(museum_data.get("id"), "museum.id", issues),
        name=_text(museum_data.get("name"), "museum.name", issues),
        status=_enum(
            museum_data.get("status"),
            "museum.status",
            {"active", "archived"},
            issues,
        ),
    )

    zones = tuple(
        _parse_zone(item, index, issues)
        for index, item in enumerate(_list(root.get("zones"), "zones", issues))
    )
    sources = tuple(
        _parse_source(item, index, issues)
        for index, item in enumerate(_list(root.get("sources"), "sources", issues))
    )
    exhibits = tuple(
        _parse_exhibit(item, index, issues)
        for index, item in enumerate(_list(root.get("exhibits"), "exhibits", issues))
    )
    if not zones:
        issues.append("zones 至少需要一项")
    if not sources:
        issues.append("sources 至少需要一项")
    if not exhibits:
        issues.append("exhibits 至少需要一项")

    _validate_relationships(zones, sources, exhibits, issues)
    if issues:
        raise ContentPackageValidationError(issues)
    return MuseumContentPackage(
        schema_version=schema_version,
        museum=museum,
        zones=zones,
        sources=sources,
        exhibits=exhibits,
    )


def validate_content_package_for_store(
    store: MuseumStore,
    package: MuseumContentPackage,
) -> None:
    with store.connection() as connection:
        issues = _database_issues(connection, package)
    if issues:
        raise ContentPackageValidationError(issues)


def import_draft_content(
    store: MuseumStore,
    package: MuseumContentPackage,
) -> ContentImportResult:
    with store.connection() as connection:
        issues = _database_issues(connection, package)
        if issues:
            raise ContentPackageValidationError(issues)
        _insert_package(connection, package)
    return ContentImportResult(
        museum_id=package.museum.id,
        exhibit_ids=tuple(exhibit.id for exhibit in package.exhibits),
        revision_ids=tuple(exhibit.revision.id for exhibit in package.exhibits),
        fact_ids=tuple(
            fact.id
            for exhibit in package.exhibits
            for fact in exhibit.revision.facts
        ),
        source_ids=tuple(source.id for source in package.sources),
    )


def review_revision(
    store: MuseumStore,
    *,
    revision_id: str,
    reviewed_by: str,
    reviewed_at: datetime,
) -> RevisionLifecycleResult:
    reviewer = _required_argument(reviewed_by, "reviewed_by")
    occurred_at = _iso_timestamp(reviewed_at)
    with store.connection() as connection:
        revision = _revision_row(connection, revision_id)
        if revision["status"] != "draft":
            raise ContentPackageValidationError(
                [f"内容版本 {revision_id} 当前状态不是 draft"]
            )
        connection.execute(
            """
            UPDATE content_revision
            SET status = 'reviewed', reviewed_by = ?, reviewed_at = ?
            WHERE id = ?
            """,
            (reviewer, occurred_at, revision_id),
        )
        _record_revision_event(
            connection,
            revision_id=revision_id,
            exhibit_id=str(revision["exhibit_id"]),
            action="review",
            from_status="draft",
            to_status="reviewed",
            actor=reviewer,
            reason="",
            occurred_at=occurred_at,
        )
    return RevisionLifecycleResult(
        revision_id=revision_id,
        exhibit_id=str(revision["exhibit_id"]),
        revision_number=int(revision["revision_no"]),
        status="reviewed",
    )


def publish_revision(
    store: MuseumStore,
    *,
    revision_id: str,
    published_by: str,
    published_at: datetime,
) -> RevisionLifecycleResult:
    publisher = _required_argument(published_by, "published_by")
    occurred_at = _iso_timestamp(published_at)
    with store.connection() as connection:
        revision = _revision_row(connection, revision_id)
        issues = _publication_issues(
            connection,
            revision,
            expected_status="reviewed",
        )
        if issues:
            raise ContentPackageValidationError(issues)
        previous_published_revision_id = _activate_revision(
            connection,
            revision=revision,
            action="publish",
            from_status="reviewed",
            actor=publisher,
            reason="",
            occurred_at=occurred_at,
            require_newer=True,
        )
    return RevisionLifecycleResult(
        revision_id=revision_id,
        exhibit_id=str(revision["exhibit_id"]),
        revision_number=int(revision["revision_no"]),
        status="published",
        previous_published_revision_id=previous_published_revision_id,
    )


def withdraw_revision(
    store: MuseumStore,
    *,
    revision_id: str,
    withdrawn_by: str,
    withdrawn_at: datetime,
    reason: str,
) -> RevisionLifecycleResult:
    actor = _required_argument(withdrawn_by, "withdrawn_by")
    normalized_reason = _required_argument(reason, "reason")
    occurred_at = _iso_timestamp(withdrawn_at)
    with store.connection() as connection:
        revision = _revision_row(connection, revision_id)
        if revision["status"] != "published":
            raise ContentPackageValidationError(
                [f"内容版本 {revision_id} 当前状态不是 published"]
            )
        connection.execute(
            "UPDATE content_revision SET status = 'withdrawn' WHERE id = ?",
            (revision_id,),
        )
        _record_revision_event(
            connection,
            revision_id=revision_id,
            exhibit_id=str(revision["exhibit_id"]),
            action="withdraw",
            from_status="published",
            to_status="withdrawn",
            actor=actor,
            reason=normalized_reason,
            occurred_at=occurred_at,
        )
    return RevisionLifecycleResult(
        revision_id=revision_id,
        exhibit_id=str(revision["exhibit_id"]),
        revision_number=int(revision["revision_no"]),
        status="withdrawn",
    )


def rollback_revision(
    store: MuseumStore,
    *,
    revision_id: str,
    rolled_back_by: str,
    rolled_back_at: datetime,
    reason: str,
) -> RevisionLifecycleResult:
    actor = _required_argument(rolled_back_by, "rolled_back_by")
    normalized_reason = _required_argument(reason, "reason")
    occurred_at = _iso_timestamp(rolled_back_at)
    with store.connection() as connection:
        revision = _revision_row(connection, revision_id)
        issues = _publication_issues(
            connection,
            revision,
            expected_status="withdrawn",
        )
        if issues:
            raise ContentPackageValidationError(issues)
        previous_published_revision_id = _activate_revision(
            connection,
            revision=revision,
            action="rollback",
            from_status="withdrawn",
            actor=actor,
            reason=normalized_reason,
            occurred_at=occurred_at,
            require_newer=False,
        )
    return RevisionLifecycleResult(
        revision_id=revision_id,
        exhibit_id=str(revision["exhibit_id"]),
        revision_number=int(revision["revision_no"]),
        status="published",
        previous_published_revision_id=previous_published_revision_id,
    )


def show_exhibit_versions(
    store: MuseumStore,
    *,
    exhibit_id: str,
) -> ExhibitVersionHistory:
    with store.connection() as connection:
        exhibit = connection.execute(
            "SELECT id, name FROM exhibit WHERE id = ?",
            (exhibit_id,),
        ).fetchone()
        if exhibit is None:
            raise ContentPackageValidationError([f"展品 {exhibit_id} 不存在"])
        revision_rows = connection.execute(
            """
            SELECT cr.id, cr.revision_no, cr.status,
                   cr.reviewed_by, cr.reviewed_at, cr.published_at,
                   COUNT(DISTINCT f.id) AS fact_count,
                   COUNT(DISTINCT fs.source_id) AS source_count,
                   GROUP_CONCAT(DISTINCT f.id) AS fact_ids
            FROM content_revision cr
            LEFT JOIN exhibit_fact f ON f.revision_id = cr.id
            LEFT JOIN fact_source fs ON fs.fact_id = f.id
            WHERE cr.exhibit_id = ?
            GROUP BY cr.id, cr.revision_no, cr.status,
                     cr.reviewed_by, cr.reviewed_at, cr.published_at
            ORDER BY cr.revision_no, cr.id
            """,
            (exhibit_id,),
        ).fetchall()
        event_rows = connection.execute(
            """
            SELECT id, revision_id, action, from_status, to_status,
                   actor, reason, occurred_at
            FROM content_revision_event
            WHERE exhibit_id = ?
            ORDER BY id
            """,
            (exhibit_id,),
        ).fetchall()

    revision_summaries: list[RevisionVersionSummary] = []
    previous_fact_ids: set[str] = set()
    for row in revision_rows:
        fact_ids = tuple(
            sorted(
                str(row["fact_ids"]).split(",")
                if row["fact_ids"] is not None
                else ()
            )
        )
        current_fact_ids = set(fact_ids)
        revision_summaries.append(
            RevisionVersionSummary(
                revision_id=str(row["id"]),
                revision_number=int(row["revision_no"]),
                status=str(row["status"]),
                reviewed_by=(
                    str(row["reviewed_by"])
                    if row["reviewed_by"] is not None
                    else None
                ),
                reviewed_at=(
                    str(row["reviewed_at"])
                    if row["reviewed_at"] is not None
                    else None
                ),
                published_at=(
                    str(row["published_at"])
                    if row["published_at"] is not None
                    else None
                ),
                fact_count=int(row["fact_count"]),
                source_count=int(row["source_count"]),
                fact_ids=fact_ids,
                added_fact_ids=tuple(sorted(current_fact_ids - previous_fact_ids)),
                removed_fact_ids=tuple(sorted(previous_fact_ids - current_fact_ids)),
            )
        )
        previous_fact_ids = current_fact_ids
    revisions = tuple(revision_summaries)
    return ExhibitVersionHistory(
        exhibit_id=exhibit_id,
        exhibit_name=str(exhibit["name"]),
        current_published_revision_id=next(
            (
                revision.revision_id
                for revision in revisions
                if revision.status == "published"
            ),
            None,
        ),
        revisions=revisions,
        events=tuple(
            RevisionLifecycleEvent(
                event_id=int(row["id"]),
                revision_id=str(row["revision_id"]),
                action=str(row["action"]),
                from_status=str(row["from_status"]),
                to_status=str(row["to_status"]),
                actor=str(row["actor"]),
                reason=str(row["reason"]),
                occurred_at=str(row["occurred_at"]),
            )
            for row in event_rows
        ),
    )


def audit_interaction_evidence(
    store: MuseumStore,
    *,
    request_id: str,
) -> InteractionEvidenceAudit:
    with store.connection() as connection:
        trace = connection.execute(
            """
            SELECT *
            FROM interaction_trace
            WHERE request_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (request_id,),
        ).fetchone()
        if trace is None:
            raise ContentPackageValidationError(
                [f"请求 {request_id} 不存在交互审计记录"]
            )
        try:
            evidence = json.loads(str(trace["evidence_json"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ContentPackageValidationError(
                [f"请求 {request_id} 的依据快照无法解析"]
            ) from exc
        if not isinstance(evidence, Mapping):
            raise ContentPackageValidationError(
                [f"请求 {request_id} 的依据快照不是对象"]
            )

        revision_id = evidence.get("content_revision_id")
        content_version = evidence.get("content_version")
        fact_ids = _audit_id_tuple(
            evidence.get("fact_ids", []),
            field="fact_ids",
            request_id=request_id,
        )
        source_ids = _audit_id_tuple(
            evidence.get("source_ids", []),
            field="source_ids",
            request_id=request_id,
        )
        fact_rows: list[sqlite3.Row] = []
        source_rows: list[sqlite3.Row] = []
        if revision_id is not None:
            if not isinstance(content_version, int) or isinstance(
                content_version, bool
            ):
                raise ContentPackageValidationError(
                    [f"请求 {request_id} 的历史内容版本号无效"]
                )
            revision = connection.execute(
                """
                SELECT id, revision_no
                FROM content_revision
                WHERE id = ?
                """,
                (revision_id,),
            ).fetchone()
            if revision is None:
                raise ContentPackageValidationError(
                    [f"历史内容版本 {revision_id} 已不存在，无法复核"]
                )
            if int(revision["revision_no"]) != int(content_version):
                raise ContentPackageValidationError(
                    [f"请求 {request_id} 的历史内容版本号不一致"]
                )
            if fact_ids:
                placeholders = ", ".join("?" for _ in fact_ids)
                fact_rows = connection.execute(
                    f"""
                    SELECT f.id, f.fact_type, f.statement,
                           GROUP_CONCAT(fs.source_id) AS source_ids
                    FROM exhibit_fact f
                    JOIN fact_source fs ON fs.fact_id = f.id
                    WHERE f.revision_id = ? AND f.id IN ({placeholders})
                    GROUP BY f.id, f.fact_type, f.statement
                    """,
                    (revision_id, *fact_ids),
                ).fetchall()
            if source_ids:
                placeholders = ", ".join("?" for _ in source_ids)
                source_rows = connection.execute(
                    f"""
                    SELECT id, title, source_type, locator, rights_note
                    FROM source_document
                    WHERE id IN ({placeholders})
                    """,
                    source_ids,
                ).fetchall()
        elif content_version is not None or fact_ids or source_ids:
            raise ContentPackageValidationError(
                [f"请求 {request_id} 的依据快照缺少内容版本 ID"]
            )

    facts_by_id = {str(row["id"]): row for row in fact_rows}
    sources_by_id = {str(row["id"]): row for row in source_rows}
    missing_fact_ids = [fact_id for fact_id in fact_ids if fact_id not in facts_by_id]
    missing_source_ids = [
        source_id for source_id in source_ids if source_id not in sources_by_id
    ]
    issues = [f"历史事实 {fact_id} 已不存在，无法复核" for fact_id in missing_fact_ids]
    issues.extend(
        f"历史来源 {source_id} 已不存在，无法复核"
        for source_id in missing_source_ids
    )
    actual_source_ids = {
        source_id
        for row in fact_rows
        for source_id in str(row["source_ids"]).split(",")
    }
    if actual_source_ids != set(source_ids):
        issues.append(f"请求 {request_id} 的历史事实与来源关联不一致")
    if issues:
        raise ContentPackageValidationError(issues)

    return InteractionEvidenceAudit(
        trace_id=str(trace["id"]),
        request_id=str(trace["request_id"]),
        exhibit_id=(
            str(trace["exhibit_id"]) if trace["exhibit_id"] is not None else None
        ),
        grounding_status=str(trace["grounding_status"]),
        content_revision_id=(str(revision_id) if revision_id is not None else None),
        content_version=(
            int(content_version) if content_version is not None else None
        ),
        facts=tuple(
            HistoricalFactEvidence(
                fact_id=fact_id,
                fact_type=str(facts_by_id[fact_id]["fact_type"]),
                statement=str(facts_by_id[fact_id]["statement"]),
                source_ids=tuple(
                    sorted(str(facts_by_id[fact_id]["source_ids"]).split(","))
                ),
            )
            for fact_id in fact_ids
        ),
        sources=tuple(
            HistoricalSourceEvidence(
                source_id=source_id,
                title=str(sources_by_id[source_id]["title"]),
                source_type=str(sources_by_id[source_id]["source_type"]),
                locator=str(sources_by_id[source_id]["locator"]),
                rights_note=str(sources_by_id[source_id]["rights_note"]),
            )
            for source_id in source_ids
        ),
        answer_text=str(trace["answer_text"]),
        created_at=str(trace["created_at"]),
    )


def _parse_zone(value: Any, index: int, issues: list[str]) -> ZoneDefinition:
    path = f"zones[{index}]"
    data = _mapping(value, path, issues)
    _check_keys(
        data,
        required={"id", "name", "sort_order"},
        optional=set(),
        path=path,
        issues=issues,
    )
    return ZoneDefinition(
        id=_identifier(data.get("id"), f"{path}.id", issues),
        name=_text(data.get("name"), f"{path}.name", issues),
        sort_order=_integer(data.get("sort_order"), f"{path}.sort_order", issues),
    )


def _parse_source(value: Any, index: int, issues: list[str]) -> SourceDefinition:
    path = f"sources[{index}]"
    data = _mapping(value, path, issues)
    _check_keys(
        data,
        required={"id", "title", "source_type", "locator", "rights_note"},
        optional=set(),
        path=path,
        issues=issues,
    )
    return SourceDefinition(
        id=_identifier(data.get("id"), f"{path}.id", issues),
        title=_text(data.get("title"), f"{path}.title", issues),
        source_type=_text(
            data.get("source_type"), f"{path}.source_type", issues
        ),
        locator=_text(data.get("locator"), f"{path}.locator", issues),
        rights_note=_text(
            data.get("rights_note"), f"{path}.rights_note", issues
        ),
    )


def _parse_exhibit(value: Any, index: int, issues: list[str]) -> ExhibitDefinition:
    path = f"exhibits[{index}]"
    data = _mapping(value, path, issues)
    _check_keys(
        data,
        required={"id", "zone_id", "name", "aliases", "status", "revision"},
        optional={"image_uri"},
        path=path,
        issues=issues,
    )
    revision_data = _mapping(data.get("revision"), f"{path}.revision", issues)
    _check_keys(
        revision_data,
        required={"id", "number", "status", "facts"},
        optional=set(),
        path=f"{path}.revision",
        issues=issues,
    )
    revision_status = _enum(
        revision_data.get("status"),
        f"{path}.revision.status",
        {"draft", "reviewed", "published", "withdrawn"},
        issues,
    )
    if revision_status and revision_status != "draft":
        issues.append(f"{path}.revision.status 必须是 draft")
    facts = tuple(
        _parse_fact(item, path, fact_index, issues)
        for fact_index, item in enumerate(
            _list(revision_data.get("facts"), f"{path}.revision.facts", issues)
        )
    )
    if not facts:
        issues.append(f"{path}.revision.facts 至少需要一项")
    return ExhibitDefinition(
        id=_identifier(data.get("id"), f"{path}.id", issues),
        zone_id=_identifier(data.get("zone_id"), f"{path}.zone_id", issues),
        name=_text(data.get("name"), f"{path}.name", issues),
        aliases=_string_list(data.get("aliases"), f"{path}.aliases", issues),
        status=_enum(
            data.get("status"),
            f"{path}.status",
            {"active", "archived"},
            issues,
        ),
        image_uri=_optional_text(data.get("image_uri"), f"{path}.image_uri", issues),
        revision=RevisionDefinition(
            id=_identifier(
                revision_data.get("id"), f"{path}.revision.id", issues
            ),
            number=_positive_integer(
                revision_data.get("number"), f"{path}.revision.number", issues
            ),
            status=revision_status,
            facts=facts,
        ),
    )


def _parse_fact(
    value: Any,
    exhibit_path: str,
    index: int,
    issues: list[str],
) -> FactDefinition:
    path = f"{exhibit_path}.revision.facts[{index}]"
    data = _mapping(value, path, issues)
    _check_keys(
        data,
        required={"id", "type", "statement", "keywords", "confidence", "sources"},
        optional=set(),
        path=path,
        issues=issues,
    )
    fact_type = _text(data.get("type"), f"{path}.type", issues)
    if fact_type and fact_type not in _FACT_TYPES:
        issues.append(
            f"{path}.type 不支持 {fact_type}；允许值为 {', '.join(sorted(_FACT_TYPES))}"
        )
    keywords = _string_list(data.get("keywords"), f"{path}.keywords", issues)
    source_ids = _identifier_list(data.get("sources"), f"{path}.sources", issues)
    _report_duplicate_ids(f"{path}.sources", source_ids, issues)
    if not keywords:
        issues.append(f"{path}.keywords 至少需要一项")
    if not source_ids:
        issues.append(f"{path}.sources 至少需要一项")
    return FactDefinition(
        id=_identifier(data.get("id"), f"{path}.id", issues),
        fact_type=fact_type,
        statement=_text(data.get("statement"), f"{path}.statement", issues),
        keywords=keywords,
        confidence=_text(data.get("confidence"), f"{path}.confidence", issues),
        source_ids=source_ids,
    )


def _validate_relationships(
    zones: tuple[ZoneDefinition, ...],
    sources: tuple[SourceDefinition, ...],
    exhibits: tuple[ExhibitDefinition, ...],
    issues: list[str],
) -> None:
    _report_duplicate_ids("zone", (zone.id for zone in zones), issues)
    _report_duplicate_ids("source", (source.id for source in sources), issues)
    _report_duplicate_ids("exhibit", (exhibit.id for exhibit in exhibits), issues)
    _report_duplicate_ids(
        "revision", (exhibit.revision.id for exhibit in exhibits), issues
    )
    _report_duplicate_ids(
        "fact",
        (
            fact.id
            for exhibit in exhibits
            for fact in exhibit.revision.facts
        ),
        issues,
    )

    zone_ids = {zone.id for zone in zones}
    source_ids = {source.id for source in sources}
    mention_owners: dict[str, tuple[str, str]] = {}
    for exhibit in exhibits:
        if exhibit.zone_id not in zone_ids:
            issues.append(
                f"展品 {exhibit.id} 引用了内容包中不存在的展区 {exhibit.zone_id}"
            )
        seen_mentions: set[str] = set()
        for mention in (exhibit.name, *exhibit.aliases):
            normalized = _normalize_mention(mention)
            if normalized in seen_mentions:
                issues.append(f"展品 {exhibit.id} 内部存在重复名称或别名 {mention}")
                continue
            seen_mentions.add(normalized)
            existing = mention_owners.get(normalized)
            if existing is not None and existing[0] != exhibit.id:
                issues.append(
                    "别名或名称冲突："
                    f"{mention} 同时属于 {existing[0]} 和 {exhibit.id}"
                )
            else:
                mention_owners[normalized] = (exhibit.id, mention)
        for fact in exhibit.revision.facts:
            for source_id in fact.source_ids:
                if source_id not in source_ids:
                    issues.append(
                        f"事实 {fact.id} 引用了内容包中不存在的来源 {source_id}"
                    )


def _database_issues(
    connection: sqlite3.Connection,
    package: MuseumContentPackage,
) -> list[str]:
    issues: list[str] = []
    _check_existing_row(
        connection,
        table="museum",
        row_id=package.museum.id,
        expected={"name": package.museum.name, "status": package.museum.status},
        label="博物馆",
        issues=issues,
    )
    for zone in package.zones:
        _check_existing_row(
            connection,
            table="zone",
            row_id=zone.id,
            expected={
                "museum_id": package.museum.id,
                "name": zone.name,
                "sort_order": zone.sort_order,
            },
            label="展区",
            issues=issues,
        )
    for source in package.sources:
        _check_existing_row(
            connection,
            table="source_document",
            row_id=source.id,
            expected={
                "museum_id": package.museum.id,
                "title": source.title,
                "source_type": source.source_type,
                "locator": source.locator,
                "rights_note": source.rights_note,
            },
            label="来源",
            issues=issues,
        )
    for exhibit in package.exhibits:
        row = connection.execute(
            "SELECT * FROM exhibit WHERE id = ?", (exhibit.id,)
        ).fetchone()
        if row is not None:
            expected_aliases = {_normalize_mention(alias) for alias in exhibit.aliases}
            actual_aliases = {
                _normalize_mention(alias)
                for alias in json.loads(str(row["aliases_json"] or "[]"))
            }
            expected = {
                "zone_id": exhibit.zone_id,
                "name": exhibit.name,
                "status": exhibit.status,
                "image_uri": exhibit.image_uri,
            }
            for column, value in expected.items():
                if row[column] != value:
                    issues.append(
                        f"展品 {exhibit.id} 已存在且 {column} 不一致"
                    )
            if actual_aliases != expected_aliases:
                issues.append(f"展品 {exhibit.id} 已存在且 aliases 不一致")

        revision_conflict = connection.execute(
            """
            SELECT id, exhibit_id, revision_no
            FROM content_revision
            WHERE id = ? OR (exhibit_id = ? AND revision_no = ?)
            LIMIT 1
            """,
            (exhibit.revision.id, exhibit.id, exhibit.revision.number),
        ).fetchone()
        if revision_conflict is not None:
            issues.append(
                f"内容版本 {exhibit.revision.id} 或 {exhibit.id}#"
                f"{exhibit.revision.number} 已存在"
            )
        for fact in exhibit.revision.facts:
            if connection.execute(
                "SELECT 1 FROM exhibit_fact WHERE id = ?", (fact.id,)
            ).fetchone():
                issues.append(f"事实 {fact.id} 已存在")

    package_mentions = {
        _normalize_mention(mention): exhibit.id
        for exhibit in package.exhibits
        for mention in (exhibit.name, *exhibit.aliases)
    }
    rows = connection.execute(
        """
        SELECT e.id, e.name, e.aliases_json
        FROM exhibit e
        JOIN zone z ON z.id = e.zone_id
        JOIN museum m ON m.id = z.museum_id
        WHERE e.status = 'active'
          AND m.status = 'active'
          AND EXISTS (
              SELECT 1 FROM content_revision cr
              WHERE cr.exhibit_id = e.id
                AND cr.status IN ('published', 'withdrawn')
          )
        """
    ).fetchall()
    for row in rows:
        existing_id = str(row["id"])
        existing_mentions = (
            str(row["name"]),
            *tuple(json.loads(str(row["aliases_json"] or "[]"))),
        )
        for mention in existing_mentions:
            package_owner = package_mentions.get(_normalize_mention(mention))
            if package_owner is not None and package_owner != existing_id:
                issues.append(
                    f"别名或名称冲突：{mention} 已属于已发布展品 {existing_id}"
                )
    return issues


def _insert_package(
    connection: sqlite3.Connection,
    package: MuseumContentPackage,
) -> None:
    if not _row_exists(connection, "museum", package.museum.id):
        connection.execute(
            "INSERT INTO museum(id, name, status) VALUES (?, ?, ?)",
            (package.museum.id, package.museum.name, package.museum.status),
        )
    for zone in package.zones:
        if not _row_exists(connection, "zone", zone.id):
            connection.execute(
                """
                INSERT INTO zone(id, museum_id, name, sort_order)
                VALUES (?, ?, ?, ?)
                """,
                (zone.id, package.museum.id, zone.name, zone.sort_order),
            )
    for source in package.sources:
        if not _row_exists(connection, "source_document", source.id):
            connection.execute(
                """
                INSERT INTO source_document(
                    id, museum_id, title, source_type, locator, rights_note
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    source.id,
                    package.museum.id,
                    source.title,
                    source.source_type,
                    source.locator,
                    source.rights_note,
                ),
            )
    for exhibit in package.exhibits:
        aliases_json = json.dumps(list(exhibit.aliases), ensure_ascii=False)
        if not _row_exists(connection, "exhibit", exhibit.id):
            connection.execute(
                """
                INSERT INTO exhibit(
                    id, zone_id, name, aliases_json, image_uri, status
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    exhibit.id,
                    exhibit.zone_id,
                    exhibit.name,
                    aliases_json,
                    exhibit.image_uri,
                    exhibit.status,
                ),
            )
        connection.execute(
            """
            INSERT INTO content_revision(
                id, exhibit_id, revision_no, status,
                reviewed_by, reviewed_at, published_at
            ) VALUES (?, ?, ?, 'draft', NULL, NULL, NULL)
            """,
            (exhibit.revision.id, exhibit.id, exhibit.revision.number),
        )
        for fact in exhibit.revision.facts:
            connection.execute(
                """
                INSERT INTO exhibit_fact(
                    id, revision_id, fact_type, statement,
                    keywords_json, confidence
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    fact.id,
                    exhibit.revision.id,
                    fact.fact_type,
                    fact.statement,
                    json.dumps(list(fact.keywords), ensure_ascii=False),
                    fact.confidence,
                ),
            )
            for source_id in fact.source_ids:
                connection.execute(
                    "INSERT INTO fact_source(fact_id, source_id) VALUES (?, ?)",
                    (fact.id, source_id),
                )
            connection.execute(
                """
                INSERT INTO exhibit_fact_fts(
                    fact_id, exhibit_name, aliases, fact_type, statement, keywords
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    fact.id,
                    exhibit.name,
                    " ".join(exhibit.aliases),
                    fact.fact_type,
                    fact.statement,
                    " ".join(fact.keywords),
                ),
            )


def _check_existing_row(
    connection: sqlite3.Connection,
    *,
    table: str,
    row_id: str,
    expected: Mapping[str, Any],
    label: str,
    issues: list[str],
) -> None:
    row = connection.execute(
        f"SELECT * FROM {table} WHERE id = ?", (row_id,)
    ).fetchone()
    if row is None:
        return
    for column, value in expected.items():
        if row[column] != value:
            issues.append(f"{label} {row_id} 已存在且 {column} 不一致")


def _row_exists(connection: sqlite3.Connection, table: str, row_id: str) -> bool:
    return (
        connection.execute(
            f"SELECT 1 FROM {table} WHERE id = ?", (row_id,)
        ).fetchone()
        is not None
    )


def _revision_row(
    connection: sqlite3.Connection,
    revision_id: str,
) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT id, exhibit_id, revision_no, status,
               reviewed_by, reviewed_at, published_at
        FROM content_revision
        WHERE id = ?
        """,
        (revision_id,),
    ).fetchone()
    if row is None:
        raise ContentPackageValidationError([f"内容版本 {revision_id} 不存在"])
    return row


def _record_revision_event(
    connection: sqlite3.Connection,
    *,
    revision_id: str,
    exhibit_id: str,
    action: str,
    from_status: str,
    to_status: str,
    actor: str,
    reason: str,
    occurred_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO content_revision_event(
            revision_id, exhibit_id, action, from_status, to_status,
            actor, reason, occurred_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            revision_id,
            exhibit_id,
            action,
            from_status,
            to_status,
            actor,
            reason,
            occurred_at,
        ),
    )


def _publication_issues(
    connection: sqlite3.Connection,
    revision: sqlite3.Row,
    *,
    expected_status: str,
) -> list[str]:
    revision_id = str(revision["id"])
    issues: list[str] = []
    if revision["status"] != expected_status:
        issues.append(
            f"内容版本 {revision_id} 当前状态不是 {expected_status}"
        )
    if not str(revision["reviewed_by"] or "").strip():
        issues.append(f"内容版本 {revision_id} 缺少审核人")
    if not str(revision["reviewed_at"] or "").strip():
        issues.append(f"内容版本 {revision_id} 缺少审核时间")
    issues.extend(_fact_publication_issues(connection, revision_id))
    issues.extend(
        _alias_publication_issues(connection, str(revision["exhibit_id"]))
    )
    return issues


def _activate_revision(
    connection: sqlite3.Connection,
    *,
    revision: sqlite3.Row,
    action: str,
    from_status: str,
    actor: str,
    reason: str,
    occurred_at: str,
    require_newer: bool,
) -> str | None:
    revision_id = str(revision["id"])
    exhibit_id = str(revision["exhibit_id"])
    previous = connection.execute(
        """
        SELECT id, revision_no
        FROM content_revision
        WHERE exhibit_id = ? AND status = 'published'
        """,
        (exhibit_id,),
    ).fetchone()
    previous_revision_id: str | None = None
    if previous is not None:
        if require_newer and int(revision["revision_no"]) <= int(
            previous["revision_no"]
        ):
            raise ContentPackageValidationError(
                [
                    f"内容版本 {revision_id} 不是比当前发布版本更新的版本；"
                    "恢复旧版本请使用 rollback"
                ]
            )
        previous_revision_id = str(previous["id"])
        connection.execute(
            "UPDATE content_revision SET status = 'withdrawn' WHERE id = ?",
            (previous_revision_id,),
        )
        operation_label = "发布" if action == "publish" else "回滚"
        _record_revision_event(
            connection,
            revision_id=previous_revision_id,
            exhibit_id=exhibit_id,
            action="supersede",
            from_status="published",
            to_status="withdrawn",
            actor=actor,
            reason=f"由内容版本 {revision_id} {operation_label}自动替代",
            occurred_at=occurred_at,
        )
    connection.execute(
        """
        UPDATE content_revision
        SET status = 'published', published_at = ?
        WHERE id = ?
        """,
        (occurred_at, revision_id),
    )
    _record_revision_event(
        connection,
        revision_id=revision_id,
        exhibit_id=exhibit_id,
        action=action,
        from_status=from_status,
        to_status="published",
        actor=actor,
        reason=reason,
        occurred_at=occurred_at,
    )
    return previous_revision_id


def _fact_publication_issues(
    connection: sqlite3.Connection,
    revision_id: str,
) -> list[str]:
    rows = connection.execute(
        """
        SELECT f.id, f.statement, COUNT(fs.source_id) AS source_count
        FROM exhibit_fact f
        LEFT JOIN fact_source fs ON fs.fact_id = f.id
        WHERE f.revision_id = ?
        GROUP BY f.id, f.statement
        ORDER BY f.id
        """,
        (revision_id,),
    ).fetchall()
    if not rows:
        return [f"内容版本 {revision_id} 没有可发布事实"]
    issues: list[str] = []
    for row in rows:
        fact_id = str(row["id"])
        if not str(row["statement"] or "").strip():
            issues.append(f"事实 {fact_id} 的陈述为空")
        if int(row["source_count"]) < 1:
            issues.append(f"事实 {fact_id} 缺少来源")
    return issues


def _alias_publication_issues(
    connection: sqlite3.Connection,
    exhibit_id: str,
) -> list[str]:
    exhibit = connection.execute(
        """
        SELECT e.id, e.name, e.aliases_json, e.status AS exhibit_status,
               m.status AS museum_status
        FROM exhibit e
        JOIN zone z ON z.id = e.zone_id
        JOIN museum m ON m.id = z.museum_id
        WHERE e.id = ?
        """,
        (exhibit_id,),
    ).fetchone()
    if exhibit is None:
        return [f"展品 {exhibit_id} 不存在"]

    issues: list[str] = []
    if exhibit["exhibit_status"] != "active":
        issues.append(f"展品 {exhibit_id} 当前不是 active")
    if exhibit["museum_status"] != "active":
        issues.append(f"展品 {exhibit_id} 所属博物馆当前不是 active")

    mentions = (
        str(exhibit["name"]),
        *tuple(json.loads(str(exhibit["aliases_json"] or "[]"))),
    )
    normalized_mentions: dict[str, str] = {}
    for mention in mentions:
        normalized = _normalize_mention(mention)
        if normalized in normalized_mentions:
            issues.append(f"展品 {exhibit_id} 内部存在重复名称或别名 {mention}")
        else:
            normalized_mentions[normalized] = mention

    rows = connection.execute(
        """
        SELECT e.id, e.name, e.aliases_json
        FROM exhibit e
        JOIN zone z ON z.id = e.zone_id
        JOIN museum m ON m.id = z.museum_id
        WHERE e.id <> ?
          AND e.status = 'active'
          AND m.status = 'active'
          AND EXISTS (
              SELECT 1 FROM content_revision cr
              WHERE cr.exhibit_id = e.id
                AND cr.status IN ('published', 'withdrawn')
          )
        """,
        (exhibit_id,),
    ).fetchall()
    for row in rows:
        other_mentions = (
            str(row["name"]),
            *tuple(json.loads(str(row["aliases_json"] or "[]"))),
        )
        for mention in other_mentions:
            normalized = _normalize_mention(mention)
            target_mention = normalized_mentions.get(normalized)
            if target_mention is not None:
                issues.append(
                    "别名或名称冲突："
                    f"{target_mention} 已属于可见展品 {row['id']}"
                )
    return issues


def _required_argument(value: str, name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ContentPackageValidationError([f"{name} 不能为空"])
    return normalized


def _audit_id_tuple(value: Any, *, field: str, request_id: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ContentPackageValidationError(
            [f"请求 {request_id} 的 {field} 不是数组"]
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ContentPackageValidationError(
                [f"请求 {request_id} 的 {field} 包含无效 ID"]
            )
        normalized = item.strip()
        if normalized in result:
            raise ContentPackageValidationError(
                [f"请求 {request_id} 的 {field} 包含重复 ID {normalized}"]
            )
        result.append(normalized)
    return tuple(result)


def _iso_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _mapping(value: Any, path: str, issues: list[str]) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    issues.append(f"{path} 必须是对象")
    return {}


def _list(value: Any, path: str, issues: list[str]) -> list[Any]:
    if isinstance(value, list):
        return value
    issues.append(f"{path} 必须是数组")
    return []


def _text(value: Any, path: str, issues: list[str]) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    issues.append(f"{path} 必须是非空字符串")
    return ""


def _optional_text(value: Any, path: str, issues: list[str]) -> str | None:
    if value is None:
        return None
    return _text(value, path, issues)


def _identifier(value: Any, path: str, issues: list[str]) -> str:
    identifier = _text(value, path, issues)
    if identifier and not _ID_PATTERN.fullmatch(identifier):
        issues.append(f"{path} 只能包含小写字母、数字、点、下划线和连字符")
    return identifier


def _integer(value: Any, path: str, issues: list[str]) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    issues.append(f"{path} 必须是整数")
    return 0


def _positive_integer(value: Any, path: str, issues: list[str]) -> int:
    number = _integer(value, path, issues)
    if number < 1:
        issues.append(f"{path} 必须大于等于 1")
    return number


def _enum(
    value: Any,
    path: str,
    allowed: set[str],
    issues: list[str],
) -> str:
    text = _text(value, path, issues)
    if text and text not in allowed:
        issues.append(f"{path} 不支持 {text}；允许值为 {', '.join(sorted(allowed))}")
    return text


def _string_list(value: Any, path: str, issues: list[str]) -> tuple[str, ...]:
    items = _list(value, path, issues)
    result = tuple(_text(item, f"{path}[{index}]", issues) for index, item in enumerate(items))
    return tuple(item for item in result if item)


def _identifier_list(value: Any, path: str, issues: list[str]) -> tuple[str, ...]:
    items = _list(value, path, issues)
    result = tuple(
        _identifier(item, f"{path}[{index}]", issues)
        for index, item in enumerate(items)
    )
    return tuple(item for item in result if item)


def _check_keys(
    data: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str],
    path: str,
    issues: list[str],
) -> None:
    keys = set(data)
    for key in sorted(required - keys):
        issues.append(f"{path}.{key} 缺失")
    for key in sorted(keys - required - optional):
        issues.append(f"{path}.{key} 是未知字段")


def _report_duplicate_ids(
    label: str,
    values: Iterable[str],
    issues: list[str],
) -> None:
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        if value in seen:
            issues.append(f"{label} ID 重复：{value}")
        seen.add(value)


def _normalize_mention(value: str) -> str:
    return re.sub(r"[\s，。！？、；：,.!?;:]", "", value).lower()
