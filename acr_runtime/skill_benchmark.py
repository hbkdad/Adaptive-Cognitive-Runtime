from __future__ import annotations

import json
import math
import sqlite3
import uuid
from dataclasses import asdict, dataclass

from .memory import utc_now
from .secret_management import assert_secret_free

ARMS = ("without_skill", "existing_skill", "candidate_skill")


def _strict(payload: object, fields: set[str], name: str) -> dict:
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError(f"{name} must contain exactly {sorted(fields)}")
    return dict(payload)


@dataclass(frozen=True)
class SkillTrial:
    case_id: str
    task_class: str
    arm: str
    quality: float
    tokens: int
    latency_ms: int
    cost: float
    failed: bool
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.case_id, str) or not self.case_id.strip()
            or len(self.case_id) > 128
            or not isinstance(self.task_class, str) or not self.task_class.strip()
            or len(self.task_class) > 128
            or self.arm not in ARMS
            or isinstance(self.quality, bool)
            or not isinstance(self.quality, (int, float))
            or not math.isfinite(self.quality)
            or not 0 <= self.quality <= 1
            or type(self.tokens) is not int or self.tokens < 0
            or type(self.latency_ms) is not int or self.latency_ms < 0
            or isinstance(self.cost, bool)
            or not isinstance(self.cost, (int, float))
            or not math.isfinite(self.cost) or self.cost < 0
            or type(self.failed) is not bool
            or not isinstance(self.evidence, tuple)
            or not 1 <= len(self.evidence) <= 32
            or any(
                not isinstance(item, str) or not item.strip() or len(item) > 512
                for item in self.evidence
            )
        ):
            raise ValueError("Skill benchmark trial is invalid")
        for item in self.evidence:
            assert_secret_free(item, "skill benchmark evidence")

    @classmethod
    def from_dict(cls, payload: object) -> "SkillTrial":
        fields = {
            "case_id", "task_class", "arm", "quality", "tokens",
            "latency_ms", "cost", "failed", "evidence",
        }
        data = _strict(payload, fields, "Skill benchmark trial")
        if not isinstance(data["evidence"], list):
            raise ValueError("Skill trial evidence must be a list")
        data["evidence"] = tuple(data["evidence"])
        return cls(**data)


@dataclass(frozen=True)
class SkillBenchmarkRequest:
    skill_name: str
    existing_ref: str
    candidate_ref: str
    trials: tuple[SkillTrial, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.skill_name, str) or not self.skill_name.strip()
            or len(self.skill_name) > 128
            or not isinstance(self.existing_ref, str)
            or not self.existing_ref.strip() or len(self.existing_ref) > 256
            or not isinstance(self.candidate_ref, str)
            or not self.candidate_ref.strip() or len(self.candidate_ref) > 256
            or self.existing_ref == self.candidate_ref
            or not isinstance(self.trials, tuple)
            or not self.trials
        ):
            raise ValueError("Skill benchmark request is invalid")
        assert_secret_free(
            json.dumps({
                "skill_name": self.skill_name,
                "existing_ref": self.existing_ref,
                "candidate_ref": self.candidate_ref,
            }),
            "skill benchmark identity",
        )
        by_case: dict[str, set[str]] = {}
        task_classes: dict[str, str] = {}
        for trial in self.trials:
            if trial.case_id in task_classes and (
                task_classes[trial.case_id] != trial.task_class
            ):
                raise ValueError("A benchmark case must have one task class")
            task_classes[trial.case_id] = trial.task_class
            arms = by_case.setdefault(trial.case_id, set())
            if trial.arm in arms:
                raise ValueError("Duplicate skill benchmark case arm")
            arms.add(trial.arm)
        incomplete = {
            case_id: sorted(set(ARMS) - arms)
            for case_id, arms in by_case.items()
            if arms != set(ARMS)
        }
        if incomplete:
            raise ValueError(f"Every case requires all three arms: {incomplete}")

    @classmethod
    def from_dict(cls, payload: object) -> "SkillBenchmarkRequest":
        data = _strict(
            payload,
            {"skill_name", "existing_ref", "candidate_ref", "trials"},
            "Skill benchmark request",
        )
        if not isinstance(data["trials"], list):
            raise ValueError("Skill benchmark trials must be a list")
        data["trials"] = tuple(
            SkillTrial.from_dict(item) for item in data["trials"]
        )
        return cls(**data)


@dataclass(frozen=True)
class SkillBenchmarkPolicy:
    minimum_paired_cases: int = 5
    meaningful_quality_gain: float = 0.02
    meaningful_failure_reduction: float = 0.02
    resource_overhead_ratio: float = 0.05
    absolute_token_overhead: int = 10
    absolute_latency_overhead_ms: int = 10
    absolute_cost_overhead: float = 0.0001


class SkillBenchmarkController:
    """Retain paired three-arm evidence and non-mutating lifecycle advice."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        policy: SkillBenchmarkPolicy | None = None,
    ) -> None:
        self.connection = connection
        self.policy = policy or SkillBenchmarkPolicy()

    @staticmethod
    def _summary(trials: tuple[SkillTrial, ...]) -> dict[str, float | int]:
        count = len(trials)
        return {
            "cases": count,
            "quality": sum(item.quality for item in trials) / count,
            "tokens": sum(item.tokens for item in trials),
            "average_tokens": sum(item.tokens for item in trials) / count,
            "latency_ms": sum(item.latency_ms for item in trials),
            "average_latency_ms": (
                sum(item.latency_ms for item in trials) / count
            ),
            "cost": sum(item.cost for item in trials),
            "failure_rate": sum(item.failed for item in trials) / count,
        }

    def _overhead(
        self, baseline: dict[str, float | int], skill: dict[str, float | int]
    ) -> dict[str, bool]:
        policy = self.policy
        return {
            "tokens": float(skill["average_tokens"]) > max(
                float(baseline["average_tokens"])
                * (1 + policy.resource_overhead_ratio),
                float(baseline["average_tokens"])
                + policy.absolute_token_overhead,
            ),
            "latency": float(skill["average_latency_ms"]) > max(
                float(baseline["average_latency_ms"])
                * (1 + policy.resource_overhead_ratio),
                float(baseline["average_latency_ms"])
                + policy.absolute_latency_overhead_ms,
            ),
            "cost": float(skill["cost"]) > max(
                float(baseline["cost"]) * (1 + policy.resource_overhead_ratio),
                float(baseline["cost"]) + policy.absolute_cost_overhead,
            ),
        }

    def analyze(self, request: SkillBenchmarkRequest) -> dict[str, object]:
        run_id = str(uuid.uuid4())
        now = utc_now()
        by_arm = {
            arm: tuple(item for item in request.trials if item.arm == arm)
            for arm in ARMS
        }
        summaries = {
            arm: self._summary(trials) for arm, trials in by_arm.items()
        }
        task_classes = sorted({item.task_class for item in request.trials})
        class_summaries = {
            task_class: {
                arm: self._summary(tuple(
                    item for item in trials
                    if item.task_class == task_class
                ))
                for arm, trials in by_arm.items()
            }
            for task_class in task_classes
        }
        baseline = summaries["without_skill"]
        existing = summaries["existing_skill"]
        candidate = summaries["candidate_skill"]
        existing_overhead = self._overhead(baseline, existing)
        candidate_overhead = self._overhead(baseline, candidate)

        def earns_value(
            skill: dict[str, float | int],
            no_skill: dict[str, float | int],
        ) -> bool:
            return (
                float(skill["quality"]) - float(no_skill["quality"])
                >= self.policy.meaningful_quality_gain
                or float(no_skill["failure_rate"])
                - float(skill["failure_rate"])
                >= self.policy.meaningful_failure_reduction
            )

        existing_value_by_class = {
            task_class: earns_value(
                rows["existing_skill"], rows["without_skill"]
            )
            for task_class, rows in class_summaries.items()
        }
        candidate_value_by_class = {
            task_class: earns_value(
                rows["candidate_skill"], rows["without_skill"]
            )
            for task_class, rows in class_summaries.items()
        }
        existing_value = any(existing_value_by_class.values())
        candidate_value = any(candidate_value_by_class.values())

        recommendations: list[dict[str, object]] = []
        enough_evidence = len({item.case_id for item in request.trials}) >= (
            self.policy.minimum_paired_cases
        )
        if not enough_evidence:
            recommendations.append({
                "id": str(uuid.uuid4()),
                "target_ref": request.existing_ref,
                "action": "insufficient_evidence",
                "reason": "minimum_paired_case_count_not_met",
                "evidence": {
                    "cases": len(by_arm["without_skill"]),
                    "minimum_cases": self.policy.minimum_paired_cases,
                },
            })
        elif not existing_value and any(existing_overhead.values()):
            recommendations.append({
                "id": str(uuid.uuid4()),
                "target_ref": request.existing_ref,
                "action": "deprecate",
                "reason": "existing_skill_adds_measured_overhead_without_value",
                "evidence": {
                    "value_earned": False,
                    "value_by_task_class": existing_value_by_class,
                    "overhead": existing_overhead,
                },
            })
        else:
            recommendations.append({
                "id": str(uuid.uuid4()),
                "target_ref": request.existing_ref,
                "action": "keep",
                "reason": "existing_skill_earns_value_or_has_no_material_overhead",
                "evidence": {
                    "value_earned": existing_value,
                    "value_by_task_class": existing_value_by_class,
                    "overhead": existing_overhead,
                },
            })

        weakly_better = {
            "quality": float(candidate["quality"]) >= float(existing["quality"]),
            "tokens": float(candidate["tokens"]) <= float(existing["tokens"]),
            "latency": float(candidate["latency_ms"])
            <= float(existing["latency_ms"]),
            "cost": float(candidate["cost"]) <= float(existing["cost"]),
            "failure_rate": float(candidate["failure_rate"])
            <= float(existing["failure_rate"]),
        }
        strictly_better = {
            "quality": float(candidate["quality"]) > float(existing["quality"]),
            "tokens": float(candidate["tokens"]) < float(existing["tokens"]),
            "latency": float(candidate["latency_ms"])
            < float(existing["latency_ms"]),
            "cost": float(candidate["cost"]) < float(existing["cost"]),
            "failure_rate": float(candidate["failure_rate"])
            < float(existing["failure_rate"]),
        }
        candidate_wins = all(weakly_better.values()) and any(
            strictly_better.values()
        )
        per_class_non_regression = {}
        for task_class in task_classes:
            existing_class = self._summary(tuple(
                x for x in by_arm["existing_skill"]
                if x.task_class == task_class
            ))
            candidate_class = self._summary(tuple(
                x for x in by_arm["candidate_skill"]
                if x.task_class == task_class
            ))
            per_class_non_regression[task_class] = (
                float(candidate_class["quality"])
                >= float(existing_class["quality"])
                and float(candidate_class["failure_rate"])
                <= float(existing_class["failure_rate"])
            )
        candidate_wins = candidate_wins and all(
            per_class_non_regression.values()
        )
        if not enough_evidence:
            action = "insufficient_evidence"
            reason = "minimum_paired_case_count_not_met"
        elif not candidate_value and any(candidate_overhead.values()):
            action = "deprecate"
            reason = "candidate_skill_adds_measured_overhead_without_value"
        elif candidate_wins:
            action = "consider_candidate"
            reason = "candidate_is_pareto_better_than_existing_skill"
        else:
            action = "reject_candidate"
            reason = "candidate_does_not_meet_pareto_no_regression_policy"
        recommendations.append({
            "id": str(uuid.uuid4()),
            "target_ref": request.candidate_ref,
            "action": action,
            "reason": reason,
            "evidence": {
                "value_earned": candidate_value,
                "value_by_task_class": candidate_value_by_class,
                "overhead": candidate_overhead,
                "weakly_better_than_existing": weakly_better,
                "strictly_better_than_existing": strictly_better,
                "per_task_class_non_regression": per_class_non_regression,
            },
        })

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO skill_benchmark_runs (
                    id, skill_name, existing_ref, candidate_ref, policy_json,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'completed', ?)
                """,
                (
                    run_id, request.skill_name, request.existing_ref,
                    request.candidate_ref, json.dumps(asdict(self.policy)), now,
                ),
            )
            for trial in request.trials:
                self.connection.execute(
                    """
                    INSERT INTO skill_benchmark_trials (
                        id, run_id, case_id, task_class, arm, quality, tokens,
                        latency_ms, cost, failed, evidence_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()), run_id, trial.case_id,
                        trial.task_class, trial.arm, trial.quality, trial.tokens,
                        trial.latency_ms, trial.cost, int(trial.failed),
                        json.dumps(trial.evidence), now,
                    ),
                )
            for recommendation in recommendations:
                self.connection.execute(
                    """
                    INSERT INTO skill_benchmark_recommendations (
                        id, run_id, target_ref, action, reason, evidence_json,
                        status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?)
                    """,
                    (
                        recommendation["id"], run_id,
                        recommendation["target_ref"], recommendation["action"],
                        recommendation["reason"],
                        json.dumps(recommendation["evidence"], sort_keys=True),
                        now,
                    ),
                )
        return self.report(run_id)

    def report(self, run_id: str) -> dict[str, object]:
        run = self.connection.execute(
            "SELECT * FROM skill_benchmark_runs WHERE id=?", (run_id,)
        ).fetchone()
        if run is None:
            raise LookupError(f"Unknown skill benchmark run: {run_id}")
        trials = [
            SkillTrial(
                case_id=row["case_id"],
                task_class=row["task_class"],
                arm=row["arm"],
                quality=row["quality"],
                tokens=row["tokens"],
                latency_ms=row["latency_ms"],
                cost=row["cost"],
                failed=bool(row["failed"]),
                evidence=tuple(json.loads(row["evidence_json"])),
            )
            for row in self.connection.execute(
                """
                SELECT * FROM skill_benchmark_trials
                WHERE run_id=? ORDER BY case_id, arm
                """,
                (run_id,),
            )
        ]
        summary = {
            arm: self._summary(tuple(x for x in trials if x.arm == arm))
            for arm in ARMS
        }
        task_class_summary = {
            task_class: {
                arm: self._summary(tuple(
                    x for x in trials
                    if x.arm == arm and x.task_class == task_class
                ))
                for arm in ARMS
            }
            for task_class in sorted({item.task_class for item in trials})
        }
        recommendations = []
        for row in self.connection.execute(
            """
            SELECT * FROM skill_benchmark_recommendations
            WHERE run_id=? ORDER BY target_ref, id
            """,
            (run_id,),
        ):
            item = dict(row)
            item["evidence"] = json.loads(item.pop("evidence_json"))
            item["automatic_lifecycle_change_performed"] = False
            recommendations.append(item)
        return {
            "run": {
                **{
                    key: value for key, value in dict(run).items()
                    if key != "policy_json"
                },
                "policy": json.loads(run["policy_json"]),
            },
            "summary": summary,
            "task_class_summary": task_class_summary,
            "trials": [asdict(item) for item in trials],
            "recommendations": recommendations,
            "automatic_lifecycle_change_performed": False,
            "interpretation": (
                "paired_descriptive_metrics_and_non_mutating_recommendations"
            ),
        }
