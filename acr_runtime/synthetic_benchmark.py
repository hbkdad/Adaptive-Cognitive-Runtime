from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import string
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

from .secret_management import SecretBoundaryError, assert_secret_free


MAX_JSON_BYTES = 65_536
MAX_CASES = 128
DIFFICULTIES = ("basic", "intermediate", "advanced")
REVIEW_DIMENSIONS = ("leakage", "triviality", "coverage")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_REFERENCE = re.compile(r"^[a-z][a-z0-9_.-]{0,31}:[^\s]{1,240}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SyntheticBenchmarkError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _closed(
    payload: object, fields: set[str], label: str
) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise SyntheticBenchmarkError(f"{label} must be an object")
    unknown = set(payload) - fields
    if unknown:
        raise SyntheticBenchmarkError(
            f"{label} contains unknown fields: {sorted(unknown)}"
        )
    return payload


def _text(value: object, field: str, maximum: int = 4_000) -> str:
    if not isinstance(value, str):
        raise SyntheticBenchmarkError(f"{field} must be text")
    normalized = value.strip()
    if not 1 <= len(normalized) <= maximum:
        raise SyntheticBenchmarkError(
            f"{field} must be 1..{maximum} characters"
        )
    try:
        assert_secret_free(normalized, f"synthetic benchmark {field}")
    except SecretBoundaryError as exc:
        raise SyntheticBenchmarkError(
            f"{field} contains secret material"
        ) from exc
    return normalized


def _identifier(value: object, field: str) -> str:
    normalized = _text(value, field, 64)
    if not _IDENTIFIER.fullmatch(normalized):
        raise SyntheticBenchmarkError(
            f"{field} must be a lowercase identifier"
        )
    return normalized


def _reference(value: object, field: str) -> str:
    normalized = _text(value, field, 240)
    if not _REFERENCE.fullmatch(normalized):
        raise SyntheticBenchmarkError(
            f"{field} must be a bounded type:value reference"
        )
    return normalized


def _evidence(
    value: object, field: str, *, minimum: int = 1
) -> tuple[str, ...]:
    if not isinstance(value, list) or not minimum <= len(value) <= 8:
        raise SyntheticBenchmarkError(
            f"{field} must contain {minimum}..8 references"
        )
    result = tuple(_reference(item, field) for item in value)
    if len(set(result)) != len(result):
        raise SyntheticBenchmarkError(f"{field} references must be unique")
    return result


def _json_object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise SyntheticBenchmarkError(f"{field} must be an object")
    normalized = dict(value)
    encoded = json.dumps(
        normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise SyntheticBenchmarkError(f"{field} exceeds the 64 KiB limit")
    try:
        assert_secret_free(encoded, f"synthetic benchmark {field}")
    except SecretBoundaryError as exc:
        raise SyntheticBenchmarkError(
            f"{field} contains secret material"
        ) from exc
    return normalized


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class SyntheticVariant:
    id: str
    value: str
    difficulty: str

    @classmethod
    def from_dict(cls, payload: object) -> "SyntheticVariant":
        fields = {"id", "value", "difficulty"}
        data = _closed(payload, fields, "synthetic variant")
        if set(data) != fields:
            raise SyntheticBenchmarkError(
                "synthetic variant requires the complete schema"
            )
        variant = cls(
            id=_identifier(data["id"], "variant.id"),
            value=_text(data["value"], "variant.value", 1_000),
            difficulty=_text(data["difficulty"], "variant.difficulty", 32),
        )
        if variant.difficulty not in DIFFICULTIES:
            raise SyntheticBenchmarkError("variant difficulty is unsupported")
        return variant

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "value": self.value,
            "difficulty": self.difficulty,
        }


@dataclass(frozen=True)
class SyntheticCapabilityClass:
    id: str
    description: str
    objective_template: str
    variants: tuple[SyntheticVariant, ...]
    evaluation_spec: Mapping[str, object]

    @classmethod
    def from_dict(cls, payload: object) -> "SyntheticCapabilityClass":
        fields = {
            "id",
            "description",
            "objective_template",
            "variants",
            "evaluation_spec",
        }
        data = _closed(payload, fields, "synthetic capability class")
        if set(data) != fields or not isinstance(data["variants"], list):
            raise SyntheticBenchmarkError(
                "synthetic capability class requires the complete schema"
            )
        template = _text(
            data["objective_template"], "objective_template", 4_000
        )
        parsed = list(string.Formatter().parse(template))
        field_names = [item[1] for item in parsed if item[1] is not None]
        if field_names != ["variant"]:
            raise SyntheticBenchmarkError(
                "objective_template must contain exactly one {variant} field"
            )
        variants = tuple(
            SyntheticVariant.from_dict(item) for item in data["variants"]
        )
        if not 2 <= len(variants) <= 16:
            raise SyntheticBenchmarkError(
                "each capability class requires 2..16 variants"
            )
        if len({item.id for item in variants}) != len(variants):
            raise SyntheticBenchmarkError("variant IDs must be unique")
        if len({item.value for item in variants}) != len(variants):
            raise SyntheticBenchmarkError("variant values must be unique")
        if len({item.difficulty for item in variants}) < 2:
            raise SyntheticBenchmarkError(
                "each capability class requires at least two difficulty levels"
            )
        result = cls(
            id=_identifier(data["id"], "capability_class.id"),
            description=_text(
                data["description"], "capability_class.description", 1_000
            ),
            objective_template=template,
            variants=variants,
            evaluation_spec=_json_object(
                data["evaluation_spec"], "evaluation_spec"
            ),
        )
        for variant in result.variants:
            objective = result.objective_template.format(variant=variant.value)
            if len(objective.strip()) < 40:
                raise SyntheticBenchmarkError(
                    "generated objectives must contain at least 40 characters"
                )
        return result

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "description": self.description,
            "objective_template": self.objective_template,
            "variants": [item.as_dict() for item in self.variants],
            "evaluation_spec": dict(self.evaluation_spec),
        }


@dataclass(frozen=True)
class SyntheticBenchmarkCreate:
    name: str
    generator_ref: str
    generator_version_hash: str
    seed: int
    capability_classes: tuple[SyntheticCapabilityClass, ...]
    evidence: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: object) -> "SyntheticBenchmarkCreate":
        fields = {
            "schema_version",
            "name",
            "generator_ref",
            "generator_version_hash",
            "seed",
            "capability_classes",
            "evidence",
        }
        data = _closed(payload, fields, "synthetic benchmark request")
        if (
            set(data) != fields
            or data.get("schema_version") != 1
            or not isinstance(data["capability_classes"], list)
            or not isinstance(data["evidence"], list)
        ):
            raise SyntheticBenchmarkError(
                "synthetic benchmark request requires the complete version 1 schema"
            )
        classes = tuple(
            SyntheticCapabilityClass.from_dict(item)
            for item in data["capability_classes"]
        )
        if not 2 <= len(classes) <= 16:
            raise SyntheticBenchmarkError(
                "synthetic benchmarks require 2..16 capability classes"
            )
        if len({item.id for item in classes}) != len(classes):
            raise SyntheticBenchmarkError(
                "capability class IDs must be unique"
            )
        case_count = sum(len(item.variants) for item in classes)
        if not 4 <= case_count <= MAX_CASES:
            raise SyntheticBenchmarkError(
                f"synthetic benchmarks require 4..{MAX_CASES} cases"
            )
        version_hash = data["generator_version_hash"]
        if not isinstance(version_hash, str) or not _SHA256.fullmatch(
            version_hash
        ):
            raise SyntheticBenchmarkError(
                "generator_version_hash must be a lowercase SHA-256"
            )
        seed = data["seed"]
        if (
            type(seed) is not int
            or not 0 <= seed <= 2_147_483_647
        ):
            raise SyntheticBenchmarkError(
                "seed must be between 0 and 2147483647"
            )
        return cls(
            name=_text(data["name"], "name", 240),
            generator_ref=_reference(data["generator_ref"], "generator_ref"),
            generator_version_hash=version_hash,
            seed=seed,
            capability_classes=classes,
            evidence=_evidence(data["evidence"], "evidence"),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "name": self.name,
            "generator_ref": self.generator_ref,
            "generator_version_hash": self.generator_version_hash,
            "seed": self.seed,
            "capability_classes": [
                item.as_dict() for item in self.capability_classes
            ],
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class SyntheticReviewAssessment:
    status: str
    rationale: str
    evidence: tuple[str, ...]

    @classmethod
    def from_dict(
        cls, payload: object, dimension: str
    ) -> "SyntheticReviewAssessment":
        fields = {"status", "rationale", "evidence"}
        data = _closed(payload, fields, f"{dimension} assessment")
        if set(data) != fields or not isinstance(data["evidence"], list):
            raise SyntheticBenchmarkError(
                f"{dimension} assessment requires the complete schema"
            )
        status = _text(data["status"], f"{dimension}.status", 16)
        if status not in {"passed", "failed"}:
            raise SyntheticBenchmarkError(
                f"{dimension} status must be passed or failed"
            )
        return cls(
            status=status,
            rationale=_text(
                data["rationale"], f"{dimension}.rationale", 2_000
            ),
            evidence=_evidence(
                data["evidence"], f"{dimension}.evidence"
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "rationale": self.rationale,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class SyntheticBenchmarkReviewCreate:
    suite_id: str
    suite_hash: str
    reviewer_ref: str
    assessments: Mapping[str, SyntheticReviewAssessment]
    real_task_evidence: tuple[str, ...]
    evidence: tuple[str, ...]

    @classmethod
    def from_dict(
        cls, payload: object
    ) -> "SyntheticBenchmarkReviewCreate":
        fields = {
            "schema_version",
            "suite_id",
            "suite_hash",
            "reviewer_ref",
            "assessments",
            "real_task_evidence",
            "evidence",
        }
        data = _closed(payload, fields, "synthetic benchmark review")
        assessments = data.get("assessments")
        if (
            set(data) != fields
            or data.get("schema_version") != 1
            or not isinstance(assessments, Mapping)
            or set(assessments) != set(REVIEW_DIMENSIONS)
            or not isinstance(data["real_task_evidence"], list)
            or not isinstance(data["evidence"], list)
        ):
            raise SyntheticBenchmarkError(
                "synthetic benchmark review requires the complete version 1 schema"
            )
        suite_hash = data["suite_hash"]
        if not isinstance(suite_hash, str) or not _SHA256.fullmatch(suite_hash):
            raise SyntheticBenchmarkError(
                "suite_hash must be a lowercase SHA-256"
            )
        reviewer_ref = _reference(data["reviewer_ref"], "reviewer_ref")
        if not reviewer_ref.startswith("human:"):
            raise SyntheticBenchmarkError(
                "reviewer_ref must identify an explicit human reviewer"
            )
        return cls(
            suite_id=_text(data["suite_id"], "suite_id", 128),
            suite_hash=suite_hash,
            reviewer_ref=reviewer_ref,
            assessments={
                dimension: SyntheticReviewAssessment.from_dict(
                    assessments[dimension], dimension
                )
                for dimension in REVIEW_DIMENSIONS
            },
            real_task_evidence=_evidence(
                data["real_task_evidence"],
                "real_task_evidence",
                minimum=0,
            ),
            evidence=_evidence(data["evidence"], "evidence"),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "suite_id": self.suite_id,
            "suite_hash": self.suite_hash,
            "reviewer_ref": self.reviewer_ref,
            "assessments": {
                key: value.as_dict()
                for key, value in self.assessments.items()
            },
            "real_task_evidence": list(self.real_task_evidence),
            "evidence": list(self.evidence),
        }


class SyntheticBenchmarkController:
    """Own deterministic synthetic suites and explicit immutable reviews."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        mutation_guard: Callable[[str], None] | None = None,
    ) -> None:
        self.connection = connection
        self.mutation_guard = mutation_guard

    def generate(
        self, request: SyntheticBenchmarkCreate
    ) -> dict[str, object]:
        self._guard("synthetic_benchmark_write")
        request_payload = request.as_dict()
        request_hash = _digest(request_payload)
        existing = self.connection.execute(
            "SELECT id FROM synthetic_benchmark_suites WHERE request_hash=?",
            (request_hash,),
        ).fetchone()
        if existing is not None:
            return self.report(str(existing["id"]))

        generated: list[dict[str, object]] = []
        for capability in request.capability_classes:
            for variant in capability.variants:
                objective = capability.objective_template.format(
                    variant=variant.value
                ).strip()
                evaluation = dict(capability.evaluation_spec)
                generated.append(
                    {
                        "capability_class": capability.id,
                        "variant_id": variant.id,
                        "difficulty": variant.difficulty,
                        "objective": objective,
                        "objective_hash": _digest(objective),
                        "evaluation_spec": evaluation,
                        "evaluation_hash": _digest(evaluation),
                    }
                )
        generated.sort(
            key=lambda item: _digest(
                {
                    "seed": request.seed,
                    "class": item["capability_class"],
                    "variant": item["variant_id"],
                }
            )
        )
        objective_hashes = [str(item["objective_hash"]) for item in generated]
        if len(set(objective_hashes)) != len(objective_hashes):
            raise SyntheticBenchmarkError(
                "generated objectives must be unique across the suite"
            )
        suite_hash = _digest(
            {
                "request_hash": request_hash,
                "cases": generated,
                "origin": "synthetic",
            }
        )
        suite_id = str(uuid.uuid4())
        now = _now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO synthetic_benchmark_suites(
                    id, request_hash, name, generator_ref,
                    generator_version_hash, seed, capability_class_count,
                    case_count, suite_hash, evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    suite_id,
                    request_hash,
                    request.name,
                    request.generator_ref,
                    request.generator_version_hash,
                    request.seed,
                    len(request.capability_classes),
                    len(generated),
                    suite_hash,
                    json.dumps(request.evidence, separators=(",", ":")),
                    now,
                ),
            )
            for sequence, item in enumerate(generated, start=1):
                case_hash = _digest(
                    {
                        "suite_hash": suite_hash,
                        "sequence": sequence,
                        **item,
                    }
                )
                self.connection.execute(
                    """
                    INSERT INTO synthetic_benchmark_cases(
                        id, suite_id, sequence, capability_class,
                        variant_id, difficulty, objective, objective_hash,
                        evaluation_json, evaluation_hash, case_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        suite_id,
                        sequence,
                        item["capability_class"],
                        item["variant_id"],
                        item["difficulty"],
                        item["objective"],
                        item["objective_hash"],
                        json.dumps(
                            item["evaluation_spec"],
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        item["evaluation_hash"],
                        case_hash,
                        now,
                    ),
                )
        return self.report(suite_id)

    def report(self, suite_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM synthetic_benchmark_suites WHERE id=?",
            (suite_id,),
        ).fetchone()
        if row is None:
            raise SyntheticBenchmarkError(
                f"unknown synthetic benchmark suite: {suite_id}"
            )
        cases = self.connection.execute(
            """
            SELECT sequence, capability_class, variant_id, difficulty,
                   objective, objective_hash, evaluation_json,
                   evaluation_hash, case_hash
            FROM synthetic_benchmark_cases
            WHERE suite_id=?
            ORDER BY sequence
            """,
            (suite_id,),
        ).fetchall()
        review = self.connection.execute(
            "SELECT id FROM synthetic_benchmark_reviews WHERE suite_id=?",
            (suite_id,),
        ).fetchone()
        return {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "origin": "synthetic",
            "content_trust": "generated_untrusted_evaluation_data",
            "request_hash": str(row["request_hash"]),
            "generator_ref": str(row["generator_ref"]),
            "generator_version_hash": str(row["generator_version_hash"]),
            "seed": int(row["seed"]),
            "capability_class_count": int(row["capability_class_count"]),
            "case_count": int(row["case_count"]),
            "suite_hash": str(row["suite_hash"]),
            "evidence": list(json.loads(row["evidence_json"])),
            "review_id": None if review is None else str(review["id"]),
            "review_required": review is None,
            "cases": [
                {
                    "sequence": int(case["sequence"]),
                    "capability_class": str(case["capability_class"]),
                    "variant_id": str(case["variant_id"]),
                    "difficulty": str(case["difficulty"]),
                    "objective": str(case["objective"]),
                    "objective_hash": str(case["objective_hash"]),
                    "evaluation_spec": json.loads(case["evaluation_json"]),
                    "evaluation_hash": str(case["evaluation_hash"]),
                    "case_hash": str(case["case_hash"]),
                    "origin": "synthetic",
                }
                for case in cases
            ],
            "historical_task_rows_used": 0,
            "promotion_authority": False,
            "deployment_authority": False,
            "created_at": str(row["created_at"]),
        }

    def review(
        self, request: SyntheticBenchmarkReviewCreate
    ) -> dict[str, object]:
        self._guard("synthetic_benchmark_review")
        suite = self.connection.execute(
            "SELECT suite_hash FROM synthetic_benchmark_suites WHERE id=?",
            (request.suite_id,),
        ).fetchone()
        if suite is None:
            raise SyntheticBenchmarkError(
                f"unknown synthetic benchmark suite: {request.suite_id}"
            )
        if str(suite["suite_hash"]) != request.suite_hash:
            raise SyntheticBenchmarkError(
                "review suite_hash does not match the immutable suite"
            )
        request_hash = _digest(request.as_dict())
        existing = self.connection.execute(
            "SELECT id, request_hash FROM synthetic_benchmark_reviews WHERE suite_id=?",
            (request.suite_id,),
        ).fetchone()
        if existing is not None:
            if str(existing["request_hash"]) != request_hash:
                raise SyntheticBenchmarkError(
                    "synthetic benchmark suite already has a different review"
                )
            return self.review_report(str(existing["id"]))
        all_passed = all(
            item.status == "passed" for item in request.assessments.values()
        )
        accepted = all_passed and bool(request.real_task_evidence)
        review_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO synthetic_benchmark_reviews(
                    id, suite_id, request_hash, suite_hash, reviewer_ref,
                    assessments_json, real_task_evidence_json, evidence_json,
                    accepted, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    request.suite_id,
                    request_hash,
                    request.suite_hash,
                    request.reviewer_ref,
                    json.dumps(
                        {
                            key: value.as_dict()
                            for key, value in request.assessments.items()
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    json.dumps(
                        request.real_task_evidence, separators=(",", ":")
                    ),
                    json.dumps(request.evidence, separators=(",", ":")),
                    int(accepted),
                    _now(),
                ),
            )
        return self.review_report(review_id)

    def review_report(self, review_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM synthetic_benchmark_reviews WHERE id=?",
            (review_id,),
        ).fetchone()
        if row is None:
            raise SyntheticBenchmarkError(
                f"unknown synthetic benchmark review: {review_id}"
            )
        real_evidence = list(json.loads(row["real_task_evidence_json"]))
        accepted = bool(row["accepted"])
        return {
            "id": str(row["id"]),
            "suite_id": str(row["suite_id"]),
            "request_hash": str(row["request_hash"]),
            "suite_hash": str(row["suite_hash"]),
            "reviewer_ref": str(row["reviewer_ref"]),
            "assessments": json.loads(row["assessments_json"]),
            "real_task_evidence": real_evidence,
            "evidence": list(json.loads(row["evidence_json"])),
            "accepted_for_synthetic_evaluation": accepted,
            "real_task_gate_satisfied": bool(real_evidence),
            "review_complete": True,
            "synthetic_only": True,
            "promotion_authority": False,
            "deployment_authority": False,
            "created_at": str(row["created_at"]),
        }

    def _guard(self, action: str) -> None:
        if self.mutation_guard is not None:
            self.mutation_guard(action)
