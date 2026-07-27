from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
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
from acr_runtime.memory import MemoryCreate, MemoryStatus, MemoryType, Sensitivity


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


if __name__ == "__main__":
    unittest.main()
