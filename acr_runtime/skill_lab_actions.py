from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict
from typing import Any

from .memory import utc_now
from .permissions import CapabilityCheck
from .secret_management import assert_secret_free
from .service import AdaptiveRuntime
from .skill_lab import SkillLabReader
from .skill_benchmark import SkillBenchmarkRequest


IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class SkillLabConflict(RuntimeError):
    pass


class SkillLabActions:
    """Atomic, exact-authority Skill Lab lifecycle operations."""

    def __init__(self, runtime: AdaptiveRuntime, *, operator_id: str) -> None:
        if not operator_id.strip():
            raise ValueError("Skill Lab operator ID cannot be empty")
        self.runtime = runtime
        self.connection = runtime.db.connection
        self.operator_id = operator_id

    @staticmethod
    def _hash(payload: object) -> str:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _authorize(
        self,
        skill_id: str,
        *,
        capability: str = "skill.activate",
        prefix: str = "skill",
    ) -> None:
        decision = self.runtime.permissions.check(CapabilityCheck(
            subject_type="agent",
            subject_id=self.operator_id,
            capability=capability,
            resource_scope=f"{prefix}:{skill_id}",
        ))
        if not decision["allowed"]:
            raise PermissionError(
                f"Operator lacks active {capability} authority for this exact target"
            )

    def _skill(self, reference: str) -> dict[str, Any]:
        return self.runtime.skill_registry.inspect(reference)

    def _revision(self, skill: dict[str, Any]) -> str:
        return SkillLabReader._revision(skill)

    def _validate_key(self, value: str) -> None:
        if not IDEMPOTENCY_KEY.fullmatch(value):
            raise ValueError("Idempotency key must be 8-128 bounded characters")

    def _replay(
        self, idempotency_key: str, request_hash: str
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT request_hash, result_json FROM skill_lab_actions
            WHERE operator_id=? AND idempotency_key=?
            """,
            (self.operator_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise SkillLabConflict(
                "Idempotency key was already used for a different request"
            )
        return json.loads(row["result_json"])

    def _record(
        self,
        *,
        action: str,
        target_ref: str,
        idempotency_key: str,
        request_hash: str,
        reason: str | None,
        result: dict[str, Any],
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO skill_lab_actions(
                id, operator_id, idempotency_key, action, target_ref,
                request_hash, reason_hash, status, result_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?)
            """,
            (
                str(uuid.uuid4()),
                self.operator_id,
                idempotency_key,
                action,
                target_ref,
                request_hash,
                (
                    hashlib.sha256(reason.encode("utf-8")).hexdigest()
                    if reason is not None
                    else None
                ),
                json.dumps(result, sort_keys=True),
                utc_now(),
            ),
        )

    def _activation_proof(self, skill: dict[str, Any]) -> None:
        if skill["lifecycle_status"] == "retired":
            raise ValueError("Retired skills cannot be reactivated")
        if skill["verification_status"] != "static_passed":
            raise ValueError("Skill must pass registry testing before activation")
        package_path = skill.get("package_path")
        if not package_path:
            raise ValueError("Only validated v1 packages can be activated")
        package = self.runtime.skill_registry.loader.load(str(package_path))
        if package.content_hash != skill["content_hash"]:
            raise SkillLabConflict("Skill package changed after verification")
        validation = self.connection.execute(
            """
            SELECT r.id, COUNT(v.stage) AS stage_count,
                   SUM(CASE WHEN v.outcome='passed' THEN 1 ELSE 0 END)
                       AS passed_count
            FROM skill_validation_runs AS r
            JOIN skill_validation_results AS v ON v.run_id=r.id
            WHERE r.skill_id=? AND r.package_hash=?
              AND r.status IN ('passed','promoted')
            GROUP BY r.id
            HAVING stage_count=10 AND passed_count=10
            ORDER BY r.completed_at DESC, r.created_at DESC LIMIT 1
            """,
            (skill["id"], skill["content_hash"]),
        ).fetchone()
        if validation is None:
            raise ValueError(
                "Skill must pass all mandatory validation stages before activation"
            )

    def transition(
        self,
        reference: str,
        *,
        action: str,
        expected_revision: str,
        idempotency_key: str,
        reason: str,
        confirmation: str | None = None,
    ) -> dict[str, Any]:
        if action not in {"activate", "quarantine", "retire"}:
            raise ValueError("Unsupported Skill Lab lifecycle action")
        self._validate_key(idempotency_key)
        if not reason.strip() or len(reason) > 2_000:
            raise ValueError("Skill lifecycle action requires a bounded reason")
        assert_secret_free(reason, "skill lifecycle reason")
        initial = self._skill(reference)
        self._authorize(str(initial["id"]))
        exact_ref = f"{initial['manifest_id']}@{initial['version']}"
        if action == "retire" and confirmation != exact_ref:
            raise ValueError("Retirement confirmation must match the exact skill version")
        request_hash = self._hash({
            "action": action,
            "skill_id": initial["id"],
            "expected_revision": expected_revision,
            "reason": reason.strip(),
            "confirmation": confirmation,
        })

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            replay = self._replay(idempotency_key, request_hash)
            if replay is not None:
                self.connection.commit()
                return replay
            current = self._skill(str(initial["id"]))
            if self._revision(current) != expected_revision:
                raise SkillLabConflict("Skill changed; refresh before trying again")
            source = str(current["lifecycle_status"])
            if source == "retired":
                raise ValueError("Retired skills are terminal")
            if action == "activate":
                self._activation_proof(current)
                target, legacy = "active", "active"
            elif action == "quarantine":
                target, legacy = "quarantined", "quarantine"
            else:
                target, legacy = "retired", "deprecated"
            transition_update = self.connection.execute(
                """
                UPDATE skills SET lifecycle_status=?, status=?
                WHERE id=? AND lifecycle_status=?
                """,
                (target, legacy, current["id"], source),
            )
            if transition_update.rowcount != 1:
                raise SkillLabConflict("Skill lifecycle changed concurrently")
            action_id = str(uuid.uuid4())
            reason_hash = hashlib.sha256(
                reason.strip().encode("utf-8")
            ).hexdigest()
            self.connection.execute(
                """
                INSERT INTO skill_registry_history(
                    id, skill_id, event, from_status, to_status,
                    details_json, created_at
                ) VALUES (?, ?, 'status_changed', ?, ?, ?, ?)
                """,
                (
                    action_id,
                    current["id"],
                    source,
                    target,
                    json.dumps({
                        "source": "skill_lab",
                        "reason_hash": reason_hash,
                    }),
                    utc_now(),
                ),
            )
            result = {
                "action": action,
                "skill_id": current["id"],
                "reference": exact_ref,
                "from_status": source,
                "to_status": target,
                "action_id": action_id,
            }
            self._record(
                action=action,
                target_ref=exact_ref,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                reason=reason.strip(),
                result=result,
            )
            self.connection.commit()
            return result
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    def rollback(
        self,
        run_id: str,
        *,
        expected_source_revision: str,
        expected_candidate_revision: str,
        idempotency_key: str,
        reason: str,
    ) -> dict[str, Any]:
        self._validate_key(idempotency_key)
        if not reason.strip() or len(reason) > 2_000:
            raise ValueError("Rollback requires a bounded reason")
        assert_secret_free(reason, "skill rollback reason")
        run = self.connection.execute(
            "SELECT * FROM skill_evolution_runs WHERE id=?", (run_id,)
        ).fetchone()
        if run is None:
            raise LookupError(f"Unknown skill evolution run: {run_id}")
        self._authorize(str(run["source_skill_id"]))
        self._authorize(str(run["candidate_skill_id"]))
        request_hash = self._hash({
            "action": "rollback",
            "run_id": run_id,
            "source_revision": expected_source_revision,
            "candidate_revision": expected_candidate_revision,
            "reason": reason.strip(),
        })

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            replay = self._replay(idempotency_key, request_hash)
            if replay is not None:
                self.connection.commit()
                return replay
            current_run = self.connection.execute(
                "SELECT * FROM skill_evolution_runs WHERE id=?", (run_id,)
            ).fetchone()
            if current_run["status"] != "promoted":
                raise SkillLabConflict(
                    "Only the currently promoted evolution can be rolled back"
                )
            source = self._skill(str(current_run["source_skill_id"]))
            candidate = self._skill(str(current_run["candidate_skill_id"]))
            if (
                self._revision(source) != expected_source_revision
                or self._revision(candidate) != expected_candidate_revision
            ):
                raise SkillLabConflict(
                    "A rollback skill changed; refresh before trying again"
                )
            self._activation_proof(source)
            now = utc_now()
            candidate_update = self.connection.execute(
                """
                UPDATE skills SET lifecycle_status='quarantined', status='quarantine'
                WHERE id=? AND lifecycle_status='active'
                """,
                (candidate["id"],),
            )
            if candidate_update.rowcount != 1:
                raise SkillLabConflict("Candidate is no longer active")
            self.connection.execute(
                """
                UPDATE skills SET lifecycle_status='active', status='active'
                WHERE id=? AND lifecycle_status!='retired'
                """,
                (source["id"],),
            )
            self.connection.execute(
                """
                UPDATE skill_evolution_runs
                SET status='rolled_back', rolled_back_at=?
                WHERE id=? AND status='promoted'
                """,
                (now, run_id),
            )
            rollback_id = str(uuid.uuid4())
            self.connection.execute(
                """
                INSERT INTO skill_evolution_rollbacks(
                    id, run_id, from_skill_id, to_skill_id, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    rollback_id,
                    run_id,
                    candidate["id"],
                    source["id"],
                    reason.strip(),
                    now,
                ),
            )
            reason_hash = hashlib.sha256(
                reason.strip().encode("utf-8")
            ).hexdigest()
            for skill, from_status, to_status in (
                (candidate, "active", "quarantined"),
                (source, source["lifecycle_status"], "active"),
            ):
                self.connection.execute(
                    """
                    INSERT INTO skill_registry_history(
                        id, skill_id, event, from_status, to_status,
                        details_json, created_at
                    ) VALUES (?, ?, 'status_changed', ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        skill["id"],
                        from_status,
                        to_status,
                        json.dumps({
                            "source": "skill_lab_rollback",
                            "run_id": run_id,
                            "reason_hash": reason_hash,
                        }),
                        now,
                    ),
                )
            result = {
                "action": "rollback",
                "run_id": run_id,
                "rollback_id": rollback_id,
                "from_skill_id": candidate["id"],
                "to_skill_id": source["id"],
            }
            self._record(
                action="rollback",
                target_ref=run_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                reason=reason.strip(),
                result=result,
            )
            self.connection.commit()
            return result
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    def benchmark(
        self,
        request: SkillBenchmarkRequest,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._validate_key(idempotency_key)
        existing = self._skill(request.existing_ref)
        candidate = self._skill(request.candidate_ref)
        if existing["manifest_id"] != candidate["manifest_id"]:
            raise ValueError("Benchmark versions must belong to one skill family")
        existing_ref = f"{existing['manifest_id']}@{existing['version']}"
        candidate_ref = f"{candidate['manifest_id']}@{candidate['version']}"
        if (
            request.existing_ref != existing_ref
            or request.candidate_ref != candidate_ref
        ):
            raise ValueError("Benchmark requires two exact skill version references")
        family = str(existing["manifest_id"])
        self._authorize(
            family,
            capability="database.write",
            prefix="skill-benchmark",
        )
        request_hash = self._hash({
            "action": "benchmark",
            "request": {
                "skill_name": request.skill_name,
                "existing_ref": request.existing_ref,
                "candidate_ref": request.candidate_ref,
                "trials": [asdict(item) for item in request.trials],
            },
        })
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            replay = self._replay(idempotency_key, request_hash)
            if replay is not None:
                self.connection.commit()
                return replay
            report = self.runtime.skill_benchmarks.analyze(
                request, manage_transaction=False
            )
            run = report["run"]
            result = {
                "action": "benchmark",
                "run_id": run["id"],
                "skill_name": run["skill_name"],
                "existing_ref": run["existing_ref"],
                "candidate_ref": run["candidate_ref"],
                "status": run["status"],
                "recommendations": report["recommendations"],
                "lifecycle_changed": False,
            }
            self._record(
                action="benchmark",
                target_ref=family,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                reason=None,
                result=result,
            )
            self.connection.commit()
            return result
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise
