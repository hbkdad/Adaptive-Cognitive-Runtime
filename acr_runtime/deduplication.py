from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import unicodedata
import uuid
from dataclasses import asdict, dataclass, replace
from difflib import SequenceMatcher
from itertools import combinations
from typing import Iterable, Protocol

from .memory import utc_now
from .models import ContextCandidate, ContextRejection
from .scoring import query_terms
from .secret_management import detect_secret_material


ARTIFACT_KINDS = frozenset(
    {"memory", "context", "skill", "tool_output", "model_request"}
)
RELATION_TYPES = frozenset(
    {
        "exact_duplicate",
        "semantic_duplicate",
        "near_duplicate",
        "version_successor",
        "overlapping_capability",
    }
)
RECOMMENDATIONS = frozenset(
    {"MERGE", "REFERENCE", "SUPERSEDE", "COMPOSE", "KEEP_SEPARATE"}
)
CANONICALIZER_VERSION = "acr-canonical-json-nfc-v1"
DETECTOR_VERSION = "acr-dedup-v1"
MAX_ARTIFACTS = 500
MAX_SIMILARITY_COMPARISONS = 10_000
MAX_ARTIFACT_BYTES = 1_048_576
MAX_RUN_BYTES = 16_777_216
NUMBER_PATTERN = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
NEGATION_PATTERN = re.compile(
    r"\b(?:no|not|never|without|deny|denies|forbid|forbids)\b",
    re.IGNORECASE,
)


class SemanticSimilarity(Protocol):
    trusted_local: bool
    model_id: str
    version: str

    def similarity(self, left: str, right: str) -> float: ...


def _canonical_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Canonical JSON does not permit NaN or infinity")
        return value
    if isinstance(value, str):
        return unicodedata.normalize(
            "NFC", value.replace("\r\n", "\n").replace("\r", "\n")
        )
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Canonical JSON object keys must be strings")
            normalized_key = str(_canonical_value(key))
            if normalized_key in normalized:
                raise ValueError("Unicode normalization produced a duplicate key")
            normalized[normalized_key] = _canonical_value(item)
        return normalized
    raise TypeError(f"Unsupported canonical value: {type(value).__name__}")


def canonical_json(value: object) -> str:
    """Return deterministic, versioned JSON without lossy case/space folding."""

    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DeduplicationArtifact:
    kind: str
    artifact_id: str
    identity: object
    similarity_text: str
    scope: str = "global"
    privacy: str = "public"
    behavior: object = None
    source_version: str = ""
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in ARTIFACT_KINDS:
            raise ValueError(f"Unsupported artifact kind: {self.kind}")
        if not self.artifact_id.strip():
            raise ValueError("artifact_id is required")
        if not self.scope.strip() or not self.privacy.strip():
            raise ValueError("scope and privacy partitions are required")
        if any(not item.strip() for item in self.provenance):
            raise ValueError("provenance references cannot be blank")
        retained_references = (self.artifact_id, *self.provenance)
        if any(detect_secret_material(item) for item in retained_references):
            raise ValueError(
                "Artifact IDs and provenance cannot contain secret material"
            )
        canonical_json(self.identity)
        canonical_json(self.behavior)
        artifact_bytes = sum(
            len(value.encode("utf-8"))
            for value in (
                canonical_json(self.identity),
                canonical_json(self.behavior),
                self.similarity_text,
                self.artifact_id,
                self.source_version,
                *self.provenance,
            )
        )
        if artifact_bytes > MAX_ARTIFACT_BYTES:
            raise ValueError(
                f"Artifact exceeds the {MAX_ARTIFACT_BYTES}-byte limit"
            )

    @property
    def canonical_hash(self) -> str:
        return _digest(
            {
                "canonicalizer": CANONICALIZER_VERSION,
                "kind": self.kind,
                "scope": self.scope,
                "privacy": self.privacy,
                "identity": self.identity,
                "behavior": self.behavior,
            }
        )

    @property
    def behavior_hash(self) -> str:
        return _digest(
            {
                "canonicalizer": CANONICALIZER_VERSION,
                "kind": self.kind,
                "behavior": self.behavior,
            }
        )

    @property
    def scope_hash(self) -> str:
        return _digest({"scope": self.scope})

    @property
    def privacy_hash(self) -> str:
        return _digest({"kind": self.kind, "privacy": self.privacy})


@dataclass(frozen=True)
class DeduplicationMatch:
    id: str
    run_id: str
    artifact_kind: str
    left_artifact_id: str
    right_artifact_id: str
    left_hash: str
    right_hash: str
    relation: str
    recommendation: str
    score: float
    method: str
    detector_version: str
    evidence: dict[str, object]
    provenance: tuple[str, ...]
    review_required: bool
    automatic_action_allowed: bool
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class DeduplicationRun:
    id: str
    policy: dict[str, object]
    requested_kinds: tuple[str, ...]
    artifact_count: int
    exact_comparisons: int
    similarity_comparisons: int
    match_count: int
    matches: tuple[DeduplicationMatch, ...]
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "matches": [match.as_dict() for match in self.matches],
        }


class DeduplicationEngine:
    """Bounded, advisory-only duplicate detection with content-minimized audit."""

    POLICY = {
        "name": DETECTOR_VERSION,
        "canonicalizer": CANONICALIZER_VERSION,
        "maximum_artifacts": MAX_ARTIFACTS,
        "maximum_similarity_comparisons": MAX_SIMILARITY_COMPARISONS,
        "near_duplicate_threshold": 0.92,
        "semantic_candidate_floor": 0.35,
        "semantic_duplicate_threshold": 0.95,
        "automatic_actions": False,
        "cross_kind_comparison": False,
        "cross_scope_comparison": False,
        "cross_privacy_comparison": False,
        "raw_content_persisted": False,
        "semantic_requires_trusted_local_adapter": True,
    }

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        semantic_similarity: SemanticSimilarity | None = None,
    ) -> None:
        self.connection = connection
        self.semantic_similarity = semantic_similarity

    @staticmethod
    def _terms(text: str) -> tuple[str, ...]:
        return tuple(query_terms(unicodedata.normalize("NFC", text)))

    @staticmethod
    def _jaccard(left: set[str], right: set[str]) -> float:
        if not left and not right:
            return 1.0
        union = left | right
        return len(left & right) / len(union) if union else 0.0

    @classmethod
    def _lexical_similarity(cls, left: str, right: str) -> tuple[float, float]:
        left_terms = cls._terms(left)
        right_terms = cls._terms(right)
        if not left_terms or not right_terms:
            return 0.0, 0.0
        overlap = cls._jaccard(set(left_terms), set(right_terms))
        sequence = SequenceMatcher(
            None, left_terms, right_terms, autojunk=False
        ).ratio()
        return round((overlap + sequence) / 2, 6), round(overlap, 6)

    @staticmethod
    def _safety_blockers(left: str, right: str) -> list[str]:
        blockers: list[str] = []
        left_normalized = unicodedata.normalize("NFC", left)
        right_normalized = unicodedata.normalize("NFC", right)
        left_numbers = NUMBER_PATTERN.findall(left_normalized)
        right_numbers = NUMBER_PATTERN.findall(right_normalized)
        if left_numbers != right_numbers:
            blockers.append("numeric_values_differ")
        if bool(NEGATION_PATTERN.search(left_normalized)) != bool(
            NEGATION_PATTERN.search(right_normalized)
        ):
            blockers.append("negation_differs")
        return blockers

    def _semantic_identity(self) -> str | None:
        adapter = self.semantic_similarity
        if adapter is None or not getattr(adapter, "trusted_local", False):
            return None
        model_id = str(getattr(adapter, "model_id", "")).strip()
        version = str(getattr(adapter, "version", "")).strip()
        if not model_id or not version:
            return None
        if detect_secret_material(model_id) or detect_secret_material(version):
            return None
        return f"{type(adapter).__name__}:{model_id}:{version}"

    @staticmethod
    def _ordered(
        left: DeduplicationArtifact, right: DeduplicationArtifact
    ) -> tuple[DeduplicationArtifact, DeduplicationArtifact]:
        if left.artifact_id <= right.artifact_id:
            return left, right
        return right, left

    def _match(
        self,
        *,
        run_id: str,
        left: DeduplicationArtifact,
        right: DeduplicationArtifact,
        relation: str,
        recommendation: str,
        score: float,
        method: str,
        evidence: dict[str, object],
        created_at: str,
    ) -> DeduplicationMatch:
        left, right = self._ordered(left, right)
        provenance = tuple(
            dict.fromkeys(
                (
                    f"{left.kind}:{left.artifact_id}",
                    *left.provenance,
                    f"{right.kind}:{right.artifact_id}",
                    *right.provenance,
                )
            )
        )
        return DeduplicationMatch(
            id=str(uuid.uuid4()),
            run_id=run_id,
            artifact_kind=left.kind,
            left_artifact_id=left.artifact_id,
            right_artifact_id=right.artifact_id,
            left_hash=left.canonical_hash,
            right_hash=right.canonical_hash,
            relation=relation,
            recommendation=recommendation,
            score=score,
            method=method,
            detector_version=DETECTOR_VERSION,
            evidence=evidence,
            provenance=provenance,
            review_required=True,
            automatic_action_allowed=False,
            created_at=created_at,
        )

    def analyze(
        self,
        artifacts: Iterable[DeduplicationArtifact],
        *,
        persist: bool = True,
    ) -> DeduplicationRun:
        items = sorted(
            tuple(artifacts), key=lambda item: (item.kind, item.artifact_id)
        )
        if not items:
            raise ValueError("At least one artifact is required")
        if len(items) > MAX_ARTIFACTS:
            raise ValueError(f"At most {MAX_ARTIFACTS} artifacts may be analyzed")
        total_bytes = sum(
            len(canonical_json(item.identity).encode("utf-8"))
            + len(canonical_json(item.behavior).encode("utf-8"))
            + len(item.similarity_text.encode("utf-8"))
            + sum(len(value.encode("utf-8")) for value in item.provenance)
            for item in items
        )
        if total_bytes > MAX_RUN_BYTES:
            raise ValueError(f"Run exceeds the {MAX_RUN_BYTES}-byte limit")
        identities = [(item.kind, item.artifact_id) for item in items]
        if len(set(identities)) != len(identities):
            raise ValueError("Artifact kind and ID pairs must be unique")

        run_id = str(uuid.uuid4())
        now = utc_now()
        matches: list[DeduplicationMatch] = []
        buckets: dict[
            tuple[str, str, str, str], list[DeduplicationArtifact]
        ] = {}
        for item in items:
            buckets.setdefault(
                (
                    item.kind,
                    item.scope_hash,
                    item.privacy_hash,
                    item.canonical_hash,
                ),
                [],
            ).append(item)
        for bucket in buckets.values():
            if len(bucket) < 2:
                continue
            canonical = bucket[0]
            for duplicate in bucket[1:]:
                matches.append(
                    self._match(
                        run_id=run_id,
                        left=canonical,
                        right=duplicate,
                        relation="exact_duplicate",
                        recommendation="REFERENCE",
                        score=1.0,
                        method=CANONICALIZER_VERSION,
                        evidence={
                            "same_kind": True,
                            "same_scope_partition": True,
                            "same_privacy_partition": True,
                            "same_behavior_contract": True,
                            "canonical_hash_equal": True,
                            "human_review_required": True,
                            "automatic_action_taken": False,
                        },
                        created_at=now,
                    )
                )

        similarity_comparisons = 0
        semantic_identity = self._semantic_identity()
        for left, right in combinations(items, 2):
            if similarity_comparisons >= MAX_SIMILARITY_COMPARISONS:
                break
            if (
                left.kind != right.kind
                or left.scope_hash != right.scope_hash
                or left.privacy_hash != right.privacy_hash
                or left.canonical_hash == right.canonical_hash
            ):
                continue
            lexical_score, token_overlap = self._lexical_similarity(
                left.similarity_text, right.similarity_text
            )
            if token_overlap == 0:
                continue
            similarity_comparisons += 1
            blockers = self._safety_blockers(
                left.similarity_text, right.similarity_text
            )
            same_behavior = left.behavior_hash == right.behavior_hash
            relation: str | None = None
            recommendation = "KEEP_SEPARATE"
            score = lexical_score
            method = "ordered_tokens_and_jaccard_v1"
            semantic_score: float | None = None

            if not same_behavior and left.kind == "skill" and token_overlap >= 0.6:
                relation = "overlapping_capability"
                recommendation = "COMPOSE"
                blockers.append("behavior_contract_differs")
            elif not same_behavior:
                blockers.append("behavior_contract_differs")
                if lexical_score >= float(
                    self.POLICY["near_duplicate_threshold"]
                ):
                    relation = "near_duplicate"
                    recommendation = "KEEP_SEPARATE"
                else:
                    continue
            elif (
                semantic_identity is not None
                and left.privacy == "public"
                and token_overlap
                >= float(self.POLICY["semantic_candidate_floor"])
                and not blockers
            ):
                try:
                    semantic_score = float(
                        self.semantic_similarity.similarity(
                            left.similarity_text, right.similarity_text
                        )
                    )
                except Exception:
                    semantic_score = None
                if semantic_score is not None and not 0 <= semantic_score <= 1:
                    raise ValueError("Semantic similarity must be between 0 and 1")
                if semantic_score is not None and semantic_score >= float(
                    self.POLICY["semantic_duplicate_threshold"]
                ):
                    relation = "semantic_duplicate"
                    recommendation = "REFERENCE"
                    score = semantic_score
                    method = semantic_identity
            if relation is None and (
                lexical_score >= float(self.POLICY["near_duplicate_threshold"])
                or blockers and lexical_score >= 0.60
            ):
                relation = "near_duplicate"
                recommendation = (
                    "KEEP_SEPARATE" if blockers else "REFERENCE"
                )
            if relation is None:
                continue
            matches.append(
                self._match(
                    run_id=run_id,
                    left=left,
                    right=right,
                    relation=relation,
                    recommendation=recommendation,
                    score=score,
                    method=method,
                    evidence={
                        "token_overlap": token_overlap,
                        "lexical_score": lexical_score,
                        "semantic_score": semantic_score,
                        "semantic_adapter": semantic_identity,
                        "same_behavior_contract": same_behavior,
                        "blockers": blockers,
                        "human_review_required": True,
                        "automatic_action_taken": False,
                    },
                    created_at=now,
                )
            )

        matches.sort(
            key=lambda item: (
                item.artifact_kind,
                item.left_artifact_id,
                item.right_artifact_id,
                item.relation,
            )
        )
        run = DeduplicationRun(
            id=run_id,
            policy=dict(self.POLICY),
            requested_kinds=tuple(sorted({item.kind for item in items})),
            artifact_count=len(items),
            exact_comparisons=len(items),
            similarity_comparisons=similarity_comparisons,
            match_count=len(matches),
            matches=tuple(matches),
            created_at=now,
        )
        if persist:
            if len({item.scope_hash for item in items}) != 1:
                raise ValueError(
                    "Persisted deduplication runs require one exact scope"
                )
            self._persist(run, items)
        return run

    def _persist(
        self,
        run: DeduplicationRun,
        artifacts: list[DeduplicationArtifact],
        *,
        own_transaction: bool = True,
    ) -> None:
        if own_transaction and self.connection.in_transaction:
            raise RuntimeError(
                "Deduplication scans cannot join a caller-owned transaction"
            )
        try:
            if own_transaction:
                self.connection.execute("BEGIN IMMEDIATE")
            policy = {
                **run.policy,
                "_exact_comparisons": run.exact_comparisons,
                "_similarity_comparisons": run.similarity_comparisons,
            }
            scope_hashes = {item.scope_hash for item in artifacts}
            self.connection.execute(
                """
                INSERT INTO deduplication_runs(
                    id, algorithm_version, scope_hash, kinds_json, policy_json,
                    item_count, match_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    DETECTOR_VERSION,
                    next(iter(scope_hashes)) if len(scope_hashes) == 1 else None,
                    canonical_json(run.requested_kinds),
                    canonical_json(policy),
                    run.artifact_count,
                    run.match_count,
                    run.created_at,
                ),
            )
            item_ids = {
                (item.kind, item.artifact_id): str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"acr:dedup:{run.id}:{item.kind}:{item.artifact_id}",
                    )
                )
                for item in artifacts
            }
            self.connection.executemany(
                """
                INSERT INTO deduplication_items(
                    id, run_id, kind, source_id, source_version, content_hash,
                    evidence_json, provenance_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        item_ids[(item.kind, item.artifact_id)],
                        run.id,
                        item.kind,
                        _digest(
                            {
                                "run_id": run.id,
                                "kind": item.kind,
                                "source_id": item.artifact_id,
                            }
                        ),
                        _digest(
                            {
                                "source_version": (
                                    item.source_version
                                    or item.canonical_hash
                                )
                            }
                        ),
                        item.canonical_hash,
                        canonical_json(
                            {
                                "behavior_hash": item.behavior_hash,
                                "scope_hash": item.scope_hash,
                                "privacy_hash": item.privacy_hash,
                                "canonicalizer": CANONICALIZER_VERSION,
                            }
                        ),
                        canonical_json(
                            tuple(
                                _digest(
                                    {
                                        "run_id": run.id,
                                        "provenance": reference,
                                    }
                                )
                                for reference in item.provenance
                            )
                        ),
                        run.created_at,
                    )
                    for item in artifacts
                ),
            )
            persisted_matches = []
            for item in run.matches:
                left_item = item_ids[
                    (item.artifact_kind, item.left_artifact_id)
                ]
                right_item = item_ids[
                    (item.artifact_kind, item.right_artifact_id)
                ]
                if left_item > right_item:
                    left_item, right_item = right_item, left_item
                persisted_matches.append(
                    (
                        item.id,
                        item.run_id,
                        left_item,
                        right_item,
                        item.relation,
                        item.recommendation,
                        item.score,
                        item.method,
                        item.detector_version,
                        canonical_json(item.evidence),
                        canonical_json(
                            tuple(
                                _digest(
                                    {
                                        "run_id": run.id,
                                        "provenance": reference,
                                    }
                                )
                                for reference in item.provenance
                            )
                        ),
                        item.created_at,
                    )
                )
            self.connection.executemany(
                """
                INSERT INTO deduplication_matches(
                    id, run_id, left_item_id, right_item_id, relation,
                    recommendation, score, method_id, method_version,
                    evidence_json, provenance_json, review_required,
                    automatic_action_allowed, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?)
                """,
                persisted_matches,
            )
            sealed = self.connection.execute(
                """
                UPDATE deduplication_runs SET sealed=1
                WHERE id=? AND sealed=0
                """,
                (run.id,),
            )
            if sealed.rowcount != 1:
                raise RuntimeError("Deduplication report could not be sealed")
            if own_transaction:
                self.connection.commit()
        except Exception:
            if own_transaction:
                self.connection.rollback()
            raise

    def load(self, run_id: str, *, scope: str) -> DeduplicationRun:
        if not scope.strip():
            raise ValueError("scope is required")
        row = self.connection.execute(
            "SELECT * FROM deduplication_runs WHERE id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        if row["scope_hash"] != _digest({"scope": scope}):
            raise PermissionError("Deduplication report scope does not match")
        if not bool(row["sealed"]):
            raise RuntimeError("Deduplication report is not sealed")
        actual_items = self.connection.execute(
            "SELECT COUNT(*) FROM deduplication_items WHERE run_id=?",
            (run_id,),
        ).fetchone()[0]
        actual_matches = self.connection.execute(
            "SELECT COUNT(*) FROM deduplication_matches WHERE run_id=?",
            (run_id,),
        ).fetchone()[0]
        if (
            int(actual_items) != int(row["item_count"])
            or int(actual_matches) != int(row["match_count"])
        ):
            raise RuntimeError("Deduplication report count mismatch")
        rows = self.connection.execute(
            """
            SELECT m.*,
                   li.kind AS artifact_kind,
                   li.source_id AS left_source_id,
                   li.content_hash AS left_content_hash,
                   ri.source_id AS right_source_id,
                   ri.content_hash AS right_content_hash
            FROM deduplication_matches m
            JOIN deduplication_items li
              ON li.run_id=m.run_id AND li.id=m.left_item_id
            JOIN deduplication_items ri
              ON ri.run_id=m.run_id AND ri.id=m.right_item_id
            WHERE m.run_id=?
            """,
            (run_id,),
        ).fetchall()
        loaded_matches: list[DeduplicationMatch] = []
        for item in rows:
            left_id = f"ref:{item['left_source_id']}"
            left_hash = item["left_content_hash"]
            right_id = f"ref:{item['right_source_id']}"
            right_hash = item["right_content_hash"]
            if left_id > right_id:
                left_id, right_id = right_id, left_id
                left_hash, right_hash = right_hash, left_hash
            loaded_matches.append(
                DeduplicationMatch(
                    id=item["id"],
                    run_id=item["run_id"],
                    artifact_kind=item["artifact_kind"],
                    left_artifact_id=left_id,
                    right_artifact_id=right_id,
                    left_hash=left_hash,
                    right_hash=right_hash,
                    relation=item["relation"],
                    recommendation=item["recommendation"],
                    score=float(item["score"]),
                    method=item["method_id"],
                    detector_version=item["method_version"],
                    evidence=json.loads(item["evidence_json"]),
                    provenance=tuple(json.loads(item["provenance_json"])),
                    review_required=bool(item["review_required"]),
                    automatic_action_allowed=bool(
                        item["automatic_action_allowed"]
                    ),
                    created_at=item["created_at"],
                )
            )
        matches = tuple(
            sorted(
                loaded_matches,
                key=lambda item: (
                    item.artifact_kind,
                    item.left_artifact_id,
                    item.right_artifact_id,
                    item.relation,
                ),
            )
        )
        policy = json.loads(row["policy_json"])
        exact_comparisons = int(policy.pop("_exact_comparisons", 0))
        similarity_comparisons = int(
            policy.pop("_similarity_comparisons", 0)
        )
        return DeduplicationRun(
            id=row["id"],
            policy=policy,
            requested_kinds=tuple(json.loads(row["kinds_json"])),
            artifact_count=int(row["item_count"]),
            exact_comparisons=exact_comparisons,
            similarity_comparisons=similarity_comparisons,
            match_count=int(row["match_count"]),
            matches=matches,
            created_at=row["created_at"],
        )

    def scan_database(
        self,
        *,
        kinds: Iterable[str] = ARTIFACT_KINDS,
        scope: str,
        limit: int = 100,
    ) -> DeduplicationRun:
        selected = tuple(sorted(set(kinds)))
        if not selected or any(kind not in ARTIFACT_KINDS for kind in selected):
            raise ValueError("At least one supported artifact kind is required")
        if not scope.strip():
            raise ValueError("scope is required")
        if not 1 <= limit <= MAX_ARTIFACTS:
            raise ValueError(f"limit must be between 1 and {MAX_ARTIFACTS}")
        if self.connection.in_transaction:
            raise RuntimeError(
                "Deduplication scans cannot join a caller-owned transaction"
            )
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            artifacts = self._database_artifacts(selected, limit, scope)
            if not artifacts:
                raise ValueError(
                    "No authorized persisted artifacts matched the scan"
                )
            run = self.analyze(artifacts, persist=False)
            self._persist(run, artifacts, own_transaction=False)
            self.connection.commit()
            return run
        except Exception:
            self.connection.rollback()
            raise

    def _database_artifacts(
        self, kinds: tuple[str, ...], limit: int, scope: str
    ) -> list[DeduplicationArtifact]:
        per_kind = max(1, limit // len(kinds))
        artifacts: list[DeduplicationArtifact] = []
        if "memory" in kinds:
            rows = self.connection.execute(
                """
                SELECT * FROM memories
                WHERE scope=?
                  AND status IN ('candidate', 'confirmed')
                  AND lifecycle_state IN ('active', 'cold')
                  AND (
                      retention_until IS NULL
                      OR julianday(retention_until) > julianday('now')
                  )
                ORDER BY id LIMIT ?
                """,
                (scope, per_kind),
            ).fetchall()
            for row in rows:
                artifacts.append(
                    DeduplicationArtifact(
                        kind="memory",
                        artifact_id=row["id"],
                        identity={
                            "subject": row["subject"],
                            "content": row["content"],
                            "structured_payload": json.loads(
                                row["structured_payload_json"]
                            ),
                        },
                        similarity_text=f"{row['subject']} {row['content']}",
                        scope=row["scope"],
                        privacy=row["sensitivity"],
                        behavior={
                            "type": row["type"],
                            "valid_from": row["valid_from"],
                            "valid_until": row["valid_until"],
                            "retention_until": row["retention_until"],
                            "privacy_policy_version": row[
                                "privacy_policy_version"
                            ],
                            "status": row["status"],
                            "lifecycle_state": row["lifecycle_state"],
                        },
                        source_version=(
                            f"{row['updated_at']}:"
                            f"{row['privacy_policy_version']}"
                        ),
                        provenance=tuple(
                            reference
                            for reference in (
                                f"memory:{row['id']}",
                                (
                                    f"source:{row['source_type']}:"
                                    f"{row['source_id']}"
                                    if row["source_type"] is not None
                                    and row["source_id"] is not None
                                    else None
                                ),
                            )
                            if reference is not None
                        ),
                    )
                )
        if "skill" in kinds and scope == "global":
            rows = self.connection.execute(
                "SELECT * FROM skills ORDER BY id LIMIT ?", (per_kind,)
            ).fetchall()
            for row in rows:
                manifest = json.loads(row["manifest_json"])
                artifacts.append(
                    DeduplicationArtifact(
                        kind="skill",
                        artifact_id=row["id"],
                        identity={"package_content_hash": row["content_hash"]},
                        similarity_text=(
                            f"{row['description']} {row['instructions']} "
                            f"{row['task_classes_json']}"
                        ),
                        behavior={
                            "task_classes": json.loads(
                                row["task_classes_json"]
                            ),
                            "permissions": json.loads(row["permissions_json"]),
                            "models": json.loads(row["models_json"]),
                            "tools": manifest.get("tools", []),
                            "dependencies": manifest.get("dependencies", []),
                            "inputs": manifest.get("inputs", []),
                            "outputs": manifest.get("outputs", []),
                            "applicability": json.loads(
                                row["applicability_json"]
                            ),
                            "contraindications": json.loads(
                                row["contraindications_json"]
                            ),
                            "verification_status": row[
                                "verification_status"
                            ],
                            "verification": json.loads(
                                row["verification_json"]
                            ),
                            "lifecycle_status": row["lifecycle_status"],
                        },
                        source_version=(
                            f"{row['version']}:{row['content_hash']}"
                        ),
                        provenance=(f"skill:{row['id']}",),
                    )
                )
        if "model_request" in kinds:
            rows = self.connection.execute(
                "SELECT * FROM model_routes ORDER BY id LIMIT ?", (per_kind,)
            ).fetchall()
            for row in rows:
                request = json.loads(row["request_json"])
                request_scope = f"task_class:{row['task_class']}"
                if request_scope != scope:
                    continue
                artifacts.append(
                    DeduplicationArtifact(
                        kind="model_request",
                        artifact_id=row["id"],
                        identity=request,
                        similarity_text=canonical_json(request),
                        scope=request_scope,
                        privacy="internal",
                        behavior={"task_class": row["task_class"]},
                        source_version=row["updated_at"],
                        provenance=(f"model_route:{row['id']}",),
                    )
                )
        return sorted(
            artifacts, key=lambda item: (item.kind, item.artifact_id)
        )[:limit]


def deduplicate_context_candidates(
    candidates: Iterable[ContextCandidate],
) -> tuple[list[ContextCandidate], list[ContextRejection]]:
    """Coalesce exact safe equivalents while retaining source provenance."""

    selected, rejected, _ = deduplicate_context_candidates_with_aliases(
        candidates
    )
    return selected, rejected


def deduplicate_context_candidates_with_aliases(
    candidates: Iterable[ContextCandidate],
) -> tuple[
    list[ContextCandidate], list[ContextRejection], dict[str, str]
]:
    """Return direct loser-to-final-winner aliases for dependency resolution."""

    groups: dict[tuple[object, ...], list[ContextCandidate]] = {}
    for item in candidates:
        content_hash = _digest(
            {
                "canonicalizer": CANONICALIZER_VERSION,
                "content": item.content,
            }
        )
        key = (
            item.source_type,
            item.content_origin,
            item.security_authority,
            item.content_kind,
            item.exact_required,
            content_hash,
        )
        groups.setdefault(key, []).append(item)

    selected: list[ContextCandidate] = []
    rejected: list[ContextRejection] = []
    aliases: dict[str, str] = {}
    for group in groups.values():
        winner = max(
            group,
            key=lambda candidate: (
                candidate.required,
                candidate.expected_utility,
                candidate.confidence,
                candidate.source_id,
            ),
        )
        provenance = tuple(
            dict.fromkeys(
                reference
                for item in group
                for reference in (
                    f"context:{item.source_type}:{item.source_id}",
                    *item.provenance,
                )
            )
        )
        dependencies = tuple(
            dict.fromkeys(
                dependency
                for item in group
                for dependency in item.dependencies
            )
        )
        selected.append(
            replace(
                winner,
                required=any(item.required for item in group),
                expected_utility=max(
                    item.expected_utility for item in group
                ),
                confidence=max(item.confidence for item in group),
                dependencies=dependencies,
                provenance=provenance,
            )
        )
        for duplicate in group:
            if duplicate is winner:
                continue
            aliases[duplicate.source_id] = winner.source_id
            rejected.append(
                ContextRejection(
                    duplicate.source_type,
                    duplicate.source_id,
                    f"exact_duplicate_of:{winner.source_type}:{winner.source_id}",
                )
            )
    return selected, rejected, aliases
