from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from enum import Enum

from .models import ContextCandidate
from .scoring import estimate_tokens, query_terms


class CompressionStrategy(str, Enum):
    NONE = "none"
    EXACT_PROTECTED = "exact_protected"
    REFERENCE = "reference_replacement"
    STRUCTURED = "structured_compaction"
    EXACT_EXTRACTION = "exact_extraction"
    PYTHON_AST = "python_ast_symbols"
    CODE_SYMBOLS = "code_symbol_extraction"
    CONVERSATION = "conversation_distillation"
    DEDUPLICATION = "deduplication"


@dataclass(frozen=True)
class CompressionResult:
    content: str
    strategy: CompressionStrategy
    original_tokens: int
    compressed_tokens: int
    exact_preserved: bool
    artifact_uri: str | None = None


class ContextCompressor:
    HASH = re.compile(r"\b(?:[A-Fa-f0-9]{32,}|sha(?:256|512):\S+)\b")
    ERROR = re.compile(
        r"(?im)^(?:traceback \(most recent call last\):|.*(?:error|exception):)"
    )
    LEGAL = re.compile(
        r"\b(agreement|indemnif|hereby|whereas|shall|governing law)\b",
        re.IGNORECASE,
    )
    COMMAND = re.compile(
        r"(?m)^\s*(?:PS [^>]+>|[$>])\s+\S+|"
        r"\b(?:python|npm|git|docker|kubectl|powershell)\s+[-\w]",
        re.IGNORECASE,
    )

    PROTECTED_KINDS = frozenset(
        {"command", "error", "legal", "cryptographic"}
    )

    def __init__(self, *, minimum_tokens: int = 80) -> None:
        if minimum_tokens < 1:
            raise ValueError("minimum_tokens must be positive")
        self.minimum_tokens = minimum_tokens

    def compress(
        self, candidate: ContextCandidate, task: str
    ) -> CompressionResult:
        content = candidate.content.strip()
        original = estimate_tokens(content)
        if self._must_preserve(candidate, content):
            return self._result(
                content, CompressionStrategy.EXACT_PROTECTED, original, True
            )
        if candidate.artifact_uri:
            reference = f"[Accessible artifact: {candidate.artifact_uri}]"
            if estimate_tokens(reference) < original:
                return self._result(
                    reference,
                    CompressionStrategy.REFERENCE,
                    original,
                    True,
                    candidate.artifact_uri,
                )
        if candidate.content_kind == "structured":
            compacted = self._compact_json(content)
            if compacted != content:
                return self._result(
                    compacted, CompressionStrategy.STRUCTURED, original, True
                )
        if candidate.content_kind == "python" and candidate.symbols:
            extracted = self._python_symbols(content, candidate.symbols)
            if extracted and estimate_tokens(extracted) < original:
                return self._result(
                    extracted, CompressionStrategy.PYTHON_AST, original, True
                )
        if candidate.content_kind == "code" and candidate.symbols:
            extracted = self._code_symbols(content, candidate.symbols)
            if extracted and estimate_tokens(extracted) < original:
                return self._result(
                    extracted, CompressionStrategy.CODE_SYMBOLS, original, True
                )
        if original < self.minimum_tokens:
            return self._result(
                content, CompressionStrategy.NONE, original, True
            )
        if candidate.content_kind == "conversation":
            distilled = self._conversation(content, task)
            if estimate_tokens(distilled) < original:
                return self._result(
                    distilled, CompressionStrategy.CONVERSATION, original, True
                )
        deduplicated = self._deduplicate(content)
        if estimate_tokens(deduplicated) < original:
            content = deduplicated
            original_after_dedupe = estimate_tokens(content)
            if original_after_dedupe < self.minimum_tokens:
                return self._result(
                    content, CompressionStrategy.DEDUPLICATION, original, True
                )
        extracted = self._exact_paragraphs(content, task)
        if extracted and estimate_tokens(extracted) < estimate_tokens(content):
            return self._result(
                extracted, CompressionStrategy.EXACT_EXTRACTION, original, True
            )
        strategy = (
            CompressionStrategy.DEDUPLICATION
            if content != candidate.content.strip()
            else CompressionStrategy.NONE
        )
        return self._result(content, strategy, original, True)

    def _must_preserve(
        self, candidate: ContextCandidate, content: str
    ) -> bool:
        if (
            candidate.required
            or candidate.exact_required
            or candidate.content_kind in self.PROTECTED_KINDS
        ):
            return True
        if candidate.content_kind in {"python", "code"} and not candidate.symbols:
            return True
        return bool(
            self.HASH.search(content)
            or self.ERROR.search(content)
            or self.LEGAL.search(content)
            or self.COMMAND.search(content)
        )

    @staticmethod
    def _compact_json(content: str) -> str:
        try:
            value = json.loads(content)
        except json.JSONDecodeError:
            return content
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _python_symbols(content: str, symbols: tuple[str, ...]) -> str | None:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return None
        wanted = set(symbols)
        segments: list[str] = []
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                segment = ast.get_source_segment(content, node)
                if segment:
                    segments.append(segment)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name in wanted:
                    segment = ast.get_source_segment(content, node)
                    if segment:
                        segments.append(segment)
        return "\n\n".join(segments) or None

    @staticmethod
    def _code_symbols(content: str, symbols: tuple[str, ...]) -> str | None:
        lines = content.splitlines()
        matches = [
            line for line in lines if any(symbol in line for symbol in symbols)
        ]
        return "\n".join(matches) or None

    @staticmethod
    def _conversation(content: str, task: str) -> str:
        turns = re.split(r"(?m)(?=^(?:user|assistant|system|tool)\s*:)", content)
        terms = query_terms(task)
        selected = [
            turn.strip()
            for turn in turns
            if turn.strip()
            and any(term in turn.casefold() for term in terms)
        ]
        if turns and turns[-1].strip() not in selected:
            selected.append(turns[-1].strip())
        return "\n\n".join(dict.fromkeys(selected))

    @staticmethod
    def _deduplicate(content: str) -> str:
        paragraphs = re.split(r"\n\s*\n", content)
        return "\n\n".join(dict.fromkeys(item.strip() for item in paragraphs if item.strip()))

    @staticmethod
    def _exact_paragraphs(content: str, task: str) -> str | None:
        terms = query_terms(task)
        paragraphs = re.split(r"\n\s*\n", content)
        selected = [
            item.strip()
            for item in paragraphs
            if any(term in item.casefold() for term in terms)
        ]
        return "\n\n".join(selected) or None

    @staticmethod
    def _result(
        content: str,
        strategy: CompressionStrategy,
        original_tokens: int,
        exact_preserved: bool,
        artifact_uri: str | None = None,
    ) -> CompressionResult:
        return CompressionResult(
            content=content,
            strategy=strategy,
            original_tokens=original_tokens,
            compressed_tokens=estimate_tokens(content),
            exact_preserved=exact_preserved,
            artifact_uri=artifact_uri,
        )
