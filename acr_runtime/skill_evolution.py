from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .memory import utc_now
from .scoring import estimate_tokens
from .skill_format import SEMVER, SkillPackageLoader
from .skill_registry import SkillRegistry
from .skill_validator import SkillValidator


@dataclass(frozen=True)
class SkillMutation:
    instructions: str | None = None
    workflow: tuple[str, ...] = ()
    tools: tuple[str, ...] | None = None
    retrieval_strategy: str | None = None
    verification: tuple[str, ...] | None = None
    error_handling: str | None = None
    token_budget: int | None = None

    def __post_init__(self) -> None:
        if not any(
            (
                self.instructions,
                self.workflow,
                self.tools is not None,
                self.retrieval_strategy,
                self.verification is not None,
                self.error_handling,
                self.token_budget is not None,
            )
        ):
            raise ValueError("At least one skill mutation is required")
        values = (
            *((self.instructions,) if self.instructions is not None else ()),
            *self.workflow,
            *((self.retrieval_strategy,) if self.retrieval_strategy else ()),
            *((self.error_handling,) if self.error_handling else ()),
        )
        if any(not value.strip() for value in values):
            raise ValueError("Mutation text cannot be empty")
        if self.tools is not None and any(not item.strip() for item in self.tools):
            raise ValueError("Mutation tools cannot contain empty values")
        if self.verification is not None and (
            not self.verification
            or any(not item.strip() for item in self.verification)
        ):
            raise ValueError("Mutation verification must be non-empty")
        if self.token_budget is not None and self.token_budget < 1:
            raise ValueError("Mutation token_budget must be positive")


@dataclass(frozen=True)
class EvolutionMetrics:
    quality: float
    tokens: int
    cost: float
    latency_ms: int
    reliability: float
    security: float

    def __post_init__(self) -> None:
        if not 0 <= self.quality <= 1:
            raise ValueError("quality must be 0..1")
        if not 0 <= self.reliability <= 1:
            raise ValueError("reliability must be 0..1")
        if not 0 <= self.security <= 1:
            raise ValueError("security must be 0..1")
        if self.tokens < 0 or self.cost < 0 or self.latency_ms < 0:
            raise ValueError("resource metrics cannot be negative")


@dataclass(frozen=True)
class SkillEvolutionRun:
    id: str
    source_skill_id: str
    candidate_skill_id: str
    source_version: str
    candidate_version: str
    status: str
    mutation: SkillMutation
    source_hash: str
    candidate_hash: str
    baseline_validation_id: str | None
    candidate_validation_id: str | None
    comparison: dict[str, object] | None
    winner: str | None
    created_at: str
    compared_at: str | None = None
    promoted_at: str | None = None
    rolled_back_at: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "mutation": asdict(self.mutation),
        }


class SkillEvolutionEngine:
    """Creates immutable candidate versions and promotes Pareto-safe winners."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        registry: SkillRegistry,
        validator: SkillValidator,
        skills_dir: str | Path,
        *,
        loader: SkillPackageLoader | None = None,
    ) -> None:
        self.connection = connection
        self.registry = registry
        self.validator = validator
        self.skills_dir = Path(skills_dir)
        self.loader = loader or SkillPackageLoader()

    @staticmethod
    def _next_version(version: str) -> str:
        core = version.partition("-")[0].partition("+")[0]
        major, minor, _ = (int(item) for item in core.split("."))
        return f"{major}.{minor + 1}.0"

    @staticmethod
    def _safe_version(version: str) -> str:
        if not SEMVER.fullmatch(version):
            raise ValueError("candidate version must follow Semantic Versioning")
        return version

    @staticmethod
    def _version_key(version: str) -> tuple[int, int, int]:
        core = version.partition("-")[0].partition("+")[0]
        major, minor, patch = (int(item) for item in core.split("."))
        return major, minor, patch

    def _write_candidate(
        self,
        source_path: Path,
        mutation: SkillMutation,
        version: str,
    ) -> Path:
        source = self.loader.load(source_path)
        base = (self.skills_dir / "evolved").resolve()
        base.mkdir(parents=True, exist_ok=True)
        safe_version = re.sub(r"[^0-9A-Za-z.+-]", "-", version)
        final = (base / f"{source.manifest.id}-{safe_version}").resolve()
        if base not in final.parents:
            raise ValueError("Evolution path escapes the skills directory")
        if final.exists():
            raise ValueError("Candidate version directory already exists")
        temporary = Path(tempfile.mkdtemp(prefix=".mutation-", dir=base))
        shutil.rmtree(temporary)
        try:
            shutil.copytree(source.root, temporary)
            instructions = (
                mutation.instructions.strip()
                if mutation.instructions is not None
                else source.instructions
            )
            sections: list[str] = []
            if mutation.workflow:
                sections.append(
                    "## Evolved workflow\n\n"
                    + "\n".join(
                        f"{number}. {step}"
                        for number, step in enumerate(mutation.workflow, start=1)
                    )
                )
            if mutation.retrieval_strategy:
                sections.append(
                    "## Retrieval strategy\n\n" + mutation.retrieval_strategy
                )
            if mutation.error_handling:
                sections.append(
                    "## Error handling\n\n" + mutation.error_handling
                )
            if mutation.token_budget is not None:
                sections.append(
                    f"## Token budget\n\nHard ceiling: {mutation.token_budget} tokens."
                )
            if sections:
                instructions = instructions.rstrip() + "\n\n" + "\n\n".join(sections)
            manifest_path = temporary / "SKILL.yaml"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["version"] = version
            manifest["status"] = "experimental"
            manifest["updated_at"] = utc_now()
            manifest["token_estimate"] = estimate_tokens(instructions)
            if mutation.tools is not None:
                manifest["tools"] = list(mutation.tools)
            if mutation.verification is not None:
                manifest["verification"] = list(mutation.verification)
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            (temporary / "instructions.md").write_text(
                instructions + "\n", encoding="utf-8"
            )
            with (temporary / "history.jsonl").open(
                "a", encoding="utf-8", newline="\n"
            ) as history:
                history.write(
                    json.dumps(
                        {
                            "version": version,
                            "event": "candidate_mutation",
                            "source_version": source.manifest.version,
                            "mutation": asdict(mutation),
                            "created_at": manifest["updated_at"],
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            (temporary / "evolution.json").write_text(
                json.dumps(
                    {
                        "source": (
                            f"{source.manifest.id}@{source.manifest.version}"
                        ),
                        "candidate": f"{source.manifest.id}@{version}",
                        "mutation": asdict(mutation),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            self.loader.load(temporary)
            temporary.replace(final)
            return final
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise

    def create_candidate(
        self,
        source_reference: str,
        mutation: SkillMutation,
        *,
        version: str | None = None,
    ) -> SkillEvolutionRun:
        source = self.registry.inspect(source_reference)
        if source["lifecycle_status"] != "active":
            raise ValueError("Only an active validated skill can evolve")
        source_path = source.get("package_path")
        if not source_path:
            raise ValueError("Only Skill Format v1 packages can evolve")
        loaded_source = self.loader.load(Path(str(source_path)))
        if loaded_source.content_hash != source["content_hash"]:
            raise ValueError("Source package changed after validation")
        candidate_version = self._safe_version(
            version or self._next_version(str(source["version"]))
        )
        if self._version_key(candidate_version) <= self._version_key(
            str(source["version"])
        ):
            raise ValueError("Candidate version must be newer than its source")
        package_path = self._write_candidate(
            Path(str(source_path)), mutation, candidate_version
        )
        admitted = self.registry.admit(package_path)
        candidate = self.registry.inspect(str(admitted["id"]))
        if candidate["lifecycle_status"] != "quarantined":
            raise RuntimeError("Evolution candidate escaped quarantine")
        run_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO skill_evolution_runs(
                    id, source_skill_id, candidate_skill_id, source_version,
                    candidate_version, status, mutation_json, source_hash,
                    candidate_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, 'candidate', ?, ?, ?, ?)
                """,
                (
                    run_id, source["id"], candidate["id"], source["version"],
                    candidate["version"], json.dumps(asdict(mutation)),
                    source["content_hash"], candidate["content_hash"], utc_now(),
                ),
            )
        return self.load(run_id)

    @staticmethod
    def _comparison(
        source: EvolutionMetrics, candidate: EvolutionMetrics
    ) -> tuple[str, dict[str, object]]:
        weakly_better = {
            "quality": candidate.quality >= source.quality,
            "tokens": candidate.tokens <= source.tokens,
            "cost": candidate.cost <= source.cost,
            "latency": candidate.latency_ms <= source.latency_ms,
            "reliability": candidate.reliability >= source.reliability,
            "security": candidate.security >= source.security,
        }
        strict = {
            "quality": candidate.quality > source.quality,
            "tokens": candidate.tokens < source.tokens,
            "cost": candidate.cost < source.cost,
            "latency": candidate.latency_ms < source.latency_ms,
            "reliability": candidate.reliability > source.reliability,
            "security": candidate.security > source.security,
        }
        winner = (
            "candidate"
            if all(weakly_better.values()) and any(strict.values())
            else "source"
        )
        return winner, {
            "policy": "pareto_no_regression_v1",
            "source": asdict(source),
            "candidate": asdict(candidate),
            "weakly_better": weakly_better,
            "strictly_better": strict,
            "benchmark_score_alone_sufficient": False,
        }

    def compare(
        self,
        run_id: str,
        *,
        baseline_validation_id: str,
        candidate_validation_id: str,
    ) -> SkillEvolutionRun:
        run = self.load(run_id)
        if run.status != "candidate":
            raise ValueError("Only an unevaluated candidate can be compared")
        baseline = self.validator.load(baseline_validation_id)
        candidate_validation = self.validator.load(candidate_validation_id)
        if (
            baseline.skill_id != run.source_skill_id
            or baseline.package_hash != run.source_hash
            or baseline.status not in {"passed", "promoted"}
        ):
            raise ValueError("Baseline validation does not prove the source")
        if (
            candidate_validation.skill_id != run.candidate_skill_id
            or candidate_validation.package_hash != run.candidate_hash
            or candidate_validation.status != "passed"
        ):
            raise ValueError("Candidate must fully pass Prompt 20 first")
        benchmark = candidate_validation.results[-1]
        if benchmark.stage != "benchmark_comparison":
            raise ValueError("Candidate validation has no benchmark comparison")
        details = benchmark.evidence.details
        required = {
            "candidate_quality", "incumbent_quality",
            "candidate_tokens", "incumbent_tokens",
            "candidate_cost", "incumbent_cost",
            "candidate_latency_ms", "incumbent_latency_ms",
            "candidate_reliability", "incumbent_reliability",
            "candidate_security", "incumbent_security",
        }
        if not required.issubset(details):
            raise ValueError(
                "Benchmark evidence lacks Prompt 21 multi-objective metrics"
            )
        source_metrics = EvolutionMetrics(
            quality=float(details["incumbent_quality"]),
            tokens=int(details["incumbent_tokens"]),
            cost=float(details["incumbent_cost"]),
            latency_ms=int(details["incumbent_latency_ms"]),
            reliability=float(details["incumbent_reliability"]),
            security=float(details["incumbent_security"]),
        )
        candidate_metrics = EvolutionMetrics(
            quality=float(details["candidate_quality"]),
            tokens=int(details["candidate_tokens"]),
            cost=float(details["candidate_cost"]),
            latency_ms=int(details["candidate_latency_ms"]),
            reliability=float(details["candidate_reliability"]),
            security=float(details["candidate_security"]),
        )
        winner, comparison = self._comparison(
            source_metrics, candidate_metrics
        )
        with self.connection:
            self.connection.execute(
                """
                UPDATE skill_evolution_runs
                SET status = ?, baseline_validation_id = ?,
                    candidate_validation_id = ?, comparison_json = ?,
                    winner = ?, compared_at = ?
                WHERE id = ?
                """,
                (
                    "compared" if winner == "candidate" else "rejected",
                    baseline_validation_id, candidate_validation_id,
                    json.dumps(comparison, sort_keys=True), winner, utc_now(),
                    run_id,
                ),
            )
        return self.load(run_id)

    def promote(self, run_id: str) -> SkillEvolutionRun:
        run = self.load(run_id)
        if run.status != "compared" or run.winner != "candidate":
            raise ValueError("Only a multi-objective candidate winner can promote")
        self.validator.promote(str(run.candidate_validation_id))
        self.registry.quarantine(run.source_skill_id)
        with self.connection:
            self.connection.execute(
                """
                UPDATE skill_evolution_runs
                SET status = 'promoted', promoted_at = ? WHERE id = ?
                """,
                (utc_now(), run_id),
            )
        return self.load(run_id)

    def rollback(self, run_id: str, *, reason: str) -> SkillEvolutionRun:
        if not reason.strip():
            raise ValueError("Rollback reason is required")
        run = self.load(run_id)
        if run.status != "promoted":
            raise ValueError("Only a promoted evolution can be rolled back")
        self.registry.quarantine(run.candidate_skill_id)
        self.registry.activate(run.source_skill_id)
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """
                UPDATE skill_evolution_runs
                SET status = 'rolled_back', rolled_back_at = ? WHERE id = ?
                """,
                (now, run_id),
            )
            self.connection.execute(
                """
                INSERT INTO skill_evolution_rollbacks(
                    id, run_id, from_skill_id, to_skill_id, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()), run_id, run.candidate_skill_id,
                    run.source_skill_id, reason.strip(), now,
                ),
            )
        return self.load(run_id)

    def load(self, run_id: str) -> SkillEvolutionRun:
        row = self.connection.execute(
            "SELECT * FROM skill_evolution_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        mutation_data = json.loads(row["mutation_json"])
        for field in ("workflow", "tools", "verification"):
            if mutation_data.get(field) is not None:
                mutation_data[field] = tuple(mutation_data[field])
        return SkillEvolutionRun(
            id=row["id"],
            source_skill_id=row["source_skill_id"],
            candidate_skill_id=row["candidate_skill_id"],
            source_version=row["source_version"],
            candidate_version=row["candidate_version"],
            status=row["status"],
            mutation=SkillMutation(**mutation_data),
            source_hash=row["source_hash"],
            candidate_hash=row["candidate_hash"],
            baseline_validation_id=row["baseline_validation_id"],
            candidate_validation_id=row["candidate_validation_id"],
            comparison=(
                json.loads(row["comparison_json"])
                if row["comparison_json"] else None
            ),
            winner=row["winner"],
            created_at=row["created_at"],
            compared_at=row["compared_at"],
            promoted_at=row["promoted_at"],
            rolled_back_at=row["rolled_back_at"],
        )
