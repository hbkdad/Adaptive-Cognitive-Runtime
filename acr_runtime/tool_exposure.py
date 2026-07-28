from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass

from .agent_spec import AgentSpecRegistry
from .memory import utc_now
from .permissions import CapabilityCheck, PermissionController
from .scoring import estimate_tokens
from .secret_management import assert_secret_free
from .tool_registry import ToolAccessRequest, ToolRegistry
from .tool_router import AGENT_ALLOWLIST_SELECTOR, ToolRouter

HASH = re.compile(r"^[0-9a-f]{64}$")
OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
TASK_CLASS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
SELECTOR_VERSION = "acr-direct-filtered-v1.0.0"
ESTIMATE_VERSION = "acr-json-char-estimate-v1.0.0"
MEASURED_TOKEN_QUALITIES = {"provider_reported", "locally_measured"}


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _checked_hash(value: str, field: str) -> str:
    if not HASH.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _tool_payload(tool: dict[str, object]) -> dict[str, object]:
    # Canonical descriptions and input schemas are projected without rewriting.
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }


@dataclass(frozen=True)
class ToolExposureBenchmarkSpec:
    task_class: str
    agent_spec_hash: str
    catalog_hash: str
    selector_hash: str
    dataset_hash: str
    model_hash: str
    settings_hash: str
    evaluator_hash: str
    case_hashes: tuple[str, ...]
    seed: int
    expected_cases: int
    quality_margin_micros: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.task_class, str):
            raise ValueError("task_class must be a string")
        if not TASK_CLASS.fullmatch(self.task_class):
            raise ValueError("task_class must be a bounded taxonomy identifier")
        assert_secret_free(self.task_class, "tool exposure task_class")
        for field in (
            "agent_spec_hash", "catalog_hash", "selector_hash", "dataset_hash",
            "model_hash", "settings_hash", "evaluator_hash",
        ):
            _checked_hash(str(getattr(self, field)), field)
        for field in ("seed", "expected_cases", "quality_margin_micros"):
            if type(getattr(self, field)) is not int:
                raise ValueError(f"{field} must be an integer")
        if not 5 <= self.expected_cases <= 1000:
            raise ValueError("expected_cases must be between 5 and 1000")
        if (
            len(self.case_hashes) != self.expected_cases
            or len(set(self.case_hashes)) != len(self.case_hashes)
        ):
            raise ValueError("case_hashes must contain every distinct expected case")
        for case_hash in self.case_hashes:
            if not isinstance(case_hash, str):
                raise ValueError("case_hashes must contain strings")
            _checked_hash(case_hash, "case_hash")
        if self.dataset_hash != _digest(list(self.case_hashes)):
            raise ValueError("dataset_hash does not match sealed case membership")
        if not 0 <= self.quality_margin_micros <= 100_000:
            raise ValueError("quality_margin_micros must be between 0 and 100000")

    @classmethod
    def from_dict(cls, payload: object) -> "ToolExposureBenchmarkSpec":
        fields = {
            "task_class", "agent_spec_hash", "catalog_hash", "selector_hash",
            "dataset_hash", "model_hash", "settings_hash", "evaluator_hash",
            "case_hashes", "seed", "expected_cases", "quality_margin_micros",
        }
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError(f"benchmark spec must contain {sorted(fields)} only")
        converted = dict(payload)
        if not isinstance(converted["case_hashes"], list):
            raise ValueError("case_hashes must be a list")
        converted["case_hashes"] = tuple(converted["case_hashes"])
        return cls(**converted)


@dataclass(frozen=True)
class ToolExposureTrial:
    run_id: str
    sequence: int
    case_hash: str
    projection_id: str
    arm: str
    attempt_id: str
    success: bool
    quality_micros: int
    required_tool_recall_micros: int
    hard_violation: bool
    unauthorized_exposure_count: int
    invalid_call_count: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    token_quality: str
    latency_ms: int
    evidence_hash: str

    def __post_init__(self) -> None:
        for field in (
            "run_id", "case_hash", "projection_id", "arm", "attempt_id",
            "token_quality", "evidence_hash",
        ):
            if not isinstance(getattr(self, field), str):
                raise ValueError(f"{field} must be a string")
        if not self.run_id or not self.projection_id:
            raise ValueError("run_id and projection_id cannot be empty")
        _checked_hash(self.case_hash, "case_hash")
        _checked_hash(self.evidence_hash, "evidence_hash")
        if self.arm not in {"full_authorized", "dynamic"}:
            raise ValueError("arm must be full_authorized or dynamic")
        if not OPAQUE_ID.fullmatch(self.attempt_id):
            raise ValueError("attempt_id must be a bounded opaque identifier")
        assert_secret_free(self.attempt_id, "tool exposure attempt_id")
        for field in ("success", "hard_violation"):
            if type(getattr(self, field)) is not bool:
                raise ValueError(f"{field} must be a boolean")
        if self.sequence < 1:
            raise ValueError("sequence must be positive")
        for field in (
            "quality_micros", "required_tool_recall_micros",
            "unauthorized_exposure_count", "invalid_call_count", "input_tokens",
            "output_tokens", "cached_tokens", "latency_ms",
        ):
            if type(getattr(self, field)) is not int or getattr(self, field) < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        if self.quality_micros > 1_000_000:
            raise ValueError("quality_micros cannot exceed 1000000")
        if self.required_tool_recall_micros > 1_000_000:
            raise ValueError("required_tool_recall_micros cannot exceed 1000000")
        if self.cached_tokens > self.input_tokens:
            raise ValueError("cached_tokens cannot exceed input_tokens")
        if self.token_quality not in {
            "provider_reported", "locally_measured", "estimated", "unknown",
        }:
            raise ValueError("invalid token_quality")

    @classmethod
    def from_dict(cls, payload: object) -> "ToolExposureTrial":
        fields = set(cls.__dataclass_fields__)
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError(f"benchmark trial must contain {sorted(fields)} only")
        return cls(**payload)


class ToolExposureEngine:
    """Immutable, authorization-filtered projections plus paired evaluation."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        registry: ToolRegistry,
        router: ToolRouter,
        agents: AgentSpecRegistry,
        permissions: PermissionController,
    ) -> None:
        self.connection = connection
        self.registry = registry
        self.router = router
        self.agents = agents
        self.permissions = permissions

    @property
    def selector_hash(self) -> str:
        return _digest({"selector_version": SELECTOR_VERSION, "max_tools": 8})

    def _evaluate(
        self, route_id: str, agent_spec_id: str
    ) -> dict[str, object]:
        route = self.router.get(route_id)
        stored = self.agents.inspect(agent_spec_id)
        spec = stored.spec
        reasons: list[str] = []
        request = route["request"]
        if request["subject_type"] != "agent" or request["subject_id"] != spec.id:
            reasons.append("route_agent_mismatch")
        if route["task_class"] not in spec.task_scope:
            reasons.append("task_class_outside_agent_scope")
        expected_allowlist_hash = hashlib.sha256(
            json.dumps(
                sorted(spec.tools), separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        if request.get("exposure_selector") != AGENT_ALLOWLIST_SELECTOR:
            reasons.append("route_not_agent_filtered")
        if (
            request.get("agent_allowlist_hash") != expected_allowlist_hash
            or request.get("agent_allowlist_count") != len(spec.tools)
        ):
            reasons.append("agent_allowlist_changed")

        definitions: dict[str, dict[str, object]] = {}
        for name in spec.tools:
            try:
                definitions[name] = self.registry.get(name)
            except LookupError:
                reasons.append("agent_tool_missing")

        candidates = {item["tool_name"]: item for item in route["candidates"]}
        baseline: list[str] = []
        resource_scope = str(request["resource_scope"])
        for name in sorted(definitions):
            tool = definitions[name]
            candidate = candidates.get(name)
            if candidate is None:
                reasons.append("route_catalog_incomplete")
                continue
            if not set(tool["permissions"]) <= set(spec.permissions):
                continue
            current = True
            granted: list[str] = []
            for capability in tool["permissions"]:
                decision = self.permissions.check(CapabilityCheck(
                    subject_type="agent",
                    subject_id=spec.id,
                    capability=str(capability),
                    resource_scope=resource_scope,
                ))
                current = current and bool(decision["allowed"])
                if decision["allowed"]:
                    granted.append(str(capability))
            if not current:
                continue
            access = self.registry.authorize(ToolAccessRequest(
                tool_name=name,
                granted_permissions=tuple(granted),
                network_allowed=bool(request["network_allowed"]),
                filesystem_access=str(request["filesystem_access"]),
                available_credentials=(),
                approval_reference=None,
            ))
            if not access["allowed"]:
                # Stored routes retain credential labels and approval presence,
                # not live authorizations; those gates therefore deny here.
                continue
            baseline.append(name)

        exposed = list(route["selected_tools"])
        if len(exposed) > 8:
            reasons.append("exposure_limit_exceeded")
        if not set(exposed) <= set(baseline):
            reasons.append("selected_tool_not_currently_authorized")
        if route["deterministic_tool_required"] and not exposed:
            reasons.append("required_tool_missing")

        definition_hashes = {
            name: definitions[name]["definition_hash"] for name in baseline
        }
        catalog_hash = _digest(definition_hashes)
        baseline_payload = [_tool_payload(definitions[name]) for name in baseline]
        exposed_payload = [
            _tool_payload(definitions[name])
            for name in exposed if name in definitions
        ]
        return {
            "route": route,
            "agent_spec_hash": stored.content_hash,
            "catalog_hash": catalog_hash,
            "selector_hash": self.selector_hash,
            "baseline_tools": baseline,
            "exposed_tools": exposed if not reasons else [],
            "definition_hashes": definition_hashes,
            "baseline_estimated_tokens": estimate_tokens(_canonical(baseline_payload)),
            "exposed_estimated_tokens": (
                estimate_tokens(_canonical(exposed_payload)) if not reasons else 0
            ),
            "status": "available" if not reasons else "unavailable",
            "reasons": sorted(set(reasons)),
            "payload": exposed_payload if not reasons else [],
        }

    def route_for_agent(
        self, request: object, agent_spec_id: str
    ) -> dict[str, object]:
        from .tool_router import ToolRouteRequest

        if not isinstance(request, ToolRouteRequest):
            raise TypeError("request must be a ToolRouteRequest")
        stored = self.agents.inspect(agent_spec_id)
        if request.subject_type != "agent" or request.subject_id != agent_spec_id:
            raise ValueError("Agent route identity must match the AgentSpec")
        if request.task_class not in stored.spec.task_scope:
            raise ValueError("Agent route task class is outside its scope")
        return self.router.route(
            request, allowed_tools=frozenset(stored.spec.tools)
        )

    def create_projection(
        self, route_id: str, agent_spec_id: str
    ) -> dict[str, object]:
        result = self._evaluate(route_id, agent_spec_id)
        row = self.connection.execute(
            """
            SELECT id FROM tool_exposure_projections
            WHERE route_id=? AND agent_spec_id=? AND agent_spec_hash=?
              AND catalog_hash=? AND selector_hash=?
            """,
            (
                route_id, agent_spec_id, result["agent_spec_hash"],
                result["catalog_hash"], result["selector_hash"],
            ),
        ).fetchone()
        if row is not None:
            return self.get_projection(row["id"])
        projection_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO tool_exposure_projections(
                id, route_id, agent_spec_id, task_class, agent_spec_hash,
                catalog_hash, selector_hash, mode, baseline_tools_json,
                exposed_tools_json, definition_hashes_json,
                baseline_tool_count, exposed_tool_count,
                baseline_estimated_tokens, exposed_estimated_tokens,
                estimate_version, status, reasons_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'direct_filtered', ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?)
            """,
            (
                projection_id, route_id, agent_spec_id,
                result["route"]["task_class"], result["agent_spec_hash"],
                result["catalog_hash"], result["selector_hash"],
                _canonical(result["baseline_tools"]),
                _canonical(result["exposed_tools"]),
                _canonical(result["definition_hashes"]),
                len(result["baseline_tools"]), len(result["exposed_tools"]),
                result["baseline_estimated_tokens"],
                result["exposed_estimated_tokens"], ESTIMATE_VERSION,
                result["status"], _canonical(result["reasons"]), utc_now(),
            ),
        )
        self.connection.commit()
        return self.get_projection(projection_id)

    def get_projection(self, projection_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM tool_exposure_projections WHERE id=?",
            (projection_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown tool exposure projection: {projection_id}")
        result = dict(row)
        for field in (
            "baseline_tools_json", "exposed_tools_json",
            "definition_hashes_json", "reasons_json",
        ):
            result[field.removesuffix("_json")] = json.loads(result.pop(field))
        return result

    def render(self, projection_id: str) -> dict[str, object]:
        projection = self.get_projection(projection_id)
        if projection["status"] != "available":
            raise PermissionError("Unavailable projections cannot be rendered")
        current = self._evaluate(
            str(projection["route_id"]), str(projection["agent_spec_id"])
        )
        for field in (
            "agent_spec_hash", "catalog_hash", "selector_hash",
            "definition_hashes", "baseline_tools", "exposed_tools",
        ):
            if current[field] != projection[field]:
                raise PermissionError(f"Stale tool exposure projection: {field}")
        if current["status"] != "available":
            raise PermissionError("Tool exposure authorization is no longer current")
        return {
            "projection_id": projection_id,
            "mode": "direct_filtered",
            "tools": current["payload"],
            "tool_count": len(current["payload"]),
            "estimate": {
                "tokens": projection["exposed_estimated_tokens"],
                "version": projection["estimate_version"],
                "quality": "estimated",
            },
        }

    def start_benchmark(
        self, spec: ToolExposureBenchmarkSpec
    ) -> dict[str, object]:
        row = self.connection.execute(
            """
            SELECT id FROM tool_exposure_benchmark_runs
            WHERE task_class=? AND agent_spec_hash=? AND catalog_hash=?
              AND selector_hash=? AND dataset_hash=? AND model_hash=?
              AND settings_hash=? AND evaluator_hash=? AND seed=?
              AND quality_margin_micros=?
            """,
            (
                spec.task_class, spec.agent_spec_hash, spec.catalog_hash,
                spec.selector_hash, spec.dataset_hash, spec.model_hash,
                spec.settings_hash, spec.evaluator_hash, spec.seed,
                spec.quality_margin_micros,
            ),
        ).fetchone()
        if row is not None:
            return self.get_benchmark(row["id"])
        run_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO tool_exposure_benchmark_runs(
                id, task_class, agent_spec_hash, catalog_hash, selector_hash,
                dataset_hash, model_hash, settings_hash, evaluator_hash, seed,
                expected_cases, quality_margin_micros, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)
            """,
            (
                run_id, spec.task_class, spec.agent_spec_hash, spec.catalog_hash,
                spec.selector_hash, spec.dataset_hash, spec.model_hash,
                spec.settings_hash, spec.evaluator_hash, spec.seed,
                spec.expected_cases, spec.quality_margin_micros, utc_now(),
            ),
        )
        self.connection.executemany(
            """
            INSERT INTO tool_exposure_benchmark_cases(run_id, sequence, case_hash)
            VALUES (?, ?, ?)
            """,
            (
                (run_id, sequence, case_hash)
                for sequence, case_hash in enumerate(spec.case_hashes, 1)
            ),
        )
        self.connection.commit()
        return self.get_benchmark(run_id)

    def record_trial(self, trial: ToolExposureTrial) -> str:
        run = self.connection.execute(
            "SELECT * FROM tool_exposure_benchmark_runs WHERE id=?",
            (trial.run_id,),
        ).fetchone()
        if run is None:
            raise LookupError(f"Unknown tool exposure benchmark: {trial.run_id}")
        projection = self.get_projection(trial.projection_id)
        if projection["status"] != "available":
            raise ValueError("Benchmark trials require an available projection")
        self.render(trial.projection_id)
        for run_field, projection_field in (
            ("task_class", "task_class"),
            ("agent_spec_hash", "agent_spec_hash"),
            ("catalog_hash", "catalog_hash"),
            ("selector_hash", "selector_hash"),
        ):
            if run[run_field] != projection[projection_field]:
                raise ValueError("Trial projection does not match benchmark lineage")
        trial_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO tool_exposure_benchmark_trials(
                id, run_id, sequence, case_hash, projection_id, arm, attempt_id,
                success, quality_micros, required_tool_recall_micros,
                hard_violation, unauthorized_exposure_count, invalid_call_count,
                input_tokens, output_tokens, cached_tokens, token_quality,
                latency_ms, evidence_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trial_id, trial.run_id, trial.sequence, trial.case_hash,
                trial.projection_id, trial.arm, trial.attempt_id, trial.success,
                trial.quality_micros, trial.required_tool_recall_micros,
                trial.hard_violation, trial.unauthorized_exposure_count,
                trial.invalid_call_count, trial.input_tokens,
                trial.output_tokens, trial.cached_tokens, trial.token_quality,
                trial.latency_ms, trial.evidence_hash, utc_now(),
            ),
        )
        self.connection.commit()
        return trial_id

    def seal_benchmark(self, run_id: str) -> dict[str, object]:
        run = self.connection.execute(
            "SELECT * FROM tool_exposure_benchmark_runs WHERE id=?", (run_id,)
        ).fetchone()
        if run is None:
            raise LookupError(f"Unknown tool exposure benchmark: {run_id}")
        if run["status"] != "running":
            return self.get_benchmark(run_id)
        trials = self.connection.execute(
            """
            SELECT * FROM tool_exposure_benchmark_trials
            WHERE run_id=? ORDER BY case_hash, arm
            """,
            (run_id,),
        ).fetchall()
        for projection_id in sorted({
            str(trial["projection_id"]) for trial in trials
        }):
            self.render(projection_id)
        pairs: dict[str, dict[str, sqlite3.Row]] = {}
        for trial in trials:
            pairs.setdefault(trial["case_hash"], {})[trial["arm"]] = trial
        complete = (
            len(pairs) == run["expected_cases"]
            and len(trials) == run["expected_cases"] * 2
            and all(set(pair) == {"full_authorized", "dynamic"} for pair in pairs.values())
        )
        if not complete:
            raise ValueError("Benchmark cannot seal until every paired case is complete")

        measured = all(
            trial["token_quality"] in MEASURED_TOKEN_QUALITIES for trial in trials
        )
        candidate_preserved = measured
        baseline_tokens = 0
        dynamic_tokens = 0
        baseline_output_tokens = 0
        dynamic_output_tokens = 0
        baseline_latency_ms = 0
        dynamic_latency_ms = 0
        for pair in pairs.values():
            baseline, dynamic = pair["full_authorized"], pair["dynamic"]
            baseline_tokens += baseline["input_tokens"]
            dynamic_tokens += dynamic["input_tokens"]
            baseline_output_tokens += baseline["output_tokens"]
            dynamic_output_tokens += dynamic["output_tokens"]
            baseline_latency_ms += baseline["latency_ms"]
            dynamic_latency_ms += dynamic["latency_ms"]
            candidate_preserved = candidate_preserved and (
                not baseline["hard_violation"]
                and not dynamic["hard_violation"]
                and bool(baseline["success"])
                and bool(dynamic["success"])
                and baseline["unauthorized_exposure_count"] == 0
                and dynamic["unauthorized_exposure_count"] == 0
                and baseline["invalid_call_count"] == 0
                and dynamic["invalid_call_count"] == 0
                and dynamic["required_tool_recall_micros"] == 1_000_000
                and not (baseline["success"] and not dynamic["success"])
                and dynamic["quality_micros"] + run["quality_margin_micros"]
                    >= baseline["quality_micros"]
                and dynamic["input_tokens"] <= baseline["input_tokens"]
            )
        candidate_preserved = (
            candidate_preserved and dynamic_tokens < baseline_tokens
        )
        if measured and not candidate_preserved:
            status = "rejected"
            recommendation = "reject_dynamic_exposure"
        else:
            status = "insufficient_evidence"
            recommendation = "collect_verified_receipts"
        summary = {
            "case_count": len(pairs),
            "paired_trial_count": len(trials),
            "all_tokens_measured": measured,
            "baseline_input_tokens": baseline_tokens,
            "dynamic_input_tokens": dynamic_tokens,
            "input_token_delta": dynamic_tokens - baseline_tokens,
            "baseline_output_tokens": baseline_output_tokens,
            "dynamic_output_tokens": dynamic_output_tokens,
            "output_token_delta": (
                dynamic_output_tokens - baseline_output_tokens
            ),
            "baseline_latency_ms": baseline_latency_ms,
            "dynamic_latency_ms": dynamic_latency_ms,
            "latency_delta_ms": dynamic_latency_ms - baseline_latency_ms,
            "candidate_quality_preserved": candidate_preserved,
            "candidate_scope": "input_context_only",
            "receipt_provenance": "caller_supplied_unverified",
            "automatic_activation": False,
        }
        self.connection.execute(
            """
            UPDATE tool_exposure_benchmark_runs
            SET status=?, recommendation=?, summary_json=?, completed_at=?
            WHERE id=?
            """,
            (status, recommendation, _canonical(summary), utc_now(), run_id),
        )
        self.connection.commit()
        return self.get_benchmark(run_id)

    def get_benchmark(self, run_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM tool_exposure_benchmark_runs WHERE id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown tool exposure benchmark: {run_id}")
        result = dict(row)
        result["summary"] = (
            None if result["summary_json"] is None
            else json.loads(result["summary_json"])
        )
        result.pop("summary_json")
        result["trials"] = [
            dict(trial) for trial in self.connection.execute(
                """
                SELECT id, sequence, case_hash, projection_id, arm, attempt_id,
                       success, quality_micros, required_tool_recall_micros,
                       hard_violation, unauthorized_exposure_count,
                       invalid_call_count, input_tokens, output_tokens,
                       cached_tokens, token_quality, latency_ms, evidence_hash,
                       created_at
                FROM tool_exposure_benchmark_trials
                WHERE run_id=? ORDER BY sequence
                """,
                (run_id,),
            )
        ]
        return result
