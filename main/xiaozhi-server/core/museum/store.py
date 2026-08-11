from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from core.museum.contracts import (
    EvidenceFact,
    EvidenceSnapshot,
    ExhibitContext,
    VisitorSession,
)


DEMO_MUSEUM_ID = "hangzhou-museum-demo"
DEMO_ZONE_ID = "hangzhou-history-demo-zone"
DEMO_EXHIBIT_ID = "warring-states-crystal-cup"
DEMO_REVISION_ID = "warring-states-crystal-cup-r1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS museum (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'archived'))
);

CREATE TABLE IF NOT EXISTS zone (
    id TEXT PRIMARY KEY,
    museum_id TEXT NOT NULL REFERENCES museum(id),
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS exhibit (
    id TEXT PRIMARY KEY,
    zone_id TEXT NOT NULL REFERENCES zone(id),
    name TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    image_uri TEXT,
    status TEXT NOT NULL CHECK (status IN ('active', 'archived'))
);

CREATE TABLE IF NOT EXISTS source_document (
    id TEXT PRIMARY KEY,
    museum_id TEXT NOT NULL REFERENCES museum(id),
    title TEXT NOT NULL,
    source_type TEXT NOT NULL,
    locator TEXT NOT NULL,
    rights_note TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS content_revision (
    id TEXT PRIMARY KEY,
    exhibit_id TEXT NOT NULL REFERENCES exhibit(id),
    revision_no INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('draft', 'reviewed', 'published', 'withdrawn')
    ),
    reviewed_by TEXT,
    reviewed_at TEXT,
    published_at TEXT,
    UNIQUE (exhibit_id, revision_no)
);

CREATE UNIQUE INDEX IF NOT EXISTS one_published_revision_per_exhibit
ON content_revision(exhibit_id) WHERE status = 'published';

CREATE TABLE IF NOT EXISTS content_revision_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    revision_id TEXT NOT NULL REFERENCES content_revision(id),
    exhibit_id TEXT NOT NULL REFERENCES exhibit(id),
    action TEXT NOT NULL CHECK (
        action IN ('review', 'publish', 'supersede', 'withdraw', 'rollback')
    ),
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS revision_event_by_exhibit
ON content_revision_event(exhibit_id, id);

CREATE TABLE IF NOT EXISTS exhibit_fact (
    id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES content_revision(id),
    fact_type TEXT NOT NULL,
    statement TEXT NOT NULL,
    keywords_json TEXT NOT NULL DEFAULT '[]',
    confidence TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_source (
    fact_id TEXT NOT NULL REFERENCES exhibit_fact(id),
    source_id TEXT NOT NULL REFERENCES source_document(id),
    PRIMARY KEY (fact_id, source_id)
);

CREATE TABLE IF NOT EXISTS device_placement (
    device_id TEXT PRIMARY KEY,
    museum_id TEXT NOT NULL REFERENCES museum(id),
    zone_id TEXT NOT NULL REFERENCES zone(id),
    default_exhibit_id TEXT NOT NULL REFERENCES exhibit(id),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS visitor_session (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    current_exhibit_id TEXT NOT NULL REFERENCES exhibit(id),
    visitor_mode TEXT NOT NULL CHECK (visitor_mode IN ('general', 'family', 'deep')),
    started_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE INDEX IF NOT EXISTS active_session_by_device
ON visitor_session(device_id, expires_at);

CREATE TABLE IF NOT EXISTS interaction_trace (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    visitor_session_id TEXT,
    device_id TEXT,
    exhibit_id TEXT,
    resolution_status TEXT NOT NULL DEFAULT 'missing',
    context_source TEXT NOT NULL DEFAULT 'missing',
    matched_exhibit_text TEXT,
    candidate_exhibit_ids_json TEXT NOT NULL DEFAULT '[]',
    user_text TEXT NOT NULL,
    grounding_status TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    unanswered_reason TEXT,
    coarse_intent TEXT NOT NULL DEFAULT '',
    fine_intent TEXT NOT NULL DEFAULT '',
    intent_confidence REAL NOT NULL DEFAULT 0,
    guard_result TEXT NOT NULL,
    llm_invoked INTEGER NOT NULL DEFAULT 0,
    llm_model TEXT NOT NULL DEFAULT '',
    llm_prompt_version TEXT NOT NULL DEFAULT '',
    llm_result TEXT NOT NULL DEFAULT 'not_called',
    llm_response_summary TEXT NOT NULL DEFAULT '{}',
    stage_latency_json TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS interaction_trace_by_request
ON interaction_trace(request_id);
"""

_DEMO_SOURCES = (
    (
        "source-hangzhou-portal-2020",
        "杭州出土的战国水晶杯，连现代工艺都难以复制",
        "杭州市门户网站公开文章",
        "https://z.hangzhou.com.cn/2020/rwwhql/content/content_7734423.htm",
        "仅用于演示事实核对；未取得图片或展陈素材授权。",
    ),
    (
        "source-people-daily-2026",
        "两千多年前的水晶杯",
        "人民日报公开报道",
        "https://paper.people.com.cn/rmrb/pad/content/202602/19/content_30141292.html",
        "仅用于演示事实核对；未取得图片或展陈素材授权。",
    ),
)

_DEMO_FACTS = (
    (
        "fact-crystal-cup-era",
        "era",
        "这件水晶杯经鉴定为战国中晚期遗物，已有两千多年历史。",
        ["年代", "时期", "战国", "多久", "历史"],
        ("source-people-daily-2026",),
    ),
    (
        "fact-crystal-cup-material",
        "material",
        "它由一整块天然水晶琢制而成。",
        ["材质", "材料", "水晶", "天然", "做成"],
        ("source-hangzhou-portal-2020", "source-people-daily-2026"),
    ),
    (
        "fact-crystal-cup-excavation",
        "excavation",
        "它于1990年在杭州半山镇石塘村的战国墓葬中出土。",
        ["出土", "发现", "哪里", "地点", "1990", "半山", "石塘"],
        ("source-hangzhou-portal-2020", "source-people-daily-2026"),
    ),
    (
        "fact-crystal-cup-dimensions",
        "dimensions",
        "它高15.4厘米，口径7.8厘米，底径5.4厘米。",
        ["尺寸", "多高", "多大", "口径", "底径", "厘米"],
        ("source-hangzhou-portal-2020", "source-people-daily-2026"),
    ),
    (
        "fact-crystal-cup-appearance",
        "appearance",
        "它器口微敞、杯壁斜直、圈足外撇，外形很像现代常见的玻璃杯。",
        ["外形", "样子", "玻璃杯", "现代", "长什么", "为什么像"],
        ("source-hangzhou-portal-2020", "source-people-daily-2026"),
    ),
    (
        "fact-crystal-cup-craft-limit",
        "research_limit",
        "水晶硬度高、脆性大，开料、掏膛和抛光难度很高；它的具体原料来源及部分制作细节目前仍有未解之处。",
        ["工艺", "制作", "怎么做", "掏膛", "抛光", "原料来源", "未解"],
        ("source-hangzhou-portal-2020", "source-people-daily-2026"),
    ),
)

_TYPE_TERMS = {
    "era": ("年代", "时期", "什么时候", "多久", "历史"),
    "material": ("材质", "材料", "什么做", "是水晶吗", "天然水晶"),
    "excavation": ("出土", "发现", "哪里", "地点", "哪儿"),
    "dimensions": ("尺寸", "多高", "多大", "口径", "底径"),
    "appearance": ("外形", "样子", "玻璃杯", "现代", "长什么", "为什么像"),
    "craft": ("工艺", "制作", "怎么做", "雕琢", "加工"),
    "research_limit": ("工艺", "制作", "怎么做", "掏膛", "抛光", "原料来源"),
    "price": ("多少钱", "价格", "售价", "卖了多少", "值多少钱", "市场价"),
}
_HIGH_PRIORITY_TYPES = {"price"}

_INTRO_TERMS = ("介绍", "讲讲", "看看", "了解")
_INTRO_TYPES = {"era": 30, "material": 29, "appearance": 28, "excavation": 20}
_RETRIEVAL_FAILURE_STATUSES = {
    "temporary_failure",
    "retrieval_failure",
    "system_error",
}
_RETRIEVAL_FAILURE_REASONS = {
    "retrieval_failure",
    "retrieval_timeout",
    "retrieval_error",
    "database_error",
    "store_error",
}


@dataclass(frozen=True)
class UnansweredIssue:
    request_id: str
    original_question: str
    resolution_status: str
    exhibit_id: str | None
    unanswered_reason: str
    recorded_unanswered_reason: str | None
    coarse_intent: str
    fine_intent: str
    occurrence_count: int
    last_occurred_at: str
    fact_candidate_ids: tuple[str, ...]
    guard_result: str


class MuseumStore:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(_SCHEMA)
            _ensure_column(
                connection,
                "interaction_trace",
                "guard_result",
                "TEXT NOT NULL DEFAULT 'not_evaluated'",
            )
            _ensure_column(
                connection,
                "interaction_trace",
                "stage_latency_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            _ensure_column(
                connection,
                "interaction_trace",
                "coarse_intent",
                "TEXT NOT NULL DEFAULT ''",
            )
            _ensure_column(
                connection,
                "interaction_trace",
                "fine_intent",
                "TEXT NOT NULL DEFAULT ''",
            )
            _ensure_column(
                connection,
                "interaction_trace",
                "intent_confidence",
                "REAL NOT NULL DEFAULT 0",
            )
            _ensure_column(
                connection,
                "interaction_trace",
                "resolution_status",
                "TEXT NOT NULL DEFAULT 'missing'",
            )
            _ensure_column(
                connection,
                "interaction_trace",
                "context_source",
                "TEXT NOT NULL DEFAULT 'missing'",
            )
            _ensure_column(
                connection,
                "interaction_trace",
                "matched_exhibit_text",
                "TEXT",
            )
            _ensure_column(
                connection,
                "interaction_trace",
                "candidate_exhibit_ids_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            _ensure_column(
                connection,
                "interaction_trace",
                "llm_invoked",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                connection,
                "interaction_trace",
                "llm_model",
                "TEXT NOT NULL DEFAULT ''",
            )
            _ensure_column(
                connection,
                "interaction_trace",
                "llm_prompt_version",
                "TEXT NOT NULL DEFAULT ''",
            )
            _ensure_column(
                connection,
                "interaction_trace",
                "llm_result",
                "TEXT NOT NULL DEFAULT 'not_called'",
            )
            _ensure_column(
                connection,
                "interaction_trace",
                "llm_response_summary",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            fts_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(exhibit_fact_fts)"
                ).fetchall()
            }
            expected_fts_columns = {
                "fact_id",
                "exhibit_id",
                "revision_id",
                "exhibit_name",
                "aliases",
                "fact_type",
                "statement",
                "keywords",
            }
            rebuild_fts = not fts_columns or not expected_fts_columns.issubset(
                fts_columns
            )
            if fts_columns and rebuild_fts:
                connection.execute("DROP TABLE exhibit_fact_fts")
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS exhibit_fact_fts USING fts5(
                    fact_id UNINDEXED,
                    exhibit_id UNINDEXED,
                    revision_id UNINDEXED,
                    exhibit_name,
                    aliases,
                    fact_type,
                    statement,
                    keywords,
                    tokenize = 'unicode61'
                )
                """
            )
            if rebuild_fts:
                _rebuild_exhibit_fact_fts(connection)

    def seed_demo_content(self) -> None:
        published_at = "2026-08-09T00:00:00+00:00"
        with self.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO museum(id, name, status) VALUES (?, ?, 'active')",
                (DEMO_MUSEUM_ID, "杭州博物馆（演示数据）"),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO zone(id, museum_id, name, sort_order)
                VALUES (?, ?, ?, 1)
                """,
                (DEMO_ZONE_ID, DEMO_MUSEUM_ID, "杭州历史展区（演示点位）"),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO exhibit(
                    id, zone_id, name, aliases_json, image_uri, status
                ) VALUES (?, ?, ?, ?, NULL, 'active')
                """,
                (
                    DEMO_EXHIBIT_ID,
                    DEMO_ZONE_ID,
                    "战国水晶杯",
                    json.dumps(["水晶杯", "战国时期水晶杯"], ensure_ascii=False),
                ),
            )
            for source_id, title, source_type, locator, rights_note in _DEMO_SOURCES:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO source_document(
                        id, museum_id, title, source_type, locator, rights_note
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        DEMO_MUSEUM_ID,
                        title,
                        source_type,
                        locator,
                        rights_note,
                    ),
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO content_revision(
                    id, exhibit_id, revision_no, status,
                    reviewed_by, reviewed_at, published_at
                ) VALUES (?, ?, 1, 'published', ?, ?, ?)
                """,
                (
                    DEMO_REVISION_ID,
                    DEMO_EXHIBIT_ID,
                    "competition-demo-review",
                    published_at,
                    published_at,
                ),
            )
            for fact_id, fact_type, statement, keywords, source_ids in _DEMO_FACTS:
                keywords_json = json.dumps(keywords, ensure_ascii=False)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO exhibit_fact(
                        id, revision_id, fact_type, statement,
                        keywords_json, confidence
                    ) VALUES (?, ?, ?, ?, ?, 'reviewed-demo')
                    """,
                    (
                        fact_id,
                        DEMO_REVISION_ID,
                        fact_type,
                        statement,
                        keywords_json,
                    ),
                )
                for source_id in source_ids:
                    connection.execute(
                        "INSERT OR IGNORE INTO fact_source(fact_id, source_id) VALUES (?, ?)",
                        (fact_id, source_id),
                    )
                connection.execute(
                    "DELETE FROM exhibit_fact_fts WHERE fact_id = ?",
                    (fact_id,),
                )
                connection.execute(
                    """
                    INSERT INTO exhibit_fact_fts(
                        fact_id, exhibit_id, revision_id, exhibit_name,
                        aliases, fact_type, statement, keywords
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fact_id,
                        DEMO_EXHIBIT_ID,
                        DEMO_REVISION_ID,
                        "战国水晶杯",
                        "水晶杯 战国时期水晶杯",
                        fact_type,
                        statement,
                        " ".join(keywords),
                    ),
                )

    def ensure_demo_placement(self, device_id: str, occurred_at: datetime) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO device_placement(
                    device_id, museum_id, zone_id, default_exhibit_id, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    device_id,
                    DEMO_MUSEUM_ID,
                    DEMO_ZONE_ID,
                    DEMO_EXHIBIT_ID,
                    _iso(occurred_at),
                ),
            )

    def active_exhibits(self) -> tuple[tuple[str, str, str], ...]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT e.id, e.name, e.aliases_json
                FROM exhibit e
                JOIN zone z ON z.id = e.zone_id
                JOIN museum m ON m.id = z.museum_id
                WHERE e.status = 'active' AND m.status = 'active'
                  AND EXISTS (
                      SELECT 1 FROM content_revision cr
                      WHERE cr.exhibit_id = e.id
                        AND cr.status IN ('published', 'withdrawn')
                  )
                ORDER BY e.id
                """
            ).fetchall()
        return tuple(
            (str(row["id"]), str(row["name"]), str(row["aliases_json"] or "[]"))
            for row in rows
        )

    def resolve_or_create_session(
        self,
        *,
        device_id: str,
        occurred_at: datetime,
        requested_session_id: str | None = None,
        explicit_exhibit_id: str | None = None,
        route_exhibit_id: str | None = None,
        allow_device_placement: bool = True,
    ) -> tuple[VisitorSession, ExhibitContext] | None:
        now = _as_utc(occurred_at)
        now_iso = _iso(now)
        with self.connection() as connection:
            context_source = "visitor_session"
            if requested_session_id:
                row = connection.execute(
                    """
                    SELECT * FROM visitor_session
                    WHERE id = ? AND device_id = ? AND ended_at IS NULL AND expires_at > ?
                    """,
                    (requested_session_id, device_id, now_iso),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT * FROM visitor_session
                    WHERE device_id = ? AND ended_at IS NULL AND expires_at > ?
                    ORDER BY started_at DESC LIMIT 1
                    """,
                    (device_id, now_iso),
                ).fetchone()

            authoritative_exhibit_id = explicit_exhibit_id or route_exhibit_id
            if authoritative_exhibit_id:
                exhibit_exists = connection.execute(
                    "SELECT 1 FROM exhibit WHERE id = ? AND status = 'active'",
                    (authoritative_exhibit_id,),
                ).fetchone()
                if exhibit_exists is None:
                    return None
                context_source = (
                    "explicit_selection" if explicit_exhibit_id else "route_stop"
                )
                if row is not None:
                    connection.execute(
                        "UPDATE visitor_session SET current_exhibit_id = ? WHERE id = ?",
                        (authoritative_exhibit_id, row["id"]),
                    )
                    row = connection.execute(
                        "SELECT * FROM visitor_session WHERE id = ?",
                        (row["id"],),
                    ).fetchone()

            if row is None and authoritative_exhibit_id is None and allow_device_placement:
                placement = connection.execute(
                    "SELECT * FROM device_placement WHERE device_id = ?",
                    (device_id,),
                ).fetchone()
                if placement is None:
                    return None
                authoritative_exhibit_id = placement["default_exhibit_id"]
                context_source = "device_placement"

            # Explicit mode must not create a session without a current exhibit.
            # The schema intentionally keeps current_exhibit_id non-null for now;
            # callers can ask for clarification before an exhibit is identified.
            if row is None and authoritative_exhibit_id is None and not allow_device_placement:
                return None

            if row is None:
                session_id = uuid.uuid4().hex
                expires_at = now + timedelta(minutes=30)
                connection.execute(
                    """
                    INSERT INTO visitor_session(
                        id, device_id, current_exhibit_id, visitor_mode,
                        started_at, expires_at, ended_at
                    ) VALUES (?, ?, ?, 'general', ?, ?, NULL)
                    """,
                    (
                        session_id,
                        device_id,
                        authoritative_exhibit_id,
                        now_iso,
                        _iso(expires_at),
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM visitor_session WHERE id = ?",
                    (session_id,),
                ).fetchone()

            context_row = connection.execute(
                """
                SELECT
                    m.id AS museum_id, m.name AS museum_name,
                    z.id AS zone_id, z.name AS zone_name,
                    e.id AS exhibit_id, e.name AS exhibit_name
                FROM exhibit e
                JOIN zone z ON z.id = e.zone_id
                JOIN museum m ON m.id = z.museum_id
                WHERE e.id = ? AND e.status = 'active' AND m.status = 'active'
                """,
                (row["current_exhibit_id"],),
            ).fetchone()
            if context_row is None:
                return None

        session = VisitorSession(
            id=row["id"],
            device_id=row["device_id"],
            current_exhibit_id=row["current_exhibit_id"],
            visitor_mode=row["visitor_mode"],
            started_at=_parse_datetime(row["started_at"]),
            expires_at=_parse_datetime(row["expires_at"]),
        )
        context = ExhibitContext(
            museum_id=context_row["museum_id"],
            museum_name=context_row["museum_name"],
            zone_id=context_row["zone_id"],
            zone_name=context_row["zone_name"],
            exhibit_id=context_row["exhibit_id"],
            exhibit_name=context_row["exhibit_name"],
            context_source=context_source,
        )
        return session, context

    def retrieve_evidence(
        self,
        *,
        exhibit_id: str,
        question: str,
        limit: int = 3,
        fact_types: tuple[str, ...] | None = None,
        query_terms: tuple[str, ...] = (),
        overview: bool = False,
    ) -> EvidenceSnapshot | None:
        with self.connection() as connection:
            revision = connection.execute(
                """
                SELECT id, revision_no
                FROM content_revision
                WHERE exhibit_id = ? AND status = 'published'
                """,
                (exhibit_id,),
            ).fetchone()
            if revision is None:
                return None
            rows = connection.execute(
                """
                SELECT f.id, f.fact_type, f.statement, f.keywords_json,
                       e.name AS exhibit_name, e.aliases_json,
                       GROUP_CONCAT(fs.source_id) AS source_ids
                FROM exhibit_fact f
                JOIN content_revision cr ON cr.id = f.revision_id
                JOIN exhibit e ON e.id = cr.exhibit_id
                JOIN fact_source fs ON fs.fact_id = f.id
                WHERE f.revision_id = ?
                GROUP BY f.id, f.fact_type, f.statement, f.keywords_json,
                         e.name, e.aliases_json
                """,
                (revision["id"],),
            ).fetchall()
            if not rows:
                return None
            fts_ids = self._fts_candidate_ids(
                connection,
                exhibit_id=exhibit_id,
                revision_id=str(revision["id"]),
                rows=rows,
                question=question,
            )

        normalized = _normalize_text(
            question + " " + " ".join(query_terms)
        )
        if fact_types is None:
            matched_types = {
                fact_type
                for fact_type, terms in _TYPE_TERMS.items()
                if any(term in normalized for term in terms)
            }
            high_priority_types = matched_types & _HIGH_PRIORITY_TYPES
            if high_priority_types:
                matched_types = high_priority_types
        else:
            matched_types = set(fact_types)
        aliases = {
            rows[0]["exhibit_name"],
            *json.loads(rows[0]["aliases_json"]),
        }
        mentions_exhibit = any(
            _normalize_text(alias) in normalized for alias in aliases
        )
        general_exhibit_question = mentions_exhibit and any(
            term in normalized
            for term in ("特点", "特别", "看点", "介绍", "讲讲", "是什么", "怎么样")
        )
        intro = overview or general_exhibit_question or any(
            term in normalized for term in _INTRO_TERMS
        ) or normalized in {
            "这是什么",
            "它是什么",
            "这个是什么",
        }
        scored: list[tuple[int, sqlite3.Row]] = []
        for row in rows:
            if matched_types and row["fact_type"] not in matched_types:
                continue
            if not matched_types and not intro:
                continue
            score = 6 if row["id"] in fts_ids else 0
            keywords = json.loads(row["keywords_json"])
            score += sum(8 for keyword in keywords if keyword in normalized)
            score += sum(
                20
                for term in _TYPE_TERMS.get(row["fact_type"], ())
                if term in normalized
            )
            if intro:
                score += _INTRO_TYPES.get(row["fact_type"], 0)
            if score > 0:
                scored.append((score, row))

        if not scored:
            return None
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        facts = tuple(
            EvidenceFact(
                id=row["id"],
                fact_type=row["fact_type"],
                statement=row["statement"],
                source_ids=tuple(sorted(row["source_ids"].split(","))),
            )
            for _, row in scored[:limit]
        )
        return EvidenceSnapshot(
            exhibit_id=exhibit_id,
            content_revision_id=revision["id"],
            content_version=revision["revision_no"],
            facts=facts,
        )

    def published_evidence(self, exhibit_id: str) -> EvidenceSnapshot | None:
        with self.connection() as connection:
            revision = connection.execute(
                """
                SELECT id, revision_no
                FROM content_revision
                WHERE exhibit_id = ? AND status = 'published'
                """,
                (exhibit_id,),
            ).fetchone()
            if revision is None:
                return None
            rows = connection.execute(
                """
                SELECT f.id, f.fact_type, f.statement,
                       GROUP_CONCAT(fs.source_id) AS source_ids
                FROM exhibit_fact f
                JOIN fact_source fs ON fs.fact_id = f.id
                WHERE f.revision_id = ?
                GROUP BY f.id, f.fact_type, f.statement
                ORDER BY f.id
                """,
                (revision["id"],),
            ).fetchall()
        if not rows:
            return None
        return EvidenceSnapshot(
            exhibit_id=exhibit_id,
            content_revision_id=revision["id"],
            content_version=revision["revision_no"],
            facts=tuple(
                EvidenceFact(
                    id=row["id"],
                    fact_type=row["fact_type"],
                    statement=row["statement"],
                    source_ids=tuple(sorted(row["source_ids"].split(","))),
                )
                for row in rows
            ),
        )

    def published_content_version(self, exhibit_id: str) -> int | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT revision_no FROM content_revision
                WHERE exhibit_id = ? AND status = 'published'
                """,
                (exhibit_id,),
            ).fetchone()
        return int(row["revision_no"]) if row is not None else None

    @staticmethod
    def _fts_candidate_ids(
        connection: sqlite3.Connection,
        *,
        exhibit_id: str,
        revision_id: str,
        rows: list[sqlite3.Row],
        question: str,
    ) -> set[str]:
        normalized = _normalize_text(question)
        terms: list[str] = []
        for row in rows:
            for keyword in json.loads(row["keywords_json"]):
                if keyword in normalized and keyword not in terms:
                    terms.append(keyword)
        terms.extend(
            token
            for token in re.findall(r"[a-zA-Z0-9]+", question)
            if token not in terms
        )
        if not terms:
            return set()
        query = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
        try:
            matches = connection.execute(
                """
                SELECT fact_id
                FROM exhibit_fact_fts
                WHERE exhibit_fact_fts MATCH ?
                  AND exhibit_id = ?
                  AND revision_id = ?
                """,
                (query, exhibit_id, revision_id),
            ).fetchall()
        except sqlite3.OperationalError:
            return set()
        return {row["fact_id"] for row in matches}

    def record_interaction(
        self,
        *,
        request_id: str,
        visitor_session_id: str | None,
        device_id: str | None,
        exhibit_id: str | None,
        user_text: str,
        grounding_status: str,
        evidence: EvidenceSnapshot | None,
        answer_text: str,
        unanswered_reason: str | None,
        coarse_intent: str = "",
        fine_intent: str = "",
        intent_confidence: float = 0.0,
        guard_result: str,
        llm_invoked: bool = False,
        llm_model: str = "",
        llm_prompt_version: str = "",
        llm_result: str = "not_called",
        llm_response_summary: str = "{}",
        stage_latency: dict[str, int],
        duration_ms: int,
        occurred_at: datetime,
        resolution_status: str = "missing",
        context_source: str = "missing",
        matched_exhibit_text: str | None = None,
        candidate_exhibit_ids: tuple[str, ...] = (),
    ) -> str:
        trace_id = uuid.uuid4().hex
        evidence_json = json.dumps(
            {
                "content_revision_id": (
                    evidence.content_revision_id if evidence else None
                ),
                "content_version": evidence.content_version if evidence else None,
                "fact_ids": list(evidence.fact_ids) if evidence else [],
                "source_ids": list(evidence.source_ids) if evidence else [],
            },
            ensure_ascii=False,
        )
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO interaction_trace(
                    id, request_id, visitor_session_id, device_id, exhibit_id,
                    resolution_status, context_source, matched_exhibit_text,
                    candidate_exhibit_ids_json,
                    user_text, grounding_status, evidence_json, answer_text,
                    unanswered_reason, coarse_intent, fine_intent,
                    intent_confidence, guard_result, llm_invoked, llm_model,
                    llm_prompt_version, llm_result, llm_response_summary,
                    stage_latency_json, duration_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    request_id,
                    visitor_session_id,
                    device_id,
                    exhibit_id,
                    resolution_status,
                    context_source,
                    matched_exhibit_text,
                    json.dumps(list(candidate_exhibit_ids), ensure_ascii=False),
                    user_text,
                    grounding_status,
                    evidence_json,
                    answer_text,
                    unanswered_reason,
                    coarse_intent,
                    fine_intent,
                    intent_confidence,
                    guard_result,
                    int(llm_invoked),
                    llm_model,
                    llm_prompt_version,
                    llm_result,
                    llm_response_summary,
                    json.dumps(stage_latency, ensure_ascii=False),
                    duration_ms,
                    _iso(occurred_at),
                ),
            )
        return trace_id

    def get_interaction_trace(self, trace_id: str) -> sqlite3.Row | None:
        with self.connection() as connection:
            return connection.execute(
                "SELECT * FROM interaction_trace WHERE id = ?",
                (trace_id,),
            ).fetchone()

    def get_interaction_trace_by_request_id(
        self, request_id: str
    ) -> sqlite3.Row | None:
        """Return the latest complete audit record for one transport request."""
        with self.connection() as connection:
            return connection.execute(
                """
                SELECT *
                FROM interaction_trace
                WHERE request_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (request_id,),
            ).fetchone()

    def get_interaction_audit_by_request_id(
        self, request_id: str
    ) -> dict[str, Any] | None:
        """Return one complete trace with JSON audit fields decoded."""
        row = self.get_interaction_trace_by_request_id(request_id)
        if row is None:
            return None
        audit = {key: row[key] for key in row.keys()}
        audit["record_type"] = "interaction_trace"
        audit["candidate_exhibit_ids"] = _json_list(
            audit.pop("candidate_exhibit_ids_json", "[]")
        )
        audit["evidence"] = _json_object(audit.pop("evidence_json", "{}"))
        audit["stage_latency"] = _json_object(
            audit.pop("stage_latency_json", "{}")
        )
        audit["llm_response_summary"] = _json_object(
            audit["llm_response_summary"]
        )
        audit["llm_invoked"] = bool(audit["llm_invoked"])
        return audit

    def list_unanswered_issues(self) -> tuple[UnansweredIssue, ...]:
        """Return actionable unanswered questions grouped for content operations."""
        exhibit_mentions = _exhibit_mentions(self.active_exhibits())
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM interaction_trace
                WHERE unanswered_reason IS NOT NULL
                   OR resolution_status IN ('not_found', 'ambiguous')
                   OR grounding_status IN (
                       'unsupported', 'temporary_failure',
                       'retrieval_failure', 'system_error'
                   )
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()

        seen_request_ids: set[str] = set()
        groups: dict[tuple[str | None, str, str], dict] = {}
        for row in rows:
            request_id = str(row["request_id"])
            if request_id in seen_request_ids:
                continue
            seen_request_ids.add(request_id)
            reason = _classify_unanswered_trace(row, exhibit_mentions)
            if reason is None:
                continue
            key = (
                row["exhibit_id"],
                reason,
                _normalize_issue_question(str(row["user_text"])),
            )
            group = groups.setdefault(
                key,
                {
                    "representative": row,
                    "reason": reason,
                    "count": 0,
                },
            )
            group["count"] += 1

        issues = []
        for group in groups.values():
            row = group["representative"]
            evidence = _json_object(row["evidence_json"])
            issues.append(
                UnansweredIssue(
                    request_id=str(row["request_id"]),
                    original_question=str(row["user_text"]),
                    resolution_status=str(row["resolution_status"]),
                    exhibit_id=(
                        str(row["exhibit_id"])
                        if row["exhibit_id"] is not None
                        else None
                    ),
                    unanswered_reason=str(group["reason"]),
                    recorded_unanswered_reason=(
                        str(row["unanswered_reason"])
                        if row["unanswered_reason"] is not None
                        else None
                    ),
                    coarse_intent=str(row["coarse_intent"]),
                    fine_intent=str(row["fine_intent"]),
                    occurrence_count=int(group["count"]),
                    last_occurred_at=str(row["created_at"]),
                    fact_candidate_ids=_string_tuple(evidence.get("fact_ids")),
                    guard_result=str(row["guard_result"]),
                )
            )
        return tuple(
            sorted(
                issues,
                key=lambda issue: (
                    -issue.occurrence_count,
                    -_timestamp_value(issue.last_occurred_at),
                    issue.exhibit_id or "",
                    issue.unanswered_reason,
                    issue.original_question,
                ),
                reverse=False,
            )
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="seconds")


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _normalize_text(value: str) -> str:
    return re.sub(r"[\s，。！？、；：,.!?;:]", "", value).lower()


def _normalize_issue_question(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


def _classify_unanswered_trace(
    row: sqlite3.Row,
    exhibit_mentions: tuple[str, ...],
) -> str | None:
    if (
        row["grounding_status"] == "conversational"
        or row["coarse_intent"] == "social"
    ):
        return None
    grounding_status = str(row["grounding_status"] or "")
    recorded_reason = str(row["unanswered_reason"] or "")
    resolution_status = str(row["resolution_status"] or "")
    if (
        grounding_status in _RETRIEVAL_FAILURE_STATUSES
        or recorded_reason in _RETRIEVAL_FAILURE_REASONS
    ):
        return "retrieval_failure"
    if resolution_status == "ambiguous":
        return "exhibit_ambiguous"
    if resolution_status == "not_found":
        if _is_asr_suspected(row["matched_exhibit_text"], exhibit_mentions):
            return "asr_suspected"
        return "exhibit_not_found"
    if (
        row["coarse_intent"] in {"comparison", "unsupported"}
        or recorded_reason == "out_of_scope"
    ):
        return "out_of_scope"
    if (
        row["exhibit_id"] is not None
        and grounding_status == "unsupported"
        and recorded_reason == "no_published_fact_match"
    ):
        return "fact_not_covered"
    return None


def _exhibit_mentions(
    exhibits: tuple[tuple[str, str, str], ...],
) -> tuple[str, ...]:
    mentions: list[str] = []
    for _exhibit_id, name, aliases_json in exhibits:
        values = [name]
        try:
            aliases = json.loads(aliases_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            aliases = []
        if isinstance(aliases, list):
            values.extend(str(alias) for alias in aliases)
        for value in values:
            normalized = _normalize_issue_question(value)
            if normalized and normalized not in mentions:
                mentions.append(normalized)
    return tuple(mentions)


def _is_asr_suspected(
    matched_exhibit_text: object,
    exhibit_mentions: tuple[str, ...],
) -> bool:
    reference = _normalize_issue_question(str(matched_exhibit_text or ""))
    if len(reference) < 4:
        return False
    for mention in exhibit_mentions:
        if reference == mention:
            continue
        max_length = max(len(reference), len(mention))
        allowed_distance = 1 if max_length < 10 else 2
        if abs(len(reference) - len(mention)) > allowed_distance:
            continue
        if _edit_distance(reference, mention) <= allowed_distance:
            return True
    return False


def _edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _json_object(value: object) -> dict:
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _json_list(value: object) -> list:
    try:
        decoded = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return decoded if isinstance(decoded, list) else []


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def _timestamp_value(value: str) -> float:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return float("-inf")
    return _as_utc(parsed).timestamp()


def _ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    declaration: str,
) -> None:
    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {declaration}"
        )


def _rebuild_exhibit_fact_fts(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT f.id AS fact_id, f.revision_id, cr.exhibit_id,
               e.name AS exhibit_name, e.aliases_json,
               f.fact_type, f.statement, f.keywords_json
        FROM exhibit_fact f
        JOIN content_revision cr ON cr.id = f.revision_id
        JOIN exhibit e ON e.id = cr.exhibit_id
        ORDER BY f.id
        """
    ).fetchall()
    connection.execute("DELETE FROM exhibit_fact_fts")
    connection.executemany(
        """
        INSERT INTO exhibit_fact_fts(
            fact_id, exhibit_id, revision_id, exhibit_name,
            aliases, fact_type, statement, keywords
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (
                str(row["fact_id"]),
                str(row["exhibit_id"]),
                str(row["revision_id"]),
                str(row["exhibit_name"]),
                " ".join(json.loads(str(row["aliases_json"] or "[]"))),
                str(row["fact_type"]),
                str(row["statement"]),
                " ".join(json.loads(str(row["keywords_json"] or "[]"))),
            )
            for row in rows
        ),
    )
