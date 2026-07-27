from __future__ import annotations

import hashlib
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
from .skill_format import SkillPackageLoader
from .skill_registry import SkillRegistry
from .write_controller import content_risk_flags


@dataclass(frozen=True)
class SkillGenerationConfig:
    minimum_occurrences: int = 3
    minimum_significance: float = 0.60
    expensive_reasoning_tokens: int = 500
    generator: str = "repeated-success-v1"

    def __post_init__(self) -> None:
        if self.minimum_occurrences < 3:
            raise ValueError("minimum_occurrences must be at least 3")
        if not 0 <= self.minimum_significance <= 1:
            raise ValueError("minimum_significance must be 0..1")
        if self.expensive_reasoning_tokens < 1:
            raise ValueError("expensive_reasoning_tokens must be positive")
        if not self.generator.strip():
            raise ValueError("generator cannot be empty")


@dataclass(frozen=True)
class SkillGenerationCandidate:
    id: str
    pattern_hash: str
    trigger_kind: str
    scope: str
    task_class: str
    occurrence_count: int
    average_significance: float
    procedure: str
    applicability: tuple[str, ...]
    inputs: dict[str, str]
    outputs: dict[str, str]
    verification: tuple[str, ...]
    failure_modes: tuple[str, ...]
    permissions: tuple[str, ...]
    tools: tuple[str, ...]
    evidence: tuple[str, ...]
    trace_ids: tuple[str, ...]
    status: str = "proposed"
    package_path: str | None = None
    skill_id: str | None = None
    error_type: str | None = None
    created_at: str = ""
    applied_at: str | None = None


@dataclass(frozen=True)
class SkillGenerationPlan:
    id: str
    status: str
    scope: str | None
    config: SkillGenerationConfig
    candidates: tuple[SkillGenerationCandidate, ...]
    created_at: str
    applied_at: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "status": self.status,
            "scope": self.scope,
            "config": asdict(self.config),
            "candidates": [asdict(item) for item in self.candidates],
            "created_at": self.created_at,
            "applied_at": self.applied_at,
        }


class SkillGenerator:
    """Creates reviewable v1 skill packages from repeated successful evidence."""

    TRIGGERS = frozenset(
        {
            "repeated_successful_procedure",
            "repeated_expensive_reasoning",
            "repeated_tool_sequence",
            "repeated_human_instruction",
        }
    )

    def __init__(
        self,
        connection: sqlite3.Connection,
        registry: SkillRegistry,
        skills_dir: str | Path,
        *,
        loader: SkillPackageLoader | None = None,
        config: SkillGenerationConfig | None = None,
    ) -> None:
        self.connection = connection
        self.registry = registry
        self.skills_dir = Path(skills_dir)
        self.loader = loader or SkillPackageLoader()
        self.config = config or SkillGenerationConfig()

    @staticmethod
    def _normalized(content: str) -> str:
        return " ".join(content.casefold().split())

    @staticmethod
    def _metadata(event: dict[str, object]) -> dict[str, object]:
        try:
            value = json.loads(str(event.get("metadata_json", "{}")))
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _trigger(
        self, event: dict[str, object], metadata: dict[str, object]
    ) -> str | None:
        kind = str(event.get("kind", ""))
        if (
            metadata.get("human_instruction") is True
            or metadata.get("source") == "human"
        ):
            return "repeated_human_instruction"
        reasoning_tokens = metadata.get("reasoning_tokens", 0)
        if (
            isinstance(reasoning_tokens, int)
            and not isinstance(reasoning_tokens, bool)
            and reasoning_tokens >= self.config.expensive_reasoning_tokens
        ):
            return "repeated_expensive_reasoning"
        if kind == "tool_sequence":
            return "repeated_tool_sequence"
        if kind in {"procedure", "candidate_skill"}:
            return "repeated_successful_procedure"
        return None

    @staticmethod
    def _string_list(
        metadata: dict[str, object], field: str
    ) -> tuple[str, ...]:
        value = metadata.get(field, [])
        if not isinstance(value, list):
            return ()
        return tuple(
            dict.fromkeys(
                item.strip()
                for item in value
                if isinstance(item, str) and item.strip()
            )
        )

    @staticmethod
    def _interface(
        metadata: dict[str, object], field: str, fallback: dict[str, str]
    ) -> dict[str, str]:
        value = metadata.get(field)
        if not isinstance(value, dict):
            return fallback
        result = {
            str(key).strip(): str(item).strip()
            for key, item in value.items()
            if str(key).strip() and str(item).strip()
        }
        return result or fallback

    def plan(
        self,
        *,
        scope: str | None = None,
        manage_transaction: bool = True,
    ) -> SkillGenerationPlan:
        if scope is not None and not scope.strip():
            raise ValueError("scope cannot be empty")
        rows = self.connection.execute(
            """
            SELECT id, scope, task_class, significance_score, raw_trace_json
            FROM experience_traces
            WHERE outcome = 'succeeded' AND significance_score >= ?
              AND (? IS NULL OR scope = ?)
            ORDER BY created_at, id
            """,
            (self.config.minimum_significance, scope, scope),
        ).fetchall()
        grouped: dict[tuple[str, str, str, str], list[dict[str, object]]] = {}
        failures: dict[tuple[str, str], list[str]] = {}
        failure_rows = self.connection.execute(
            """
            SELECT scope, task_class, raw_trace_json
            FROM experience_traces
            WHERE (? IS NULL OR scope = ?)
            ORDER BY created_at, id
            """,
            (scope, scope),
        ).fetchall()
        for row in failure_rows:
            raw = json.loads(row["raw_trace_json"])
            for event in raw["events"]:
                if event.get("kind") == "failure":
                    content = str(event.get("content", "")).strip()
                    if content and not content_risk_flags(content):
                        failures.setdefault(
                            (row["scope"], row["task_class"]), []
                        ).append(content)
        for row in rows:
            raw = json.loads(row["raw_trace_json"])
            for event in raw["events"]:
                content = str(event.get("content", "")).strip()
                if not content:
                    continue
                if event.get("kind") == "failure":
                    continue
                metadata = self._metadata(event)
                trigger = self._trigger(event, metadata)
                if trigger is None:
                    continue
                normalized = self._normalized(content)
                key = (row["scope"], row["task_class"], trigger, normalized)
                grouped.setdefault(key, []).append(
                    {
                        "trace_id": row["id"],
                        "significance": row["significance_score"],
                        "content": content,
                        "event": event,
                        "metadata": metadata,
                    }
                )

        created_at = utc_now()
        candidates: list[SkillGenerationCandidate] = []
        for (
            candidate_scope,
            task_class,
            trigger,
            normalized,
        ), items in grouped.items():
            trace_ids = tuple(
                dict.fromkeys(str(item["trace_id"]) for item in items)
            )
            if len(trace_ids) < self.config.minimum_occurrences:
                continue
            procedure = str(items[0]["content"])
            if content_risk_flags(procedure):
                continue
            pattern_hash = hashlib.sha256(
                f"{candidate_scope}\0{task_class}\0{trigger}\0{normalized}".encode(
                    "utf-8"
                )
            ).hexdigest()
            already_generated = self.connection.execute(
                """
                SELECT 1 FROM skill_generation_candidates
                WHERE pattern_hash = ? AND status = 'generated'
                LIMIT 1
                """,
                (pattern_hash,),
            ).fetchone()
            if already_generated:
                continue
            metadata_items = [item["metadata"] for item in items]
            evidence = tuple(
                dict.fromkeys(
                    (
                        *(
                            str(reference)
                            for item in items
                            for reference in item["event"].get("evidence", [])
                            if str(reference).strip()
                        ),
                        *(f"trace:{trace_id}" for trace_id in trace_ids),
                    )
                )
            )
            permissions = tuple(
                dict.fromkeys(
                    permission
                    for metadata in metadata_items
                    for permission in self._string_list(metadata, "permissions")
                )
            )
            tools = tuple(
                dict.fromkeys(
                    tool
                    for metadata in metadata_items
                    for tool in self._string_list(metadata, "tools")
                )
            )
            verification = tuple(
                dict.fromkeys(
                    criterion
                    for metadata in metadata_items
                    for criterion in self._string_list(metadata, "verification")
                )
            ) or (
                "Run focused unit tests and scenario tests before promotion",
                "Compare the candidate against the no-skill baseline",
            )
            failure_modes = tuple(
                dict.fromkeys(failures.get((candidate_scope, task_class), ()))
            ) or (
                "The task falls outside the declared applicability boundary",
                "Verification cannot establish a correct result",
            )
            metadata = metadata_items[0]
            applicability = self._string_list(metadata, "applicability") or (
                f"Tasks in class: {task_class}",
                procedure,
            )
            inputs = self._interface(
                metadata,
                "inputs",
                {"task": "task matching the applicability boundary"},
            )
            outputs = self._interface(
                metadata,
                "outputs",
                {"result": "verified result with evidence"},
            )
            generated_text = "\n".join(
                (
                    procedure,
                    *applicability,
                    *inputs.keys(),
                    *inputs.values(),
                    *outputs.keys(),
                    *outputs.values(),
                    *verification,
                    *failure_modes,
                    *permissions,
                    *tools,
                )
            )
            if content_risk_flags(generated_text):
                continue
            candidates.append(
                SkillGenerationCandidate(
                    id=str(uuid.uuid4()),
                    pattern_hash=pattern_hash,
                    trigger_kind=trigger,
                    scope=candidate_scope,
                    task_class=task_class,
                    occurrence_count=len(trace_ids),
                    average_significance=round(
                        sum(float(item["significance"]) for item in items)
                        / len(items),
                        6,
                    ),
                    procedure=procedure,
                    applicability=applicability,
                    inputs=inputs,
                    outputs=outputs,
                    verification=verification,
                    failure_modes=failure_modes,
                    permissions=permissions,
                    tools=tools,
                    evidence=evidence,
                    trace_ids=trace_ids,
                    created_at=created_at,
                )
            )
        candidates.sort(
            key=lambda item: (
                item.scope, item.task_class, item.trigger_kind,
                item.pattern_hash,
            )
        )
        plan = SkillGenerationPlan(
            id=str(uuid.uuid4()),
            status="planned",
            scope=scope,
            config=self.config,
            candidates=tuple(candidates),
            created_at=created_at,
        )
        self._save(plan, manage_transaction=manage_transaction)
        return plan

    def _save(
        self, plan: SkillGenerationPlan, *, manage_transaction: bool = True
    ) -> None:
        try:
            self.connection.execute(
                """
                INSERT INTO skill_generation_runs(
                    id, status, scope, config_json, candidate_count,
                    created_at, applied_at
                ) VALUES (?, 'planned', ?, ?, ?, ?, NULL)
                """,
                (
                    plan.id, plan.scope, json.dumps(asdict(plan.config)),
                    len(plan.candidates), plan.created_at,
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO skill_generation_candidates(
                    id, run_id, pattern_hash, trigger_kind, scope, task_class,
                    occurrence_count, average_significance, procedure,
                    applicability_json, inputs_json, outputs_json,
                    verification_json, failure_modes_json, permissions_json,
                    tools_json, evidence_json, trace_ids_json, status,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, 'proposed', ?)
                """,
                (
                    (
                        item.id, plan.id, item.pattern_hash, item.trigger_kind,
                        item.scope, item.task_class, item.occurrence_count,
                        item.average_significance, item.procedure,
                        json.dumps(item.applicability),
                        json.dumps(item.inputs), json.dumps(item.outputs),
                        json.dumps(item.verification),
                        json.dumps(item.failure_modes),
                        json.dumps(item.permissions), json.dumps(item.tools),
                        json.dumps(item.evidence), json.dumps(item.trace_ids),
                        item.created_at,
                    )
                    for item in plan.candidates
                ),
            )
            if manage_transaction:
                self.connection.commit()
        except Exception:
            if manage_transaction:
                self.connection.rollback()
            raise

    def load(self, run_id: str) -> SkillGenerationPlan:
        run = self.connection.execute(
            "SELECT * FROM skill_generation_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None:
            raise KeyError(run_id)
        rows = self.connection.execute(
            """
            SELECT * FROM skill_generation_candidates
            WHERE run_id = ? ORDER BY scope, task_class, trigger_kind, pattern_hash
            """,
            (run_id,),
        ).fetchall()
        config = SkillGenerationConfig(**json.loads(run["config_json"]))
        return SkillGenerationPlan(
            id=run["id"],
            status=run["status"],
            scope=run["scope"],
            config=config,
            candidates=tuple(
                SkillGenerationCandidate(
                    id=row["id"],
                    pattern_hash=row["pattern_hash"],
                    trigger_kind=row["trigger_kind"],
                    scope=row["scope"],
                    task_class=row["task_class"],
                    occurrence_count=row["occurrence_count"],
                    average_significance=row["average_significance"],
                    procedure=row["procedure"],
                    applicability=tuple(json.loads(row["applicability_json"])),
                    inputs=json.loads(row["inputs_json"]),
                    outputs=json.loads(row["outputs_json"]),
                    verification=tuple(json.loads(row["verification_json"])),
                    failure_modes=tuple(json.loads(row["failure_modes_json"])),
                    permissions=tuple(json.loads(row["permissions_json"])),
                    tools=tuple(json.loads(row["tools_json"])),
                    evidence=tuple(json.loads(row["evidence_json"])),
                    trace_ids=tuple(json.loads(row["trace_ids_json"])),
                    status=row["status"],
                    package_path=row["package_path"],
                    skill_id=row["skill_id"],
                    error_type=row["error_type"],
                    created_at=row["created_at"],
                    applied_at=row["applied_at"],
                )
                for row in rows
            ),
            created_at=run["created_at"],
            applied_at=run["applied_at"],
        )

    @staticmethod
    def _slug(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
        return (normalized or "procedure")[:72]

    def _write_package(self, item: SkillGenerationCandidate) -> Path:
        base = (self.skills_dir / "generated").resolve()
        base.mkdir(parents=True, exist_ok=True)
        manifest_id = (
            f"generated-{self._slug(item.task_class)}-"
            f"{item.pattern_hash[:10]}"
        )[:127]
        final = (base / f"{manifest_id}-0.1.0").resolve()
        if base not in final.parents:
            raise ValueError("Generated skill path escapes the skills directory")
        if final.exists():
            self.loader.load(final)
            return final
        temporary = Path(tempfile.mkdtemp(prefix=".candidate-", dir=base))
        try:
            for name in ("examples", "tests", "scripts", "assets"):
                (temporary / name).mkdir()
            instructions = (
                f"# {item.task_class} procedure\n\n"
                "## Applicability boundaries\n\n"
                + "\n".join(f"- {value}" for value in item.applicability)
                + "\n\n## Inputs\n\n"
                + "\n".join(
                    f"- `{key}`: {value}" for key, value in item.inputs.items()
                )
                + "\n\n## Outputs\n\n"
                + "\n".join(
                    f"- `{key}`: {value}" for key, value in item.outputs.items()
                )
                + f"\n\n## Procedure\n\n{item.procedure}"
                + "\n\n## Verification criteria\n\n"
                + "\n".join(f"- {value}" for value in item.verification)
                + "\n\n## Known failure modes\n\n"
                + "\n".join(f"- {value}" for value in item.failure_modes)
            )
            manifest = {
                "id": manifest_id,
                "name": (
                    f"Generated {item.task_class} "
                    f"{item.trigger_kind.replace('_', ' ')} "
                    f"{item.pattern_hash[:8]}"
                ),
                "version": "0.1.0",
                "description": (
                    f"Experimental candidate from {item.occurrence_count} "
                    f"successful {item.trigger_kind.replace('_', ' ')} traces."
                ),
                "task_classes": [item.task_class],
                "inputs": item.inputs,
                "outputs": item.outputs,
                "dependencies": [],
                "permissions": list(item.permissions),
                "tools": list(item.tools),
                "models": ["any"],
                "token_estimate": estimate_tokens(instructions),
                "applicability": list(item.applicability),
                "contraindications": list(item.failure_modes),
                "verification": list(item.verification),
                "author": "ACR skill generator",
                "origin": f"experience:{item.pattern_hash}",
                "created_at": item.created_at,
                "updated_at": item.created_at,
                "status": "experimental",
                "reliability": min(0.49, item.average_significance / 2),
            }
            (temporary / "SKILL.yaml").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            (temporary / "instructions.md").write_text(
                instructions + "\n", encoding="utf-8"
            )
            (temporary / "history.jsonl").write_text(
                json.dumps(
                    {
                        "version": "0.1.0",
                        "event": "generated_from_repeated_success",
                        "trigger": item.trigger_kind,
                        "evidence": list(item.evidence),
                        "created_at": item.created_at,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            (temporary / "tests" / "scenarios.json").write_text(
                json.dumps(
                    {
                        "status": "declarative_only",
                        "verification": list(item.verification),
                        "promotion_required": True,
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

    def approve(self, run_id: str) -> SkillGenerationPlan:
        plan = self.load(run_id)
        if plan.status != "planned":
            raise ValueError("Only a planned skill-generation run can be approved")
        failures = 0
        for item in plan.candidates:
            try:
                package_path = self._write_package(item)
                admitted = self.registry.admit(package_path)
                if (
                    admitted["status"] != "quarantine"
                    or admitted["lifecycle_status"] != "quarantined"
                ):
                    raise RuntimeError("Generated skill escaped quarantine")
                with self.connection:
                    self.connection.execute(
                        """
                        UPDATE skill_generation_candidates
                        SET status = 'generated', package_path = ?, skill_id = ?,
                            applied_at = ?
                        WHERE id = ?
                        """,
                        (
                            str(package_path), admitted["id"], utc_now(), item.id,
                        ),
                    )
            except Exception as error:
                failures += 1
                with self.connection:
                    self.connection.execute(
                        """
                        UPDATE skill_generation_candidates
                        SET status = 'error', error_type = ?, applied_at = ?
                        WHERE id = ?
                        """,
                        (type(error).__name__, utc_now(), item.id),
                    )
        with self.connection:
            self.connection.execute(
                """
                UPDATE skill_generation_runs
                SET status = ?, applied_at = ? WHERE id = ?
                """,
                (
                    "partially_applied" if failures else "applied",
                    utc_now(), run_id,
                ),
            )
        return self.load(run_id)
