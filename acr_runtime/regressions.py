from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .memory import utc_now
from .secret_management import assert_secret_free

MetricName = Literal[
    "token_consumption",
    "quality",
    "latency",
    "model_escalation",
    "memory_retrieval",
    "skill_failure",
]

METRICS = (
    "token_consumption",
    "quality",
    "latency",
    "model_escalation",
    "memory_retrieval",
    "skill_failure",
)
LOWER_IS_BAD = {"quality", "memory_retrieval"}
METRIC_DOMAINS = {
    "token_consumption": {
        "context_budget", "model_router", "retrieval_algorithm",
        "skill_version", "planner_strategy",
    },
    "quality": {
        "context_budget", "model_router", "retrieval_algorithm",
        "skill_version", "planner_strategy",
    },
    "latency": {
        "model_router", "retrieval_algorithm", "skill_version",
        "planner_strategy",
    },
    "model_escalation": {"model_router", "context_budget"},
    "memory_retrieval": {"retrieval_algorithm", "context_budget"},
    "skill_failure": {"skill_version"},
}
DOMAINS = frozenset().union(*METRIC_DOMAINS.values())
IDENTIFIER = re.compile(r"[a-z][a-z0-9_.-]{1,127}")


def _timestamp(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


@dataclass(frozen=True)
class MetricSummary:
    name: MetricName
    baseline_value: float
    baseline_samples: int
    candidate_value: float
    candidate_samples: int
    baseline_stddev: float = 0.0

    def __post_init__(self) -> None:
        values = (
            self.baseline_value, self.candidate_value, self.baseline_stddev
        )
        if (
            self.name not in METRICS
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) for value in values)
            or self.baseline_stddev < 0
            or type(self.baseline_samples) is not int
            or type(self.candidate_samples) is not int
            or min(self.baseline_samples, self.candidate_samples) < 0
        ):
            raise ValueError("Metric summary is invalid")
        if self.name in {
            "quality", "model_escalation", "memory_retrieval", "skill_failure"
        } and not (
            0 <= self.baseline_value <= 1
            and 0 <= self.candidate_value <= 1
            and 0 <= self.baseline_stddev <= 1
        ):
            raise ValueError(f"{self.name} values must be between 0 and 1")
        if self.name in {"token_consumption", "latency"} and min(
            self.baseline_value, self.candidate_value
        ) < 0:
            raise ValueError(f"{self.name} values cannot be negative")

    @classmethod
    def from_dict(cls, payload: object) -> "MetricSummary":
        fields = {
            "name", "baseline_value", "baseline_samples", "candidate_value",
            "candidate_samples", "baseline_stddev",
        }
        if not isinstance(payload, dict) or not set(payload) <= fields:
            raise ValueError("Metric summary contains unknown fields")
        required = fields - {"baseline_stddev"}
        if not required <= set(payload):
            raise ValueError(f"Metric summary requires {sorted(required)}")
        return cls(
            name=payload["name"],
            baseline_value=payload["baseline_value"],
            baseline_samples=payload["baseline_samples"],
            candidate_value=payload["candidate_value"],
            candidate_samples=payload["candidate_samples"],
            baseline_stddev=payload.get("baseline_stddev", 0.0),
        )


@dataclass(frozen=True)
class ChangeCandidate:
    id: str
    domain: str
    changed_at: str
    before_ref: str
    after_ref: str
    rollback_ref: str | None
    affected_metrics: tuple[MetricName, ...]
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not IDENTIFIER.fullmatch(self.id):
            raise ValueError("Change ID must be a bounded identifier")
        if not isinstance(self.domain, str) or self.domain not in DOMAINS:
            raise ValueError("Unsupported change domain")
        _timestamp(self.changed_at, "changed_at")
        if (
            not isinstance(self.before_ref, str)
            or not isinstance(self.after_ref, str)
            or self.rollback_ref is not None
            and not isinstance(self.rollback_ref, str)
            or not isinstance(self.affected_metrics, tuple)
            or not isinstance(self.evidence, tuple)
            or any(not isinstance(item, str) for item in self.evidence)
        ):
            raise ValueError("Change candidate field types are invalid")
        bounded = (self.before_ref, self.after_ref, *self.evidence)
        if (
            not self.before_ref.strip() or not self.after_ref.strip()
            or max(map(len, bounded), default=0) > 512
            or self.rollback_ref is not None
            and (not self.rollback_ref.strip() or len(self.rollback_ref) > 512)
            or not 1 <= len(self.evidence) <= 32
            or not self.affected_metrics
            or any(metric not in METRICS for metric in self.affected_metrics)
        ):
            raise ValueError("Change candidate requires bounded evidence and refs")
        if len(set(self.affected_metrics)) != len(self.affected_metrics):
            raise ValueError("Affected metrics must be unique")
        assert_secret_free(
            json.dumps({
                "before": self.before_ref, "after": self.after_ref,
                "rollback": self.rollback_ref, "evidence": self.evidence,
            }),
            "regression change evidence",
        )

    @classmethod
    def from_dict(cls, payload: object) -> "ChangeCandidate":
        fields = {
            "id", "domain", "changed_at", "before_ref", "after_ref",
            "rollback_ref", "affected_metrics", "evidence",
        }
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError(f"Change candidate must contain {sorted(fields)}")
        if not isinstance(payload["affected_metrics"], list) or not isinstance(
            payload["evidence"], list
        ):
            raise ValueError("Change metrics and evidence must be lists")
        return cls(
            **{
                **payload,
                "affected_metrics": tuple(payload["affected_metrics"]),
                "evidence": tuple(payload["evidence"]),
            }
        )


@dataclass(frozen=True)
class RegressionRequest:
    scope: str
    task_class: str
    baseline_start: str
    baseline_end: str
    candidate_start: str
    candidate_end: str
    metrics: tuple[MetricSummary, ...]
    changes: tuple[ChangeCandidate, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.scope, str) or not self.scope.strip()
            or len(self.scope) > 128
            or not isinstance(self.task_class, str) or not self.task_class.strip()
            or len(self.task_class) > 128
        ):
            raise ValueError("Regression scope and task_class must be bounded")
        times = [
            _timestamp(self.baseline_start, "baseline_start"),
            _timestamp(self.baseline_end, "baseline_end"),
            _timestamp(self.candidate_start, "candidate_start"),
            _timestamp(self.candidate_end, "candidate_end"),
        ]
        if not times[0] < times[1] <= times[2] < times[3]:
            raise ValueError("Regression windows must be ordered and non-overlapping")
        names = [item.name for item in self.metrics]
        if set(names) != set(METRICS) or len(names) != len(METRICS):
            raise ValueError("Exactly one summary for each required metric is required")
        if len({item.id for item in self.changes}) != len(self.changes):
            raise ValueError("Change IDs must be unique")
        assert_secret_free(
            json.dumps({"scope": self.scope, "task_class": self.task_class}),
            "regression comparison identity",
        )

    @classmethod
    def from_dict(cls, payload: object) -> "RegressionRequest":
        fields = {
            "scope", "task_class", "baseline_start", "baseline_end",
            "candidate_start", "candidate_end", "metrics", "changes",
        }
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError(f"Regression request must contain {sorted(fields)}")
        if not isinstance(payload["metrics"], list) or not isinstance(
            payload["changes"], list
        ):
            raise ValueError("Regression metrics and changes must be lists")
        return cls(
            **{
                **payload,
                "metrics": tuple(MetricSummary.from_dict(x)
                                 for x in payload["metrics"]),
                "changes": tuple(ChangeCandidate.from_dict(x)
                                 for x in payload["changes"]),
            }
        )


@dataclass(frozen=True)
class MetricPolicy:
    relative_threshold: float
    absolute_threshold: float
    minimum_samples: int = 30
    sigma_limit: float = 3.0


DEFAULT_POLICIES = {
    "token_consumption": MetricPolicy(0.20, 100.0),
    "quality": MetricPolicy(0.05, 0.03),
    "latency": MetricPolicy(0.20, 50.0),
    "model_escalation": MetricPolicy(0.25, 0.05),
    "memory_retrieval": MetricPolicy(0.10, 0.05),
    "skill_failure": MetricPolicy(0.25, 0.05),
}


class RegressionDetector:
    """Persist significant comparable shifts and evidence-bounded recommendations."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def analyze(self, request: RegressionRequest) -> dict[str, object]:
        run_id = str(uuid.uuid4())
        now = utc_now()
        rows: list[dict[str, object]] = []
        for metric in request.metrics:
            policy = DEFAULT_POLICIES[metric.name]
            adverse = (
                metric.baseline_value - metric.candidate_value
                if metric.name in LOWER_IS_BAD
                else metric.candidate_value - metric.baseline_value
            )
            relative = adverse / max(abs(metric.baseline_value), 1e-12)
            sampling_limit = (
                policy.sigma_limit * metric.baseline_stddev
                / math.sqrt(max(metric.candidate_samples, 1))
            )
            effective = max(
                policy.absolute_threshold,
                policy.relative_threshold * abs(metric.baseline_value),
                sampling_limit,
            )
            enough = min(
                metric.baseline_samples, metric.candidate_samples
            ) >= policy.minimum_samples
            regressed = enough and adverse >= effective
            rows.append({
                "name": metric.name,
                "baseline_value": metric.baseline_value,
                "baseline_samples": metric.baseline_samples,
                "baseline_stddev": metric.baseline_stddev,
                "candidate_value": metric.candidate_value,
                "candidate_samples": metric.candidate_samples,
                "adverse_delta": adverse,
                "relative_delta": relative,
                "effective_threshold": effective,
                "minimum_samples": policy.minimum_samples,
                "status": (
                    "regressed" if regressed
                    else "insufficient_data" if not enough
                    else "within_limit"
                ),
            })

        alerts: list[dict[str, object]] = []
        recommendations: dict[str, dict[str, object]] = {}
        for row in rows:
            if row["status"] != "regressed":
                continue
            likely, attribution = self._attribute(
                request, str(row["name"])
            )
            alert_id = str(uuid.uuid4())
            alert = {
                "id": alert_id,
                "metric": row["name"],
                "severity": self._severity(
                    float(row["adverse_delta"]),
                    float(row["effective_threshold"]),
                ),
                "likely_change_id": likely.id if likely else None,
                "attribution": attribution,
            }
            alerts.append(alert)
            if likely and likely.rollback_ref:
                recommendations.setdefault(likely.id, {
                    "id": str(uuid.uuid4()),
                    "change_id": likely.id,
                    "rollback_ref": likely.rollback_ref,
                    "reason": (
                        "Investigate and, after operator review, roll back "
                        f"{likely.after_ref} to {likely.rollback_ref}; linked "
                        "regression alerts exist."
                    ),
                    "status": "proposed",
                    "automatic_action_performed": False,
                })

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO regression_runs (
                    id, scope, task_class, baseline_start, baseline_end,
                    candidate_start, candidate_end, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'completed', ?)
                """,
                (
                    run_id, request.scope, request.task_class,
                    request.baseline_start, request.baseline_end,
                    request.candidate_start, request.candidate_end, now,
                ),
            )
            for change in request.changes:
                self.connection.execute(
                    """
                    INSERT INTO regression_changes (
                        run_id, change_id, domain, changed_at, before_ref,
                        after_ref, rollback_ref, affected_metrics_json,
                        evidence_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id, change.id, change.domain, change.changed_at,
                        change.before_ref, change.after_ref, change.rollback_ref,
                        json.dumps(change.affected_metrics),
                        json.dumps(change.evidence),
                    ),
                )
            for row in rows:
                self.connection.execute(
                    """
                    INSERT INTO regression_metrics (
                        run_id, metric, baseline_value, baseline_samples,
                        baseline_stddev, candidate_value, candidate_samples,
                        adverse_delta, relative_delta, effective_threshold,
                        minimum_samples, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id, row["name"], row["baseline_value"],
                        row["baseline_samples"], row["baseline_stddev"],
                        row["candidate_value"], row["candidate_samples"],
                        row["adverse_delta"], row["relative_delta"],
                        row["effective_threshold"], row["minimum_samples"],
                        row["status"],
                    ),
                )
            for alert in alerts:
                self.connection.execute(
                    """
                    INSERT INTO regression_alerts (
                        id, run_id, metric, severity, likely_change_id,
                        attribution, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        alert["id"], run_id, alert["metric"], alert["severity"],
                        alert["likely_change_id"], alert["attribution"], now,
                    ),
                )
            for recommendation in recommendations.values():
                self.connection.execute(
                    """
                    INSERT INTO rollback_recommendations (
                        id, run_id, change_id, rollback_ref, reason, status,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        recommendation["id"], run_id,
                        recommendation["change_id"],
                        recommendation["rollback_ref"],
                        recommendation["reason"], recommendation["status"], now,
                    ),
                )
        return self.report(run_id)

    @staticmethod
    def _severity(adverse: float, threshold: float) -> str:
        multiple = adverse / max(threshold, 1e-12)
        return "critical" if multiple >= 2 else "warning"

    @staticmethod
    def _attribute(
        request: RegressionRequest, metric: str
    ) -> tuple[ChangeCandidate | None, str]:
        baseline_end = _timestamp(request.baseline_end, "baseline_end")
        candidate_start = _timestamp(request.candidate_start, "candidate_start")
        eligible = [
            change for change in request.changes
            if baseline_end <= _timestamp(change.changed_at, "changed_at")
            <= candidate_start
            and change.domain in METRIC_DOMAINS[metric]
            and metric in change.affected_metrics
        ]
        if not eligible:
            return None, "unattributed_no_matching_evidenced_change"
        latest_time = max(_timestamp(x.changed_at, "changed_at") for x in eligible)
        latest = [
            item for item in eligible
            if _timestamp(item.changed_at, "changed_at") == latest_time
        ]
        if len(latest) != 1:
            return None, "unattributed_ambiguous_matching_changes"
        return latest[0], "likely_temporal_domain_and_metric_match_not_causal_proof"

    def report(self, run_id: str) -> dict[str, object]:
        run = self.connection.execute(
            "SELECT * FROM regression_runs WHERE id=?", (run_id,)
        ).fetchone()
        if run is None:
            raise LookupError(f"Unknown regression run: {run_id}")
        metrics = [
            dict(row) for row in self.connection.execute(
                "SELECT * FROM regression_metrics WHERE run_id=? ORDER BY metric",
                (run_id,),
            )
        ]
        alerts = [
            dict(row) for row in self.connection.execute(
                "SELECT * FROM regression_alerts WHERE run_id=? ORDER BY metric",
                (run_id,),
            )
        ]
        recommendations = [
            {
                **dict(row),
                "automatic_action_performed": False,
            }
            for row in self.connection.execute(
                """
                SELECT * FROM rollback_recommendations
                WHERE run_id=? ORDER BY created_at, id
                """,
                (run_id,),
            )
        ]
        return {
            "run": dict(run),
            "metrics": metrics,
            "alerts": alerts,
            "rollback_recommendations": recommendations,
            "alert_count": len(alerts),
            "automatic_rollback_performed": False,
            "interpretation": (
                "alerts_identify_significant_shifts_attribution_is_a_hypothesis"
            ),
        }
