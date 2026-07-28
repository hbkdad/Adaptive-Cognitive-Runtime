from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol, Sequence

from .autonomous_improvement import canonical_json, digest


ORDERING_PROFILES = frozenset({"production", "utility_desc", "roi_desc"})
PRODUCTION_CONTEXT_STRATEGY = {
    "ordering_profile": "production",
    "compression_minimum_tokens": 80,
    "max_memories": 24,
    "max_skills": 4,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ContextStrategy:
    ordering_profile: str = "production"
    compression_minimum_tokens: int = 80
    max_memories: int = 24
    max_skills: int = 4

    def __post_init__(self) -> None:
        if self.ordering_profile not in ORDERING_PROFILES:
            raise ValueError("unknown context ordering profile")
        for name, value, minimum, maximum in (
            (
                "compression_minimum_tokens",
                self.compression_minimum_tokens,
                40,
                200,
            ),
            ("max_memories", self.max_memories, 4, 32),
            ("max_skills", self.max_skills, 1, 4),
        ):
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(
                    f"{name} must be an integer from {minimum} to {maximum}"
                )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ContextStrategy":
        if set(value) != set(PRODUCTION_CONTEXT_STRATEGY):
            raise ValueError(
                "context strategy requires exactly ordering_profile, "
                "compression_minimum_tokens, max_memories, max_skills"
            )
        return cls(**dict(value))

    def as_dict(self) -> dict[str, object]:
        return {
            "ordering_profile": self.ordering_profile,
            "compression_minimum_tokens": self.compression_minimum_tokens,
            "max_memories": self.max_memories,
            "max_skills": self.max_skills,
        }


@dataclass(frozen=True)
class MetaContextCaseEvidence:
    case_id: str
    incumbent_quality_micros: int
    candidate_quality_micros: int
    incumbent_tokens: int
    candidate_tokens: int
    hard_violations: int = 0
    protected_regression: bool = False
    authority_invariant: bool = True
    provenance_invariant: bool = True

    def validate(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id cannot be empty")
        integer_fields = (
            self.incumbent_quality_micros,
            self.candidate_quality_micros,
            self.incumbent_tokens,
            self.candidate_tokens,
            self.hard_violations,
        )
        if any(type(value) is not int for value in integer_fields):
            raise ValueError("meta-context case metrics must be integers")
        if (
            self.incumbent_tokens < 0
            or self.candidate_tokens < 0
            or self.hard_violations < 0
        ):
            raise ValueError("meta-context counts cannot be negative")
        if any(
            type(value) is not bool
            for value in (
                self.protected_regression,
                self.authority_invariant,
                self.provenance_invariant,
            )
        ):
            raise ValueError("meta-context invariants must be boolean")


class SealedContextHarness(Protocol):
    def identity(self) -> Mapping[str, object]: ...

    def run_paired(
        self,
        *,
        incumbent: ContextStrategy,
        candidate: ContextStrategy,
        seed: int,
    ) -> Sequence[MetaContextCaseEvidence]: ...


class MetaContextEngine:
    """Experimental context strategies; evaluation never mutates production."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        minimum_cases: int = 30,
        minimum_improvement_micros: int = 1_000,
    ) -> None:
        self.connection = connection
        self.minimum_cases = minimum_cases
        self.minimum_improvement_micros = minimum_improvement_micros

    @property
    def production(self) -> ContextStrategy:
        return ContextStrategy.from_dict(PRODUCTION_CONTEXT_STRATEGY)

    @property
    def production_hash(self) -> str:
        return digest(self.production.as_dict())

    def propose(
        self, config: Mapping[str, object], *, hypothesis: str
    ) -> dict[str, object]:
        if not hypothesis.strip():
            raise ValueError("hypothesis cannot be empty")
        strategy = ContextStrategy.from_dict(config)
        if strategy == self.production:
            raise ValueError("candidate must differ from production")
        if (
            abs(
                strategy.compression_minimum_tokens
                - self.production.compression_minimum_tokens
            )
            > 40
            or abs(strategy.max_memories - self.production.max_memories) > 8
            or abs(strategy.max_skills - self.production.max_skills) > 1
        ):
            raise ValueError("candidate exceeds bounded strategy deltas")
        config_hash = digest(strategy.as_dict())
        existing = self.connection.execute(
            "SELECT * FROM meta_context_strategies WHERE config_hash = ?",
            (config_hash,),
        ).fetchone()
        if existing is not None:
            return self._strategy_row(existing)
        version = int(
            self.connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 "
                "FROM meta_context_strategies"
            ).fetchone()[0]
        )
        strategy_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO meta_context_strategies (
                    id, version, parent_hash, config_json, config_hash,
                    hypothesis_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy_id,
                    version,
                    self.production_hash,
                    canonical_json(strategy.as_dict()),
                    config_hash,
                    digest(hypothesis.strip()),
                    _now(),
                ),
            )
            self._event(
                strategy_id, None, "candidate", config_hash
            )
        return self.get(strategy_id)

    def evaluate(
        self,
        strategy_id: str,
        *,
        harness: SealedContextHarness,
        seed: int,
    ) -> dict[str, object]:
        if type(seed) is not int:
            raise ValueError("seed must be an integer")
        row = self.connection.execute(
            "SELECT * FROM meta_context_strategies WHERE id = ?",
            (strategy_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown meta-context strategy: {strategy_id}")
        if row["status"] != "candidate":
            raise RuntimeError("meta-context candidate was already evaluated")
        candidate = ContextStrategy.from_dict(json.loads(row["config_json"]))
        identity = self._harness_identity(harness.identity())
        run_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO meta_context_runs (
                    id, strategy_id, production_hash, dataset_hash, harness_hash,
                    seed, expected_cases, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)
                """,
                (
                    run_id,
                    strategy_id,
                    self.production_hash,
                    identity["dataset_hash"],
                    identity["harness_hash"],
                    seed,
                    identity["expected_cases"],
                    _now(),
                ),
            )
        try:
            cases = tuple(
                harness.run_paired(
                    incumbent=self.production,
                    candidate=candidate,
                    seed=seed,
                )
            )
            self._validate_cases(cases, identity["expected_cases"])
        except Exception:
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE meta_context_runs
                    SET status = 'blocked',
                        decision_reason = 'sealed_harness_failed',
                        completed_at = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (_now(), run_id),
                )
                self.connection.execute(
                    """
                    UPDATE meta_context_strategies SET status = 'evaluated'
                    WHERE id = ? AND status = 'candidate'
                    """,
                    (strategy_id,),
                )
                self._event(
                    strategy_id,
                    run_id,
                    "blocked",
                    digest({"run_id": run_id, "reason": "sealed_harness_failed"}),
                )
            raise
        total_incumbent = sum(x.incumbent_quality_micros for x in cases)
        total_candidate = sum(x.candidate_quality_micros for x in cases)
        incumbent_tokens = sum(x.incumbent_tokens for x in cases)
        candidate_tokens = sum(x.candidate_tokens for x in cases)
        reasons: list[str] = []
        if len(cases) < self.minimum_cases:
            reasons.append("insufficient_cases")
        if any(x.hard_violations for x in cases):
            reasons.append("hard_violation")
        if any(x.protected_regression for x in cases):
            reasons.append("protected_regression")
        if any(not x.authority_invariant for x in cases):
            reasons.append("authority_changed")
        if any(not x.provenance_invariant for x in cases):
            reasons.append("provenance_changed")
        if (
            total_candidate - total_incumbent
            < self.minimum_improvement_micros * len(cases)
        ):
            reasons.append("insufficient_practical_improvement")
        if candidate_tokens > incumbent_tokens:
            reasons.append("token_regression")
        status = "rejected" if reasons else "promotion_eligible"
        reason = ",".join(reasons) if reasons else "all_offline_gates_passed"
        timestamp = _now()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.executemany(
                """
                INSERT INTO meta_context_case_results (
                    id, run_id, case_hash, incumbent_quality_micros,
                    candidate_quality_micros, incumbent_tokens, candidate_tokens,
                    hard_violations, protected_regression, authority_invariant,
                    provenance_invariant, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        str(uuid.uuid4()),
                        run_id,
                        digest({"case_id": case.case_id}),
                        case.incumbent_quality_micros,
                        case.candidate_quality_micros,
                        case.incumbent_tokens,
                        case.candidate_tokens,
                        case.hard_violations,
                        int(case.protected_regression),
                        int(case.authority_invariant),
                        int(case.provenance_invariant),
                        timestamp,
                    )
                    for case in cases
                ),
            )
            self.connection.execute(
                """
                UPDATE meta_context_runs
                SET status = ?, decision_reason = ?, completed_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (status, reason, timestamp, run_id),
            )
            self.connection.execute(
                """
                UPDATE meta_context_strategies SET status = ?
                WHERE id = ? AND status = 'candidate'
                """,
                (status, strategy_id),
            )
            evidence_hash = digest(
                {
                    "run_id": run_id,
                    "cases": len(cases),
                    "incumbent_quality": total_incumbent,
                    "candidate_quality": total_candidate,
                    "incumbent_tokens": incumbent_tokens,
                    "candidate_tokens": candidate_tokens,
                    "reason": reason,
                }
            )
            self._event(
                strategy_id,
                run_id,
                "reject" if reasons else "eligible",
                evidence_hash,
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.report(run_id)

    def readiness(self) -> dict[str, object]:
        return {
            "experimental_layer_ready": True,
            "production_activation_ready": False,
            "production_hash": self.production_hash,
            "minimum_cases": self.minimum_cases,
            "blocked_dimensions": {
                "file_selection": "governed_file_selection_adapter_unavailable",
                "production_activation": (
                    "shadow_canary_and_operator_authorization_unavailable"
                ),
            },
        }

    def get(self, strategy_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM meta_context_strategies WHERE id = ?",
            (strategy_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown meta-context strategy: {strategy_id}")
        return self._strategy_row(row)

    def report(self, run_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM meta_context_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown meta-context run: {run_id}")
        aggregate = self.connection.execute(
            """
            SELECT COUNT(*) AS cases,
                   SUM(incumbent_quality_micros) AS incumbent_quality_micros,
                   SUM(candidate_quality_micros) AS candidate_quality_micros,
                   SUM(incumbent_tokens) AS incumbent_tokens,
                   SUM(candidate_tokens) AS candidate_tokens,
                   SUM(hard_violations) AS hard_violations,
                   SUM(protected_regression) AS protected_regressions
            FROM meta_context_case_results WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        return {
            **dict(row),
            "evidence": {
                key: int(value or 0) for key, value in dict(aggregate).items()
            },
            "production_changed": False,
        }

    @staticmethod
    def _harness_identity(identity: Mapping[str, object]) -> dict[str, object]:
        expected = {"dataset_hash", "harness_hash", "expected_cases"}
        if set(identity) != expected:
            raise ValueError("sealed harness identity is incomplete")
        dataset_hash = identity["dataset_hash"]
        harness_hash = identity["harness_hash"]
        expected_cases = identity["expected_cases"]
        if (
            not isinstance(dataset_hash, str)
            or len(dataset_hash) != 64
            or not isinstance(harness_hash, str)
            or len(harness_hash) != 64
            or type(expected_cases) is not int
            or not 1 <= expected_cases <= 10_000
        ):
            raise ValueError("sealed harness identity is invalid")
        return dict(identity)

    @staticmethod
    def _validate_cases(
        cases: Sequence[MetaContextCaseEvidence], expected_cases: object
    ) -> None:
        if len(cases) != expected_cases:
            raise ValueError("sealed harness returned incomplete cases")
        seen: set[str] = set()
        for case in cases:
            if not isinstance(case, MetaContextCaseEvidence):
                raise ValueError("sealed harness returned an invalid case")
            case.validate()
            case_hash = digest({"case_id": case.case_id})
            if case_hash in seen:
                raise ValueError("sealed harness returned duplicate cases")
            seen.add(case_hash)

    def _event(
        self,
        strategy_id: str,
        run_id: str | None,
        event_type: str,
        evidence_hash: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO meta_context_events (
                id, strategy_id, run_id, event_type, evidence_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                strategy_id,
                run_id,
                event_type,
                evidence_hash,
                _now(),
            ),
        )

    @staticmethod
    def _strategy_row(row: sqlite3.Row) -> dict[str, object]:
        return {
            "id": row["id"],
            "version": row["version"],
            "parent_hash": row["parent_hash"],
            "config": json.loads(row["config_json"]),
            "config_hash": row["config_hash"],
            "hypothesis_hash": row["hypothesis_hash"],
            "status": row["status"],
            "created_at": row["created_at"],
        }
