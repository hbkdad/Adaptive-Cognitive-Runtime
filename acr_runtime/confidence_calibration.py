from __future__ import annotations

import json
import math
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

CalibrationDomain = Literal["memory", "routing", "evaluation"]
DOMAINS = ("memory", "routing", "evaluation")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _domain(value: str) -> CalibrationDomain:
    if value not in DOMAINS:
        raise ValueError(f"domain must be one of {DOMAINS}")
    return value  # type: ignore[return-value]


def _confidence(value: float) -> float:
    if isinstance(value, bool):
        raise ValueError("predicted confidence must be numeric, not boolean")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError("predicted confidence must be between 0 and 1")
    return number


def _wilson_interval(successes: int, trials: int) -> tuple[float, float]:
    if trials <= 0:
        raise ValueError("Wilson interval requires at least one trial")
    z = 1.959963984540054
    proportion = successes / trials
    denominator = 1.0 + z * z / trials
    centre = (proportion + z * z / (2.0 * trials)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1.0 - proportion) / trials
            + z * z / (4.0 * trials * trials)
        )
        / denominator
    )
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _nonempty(value: str, field: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{field} cannot be empty")
    return text


@dataclass(frozen=True)
class CalibrationBin:
    lower_bound: float
    upper_bound: float
    sample_count: int
    mean_predicted_confidence: float | None
    actual_success_rate: float | None
    actual_rate_lower_95: float | None
    actual_rate_upper_95: float | None
    calibration_gap: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "lower_bound": self.lower_bound,
            "upper_bound": self.upper_bound,
            "sample_count": self.sample_count,
            "mean_predicted_confidence": self.mean_predicted_confidence,
            "actual_success_rate": self.actual_success_rate,
            "actual_rate_lower_95": self.actual_rate_lower_95,
            "actual_rate_upper_95": self.actual_rate_upper_95,
            "calibration_gap": self.calibration_gap,
        }


@dataclass(frozen=True)
class CalibrationReport:
    domain: CalibrationDomain
    group_key: str | None
    bin_count: int
    sample_count: int
    unresolved_count: int
    mean_predicted_confidence: float | None
    actual_success_rate: float | None
    expected_calibration_error: float | None
    maximum_calibration_error: float | None
    brier_score: float | None
    bins: tuple[CalibrationBin, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "group_key": self.group_key,
            "bin_count": self.bin_count,
            "sample_count": self.sample_count,
            "unresolved_count": self.unresolved_count,
            "mean_predicted_confidence": self.mean_predicted_confidence,
            "actual_success_rate": self.actual_success_rate,
            "expected_calibration_error": self.expected_calibration_error,
            "maximum_calibration_error": self.maximum_calibration_error,
            "brier_score": self.brier_score,
            "bins": [item.as_dict() for item in self.bins],
            "policy": {
                "curve": "fixed_equal_width_bins",
                "outcome": "binary_independently_retained_result",
                "automatic_rewrite": False,
            },
        }


@dataclass(frozen=True)
class ConfidenceInterpretation:
    domain: CalibrationDomain
    group_key: str | None
    raw_confidence: float
    interpreted_confidence: float | None
    status: Literal["empirically_adjusted", "insufficient_evidence"]
    sample_count: int
    minimum_samples: int
    lower_bound: float
    upper_bound: float
    actual_success_rate: float | None
    actual_rate_lower_95: float | None
    actual_rate_upper_95: float | None
    adjustment_delta: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "group_key": self.group_key,
            "raw_confidence": self.raw_confidence,
            "interpreted_confidence": self.interpreted_confidence,
            "status": self.status,
            "sample_count": self.sample_count,
            "minimum_samples": self.minimum_samples,
            "lower_bound": self.lower_bound,
            "upper_bound": self.upper_bound,
            "actual_success_rate": self.actual_success_rate,
            "actual_rate_lower_95": self.actual_rate_lower_95,
            "actual_rate_upper_95": self.actual_rate_upper_95,
            "adjustment_delta": self.adjustment_delta,
            "stored_confidence_changed": False,
        }


class ConfidenceCalibration:
    """Retains forecasts and compares them with later binary outcomes."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def record_prediction(
        self,
        domain: CalibrationDomain,
        source_id: str,
        predicted_confidence: float,
        *,
        group_key: str = "all",
        evidence: tuple[str, ...] = (),
        commit: bool = True,
    ) -> str:
        domain = _domain(domain)
        source_id = _nonempty(source_id, "source_id")
        group_key = _nonempty(group_key, "group_key")
        if any(not item.strip() for item in evidence):
            raise ValueError("calibration evidence cannot contain empty values")
        prediction_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO confidence_predictions (
                id, domain, source_id, group_key, predicted_confidence,
                actual_outcome, evidence_json, outcome_evidence_json,
                created_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, '[]', ?, NULL)
            """,
            (
                prediction_id,
                domain,
                source_id,
                group_key,
                _confidence(predicted_confidence),
                json.dumps(sorted(set(evidence))),
                _utc_now(),
            ),
        )
        if commit:
            self.connection.commit()
        return prediction_id

    def resolve(
        self,
        domain: CalibrationDomain,
        source_id: str,
        actual_outcome: bool,
        *,
        evidence: tuple[str, ...],
        commit: bool = True,
    ) -> None:
        domain = _domain(domain)
        if not isinstance(actual_outcome, bool):
            raise ValueError("actual_outcome must be a boolean")
        if not evidence or any(not item.strip() for item in evidence):
            raise ValueError("outcome evidence must contain non-empty values")
        cursor = self.connection.execute(
            """
            UPDATE confidence_predictions
            SET actual_outcome = ?, outcome_evidence_json = ?, resolved_at = ?
            WHERE domain = ? AND source_id = ? AND actual_outcome IS NULL
            """,
            (
                int(actual_outcome),
                json.dumps(sorted(set(evidence))),
                _utc_now(),
                domain,
                _nonempty(source_id, "source_id"),
            ),
        )
        if cursor.rowcount != 1:
            raise LookupError("Unknown or already resolved confidence prediction")
        if commit:
            self.connection.commit()

    def observe(
        self,
        domain: CalibrationDomain,
        source_id: str,
        predicted_confidence: float,
        actual_outcome: bool,
        *,
        group_key: str = "all",
        evidence: tuple[str, ...] = (),
        commit: bool = True,
    ) -> str:
        if not isinstance(actual_outcome, bool):
            raise ValueError("actual_outcome must be a boolean")
        prediction_id = self.record_prediction(
            domain,
            source_id,
            predicted_confidence,
            group_key=group_key,
            evidence=evidence,
            commit=False,
        )
        self.resolve(
            domain,
            source_id,
            actual_outcome,
            evidence=evidence or ("direct_observation",),
            commit=False,
        )
        if commit:
            self.connection.commit()
        return prediction_id

    def report(
        self,
        domain: CalibrationDomain,
        *,
        group_key: str | None = None,
        bins: int = 10,
    ) -> CalibrationReport:
        domain = _domain(domain)
        if not 2 <= bins <= 100:
            raise ValueError("bins must be between 2 and 100")
        where = "domain = ?"
        params: list[object] = [domain]
        if group_key is not None:
            where += " AND group_key = ?"
            params.append(_nonempty(group_key, "group_key"))
        rows = self.connection.execute(
            f"""
            SELECT predicted_confidence, actual_outcome
            FROM confidence_predictions
            WHERE {where} AND actual_outcome IS NOT NULL
            ORDER BY created_at, id
            """,
            params,
        ).fetchall()
        unresolved = int(
            self.connection.execute(
                f"""
                SELECT COUNT(*) FROM confidence_predictions
                WHERE {where} AND actual_outcome IS NULL
                """,
                params,
            ).fetchone()[0]
        )
        buckets: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
        for row in rows:
            predicted = float(row["predicted_confidence"])
            index = min(int(predicted * bins), bins - 1)
            buckets[index].append((predicted, int(row["actual_outcome"])))
        curve: list[CalibrationBin] = []
        for index, samples in enumerate(buckets):
            lower = index / bins
            upper = (index + 1) / bins
            if samples:
                mean = sum(item[0] for item in samples) / len(samples)
                successes = sum(item[1] for item in samples)
                actual = successes / len(samples)
                interval_lower, interval_upper = _wilson_interval(
                    successes, len(samples)
                )
                gap = abs(mean - actual)
            else:
                mean = actual = interval_lower = interval_upper = gap = None
            curve.append(
                CalibrationBin(
                    lower,
                    upper,
                    len(samples),
                    mean,
                    actual,
                    interval_lower,
                    interval_upper,
                    gap,
                )
            )
        count = len(rows)
        if count:
            mean_predicted = sum(float(row[0]) for row in rows) / count
            actual_rate = sum(int(row[1]) for row in rows) / count
            ece = sum(
                item.sample_count * (item.calibration_gap or 0.0)
                for item in curve
            ) / count
            mce = max(
                item.calibration_gap or 0.0
                for item in curve
                if item.sample_count
            )
            brier = sum(
                (float(row[0]) - int(row[1])) ** 2 for row in rows
            ) / count
        else:
            mean_predicted = actual_rate = ece = mce = brier = None
        return CalibrationReport(
            domain=domain,
            group_key=group_key,
            bin_count=bins,
            sample_count=count,
            unresolved_count=unresolved,
            mean_predicted_confidence=mean_predicted,
            actual_success_rate=actual_rate,
            expected_calibration_error=ece,
            maximum_calibration_error=mce,
            brier_score=brier,
            bins=tuple(curve),
        )

    def interpret(
        self,
        domain: CalibrationDomain,
        confidence: float,
        *,
        group_key: str | None = None,
        bins: int = 10,
        minimum_samples: int = 20,
    ) -> ConfidenceInterpretation:
        raw = _confidence(confidence)
        if minimum_samples < 1:
            raise ValueError("minimum_samples must be positive")
        report = self.report(domain, group_key=group_key, bins=bins)
        index = min(int(raw * bins), bins - 1)
        selected = report.bins[index]
        sufficient = selected.sample_count >= minimum_samples
        return ConfidenceInterpretation(
            domain=report.domain,
            group_key=group_key,
            raw_confidence=raw,
            interpreted_confidence=(
                selected.actual_success_rate if sufficient else None
            ),
            status=(
                "empirically_adjusted" if sufficient
                else "insufficient_evidence"
            ),
            sample_count=selected.sample_count,
            minimum_samples=minimum_samples,
            lower_bound=selected.lower_bound,
            upper_bound=selected.upper_bound,
            actual_success_rate=selected.actual_success_rate,
            actual_rate_lower_95=selected.actual_rate_lower_95,
            actual_rate_upper_95=selected.actual_rate_upper_95,
            adjustment_delta=(
                selected.actual_success_rate - raw
                if sufficient and selected.actual_success_rate is not None
                else None
            ),
        )
