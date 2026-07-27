from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from dataclasses import dataclass

from .memory import utc_now
from .scoring import query_terms
from .tool_registry import ToolAccessRequest, ToolRegistry, _strings

INTENTS = {
    "calculator": frozenset({"calculate", "calculation", "math", "arithmetic", "sum"}),
    "filesystem": frozenset({"file", "files", "filesystem", "folder", "directory", "search"}),
    "database": frozenset({"database", "sql", "sqlite", "query", "table"}),
    "web": frozenset({"current", "latest", "today", "web", "online", "news"}),
}
RISK = {"READ_ONLY": 0.0, "REVERSIBLE_WRITE": 0.4, "DESTRUCTIVE": 1.0}


@dataclass(frozen=True)
class ToolRouteRequest:
    task: str
    task_class: str
    granted_permissions: tuple[str, ...]
    network_allowed: bool
    filesystem_access: str
    available_credentials: tuple[str, ...]
    approval_reference: str | None = None
    max_tools: int = 3
    max_cost: float = 1_000_000.0
    max_latency_ms: int = 2_147_483_647

    def __post_init__(self) -> None:
        if not self.task.strip() or not self.task_class.strip():
            raise ValueError("Tool route task and task_class cannot be empty")
        if type(self.network_allowed) is not bool:
            raise ValueError("network_allowed must be a boolean")
        if not 1 <= self.max_tools <= 8:
            raise ValueError("max_tools must be between 1 and 8")
        if self.max_cost < 0 or self.max_latency_ms < 0:
            raise ValueError("Tool route budgets cannot be negative")

    @classmethod
    def from_dict(cls, payload: object) -> "ToolRouteRequest":
        if not isinstance(payload, dict):
            raise ValueError("Tool route request must be an object")
        required = {
            "task", "task_class", "granted_permissions", "network_allowed",
            "filesystem_access", "available_credentials",
        }
        optional = {
            "approval_reference", "max_tools", "max_cost", "max_latency_ms",
        }
        if not required <= set(payload) or set(payload) - required - optional:
            raise ValueError(f"Tool route request requires {sorted(required)}")
        if not isinstance(payload["network_allowed"], bool):
            raise ValueError("network_allowed must be a boolean")
        return cls(
            task=str(payload["task"]), task_class=str(payload["task_class"]),
            granted_permissions=_strings(
                payload["granted_permissions"], "granted_permissions"
            ),
            network_allowed=payload["network_allowed"],
            filesystem_access=str(payload["filesystem_access"]),
            available_credentials=_strings(
                payload["available_credentials"], "available_credentials"
            ),
            approval_reference=(
                None if payload.get("approval_reference") is None
                else str(payload["approval_reference"]).strip()
            ),
            max_tools=int(payload.get("max_tools", 3)),
            max_cost=float(payload.get("max_cost", 1_000_000.0)),
            max_latency_ms=int(payload.get("max_latency_ms", 2_147_483_647)),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "task_hash": hashlib.sha256(self.task.encode("utf-8")).hexdigest(),
            "task_class": self.task_class,
            "granted_permissions": list(self.granted_permissions),
            "network_allowed": self.network_allowed,
            "filesystem_access": self.filesystem_access,
            "available_credentials": list(self.available_credentials),
            "approval_reference_present": bool(self.approval_reference),
            "max_tools": self.max_tools, "max_cost": self.max_cost,
            "max_latency_ms": self.max_latency_ms,
        }


@dataclass(frozen=True)
class ToolOutcome:
    route_id: str
    tool_name: str
    success: bool
    latency_ms: int
    cost: float
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.route_id or not self.tool_name:
            raise ValueError("Tool outcome route and tool cannot be empty")
        if self.latency_ms < 0 or self.cost < 0:
            raise ValueError("Tool outcome metrics cannot be negative")
        if not self.evidence or any(not item.strip() for item in self.evidence):
            raise ValueError("Tool outcome requires evidence")

    @classmethod
    def from_dict(cls, payload: object) -> "ToolOutcome":
        fields = {"route_id", "tool_name", "success", "latency_ms", "cost", "evidence"}
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError(f"Tool outcome must contain {sorted(fields)} only")
        if not isinstance(payload["success"], bool) or not isinstance(
            payload["evidence"], list
        ):
            raise ValueError("Tool outcome success/evidence types are invalid")
        return cls(
            route_id=str(payload["route_id"]), tool_name=str(payload["tool_name"]),
            success=payload["success"], latency_ms=int(payload["latency_ms"]),
            cost=float(payload["cost"]),
            evidence=tuple(str(item) for item in payload["evidence"]),
        )


class ToolRouter:
    def __init__(self, connection: sqlite3.Connection, registry: ToolRegistry) -> None:
        self.connection = connection
        self.registry = registry

    @staticmethod
    def _intent(task_terms: frozenset[str]) -> set[str]:
        return {
            name for name, terms in INTENTS.items() if task_terms & terms
        }

    def route(self, request: ToolRouteRequest) -> dict[str, object]:
        task_terms = frozenset(query_terms(request.task))
        intents = self._intent(task_terms)
        candidates: list[dict[str, object]] = []
        for tool in self.registry.list():
            access = self.registry.authorize(ToolAccessRequest(
                tool_name=tool["name"],
                granted_permissions=request.granted_permissions,
                network_allowed=request.network_allowed,
                filesystem_access=request.filesystem_access,
                available_credentials=request.available_credentials,
                approval_reference=request.approval_reference,
            ))
            metadata_terms = frozenset(query_terms(
                f"{tool['name']} {tool['description']}"
            ))
            lexical = len(task_terms & metadata_terms) / max(1, len(task_terms))
            matched_intents = {
                intent for intent in intents
                if intent in metadata_terms or any(
                    term in metadata_terms for term in INTENTS[intent]
                )
            }
            relevance = min(1.0, lexical + (0.65 if matched_intents else 0.0))
            history = self.connection.execute(
                """
                SELECT COUNT(*) AS uses, COALESCE(SUM(success), 0) AS successes,
                       AVG(latency_ms) AS latency, AVG(cost) AS cost
                FROM tool_outcomes
                WHERE tool_name=? AND task_class=?
                """,
                (tool["name"], request.task_class),
            ).fetchone()
            uses, successes = int(history["uses"]), int(history["successes"])
            reliability = (successes + 1) / (uses + 2)
            latency = float(
                history["latency"]
                if history["latency"] is not None
                else tool["latency_estimate_ms"]
            )
            cost = float(history["cost"] if history["cost"] is not None else tool["cost"])
            reasons = list(access["rejection_reasons"])
            if relevance <= 0:
                reasons.append("not_relevant")
            if latency > request.max_latency_ms:
                reasons.append("latency_budget")
            if cost > request.max_cost:
                reasons.append("cost_budget")
            score = (
                0.50 * relevance + 0.30 * reliability
                + 0.10 * (1 / (1 + latency / 1000))
                + 0.05 * (1 / (1 + cost))
                - 0.15 * RISK[tool["side_effect"]]
            )
            candidates.append({
                "tool_name": tool["name"], "eligible": not reasons,
                "rejection_reasons": reasons, "relevance": relevance,
                "matched_intents": sorted(matched_intents),
                "historical_uses": uses, "historical_reliability": reliability,
                "expected_latency_ms": latency, "expected_cost": cost,
                "side_effect": tool["side_effect"], "score": score,
                "selected": False,
            })
        eligible = sorted(
            (item for item in candidates if item["eligible"]),
            key=lambda item: (-float(item["score"]), str(item["tool_name"])),
        )
        selected: list[dict[str, object]] = []
        covered: set[str] = set()
        for candidate in eligible:
            candidate_intents = set(candidate["matched_intents"])
            if selected and candidate_intents <= covered:
                continue
            candidate["selected"] = True
            selected.append(candidate)
            covered |= candidate_intents
            if len(selected) >= request.max_tools or (intents and covered >= intents):
                break
            if not intents:
                break
        route_id = str(uuid.uuid4())
        prohibits_simulation = bool(
            intents and any(
                set(item["matched_intents"]) & intents for item in candidates
            )
        )
        self.connection.execute(
            """
            INSERT INTO tool_routes (
                id, task_class, request_json, selected_tools_json,
                deterministic_tool_required, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (route_id, request.task_class, json.dumps(request.as_dict()),
             json.dumps([item["tool_name"] for item in selected]),
             prohibits_simulation, utc_now()),
        )
        for sequence, candidate in enumerate(candidates, 1):
            self.connection.execute(
                """
                INSERT INTO tool_route_candidates (
                    route_id, sequence, tool_name, selected, candidate_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (route_id, sequence, candidate["tool_name"],
                 candidate["selected"], json.dumps(candidate)),
            )
        self.connection.commit()
        return self.get(route_id)

    def record_outcome(self, outcome: ToolOutcome) -> str:
        route = self.get(outcome.route_id)
        if outcome.tool_name not in route["selected_tools"]:
            raise ValueError("Outcome tool was not selected by this route")
        existing = self.connection.execute(
            "SELECT 1 FROM tool_outcomes WHERE route_id=? AND tool_name=?",
            (outcome.route_id, outcome.tool_name),
        ).fetchone()
        if existing:
            raise ValueError("Tool outcome is append-only and already recorded")
        outcome_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO tool_outcomes (
                id, route_id, tool_name, task_class, success, latency_ms,
                cost, evidence_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (outcome_id, outcome.route_id, outcome.tool_name,
             route["task_class"], outcome.success, outcome.latency_ms,
             outcome.cost, json.dumps(outcome.evidence), utc_now()),
        )
        self.connection.commit()
        return outcome_id

    def get(self, route_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM tool_routes WHERE id=?", (route_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown tool route: {route_id}")
        candidates = [
            json.loads(item["candidate_json"])
            for item in self.connection.execute(
                "SELECT candidate_json FROM tool_route_candidates "
                "WHERE route_id=? ORDER BY sequence",
                (route_id,),
            )
        ]
        return {
            "id": row["id"], "task_class": row["task_class"],
            "request": json.loads(row["request_json"]),
            "selected_tools": json.loads(row["selected_tools_json"]),
            "deterministic_tool_required": bool(row["deterministic_tool_required"]),
            "candidates": candidates, "created_at": row["created_at"],
        }
