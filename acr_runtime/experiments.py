from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from dataclasses import dataclass
from statistics import fmean
from typing import Literal

from .memory import utc_now
from .secret_management import assert_secret_free

ExperimentDomain = Literal[
    "retrieval_algorithm",
    "context_budget",
    "skill_version",
    "model_router",
    "planner_strategy",
]
PrimaryMetric = Literal[
    "quality", "tokens", "cost", "latency_ms", "failure_rate"
]
IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{1,63}")
DOMAINS = {
    "retrieval_algorithm", "context_budget", "skill_version",
    "model_router", "planner_strategy",
}
METRICS = {"quality", "tokens", "cost", "latency_ms", "failure_rate"}
METRIC_NAMES = ("quality", "tokens", "cost", "latency_ms", "failure_rate")


@dataclass(frozen=True)
class ExperimentVariant:
    id: str
    allocation: int
    config: dict[str, object]
    baseline: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not IDENTIFIER.fullmatch(self.id):
            raise ValueError("Variant ID must be a bounded slug")
        if not 1 <= self.allocation <= 9_999:
            raise ValueError("Variant allocation must be between 1 and 9999")
        if not isinstance(self.config, dict) or not self.config:
            raise ValueError("Variant config must be a non-empty object")
        serialized = json.dumps(
            self.config, sort_keys=True, separators=(",", ":")
        )
        if len(serialized) > 32_000:
            raise ValueError("Variant config exceeds 32 KB")
        assert_secret_free(serialized, "experiment variant config")

    @classmethod
    def from_dict(cls, payload: object) -> "ExperimentVariant":
        fields = {"id", "allocation", "config", "baseline"}
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError(f"Variant must contain {sorted(fields)}")
        if (
            not isinstance(payload["id"], str)
            or type(payload["allocation"]) is not int
            or not isinstance(payload["config"], dict)
            or type(payload["baseline"]) is not bool
        ):
            raise ValueError("Variant field types are invalid")
        return cls(**payload)

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "allocation": self.allocation,
            "config": self.config,
            "baseline": self.baseline,
        }


@dataclass(frozen=True)
class ExperimentCreate:
    name: str
    domain: ExperimentDomain
    hypothesis: str
    randomization_unit: str
    seed: int
    variants: tuple[ExperimentVariant, ...]
    primary_metric: PrimaryMetric

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not IDENTIFIER.fullmatch(self.name):
            raise ValueError("Experiment name must be a bounded slug")
        if not isinstance(self.domain, str) or self.domain not in DOMAINS:
            raise ValueError("Unsupported experiment domain")
        if (
            not isinstance(self.hypothesis, str)
            or not self.hypothesis.strip()
            or len(self.hypothesis) > 2_000
            or not isinstance(self.randomization_unit, str)
            or not self.randomization_unit.strip()
            or len(self.randomization_unit) > 128
        ):
            raise ValueError("Experiment requires a bounded hypothesis and unit")
        assert_secret_free(self.hypothesis, "experiment hypothesis")
        if type(self.seed) is not int or not 0 <= self.seed <= 2_147_483_647:
            raise ValueError("Experiment seed is invalid")
        if (
            not isinstance(self.primary_metric, str)
            or self.primary_metric not in METRICS
        ):
            raise ValueError("Unsupported primary metric")
        if not 2 <= len(self.variants) <= 10:
            raise ValueError("Experiments require 2..10 variants")
        if len({variant.id for variant in self.variants}) != len(self.variants):
            raise ValueError("Variant IDs must be unique")
        if sum(variant.allocation for variant in self.variants) != 10_000:
            raise ValueError("Variant allocation must total 10000")
        if sum(variant.baseline for variant in self.variants) != 1:
            raise ValueError("Exactly one variant must be the baseline")

    @classmethod
    def from_dict(cls, payload: object) -> "ExperimentCreate":
        fields = {
            "name", "domain", "hypothesis", "randomization_unit", "seed",
            "variants", "primary_metric",
        }
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError(f"Experiment must contain {sorted(fields)}")
        if not isinstance(payload["variants"], list):
            raise ValueError("variants must be a list")
        return cls(
            name=payload["name"],
            domain=payload["domain"],
            hypothesis=payload["hypothesis"],
            randomization_unit=payload["randomization_unit"],
            seed=payload["seed"],
            variants=tuple(
                ExperimentVariant.from_dict(item)
                for item in payload["variants"]
            ),
            primary_metric=payload["primary_metric"],
        )


@dataclass(frozen=True)
class ExperimentOutcome:
    assignment_id: str
    quality: float
    tokens: int
    cost: float
    latency_ms: int
    failed: bool
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.quality, bool)
            or not isinstance(self.quality, (int, float))
            or not 0 <= self.quality <= 1
        ):
            raise ValueError("quality must be between 0 and 1")
        if (
            type(self.tokens) is not int or self.tokens < 0
            or isinstance(self.cost, bool)
            or not isinstance(self.cost, (int, float))
            or self.cost < 0
            or type(self.latency_ms) is not int or self.latency_ms < 0
            or type(self.failed) is not bool
        ):
            raise ValueError("Outcome metrics are invalid")
        if not 1 <= len(self.evidence) <= 64 or any(
            not item.strip() or len(item) > 512 for item in self.evidence
        ):
            raise ValueError("Outcome requires bounded evidence")
        for item in self.evidence:
            assert_secret_free(item, "experiment evidence")

    @classmethod
    def from_dict(cls, payload: object) -> "ExperimentOutcome":
        fields = {
            "assignment_id", "quality", "tokens", "cost", "latency_ms",
            "failed", "evidence",
        }
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError(f"Outcome must contain {sorted(fields)}")
        if not isinstance(payload["evidence"], list):
            raise ValueError("evidence must be a list")
        return cls(
            assignment_id=payload["assignment_id"],
            quality=payload["quality"],
            tokens=payload["tokens"],
            cost=payload["cost"],
            latency_ms=payload["latency_ms"],
            failed=payload["failed"],
            evidence=tuple(payload["evidence"]),
        )


class ExperimentController:
    """Reproducible, opt-in strategy experiments with no production mutation."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @staticmethod
    def _variants(row: sqlite3.Row) -> tuple[ExperimentVariant, ...]:
        return tuple(
            ExperimentVariant.from_dict(item)
            for item in json.loads(row["variants_json"])
        )

    def create(self, request: ExperimentCreate) -> dict[str, object]:
        experiment_id = str(uuid.uuid4())
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO runtime_experiments (
                    id, name, domain, hypothesis, randomization_unit, seed,
                    variants_json, primary_metric, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)
                """,
                (
                    experiment_id, request.name, request.domain,
                    request.hypothesis, request.randomization_unit,
                    request.seed,
                    json.dumps([item.as_dict() for item in request.variants],
                               sort_keys=True),
                    request.primary_metric, now,
                ),
            )
        return self.get(experiment_id)

    def get(self, experiment_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM runtime_experiments WHERE id=?",
            (experiment_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown experiment: {experiment_id}")
        payload = dict(row)
        payload["variants"] = json.loads(payload.pop("variants_json"))
        payload["production_default_changed"] = False
        return payload

    def start(self, experiment_id: str) -> dict[str, object]:
        experiment = self.get(experiment_id)
        if experiment["status"] != "draft":
            raise ValueError("Only draft experiments can start")
        with self.connection:
            self.connection.execute(
                """
                UPDATE runtime_experiments
                SET status='running', started_at=? WHERE id=?
                """,
                (utc_now(), experiment_id),
            )
        return self.get(experiment_id)

    def finish(
        self, experiment_id: str, *, cancelled: bool = False
    ) -> dict[str, object]:
        experiment = self.get(experiment_id)
        if experiment["status"] != "running":
            raise ValueError("Only running experiments can finish")
        with self.connection:
            self.connection.execute(
                """
                UPDATE runtime_experiments
                SET status=?, completed_at=? WHERE id=?
                """,
                (
                    "cancelled" if cancelled else "completed",
                    utc_now(), experiment_id,
                ),
            )
        return self.get(experiment_id)

    def assign(self, experiment_id: str, unit_id: str) -> dict[str, object]:
        if not unit_id.strip() or len(unit_id) > 512:
            raise ValueError("Randomization unit ID must be bounded")
        assert_secret_free(unit_id, "experiment unit")
        row = self.connection.execute(
            "SELECT * FROM runtime_experiments WHERE id=?",
            (experiment_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown experiment: {experiment_id}")
        if row["status"] != "running":
            raise ValueError("Assignments require a running experiment")
        unit_hash = hashlib.sha256(
            f"{experiment_id}:{row['seed']}:{unit_id}".encode("utf-8")
        ).hexdigest()
        existing = self.connection.execute(
            """
            SELECT * FROM experiment_assignments
            WHERE experiment_id=? AND unit_hash=?
            """,
            (experiment_id, unit_hash),
        ).fetchone()
        variants = self._variants(row)
        if existing is None:
            bucket = int.from_bytes(
                hashlib.sha256(
                    f"bucket:{experiment_id}:{row['seed']}:{unit_id}".encode(
                        "utf-8"
                    )
                ).digest()[:8],
                "big",
            ) % 10_000
            boundary = 0
            selected = variants[-1]
            for variant in variants:
                boundary += variant.allocation
                if bucket < boundary:
                    selected = variant
                    break
            assignment_id = str(uuid.uuid4())
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO experiment_assignments (
                        id, experiment_id, unit_hash, variant_id, bucket,
                        assigned_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        assignment_id, experiment_id, unit_hash, selected.id,
                        bucket, utc_now(),
                    ),
                )
            existing = self.connection.execute(
                "SELECT * FROM experiment_assignments WHERE id=?",
                (assignment_id,),
            ).fetchone()
        selected = next(
            variant for variant in variants
            if variant.id == existing["variant_id"]
        )
        return {
            "id": existing["id"],
            "experiment_id": experiment_id,
            "variant_id": selected.id,
            "config": selected.config,
            "bucket": existing["bucket"],
            "unit_hash": unit_hash,
            "production_default_changed": False,
        }

    def record(self, experiment_id: str, outcome: ExperimentOutcome) -> str:
        experiment = self.get(experiment_id)
        if experiment["status"] not in ("running", "completed"):
            raise ValueError("Outcomes require a running or completed experiment")
        assignment = self.connection.execute(
            "SELECT * FROM experiment_assignments WHERE id=?",
            (outcome.assignment_id,),
        ).fetchone()
        if assignment is None or assignment["experiment_id"] != experiment_id:
            raise ValueError("Outcome assignment does not belong to experiment")
        outcome_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO experiment_outcomes (
                    id, experiment_id, assignment_id, quality, tokens, cost,
                    latency_ms, failed, evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outcome_id, experiment_id, outcome.assignment_id,
                    outcome.quality, outcome.tokens, outcome.cost,
                    outcome.latency_ms, int(outcome.failed),
                    json.dumps(outcome.evidence), utc_now(),
                ),
            )
        return outcome_id

    def report(self, experiment_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM runtime_experiments WHERE id=?",
            (experiment_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown experiment: {experiment_id}")
        variants = self._variants(row)
        assignments = self.connection.execute(
            """
            SELECT variant_id, COUNT(*) AS count
            FROM experiment_assignments WHERE experiment_id=?
            GROUP BY variant_id
            """,
            (experiment_id,),
        ).fetchall()
        assigned = {item["variant_id"]: item["count"] for item in assignments}
        outcomes = self.connection.execute(
            """
            SELECT a.variant_id, o.* FROM experiment_outcomes o
            JOIN experiment_assignments a ON a.id=o.assignment_id
            WHERE o.experiment_id=?
            ORDER BY o.created_at, o.id
            """,
            (experiment_id,),
        ).fetchall()
        by_variant: dict[str, list[sqlite3.Row]] = {
            variant.id: [] for variant in variants
        }
        for outcome in outcomes:
            by_variant[outcome["variant_id"]].append(outcome)
        total_assigned = sum(assigned.values())
        summary: dict[str, dict[str, object]] = {}
        allocation_warning = False
        for variant in variants:
            rows = by_variant[variant.id]
            observed_share = (
                assigned.get(variant.id, 0) / total_assigned
                if total_assigned else 0.0
            )
            expected_share = variant.allocation / 10_000
            if total_assigned:
                sigma = math.sqrt(
                    expected_share * (1 - expected_share) / total_assigned
                )
                if abs(observed_share - expected_share) > max(0.05, 3 * sigma):
                    allocation_warning = True
            summary[variant.id] = {
                "baseline": variant.baseline,
                "expected_share": expected_share,
                "observed_share": observed_share,
                "assignments": assigned.get(variant.id, 0),
                "outcomes": len(rows),
                "quality": fmean(item["quality"] for item in rows) if rows else None,
                "tokens": fmean(item["tokens"] for item in rows) if rows else None,
                "cost": fmean(item["cost"] for item in rows) if rows else None,
                "latency_ms": (
                    fmean(item["latency_ms"] for item in rows) if rows else None
                ),
                "failure_rate": (
                    fmean(item["failed"] for item in rows) if rows else None
                ),
            }
        baseline = next(item for item in variants if item.baseline)
        baseline_metrics = summary[baseline.id]
        for variant in variants:
            metrics = summary[variant.id]
            metrics["delta_vs_baseline"] = {
                metric: (
                    None
                    if metrics[metric] is None or baseline_metrics[metric] is None
                    else metrics[metric] - baseline_metrics[metric]
                )
                for metric in METRIC_NAMES
            }
        return {
            "experiment": self.get(experiment_id),
            "variants": summary,
            "allocation_warning": allocation_warning,
            "interpretation": (
                "descriptive_only_replicate_before_production_decision"
            ),
            "production_default_changed": False,
        }
