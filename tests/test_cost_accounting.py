from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from acr_runtime import AdaptiveRuntime
from acr_runtime.autonomous_improvement import digest
from acr_runtime.cli import main
from acr_runtime.cost_accounting import LocalCostProfile, PriceRate
from acr_runtime.memory_scope import MemoryScopeKind
from acr_runtime.providers.base import ModelCallRecord
from acr_runtime.telemetry import TelemetryRecorder


START = "2026-01-01T00:00:00+00:00"
WHEN = "2026-06-01T00:00:00+00:00"
SOURCE_HASH = digest({"official_price_page": "captured_fixture"})


class CostAccountingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "acr.db"
        self.runtime = AdaptiveRuntime(self.database)

    def tearDown(self) -> None:
        self.runtime.close()
        self.directory.cleanup()

    def _rate(
        self,
        meter: str,
        price_micros: int,
        *,
        provider: str = "provider-a",
        sku: str = "model-a",
        operation: str = "chat",
        currency: str = "USD",
        service_kind: str = "model",
        start: str = START,
        end: str | None = None,
    ) -> dict[str, object]:
        return self.runtime.costs.add_rate(
            PriceRate(
                service_kind=service_kind,
                provider=provider,
                sku=sku,
                operation=operation,
                meter_kind=meter,
                currency_code=currency,
                price_micros=price_micros,
                unit_size=1_000_000 if meter != "tool_call" else 1,
                effective_from=start,
                effective_until=end,
                source_url="https://example.test/official-pricing",
                source_hash=SOURCE_HASH,
            )
        )

    def _model_rates(self, *, currency: str = "USD") -> None:
        self._rate("uncached_input_token", 1_000_000, currency=currency)
        self._rate("cache_read_token", 100_000, currency=currency)
        self._rate("cache_write_token", 1_250_000, currency=currency)
        self._rate("output_token", 2_000_000, currency=currency)

    def test_component_math_uses_fixed_point_and_distinct_cache_meters(self):
        self._model_rates()
        quote = self.runtime.costs.quote_model_upper_bound(
            provider="provider-a",
            model="model-a",
            operation="chat",
            input_tokens=1_000,
            output_tokens=100,
            occurred_at=WHEN,
        )
        self.assertEqual(quote["cost_micros"], 1_450)
        self.assertEqual(quote["currency_code"], "USD")
        event = self.runtime.costs.record_model(
            attempt_id="attempt-1",
            provider="provider-a",
            model="model-a",
            input_tokens=1_000,
            output_tokens=100,
            cache_read_tokens=100,
            cache_write_tokens=50,
            occurred_at=WHEN,
        )
        amounts = {
            row["meter_kind"]: row["amount_micros"]
            for row in event["meters"]
        }
        self.assertEqual(
            amounts,
            {
                "cache_read_token": 10,
                "cache_write_token": 63,
                "output_token": 200,
                "uncached_input_token": 850,
            },
        )
        self.assertEqual(event["total_micros"], 1_123)
        self.assertEqual(event["currency_code"], "USD")
        self.assertEqual(event["accounting_status"], "priced")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "sealed"):
            self.runtime.db.connection.execute(
                """
                INSERT INTO cost_meter_lines(
                    event_id, meter_kind, quantity, quantity_unit, rate_id,
                    amount_micros, pricing_status
                ) VALUES (?, 'tool_call', 1, 'call', NULL, 0, 'unpriced')
                """,
                (event["id"],),
            )
        forged_event_id = "forged-event"
        self.runtime.db.connection.execute(
            """
            INSERT INTO cost_events(
                id, attempt_id, source_kind, task_id, project_scope,
                provider, sku, operation, call_status, usage_quality,
                accounting_status, expected_meter_lines,
                expected_skill_allocations, currency_code,
                local_profile_id, evidence_hash, occurred_at, created_at
            ) VALUES (
                ?, 'forged-attempt', 'model', NULL, NULL,
                'provider-a', 'model-a', 'chat', 'succeeded',
                'provider_reported', 'priced', 1, 0, 'USD',
                NULL, ?, ?, ?
            )
            """,
            (forged_event_id, SOURCE_HASH, WHEN, WHEN),
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "reproducible"):
            self.runtime.db.connection.execute(
                """
                INSERT INTO cost_meter_lines(
                    event_id, meter_kind, quantity, quantity_unit, rate_id,
                    amount_micros, pricing_status
                ) VALUES (
                    ?, 'uncached_input_token', 10, 'token', ?, 999, 'priced'
                )
                """,
                (forged_event_id, event["meters"][-1]["rate_id"]),
            )
        self.runtime.db.connection.rollback()

    def test_invalid_usage_and_replayed_physical_attempt_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "cannot exceed"):
            self.runtime.costs.record_model(
                attempt_id="bad-cache",
                provider="provider-a",
                model="model-a",
                input_tokens=5,
                output_tokens=0,
                cache_read_tokens=4,
                cache_write_tokens=2,
            )
        with self.assertRaisesRegex(ValueError, "integer"):
            self.runtime.costs.record_model(
                attempt_id="bool-count",
                provider="provider-a",
                model="model-a",
                input_tokens=True,
                output_tokens=0,
            )
        self.runtime.costs.record_model(
            attempt_id="same-physical-call",
            provider="provider-a",
            model="model-a",
            input_tokens=1,
            output_tokens=1,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.costs.record_model(
                attempt_id="same-physical-call",
                provider="provider-a",
                model="model-a",
                input_tokens=1,
                output_tokens=1,
            )

    def test_rates_are_effective_dated_non_overlapping_and_immutable(self):
        old = self._rate(
            "uncached_input_token",
            1_000_000,
            end="2026-04-01T00:00:00+00:00",
        )
        new = self._rate(
            "uncached_input_token",
            2_000_000,
            start="2026-04-01T00:00:00+00:00",
        )
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self._rate(
                "uncached_input_token",
                3_000_000,
                start="2026-03-01T00:00:00+00:00",
                end="2026-05-01T00:00:00+00:00",
            )
        first = self.runtime.costs.record_model(
            attempt_id="old-call",
            provider="provider-a",
            model="model-a",
            input_tokens=10,
            output_tokens=0,
            occurred_at="2026-03-01T00:00:00+00:00",
        )
        second = self.runtime.costs.record_model(
            attempt_id="new-call",
            provider="provider-a",
            model="model-a",
            input_tokens=10,
            output_tokens=0,
            occurred_at=WHEN,
        )
        self.assertEqual(first["meters"][0]["rate_id"], old["id"])
        self.assertEqual(second["meters"][0]["rate_id"], new["id"])
        self.assertEqual(first["total_micros"], 10)
        self.assertEqual(second["total_micros"], 20)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.runtime.db.connection.execute(
                "UPDATE price_rates SET price_micros=0 WHERE id=?", (old["id"],)
            )

    def test_missing_rate_is_partial_not_zero_and_currencies_never_mix(self):
        self._rate("uncached_input_token", 1_000_000)
        partial = self.runtime.costs.record_model(
            attempt_id="partial-call",
            provider="provider-a",
            model="model-a",
            input_tokens=10,
            output_tokens=10,
            occurred_at=WHEN,
        )
        self.assertEqual(partial["accounting_status"], "partially_priced")
        self.assertEqual(partial["total_micros"], 10)
        self.assertEqual(
            {row["pricing_status"] for row in partial["meters"]},
            {"priced", "unpriced"},
        )
        self._rate("output_token", 2_000_000, currency="CAD")
        with self.assertRaisesRegex(ValueError, "mix currencies"):
            self.runtime.costs.record_model(
                attempt_id="mixed-currency",
                provider="provider-a",
                model="model-a",
                input_tokens=10,
                output_tokens=10,
                occurred_at=WHEN,
            )
        unknown = self.runtime.costs.record_model_from_adapter(
            attempt_id="unknown-usage",
            provider="provider-a",
            model="model-a",
            input_tokens=10,
            output_tokens=0,
            usage_quality="unknown",
            occurred_at=WHEN,
        )
        self.assertEqual(unknown["accounting_status"], "unpriced")
        self.assertEqual(unknown["total_micros"], 0)

    def test_telemetry_and_cost_rollback_together_on_accounting_failure(self):
        self._rate("uncached_input_token", 1_000_000, currency="USD")
        self._rate("output_token", 1_000_000, currency="CAD")
        recorder = TelemetryRecorder(self.runtime.db)
        with self.assertRaisesRegex(ValueError, "mix currencies"):
            recorder.record_model_call(
                ModelCallRecord(
                    provider="provider-a",
                    model="model-a",
                    operation="chat",
                    status="succeeded",
                    task_id=None,
                    step_id=None,
                    context_bundle_id=None,
                    input_tokens=10,
                    output_tokens=10,
                    cached_tokens=0,
                    latency_ms=1,
                    estimated_cost=0,
                    attempt_id="atomic-failure",
                )
            )
        self.assertEqual(
            self.runtime.db.connection.execute(
                """
                SELECT COUNT(*) FROM telemetry_events
                WHERE provider='provider-a' AND model='model-a'
                """
            ).fetchone()[0],
            0,
        )

    def test_reports_cost_per_task_success_project_model_and_skill(self):
        self._model_rates()
        skill_id = self.runtime.register_skill(
            "accounting-helper",
            "Apply exact fixed-point accounting.",
            trusted=True,
        )
        self.runtime.db.scopes.register(
            "project-alpha", MemoryScopeKind.PROJECT, parent_id="global"
        )
        self.runtime.db.scopes.register(
            "repo-alpha",
            MemoryScopeKind.REPOSITORY,
            parent_id="project-alpha",
        )
        successful = self.runtime.db.create_task(
            objective="Successful task", scope="repo-alpha", token_budget=20
        )
        failed = self.runtime.db.create_task(
            objective="Failed task", scope="repo-alpha", token_budget=20
        )
        for task_id in (successful, failed):
            self.runtime.db.record_context(
                task_id,
                (
                    {
                        "source_type": "skill",
                        "source_id": skill_id,
                        "tokens": 5,
                        "utility": 1,
                        "roi": 0.2,
                        "compression_strategy": "none",
                        "original_tokens": 5,
                        "exact_preserved": 1,
                    },
                ),
                5,
            )
        self.runtime.db.connection.execute(
            "UPDATE tasks SET status='succeeded' WHERE id=?", (successful,)
        )
        self.runtime.db.connection.execute(
            "UPDATE tasks SET status='failed' WHERE id=?", (failed,)
        )
        self.runtime.db.connection.commit()
        for index, task_id in enumerate((successful, failed), start=1):
            self.runtime.costs.record_model(
                attempt_id=f"task-call-{index}",
                provider="provider-a",
                model="model-a",
                input_tokens=100,
                output_tokens=0,
                task_id=task_id,
                occurred_at=WHEN,
                call_status="succeeded" if index == 1 else "failed",
                skill_ids=(skill_id,),
            )
        self.runtime.costs.record_model(
            attempt_id="unallocated-call",
            provider="provider-a",
            model="model-a",
            input_tokens=100,
            output_tokens=0,
            occurred_at=WHEN,
        )
        report = self.runtime.costs.report()
        usd = report["currencies"][0]
        self.assertEqual(usd["total_micros"], 300)
        self.assertEqual(usd["unallocated_micros"], 100)
        self.assertEqual(usd["cost_per_task_micros"], 100)
        self.assertEqual(usd["cost_per_success_micros"], 200)
        self.assertEqual(report["by_project"][0]["project"], "project-alpha")
        self.assertEqual(report["by_model"][0]["sku"], "model-a")
        self.assertEqual(report["by_skill"][0]["allocated_micros"], 200)
        self.assertEqual(
            report["by_skill"][0]["interpretation"],
            "allocation_view_not_additional_spend",
        )

    def test_local_estimation_is_disabled_by_default_and_separate_when_enabled(self):
        disabled = self.runtime.costs.record_local(
            attempt_id="local-disabled",
            provider="ollama",
            model="local-a",
            duration_ms=3_600_000,
            occurred_at=WHEN,
        )
        self.assertEqual(disabled["accounting_status"], "local_disabled")
        self.assertIsNone(disabled["currency_code"])
        self.assertEqual(disabled["total_micros"], 0)

        self.runtime.costs.add_local_profile(
            LocalCostProfile(
                provider="ollama",
                sku="local-b",
                currency_code="CAD",
                enabled=True,
                power_milliwatts=100_000,
                electricity_micros_per_kwh=200_000,
                hardware_micros_per_hour=500_000,
                effective_from=START,
                evidence_hash=SOURCE_HASH,
            )
        )
        estimated = self.runtime.costs.record_local(
            attempt_id="local-enabled",
            provider="ollama",
            model="local-b",
            duration_ms=3_600_000,
            occurred_at=WHEN,
        )
        self.assertEqual(estimated["accounting_status"], "local_estimate")
        self.assertIsNotNone(estimated["local_profile_id"])
        self.assertEqual(estimated["currency_code"], "CAD")
        self.assertEqual(estimated["total_micros"], 520_000)
        self.assertEqual(
            {row["pricing_status"] for row in estimated["meters"]},
            {"local_estimate"},
        )

    def test_cli_imports_rates_records_usage_and_reports(self):
        self.runtime.close()
        rate_file = Path(self.directory.name) / "rate.json"
        usage_file = Path(self.directory.name) / "usage.json"
        rate_file.write_text(
            json.dumps(
                {
                    "service_kind": "tool",
                    "provider": "search-vendor",
                    "sku": "web-search",
                    "operation": "call",
                    "meter_kind": "tool_call",
                    "currency_code": "USD",
                    "price_micros": 2_000,
                    "unit_size": 1,
                    "effective_from": START,
                    "source_url": "https://example.test/pricing",
                    "source_hash": SOURCE_HASH,
                }
            ),
            encoding="utf-8",
        )
        usage_file.write_text(
            json.dumps(
                {
                    "attempt_id": "tool-attempt",
                    "provider": "search-vendor",
                    "tool": "web-search",
                    "calls": 2,
                    "occurred_at": WHEN,
                }
            ),
            encoding="utf-8",
        )
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                main([
                    "--db", str(self.database), "cost", "rate-add",
                    str(rate_file),
                ]),
                0,
            )
            self.assertEqual(
                main([
                    "--db", str(self.database), "cost", "record-tool",
                    str(usage_file),
                ]),
                0,
            )
            self.assertEqual(
                main(["--db", str(self.database), "cost", "report"]),
                0,
            )
        self.assertIn('"by_tool"', output.getvalue())
        self.assertIn('"total_micros": 4000', output.getvalue())
        self.runtime = AdaptiveRuntime(self.database)


if __name__ == "__main__":
    unittest.main()
