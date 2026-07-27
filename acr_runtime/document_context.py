from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from .code_index import (
    CodebaseIndexer,
    IndexPolicy,
    _hash_bytes,
    _path_is_safe,
    _repository_key,
    _safe_relative,
)
from .content_security import (
    ContentAssessmentRequest,
    ContentSecurityController,
    detect_suspicious_instructions,
)
from .memory import utc_now
from .scoring import estimate_tokens
from .secret_management import detect_secret_material

PARSER_VERSION = "acr-markdown-context-v1"
PARSER_CONFIG_HASH = hashlib.sha256(
    b"commonmark-atx-setext-fences;semantic-first;no-overlap"
).hexdigest()
DEFAULT_MAX_CHUNK_CHARS = 8_000
HARD_MAX_CHUNK_CHARS = 32_000
MAX_DOCUMENTS = 4_000
MAX_SECTIONS_PER_DOCUMENT = 4_000
MAX_CHUNKS_PER_DOCUMENT = 8_000
MAX_RELATIONSHIPS_PER_DOCUMENT = 16_000
MAX_QUERY = 512
_ATX = re.compile(
    r"^ {0,3}(#{1,6})(?:[ \t]+(.*?)(?:[ \t]+#+[ \t]*)?|[ \t]*)$"
)
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_LINK = re.compile(
    r"(?<![!\\])\[[^\]\r\n]{1,256}\]\((#[^) \t\r\n]{1,256})\)"
)
_CODE_SPAN = re.compile(r"`+[^`\r\n]*`+")
_WORD = re.compile(r"[\w.-]{2,64}", re.UNICODE)


def _stable_id(kind: str, *parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "\0".join((kind, *parts))))


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _byte_offset(text: str, char_offset: int) -> int:
    return len(text[:char_offset].encode("utf-8"))


def _line(text: str, char_offset: int) -> int:
    return text.count("\n", 0, char_offset) + 1


def _slug(value: str) -> str:
    normalized = re.sub(r"[^\w -]+", "", value.casefold(), flags=re.UNICODE)
    return re.sub(r"[-\s]+", "-", normalized).strip("-") or "section"


@dataclass(frozen=True)
class DocumentIndexRequest:
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS

    def __post_init__(self) -> None:
        if not 256 <= self.max_chunk_chars <= HARD_MAX_CHUNK_CHARS:
            raise ValueError(
                f"max_chunk_chars must be 256..{HARD_MAX_CHUNK_CHARS}"
            )


@dataclass(frozen=True)
class DocumentContextRequest:
    query: str
    mode: str = "lexical"
    document: str | None = None
    section_id: str | None = None
    occurrence: int | None = None
    max_tokens: int = 4_000
    max_chunks: int = 8

    def __post_init__(self) -> None:
        if (
            not self.query.strip()
            or self.query != self.query.strip()
            or len(self.query) > MAX_QUERY
        ):
            raise ValueError("query must be bounded, non-empty, and trimmed")
        if self.mode not in {"lexical", "exact"}:
            raise ValueError("mode must be lexical or exact")
        if self.document is not None:
            _safe_relative(self.document)
        if self.section_id is not None and len(self.section_id) > 128:
            raise ValueError("section_id is invalid")
        if self.occurrence is not None and not 0 <= self.occurrence <= 10_000:
            raise ValueError("occurrence must be 0..10000")
        if not 64 <= self.max_tokens <= 20_000:
            raise ValueError("max_tokens must be 64..20000")
        if not 1 <= self.max_chunks <= 24:
            raise ValueError("max_chunks must be 1..24")


@dataclass(frozen=True)
class _Heading:
    start: int
    end: int
    line: int
    level: int
    text: str


class DocumentContextEngine:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        security: ContentSecurityController | None = None,
    ) -> None:
        self.connection = connection
        self.security = security or ContentSecurityController(connection)

    @staticmethod
    def _headings(text: str) -> list[_Heading]:
        lines = text.splitlines(keepends=True)
        starts: list[int] = []
        cursor = 0
        for raw in lines:
            starts.append(cursor)
            cursor += len(raw)
        headings: list[_Heading] = []
        fence_char: str | None = None
        fence_width = 0
        in_comment = False
        for index, raw in enumerate(lines):
            value = raw.rstrip("\r\n")
            if in_comment:
                if "-->" in value:
                    in_comment = False
                continue
            if "<!--" in value and "-->" not in value.split("<!--", 1)[1]:
                in_comment = True
                continue
            fence = _FENCE.match(value)
            if fence:
                marker = fence.group(1)
                if fence_char is None:
                    fence_char, fence_width = marker[0], len(marker)
                elif marker[0] == fence_char and len(marker) >= fence_width:
                    fence_char, fence_width = None, 0
                continue
            if fence_char is not None:
                continue
            atx = _ATX.match(value)
            if atx and atx.group(2) is not None:
                heading = atx.group(2).strip()
                if heading:
                    headings.append(
                        _Heading(
                            starts[index],
                            starts[index] + len(raw),
                            index + 1,
                            len(atx.group(1)),
                            heading,
                        )
                    )
                continue
            if index and _SETEXT.match(value):
                previous_raw = lines[index - 1]
                previous = previous_raw.rstrip("\r\n").strip()
                if previous and not previous.startswith((">", "-", "*", "+")):
                    headings.append(
                        _Heading(
                            starts[index - 1],
                            starts[index] + len(raw),
                            index,
                            1 if value.lstrip().startswith("=") else 2,
                            previous,
                        )
                    )
        if len(headings) > MAX_SECTIONS_PER_DOCUMENT:
            raise ValueError("document exceeds the heading limit")
        return sorted(headings, key=lambda item: item.start)

    @staticmethod
    def _chunk_ranges(text: str, start: int, end: int, limit: int):
        if end - start <= limit:
            yield start, end, "semantic_section", "section_boundary"
            return
        raw = text[start:end]
        blocks: list[tuple[int, int]] = []
        block_start = start
        cursor = start
        fence_char: str | None = None
        fence_width = 0
        for line_raw in raw.splitlines(keepends=True):
            value = line_raw.rstrip("\r\n")
            fence = _FENCE.match(value)
            if fence:
                marker = fence.group(1)
                if fence_char is None:
                    fence_char, fence_width = marker[0], len(marker)
                elif marker[0] == fence_char and len(marker) >= fence_width:
                    fence_char, fence_width = None, 0
            cursor += len(line_raw)
            if fence_char is None and not value.strip():
                blocks.append((block_start, cursor))
                block_start = cursor
        if block_start < end:
            blocks.append((block_start, end))
        group_start: int | None = None
        group_end = 0
        for block_start, block_end in blocks:
            if block_end - block_start > limit:
                if group_start is not None:
                    yield (
                        group_start, group_end, "paragraph_group",
                        "oversized_section",
                    )
                    group_start = None
                yield (
                    block_start, block_end, "oversize_atomic_block",
                    "oversized_block",
                )
                continue
            if group_start is None:
                group_start, group_end = block_start, block_end
            elif block_end - group_start <= limit:
                group_end = block_end
            else:
                yield (
                    group_start, group_end, "paragraph_group",
                    "oversized_section",
                )
                group_start, group_end = block_start, block_end
        if group_start is not None:
            yield (
                group_start, group_end, "paragraph_group",
                "oversized_section",
            )

    @staticmethod
    def _link_targets(text: str) -> tuple[str, ...]:
        targets: list[str] = []
        fence_char: str | None = None
        fence_width = 0
        in_comment = False
        for raw in text.splitlines():
            value = raw.rstrip("\r\n")
            if in_comment:
                if "-->" in value:
                    in_comment = False
                continue
            if "<!--" in value:
                prefix, suffix = value.split("<!--", 1)
                value = prefix
                if "-->" not in suffix:
                    in_comment = True
            fence = _FENCE.match(value)
            if fence:
                marker = fence.group(1)
                if fence_char is None:
                    fence_char, fence_width = marker[0], len(marker)
                elif marker[0] == fence_char and len(marker) >= fence_width:
                    fence_char, fence_width = None, 0
                continue
            if fence_char is None:
                visible = _CODE_SPAN.sub("", value)
                targets.extend(
                    match.group(1) for match in _LINK.finditer(visible)
                )
        return tuple(targets)

    @classmethod
    def _parse(
        cls,
        document_id: str,
        text: str,
        max_chunk_chars: int,
        *,
        structured: bool = True,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
        headings = cls._headings(text) if structured else []
        heading_rows: list[dict[str, object]] = []
        heading_stack: list[dict[str, object]] = []
        anchor_counts: dict[str, int] = {}
        for ordinal, heading in enumerate(headings):
            while heading_stack and int(heading_stack[-1]["level"]) >= heading.level:
                heading_stack.pop()
            parent = heading_stack[-1] if heading_stack else None
            base_anchor = _slug(heading.text)
            count = anchor_counts.get(base_anchor, 0)
            anchor_counts[base_anchor] = count + 1
            anchor = base_anchor if count == 0 else f"{base_anchor}-{count}"
            path = " / ".join(
                [*(str(item["text"]) for item in heading_stack), heading.text]
            )
            row = {
                "id": _stable_id(
                    "document-heading", document_id, str(ordinal), str(heading.start)
                ),
                "parent_id": parent["id"] if parent else None,
                "ordinal": ordinal,
                "level": heading.level,
                "text": heading.text,
                "path": path,
                "anchor": anchor,
                "start": heading.start,
                "end": heading.end,
                "line": heading.line,
                "hash": _text_hash(text[heading.start:heading.end]),
            }
            heading_rows.append(row)
            heading_stack.append(row)

        boundaries: list[tuple[int, dict[str, object] | None]] = []
        if not heading_rows or int(heading_rows[0]["start"]) > 0:
            boundaries.append((0, None))
        boundaries.extend((int(item["start"]), item) for item in heading_rows)
        sections: list[dict[str, object]] = []
        heading_to_section: dict[str, str] = {}
        section_stack: list[dict[str, object]] = []
        for ordinal, (start, heading) in enumerate(boundaries):
            end = boundaries[ordinal + 1][0] if ordinal + 1 < len(boundaries) else len(text)
            level = int(heading["level"]) if heading else 0
            while section_stack and int(section_stack[-1]["level"]) >= level:
                section_stack.pop()
            parent = section_stack[-1] if level and section_stack else None
            section_id = _stable_id(
                "document-section", document_id, str(ordinal), str(start), str(end)
            )
            section = {
                "id": section_id,
                "heading_id": heading["id"] if heading else None,
                "parent_id": parent["id"] if parent else None,
                "ordinal": ordinal,
                "level": level,
                "start": start,
                "end": end,
                "start_line": _line(text, start),
                "end_line": _line(text, max(start, end - 1)),
                "hash": _text_hash(text[start:end]),
            }
            sections.append(section)
            if heading:
                heading_to_section[str(heading["id"])] = section_id
                section_stack.append(section)
        if len(sections) > MAX_SECTIONS_PER_DOCUMENT:
            raise ValueError("document exceeds the section limit")

        chunks: list[dict[str, object]] = []
        for section in sections:
            for start, end, kind, reason in cls._chunk_ranges(
                text,
                int(section["start"]),
                int(section["end"]),
                max_chunk_chars,
            ):
                chunks.append(
                    {
                        "id": _stable_id(
                            "document-chunk", document_id, str(len(chunks)), str(start)
                        ),
                        "section_id": section["id"],
                        "ordinal": len(chunks),
                        "kind": kind,
                        "reason": reason,
                        "start": start,
                        "end": end,
                        "start_line": _line(text, start),
                        "end_line": _line(text, max(start, end - 1)),
                        "hash": _text_hash(text[start:end]),
                        "tokens": estimate_tokens(text[start:end]),
                    }
                )
        if len(chunks) > MAX_CHUNKS_PER_DOCUMENT:
            raise ValueError("document exceeds the chunk limit")

        relationships: list[dict[str, object]] = []
        anchor_sections = {
            str(heading["anchor"]): heading_to_section[str(heading["id"])]
            for heading in heading_rows
        }
        for index, section in enumerate(sections):
            if section["parent_id"]:
                relationships.append(
                    {"source": section["id"], "target": section["parent_id"], "kind": "parent", "ref": None}
                )
            if index:
                relationships.append(
                    {"source": section["id"], "target": sections[index - 1]["id"], "kind": "previous", "ref": None}
                )
            if index + 1 < len(sections):
                relationships.append(
                    {"source": section["id"], "target": sections[index + 1]["id"], "kind": "next", "ref": None}
                )
            raw = text[int(section["start"]):int(section["end"])]
            for ref in cls._link_targets(raw):
                relationships.append(
                    {
                        "source": section["id"],
                        "target": anchor_sections.get(ref[1:].casefold()),
                        "kind": "link",
                        "ref": ref,
                    }
                )
        if len(relationships) > MAX_RELATIONSHIPS_PER_DOCUMENT:
            raise ValueError("document exceeds the relationship limit")
        return heading_rows, sections, chunks, relationships

    def index(
        self,
        root: str | Path,
        request: DocumentIndexRequest | None = None,
    ) -> dict[str, object]:
        request = request or DocumentIndexRequest()
        resolved = CodebaseIndexer._root(root)
        repository = self.connection.execute(
            "SELECT * FROM code_repositories WHERE repository_key=?",
            (_repository_key(resolved),),
        ).fetchone()
        if repository is None or repository["current_run_id"] is None:
            raise LookupError("repository must have an active Prompt 53 index")
        policy = IndexPolicy(**json.loads(repository["index_config_json"]))
        if CodebaseIndexer(self.connection).snapshot(resolved, policy) != repository["snapshot_hash"]:
            raise ValueError("repository snapshot changed; reindex code first")
        files = self.connection.execute(
            """
            SELECT * FROM code_files
            WHERE repository_id=? AND file_kind='documentation'
              AND language IN ('markdown', 'text')
              AND lower(relative_path) NOT LIKE '%.mdx'
            ORDER BY relative_path
            LIMIT ?
            """,
            (repository["id"], MAX_DOCUMENTS + 1),
        ).fetchall()
        if len(files) > MAX_DOCUMENTS:
            raise ValueError("repository exceeds the document limit")

        parsed: list[dict[str, object]] = []
        for row in files:
            relative = _safe_relative(row["relative_path"])
            path = _path_is_safe(resolved, relative)
            if path is None:
                raise ValueError("document path changed; reindex code first")
            data = CodebaseIndexer._read_file(path, int(row["size_bytes"]))
            if _hash_bytes(data) != row["content_hash"]:
                raise ValueError("document changed; reindex code first")
            encoding = "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
            text = data.decode(encoding, errors="strict")
            if detect_secret_material(text):
                raise ValueError("secret material detected during document indexing")
            document_id = _stable_id(
                "document",
                repository["id"],
                str(repository["generation"]),
                relative,
                row["content_hash"],
            )
            headings, sections, chunks, relationships = self._parse(
                document_id,
                text,
                request.max_chunk_chars,
                structured=row["language"] == "markdown",
            )
            title = (
                str(headings[0]["text"])
                if headings and int(headings[0]["level"]) == 1
                else Path(relative).stem
            )
            parsed.append(
                {
                    "id": document_id,
                    "file": row,
                    "relative": relative,
                    "text": text,
                    "encoding": encoding,
                    "media_type": (
                        "text/markdown"
                        if row["language"] == "markdown"
                        else "text/plain"
                    ),
                    "title": title[:512],
                    "signals": detect_suspicious_instructions(text),
                    "headings": headings,
                    "sections": sections,
                    "chunks": chunks,
                    "relationships": relationships,
                }
            )

        indexed_at = utc_now()
        counts = {
            "documents": len(parsed),
            "headings": sum(len(item["headings"]) for item in parsed),
            "sections": sum(len(item["sections"]) for item in parsed),
            "chunks": sum(len(item["chunks"]) for item in parsed),
            "relationships": sum(len(item["relationships"]) for item in parsed),
        }
        with self.connection:
            self.connection.execute(
                "DELETE FROM documents WHERE repository_id=?",
                (repository["id"],),
            )
            self.connection.execute(
                """
                INSERT OR REPLACE INTO document_indexes (
                    repository_id, generation, snapshot_hash, parser_version,
                    parser_config_hash, counts_json, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    repository["id"], repository["generation"],
                    repository["snapshot_hash"], PARSER_VERSION,
                    PARSER_CONFIG_HASH, json.dumps(counts), indexed_at,
                ),
            )
            for document in parsed:
                row = document["file"]
                text = str(document["text"])
                self.connection.execute(
                    """
                    INSERT INTO documents (
                        id, repository_id, source_file_id, generation,
                        relative_path, source_bytes_hash, title, media_type,
                        encoding, size_bytes, char_count, line_count,
                        parser_version, parser_config_hash, status,
                        suspicious_signals_json, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document["id"], repository["id"], row["id"],
                        repository["generation"], document["relative"],
                        row["content_hash"], document["title"],
                        document["media_type"], document["encoding"],
                        row["size_bytes"], len(text),
                        text.count("\n") + (1 if text else 0), PARSER_VERSION,
                        PARSER_CONFIG_HASH, "indexed",
                        json.dumps(document["signals"]), indexed_at,
                    ),
                )
                for heading in document["headings"]:
                    self.connection.execute(
                        """
                        INSERT INTO document_headings VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        (
                            heading["id"], document["id"], heading["parent_id"],
                            heading["ordinal"], heading["level"], heading["text"],
                            heading["path"], heading["anchor"], heading["start"],
                            heading["end"], _byte_offset(text, int(heading["start"])),
                            _byte_offset(text, int(heading["end"])),
                            heading["line"], heading["hash"],
                        ),
                    )
                for section in document["sections"]:
                    self.connection.execute(
                        """
                        INSERT INTO document_sections (
                            id, document_id, heading_id, parent_section_id,
                            ordinal, level, start_char, end_char, start_byte,
                            end_byte, start_line, end_line, content_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            section["id"], document["id"], section["heading_id"],
                            section["parent_id"], section["ordinal"], section["level"],
                            section["start"], section["end"],
                            _byte_offset(text, int(section["start"])),
                            _byte_offset(text, int(section["end"])),
                            section["start_line"], section["end_line"],
                            section["hash"],
                        ),
                    )
                for chunk in document["chunks"]:
                    self.connection.execute(
                        """
                        INSERT INTO document_chunks (
                            id, document_id, section_id, ordinal, chunk_kind,
                            split_reason, start_char, end_char, start_byte,
                            end_byte, start_line, end_line, content_hash,
                            token_cost, exact_preserved
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            chunk["id"], document["id"], chunk["section_id"],
                            chunk["ordinal"], chunk["kind"], chunk["reason"],
                            chunk["start"], chunk["end"],
                            _byte_offset(text, int(chunk["start"])),
                            _byte_offset(text, int(chunk["end"])),
                            chunk["start_line"], chunk["end_line"],
                            chunk["hash"], chunk["tokens"],
                        ),
                    )
                for ordinal, relationship in enumerate(document["relationships"]):
                    self.connection.execute(
                        """
                        INSERT INTO document_relationships VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            _stable_id(
                                "document-relationship",
                                str(document["id"]),
                                str(ordinal),
                            ),
                            document["id"], relationship["source"],
                            relationship["target"], relationship["kind"],
                            relationship["ref"],
                        ),
                    )
        return {
            "status": "completed",
            "repository_id": repository["id"],
            "generation": repository["generation"],
            "snapshot_hash": repository["snapshot_hash"],
            "parser_version": PARSER_VERSION,
            "source_bodies_persisted": False,
            "counts": counts,
            "indexed_at": indexed_at,
        }

    def _repository(
        self, root: str | Path
    ) -> tuple[Path, sqlite3.Row, IndexPolicy] | tuple[Path, None, None]:
        resolved = CodebaseIndexer._root(root)
        repository = self.connection.execute(
            "SELECT * FROM code_repositories WHERE repository_key=?",
            (_repository_key(resolved),),
        ).fetchone()
        if repository is None:
            return resolved, None, None
        policy = IndexPolicy(**json.loads(repository["index_config_json"]))
        return resolved, repository, policy

    def _verified_sources(
        self, root: str | Path, request: DocumentContextRequest
    ) -> tuple[str | None, list[tuple[sqlite3.Row, str]]]:
        resolved, repository, policy = self._repository(root)
        if repository is None or policy is None:
            return "repository_not_indexed", []
        document_index = self.connection.execute(
            "SELECT * FROM document_indexes WHERE repository_id=?",
            (repository["id"],),
        ).fetchone()
        if document_index is None:
            return "document_index_not_built", []
        if (
            document_index["generation"] != repository["generation"]
            or document_index["snapshot_hash"] != repository["snapshot_hash"]
            or document_index["parser_config_hash"] != PARSER_CONFIG_HASH
        ):
            return "document_index_stale", []
        if CodebaseIndexer(self.connection).snapshot(resolved, policy) != repository["snapshot_hash"]:
            return "repository_snapshot_changed", []
        sql = """
            SELECT d.*, f.size_bytes, f.content_hash AS file_hash
            FROM documents d JOIN code_files f ON f.id=d.source_file_id
            WHERE d.repository_id=? AND d.generation=?
        """
        params: list[object] = [repository["id"], repository["generation"]]
        if request.document is not None:
            sql += " AND d.relative_path=?"
            params.append(request.document)
        sql += " ORDER BY d.relative_path"
        sources: list[tuple[sqlite3.Row, str]] = []
        for row in self.connection.execute(sql, params):
            path = _path_is_safe(resolved, _safe_relative(row["relative_path"]))
            if path is None:
                return "document_path_changed", []
            try:
                data = CodebaseIndexer._read_file(path, int(row["size_bytes"]))
            except (OSError, ValueError):
                return "document_changed", []
            if _hash_bytes(data) != row["file_hash"]:
                return "document_hash_changed", []
            sources.append(
                (row, data.decode(row["encoding"], errors="strict"))
            )
        return None, sources

    def retrieve(
        self, root: str | Path, request: DocumentContextRequest
    ) -> dict[str, object]:
        stale_reason, sources = self._verified_sources(root, request)
        if stale_reason:
            return self._empty(request, "stale", (stale_reason,))
        candidates: list[dict[str, object]] = []
        terms = tuple(dict.fromkeys(_WORD.findall(request.query.casefold())))[:12]
        total_exact = 0
        for document, text in sources:
            chunks = self.connection.execute(
                """
                SELECT c.*, s.heading_id, h.heading, h.qualified_path
                FROM document_chunks c
                JOIN document_sections s ON s.id=c.section_id
                LEFT JOIN document_headings h ON h.id=s.heading_id
                WHERE c.document_id=?
                ORDER BY c.ordinal
                """,
                (document["id"],),
            ).fetchall()
            if request.mode == "exact":
                cursor = 0
                while True:
                    found = text.find(request.query, cursor)
                    if found < 0:
                        break
                    match_end = found + len(request.query)
                    overlapping = [
                        row for row in chunks
                        if row["end_char"] > found
                        and row["start_char"] < match_end
                        and (
                            request.section_id is None
                            or row["section_id"] == request.section_id
                        )
                    ]
                    if (
                        overlapping
                        and overlapping[0]["start_char"] <= found
                        and overlapping[-1]["end_char"] >= match_end
                    ):
                        if any(
                            _text_hash(
                                text[row["start_char"]:row["end_char"]]
                            )
                            != row["content_hash"]
                            for row in overlapping
                        ):
                            return self._empty(
                                request,
                                "stale",
                                ("retained_span_hash_mismatch",),
                            )
                        first = dict(overlapping[0])
                        last = overlapping[-1]
                        first["id"] = _stable_id(
                            "document-exact-context",
                            document["id"],
                            str(found),
                            str(match_end),
                        )
                        first["end_char"] = last["end_char"]
                        first["end_byte"] = last["end_byte"]
                        first["end_line"] = last["end_line"]
                        first["chunk_kind"] = "exact_span_context"
                        first["split_reason"] = "exact_quote_context"
                        raw = text[first["start_char"]:first["end_char"]]
                        first["content_hash"] = _text_hash(raw)
                        candidates.append(
                            {
                                "document": document,
                                "chunk": first,
                                "raw": raw,
                                "score": 1.0,
                                "match_start": found,
                                "match_end": match_end,
                                "occurrence": total_exact,
                            }
                        )
                        total_exact += 1
                    cursor = found + max(1, len(request.query))
                continue
            for chunk in chunks:
                if (
                    request.section_id is not None
                    and chunk["section_id"] != request.section_id
                ):
                    continue
                raw = text[chunk["start_char"]:chunk["end_char"]]
                if _text_hash(raw) != chunk["content_hash"]:
                    return self._empty(
                        request, "stale", ("retained_span_hash_mismatch",)
                    )
                if not terms:
                    continue
                body = raw.casefold()
                heading = (chunk["heading"] or "").casefold()
                found_terms = sum(term in body for term in terms)
                if found_terms != len(terms):
                    continue
                heading_hits = sum(term in heading for term in terms)
                score = heading_hits * 10 + sum(body.count(term) for term in terms)
                candidates.append(
                    {
                        "document": document,
                        "chunk": chunk,
                        "raw": raw,
                        "score": float(score),
                        "match_start": None,
                        "match_end": None,
                        "occurrence": None,
                    }
                )
        if request.mode == "exact":
            if request.occurrence is None and total_exact > 1:
                return {
                    **self._empty(request, "ambiguous", ("occurrence_required",)),
                    "candidate_count": total_exact,
                }
            if request.occurrence is not None:
                candidates = [
                    item for item in candidates
                    if item["occurrence"] == request.occurrence
                ]
        if not candidates:
            return self._empty(request, "not_found")
        candidates.sort(
            key=lambda item: (
                -float(item["score"]),
                item["document"]["relative_path"],
                item["chunk"]["start_char"],
                item["chunk"]["id"],
            )
        )

        selected: list[dict[str, object]] = []
        used = 0
        omitted = 0
        seen_chunks: set[str] = set()
        for candidate in candidates:
            chunk = candidate["chunk"]
            if chunk["id"] in seen_chunks:
                continue
            raw = str(candidate["raw"])
            assessment_request = ContentAssessmentRequest(
                origin="document",
                source_id=(
                    f"document:{candidate['document']['id']}:"
                    f"chunk:{chunk['id']}"
                ),
                content=raw,
                provenance=(
                    f"sha256:{candidate['document']['source_bytes_hash']}",
                    f"chars:{chunk['start_char']}:{chunk['end_char']}",
                ),
            )
            assessment = self.security.assess(assessment_request)
            framed = self.security.frame_untrusted(
                assessment_request, assessment
            )
            tokens = estimate_tokens(framed)
            if (
                len(selected) >= request.max_chunks
                or used + tokens > request.max_tokens
            ):
                omitted += 1
                continue
            seen_chunks.add(chunk["id"])
            used += tokens
            selected.append(
                {
                    "document_id": candidate["document"]["id"],
                    "path": candidate["document"]["relative_path"],
                    "title": html.escape(candidate["document"]["title"]),
                    "section_id": chunk["section_id"],
                    "heading": (
                        html.escape(chunk["heading"])
                        if chunk["heading"] is not None
                        else None
                    ),
                    "heading_path": (
                        html.escape(chunk["qualified_path"])
                        if chunk["qualified_path"] is not None
                        else None
                    ),
                    "chunk_id": chunk["id"],
                    "chunk_kind": chunk["chunk_kind"],
                    "split_reason": chunk["split_reason"],
                    "span": {
                        "start_char": chunk["start_char"],
                        "end_char": chunk["end_char"],
                        "start_byte": chunk["start_byte"],
                        "end_byte": chunk["end_byte"],
                        "start_line": chunk["start_line"],
                        "end_line": chunk["end_line"],
                    },
                    "content": framed,
                    "source_text_hash": chunk["content_hash"],
                    "source_bytes_hash": candidate["document"]["source_bytes_hash"],
                    "original_text_exact": True,
                    "transport_framed": True,
                    "metadata_transport": "html_escape",
                    "authority": "none",
                    "security_assessment_id": assessment["id"],
                    "suspicious_signals": assessment["suspicious_signals"],
                    "exact_match": (
                        {
                            "start_char": candidate["match_start"],
                            "end_char": candidate["match_end"],
                            "occurrence": candidate["occurrence"],
                        }
                        if request.mode == "exact"
                        else None
                    ),
                    "estimated_tokens": tokens,
                    "comparison": {
                        "whole_document_estimated_tokens": estimate_tokens(
                            next(
                                text
                                for row, text in sources
                                if row["id"] == candidate["document"]["id"]
                            )
                        ),
                        "slice_source_estimated_tokens": estimate_tokens(raw),
                    },
                }
            )
            comparison = selected[-1]["comparison"]
            whole_tokens = comparison["whole_document_estimated_tokens"]
            slice_source_tokens = comparison["slice_source_estimated_tokens"]
            comparison["saved_estimated_tokens"] = (
                whole_tokens - slice_source_tokens
            )
            comparison["token_savings_ratio"] = (
                round(
                    (whole_tokens - slice_source_tokens) / whole_tokens,
                    6,
                )
                if whole_tokens
                else 0.0
            )
        if not selected:
            return self._empty(
                request, "unavailable", ("target_exceeds_budget",)
            )
        return {
            "status": "available" if omitted == 0 else "partial",
            "complete": omitted == 0,
            "query": request.query,
            "mode": request.mode,
            "results": selected,
            "budget": {
                "estimated_tokens": {"used": used, "limit": request.max_tokens},
                "chunks": {"used": len(selected), "limit": request.max_chunks},
            },
            "omitted": {"candidates": omitted} if omitted else {},
            "warnings": [],
        }

    @staticmethod
    def _empty(
        request: DocumentContextRequest,
        status: str,
        warnings: tuple[str, ...] = (),
    ) -> dict[str, object]:
        return {
            "status": status,
            "complete": status == "not_found",
            "query": request.query,
            "mode": request.mode,
            "results": [],
            "budget": {
                "estimated_tokens": {"used": 0, "limit": request.max_tokens},
                "chunks": {"used": 0, "limit": request.max_chunks},
            },
            "omitted": {},
            "warnings": list(warnings),
        }
