from __future__ import annotations

import tempfile
import unittest
import io
from contextlib import redirect_stdout
from pathlib import Path

from acr_runtime import (
    AdaptiveRuntime,
    AttributionSignals,
    ContextCandidate,
    ContextRequest,
    EvaluatorJudgment,
)
from acr_runtime.cli import main


class ContextAttributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.runtime = AdaptiveRuntime(Path(self.directory.name) / "acr.db")

    def tearDown(self) -> None:
        self.runtime.close()
        self.directory.cleanup()

    @staticmethod
    def candidate(source_type: str, source_id: str) -> ContextCandidate:
        return ContextCandidate(
            source_type=source_type,
            source_id=source_id,
            label=f"SQLite {source_id}",
            content=f"Use SQLite evidence from {source_id}.",
            required=True,
        )

    def test_missing_explicit_citation_remains_uncertain(self):
        bundle = self.runtime.compile_context_request(
            ContextRequest(
                task="inspect SQLite schema",
                token_budget=120,
                relevant_files=(self.candidate("file", "schema.sql"),),
            )
        )
        self.runtime.complete_task(
            bundle,
            success=True,
            critic_score=0.9,
            duration_ms=1,
        )
        records = self.runtime.context_attributions(bundle.task_id)
        self.assertEqual(records[0]["outcome"], "uncertain")
        useful = self.runtime.db.connection.execute(
            "SELECT useful FROM context_uses WHERE task_id = ?",
            (bundle.task_id,),
        ).fetchone()["useful"]
        self.assertIsNone(useful)
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(
                [
                    "--db",
                    str(Path(self.directory.name) / "acr.db"),
                    "telemetry",
                    "attribution",
                    bundle.task_id,
                ]
            )
        self.assertEqual(result, 0)
        self.assertIn('"outcome": "uncertain"', output.getvalue())

    def test_four_evidence_channels_classify_and_update_utility(self):
        memory_id = self.runtime.remember(
            "semantic",
            "SQLite attribution diagnosis uses schema evidence.",
            scope="alpha",
            confidence=1,
            importance=1,
            evidence=("test",),
        )
        skill_id = self.runtime.register_skill(
            "sqlite-attribution",
            "Use SQLite attribution evidence.",
            trusted=True,
        )
        bundle = self.runtime.compile_context_request(
            ContextRequest(
                task="diagnose SQLite attribution",
                scope="alpha",
                token_budget=240,
                relevant_files=(self.candidate("file", "schema.sql"),),
                previous_observations=(
                    self.candidate("observation", "stale-output"),
                ),
            )
        )
        selected = {(item.source_type, item.source_id) for item in bundle.blocks}
        self.assertIn(("memory", memory_id), selected)
        self.assertIn(("skill", skill_id), selected)
        signals = AttributionSignals(
            model_sources=(("memory", memory_id),),
            execution_sources=(("skill", skill_id),),
            tool_dependencies=(("file", "schema.sql"),),
            ignored_sources=(("file", "schema.sql"),),
            misled_sources=(("observation", "stale-output"),),
            evaluator_judgments=(
                EvaluatorJudgment("memory", memory_id, 0.8),
                EvaluatorJudgment("observation", "stale-output", -0.9),
            ),
        )
        self.runtime.complete_task(
            bundle,
            success=True,
            critic_score=0.95,
            duration_ms=3,
            attribution_signals=signals,
        )
        records = {
            (row["source_type"], row["source_id"]): row
            for row in self.runtime.context_attributions(bundle.task_id)
        }
        self.assertEqual(records[("memory", memory_id)]["outcome"], "contributed")
        self.assertEqual(records[("skill", skill_id)]["role"], "skill_used")
        self.assertEqual(records[("file", "schema.sql")]["outcome"], "contributed")
        self.assertEqual(
            records[("observation", "stale-output")]["outcome"], "misled"
        )
        self.assertGreater(records[("memory", memory_id)]["approximate_roi"], 0)
        self.assertLess(
            records[("observation", "stale-output")]["approximate_roi"], 0
        )
        memory = self.runtime.db.memories.get(memory_id)
        self.assertEqual(memory.successful_uses, 1)
        skill = next(
            row for row in self.runtime.skills() if row["id"] == skill_id
        )
        self.assertEqual(skill["success_count"], 1)

    def test_unknown_source_signal_fails_closed(self):
        bundle = self.runtime.compile_context_request(
            ContextRequest(
                task="inspect SQLite",
                token_budget=120,
                relevant_files=(self.candidate("file", "schema.sql"),),
            )
        )
        with self.assertRaises(ValueError):
            self.runtime.complete_task(
                bundle,
                success=True,
                critic_score=1,
                duration_ms=1,
                attribution_signals=AttributionSignals(
                    model_sources=(("file", "not-selected"),)
                ),
            )


if __name__ == "__main__":
    unittest.main()
