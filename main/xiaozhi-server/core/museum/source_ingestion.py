from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

import yaml

from core.museum.contracts import (
    IngestionReport,
    SourceDocumentRecord,
    SourceSegmentRecord,
)
from core.museum.evidence_store import EvidenceStore
from core.museum.store import MuseumStore


PARSER_VERSION = "source-ingestion-v1"
_SOURCE_LEVELS = {
    "primary_public_source",
    "secondary_public_source",
    "demo_curated",
    "synthetic_demo",
    "unverified",
}
_SOURCE_TYPES = {"pdf", "markdown", "md", "text", "txt", "json", "html", "htm", "image"}
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class SourceManifestError(ValueError):
    def __init__(self, issues: Sequence[str]):
        self.issues = tuple(dict.fromkeys(str(issue) for issue in issues if issue))
        message = "资料清单校验失败"
        if self.issues:
            message += ":\n- " + "\n- ".join(self.issues)
        super().__init__(message)


class SourceParseError(ValueError):
    pass


@dataclass(frozen=True)
class SourceManifestEntry:
    id: str
    title: str
    source_type: str
    path: str
    locator: str
    rights_note: str
    museum_id: str
    publisher: str = ""
    published_date: str = ""
    accessed_at: str = ""
    language: str = "zh-CN"
    source_level: str = "demo_curated"
    rights_status: str = "demo_authorized"
    exhibit_ids: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceManifest:
    schema_version: int
    dataset_id: str
    museum_id: str
    museum_name: str
    sources: tuple[SourceManifestEntry, ...]


@dataclass(frozen=True)
class ParsedSegment:
    text: str
    locator: str
    section: str = ""
    page: int | None = None
    ocr_confidence: float | None = None


def load_source_manifest(path: str | Path) -> SourceManifest:
    manifest_path = Path(path)
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SourceManifestError([f"{manifest_path}: 文件必须使用 UTF-8 编码"]) from exc
    except OSError as exc:
        raise SourceManifestError([f"{manifest_path}: 无法读取资料清单：{exc}"]) from exc

    try:
        if manifest_path.suffix.lower() == ".json":
            payload = json.loads(text)
        elif manifest_path.suffix.lower() in {".yaml", ".yml"}:
            payload = yaml.safe_load(text)
        else:
            raise SourceManifestError([f"{manifest_path}: 只支持 .yaml、.yml 或 .json"])
    except SourceManifestError:
        raise
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise SourceManifestError([f"{manifest_path}: 解析失败：{exc}"]) from exc

    return parse_source_manifest(payload, manifest_path=manifest_path)


def parse_source_manifest(
    payload: Any,
    *,
    manifest_path: str | Path = "<memory>",
) -> SourceManifest:
    issues: list[str] = []
    if not isinstance(payload, Mapping):
        raise SourceManifestError([f"{manifest_path}: 根节点必须是对象"])
    schema_version = payload.get("schema_version")
    if schema_version != 1:
        issues.append(f"{manifest_path}: schema_version 必须为 1")
    dataset_id = _required_id(payload.get("dataset_id"), "dataset_id", issues)
    museum = payload.get("museum")
    if not isinstance(museum, Mapping):
        issues.append(f"{manifest_path}: museum 必须是对象")
        museum = {}
    museum_id = _required_id(museum.get("id"), "museum.id", issues)
    museum_name = _required_text(museum.get("name"), "museum.name", issues)
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        issues.append(f"{manifest_path}: sources 至少需要一项")
        raw_sources = []

    entries: list[SourceManifestEntry] = []
    seen_ids: set[str] = set()
    for index, raw_source in enumerate(raw_sources):
        prefix = f"{manifest_path}: sources[{index}]"
        if not isinstance(raw_source, Mapping):
            issues.append(f"{prefix} 必须是对象")
            continue
        source_id = _required_id(raw_source.get("id"), f"{prefix}.id", issues)
        if source_id in seen_ids:
            issues.append(f"{prefix}.id 重复：{source_id}")
        seen_ids.add(source_id)
        title = _required_text(raw_source.get("title"), f"{prefix}.title", issues)
        source_type = str(raw_source.get("source_type", "")).strip().lower()
        if source_type not in _SOURCE_TYPES:
            issues.append(
                f"{prefix}.source_type 必须是 {sorted(_SOURCE_TYPES)}"
            )
        source_path = str(raw_source.get("path", "")).strip()
        if not source_path:
            issues.append(f"{prefix}.path 不能为空")
        locator = str(raw_source.get("locator", source_path)).strip()
        rights_note = str(raw_source.get("rights_note", "")).strip()
        if not rights_note:
            issues.append(f"{prefix}.rights_note 不能为空")
        source_level = str(
            raw_source.get("source_level", "demo_curated")
        ).strip()
        if source_level not in _SOURCE_LEVELS:
            issues.append(
                f"{prefix}.source_level 必须是 {sorted(_SOURCE_LEVELS)}"
            )
        source_museum_id = str(
            raw_source.get("museum_id", museum_id)
        ).strip()
        if source_museum_id != museum_id:
            issues.append(
                f"{prefix}.museum_id 必须与 museum.id 一致：{museum_id}"
            )
        exhibit_ids = _id_tuple(
            raw_source.get("exhibit_ids", []),
            f"{prefix}.exhibit_ids",
            issues,
        )
        metadata = raw_source.get("metadata", {})
        if not isinstance(metadata, Mapping):
            issues.append(f"{prefix}.metadata 必须是对象")
            metadata = {}
        entries.append(
            SourceManifestEntry(
                id=source_id,
                title=title,
                source_type=source_type,
                path=source_path,
                locator=locator,
                rights_note=rights_note,
                museum_id=source_museum_id,
                publisher=str(raw_source.get("publisher", "")).strip(),
                published_date=str(raw_source.get("published_date", "")).strip(),
                accessed_at=str(raw_source.get("accessed_at", "")).strip(),
                language=str(raw_source.get("language", "zh-CN")).strip(),
                source_level=source_level,
                rights_status=str(
                    raw_source.get("rights_status", "demo_authorized")
                ).strip(),
                exhibit_ids=exhibit_ids,
                metadata=dict(metadata),
            )
        )

    if issues:
        raise SourceManifestError(issues)
    return SourceManifest(
        schema_version=1,
        dataset_id=dataset_id,
        museum_id=museum_id,
        museum_name=museum_name,
        sources=tuple(entries),
    )


def ingest_source_manifest(
    manifest_path: str | Path,
    *,
    store: MuseumStore,
    run_id: str,
    root_dir: str | Path | None = None,
) -> IngestionReport:
    manifest_file = Path(manifest_path).resolve()
    manifest = load_source_manifest(manifest_file)
    root = Path(root_dir or manifest_file.parent).resolve()
    evidence_store = EvidenceStore(store)
    evidence_store.start_ingestion_run(
        run_id=run_id,
        dataset_id=manifest.dataset_id,
        manifest_path=str(manifest_file),
    )

    prepared: list[
        tuple[
            SourceManifestEntry,
            Path,
            str,
            str,
            tuple[SourceSegmentRecord, ...],
        ]
    ] = []
    errors: list[str] = []
    for entry in manifest.sources:
        try:
            source_path = _safe_source_path(root, entry.path)
            checksum = _source_input_checksum(source_path, entry.source_type)
            parsed = parse_source_file(
                source_path,
                source_type=entry.source_type,
            )
            source_version = _source_version_hash(
                entry,
                checksum=checksum,
                parser_version=PARSER_VERSION,
            )
            source_version_id = f"{entry.id}-v-{source_version[:24]}"
            segments = _build_source_segments(
                entry,
                parsed,
                parser_version=PARSER_VERSION,
                source_version=source_version,
                source_version_id=source_version_id,
            )
            prepared.append(
                (
                    entry,
                    source_path,
                    checksum,
                    source_version_id,
                    segments,
                )
            )
        except (OSError, SourceParseError, ValueError) as exc:
            errors.append(f"{entry.id}: {exc}")

    if errors:
        evidence_store.finish_ingestion_run(
            run_id=run_id,
            status="failed",
            source_count=0,
            segment_count=0,
            errors=errors,
        )
        return IngestionReport(run_id, (), (), errors=tuple(errors))

    source_ids: list[str] = []
    segment_ids: list[str] = []
    try:
        publications: list[
            tuple[SourceDocumentRecord, tuple[SourceSegmentRecord, ...]]
        ] = []
        for entry, source_path, checksum, source_version_id, segments in prepared:
            publications.append(
                (
                    SourceDocumentRecord(
                        id=entry.id,
                        museum_id=entry.museum_id,
                        title=entry.title,
                        source_type=entry.source_type,
                        locator=entry.locator,
                        rights_note=entry.rights_note,
                        publisher=entry.publisher,
                        published_date=entry.published_date,
                        accessed_at=entry.accessed_at,
                        language=entry.language,
                        checksum=checksum,
                        source_level=entry.source_level,
                        rights_status=entry.rights_status,
                        original_path=str(source_path),
                        parser_version=PARSER_VERSION,
                        version_id=source_version_id,
                        metadata=entry.metadata,
                    ),
                    segments,
                )
            )
        published = evidence_store.publish_source_batch(
            museum_id=manifest.museum_id,
            museum_name=manifest.museum_name,
            publications=publications,
        )
        for entry, _source_path, _checksum, _source_version_id, _segments in prepared:
            source_ids.append(entry.id)
            segment_ids.extend(published[entry.id])
    except (OSError, ValueError, sqlite3.Error) as exc:
        errors.append(str(exc))

    status = "failed" if errors else "succeeded"
    evidence_store.finish_ingestion_run(
        run_id=run_id,
        status=status,
        source_count=len(source_ids),
        segment_count=len(segment_ids),
        errors=errors,
    )
    return IngestionReport(
        run_id=run_id,
        source_ids=tuple(source_ids),
        segment_ids=tuple(segment_ids),
        errors=tuple(errors),
    )


def parse_source_file(
    path: str | Path,
    *,
    source_type: str = "",
    max_chars: int = 800,
    overlap_chars: int = 80,
) -> tuple[ParsedSegment, ...]:
    source_path = Path(path)
    kind = source_type.lower().strip() or _source_type_from_suffix(source_path)
    if kind in {"markdown", "md", "text", "txt"}:
        text = _read_text(source_path)
        return _chunk_markdown(text, max_chars=max_chars, overlap_chars=overlap_chars)
    if kind in {"html", "htm"}:
        text, sections = _read_html(source_path)
        return _chunk_sections(text, sections, max_chars, overlap_chars)
    if kind == "json":
        payload = json.loads(_read_text(source_path))
        return _chunk_json(payload, max_chars=max_chars, overlap_chars=overlap_chars)
    if kind == "pdf":
        return _parse_pdf(source_path, max_chars=max_chars, overlap_chars=overlap_chars)
    if kind == "image":
        return _parse_ocr_sidecar(
            source_path,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )
    raise SourceParseError(f"不支持的资料类型：{kind}")


def _build_source_segments(
    entry: SourceManifestEntry,
    parsed: Sequence[ParsedSegment],
    *,
    parser_version: str,
    source_version: str,
    source_version_id: str,
) -> tuple[SourceSegmentRecord, ...]:
    segments: list[SourceSegmentRecord] = []
    for ordinal, item in enumerate(parsed, start=1):
        text = item.text.strip()
        if not text:
            continue
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        locator = item.locator or entry.locator
        if item.page is not None and "page=" not in locator:
            locator = f"{locator}#page={item.page}"
        identity = json.dumps(
            {
                "source_version": source_version,
                "ordinal": ordinal,
                "locator": locator,
                "section": item.section,
                "page": item.page,
                "content_hash": content_hash,
                "ocr_confidence": item.ocr_confidence,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        segment_id = f"{entry.id}-seg{ordinal:04d}-{identity_hash[:16]}"
        segments.append(
            SourceSegmentRecord(
                id=segment_id,
                source_id=entry.id,
                text=text,
                locator=locator,
                exhibit_ids=entry.exhibit_ids,
                section=item.section,
                page=item.page,
                ordinal=ordinal,
                content_hash=content_hash,
                parser_version=parser_version,
                source_version_id=source_version_id,
                ocr_confidence=item.ocr_confidence,
            )
        )
    return tuple(segments)


def _source_version_hash(
    entry: SourceManifestEntry,
    *,
    checksum: str,
    parser_version: str,
) -> str:
    payload = json.dumps(
        {
            "checksum": checksum,
            "source_type": entry.source_type,
            "title": entry.title,
            "locator": entry.locator,
            "rights_note": entry.rights_note,
            "publisher": entry.publisher,
            "published_date": entry.published_date,
            "accessed_at": entry.accessed_at,
            "language": entry.language,
            "source_level": entry.source_level,
            "rights_status": entry.rights_status,
            "exhibit_ids": sorted(entry.exhibit_ids),
            "parser_version": parser_version,
            "metadata": dict(entry.metadata),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_source_path(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise SourceParseError("资料路径必须位于 manifest 根目录内") from exc
    if not candidate.is_file():
        raise SourceParseError(f"资料文件不存在：{relative_path}")
    return candidate


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SourceParseError(f"{path}: 资料必须使用 UTF-8 编码") from exc
    except OSError as exc:
        raise SourceParseError(f"{path}: 无法读取：{exc}") from exc


def _source_type_from_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".md": "markdown",
        ".txt": "text",
        ".json": "json",
        ".html": "html",
        ".htm": "html",
        ".pdf": "pdf",
        ".png": "image",
        ".jpg": "image",
        ".jpeg": "image",
    }.get(suffix, "")


def _chunk_markdown(
    text: str,
    *,
    max_chars: int,
    overlap_chars: int,
) -> tuple[ParsedSegment, ...]:
    sections: list[tuple[str, str]] = []
    current_section = ""
    buffer: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        if match:
            if buffer:
                sections.append((current_section, "\n".join(buffer)))
                buffer = []
            current_section = match.group(1).strip()
        elif line.strip():
            buffer.append(line.strip())
    if buffer:
        sections.append((current_section, "\n".join(buffer)))
    if not sections and text.strip():
        sections = [("", text.strip())]
    return _chunk_sections(text, sections, max_chars, overlap_chars)


def _chunk_sections(
    full_text: str,
    sections: Sequence[tuple[str, str]],
    max_chars: int,
    overlap_chars: int,
) -> tuple[ParsedSegment, ...]:
    del full_text
    result: list[ParsedSegment] = []
    for section, text in sections:
        for chunk in _split_text(text, max_chars=max_chars, overlap_chars=overlap_chars):
            result.append(ParsedSegment(text=chunk, locator="", section=section))
    return tuple(result)


def _split_text(text: str, *, max_chars: int, overlap_chars: int) -> tuple[str, ...]:
    if max_chars <= 0 or overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("chunk 参数无效")
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            candidate = f"{current}\n{paragraph}".strip() if current else paragraph
            if len(candidate) <= max_chars:
                current = candidate
                continue
            if current:
                chunks.append(current)
            current = paragraph
            continue
        if current:
            chunks.append(current)
            current = ""
        start = 0
        while start < len(paragraph):
            end = min(len(paragraph), start + max_chars)
            chunks.append(paragraph[start:end].strip())
            if end >= len(paragraph):
                break
            start = end - overlap_chars
    if current:
        chunks.append(current)
    return tuple(chunk for chunk in chunks if chunk)


class _PlainTextHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.section = ""
        self.sections: list[tuple[str, str]] = []
        self._buffer: list[str] = []
        self._heading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._flush_buffer()
            self._heading = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            heading = " ".join(self._buffer).strip()
            if heading:
                self.section = heading
            self._buffer = []
            self._heading = False

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.parts.append(value)
            self._buffer.append(value)

    def _flush_buffer(self) -> None:
        text = " ".join(self._buffer).strip()
        if text and not self._heading:
            self.sections.append((self.section, text))
        self._buffer = []

    def finish(self) -> tuple[str, tuple[tuple[str, str], ...]]:
        self._flush_buffer()
        return " ".join(self.parts), tuple(self.sections)


def _read_html(path: Path) -> tuple[str, tuple[tuple[str, str], ...]]:
    parser = _PlainTextHtmlParser()
    try:
        parser.feed(_read_text(path))
        parser.close()
    except Exception as exc:
        raise SourceParseError(f"{path}: HTML 解析失败：{exc}") from exc
    text, sections = parser.finish()
    return text, sections or (("", text),)


def _chunk_json(
    payload: Any,
    *,
    max_chars: int,
    overlap_chars: int,
) -> tuple[ParsedSegment, ...]:
    lines: list[str] = []

    def visit(value: Any, prefix: str = "") -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                visit(child, f"{prefix}.{key}" if prefix else str(key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{prefix}[{index}]")
        else:
            lines.append(f"{prefix}: {value}" if prefix else str(value))

    visit(payload)
    return _chunk_sections(
        "\n".join(lines),
        [("json", "\n".join(lines))],
        max_chars,
        overlap_chars,
    )


def _parse_pdf(
    path: Path,
    *,
    max_chars: int,
    overlap_chars: int,
) -> tuple[ParsedSegment, ...]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise SourceParseError(
            "PDF 解析需要 pypdf；先安装项目锁定版本后再摄取 PDF"
        ) from exc
    try:
        reader = PdfReader(str(path))
        result: list[ParsedSegment] = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            for chunk in _split_text(
                text,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            ):
                result.append(
                    ParsedSegment(
                        text=chunk,
                        locator=f"{path.name}#page={page_number}",
                        page=page_number,
                    )
                )
        return tuple(result)
    except Exception as exc:
        raise SourceParseError(f"{path}: PDF 解析失败：{exc}") from exc


def _parse_ocr_sidecar(
    path: Path,
    *,
    max_chars: int,
    overlap_chars: int,
) -> tuple[ParsedSegment, ...]:
    sidecar = _ocr_sidecar_path(path)
    if sidecar is None:
        raise SourceParseError(f"{path}: 未找到 OCR sidecar（应为 {path.name}.ocr.json）")
    try:
        payload = json.loads(_read_text(sidecar))
    except json.JSONDecodeError as exc:
        raise SourceParseError(f"{sidecar}: OCR sidecar 不是合法 JSON") from exc
    if not isinstance(payload, Mapping):
        raise SourceParseError(f"{sidecar}: OCR sidecar 根节点必须是对象")
    text = str(payload.get("text", "")).strip()
    confidence = payload.get("confidence")
    ocr_confidence = float(confidence) if confidence is not None else None
    if not text:
        raise SourceParseError(f"{sidecar}: OCR sidecar 缺少 text")
    return tuple(
        ParsedSegment(
            text=chunk,
            locator=path.name,
            ocr_confidence=ocr_confidence,
        )
        for chunk in _split_text(
            text,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )
    )


def _source_input_checksum(path: Path, source_type: str) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    if source_type.lower().strip() == "image":
        sidecar = _ocr_sidecar_path(path)
        if sidecar is None:
            raise SourceParseError(
                f"{path}: 未找到 OCR sidecar（应为 {path.name}.ocr.json）"
            )
        digest.update(b"\x00ocr-sidecar\x00")
        digest.update(sidecar.read_bytes())
    return digest.hexdigest()


def _ocr_sidecar_path(path: Path) -> Path | None:
    candidates = (
        Path(f"{path}.ocr.json"),
        path.with_suffix(".ocr.json"),
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _required_id(value: Any, field_name: str, issues: list[str]) -> str:
    text = str(value or "").strip()
    if not text or not _ID_PATTERN.fullmatch(text):
        issues.append(f"{field_name} 必须匹配 {_ID_PATTERN.pattern}")
    return text


def _required_text(value: Any, field_name: str, issues: list[str]) -> str:
    text = str(value or "").strip()
    if not text:
        issues.append(f"{field_name} 不能为空")
    return text


def _id_tuple(value: Any, field_name: str, issues: list[str]) -> tuple[str, ...]:
    if not isinstance(value, list):
        issues.append(f"{field_name} 必须是数组")
        return ()
    result: list[str] = []
    for index, item in enumerate(value):
        text = str(item or "").strip()
        if not _ID_PATTERN.fullmatch(text):
            issues.append(f"{field_name}[{index}] 不是合法 ID")
        elif text not in result:
            result.append(text)
    return tuple(result)
