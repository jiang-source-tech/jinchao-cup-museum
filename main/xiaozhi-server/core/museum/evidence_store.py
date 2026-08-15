from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
from typing import Any

from core.museum.contracts import SourceDocumentRecord, SourceSegmentRecord
from core.museum.store import MuseumStore


_GENERIC_QUERY_TOKENS = {
    "这件",
    "件展",
    "展品",
    "这个",
    "个展",
    "藏品",
    "文物",
    "它是",
    "请问",
    "一下",
    "关于",
    "什么",
    "是谁",
    "谁是",
    "怎么",
    "为何",
    "为什",
}


@dataclass(frozen=True)
class RankedSourceSegment:
    segment_id: str
    score: float
    source_id: str


class EvidenceStore:
    """Persistence seam for source documents and locatable evidence segments."""

    def __init__(self, store: MuseumStore):
        self._store = store

    def ensure_museum(self, museum_id: str, museum_name: str) -> None:
        with self._store.connection() as connection:
            self._ensure_museum(connection, museum_id, museum_name)

    def upsert_source_document(self, document: SourceDocumentRecord) -> str:
        with self._store.connection() as connection:
            return self._upsert_source_document(connection, document)

    def publish_source_batch(
        self,
        *,
        museum_id: str,
        museum_name: str,
        publications: Sequence[
            tuple[SourceDocumentRecord, Sequence[SourceSegmentRecord]]
        ],
    ) -> dict[str, tuple[str, ...]]:
        """Publish one manifest as a single SQLite transaction."""
        normalized = tuple(
            (document, tuple(segments))
            for document, segments in publications
        )
        source_ids = tuple(document.id for document, _segments in normalized)
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("source document IDs must be unique")
        if any(document.museum_id != museum_id for document, _ in normalized):
            raise ValueError("all source documents must belong to museum_id")
        exhibit_ids = tuple(
            dict.fromkeys(
                exhibit_id
                for _document, segments in normalized
                for segment in segments
                for exhibit_id in segment.exhibit_ids
            )
        )

        published: dict[str, tuple[str, ...]] = {}
        with self._store.connection() as connection:
            self._ensure_museum(connection, museum_id, museum_name)
            if not self._exhibit_ids_belong_to_museum(
                connection,
                exhibit_ids,
                museum_id,
            ):
                raise ValueError(
                    "source segment references an unknown or cross-museum exhibit"
                )
            for document, segments in normalized:
                source_version_id = self._upsert_source_document(
                    connection,
                    document,
                )
                published[document.id] = self._replace_source_segments(
                    connection,
                    document.id,
                    segments,
                    source_version_id=source_version_id,
                )
        return published

    def source_checksum(self, source_id: str) -> str | None:
        with self._store.connection() as connection:
            row = connection.execute(
                "SELECT checksum FROM source_document WHERE id = ?",
                (source_id,),
            ).fetchone()
        return str(row["checksum"]) if row is not None else None

    def withdraw_source(self, source_id: str, *, reason: str) -> None:
        withdrawal_reason = reason.strip()
        if not withdrawal_reason:
            raise ValueError("source withdrawal requires a reason")
        now = _now_iso()
        with self._store.connection() as connection:
            source = connection.execute(
                "SELECT active_version_id FROM source_document WHERE id = ?",
                (source_id,),
            ).fetchone()
            if source is None:
                raise ValueError(f"unknown source document: {source_id}")
            active_version_id = str(source["active_version_id"] or "")
            connection.execute(
                """
                UPDATE source_document
                SET status = 'withdrawn', updated_at = ?
                WHERE id = ?
                """,
                (now, source_id),
            )
            if active_version_id:
                connection.execute(
                    """
                    UPDATE source_document_version
                    SET status = 'withdrawn', withdrawn_at = ?,
                        withdrawal_reason = ?
                    WHERE id = ?
                    """,
                    (now, withdrawal_reason, active_version_id),
                )
            connection.execute(
                """
                UPDATE source_segment
                SET status = 'withdrawn'
                WHERE source_id = ? AND status = 'published'
                """,
                (source_id,),
            )
            connection.execute(
                "DELETE FROM source_segment_fts WHERE source_id = ?",
                (source_id,),
            )

    def exhibit_ids_exist(self, exhibit_ids: Iterable[str]) -> bool:
        ids = tuple(dict.fromkeys(value for value in exhibit_ids if value))
        with self._store.connection() as connection:
            return self._exhibit_ids_exist(connection, ids)

    def replace_source_segments(
        self,
        source_id: str,
        segments: Sequence[SourceSegmentRecord],
    ) -> tuple[str, ...]:
        """Publish a new source version while retaining withdrawn history."""
        with self._store.connection() as connection:
            return self._replace_source_segments(connection, source_id, segments)

    def _replace_source_segments(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        segments: Sequence[SourceSegmentRecord],
        *,
        source_version_id: str = "",
    ) -> tuple[str, ...]:
        normalized = tuple(segments)
        if not normalized:
            raise ValueError("a source version must contain at least one segment")
        if any(segment.source_id != source_id for segment in normalized):
            raise ValueError("all segments must belong to source_id")
        if len({segment.id for segment in normalized}) != len(normalized):
            raise ValueError("segment IDs must be unique")
        exhibit_ids = tuple(
            dict.fromkeys(
                exhibit_id
                for segment in normalized
                for exhibit_id in segment.exhibit_ids
            )
        )
        now = _now_iso()
        source = connection.execute(
            "SELECT id, museum_id, active_version_id, status "
            "FROM source_document WHERE id = ?",
            (source_id,),
        ).fetchone()
        if source is None:
            raise ValueError(f"unknown source document: {source_id}")
        if str(source["status"]) != "published":
            raise ValueError(
                f"cannot publish segments for a withdrawn source: {source_id}"
            )
        effective_version_id = source_version_id or str(
            source["active_version_id"] or ""
        )
        if not effective_version_id:
            raise ValueError(f"source document has no active version: {source_id}")
        if any(
            segment.source_version_id
            and segment.source_version_id != effective_version_id
            for segment in normalized
        ):
            raise ValueError("source segments reference a different source version")
        if not self._exhibit_ids_belong_to_museum(
            connection,
            exhibit_ids,
            str(source["museum_id"]),
        ):
            raise ValueError(
                "source segment references an unknown or cross-museum exhibit"
            )

        active_rows = self._segment_rows(
            connection,
            source_id=source_id,
            status="published",
        )
        incoming_signatures = tuple(
            _incoming_segment_signature(segment, effective_version_id)
            for segment in normalized
        )
        if self._stored_segment_signatures(connection, active_rows) == incoming_signatures:
            self._replace_fts_rows(connection, source_id, normalized)
            return tuple(segment.id for segment in normalized)

        incoming_ids = tuple(segment.id for segment in normalized)
        existing_rows = self._segment_rows(connection, segment_ids=incoming_ids)
        if existing_rows:
            existing_by_id = {str(row["id"]): row for row in existing_rows}
            if set(existing_by_id) != set(incoming_ids):
                raise ValueError("source version contains mixed existing and new segment IDs")
            ordered_existing = tuple(existing_by_id[segment.id] for segment in normalized)
            if (
                self._stored_segment_signatures(connection, ordered_existing)
                != incoming_signatures
            ):
                raise ValueError("existing segment ID does not match incoming metadata")

        connection.execute(
            "DELETE FROM source_segment_fts WHERE source_id = ?",
            (source_id,),
        )
        connection.execute(
            """
            UPDATE source_segment
            SET status = 'withdrawn'
            WHERE source_id = ? AND status = 'published'
            """,
            (source_id,),
        )

        if existing_rows:
            placeholders = ", ".join("?" for _ in incoming_ids)
            connection.execute(
                f"UPDATE source_segment SET status = 'published' "
                f"WHERE id IN ({placeholders})",
                incoming_ids,
            )
        else:
            version_row = connection.execute(
                "SELECT COALESCE(MAX(content_version), 0) AS version "
                "FROM source_segment WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            content_version = int(version_row["version"] or 0) + 1
            for segment in normalized:
                content_hash = segment.content_hash or _content_hash(segment.text)
                connection.execute(
                    """
                    INSERT INTO source_segment(
                        id, source_id, source_version_id, text, locator,
                        section, page, ordinal,
                        content_hash, parser_version, ocr_confidence, status,
                        content_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'published', ?, ?)
                    """,
                    (
                        segment.id,
                        source_id,
                        effective_version_id,
                        segment.text,
                        segment.locator,
                        segment.section,
                        segment.page,
                        segment.ordinal,
                        content_hash,
                        segment.parser_version,
                        segment.ocr_confidence,
                        content_version,
                        now,
                    ),
                )
                for exhibit_id in segment.exhibit_ids:
                    connection.execute(
                        """
                        INSERT INTO source_segment_exhibit(segment_id, exhibit_id)
                        VALUES (?, ?)
                        """,
                        (segment.id, exhibit_id),
                    )

        self._replace_fts_rows(connection, source_id, normalized)
        return incoming_ids

    @staticmethod
    def _ensure_museum(
        connection: sqlite3.Connection,
        museum_id: str,
        museum_name: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO museum(id, name, status)
            VALUES (?, ?, 'active')
            ON CONFLICT(id) DO UPDATE SET name = excluded.name
            """,
            (museum_id, museum_name),
        )

    @staticmethod
    def _upsert_source_document(
        connection: sqlite3.Connection,
        document: SourceDocumentRecord,
    ) -> str:
        existing = connection.execute(
            "SELECT museum_id, created_at FROM source_document WHERE id = ?",
            (document.id,),
        ).fetchone()
        if (
            existing is not None
            and str(existing["museum_id"]) != document.museum_id
        ):
            raise ValueError(
                f"source document ID already belongs to another museum: {document.id}"
            )
        metadata_json = json.dumps(
            dict(document.metadata),
            ensure_ascii=False,
            sort_keys=True,
        )
        version_id = document.version_id or _source_document_version_id(
            document,
            metadata_json,
        )
        version_row = connection.execute(
            "SELECT source_id, version_no FROM source_document_version WHERE id = ?",
            (version_id,),
        ).fetchone()
        if version_row is not None and str(version_row["source_id"]) != document.id:
            raise ValueError(f"source version ID belongs to another source: {version_id}")
        if version_row is None:
            latest = connection.execute(
                "SELECT COALESCE(MAX(version_no), 0) AS version_no "
                "FROM source_document_version WHERE source_id = ?",
                (document.id,),
            ).fetchone()
            version_no = int(latest["version_no"] or 0) + 1
        else:
            version_no = int(version_row["version_no"])
        now = _now_iso()
        created_at = (
            str(existing["created_at"] or now) if existing is not None else now
        )
        connection.execute(
            """
            INSERT INTO source_document(
                id, museum_id, title, source_type, locator, rights_note,
                publisher, published_date, accessed_at, language, checksum,
                source_level, rights_status, original_path,
                parser_version, metadata_json, status, content_version,
                active_version_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      'published', ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                source_type = excluded.source_type,
                locator = excluded.locator,
                rights_note = excluded.rights_note,
                publisher = excluded.publisher,
                published_date = excluded.published_date,
                accessed_at = excluded.accessed_at,
                language = excluded.language,
                checksum = excluded.checksum,
                source_level = excluded.source_level,
                rights_status = excluded.rights_status,
                original_path = excluded.original_path,
                parser_version = excluded.parser_version,
                metadata_json = excluded.metadata_json,
                status = 'published',
                content_version = excluded.content_version,
                active_version_id = excluded.active_version_id,
                updated_at = excluded.updated_at
            """,
            (
                document.id,
                document.museum_id,
                document.title,
                document.source_type,
                document.locator,
                document.rights_note,
                document.publisher,
                document.published_date,
                document.accessed_at,
                document.language,
                document.checksum,
                document.source_level,
                document.rights_status,
                document.original_path,
                document.parser_version,
                metadata_json,
                version_no,
                version_id,
                created_at,
                now,
            ),
        )
        connection.execute(
            """
            UPDATE source_document_version
            SET status = 'withdrawn',
                withdrawn_at = CASE
                    WHEN withdrawn_at = '' THEN ? ELSE withdrawn_at
                END,
                withdrawal_reason = CASE
                    WHEN withdrawal_reason = '' THEN 'superseded' ELSE withdrawal_reason
                END
            WHERE source_id = ? AND id != ? AND status = 'published'
            """,
            (now, document.id, version_id),
        )
        connection.execute(
            """
            INSERT INTO source_document_version(
                id, source_id, version_no, title, source_type, locator,
                rights_note, publisher, published_date, accessed_at, language,
                checksum, source_level, rights_status, original_path,
                parser_version, metadata_json, status, created_at,
                withdrawn_at, withdrawal_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      'published', ?, '', '')
            ON CONFLICT(id) DO UPDATE SET
                status = 'published',
                withdrawn_at = '',
                withdrawal_reason = ''
            """,
            (
                version_id,
                document.id,
                version_no,
                document.title,
                document.source_type,
                document.locator,
                document.rights_note,
                document.publisher,
                document.published_date,
                document.accessed_at,
                document.language,
                document.checksum,
                document.source_level,
                document.rights_status,
                document.original_path,
                document.parser_version,
                metadata_json,
                now,
            ),
        )
        return version_id

    @staticmethod
    def _exhibit_ids_exist(
        connection: sqlite3.Connection,
        exhibit_ids: Iterable[str],
    ) -> bool:
        ids = tuple(dict.fromkeys(value for value in exhibit_ids if value))
        if not ids:
            return True
        placeholders = ", ".join("?" for _ in ids)
        rows = connection.execute(
            f"SELECT id FROM exhibit WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        return len(rows) == len(ids)

    @staticmethod
    def _exhibit_ids_belong_to_museum(
        connection: sqlite3.Connection,
        exhibit_ids: Iterable[str],
        museum_id: str,
    ) -> bool:
        ids = tuple(dict.fromkeys(value for value in exhibit_ids if value))
        if not ids:
            return True
        placeholders = ", ".join("?" for _ in ids)
        rows = connection.execute(
            f"SELECT e.id FROM exhibit e "
            f"JOIN zone z ON z.id = e.zone_id "
            f"WHERE z.museum_id = ? AND e.id IN ({placeholders})",
            (museum_id, *ids),
        ).fetchall()
        return len(rows) == len(ids)

    @staticmethod
    def _segment_rows(
        connection: sqlite3.Connection,
        *,
        source_id: str = "",
        status: str = "",
        segment_ids: Sequence[str] = (),
    ) -> tuple[sqlite3.Row, ...]:
        conditions: list[str] = []
        parameters: list[Any] = []
        if source_id:
            conditions.append("source_id = ?")
            parameters.append(source_id)
        if status:
            conditions.append("status = ?")
            parameters.append(status)
        if segment_ids:
            placeholders = ", ".join("?" for _ in segment_ids)
            conditions.append(f"id IN ({placeholders})")
            parameters.extend(segment_ids)
        if not conditions:
            return ()
        rows = connection.execute(
            """
            SELECT id, source_id, text, locator, section, page, ordinal,
                   content_hash, parser_version, source_version_id,
                   ocr_confidence
            FROM source_segment
            WHERE """
            + " AND ".join(conditions)
            + " ORDER BY ordinal, id",
            parameters,
        ).fetchall()
        return tuple(rows)

    @staticmethod
    def _stored_segment_signatures(
        connection: sqlite3.Connection,
        rows: Sequence[sqlite3.Row],
    ) -> tuple[tuple[object, ...], ...]:
        if not rows:
            return ()
        ids = tuple(str(row["id"]) for row in rows)
        placeholders = ", ".join("?" for _ in ids)
        exhibit_rows = connection.execute(
            f"""
            SELECT segment_id, exhibit_id
            FROM source_segment_exhibit
            WHERE segment_id IN ({placeholders})
            ORDER BY segment_id, exhibit_id
            """,
            ids,
        ).fetchall()
        exhibits_by_segment: dict[str, list[str]] = {}
        for row in exhibit_rows:
            exhibits_by_segment.setdefault(str(row["segment_id"]), []).append(
                str(row["exhibit_id"])
            )
        return tuple(
            _stored_segment_signature(
                row,
                tuple(exhibits_by_segment.get(str(row["id"]), ())),
            )
            for row in rows
        )

    @staticmethod
    def _replace_fts_rows(
        connection: sqlite3.Connection,
        source_id: str,
        segments: Sequence[SourceSegmentRecord],
    ) -> None:
        source_title_row = connection.execute(
            "SELECT title FROM source_document WHERE id = ?",
            (source_id,),
        ).fetchone()
        source_title = str(source_title_row["title"])
        for segment in segments:
            connection.execute(
                "DELETE FROM source_segment_fts WHERE segment_id = ?",
                (segment.id,),
            )
            connection.execute(
                """
                INSERT INTO source_segment_fts(
                    segment_id, source_id, exhibit_ids, title, section, text
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    segment.id,
                    source_id,
                    " ".join(segment.exhibit_ids),
                    source_title,
                    segment.section,
                    segment.text,
                ),
            )

    def lexical_segment_candidates(
        self,
        *,
        question: str,
        exhibit_ids: tuple[str, ...] = (),
        source_ids: tuple[str, ...] = (),
        source_levels: tuple[str, ...] = (),
        limit: int = 12,
    ) -> tuple[RankedSourceSegment, ...]:
        if limit <= 0:
            return ()
        with self._store.connection() as connection:
            conditions = ["s.status = 'published'", "d.status = 'published'"]
            parameters: list[Any] = []
            if exhibit_ids:
                placeholders = ", ".join("?" for _ in exhibit_ids)
                conditions.append(
                    "EXISTS (SELECT 1 FROM source_segment_exhibit se "
                    f"WHERE se.segment_id = s.id AND se.exhibit_id IN ({placeholders}))"
                )
                parameters.extend(exhibit_ids)
            if source_ids:
                placeholders = ", ".join("?" for _ in source_ids)
                conditions.append(f"s.source_id IN ({placeholders})")
                parameters.extend(source_ids)
            if source_levels:
                placeholders = ", ".join("?" for _ in source_levels)
                conditions.append(f"d.source_level IN ({placeholders})")
                parameters.extend(source_levels)
            rows = connection.execute(
                """
                SELECT s.id, s.source_id, s.text, s.section, d.title
                FROM source_segment s
                JOIN source_document d ON d.id = s.source_id
                WHERE """
                + " AND ".join(conditions)
                + " ORDER BY s.ordinal, s.id",
                parameters,
            ).fetchall()
            fts_ids = _fts_candidate_ids(connection, question, exhibit_ids)

        normalized_question = _normalize(question)
        tokens = _query_tokens(question)
        scored: list[RankedSourceSegment] = []
        for row in rows:
            text = str(row["text"])
            normalized_text = _normalize(text)
            title = _normalize(str(row["title"]))
            section = _normalize(str(row["section"] or ""))
            score = 0.0
            if str(row["id"]) in fts_ids:
                score += 6.0
            if normalized_question and normalized_question in normalized_text:
                score += 12.0
            for token in tokens:
                if token in normalized_text:
                    score += 2.0
                if token in title or token in section:
                    score += 1.0
            if score > 0:
                scored.append(
                    RankedSourceSegment(
                        segment_id=str(row["id"]),
                        score=score,
                        source_id=str(row["source_id"]),
                    )
                )
        scored.sort(key=lambda item: (-item.score, item.segment_id))
        return tuple(scored[:limit])

    def hydrate_segments(
        self,
        segment_ids: Sequence[str],
        *,
        include_withdrawn: bool = False,
    ) -> tuple[SourceSegmentRecord, ...]:
        ordered_ids = tuple(dict.fromkeys(value for value in segment_ids if value))
        if not ordered_ids:
            return ()
        placeholders = ", ".join("?" for _ in ordered_ids)
        status_filter = "" if include_withdrawn else "AND s.status = 'published'"
        with self._store.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT s.id, s.source_id, s.text, s.locator, s.section, s.page,
                       s.ordinal, s.content_hash, s.parser_version,
                       s.source_version_id, s.ocr_confidence, s.status,
                       s.content_version
                FROM source_segment s
                WHERE s.id IN ({placeholders})
                  {status_filter}
                """,
                ordered_ids,
            ).fetchall()
            exhibit_rows = connection.execute(
                f"""
                SELECT segment_id, exhibit_id
                FROM source_segment_exhibit
                WHERE segment_id IN ({placeholders})
                ORDER BY segment_id, exhibit_id
                """,
                ordered_ids,
            ).fetchall()
        exhibits_by_segment: dict[str, list[str]] = {}
        for row in exhibit_rows:
            exhibits_by_segment.setdefault(str(row["segment_id"]), []).append(
                str(row["exhibit_id"])
            )
        rows_by_id = {str(row["id"]): row for row in rows}
        return tuple(
            SourceSegmentRecord(
                id=segment_id,
                source_id=str(rows_by_id[segment_id]["source_id"]),
                text=str(rows_by_id[segment_id]["text"]),
                locator=str(rows_by_id[segment_id]["locator"]),
                exhibit_ids=tuple(exhibits_by_segment.get(segment_id, ())),
                section=str(rows_by_id[segment_id]["section"] or ""),
                page=(
                    int(rows_by_id[segment_id]["page"])
                    if rows_by_id[segment_id]["page"] is not None
                    else None
                ),
                ordinal=int(rows_by_id[segment_id]["ordinal"]),
                content_hash=str(rows_by_id[segment_id]["content_hash"]),
                parser_version=str(rows_by_id[segment_id]["parser_version"]),
                source_version_id=str(
                    rows_by_id[segment_id]["source_version_id"] or ""
                ),
                ocr_confidence=(
                    float(rows_by_id[segment_id]["ocr_confidence"])
                    if rows_by_id[segment_id]["ocr_confidence"] is not None
                    else None
                ),
                status=str(rows_by_id[segment_id]["status"]),
                content_version=int(rows_by_id[segment_id]["content_version"]),
            )
            for segment_id in ordered_ids
            if segment_id in rows_by_id
        )

    def published_segment_index_records(self) -> tuple[dict[str, Any], ...]:
        with self._store.connection() as connection:
            rows = connection.execute(
                """
                SELECT s.id, s.source_id, s.text, s.locator, s.section, s.page,
                       s.content_hash, s.parser_version, s.ocr_confidence,
                       s.content_version, s.source_version_id,
                       d.title, d.source_level, d.checksum, d.original_path,
                       GROUP_CONCAT(se.exhibit_id) AS exhibit_ids
                FROM source_segment s
                JOIN source_document d ON d.id = s.source_id
                LEFT JOIN source_segment_exhibit se ON se.segment_id = s.id
                WHERE s.status = 'published'
                  AND d.status = 'published'
                  AND s.source_version_id = d.active_version_id
                GROUP BY s.id, s.source_id, s.text, s.locator, s.section, s.page,
                         s.content_hash, s.parser_version, s.ocr_confidence,
                         s.content_version, s.source_version_id,
                         d.title, d.source_level, d.checksum, d.original_path
                ORDER BY s.source_id, s.ordinal, s.id
                """
            ).fetchall()
        return tuple(
            {
                "segment_id": str(row["id"]),
                "source_id": str(row["source_id"]),
                "text": str(row["text"]),
                "locator": str(row["locator"]),
                "section": str(row["section"] or ""),
                "page": (
                    int(row["page"]) if row["page"] is not None else None
                ),
                "text_hash": str(row["content_hash"]),
                "parser_version": str(row["parser_version"]),
                "ocr_confidence": (
                    float(row["ocr_confidence"])
                    if row["ocr_confidence"] is not None
                    else None
                ),
                "content_version": int(row["content_version"]),
                "source_version_id": str(row["source_version_id"]),
                "source_title": str(row["title"]),
                "source_level": str(row["source_level"]),
                "source_checksum": str(row["checksum"]),
                "original_path": str(row["original_path"]),
                "exhibit_ids": tuple(
                    sorted(
                        value
                        for value in str(row["exhibit_ids"] or "").split(",")
                        if value
                    )
                ),
            }
            for row in rows
        )

    def segment_index_records(
        self,
        segment_ids: Sequence[str],
    ) -> tuple[dict[str, Any], ...]:
        wanted = tuple(dict.fromkeys(value for value in segment_ids if value))
        if not wanted:
            return ()
        records = self.published_segment_index_records()
        by_id = {str(record["segment_id"]): record for record in records}
        return tuple(by_id[segment_id] for segment_id in wanted if segment_id in by_id)

    def claim_support_segment_ids(
        self,
        fact_ids: Sequence[str],
    ) -> dict[str, tuple[str, ...]]:
        wanted = tuple(dict.fromkeys(value for value in fact_ids if value))
        if not wanted:
            return {}
        placeholders = ", ".join("?" for _ in wanted)
        with self._store.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT kcs.fact_id, kcs.segment_id
                FROM knowledge_claim_support kcs
                JOIN source_segment ss ON ss.id = kcs.segment_id
                WHERE kcs.fact_id IN ({placeholders})
                  AND ss.status = 'published'
                ORDER BY kcs.fact_id, kcs.segment_id
                """,
                wanted,
            ).fetchall()
        result: dict[str, list[str]] = {}
        for row in rows:
            result.setdefault(str(row["fact_id"]), []).append(
                str(row["segment_id"])
            )
        return {fact_id: tuple(values) for fact_id, values in result.items()}

    def conflict_groups(
        self,
        exhibit_ids: Sequence[str],
    ) -> tuple[tuple[str, ...], ...]:
        wanted = tuple(dict.fromkeys(value for value in exhibit_ids if value))
        if not wanted:
            return ()
        placeholders = ", ".join("?" for _ in wanted)
        with self._store.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT kcc.fact_id, kcc.segment_id
                FROM knowledge_claim_conflict kcc
                JOIN exhibit_fact f ON f.id = kcc.fact_id
                JOIN content_revision cr ON cr.id = f.revision_id
                JOIN source_segment ss ON ss.id = kcc.segment_id
                WHERE cr.status = 'published'
                  AND ss.status = 'published'
                  AND cr.exhibit_id IN ({placeholders})
                UNION
                SELECT kcc.fact_id, kcs.segment_id
                FROM knowledge_claim_conflict kcc
                JOIN exhibit_fact f ON f.id = kcc.fact_id
                JOIN content_revision cr ON cr.id = f.revision_id
                JOIN knowledge_claim_support kcs ON kcs.fact_id = kcc.fact_id
                JOIN source_segment ss ON ss.id = kcs.segment_id
                WHERE cr.status = 'published'
                  AND ss.status = 'published'
                  AND cr.exhibit_id IN ({placeholders})
                ORDER BY fact_id, segment_id
                """,
                wanted + wanted,
            ).fetchall()
        grouped: dict[str, list[str]] = {}
        for row in rows:
            grouped.setdefault(str(row["fact_id"]), []).append(
                str(row["segment_id"])
            )
        return tuple(
            tuple(values)
            for values in grouped.values()
            if values
        )

    def start_ingestion_run(
        self,
        *,
        run_id: str,
        dataset_id: str,
        manifest_path: str,
    ) -> None:
        with self._store.connection() as connection:
            connection.execute(
                """
                INSERT INTO ingestion_run(
                    id, dataset_id, status, manifest_path, started_at
                ) VALUES (?, ?, 'running', ?, ?)
                """,
                (run_id, dataset_id, manifest_path, _now_iso()),
            )

    def finish_ingestion_run(
        self,
        *,
        run_id: str,
        status: str,
        source_count: int,
        segment_count: int,
        errors: Sequence[str] = (),
    ) -> None:
        if status not in {"succeeded", "failed"}:
            raise ValueError(f"unsupported ingestion status: {status}")
        with self._store.connection() as connection:
            connection.execute(
                """
                UPDATE ingestion_run
                SET status = ?, source_count = ?, segment_count = ?,
                    error_json = ?, finished_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    source_count,
                    segment_count,
                    json.dumps(list(errors), ensure_ascii=False),
                    _now_iso(),
                    run_id,
                ),
            )


def _incoming_segment_signature(
    segment: SourceSegmentRecord,
    source_version_id: str,
) -> tuple[object, ...]:
    return (
        segment.id,
        segment.source_id,
        segment.source_version_id or source_version_id,
        segment.text,
        segment.locator,
        segment.section,
        segment.page,
        segment.ordinal,
        segment.content_hash or _content_hash(segment.text),
        segment.parser_version,
        segment.ocr_confidence,
        tuple(sorted(segment.exhibit_ids)),
    )


def _stored_segment_signature(
    row: sqlite3.Row,
    exhibit_ids: tuple[str, ...],
) -> tuple[object, ...]:
    return (
        str(row["id"]),
        str(row["source_id"]),
        str(row["source_version_id"] or ""),
        str(row["text"]),
        str(row["locator"]),
        str(row["section"] or ""),
        int(row["page"]) if row["page"] is not None else None,
        int(row["ordinal"]),
        str(row["content_hash"]),
        str(row["parser_version"]),
        (
            float(row["ocr_confidence"])
            if row["ocr_confidence"] is not None
            else None
        ),
        tuple(sorted(exhibit_ids)),
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_document_version_id(
    document: SourceDocumentRecord,
    metadata_json: str,
) -> str:
    payload = json.dumps(
        {
            "source_id": document.id,
            "title": document.title,
            "source_type": document.source_type,
            "locator": document.locator,
            "rights_note": document.rights_note,
            "publisher": document.publisher,
            "published_date": document.published_date,
            "accessed_at": document.accessed_at,
            "language": document.language,
            "checksum": document.checksum,
            "source_level": document.source_level,
            "rights_status": document.rights_status,
            "original_path": document.original_path,
            "parser_version": document.parser_version,
            "metadata_json": metadata_json,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    suffix = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"{document.id}-v-{suffix}"


def _normalize(value: str) -> str:
    return re.sub(r"[\s，。！？、；：,.!?;:]", "", value).casefold()


def _query_tokens(question: str) -> tuple[str, ...]:
    tokens: list[str] = []
    tokens.extend(re.findall(r"[A-Za-z0-9]+", question.casefold()))
    for block in re.findall(r"[\u3400-\u9fff]+", question):
        tokens.extend(block[index : index + 2] for index in range(len(block) - 1))
    return tuple(
        dict.fromkeys(
            token
            for token in tokens
            if token and token not in _GENERIC_QUERY_TOKENS
        )
    )


def _fts_candidate_ids(
    connection: sqlite3.Connection,
    question: str,
    exhibit_ids: tuple[str, ...],
) -> set[str]:
    terms = tuple(re.findall(r"[A-Za-z0-9]+", question.casefold()))
    if not terms:
        return set()
    query = " OR ".join(
        f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms
    )
    try:
        rows = connection.execute(
            """
            SELECT segment_id FROM source_segment_fts
            WHERE source_segment_fts MATCH ?
            """,
            (query,),
        ).fetchall()
    except sqlite3.OperationalError:
        return set()
    ids = {str(row["segment_id"]) for row in rows}
    if not exhibit_ids:
        return ids
    allowed_rows = connection.execute(
        """
        SELECT DISTINCT segment_id
        FROM source_segment_exhibit
        WHERE exhibit_id IN (%s)
        """ % ", ".join("?" for _ in exhibit_ids),
        exhibit_ids,
    ).fetchall()
    return ids & {str(row["segment_id"]) for row in allowed_rows}
