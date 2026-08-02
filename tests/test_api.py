from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
    from acr_runtime.api import create_app
except ModuleNotFoundError:
    TestClient = None
    create_app = None

from acr_runtime.cli import main
from acr_runtime.db import RuntimeDB
from acr_runtime.memory import (
    MemoryCreate,
    MemoryStatus,
    MemoryType,
    Sensitivity,
    SourceClass,
)
from acr_runtime.permissions import CapabilityGrantRequest, PermissionController


@unittest.skipUnless(
    TestClient is not None,
    "install the api extra to run FastAPI tests",
)
class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "acr.db"
        with RuntimeDB(self.path) as database:
            database.memories.create(MemoryCreate(
                type=MemoryType.SEMANTIC,
                content="Alpha uses SQLite FTS5.",
                scope="alpha",
                subject="database",
                source_class=SourceClass.REPOSITORY,
                status=MemoryStatus.CONFIRMED,
                sensitivity=Sensitivity.INTERNAL,
            ))
            database.memories.create(MemoryCreate(
                type=MemoryType.SEMANTIC,
                content="Alpha private credential material.",
                scope="alpha",
                subject="private",
                status=MemoryStatus.CONFIRMED,
                sensitivity=Sensitivity.SECRET,
            ))
            database.memories.create(MemoryCreate(
                type=MemoryType.SEMANTIC,
                content="Beta uses PostgreSQL.",
                scope="beta",
                subject="database",
                status=MemoryStatus.CONFIRMED,
            ))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_openapi_contains_required_schema_driven_endpoints(self):
        with TestClient(create_app(self.path)) as client:
            schema = client.get("/openapi.json").json()
        self.assertTrue({
            "/tasks", "/tasks/{task_id}", "/memory", "/memory/search",
            "/skills", "/skills/search", "/agents", "/models",
            "/telemetry", "/health",
            "/dashboard/v1/overview", "/dashboard/v1/tasks",
            "/dashboard/v1/{section}",
            "/dashboard/v1/series/{metric}",
            "/memory-inspector/v1/search",
            "/memory-inspector/v1/timeline",
            "/memory-inspector/v1/related",
            "/memory-inspector/v1/{memory_id}",
            "/memory-inspector/v1/{memory_id}/lifecycle",
            "/memory-inspector/v1/{memory_id}/correct",
            "/memory-inspector/v1/{memory_id}/deletion-plan",
            "/memory-inspector/v1/deletion-requests/{request_id}/approve",
            "/skill-lab/v1/skills",
            "/skill-lab/v1/skills/{reference}",
            "/skill-lab/v1/compare",
            "/skill-lab/v1/skills/{reference}/lifecycle",
            "/skill-lab/v1/evolutions/{run_id}/rollback",
            "/skill-lab/v1/benchmark",
            "/learning-dashboard/v1/events",
        } <= set(schema["paths"]))
        self.assertIn(
            "TaskCreateRequest", schema["components"]["schemas"]
        )
        self.assertIn(
            "MemorySearchRequest", schema["components"]["schemas"]
        )

    def test_task_create_get_validation_and_not_found(self):
        with TestClient(create_app(self.path)) as client:
            created = client.post("/tasks", json={
                "objective": "Inspect SQLite",
                "scope": "alpha",
                "token_budget": 500,
            })
            self.assertEqual(created.status_code, 201)
            task = created.json()
            self.assertEqual(client.get(f"/tasks/{task['id']}").json(), task)
            self.assertEqual(
                client.post("/tasks", json={
                    "objective": "x", "unexpected": True,
                }).status_code,
                422,
            )
            self.assertEqual(
                client.post("/tasks", json={"objective": "   "}).status_code,
                422,
            )
            self.assertEqual(client.get("/tasks/missing").status_code, 404)

    def test_memory_is_scope_bound_and_sensitive_classes_are_not_exposed(self):
        with TestClient(create_app(self.path)) as client:
            listed = client.get("/memory", params={"scope": "alpha"}).json()
            self.assertEqual(listed["count"], 1)
            self.assertEqual(listed["items"][0]["content"], "Alpha uses SQLite FTS5.")
            self.assertEqual(
                listed["items"][0]["source_class"],
                SourceClass.REPOSITORY.value,
            )
            searched = client.post("/memory/search", json={
                "query": "database SQLite",
                "scope": "alpha",
                "limit": 20,
            }).json()
            encoded = str(searched)
            self.assertIn("SQLite", encoded)
            self.assertNotIn("credential", encoded)
            self.assertNotIn("PostgreSQL", encoded)

    def test_private_rows_are_filtered_before_limit(self):
        with RuntimeDB(self.path) as database:
            for index in range(25):
                database.memories.create(MemoryCreate(
                    type=MemoryType.SEMANTIC,
                    content=f"Hidden matching record {index}",
                    scope="alpha",
                    subject="hidden",
                    status=MemoryStatus.CONFIRMED,
                    sensitivity=Sensitivity.SECRET,
                ))
            database.memories.create(MemoryCreate(
                type=MemoryType.SEMANTIC,
                content="Visible matching record",
                scope="alpha",
                subject="visible",
                status=MemoryStatus.CONFIRMED,
                sensitivity=Sensitivity.PUBLIC,
            ))
        with TestClient(create_app(self.path)) as client:
            response = client.get(
                "/memory", params={"scope": "alpha", "limit": 2}
            )
        self.assertEqual(response.status_code, 200)
        encoded = str(response.json())
        self.assertIn("Visible matching record", encoded)
        self.assertNotIn("Hidden matching record", encoded)

    def test_auth_is_constant_contract_and_public_aggregates_work(self):
        app = create_app(self.path, api_token="local-test-token")
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").status_code, 401)
            headers = {"X-ACR-Token": "local-test-token"}
            health = client.get("/health", headers=headers)
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["status"], "ok")
            for endpoint in (
                "/skills", "/agents", "/models", "/telemetry"
            ):
                self.assertEqual(
                    client.get(endpoint, headers=headers).status_code, 200
                )
            for endpoint in (
                "/dashboard/v1/overview",
                "/dashboard/v1/tasks",
                "/dashboard/v1/memory",
                "/dashboard/v1/series/tokens_per_day",
            ):
                self.assertEqual(
                    client.get(endpoint, headers=headers).status_code, 200
                )
            self.assertEqual(
                client.get(
                    "/dashboard/v1/series/invented",
                    headers=headers,
                ).status_code,
                404,
            )

    def test_request_scoped_connections_support_parallel_health_checks(self):
        with TestClient(create_app(self.path)) as client:
            def check(_: int) -> int:
                return client.get("/health").status_code

            with ThreadPoolExecutor(max_workers=4) as executor:
                statuses = list(executor.map(check, range(12)))
        self.assertEqual(statuses, [200] * 12)

    def test_database_busy_timeout_and_nonloopback_guard(self):
        with RuntimeDB(self.path) as database:
            self.assertEqual(
                database.connection.execute(
                    "PRAGMA busy_timeout"
                ).fetchone()[0],
                5_000,
            )
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(
                ValueError, "requires ACR_API_TOKEN"
            ):
                main([
                    "--db", str(self.path), "serve",
                    "--host", "0.0.0.0",
                ])

    def _grant_memory_write(self, scope: str = "alpha") -> None:
        with RuntimeDB(self.path) as database:
            PermissionController(database.connection).grant(
                CapabilityGrantRequest(
                    subject_type="agent",
                    subject_id="operator-ui",
                    capability="memory.write",
                    resource_scope=f"memory:{scope}",
                    expires_at=(
                        datetime.now(timezone.utc) + timedelta(hours=1)
                    ).isoformat(),
                    delegable=False,
                    grantor_type="trusted_workflow",
                    grantor_id="trusted-tests",
                    reason="Test the exact-scope inspector action boundary",
                    evidence=("test:memory-inspector",),
                )
            )

    def test_memory_inspector_reads_are_bounded_and_actions_default_deny(self):
        with TestClient(create_app(self.path)) as client:
            searched = client.get(
                "/memory-inspector/v1/search",
                params={"scope": "alpha", "text": "SQLite"},
            )
            self.assertEqual(searched.status_code, 200)
            self.assertEqual(searched.json()["count"], 1)
            memory_id = searched.json()["items"][0]["id"]
            detail = client.get(
                f"/memory-inspector/v1/{memory_id}",
                params={"scope": "alpha"},
            )
            self.assertEqual(detail.status_code, 200)
            self.assertNotIn("credential", str(detail.json()))
            self.assertEqual(
                client.get(
                    f"/memory-inspector/v1/{memory_id}",
                    params={"scope": "beta"},
                ).status_code,
                404,
            )
            denied = client.post(
                f"/memory-inspector/v1/{memory_id}/lifecycle",
                json={
                    "scope": "alpha",
                    "expected_updated_at": detail.json()["updated_at"],
                    "action": "pin",
                },
            )
            self.assertEqual(denied.status_code, 503)

    def test_memory_inspector_mutations_need_token_operator_and_exact_grant(self):
        headers = {"X-ACR-Token": "inspector-token"}
        app = create_app(
            self.path,
            api_token="inspector-token",
            operator_id="operator-ui",
        )
        with TestClient(app) as client:
            search = client.get(
                "/memory-inspector/v1/search",
                params={"scope": "alpha"},
                headers=headers,
            ).json()
            memory = search["items"][0]
            payload = {
                "scope": "alpha",
                "expected_updated_at": memory["updated_at"],
                "action": "pin",
                "reason": "Keep this operational fact",
            }
            self.assertEqual(
                client.post(
                    f"/memory-inspector/v1/{memory['id']}/lifecycle",
                    json=payload,
                    headers=headers,
                ).status_code,
                403,
            )
        self._grant_memory_write()
        with TestClient(app) as client:
            refreshed = client.get(
                f"/memory-inspector/v1/{memory['id']}",
                params={"scope": "alpha"},
                headers=headers,
            ).json()
            payload["expected_updated_at"] = refreshed["updated_at"]
            pinned = client.post(
                f"/memory-inspector/v1/{memory['id']}/lifecycle",
                json=payload,
                headers=headers,
            )
            self.assertEqual(pinned.status_code, 200)
            self.assertTrue(pinned.json()["pinned"])
            stale = client.post(
                f"/memory-inspector/v1/{memory['id']}/lifecycle",
                json=payload,
                headers=headers,
            )
            self.assertEqual(stale.status_code, 409)

    def test_memory_inspector_correction_and_two_step_delete_preserve_history(self):
        self._grant_memory_write()
        headers = {"X-ACR-Token": "inspector-token"}
        app = create_app(
            self.path,
            api_token="inspector-token",
            operator_id="operator-ui",
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            original = client.get(
                "/memory-inspector/v1/search",
                params={"scope": "alpha", "text": "SQLite"},
                headers=headers,
            ).json()["items"][0]
            corrected = client.post(
                f"/memory-inspector/v1/{original['id']}/correct",
                headers=headers,
                json={
                    "scope": "alpha",
                    "expected_updated_at": original["updated_at"],
                    "content": "Alpha uses SQLite FTS5 with WAL mode.",
                    "evidence": ["operator:verified-database-config"],
                    "reason": "Add the verified journal mode",
                },
            )
            self.assertEqual(corrected.status_code, 201)
            replacement_id = corrected.json()["memory_id"]
            timeline = client.get(
                "/memory-inspector/v1/timeline",
                params={"scope": "alpha", "subject": "database"},
                headers=headers,
            ).json()
            self.assertEqual(timeline["count"], 2)
            replacement = client.get(
                f"/memory-inspector/v1/{replacement_id}",
                params={"scope": "alpha"},
                headers=headers,
            ).json()
            planned = client.post(
                f"/memory-inspector/v1/{replacement_id}/deletion-plan",
                headers=headers,
                json={
                    "scope": "alpha",
                    "expected_updated_at": replacement["updated_at"],
                    "reason": "Remove the test correction",
                },
            )
            self.assertEqual(planned.status_code, 201)
            request_id = planned.json()["id"]
            wrong = client.post(
                f"/memory-inspector/v1/deletion-requests/{request_id}/approve",
                headers=headers,
                json={"scope": "alpha", "confirmation": "wrong"},
            )
            self.assertEqual(wrong.status_code, 422)
            approved = client.post(
                f"/memory-inspector/v1/deletion-requests/{request_id}/approve",
                headers=headers,
                json={"scope": "alpha", "confirmation": replacement_id},
            )
            self.assertEqual(approved.status_code, 200)
            self.assertEqual(
                client.get(
                    f"/memory-inspector/v1/{replacement_id}",
                    params={"scope": "alpha"},
                    headers=headers,
                ).status_code,
                404,
            )

    def test_skill_lab_lists_details_and_compares_without_host_paths(self):
        with RuntimeDB(self.path) as database:
            first = database.add_skill(
                name="skill-lab-demo",
                version="1.0.0",
                description="First version",
                instructions="Inspect the schema.",
                tags=("database",),
                status="quarantine",
            )
            second = database.add_skill(
                name="skill-lab-demo",
                version="2.0.0",
                description="Second version",
                instructions="Inspect the schema and FTS index.",
                tags=("database",),
                status="quarantine",
            )
        with TestClient(create_app(self.path)) as client:
            listed = client.get("/skill-lab/v1/skills")
            self.assertEqual(listed.status_code, 200)
            ids = {item["id"] for item in listed.json()["items"]}
            self.assertTrue({first, second} <= ids)
            detail = client.get(f"/skill-lab/v1/skills/{first}")
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json()["instructions"], "Inspect the schema.")
            self.assertNotIn("package_path", str(detail.json()))
            compared = client.post("/skill-lab/v1/compare", json={
                "left_ref": first,
                "right_ref": second,
            })
            self.assertEqual(compared.status_code, 200)
            self.assertIn("+Inspect the schema and FTS index.", str(
                compared.json()["instruction_diff"]
            ))
            self.assertFalse(compared.json()["automatic_changes_hidden"])

    def test_learning_dashboard_is_content_minimized_uncached_and_bounded(self):
        with TestClient(create_app(self.path)) as client:
            response = client.get("/learning-dashboard/v1/events")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.json()["status"], "empty")
            self.assertIn("No self-initiated", response.json()["truth_notice"])
            self.assertEqual(
                client.get(
                    "/learning-dashboard/v1/events",
                    params={"category": "not-real"},
                ).status_code,
                422,
            )
            self.assertEqual(
                client.get(
                    "/learning-dashboard/v1/events",
                    params={"limit": 101},
                ).status_code,
                422,
            )

    def test_skill_lab_lifecycle_requires_token_operator_exact_grant_and_key(self):
        with RuntimeDB(self.path) as database:
            skill_id = database.add_skill(
                name="governed-skill",
                version="1.0.0",
                description="Governed lifecycle fixture",
                instructions="Inspect exact evidence.",
                tags=("test",),
                status="quarantine",
            )
        headers = {
            "X-ACR-Token": "skill-token",
            "Idempotency-Key": "retire-api-0001",
        }
        app = create_app(
            self.path,
            api_token="skill-token",
            operator_id="operator-ui",
        )
        with TestClient(app) as client:
            detail = client.get(
                f"/skill-lab/v1/skills/{skill_id}",
                headers=headers,
            ).json()
            body = {
                "action": "retire",
                "expected_revision": detail["revision"],
                "reason": "Retire the API fixture",
                "confirmation": detail["reference"],
            }
            denied = client.post(
                f"/skill-lab/v1/skills/{skill_id}/lifecycle",
                headers=headers,
                json=body,
            )
            self.assertEqual(denied.status_code, 403)
        with RuntimeDB(self.path) as database:
            PermissionController(database.connection).grant(
                CapabilityGrantRequest(
                    subject_type="agent",
                    subject_id="operator-ui",
                    capability="skill.activate",
                    resource_scope=f"skill:{skill_id}",
                    expires_at=(
                        datetime.now(timezone.utc) + timedelta(hours=1)
                    ).isoformat(),
                    delegable=False,
                    grantor_type="trusted_workflow",
                    grantor_id="trusted-tests",
                    reason="Test exact Skill Lab authority",
                    evidence=("test:skill-lab-api",),
                )
            )
        with TestClient(app) as client:
            retired = client.post(
                f"/skill-lab/v1/skills/{skill_id}/lifecycle",
                headers=headers,
                json=body,
            )
            self.assertEqual(retired.status_code, 200)
            self.assertEqual(retired.json()["to_status"], "retired")
            replay = client.post(
                f"/skill-lab/v1/skills/{skill_id}/lifecycle",
                headers=headers,
                json=body,
            )
            self.assertEqual(replay.json(), retired.json())


if __name__ == "__main__":
    unittest.main()
