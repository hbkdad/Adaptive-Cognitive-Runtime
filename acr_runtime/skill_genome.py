from __future__ import annotations

import json
import math
import sqlite3
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable, Protocol

from .memory import utc_now
from .skill_evolution import EvolutionMetrics
from .skill_format import SkillPackageLoader
from .skill_registry import SkillRegistry


VERIFICATION_LEVELS = ("minimal", "standard", "strict")
MODEL_TIERS = ("economy", "standard", "capable", "premium")


@dataclass(frozen=True)
class ConfidenceThresholds:
    retrieval: float
    verification: float
    acceptance: float

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} confidence threshold must be numeric")
            if not 0 <= value <= 1:
                raise ValueError(f"{name} confidence threshold must be 0..1")


@dataclass(frozen=True)
class GenomeParameters:
    retrieval_depth: int
    context_budget: int
    maximum_iterations: int
    verification_intensity: str
    model_tier: str
    parallelism: int
    confidence_thresholds: ConfidenceThresholds

    def __post_init__(self) -> None:
        for name in (
            "retrieval_depth",
            "context_budget",
            "maximum_iterations",
            "parallelism",
        ):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be an integer")
        if not 1 <= self.retrieval_depth <= 50:
            raise ValueError("retrieval_depth must be 1..50")
        if not 128 <= self.context_budget <= 32_768:
            raise ValueError("context_budget must be 128..32768")
        if not 1 <= self.maximum_iterations <= 20:
            raise ValueError("maximum_iterations must be 1..20")
        if self.verification_intensity not in VERIFICATION_LEVELS:
            raise ValueError("invalid verification_intensity")
        if self.model_tier not in MODEL_TIERS:
            raise ValueError("invalid model_tier")
        if not 1 <= self.parallelism <= 8:
            raise ValueError("parallelism must be 1..8")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "GenomeParameters":
        expected = {
            "retrieval_depth",
            "context_budget",
            "maximum_iterations",
            "verification_intensity",
            "model_tier",
            "parallelism",
            "confidence_thresholds",
        }
        if set(payload) != expected:
            raise ValueError("Genome parameters must contain exactly seven fields")
        thresholds = payload["confidence_thresholds"]
        if not isinstance(thresholds, dict) or set(thresholds) != {
            "retrieval", "verification", "acceptance"
        }:
            raise ValueError("confidence_thresholds must contain three fields")
        return cls(
            retrieval_depth=payload["retrieval_depth"],
            context_budget=payload["context_budget"],
            maximum_iterations=payload["maximum_iterations"],
            verification_intensity=payload["verification_intensity"],
            model_tier=payload["model_tier"],
            parallelism=payload["parallelism"],
            confidence_thresholds=ConfidenceThresholds(
                retrieval=thresholds["retrieval"],
                verification=thresholds["verification"],
                acceptance=thresholds["acceptance"],
            ),
        )


@dataclass(frozen=True)
class GenomeMutation:
    retrieval_depth: int | None = None
    context_budget: int | None = None
    maximum_iterations: int | None = None
    verification_intensity: str | None = None
    model_tier: str | None = None
    parallelism: int | None = None
    confidence_thresholds: ConfidenceThresholds | None = None

    def __post_init__(self) -> None:
        changes = sum(value is not None for value in asdict(self).values())
        if not 1 <= changes <= 3:
            raise ValueError("A controlled mutation must change 1..3 parameters")
        for name in (
            "retrieval_depth",
            "context_budget",
            "maximum_iterations",
            "parallelism",
        ):
            value = getattr(self, name)
            if value is not None and type(value) is not int:
                raise ValueError(f"{name} mutation must be an integer")
        if (
            self.verification_intensity is not None
            and not isinstance(self.verification_intensity, str)
        ):
            raise ValueError("verification_intensity mutation must be text")
        if self.model_tier is not None and not isinstance(self.model_tier, str):
            raise ValueError("model_tier mutation must be text")

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "GenomeMutation":
        allowed = {
            "retrieval_depth",
            "context_budget",
            "maximum_iterations",
            "verification_intensity",
            "model_tier",
            "parallelism",
            "confidence_thresholds",
        }
        if not payload or not set(payload) <= allowed:
            raise ValueError("Mutation contains unknown or no parameters")
        values = dict(payload)
        thresholds = values.get("confidence_thresholds")
        if thresholds is not None:
            if not isinstance(thresholds, dict) or set(thresholds) != {
                "retrieval", "verification", "acceptance"
            }:
                raise ValueError(
                    "confidence_thresholds must contain three fields"
                )
            values["confidence_thresholds"] = ConfidenceThresholds(
                retrieval=thresholds["retrieval"],
                verification=thresholds["verification"],
                acceptance=thresholds["acceptance"],
            )
        return cls(**values)


@dataclass(frozen=True)
class SkillGenome:
    id: str
    source_skill_id: str
    source_hash: str
    parent_genome_id: str | None
    generation: int
    status: str
    parameters: GenomeParameters
    mutation: GenomeMutation | None
    created_at: str
    selected_at: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "parameters": self.parameters.as_dict(),
            "mutation": asdict(self.mutation) if self.mutation else None,
        }


@dataclass(frozen=True)
class GenomeCasePair:
    case_id: str
    baseline: EvolutionMetrics
    candidate: EvolutionMetrics

    def __post_init__(self) -> None:
        if not self.case_id.strip() or len(self.case_id) > 128:
            raise ValueError("case_id must be 1..128 characters")

    def as_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "baseline": asdict(self.baseline),
            "candidate": asdict(self.candidate),
        }


@dataclass(frozen=True)
class GenomeBenchmarkEvidence:
    isolated: bool
    isolation_details: dict[str, object]
    results: dict[str, tuple[GenomeCasePair, ...]]
    blocked_reason: str | None = None


class GenomeBenchmarkAdapter(Protocol):
    def run(
        self,
        source_skill: dict[str, object],
        baseline: SkillGenome,
        candidates: tuple[SkillGenome, ...],
    ) -> GenomeBenchmarkEvidence: ...


class UnavailableGenomeBenchmark:
    def run(
        self,
        source_skill: dict[str, object],
        baseline: SkillGenome,
        candidates: tuple[SkillGenome, ...],
    ) -> GenomeBenchmarkEvidence:
        return GenomeBenchmarkEvidence(
            isolated=False,
            isolation_details={"adapter": "unavailable"},
            results={},
            blocked_reason="isolated_genome_benchmark_unavailable",
        )


@dataclass(frozen=True)
class GenomeTournamentCandidate:
    candidate_genome_id: str
    observations: tuple[dict[str, object], ...]
    statistics: dict[str, object]
    qualified: bool
    tournament_rank: int | None
    rejection_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class GenomeTournament:
    id: str
    baseline_genome_id: str
    status: str
    adapter: str
    isolation: dict[str, object]
    policy: dict[str, object]
    winner_genome_id: str | None
    blocked_reason: str | None
    candidates: tuple[GenomeTournamentCandidate, ...]
    created_at: str
    completed_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "candidates": [candidate.as_dict() for candidate in self.candidates],
        }


class SkillGenomeExperiment:
    """Isolated genome search that cannot modify production skill behavior."""

    POLICY = {
        "name": "paired_genome_tournament_v1",
        "alpha": 0.05,
        "minimum_cases": 10,
        "maximum_cases": 50,
        "maximum_candidates": 8,
        "minimum_quality_effect": 0.02,
        "significance_test": "one_sided_paired_sign_test",
        "multiplicity_correction": "holm_bonferroni",
        "resource_policy": "mean_pareto_no_regression",
        "production_application": False,
    }

    def __init__(
        self,
        connection: sqlite3.Connection,
        registry: SkillRegistry,
        *,
        benchmark: GenomeBenchmarkAdapter | None = None,
        loader: SkillPackageLoader | None = None,
        mutation_guard: Callable[[str], None] | None = None,
    ) -> None:
        self.connection = connection
        self.registry = registry
        self.benchmark = benchmark or UnavailableGenomeBenchmark()
        self.loader = loader or SkillPackageLoader()
        self.mutation_guard = mutation_guard

    def _verified_source(self, reference: str) -> dict[str, object]:
        source = self.registry.inspect(reference)
        if source["lifecycle_status"] != "active":
            raise ValueError("Genome experiments require an active validated skill")
        path = source.get("package_path")
        if not path:
            raise ValueError("Genome experiments require a Skill Format package")
        package = self.loader.load(Path(str(path)))
        if package.content_hash != source["content_hash"]:
            raise ValueError("Source package changed after validation")
        return source

    def create_baseline(
        self, source_reference: str, parameters: GenomeParameters
    ) -> SkillGenome:
        source = self._verified_source(source_reference)
        genome_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO skill_genomes(
                    id, source_skill_id, source_hash, parent_genome_id,
                    generation, status, parameters_json, mutation_json,
                    created_at
                ) VALUES (?, ?, ?, NULL, 0, 'baseline', ?, NULL, ?)
                """,
                (
                    genome_id,
                    source["id"],
                    source["content_hash"],
                    json.dumps(parameters.as_dict(), sort_keys=True),
                    utc_now(),
                ),
            )
        return self.load_genome(genome_id)

    @staticmethod
    def _enum_delta(value: str, prior: str, choices: tuple[str, ...]) -> int:
        if value not in choices:
            raise ValueError(f"Invalid controlled value: {value}")
        return abs(choices.index(value) - choices.index(prior))

    def _mutated_parameters(
        self, prior: GenomeParameters, mutation: GenomeMutation
    ) -> GenomeParameters:
        numeric_limits = {
            "retrieval_depth": 10,
            "context_budget": 4_096,
            "maximum_iterations": 4,
            "parallelism": 2,
        }
        for field, maximum_delta in numeric_limits.items():
            value = getattr(mutation, field)
            if value is not None and abs(value - getattr(prior, field)) > maximum_delta:
                raise ValueError(f"{field} mutation exceeds controlled delta")
        if (
            mutation.verification_intensity is not None
            and self._enum_delta(
                mutation.verification_intensity,
                prior.verification_intensity,
                VERIFICATION_LEVELS,
            )
            > 1
        ):
            raise ValueError("verification_intensity mutation skips a tier")
        if (
            mutation.model_tier is not None
            and self._enum_delta(
                mutation.model_tier, prior.model_tier, MODEL_TIERS
            )
            > 1
        ):
            raise ValueError("model_tier mutation skips a tier")
        if mutation.confidence_thresholds is not None:
            for field in ("retrieval", "verification", "acceptance"):
                if (
                    abs(
                        getattr(mutation.confidence_thresholds, field)
                        - getattr(prior.confidence_thresholds, field)
                    )
                    > 0.1000001
                ):
                    raise ValueError(
                        "confidence threshold mutation exceeds 0.1"
                    )
        changes = {
            field: value
            for field, value in asdict(mutation).items()
            if value is not None
        }
        if mutation.confidence_thresholds is not None:
            changes["confidence_thresholds"] = mutation.confidence_thresholds
        candidate = replace(prior, **changes)
        if candidate == prior:
            raise ValueError("Mutation must change at least one value")
        return candidate

    def mutate(
        self, parent_genome_id: str, mutation: GenomeMutation
    ) -> SkillGenome:
        if self.mutation_guard is not None:
            self.mutation_guard("skill_mutation")
        parent = self.load_genome(parent_genome_id)
        if parent.status not in {"baseline", "selected"}:
            raise ValueError("Only a baseline or selected genome can mutate")
        source = self._verified_source(parent.source_skill_id)
        if source["content_hash"] != parent.source_hash:
            raise ValueError("Genome source hash no longer matches the skill")
        parameters = self._mutated_parameters(parent.parameters, mutation)
        genome_id = str(uuid.uuid4())
        mutation_payload = asdict(mutation)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO skill_genomes(
                    id, source_skill_id, source_hash, parent_genome_id,
                    generation, status, parameters_json, mutation_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, 'experimental', ?, ?, ?)
                """,
                (
                    genome_id,
                    parent.source_skill_id,
                    parent.source_hash,
                    parent.id,
                    parent.generation + 1,
                    json.dumps(parameters.as_dict(), sort_keys=True),
                    json.dumps(mutation_payload, sort_keys=True),
                    utc_now(),
                ),
            )
        return self.load_genome(genome_id)

    @staticmethod
    def _sign_p_value(differences: list[float]) -> tuple[float, int, int]:
        wins = sum(value > 1e-12 for value in differences)
        losses = sum(value < -1e-12 for value in differences)
        trials = wins + losses
        if trials == 0:
            return 1.0, wins, losses
        tail = sum(math.comb(trials, count) for count in range(wins, trials + 1))
        return tail / (2**trials), wins, losses

    def _candidate_statistics(
        self, pairs: tuple[GenomeCasePair, ...]
    ) -> tuple[dict[str, object], list[str]]:
        reasons: list[str] = []
        minimum = int(self.POLICY["minimum_cases"])
        maximum = int(self.POLICY["maximum_cases"])
        if not minimum <= len(pairs) <= maximum:
            reasons.append("case_count_out_of_bounds")
        case_ids = [pair.case_id for pair in pairs]
        if len(set(case_ids)) != len(case_ids):
            reasons.append("duplicate_case_id")
        quality_differences = [
            pair.candidate.quality - pair.baseline.quality for pair in pairs
        ]
        p_value, wins, losses = self._sign_p_value(quality_differences)

        def mean(values: list[float]) -> float:
            return sum(values) / len(values) if values else 0.0

        quality_effect = mean(quality_differences)
        comparisons = {
            "tokens": mean(
                [
                    float(pair.candidate.tokens - pair.baseline.tokens)
                    for pair in pairs
                ]
            ),
            "cost": mean(
                [pair.candidate.cost - pair.baseline.cost for pair in pairs]
            ),
            "latency_ms": mean(
                [
                    float(
                        pair.candidate.latency_ms - pair.baseline.latency_ms
                    )
                    for pair in pairs
                ]
            ),
            "reliability": mean(
                [
                    pair.candidate.reliability - pair.baseline.reliability
                    for pair in pairs
                ]
            ),
            "security": mean(
                [
                    pair.candidate.security - pair.baseline.security
                    for pair in pairs
                ]
            ),
        }
        resource_no_regression = (
            comparisons["tokens"] <= 0
            and comparisons["cost"] <= 0
            and comparisons["latency_ms"] <= 0
            and comparisons["reliability"] >= 0
            and comparisons["security"] >= 0
        )
        if quality_effect < float(self.POLICY["minimum_quality_effect"]):
            reasons.append("quality_effect_below_minimum")
        if not resource_no_regression:
            reasons.append("multi_objective_regression")
        return (
            {
                "case_count": len(pairs),
                "quality_effect": quality_effect,
                "raw_p_value": p_value,
                "positive_pairs": wins,
                "negative_pairs": losses,
                "tied_pairs": len(pairs) - wins - losses,
                "mean_deltas": comparisons,
                "resource_no_regression": resource_no_regression,
            },
            reasons,
        )

    def _persist_blocked(
        self,
        tournament_id: str,
        baseline: SkillGenome,
        candidates: tuple[SkillGenome, ...],
        adapter: str,
        isolation: dict[str, object],
        reason: str,
        now: str,
    ) -> GenomeTournament:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO skill_genome_tournaments(
                    id, baseline_genome_id, status, adapter, isolation_json,
                    policy_json, winner_genome_id, blocked_reason,
                    created_at, completed_at
                ) VALUES (?, ?, 'blocked', ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    tournament_id,
                    baseline.id,
                    adapter,
                    json.dumps(isolation, sort_keys=True),
                    json.dumps(self.POLICY, sort_keys=True),
                    reason,
                    now,
                    now,
                ),
            )
            for candidate in candidates:
                self.connection.execute(
                    """
                    INSERT INTO skill_genome_tournament_candidates(
                        id, tournament_id, candidate_genome_id,
                        observations_json, statistics_json, qualified,
                        tournament_rank, rejection_reasons_json, created_at
                    ) VALUES (?, ?, ?, '[]', '{}', 0, NULL, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        tournament_id,
                        candidate.id,
                        json.dumps([reason]),
                        now,
                    ),
                )
        return self.load_tournament(tournament_id)

    def run_tournament(
        self, baseline_genome_id: str, candidate_genome_ids: tuple[str, ...]
    ) -> GenomeTournament:
        maximum = int(self.POLICY["maximum_candidates"])
        if (
            not candidate_genome_ids
            or len(candidate_genome_ids) > maximum
            or len(set(candidate_genome_ids)) != len(candidate_genome_ids)
        ):
            raise ValueError(f"Tournament requires 1..{maximum} unique candidates")
        baseline = self.load_genome(baseline_genome_id)
        if baseline.status not in {"baseline", "selected"}:
            raise ValueError("Invalid tournament baseline")
        candidates = tuple(
            self.load_genome(candidate_id)
            for candidate_id in candidate_genome_ids
        )
        for candidate in candidates:
            if (
                candidate.status != "experimental"
                or candidate.parent_genome_id != baseline.id
                or candidate.source_skill_id != baseline.source_skill_id
                or candidate.source_hash != baseline.source_hash
            ):
                raise ValueError("Candidate is not a direct experimental mutation")
        source = self._verified_source(baseline.source_skill_id)
        if source["content_hash"] != baseline.source_hash:
            raise ValueError("Tournament source hash changed")
        tournament_id = str(uuid.uuid4())
        now = utc_now()
        adapter = type(self.benchmark).__name__
        try:
            evidence = self.benchmark.run(source, baseline, candidates)
        except Exception as error:
            return self._persist_blocked(
                tournament_id,
                baseline,
                candidates,
                adapter,
                {"isolated": False, "adapter_error": type(error).__name__},
                "benchmark_adapter_error",
                now,
            )
        isolation = {
            "isolated": evidence.isolated,
            "details": evidence.isolation_details,
        }
        encoded_isolation = json.dumps(isolation, sort_keys=True)
        if len(encoded_isolation.encode("utf-8")) > 16_384:
            raise ValueError("Isolation evidence exceeds 16 KB")
        if not evidence.isolated:
            return self._persist_blocked(
                tournament_id,
                baseline,
                candidates,
                adapter,
                isolation,
                evidence.blocked_reason or "benchmark_not_isolated",
                now,
            )
        unexpected = set(evidence.results) - set(candidate_genome_ids)
        if unexpected:
            raise ValueError("Benchmark returned an unknown candidate")
        if any(
            len(pairs) > int(self.POLICY["maximum_cases"])
            for pairs in evidence.results.values()
        ):
            return self._persist_blocked(
                tournament_id,
                baseline,
                candidates,
                adapter,
                isolation,
                "benchmark_case_count_exceeds_limit",
                now,
            )
        analyses: dict[str, dict[str, object]] = {}
        for candidate in candidates:
            pairs = evidence.results.get(candidate.id, ())
            statistics, reasons = self._candidate_statistics(pairs)
            if candidate.id not in evidence.results:
                reasons.append("missing_candidate_evidence")
            analyses[candidate.id] = {
                "pairs": pairs,
                "statistics": statistics,
                "reasons": reasons,
            }
        case_sets = {
            candidate.id: {
                pair.case_id for pair in analyses[candidate.id]["pairs"]
            }
            for candidate in candidates
        }
        if len({frozenset(values) for values in case_sets.values()}) > 1:
            for candidate in candidates:
                analyses[candidate.id]["reasons"].append(
                    "tournament_case_sets_do_not_match"
                )
        ordered = sorted(
            candidates,
            key=lambda candidate: (
                float(
                    analyses[candidate.id]["statistics"]["raw_p_value"]
                ),
                candidate.id,
            ),
        )
        correction_open = True
        candidate_count = len(ordered)
        for index, candidate in enumerate(ordered):
            threshold = float(self.POLICY["alpha"]) / (candidate_count - index)
            statistics = analyses[candidate.id]["statistics"]
            significant = (
                correction_open
                and float(statistics["raw_p_value"]) <= threshold
            )
            if not significant:
                correction_open = False
                analyses[candidate.id]["reasons"].append(
                    "not_significant_after_holm"
                )
            statistics["holm_threshold"] = threshold
            statistics["statistically_significant"] = significant
            analyses[candidate.id]["qualified"] = (
                significant and not analyses[candidate.id]["reasons"]
            )
        qualified = [
            candidate
            for candidate in candidates
            if analyses[candidate.id]["qualified"]
        ]
        ranked = sorted(
            qualified,
            key=lambda candidate: (
                -float(
                    analyses[candidate.id]["statistics"]["quality_effect"]
                ),
                float(
                    analyses[candidate.id]["statistics"]["mean_deltas"]["cost"]
                ),
                candidate.id,
            ),
        )
        winner_id = ranked[0].id if ranked else None
        ranks = {candidate.id: index for index, candidate in enumerate(ranked, 1)}
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO skill_genome_tournaments(
                    id, baseline_genome_id, status, adapter, isolation_json,
                    policy_json, winner_genome_id, blocked_reason,
                    created_at, completed_at
                ) VALUES (?, ?, 'completed', ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    tournament_id,
                    baseline.id,
                    adapter,
                    encoded_isolation,
                    json.dumps(self.POLICY, sort_keys=True),
                    winner_id,
                    now,
                    now,
                ),
            )
            for candidate in candidates:
                analysis = analyses[candidate.id]
                self.connection.execute(
                    """
                    INSERT INTO skill_genome_tournament_candidates(
                        id, tournament_id, candidate_genome_id,
                        observations_json, statistics_json, qualified,
                        tournament_rank, rejection_reasons_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        tournament_id,
                        candidate.id,
                        json.dumps(
                            [pair.as_dict() for pair in analysis["pairs"]],
                            sort_keys=True,
                        ),
                        json.dumps(analysis["statistics"], sort_keys=True),
                        int(bool(analysis["qualified"])),
                        ranks.get(candidate.id),
                        json.dumps(analysis["reasons"]),
                        now,
                    ),
                )
                self.connection.execute(
                    "UPDATE skill_genomes SET status = 'rejected' WHERE id = ?",
                    (candidate.id,),
                )
            if winner_id is not None:
                self.connection.execute(
                    """
                    UPDATE skill_genomes
                    SET status = 'selected', selected_at = ? WHERE id = ?
                    """,
                    (now, winner_id),
                )
        return self.load_tournament(tournament_id)

    def load_genome(self, genome_id: str) -> SkillGenome:
        row = self.connection.execute(
            "SELECT * FROM skill_genomes WHERE id = ?", (genome_id,)
        ).fetchone()
        if row is None:
            raise KeyError(genome_id)
        mutation_payload = (
            json.loads(row["mutation_json"]) if row["mutation_json"] else None
        )
        return SkillGenome(
            id=row["id"],
            source_skill_id=row["source_skill_id"],
            source_hash=row["source_hash"],
            parent_genome_id=row["parent_genome_id"],
            generation=row["generation"],
            status=row["status"],
            parameters=GenomeParameters.from_dict(
                json.loads(row["parameters_json"])
            ),
            mutation=(
                GenomeMutation.from_dict(mutation_payload)
                if mutation_payload is not None
                else None
            ),
            created_at=row["created_at"],
            selected_at=row["selected_at"],
        )

    def load_tournament(self, tournament_id: str) -> GenomeTournament:
        row = self.connection.execute(
            "SELECT * FROM skill_genome_tournaments WHERE id = ?",
            (tournament_id,),
        ).fetchone()
        if row is None:
            raise KeyError(tournament_id)
        candidates = self.connection.execute(
            """
            SELECT * FROM skill_genome_tournament_candidates
            WHERE tournament_id = ?
            ORDER BY
                CASE WHEN tournament_rank IS NULL THEN 1 ELSE 0 END,
                tournament_rank, candidate_genome_id
            """,
            (tournament_id,),
        ).fetchall()
        return GenomeTournament(
            id=row["id"],
            baseline_genome_id=row["baseline_genome_id"],
            status=row["status"],
            adapter=row["adapter"],
            isolation=json.loads(row["isolation_json"]),
            policy=json.loads(row["policy_json"]),
            winner_genome_id=row["winner_genome_id"],
            blocked_reason=row["blocked_reason"],
            candidates=tuple(
                GenomeTournamentCandidate(
                    candidate_genome_id=item["candidate_genome_id"],
                    observations=tuple(
                        json.loads(item["observations_json"])
                    ),
                    statistics=json.loads(item["statistics_json"]),
                    qualified=bool(item["qualified"]),
                    tournament_rank=item["tournament_rank"],
                    rejection_reasons=tuple(
                        json.loads(item["rejection_reasons_json"])
                    ),
                )
                for item in candidates
            ),
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )
