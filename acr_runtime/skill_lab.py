from __future__ import annotations

import difflib
import hashlib
import json
from typing import Any

from .service import AdaptiveRuntime


MAX_SKILLS = 100
MAX_HISTORY = 100
MAX_BENCHMARKS = 20
MAX_INSTRUCTION_CHARS = 100_000
MAX_DIFF_LINES = 800


class SkillLabReader:
    """Bounded Skill Lab projections with explicit provenance and evidence."""

    def __init__(self, runtime: AdaptiveRuntime) -> None:
        self.runtime = runtime
        self.connection = runtime.db.connection

    @staticmethod
    def _success(successes: int, uses: int) -> float | None:
        return successes / uses if uses else None

    @staticmethod
    def _revision(skill: dict[str, Any]) -> str:
        payload = "|".join((
            str(skill["id"]),
            str(skill["content_hash"]),
            str(skill["lifecycle_status"]),
            str(skill["verification_status"]),
        ))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def list(self, *, limit: int = 100) -> dict[str, Any]:
        if not 1 <= limit <= MAX_SKILLS:
            raise ValueError("Skill Lab limit must be between 1 and 100")
        items = []
        for row in self.runtime.skill_registry.list()[:limit]:
            uses = int(row["uses"])
            successes = int(row["successful_uses"])
            items.append({
                **row,
                "success_rate": self._success(successes, uses),
            })
        return {
            "status": "available" if items else "empty",
            "items": items,
            "count": len(items),
            "reason": None if items else "no_registered_skills",
        }

    def _validation_runs(self, skill_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id FROM skill_validation_runs
            WHERE skill_id=? ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (skill_id, MAX_HISTORY),
        ).fetchall()
        output = []
        for row in rows:
            run = self.runtime.skill_validator.load(row["id"])
            output.append({
                "id": run.id,
                "status": run.status,
                "package_hash": run.package_hash,
                "created_at": run.created_at,
                "completed_at": run.completed_at,
                "promoted_at": run.promoted_at,
                "stages": [{
                    "order": item.order,
                    "stage": item.stage,
                    "outcome": item.evidence.outcome,
                    "score": item.evidence.score,
                    "token_cost": item.evidence.token_cost,
                    "estimated_cost": item.evidence.estimated_cost,
                    "latency_ms": item.evidence.latency_ms,
                    "details": item.evidence.details,
                } for item in run.results],
            })
        return output

    def _evolutions(self, skill_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM skill_evolution_runs
            WHERE source_skill_id=? OR candidate_skill_id=?
            ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (skill_id, skill_id, MAX_HISTORY),
        ).fetchall()
        return [{
            "id": row["id"],
            "source_skill_id": row["source_skill_id"],
            "candidate_skill_id": row["candidate_skill_id"],
            "source_version": row["source_version"],
            "candidate_version": row["candidate_version"],
            "status": row["status"],
            "mutation": json.loads(row["mutation_json"]),
            "comparison": (
                json.loads(row["comparison_json"])
                if row["comparison_json"] else None
            ),
            "winner": row["winner"],
            "created_at": row["created_at"],
            "compared_at": row["compared_at"],
            "promoted_at": row["promoted_at"],
            "rolled_back_at": row["rolled_back_at"],
        } for row in rows]

    def _benchmarks(self, reference: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id FROM skill_benchmark_runs
            WHERE existing_ref=? OR candidate_ref=?
            ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (reference, reference, MAX_BENCHMARKS),
        ).fetchall()
        output = []
        for row in rows:
            report = self.runtime.skill_benchmarks.report(row["id"])
            run = report["run"]
            output.append({
                "run": {
                    "id": run["id"],
                    "skill_name": run["skill_name"],
                    "existing_ref": run["existing_ref"],
                    "candidate_ref": run["candidate_ref"],
                    "status": run["status"],
                    "created_at": run["created_at"],
                    "completed_at": run["completed_at"],
                },
                "summary": report["summary"],
                "recommendations": report["recommendations"],
            })
        return output

    def _history(self, skill_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT event, from_status, to_status, details_json, created_at
            FROM skill_registry_history
            WHERE skill_id=?
            ORDER BY created_at DESC, rowid DESC LIMIT ?
            """,
            (skill_id, MAX_HISTORY),
        ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            output.append(item)
        return output

    def detail(self, reference: str) -> dict[str, Any]:
        skill = self.runtime.skill_registry.inspect(reference)
        manifest = dict(skill["manifest"])
        exact_ref = f"{skill['manifest_id']}@{skill['version']}"
        uses = int(skill["use_count"])
        successes = int(skill["success_count"])
        instructions = str(skill["instructions"])
        instructions_truncated = len(instructions) > MAX_INSTRUCTION_CHARS
        runtime_history = self._history(str(skill["id"]))
        return {
            "id": skill["id"],
            "reference": exact_ref,
            "manifest_id": skill["manifest_id"],
            "name": skill["name"],
            "version": skill["version"],
            "description": skill["description"],
            "instructions": instructions[:MAX_INSTRUCTION_CHARS],
            "instructions_truncated": instructions_truncated,
            "origin": manifest.get("origin"),
            "author": manifest.get("author"),
            "origin_is_self_declared": True,
            "lifecycle_status": skill["lifecycle_status"],
            "verification_status": skill["verification_status"],
            "token_cost": skill["token_cost"],
            "reliability": skill["reliability"],
            "uses": uses,
            "successful_uses": successes,
            "failures": int(skill["failure_count"]),
            "success_rate": self._success(successes, uses),
            "permissions": skill["permissions"],
            "runtime_authority_status": "separate_not_inferred",
            "tools": manifest.get("tools", []),
            "models": skill["models"],
            "dependencies": manifest.get("dependencies", []),
            "tests": {
                "declared": skill["verification"],
                "validation_runs": self._validation_runs(str(skill["id"])),
            },
            "performance": skill["performance"],
            "history": runtime_history,
            "evolutions": self._evolutions(str(skill["id"])),
            "benchmarks": self._benchmarks(exact_ref),
            "content_hash": skill["content_hash"],
            "revision": self._revision(skill),
            "generated_change_visibility": "explicit",
        }

    def compare(self, left_ref: str, right_ref: str) -> dict[str, Any]:
        left = self.detail(left_ref)
        right = self.detail(right_ref)
        if left["manifest_id"] != right["manifest_id"]:
            raise ValueError("Skill comparison requires one exact skill family")
        diff = list(difflib.unified_diff(
            str(left["instructions"]).splitlines(),
            str(right["instructions"]).splitlines(),
            fromfile=str(left["reference"]),
            tofile=str(right["reference"]),
            lineterm="",
        ))
        truncated = len(diff) > MAX_DIFF_LINES
        return {
            "left": left,
            "right": right,
            "instruction_diff": diff[:MAX_DIFF_LINES],
            "diff_truncated": truncated,
            "manifest_changes": {
                key: {"left": left_value, "right": right_value}
                for key, left_value, right_value in (
                    ("permissions", left["permissions"], right["permissions"]),
                    ("tools", left["tools"], right["tools"]),
                    ("models", left["models"], right["models"]),
                    ("dependencies", left["dependencies"], right["dependencies"]),
                    ("token_cost", left["token_cost"], right["token_cost"]),
                    ("tests", left["tests"]["declared"], right["tests"]["declared"]),
                )
                if left_value != right_value
            },
            "automatic_changes_hidden": False,
        }
