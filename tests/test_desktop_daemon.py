from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from acr_runtime import Settings
from acr_runtime.api import create_app
from acr_runtime.desktop_daemon import DaemonState, DesktopDaemon


class DesktopDaemonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.settings = Settings(
            database=root / "acr.db",
            state_dir=root,
            skills_dir=root / "skills",
            provider=None,
            ollama_url="http://127.0.0.1:11434",
        )
        self.daemon = DesktopDaemon(self.settings)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_stopped_status_and_stale_stop_are_idempotent(self) -> None:
        self.assertEqual(self.daemon.status()["status"], "stopped")
        self.assertTrue(self.daemon.stop()["already_stopped"])

        state = DaemonState(
            instance_id="12345678-1234-5678-1234-567812345678",
            pid=999_999_999,
            host="127.0.0.1",
            port=8123,
            started_at="2026-08-01T21:00:00Z",
            database=str(self.settings.database),
        )
        self.daemon._write(state)
        self.assertTrue(self.daemon.stop()["stale_state_removed"])
        self.assertFalse(self.daemon.state_path.exists())

    def test_state_shape_is_strict(self) -> None:
        payload = {
            "schema_version": 1,
            "instance_id": "12345678-1234-5678-1234-567812345678",
            "pid": 123,
            "host": "127.0.0.1",
            "port": 8123,
            "started_at": "2026-08-01T21:00:00Z",
            "database": "acr.db",
        }
        self.assertEqual(DaemonState.from_dict(payload).port, 8123)
        payload["schema_version"] = True
        with self.assertRaisesRegex(ValueError, "schema"):
            DaemonState.from_dict(payload)
        payload["schema_version"] = 1
        payload["extra"] = True
        with self.assertRaisesRegex(ValueError, "invalid shape"):
            DaemonState.from_dict(payload)

    def test_zero_cloud_never_accepts_network_binding(self) -> None:
        root = Path(self.temporary.name)
        settings = Settings(
            database=root / "zero.db",
            state_dir=root / "zero",
            skills_dir=root / "zero" / "skills",
            provider=None,
            ollama_url="http://127.0.0.1:11434",
            deployment_profile="zero-cloud",
        )
        with self.assertRaisesRegex(ValueError, "must remain loopback"):
            DesktopDaemon(settings).start(
                host="0.0.0.0",
                port=8123,
                allow_network=True,
            )

    def test_health_exposes_only_the_daemon_instance_identifier(self) -> None:
        identifier = "12345678-1234-5678-1234-567812345678"
        with TestClient(
            create_app(
                self.settings.database,
                daemon_instance_id=identifier,
            )
        ) as client:
            payload = client.get("/health").json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["daemon_instance_id"], identifier)
        self.assertNotIn("pid", json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "UUID"):
            create_app(self.settings.database, daemon_instance_id="not-a-uuid")


if __name__ == "__main__":
    unittest.main()
