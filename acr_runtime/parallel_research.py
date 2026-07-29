from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from .content_security import ContentAssessmentRequest, ContentSecurityController
from .memory import utc_now


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _bounded(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    text = value.strip()
    if not text or len(text) > maximum:
        raise ValueError(f"{field} must be 1..{maximum} characters")
    return text


def _identifier(value: object, field: str) -> str:
    text = _bounded(value, field, 128)
    if not re.fullmatch(r"[A-Za-z0-9._:/-]+", text):
        raise ValueError(f"{field} contains unsupported characters")
    return text


@dataclass(frozen=True)
class ResearchReferenceCreate:
    locator: str
    title: str
    source_kind: str
    authority: float
    content: str

    def __post_init__(self) -> None:
        _bounded(self.locator, "locator", 2_048)
        _bounded(self.title, "title", 512)
        if self.source_kind not in {"primary", "secondary", "local", "unknown"}:
            raise ValueError("source_kind is unsupported")
        if (
            isinstance(self.authority, bool)
            or not isinstance(self.authority, (int, float))
            or not 0 <= self.authority <= 1
        ):
            raise ValueError("authority must be 0..1")
        _bounded(self.content, "content", 100_000)

    @classmethod
    def from_dict(cls, payload: object) -> "ResearchReferenceCreate":
        if not isinstance(payload, dict) or set(payload) != {
            "locator", "title", "source_kind", "authority", "content"
        }:
            raise ValueError("reference requires an exact supported shape")
        return cls(**payload)


@dataclass(frozen=True)
class ResearchReference:
    id: str
    locator: str
    title: str
    source_kind: str
    authority: float
    content: str
    content_hash: str
    assessment_id: str

    def as_dict(self, *, include_content: bool = False) -> dict[str, object]:
        result: dict[str, object] = {
            "id": self.id,
            "locator": self.locator,
            "title": self.title,
            "source_kind": self.source_kind,
            "authority": self.authority,
            "content_hash": self.content_hash,
            "assessment_id": self.assessment_id,
        }
        if include_content:
            result["content"] = self.content
        return result


@dataclass(frozen=True)
class ResearchQuestion:
    id: str
    question: str
    independent: bool
    reference_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.id, "question id")
        _bounded(self.question, "question", 4_000)
        if self.independent is not True:
            raise ValueError("every research question must be explicitly independent")
        if len(self.reference_ids) > 32:
            raise ValueError("a question may reference at most 32 shared records")
        for item in self.reference_ids:
            _identifier(item, "reference id")

    @classmethod
    def from_dict(cls, payload: object) -> "ResearchQuestion":
        if not isinstance(payload, dict) or set(payload) != {
            "id", "question", "independent", "reference_ids"
        }:
            raise ValueError("question requires an exact supported shape")
        if not isinstance(payload["reference_ids"], list):
            raise ValueError("reference_ids must be a list")
        return cls(
            id=payload["id"],
            question=payload["question"],
            independent=payload["independent"],
            reference_ids=tuple(payload["reference_ids"]),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "question": self.question,
            "independent": self.independent,
            "reference_ids": list(self.reference_ids),
        }


@dataclass(frozen=True)
class ParallelResearchRequest:
    objective: str
    questions: tuple[ResearchQuestion, ...]
    reference_ids: tuple[str, ...] = ()
    max_workers: int = 4
    max_seconds: int = 300
    factory_plan_id: str | None = None

    def __post_init__(self) -> None:
        _bounded(self.objective, "objective", 4_000)
        if not 2 <= len(self.questions) <= 6:
            raise ValueError("parallel research requires 2..6 questions")
        if len({item.id for item in self.questions}) != len(self.questions):
            raise ValueError("question IDs must be unique")
        normalized = {" ".join(item.question.casefold().split()) for item in self.questions}
        if len(normalized) != len(self.questions):
            raise ValueError("duplicate questions are not independent work")
        if (
            isinstance(self.max_workers, bool)
            or not isinstance(self.max_workers, int)
            or not 2 <= self.max_workers <= 6
        ):
            raise ValueError("max_workers must be 2..6")
        if (
            isinstance(self.max_seconds, bool)
            or not isinstance(self.max_seconds, int)
            or not 1 <= self.max_seconds <= 3_600
        ):
            raise ValueError("max_seconds must be 1..3600")
        if len(self.reference_ids) > 64:
            raise ValueError("a plan may reference at most 64 shared records")
        for item in self.reference_ids:
            _identifier(item, "reference id")
        if self.factory_plan_id is not None:
            _identifier(self.factory_plan_id, "factory plan id")

    @classmethod
    def from_dict(cls, payload: object) -> "ParallelResearchRequest":
        if not isinstance(payload, dict):
            raise ValueError("research request must be an object")
        required = {"objective", "questions"}
        allowed = required | {
            "reference_ids", "max_workers", "max_seconds", "factory_plan_id"
        }
        if not required <= set(payload) or set(payload) - allowed:
            raise ValueError("research request has missing or unknown fields")
        if not isinstance(payload["questions"], list):
            raise ValueError("questions must be a list")
        return cls(
            objective=payload["objective"],
            questions=tuple(
                ResearchQuestion.from_dict(item) for item in payload["questions"]
            ),
            reference_ids=tuple(payload.get("reference_ids", [])),
            max_workers=payload.get("max_workers", 4),
            max_seconds=payload.get("max_seconds", 300),
            factory_plan_id=payload.get("factory_plan_id"),
        )


@dataclass(frozen=True)
class ResearchAssignment:
    question_id: str
    question: str
    reference_ids: tuple[str, ...]
    deadline_monotonic: float


@dataclass(frozen=True)
class ResearchFinding:
    claim: str
    evidence_reference_ids: tuple[str, ...]
    confidence: float

    def __post_init__(self) -> None:
        _bounded(self.claim, "claim", 10_000)
        if not 1 <= len(self.evidence_reference_ids) <= 32:
            raise ValueError("finding requires 1..32 evidence references")
        if len(set(self.evidence_reference_ids)) != len(
            self.evidence_reference_ids
        ):
            raise ValueError("finding evidence references must be unique")
        for item in self.evidence_reference_ids:
            _identifier(item, "evidence reference id")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not 0 <= self.confidence <= 1
        ):
            raise ValueError("confidence must be 0..1")


@dataclass(frozen=True)
class RankedFinding:
    question_id: str
    claim: str
    claim_hash: str
    evidence_reference_ids: tuple[str, ...]
    confidence: float
    evidence_score: float
    rank: int


class ResearchWorkerAdapter(Protocol):
    adapter_id: str

    def research(
        self,
        assignment: ResearchAssignment,
        resolve_reference: Callable[[str], ResearchReference],
    ) -> Sequence[ResearchFinding]: ...

    def synthesize(
        self, objective: str, findings: Sequence[RankedFinding]
    ) -> str: ...


class QualityEvaluator(Protocol):
    evaluator_id: str

    def evaluate(
        self, objective: str, findings: Sequence[RankedFinding], synthesis: str
    ) -> float: ...


class ResearchExecutionError(RuntimeError):
    def __init__(self, message: str, run_id: str) -> None:
        super().__init__(message)
        self.run_id = run_id


class ParallelResearchEngine:
    """Bounded manager-worker research with shared immutable references."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        security: ContentSecurityController,
    ) -> None:
        self.connection = connection
        self.security = security

    def add_reference(self, create: ResearchReferenceCreate) -> ResearchReference:
        content = create.content.strip()
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        existing = self.connection.execute(
            """
            SELECT id FROM research_references
            WHERE locator=? AND content_hash=?
            """,
            (create.locator.strip(), content_hash),
        ).fetchone()
        if existing is not None:
            return self.reference(existing["id"], include_content=True)
        origin = (
            "web_content"
            if create.locator.casefold().startswith(("http://", "https://"))
            else "document"
        )
        assessment = self.security.assess(ContentAssessmentRequest(
            origin=origin,
            source_id=create.locator.strip()[:512],
            content=content,
            provenance=(
                f"research_source_kind:{create.source_kind}",
                f"research_content_hash:{content_hash}",
            ),
        ))
        if assessment["disposition"] == "quarantine":
            raise PermissionError(
                "research reference was quarantined by content security"
            )
        reference_id = f"ref-{uuid.uuid4()}"
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO research_references(
                    id, content_hash, locator, title, source_kind,
                    authority_micros, assessment_id, content, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reference_id,
                    content_hash,
                    create.locator.strip(),
                    create.title.strip(),
                    create.source_kind,
                    round(create.authority * 1_000_000),
                    assessment["id"],
                    content,
                    utc_now(),
                ),
            )
        return self.reference(reference_id, include_content=True)

    def reference(
        self, reference_id: str, *, include_content: bool = False
    ) -> ResearchReference:
        row = self.connection.execute(
            "SELECT * FROM research_references WHERE id=?", (reference_id,)
        ).fetchone()
        if row is None:
            raise KeyError(reference_id)
        reference = ResearchReference(
            id=row["id"],
            locator=row["locator"],
            title=row["title"],
            source_kind=row["source_kind"],
            authority=row["authority_micros"] / 1_000_000,
            content=row["content"] if include_content else "",
            content_hash=row["content_hash"],
            assessment_id=row["assessment_id"],
        )
        return reference

    def plan(self, request: ParallelResearchRequest) -> dict[str, object]:
        all_reference_ids = tuple(dict.fromkeys(
            (*request.reference_ids,) + tuple(
                item
                for question in request.questions
                for item in question.reference_ids
            )
        ))
        if all_reference_ids:
            found = {
                row["id"] for row in self.connection.execute(
                    "SELECT id FROM research_references WHERE id IN "
                    f"({','.join('?' for _ in all_reference_ids)})",
                    all_reference_ids,
                )
            }
            missing = sorted(set(all_reference_ids) - found)
            if missing:
                raise ValueError(f"unknown research references: {missing}")
        if request.factory_plan_id is not None:
            factory = self.connection.execute(
                """
                SELECT selected_topology, worker_count
                FROM agent_factory_plans WHERE id=?
                """,
                (request.factory_plan_id,),
            ).fetchone()
            if factory is None:
                raise ValueError("factory_plan_id does not exist")
            if factory["selected_topology"] != "researchers_synthesizer":
                raise ValueError(
                    "factory plan must select researchers_synthesizer"
                )
            if factory["worker_count"] > request.max_workers + 1:
                raise ValueError("factory plan exceeds the research worker cap")
        plan_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO research_plans(
                    id, objective_hash, objective, questions_json,
                    reference_ids_json, max_workers, max_seconds,
                    factory_plan_id, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?)
                """,
                (
                    plan_id,
                    _hash(request.objective.strip()),
                    request.objective.strip(),
                    _canonical([item.as_dict() for item in request.questions]),
                    _canonical(request.reference_ids),
                    request.max_workers,
                    request.max_seconds,
                    request.factory_plan_id,
                    utc_now(),
                ),
            )
        return self.get_plan(plan_id)

    def get_plan(self, plan_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM research_plans WHERE id=?", (plan_id,)
        ).fetchone()
        if row is None:
            raise KeyError(plan_id)
        return {
            "id": row["id"],
            "objective": row["objective"],
            "objective_hash": row["objective_hash"],
            "questions": json.loads(row["questions_json"]),
            "reference_ids": json.loads(row["reference_ids_json"]),
            "max_workers": row["max_workers"],
            "max_seconds": row["max_seconds"],
            "factory_plan_id": row["factory_plan_id"],
            "status": row["status"],
            "created_at": row["created_at"],
        }

    def _reference_snapshot(
        self, reference_ids: Sequence[str]
    ) -> dict[str, ResearchReference]:
        return {
            item: self.reference(item, include_content=True)
            for item in reference_ids
        }

    @staticmethod
    def _validate_adapter(adapter: ResearchWorkerAdapter) -> str:
        adapter_id = _identifier(
            getattr(adapter, "adapter_id", ""), "adapter_id"
        )
        if not callable(getattr(adapter, "research", None)) or not callable(
            getattr(adapter, "synthesize", None)
        ):
            raise ValueError("adapter must provide research and synthesize")
        return adapter_id

    @staticmethod
    def _deduplicate(
        batches: Sequence[tuple[str, Sequence[ResearchFinding]]],
        references: dict[str, ResearchReference],
        allowed_by_question: dict[str, frozenset[str]],
    ) -> tuple[list[RankedFinding], int]:
        raw_count = 0
        merged: dict[str, dict[str, object]] = {}
        for question_id, findings in batches:
            if not isinstance(findings, Sequence) or len(findings) > 64:
                raise ValueError("each worker must return at most 64 findings")
            for finding in findings:
                raw_count += 1
                if not isinstance(finding, ResearchFinding):
                    raise ValueError("worker returned an invalid finding")
                missing = set(finding.evidence_reference_ids) - set(references)
                if missing:
                    raise ValueError(
                        f"finding cites unavailable references: {sorted(missing)}"
                    )
                outside_scope = (
                    set(finding.evidence_reference_ids)
                    - allowed_by_question[question_id]
                )
                if outside_scope:
                    raise ValueError(
                        "finding cites references outside its question scope: "
                        f"{sorted(outside_scope)}"
                    )
                normalized = " ".join(finding.claim.casefold().split())
                claim_hash = hashlib.sha256(
                    normalized.encode("utf-8")
                ).hexdigest()
                current = merged.get(claim_hash)
                if current is None:
                    merged[claim_hash] = {
                        "question_id": question_id,
                        "claim": finding.claim.strip(),
                        "evidence": set(finding.evidence_reference_ids),
                        "confidence": float(finding.confidence),
                    }
                else:
                    current["evidence"].update(finding.evidence_reference_ids)
                    current["confidence"] = max(
                        float(current["confidence"]), float(finding.confidence)
                    )
                    current["question_id"] = min(
                        str(current["question_id"]), question_id
                    )
        scored: list[dict[str, object]] = []
        for claim_hash, item in merged.items():
            evidence = tuple(sorted(item["evidence"]))
            authority_by_content: dict[str, float] = {}
            for reference_id in evidence:
                reference = references[reference_id]
                authority_by_content[reference.content_hash] = max(
                    authority_by_content.get(reference.content_hash, 0.0),
                    reference.authority,
                )
            authority = (
                sum(authority_by_content.values()) / len(authority_by_content)
            )
            corroboration = min(len(authority_by_content), 3) / 3
            score = min(
                1.0,
                0.70 * authority
                + 0.20 * corroboration
                + 0.10 * float(item["confidence"]),
            )
            scored.append({
                **item,
                "claim_hash": claim_hash,
                "evidence": evidence,
                "score": score,
            })
        scored.sort(key=lambda item: (-float(item["score"]), item["claim_hash"]))
        ranked = [
            RankedFinding(
                question_id=str(item["question_id"]),
                claim=str(item["claim"]),
                claim_hash=str(item["claim_hash"]),
                evidence_reference_ids=tuple(item["evidence"]),
                confidence=float(item["confidence"]),
                evidence_score=float(item["score"]),
                rank=index,
            )
            for index, item in enumerate(scored, 1)
        ]
        return ranked, raw_count

    def execute(
        self,
        plan_id: str,
        adapter: ResearchWorkerAdapter,
        *,
        mode: str = "parallel",
        quality_evaluator: QualityEvaluator | None = None,
    ) -> dict[str, object]:
        if mode not in {"serial", "parallel"}:
            raise ValueError("mode must be serial or parallel")
        adapter_id = self._validate_adapter(adapter)
        plan = self.get_plan(plan_id)
        questions = tuple(
            ResearchQuestion.from_dict(item) for item in plan["questions"]
        )
        all_reference_ids = tuple(dict.fromkeys(
            tuple(plan["reference_ids"]) + tuple(
                reference_id
                for question in questions
                for reference_id in question.reference_ids
            )
        ))
        references = self._reference_snapshot(all_reference_ids)

        run_id = str(uuid.uuid4())
        started = time.monotonic()
        deadline = started + int(plan["max_seconds"])
        assignments = [
            ResearchAssignment(
                question_id=item.id,
                question=item.question,
                reference_ids=tuple(dict.fromkeys(
                    tuple(plan["reference_ids"]) + item.reference_ids
                )),
                deadline_monotonic=deadline,
            )
            for item in questions
        ]
        allowed_by_question = {
            item.question_id: frozenset(item.reference_ids)
            for item in assignments
        }

        def resolver_for(
            assignment: ResearchAssignment,
        ) -> Callable[[str], ResearchReference]:
            allowed = allowed_by_question[assignment.question_id]

            def resolve(reference_id: str) -> ResearchReference:
                if reference_id not in allowed:
                    raise KeyError(
                        "worker requested a reference outside its question scope"
                    )
                return references[reference_id]

            return resolve

        batches: list[tuple[str, Sequence[ResearchFinding]]] = []
        status = "completed"
        failure_code: str | None = None
        ranked: list[RankedFinding] = []
        raw_count = 0
        synthesis: str | None = None
        quality: float | None = None
        executor: ThreadPoolExecutor | None = None
        try:
            if mode == "serial":
                for assignment in assignments:
                    if time.monotonic() >= deadline:
                        raise TimeoutError()
                    batches.append((
                        assignment.question_id,
                        adapter.research(assignment, resolver_for(assignment)),
                    ))
            else:
                executor = ThreadPoolExecutor(
                    max_workers=min(int(plan["max_workers"]), len(assignments)),
                    thread_name_prefix="acr-research",
                )
                future_map = {
                    executor.submit(
                        adapter.research, assignment, resolver_for(assignment)
                    ):
                    assignment.question_id
                    for assignment in assignments
                }
                remaining = max(0.0, deadline - time.monotonic())
                for future in as_completed(future_map, timeout=remaining):
                    batches.append((future_map[future], future.result()))
                executor.shutdown(wait=True, cancel_futures=True)
                executor = None
            ranked, raw_count = self._deduplicate(
                batches, references, allowed_by_question
            )
            if not ranked:
                raise ValueError("research produced no evidence-backed findings")
            synthesis = _bounded(
                adapter.synthesize(str(plan["objective"]), ranked),
                "synthesis",
                100_000,
            )
            if quality_evaluator is not None:
                _identifier(
                    getattr(quality_evaluator, "evaluator_id", ""),
                    "evaluator_id",
                )
                quality = quality_evaluator.evaluate(
                    str(plan["objective"]), ranked, synthesis
                )
                if (
                    isinstance(quality, bool)
                    or not isinstance(quality, (int, float))
                    or not 0 <= quality <= 1
                ):
                    raise ValueError("quality evaluator must return 0..1")
        except TimeoutError:
            status = "timed_out"
            failure_code = "deadline_exceeded"
            ranked = []
            raw_count = 0
            synthesis = None
            quality = None
        except Exception as exc:
            status = "failed"
            failure_code = f"adapter_failure:{type(exc).__name__}"[:256]
            ranked = []
            raw_count = 0
            synthesis = None
            quality = None
        finally:
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
        latency_ms = max(0, round((time.monotonic() - started) * 1_000))
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO research_runs(
                    id, plan_id, mode, adapter_id, status, latency_ms,
                    quality_micros, raw_finding_count,
                    deduplicated_finding_count, synthesis, synthesis_hash,
                    failure_code, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    plan_id,
                    mode,
                    adapter_id,
                    status,
                    latency_ms,
                    None if quality is None else round(quality * 1_000_000),
                    raw_count,
                    len(ranked),
                    synthesis,
                    None if synthesis is None else _hash(synthesis),
                    failure_code,
                    now,
                ),
            )
            if status == "completed":
                for item in ranked:
                    self.connection.execute(
                        """
                        INSERT INTO research_findings(
                            id, run_id, question_id, claim, claim_hash,
                            evidence_reference_ids_json, confidence_micros,
                            evidence_score_micros, rank, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            run_id,
                            item.question_id,
                            item.claim,
                            item.claim_hash,
                            _canonical(item.evidence_reference_ids),
                            round(item.confidence * 1_000_000),
                            round(item.evidence_score * 1_000_000),
                            item.rank,
                            now,
                        ),
                    )
        if status != "completed":
            raise ResearchExecutionError(failure_code or status, run_id)
        return self.get_run(run_id)

    def benchmark(
        self,
        plan_id: str,
        adapter: ResearchWorkerAdapter,
        *,
        quality_evaluator: QualityEvaluator | None = None,
    ) -> dict[str, object]:
        serial = self.execute(
            plan_id, adapter, mode="serial",
            quality_evaluator=quality_evaluator,
        )
        parallel = self.execute(
            plan_id, adapter, mode="parallel",
            quality_evaluator=quality_evaluator,
        )
        latency_delta = int(serial["latency_ms"]) - int(parallel["latency_ms"])
        latency_improved = latency_delta > 0
        quality_delta: int | None = None
        quality_improved: bool | None = None
        if serial["quality"] is not None and parallel["quality"] is not None:
            quality_delta = round(
                (float(parallel["quality"]) - float(serial["quality"]))
                * 1_000_000
            )
            quality_improved = quality_delta > 0
        if latency_improved or quality_improved is True:
            recommendation = "parallel_supported"
        elif quality_improved is None:
            recommendation = "insufficient_quality_evidence"
        elif int(parallel["latency_ms"]) > int(serial["latency_ms"]):
            recommendation = "serial_preferred"
        else:
            recommendation = "no_measured_benefit"
        benchmark_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO research_parallel_benchmarks(
                    id, plan_id, serial_run_id, parallel_run_id,
                    latency_delta_ms, quality_delta_micros,
                    latency_improved, quality_improved,
                    recommendation, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    benchmark_id,
                    plan_id,
                    serial["id"],
                    parallel["id"],
                    latency_delta,
                    quality_delta,
                    int(latency_improved),
                    None if quality_improved is None else int(quality_improved),
                    recommendation,
                    utc_now(),
                ),
            )
        return self.get_benchmark(benchmark_id)

    def get_run(self, run_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM research_runs WHERE id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        findings = self.connection.execute(
            """
            SELECT * FROM research_findings
            WHERE run_id=? ORDER BY rank, claim_hash
            """,
            (run_id,),
        ).fetchall()
        return {
            "id": row["id"],
            "plan_id": row["plan_id"],
            "mode": row["mode"],
            "adapter_id": row["adapter_id"],
            "status": row["status"],
            "latency_ms": row["latency_ms"],
            "quality": (
                None
                if row["quality_micros"] is None
                else row["quality_micros"] / 1_000_000
            ),
            "raw_finding_count": row["raw_finding_count"],
            "deduplicated_finding_count": row["deduplicated_finding_count"],
            "synthesis": row["synthesis"],
            "synthesis_hash": row["synthesis_hash"],
            "failure_code": row["failure_code"],
            "findings": [
                {
                    "question_id": item["question_id"],
                    "claim": item["claim"],
                    "claim_hash": item["claim_hash"],
                    "evidence_reference_ids": json.loads(
                        item["evidence_reference_ids_json"]
                    ),
                    "confidence": item["confidence_micros"] / 1_000_000,
                    "evidence_score": (
                        item["evidence_score_micros"] / 1_000_000
                    ),
                    "rank": item["rank"],
                }
                for item in findings
            ],
            "created_at": row["created_at"],
        }

    def get_benchmark(self, benchmark_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM research_parallel_benchmarks WHERE id=?",
            (benchmark_id,),
        ).fetchone()
        if row is None:
            raise KeyError(benchmark_id)
        return {
            "id": row["id"],
            "plan_id": row["plan_id"],
            "serial_run_id": row["serial_run_id"],
            "parallel_run_id": row["parallel_run_id"],
            "latency_delta_ms": row["latency_delta_ms"],
            "quality_delta": (
                None
                if row["quality_delta_micros"] is None
                else row["quality_delta_micros"] / 1_000_000
            ),
            "latency_improved": bool(row["latency_improved"]),
            "quality_improved": (
                None
                if row["quality_improved"] is None
                else bool(row["quality_improved"])
            ),
            "recommendation": row["recommendation"],
            "created_at": row["created_at"],
        }
