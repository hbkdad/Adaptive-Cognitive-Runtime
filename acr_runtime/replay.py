from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Mapping, Protocol

from .secret_management import SecretBoundaryError, assert_secret_free


MAX_JSON_BYTES = 65_536
_REFERENCE = re.compile(r"^[a-z][a-z0-9_.-]{0,31}:[^\s]{1,240}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ReplayError(ValueError):
    pass


class ReplayTargetKind(str, Enum):
    MODEL = "model"
    SKILL = "skill"
    ROUTER = "router"
    CONTEXT_ALGORITHM = "context_algorithm"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _closed(
    payload: object, fields: set[str], label: str
) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise ReplayError(f"{label} must be an object")
    unknown = set(payload) - fields
    if unknown:
        raise ReplayError(f"{label} contains unknown fields: {sorted(unknown)}")
    return payload


def _text(value: object, field: str, maximum: int = 240) -> str:
    if not isinstance(value, str):
        raise ReplayError(f"{field} must be text")
    normalized = value.strip()
    if not 1 <= len(normalized) <= maximum:
        raise ReplayError(f"{field} must be 1..{maximum} characters")
    try:
        assert_secret_free(normalized, f"replay {field}")
    except SecretBoundaryError as exc:
        raise ReplayError(f"{field} contains secret material") from exc
    return normalized


def _json_object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ReplayError(f"{field} must be an object")
    normalized = dict(value)
    encoded = json.dumps(
        normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise ReplayError(f"{field} exceeds the 64 KiB limit")
    try:
        assert_secret_free(encoded, f"replay {field}")
    except SecretBoundaryError as exc:
        raise ReplayError(f"{field} contains secret material") from exc
    return normalized


def _evidence(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise ReplayError("evidence must contain 1..8 references")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _REFERENCE.fullmatch(item):
            raise ReplayError("evidence entries must be bounded type:value references")
        try:
            assert_secret_free(item, "replay evidence")
        except SecretBoundaryError as exc:
            raise ReplayError("evidence contains secret material") from exc
        result.append(item)
    if len(set(result)) != len(result):
        raise ReplayError("evidence references must be unique")
    return tuple(result)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class ReplayCaseCreate:
    source_task_id: str
    input_payload: Mapping[str, object]
    evaluation_spec: Mapping[str, object]
    privacy_class: str
    privacy_permission_ref: str | None
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_task_id", _text(self.source_task_id, "source_task_id", 128)
        )
        object.__setattr__(
            self, "input_payload", _json_object(self.input_payload, "input")
        )
        object.__setattr__(
            self,
            "evaluation_spec",
            _json_object(self.evaluation_spec, "evaluation_spec"),
        )
        if self.privacy_class not in {"public", "internal"}:
            raise ReplayError(
                "replay v1 permits only public or internal sanitized inputs"
            )
        permission = self.privacy_permission_ref
        if permission is not None:
            permission = _text(permission, "privacy_permission_ref", 240)
            if not _REFERENCE.fullmatch(permission):
                raise ReplayError(
                    "privacy_permission_ref must be a type:value reference"
                )
        object.__setattr__(self, "privacy_permission_ref", permission)
        object.__setattr__(self, "evidence", _evidence(list(self.evidence)))

    @classmethod
    def from_dict(cls, payload: object) -> "ReplayCaseCreate":
        fields = {
            "schema_version",
            "source_task_id",
            "input",
            "evaluation_spec",
            "privacy_class",
            "privacy_permission_ref",
            "evidence",
        }
        data = _closed(payload, fields, "replay case")
        if data.get("schema_version") != 1 or set(data) != fields:
            raise ReplayError("replay case requires the complete version 1 schema")
        if not isinstance(data["evidence"], list):
            raise ReplayError("evidence must be a list")
        return cls(
            source_task_id=data["source_task_id"],
            input_payload=data["input"],
            evaluation_spec=data["evaluation_spec"],
            privacy_class=data["privacy_class"],
            privacy_permission_ref=data["privacy_permission_ref"],
            evidence=tuple(data["evidence"]),
        )


@dataclass(frozen=True)
class ReplayCase:
    id: str
    source_task_id: str
    input_hash: str
    evaluation_hash: str
    privacy_class: str
    privacy_permission_ref: str | None
    evidence: tuple[str, ...]
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "source_task_id": self.source_task_id,
            "input_hash": self.input_hash,
            "evaluation_hash": self.evaluation_hash,
            "privacy_class": self.privacy_class,
            "privacy_permission_ref": self.privacy_permission_ref,
            "evidence": list(self.evidence),
            "immutable_input_retained": True,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ReplayRequest:
    case_id: str
    target_kind: ReplayTargetKind
    target_ref: str
    target_version_hash: str
    evaluator_ref: str
    seed: int
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _text(self.case_id, "case_id", 128))
        if not isinstance(self.target_kind, ReplayTargetKind):
            try:
                object.__setattr__(
                    self, "target_kind", ReplayTargetKind(self.target_kind)
                )
            except (TypeError, ValueError) as exc:
                raise ReplayError("unsupported replay target kind") from exc
        object.__setattr__(
            self, "target_ref", _text(self.target_ref, "target_ref", 240)
        )
        if (
            not isinstance(self.target_version_hash, str)
            or not _SHA256.fullmatch(self.target_version_hash)
        ):
            raise ReplayError("target_version_hash must be a lowercase SHA-256")
        object.__setattr__(
            self, "evaluator_ref", _text(self.evaluator_ref, "evaluator_ref", 240)
        )
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise ReplayError("seed must be an integer")
        if not 0 <= self.seed <= 2_147_483_647:
            raise ReplayError("seed must be between 0 and 2147483647")
        object.__setattr__(self, "evidence", _evidence(list(self.evidence)))

    @classmethod
    def from_dict(cls, payload: object) -> "ReplayRequest":
        fields = {
            "schema_version",
            "case_id",
            "target_kind",
            "target_ref",
            "target_version_hash",
            "evaluator_ref",
            "seed",
            "evidence",
        }
        data = _closed(payload, fields, "replay request")
        if data.get("schema_version") != 1 or set(data) != fields:
            raise ReplayError("replay request requires the complete version 1 schema")
        if not isinstance(data["evidence"], list):
            raise ReplayError("evidence must be a list")
        return cls(
            case_id=data["case_id"],
            target_kind=data["target_kind"],
            target_ref=data["target_ref"],
            target_version_hash=data["target_version_hash"],
            evaluator_ref=data["evaluator_ref"],
            seed=data["seed"],
            evidence=tuple(data["evidence"]),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "case_id": self.case_id,
            "target_kind": self.target_kind.value,
            "target_ref": self.target_ref,
            "target_version_hash": self.target_version_hash,
            "evaluator_ref": self.evaluator_ref,
            "seed": self.seed,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class ReplayContext:
    case_id: str
    source_task_id: str
    input_payload: Mapping[str, object]
    evaluation_spec: Mapping[str, object]
    privacy_class: str
    target_kind: ReplayTargetKind
    target_ref: str
    target_version_hash: str
    evaluator_ref: str
    seed: int
    external_network_allowed: bool = False
    side_effects_allowed: bool = False
    deployment_allowed: bool = False


@dataclass(frozen=True)
class ReplayObservation:
    success: bool
    quality_micros: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_micros: int
    output_hash: str
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.success, bool):
            raise ReplayError("observation success must be boolean")
        for field, maximum in (
            ("quality_micros", 1_000_000),
            ("input_tokens", 100_000_000),
            ("output_tokens", 100_000_000),
            ("latency_ms", 86_400_000),
            ("cost_micros", 100_000_000_000),
        ):
            value = getattr(self, field)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ReplayError(f"{field} must be an integer")
            if not 0 <= value <= maximum:
                raise ReplayError(f"{field} is outside the supported range")
        if not isinstance(self.output_hash, str) or not _SHA256.fullmatch(
            self.output_hash
        ):
            raise ReplayError("output_hash must be a lowercase SHA-256")
        object.__setattr__(self, "evidence", _evidence(list(self.evidence)))


class ReplayAdapter(Protocol):
    def identity(self) -> Mapping[str, object]: ...

    def run(self, context: ReplayContext) -> ReplayObservation: ...


class UnavailableReplayAdapter:
    def identity(self) -> Mapping[str, object]:
        return {"available": False}

    def run(self, context: ReplayContext) -> ReplayObservation:
        raise RuntimeError("replay adapter is unavailable")


@dataclass(frozen=True)
class ReplayRun:
    id: str
    case_id: str
    request_hash: str
    target_kind: str
    target_ref: str
    target_version_hash: str
    evaluator_ref: str
    adapter_identity_hash: str
    seed: int
    input_hash: str
    evaluation_hash: str
    success: bool
    quality_micros: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_micros: int
    output_hash: str
    evidence: tuple[str, ...]
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "case_id": self.case_id,
            "request_hash": self.request_hash,
            "target_kind": self.target_kind,
            "target_ref": self.target_ref,
            "target_version_hash": self.target_version_hash,
            "evaluator_ref": self.evaluator_ref,
            "adapter_identity_hash": self.adapter_identity_hash,
            "seed": self.seed,
            "input_hash": self.input_hash,
            "evaluation_hash": self.evaluation_hash,
            "metrics": {
                "success": self.success,
                "quality_micros": self.quality_micros,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.input_tokens + self.output_tokens,
                "latency_ms": self.latency_ms,
                "cost_micros": self.cost_micros,
            },
            "output_hash": self.output_hash,
            "evidence": list(self.evidence),
            "offline_evaluation_only": True,
            "promotion_authority": False,
            "deployment_authority": False,
            "created_at": self.created_at,
        }


class ReplayEngine:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        mutation_guard: Callable[[str], None] | None = None,
    ) -> None:
        self.connection = connection
        self.mutation_guard = mutation_guard

    def add_case(self, request: ReplayCaseCreate) -> ReplayCase:
        self._guard("replay_case_write")
        task = self.connection.execute(
            "SELECT status, completed_at FROM tasks WHERE id=?",
            (request.source_task_id,),
        ).fetchone()
        if (
            task is None
            or task["status"] not in {"succeeded", "failed"}
            or task["completed_at"] is None
        ):
            raise ReplayError("replay cases require a completed source task")
        input_json = json.dumps(
            request.input_payload, sort_keys=True, separators=(",", ":")
        )
        evaluation_json = json.dumps(
            request.evaluation_spec, sort_keys=True, separators=(",", ":")
        )
        input_hash = hashlib.sha256(input_json.encode("utf-8")).hexdigest()
        evaluation_hash = hashlib.sha256(
            evaluation_json.encode("utf-8")
        ).hexdigest()
        existing = self.connection.execute(
            """
            SELECT id, privacy_class, privacy_permission_ref, evidence_json
            FROM replay_cases
            WHERE source_task_id=? AND input_hash=? AND evaluation_hash=?
            """,
            (request.source_task_id, input_hash, evaluation_hash),
        ).fetchone()
        if existing is not None:
            if (
                existing["privacy_class"] != request.privacy_class
                or existing["privacy_permission_ref"]
                != request.privacy_permission_ref
                or tuple(json.loads(existing["evidence_json"]))
                != request.evidence
            ):
                raise ReplayError(
                    "replay case input already exists with different provenance"
                )
            return self.case(str(existing["id"]))
        case_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO replay_cases(
                id, source_task_id, input_json, input_hash, evaluation_json,
                evaluation_hash, privacy_class, privacy_permission_ref,
                evidence_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                case_id,
                request.source_task_id,
                input_json,
                input_hash,
                evaluation_json,
                evaluation_hash,
                request.privacy_class,
                request.privacy_permission_ref,
                json.dumps(request.evidence, separators=(",", ":")),
                _now(),
            ),
        )
        self.connection.commit()
        return self.case(case_id)

    def case(self, case_id: str) -> ReplayCase:
        row = self.connection.execute(
            "SELECT * FROM replay_cases WHERE id=?", (case_id,)
        ).fetchone()
        if row is None:
            raise ReplayError(f"unknown replay case: {case_id}")
        return ReplayCase(
            id=str(row["id"]),
            source_task_id=str(row["source_task_id"]),
            input_hash=str(row["input_hash"]),
            evaluation_hash=str(row["evaluation_hash"]),
            privacy_class=str(row["privacy_class"]),
            privacy_permission_ref=row["privacy_permission_ref"],
            evidence=tuple(json.loads(row["evidence_json"])),
            created_at=str(row["created_at"]),
        )

    def run(
        self, request: ReplayRequest, adapter: ReplayAdapter
    ) -> ReplayRun:
        self._guard("replay_run_write")
        identity = self._adapter_identity(adapter)
        identity_hash = _digest(identity)
        row = self.connection.execute(
            "SELECT * FROM replay_cases WHERE id=?", (request.case_id,)
        ).fetchone()
        if row is None:
            raise ReplayError(f"unknown replay case: {request.case_id}")
        request_hash = _digest(
            {
                "request": request.as_dict(),
                "adapter_identity_hash": identity_hash,
                "input_hash": row["input_hash"],
                "evaluation_hash": row["evaluation_hash"],
            }
        )
        existing = self.connection.execute(
            "SELECT id FROM replay_runs WHERE request_hash=?", (request_hash,)
        ).fetchone()
        if existing is not None:
            return self.report(str(existing["id"]))
        context = ReplayContext(
            case_id=request.case_id,
            source_task_id=str(row["source_task_id"]),
            input_payload=json.loads(row["input_json"]),
            evaluation_spec=json.loads(row["evaluation_json"]),
            privacy_class=str(row["privacy_class"]),
            target_kind=request.target_kind,
            target_ref=request.target_ref,
            target_version_hash=request.target_version_hash,
            evaluator_ref=request.evaluator_ref,
            seed=request.seed,
        )
        observation = adapter.run(context)
        if not isinstance(observation, ReplayObservation):
            raise ReplayError("replay adapter returned an invalid observation")
        run_id = str(uuid.uuid4())
        now = _now()
        self.connection.execute(
            """
            INSERT INTO replay_runs(
                id, case_id, request_hash, target_kind, target_ref,
                target_version_hash, evaluator_ref, adapter_identity_hash,
                seed, input_hash, evaluation_hash, success, quality_micros,
                input_tokens, output_tokens, latency_ms, cost_micros,
                output_hash, evidence_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                request.case_id,
                request_hash,
                request.target_kind.value,
                request.target_ref,
                request.target_version_hash,
                request.evaluator_ref,
                identity_hash,
                request.seed,
                row["input_hash"],
                row["evaluation_hash"],
                int(observation.success),
                observation.quality_micros,
                observation.input_tokens,
                observation.output_tokens,
                observation.latency_ms,
                observation.cost_micros,
                observation.output_hash,
                json.dumps(
                    tuple(request.evidence) + tuple(observation.evidence),
                    separators=(",", ":"),
                ),
                now,
            ),
        )
        self.connection.commit()
        return self.report(run_id)

    def report(self, run_id: str) -> ReplayRun:
        row = self.connection.execute(
            "SELECT * FROM replay_runs WHERE id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise ReplayError(f"unknown replay run: {run_id}")
        return ReplayRun(
            id=str(row["id"]),
            case_id=str(row["case_id"]),
            request_hash=str(row["request_hash"]),
            target_kind=str(row["target_kind"]),
            target_ref=str(row["target_ref"]),
            target_version_hash=str(row["target_version_hash"]),
            evaluator_ref=str(row["evaluator_ref"]),
            adapter_identity_hash=str(row["adapter_identity_hash"]),
            seed=int(row["seed"]),
            input_hash=str(row["input_hash"]),
            evaluation_hash=str(row["evaluation_hash"]),
            success=bool(row["success"]),
            quality_micros=int(row["quality_micros"]),
            input_tokens=int(row["input_tokens"]),
            output_tokens=int(row["output_tokens"]),
            latency_ms=int(row["latency_ms"]),
            cost_micros=int(row["cost_micros"]),
            output_hash=str(row["output_hash"]),
            evidence=tuple(json.loads(row["evidence_json"])),
            created_at=str(row["created_at"]),
        )

    def compare(self, baseline_id: str, candidate_id: str) -> dict[str, object]:
        baseline = self.report(baseline_id)
        candidate = self.report(candidate_id)
        if (
            baseline.case_id != candidate.case_id
            or baseline.input_hash != candidate.input_hash
            or baseline.evaluation_hash != candidate.evaluation_hash
            or baseline.evaluator_ref != candidate.evaluator_ref
        ):
            raise ReplayError(
                "replay comparison requires the same case input and evaluator"
            )
        return {
            "baseline_run_id": baseline.id,
            "candidate_run_id": candidate.id,
            "case_id": baseline.case_id,
            "evaluator_ref": baseline.evaluator_ref,
            "delta": {
                "quality_micros": (
                    candidate.quality_micros - baseline.quality_micros
                ),
                "total_tokens": (
                    candidate.input_tokens
                    + candidate.output_tokens
                    - baseline.input_tokens
                    - baseline.output_tokens
                ),
                "latency_ms": candidate.latency_ms - baseline.latency_ms,
                "cost_micros": candidate.cost_micros - baseline.cost_micros,
            },
            "paired_offline_observation_only": True,
            "causal_claim": False,
            "promotion_authority": False,
            "deployment_authority": False,
        }

    def _guard(self, action: str) -> None:
        if self.mutation_guard is not None:
            self.mutation_guard(action)

    @staticmethod
    def _adapter_identity(adapter: ReplayAdapter) -> dict[str, object]:
        identity = dict(adapter.identity())
        if identity.get("available") is not True:
            raise ReplayError("replay adapter is unavailable")
        required = {
            "isolation": "offline",
            "external_network": "forbidden",
            "side_effects": "none",
            "deployment": "forbidden",
        }
        if any(identity.get(key) != value for key, value in required.items()):
            raise ReplayError("replay adapter does not satisfy isolation contract")
        _text(identity.get("adapter"), "adapter identity", 240)
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 4_000:
            raise ReplayError("replay adapter identity is too large")
        try:
            assert_secret_free(encoded, "replay adapter identity")
        except SecretBoundaryError as exc:
            raise ReplayError("replay adapter identity contains secret material") from exc
        return identity
