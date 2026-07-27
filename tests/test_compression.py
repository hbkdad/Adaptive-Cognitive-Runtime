from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from acr_runtime import (
    AdaptiveRuntime,
    CompressionStrategy,
    ContextCandidate,
    ContextCompressor,
    ContextRequest,
)


class ContextCompressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compressor = ContextCompressor(minimum_tokens=12)

    @staticmethod
    def candidate(content: str, **values: object) -> ContextCandidate:
        return ContextCandidate(
            source_type="file",
            source_id=str(values.pop("source_id", "source")),
            label="source",
            content=content,
            **values,
        )

    def test_protected_exactness_classes_are_never_summarized(self):
        cases = (
            self.candidate("git push origin main", content_kind="command"),
            self.candidate("ValueError: exact diagnostic", content_kind="error"),
            self.candidate("The parties shall agree.", content_kind="legal"),
            self.candidate("sha256:" + "a" * 64, content_kind="cryptographic"),
            self.candidate("critical = left if flag else right", content_kind="code"),
        )
        for candidate in cases:
            with self.subTest(kind=candidate.content_kind):
                result = self.compressor.compress(candidate, "unrelated task")
                self.assertEqual(result.content, candidate.content)
                self.assertEqual(
                    result.strategy, CompressionStrategy.EXACT_PROTECTED
                )
                self.assertTrue(result.exact_preserved)

    def test_python_ast_extracts_requested_symbol_without_rewriting_it(self):
        content = """import sqlite3

def wanted(value):
    return value * 2 + 1

def unrelated():
    return "large unrelated implementation"
"""
        result = self.compressor.compress(
            self.candidate(
                content,
                content_kind="python",
                symbols=("wanted",),
            ),
            "inspect wanted",
        )
        self.assertEqual(result.strategy, CompressionStrategy.PYTHON_AST)
        self.assertIn("return value * 2 + 1", result.content)
        self.assertIn("import sqlite3", result.content)
        self.assertNotIn("def unrelated", result.content)

    def test_structured_reference_conversation_and_exact_extraction(self):
        structured = self.compressor.compress(
            self.candidate(
                '{\n  "b": 2,\n  "a": 1\n}', content_kind="structured"
            ),
            "inspect json",
        )
        self.assertEqual(structured.content, '{"a":1,"b":2}')
        reference = self.compressor.compress(
            self.candidate(
                "large artifact body " * 30,
                artifact_uri="repo://docs/architecture.md",
            ),
            "architecture",
        )
        self.assertEqual(reference.strategy, CompressionStrategy.REFERENCE)
        self.assertNotIn("large artifact body", reference.content)

        conversation = "\n\n".join(
            (
                "user: unrelated greeting " * 8,
                "assistant: unrelated response " * 8,
                "user: diagnose SQLite migration",
            )
        )
        distilled = self.compressor.compress(
            self.candidate(conversation, content_kind="conversation"),
            "diagnose SQLite",
        )
        self.assertEqual(
            distilled.strategy, CompressionStrategy.CONVERSATION
        )
        self.assertIn("diagnose SQLite migration", distilled.content)
        self.assertNotIn("unrelated greeting", distilled.content)

    def test_compiler_persists_measured_compression(self):
        with tempfile.TemporaryDirectory() as directory:
            with AdaptiveRuntime(Path(directory) / "acr.db") as runtime:
                content = "\n\n".join(
                    (
                        "SQLite schema evidence is focused and useful. " * 4,
                        "Unrelated marketing copy should not be selected. " * 8,
                    )
                )
                bundle = runtime.compile_context_request(
                    ContextRequest(
                        task="inspect SQLite schema evidence",
                        token_budget=160,
                        relevant_files=(
                            self.candidate(content, source_id="schema-notes"),
                        ),
                    )
                )
                block = next(
                    item for item in bundle.blocks
                    if item.source_id == "schema-notes"
                )
                self.assertLess(block.tokens, block.original_tokens)
                rows = runtime.telemetry_compression()
                self.assertGreater(rows[0]["tokens_saved"], 0)


if __name__ == "__main__":
    unittest.main()
