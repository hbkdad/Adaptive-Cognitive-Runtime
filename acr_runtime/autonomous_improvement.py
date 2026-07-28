from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol

from .retrieval import RetrievalConfig, RetrievalWeights
from .skill_router import SkillRouterConfig


POLICY_TARGETS = frozenset(
    {
        "retrieval_weights",
        "context_thresholds",
        "skill_routing_thresholds",
    }
)
ALLOWED_TARGETS = POLICY_TARGETS | {"skill_instructions"}
RETRIEVAL_KEYS = tuple(RetrievalWeights().__dict__)
ROUTING_KEYS = ("minimum_benefit_bps", "overlap_threshold_bps")
CONTEXT_KEYS = ("minimum_optional_utility_bps",)
DEFAULT_CONFIGS: dict[str, dict[str, int]] = {
    "retrieval_weights": {
        f"{key}_bps": round(value * 10_000)
        for key, value in RetrievalWeights().as_dict().items()
    },
    "context_thresholds": {"minimum_optional_utility_bps": 500},
    "skill_routing_thresholds": {
        "minimum_benefit_bps": 800,
        "overlap_threshold_bps": 6_000,
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def digest(value: object) -> str:
    encoded = value if isinstance(value, str) else canonical_json(value)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def scope_hash(scope: str) -> str:
    if not scope.strip():
        raise ValueError("scope cannot be empty")
    return digest({"scope": scope.strip()})


def _strict_bps(
    target: str,
    config: Mapping[str, object],
    *,
    incumbent: Mapping[str, int] | None = None,
) -> dict[str, int]:
    if target not in POLICY_TARGETS:
        raise ValueError(f"Unsupported improvement policy target: {target}")
    expected = {
        "retrieval_weights": tuple(f"{key}_bps" for key in RETRIEVAL_KEYS),
        "context_thresholds": CONTEXT_KEYS,
        "skill_routing_thresholds": ROUTING_KEYS,
    }[target]
    if set(config) != set(expected):
        raise ValueError(f"{target} requires exactly: {', '.join(expected)}")
    normalized: dict[str, int] = {}
    for key in expected:
        value = config[key]
        if type(value) is not int or not 0 <= value <= 10_000:
            raise ValueError(f"{key} must be an integer from 0 to 10000")
        normalized[key] = value
    if target == "retrieval_weights" and sum(normalized.values()) != 10_000:
        raise ValueError("retrieval weight basis points must sum to 10000")
    if incumbent is not None:
        maximum_delta = 500 if target == "retrieval_weights" else 1_000
        if any(
            abs(normalized[key] - incumbent[key]) > maximum_delta
            for key in expected
        ):
            raise ValueError("candidate exceeds the bounded per-field delta")
        if normalized == dict(incumbent):
            raise ValueError("candidate must differ from the incumbent")
    return normalized


@dataclass(frozen=True)
class PolicyVersion:
    id: str
    target: str
    version: int
    parent_id: str | None
    config: dict[str, int]
    config_hash: str


@dataclass(frozen=True)
class BenchmarkEvidence:
    case_count: int
    complete: bool
    hard_violations: int
    incumbent_utility_micros: int
    candidate_utility_micros: int
    protected_regressions: int = 0
    summary: Mapping[str, object] | None = None

    def validated(self, *, maximum_cases: int) -> "BenchmarkEvidence":
        if type(self.case_count) is not int or not 1 <= self.case_count <= maximum_cases:
            raise ValueError("benchmark case_count is outside its authorization")
        if type(self.complete) is not bool:
            raise ValueError("benchmark complete must be boolean")
        integers = (
            self.hard_violations,
            self.incumbent_utility_micros,
            self.candidate_utility_micros,
            self.protected_regressions,
        )
        if any(type(value) is not int for value in integers):
            raise ValueError("benchmark metrics must be fixed-point integers")
        if self.hard_violations < 0 or self.protected_regressions < 0:
            raise ValueError("benchmark violation counts cannot be negative")
        summary = dict(self.summary or {})
        if any(
            not isinstance(value, (str, int, bool, type(None)))
            or isinstance(value, float) and not math.isfinite(value)
            for value in summary.values()
        ):
            raise ValueError("benchmark summary must be scalar and finite")
        return self


class ControlledBenchmarkAdapter(Protocol):
    def identity(self) -> Mapping[str, object]: ...

    def run_paired(
        self,
        *,
        target: str,
        incumbent: Mapping[str, int],
        candidate: Mapping[str, int],
        seed: int,
        maximum_cases: int,
    ) -> BenchmarkEvidence: ...


class ImprovementPolicyRegistry:
    """Immutable safe-policy versions with compare-and-swap heads."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def bootstrap(self) -> None:
        for target, config in DEFAULT_CONFIGS.items():
            if self.connection.execute(
                "SELECT 1 FROM improvement_policy_heads WHERE target = ?",
                (target,),
            ).fetchone():
                continue
            normalized = _strict_bps(target, config)
            version_id = str(uuid.uuid4())
            config_hash = digest(normalized)
            timestamp = _now()
            with self.connection:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO improvement_policy_versions (
                        id, target, version, parent_id, config_json, config_hash,
                        provenance_json, created_at
                    ) VALUES (?, ?, 1, NULL, ?, ?, ?, ?)
                    """,
                    (
                        version_id,
                        target,
                        canonical_json(normalized),
                        config_hash,
                        canonical_json({"source": "runtime_defaults"}),
                        timestamp,
                    ),
                )
                row = self.connection.execute(
                    """
                    SELECT id FROM improvement_policy_versions
                    WHERE target = ? AND config_hash = ?
                    """,
                    (target, config_hash),
                ).fetchone()
                actual_id = str(row[0])
                inserted = self.connection.execute(
                    """
                    INSERT OR IGNORE INTO improvement_policy_heads (
                        target, version_id, revision, updated_at
                    ) VALUES (?, ?, 1, ?)
                    """,
                    (target, actual_id, timestamp),
                ).rowcount
                if inserted:
                    self._event(
                        target, "bootstrap", None, None, actual_id, config_hash
                    )

    def active(self, target: str) -> PolicyVersion:
        if target not in POLICY_TARGETS:
            raise ValueError(f"Unsupported improvement policy target: {target}")
        row = self.connection.execute(
            """
            SELECT v.* FROM improvement_policy_heads AS h
            JOIN improvement_policy_versions AS v ON v.id = h.version_id
            WHERE h.target = ?
            """,
            (target,),
        ).fetchone()
        if row is None:
            raise RuntimeError("improvement policy registry is not bootstrapped")
        return self._version(row)

    def create_candidate(
        self, target: str, config: Mapping[str, object], *, parent_id: str
    ) -> PolicyVersion:
        incumbent = self.active(target)
        if incumbent.id != parent_id:
            raise RuntimeError("stale improvement incumbent")
        normalized = _strict_bps(target, config, incumbent=incumbent.config)
        config_hash = digest(normalized)
        row = self.connection.execute(
            """
            SELECT * FROM improvement_policy_versions
            WHERE target = ? AND config_hash = ?
            """,
            (target, config_hash),
        ).fetchone()
        if row is not None:
            return self._version(row)
        version_id = str(uuid.uuid4())
        version = int(
            self.connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 "
                "FROM improvement_policy_versions WHERE target = ?",
                (target,),
            ).fetchone()[0]
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO improvement_policy_versions (
                    id, target, version, parent_id, config_json, config_hash,
                    provenance_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    target,
                    version,
                    parent_id,
                    canonical_json(normalized),
                    config_hash,
                    canonical_json({"source": "prompt67_candidate"}),
                    _now(),
                ),
            )
            self._event(
                target, "candidate", None, parent_id, version_id, config_hash
            )
        return self.active_version(version_id)

    def promote(
        self,
        target: str,
        candidate_id: str,
        *,
        expected_head_id: str,
        run_id: str | None = None,
    ) -> None:
        candidate = self.active_version(candidate_id)
        if candidate.target != target or candidate.parent_id != expected_head_id:
            raise ValueError("candidate is not a child of the expected incumbent")
        timestamp = _now()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            changed = self.connection.execute(
                """
                UPDATE improvement_policy_heads
                SET version_id = ?, revision = revision + 1, updated_at = ?
                WHERE target = ? AND version_id = ?
                """,
                (candidate_id, timestamp, target, expected_head_id),
            ).rowcount
            if changed != 1:
                raise RuntimeError("stale improvement promotion")
            self._event(
                target,
                "promote",
                run_id,
                expected_head_id,
                candidate_id,
                candidate.config_hash,
            )
            if run_id is not None:
                updated = self.connection.execute(
                    """
                    UPDATE improvement_runs
                    SET status = 'promoted',
                        decision_reason = 'all_conjunctive_gates_passed',
                        completed_at = ?
                    WHERE id = ? AND status = 'observed'
                    """,
                    (_now(), run_id),
                ).rowcount
                if updated != 1:
                    raise RuntimeError("improvement run is not promotable")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def rollback(self, target: str, *, expected_head_id: str) -> PolicyVersion:
        current = self.active(target)
        if current.id != expected_head_id:
            raise RuntimeError("stale improvement rollback")
        if current.parent_id is None:
            raise ValueError("bootstrap policy has no rollback target")
        parent = self.active_version(current.parent_id)
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            changed = self.connection.execute(
                """
                UPDATE improvement_policy_heads
                SET version_id = ?, revision = revision + 1, updated_at = ?
                WHERE target = ? AND version_id = ?
                """,
                (parent.id, _now(), target, current.id),
            ).rowcount
            if changed != 1:
                raise RuntimeError("stale improvement rollback")
            self._event(
                target, "rollback", None, current.id, parent.id, parent.config_hash
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return parent

    def active_version(self, version_id: str) -> PolicyVersion:
        row = self.connection.execute(
            "SELECT * FROM improvement_policy_versions WHERE id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown improvement policy version: {version_id}")
        return self._version(row)

    def retrieval_config(self) -> RetrievalConfig:
        config = self.active("retrieval_weights").config
        return RetrievalConfig(
            weights=RetrievalWeights(
                **{key: config[f"{key}_bps"] / 10_000 for key in RETRIEVAL_KEYS}
            )
        )

    def context_threshold(self) -> float:
        return (
            self.active("context_thresholds")
            .config["minimum_optional_utility_bps"]
            / 10_000
        )

    def routing_config(self) -> SkillRouterConfig:
        config = self.active("skill_routing_thresholds").config
        return SkillRouterConfig(
            minimum_benefit=config["minimum_benefit_bps"] / 10_000,
            overlap_threshold=config["overlap_threshold_bps"] / 10_000,
        )

    def attribute_task(self, task_id: str) -> None:
        with self.connection:
            for target in sorted(POLICY_TARGETS):
                version = self.active(target)
                self.connection.execute(
                    """
                    INSERT OR REPLACE INTO task_policy_attributions (
                        task_id, target, version_id, config_hash
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (task_id, target, version.id, version.config_hash),
                )

    def status(self) -> dict[str, object]:
        return {
            "targets": {
                target: {
                    "version_id": (version := self.active(target)).id,
                    "version": version.version,
                    "config_hash": version.config_hash,
                    "config": version.config,
                }
                for target in sorted(POLICY_TARGETS)
            }
        }

    def _event(
        self,
        target: str,
        event_type: str,
        run_id: str | None,
        from_id: str | None,
        to_id: str | None,
        evidence_hash: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO improvement_policy_events (
                id, target, event_type, run_id, from_version_id,
                to_version_id, evidence_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                target,
                event_type,
                run_id,
                from_id,
                to_id,
                evidence_hash,
                _now(),
            ),
        )

    @staticmethod
    def _version(row: sqlite3.Row) -> PolicyVersion:
        return PolicyVersion(
            id=str(row["id"]),
            target=str(row["target"]),
            version=int(row["version"]),
            parent_id=row["parent_id"],
            config=json.loads(row["config_json"]),
            config_hash=str(row["config_hash"]),
        )


class AutonomousImprovementLoop:
    """One preauthorized candidate, one sealed paired evaluation, one decision."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        registry: ImprovementPolicyRegistry,
        *,
        minimum_attributed_tasks: int = 30,
        minimum_cases: int = 30,
        minimum_improvement_micros: int = 1_000,
    ) -> None:
        self.connection = connection
        self.registry = registry
        self.minimum_attributed_tasks = minimum_attributed_tasks
        self.minimum_cases = minimum_cases
        self.minimum_improvement_micros = minimum_improvement_micros

    def authorize(
        self,
        *,
        target: str,
        scope: str,
        candidate: Mapping[str, object],
        benchmark_identity: Mapping[str, object],
        max_cases: int,
        expires_at: str,
    ) -> str:
        if target not in POLICY_TARGETS:
            raise ValueError("only numeric safe-policy targets can be authorized")
        incumbent = self.registry.active(target)
        normalized = _strict_bps(target, candidate, incumbent=incumbent.config)
        if type(max_cases) is not int or not 1 <= max_cases <= 10_000:
            raise ValueError("max_cases must be 1..10000")
        if datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= datetime.now(
            timezone.utc
        ):
            raise ValueError("authorization must expire in the future")
        authorization_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO improvement_authorizations (
                    id, target, scope_hash, incumbent_hash, candidate_hash,
                    benchmark_hash, max_cases, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    authorization_id,
                    target,
                    scope_hash(scope),
                    incumbent.config_hash,
                    digest(normalized),
                    digest(dict(benchmark_identity)),
                    max_cases,
                    expires_at,
                    _now(),
                ),
            )
        return authorization_id

    def readiness(self, target: str, *, scope: str = "global") -> dict[str, object]:
        if target == "skill_instructions":
            return {
                "ready": False,
                "target": target,
                "reasons": ["governed_skill_promotion_adapter_unavailable"],
            }
        incumbent = self.registry.active(target)
        attributed = int(
            self.connection.execute(
                """
                SELECT COUNT(*) FROM task_policy_attributions AS a
                JOIN tasks AS t ON t.id = a.task_id
                WHERE a.target = ? AND a.version_id = ?
                  AND a.config_hash = ? AND t.scope = ?
                  AND t.status = 'succeeded'
                """,
                (target, incumbent.id, incumbent.config_hash, scope),
            ).fetchone()[0]
        )
        reasons = []
        if attributed < self.minimum_attributed_tasks:
            reasons.append(
                f"insufficient_attributed_telemetry:{attributed}/"
                f"{self.minimum_attributed_tasks}"
            )
        return {
            "ready": not reasons,
            "target": target,
            "scope_hash": scope_hash(scope),
            "incumbent_version_id": incumbent.id,
            "incumbent_hash": incumbent.config_hash,
            "attributed_successful_tasks": attributed,
            "required_tasks": self.minimum_attributed_tasks,
            "reasons": reasons,
        }

    def run(
        self,
        *,
        target: str,
        scope: str,
        hypothesis: str,
        candidate: Mapping[str, object],
        authorization_id: str,
        benchmark: ControlledBenchmarkAdapter,
        seed: int,
    ) -> dict[str, object]:
        if target not in POLICY_TARGETS:
            raise ValueError("target is not eligible for numeric auto-promotion")
        if not hypothesis.strip():
            raise ValueError("hypothesis cannot be empty")
        if type(seed) is not int:
            raise ValueError("seed must be an integer")
        readiness = self.readiness(target, scope=scope)
        if not readiness["ready"]:
            raise RuntimeError(";".join(readiness["reasons"]))
        incumbent = self.registry.active(target)
        normalized = _strict_bps(target, candidate, incumbent=incumbent.config)
        benchmark_identity = dict(benchmark.identity())
        authorization = self._authorization(
            authorization_id,
            target=target,
            scope=scope,
            incumbent_hash=incumbent.config_hash,
            candidate_hash=digest(normalized),
            benchmark_hash=digest(benchmark_identity),
        )
        run_id = str(uuid.uuid4())
        candidate_version = self.registry.create_candidate(
            target, normalized, parent_id=incumbent.id
        )
        timestamp = _now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO improvement_runs (
                    id, target, scope_hash, incumbent_version_id,
                    candidate_version_id, authorization_id, hypothesis_hash,
                    benchmark_hash, seed, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'observed', ?)
                """,
                (
                    run_id,
                    target,
                    scope_hash(scope),
                    incumbent.id,
                    candidate_version.id,
                    authorization_id,
                    digest(hypothesis.strip()),
                    digest(benchmark_identity),
                    seed,
                    timestamp,
                ),
            )
            consumed = self.connection.execute(
                """
                UPDATE improvement_authorizations SET consumed_at = ?
                WHERE id = ? AND consumed_at IS NULL
                """,
                (_now(), authorization_id),
            ).rowcount
            if consumed != 1:
                raise RuntimeError("improvement authorization was already consumed")
        try:
            evidence = benchmark.run_paired(
                target=target,
                incumbent=incumbent.config,
                candidate=candidate_version.config,
                seed=seed,
                maximum_cases=int(authorization["max_cases"]),
            ).validated(maximum_cases=int(authorization["max_cases"]))
        except Exception:
            blocked_hash = digest(
                {
                    "run_id": run_id,
                    "reason": "controlled_benchmark_failed",
                }
            )
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE improvement_runs
                    SET status = 'blocked',
                        decision_reason = 'controlled_benchmark_failed',
                        completed_at = ?
                    WHERE id = ? AND status = 'observed'
                    """,
                    (_now(), run_id),
                )
                self.registry._event(
                    target,
                    "blocked",
                    run_id,
                    incumbent.id,
                    candidate_version.id,
                    blocked_hash,
                )
            raise
        summary = dict(evidence.summary or {})
        result_payload = {
            "case_count": evidence.case_count,
            "complete": evidence.complete,
            "hard_violations": evidence.hard_violations,
            "incumbent_utility_micros": evidence.incumbent_utility_micros,
            "candidate_utility_micros": evidence.candidate_utility_micros,
            "protected_regressions": evidence.protected_regressions,
            "summary": summary,
        }
        result_hash = digest(result_payload)
        reasons = self._rejection_reasons(evidence)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO improvement_benchmark_results (
                    id, run_id, case_count, complete, hard_violations,
                    incumbent_utility_micros, candidate_utility_micros,
                    protected_regressions, result_hash, summary_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    run_id,
                    evidence.case_count,
                    int(evidence.complete),
                    evidence.hard_violations,
                    evidence.incumbent_utility_micros,
                    evidence.candidate_utility_micros,
                    evidence.protected_regressions,
                    result_hash,
                    canonical_json(summary),
                    _now(),
                ),
            )
            self.registry._event(
                target,
                "benchmark",
                run_id,
                incumbent.id,
                candidate_version.id,
                result_hash,
            )
        if reasons:
            decision = "rejected"
            reason = ",".join(reasons)
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE improvement_runs
                    SET status = 'rejected', decision_reason = ?, completed_at = ?
                    WHERE id = ? AND status = 'observed'
                    """,
                    (reason, _now(), run_id),
                )
                self.registry._event(
                    target,
                    "reject",
                    run_id,
                    incumbent.id,
                    candidate_version.id,
                    result_hash,
                )
        else:
            self.registry.promote(
                target,
                candidate_version.id,
                expected_head_id=incumbent.id,
                run_id=run_id,
            )
            decision = "promoted"
            reason = "all_conjunctive_gates_passed"
        return {
            "run_id": run_id,
            "target": target,
            "decision": decision,
            "reason": reason,
            "incumbent_version_id": incumbent.id,
            "candidate_version_id": candidate_version.id,
            "result_hash": result_hash,
        }

    def report(self, run_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM improvement_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown improvement run: {run_id}")
        result = self.connection.execute(
            """
            SELECT case_count, complete, hard_violations,
                   incumbent_utility_micros, candidate_utility_micros,
                   protected_regressions, result_hash, summary_json
            FROM improvement_benchmark_results WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        payload = dict(row)
        if result is not None:
            payload["benchmark"] = {
                **dict(result),
                "complete": bool(result["complete"]),
                "summary": json.loads(result["summary_json"]),
            }
            del payload["benchmark"]["summary_json"]
        return payload

    def _authorization(
        self,
        authorization_id: str,
        *,
        target: str,
        scope: str,
        incumbent_hash: str,
        candidate_hash: str,
        benchmark_hash: str,
    ) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM improvement_authorizations WHERE id = ?",
            (authorization_id,),
        ).fetchone()
        if row is None:
            raise LookupError("unknown improvement authorization")
        expected = (
            row["target"] == target
            and row["scope_hash"] == scope_hash(scope)
            and row["incumbent_hash"] == incumbent_hash
            and row["candidate_hash"] == candidate_hash
            and row["benchmark_hash"] == benchmark_hash
            and row["consumed_at"] is None
        )
        expires = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
        if not expected or expires <= datetime.now(timezone.utc):
            raise RuntimeError("improvement authorization does not match or expired")
        return row

    def _rejection_reasons(self, evidence: BenchmarkEvidence) -> list[str]:
        reasons = []
        if not evidence.complete:
            reasons.append("incomplete_benchmark")
        if evidence.case_count < self.minimum_cases:
            reasons.append("insufficient_cases")
        if evidence.hard_violations:
            reasons.append("hard_violation")
        if evidence.protected_regressions:
            reasons.append("protected_regression")
        if (
            evidence.candidate_utility_micros
            - evidence.incumbent_utility_micros
            < self.minimum_improvement_micros
        ):
            reasons.append("insufficient_practical_improvement")
        return reasons
