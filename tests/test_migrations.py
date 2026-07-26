from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from acr_runtime.db import RuntimeDB
from acr_runtime.migrations import MigrationManager, MigrationRequired


class MigrationTests(unittest.TestCase):
    def test_existing_database_requires_explicit_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acr.db"
            with RuntimeDB(path):
                pass

            connection = sqlite3.connect(path)
            try:
                connection.execute("DELETE FROM schema_migrations WHERE version = 2")
                connection.execute("DROP TABLE telemetry_events")
                connection.execute("DROP TABLE execution_runs")
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(MigrationRequired):
                RuntimeDB(path)

            status = MigrationManager(path).apply_pending()
            self.assertEqual(status.current_version, 2)
            self.assertEqual(status.pending_versions, ())

            with RuntimeDB(path) as upgraded:
                self.assertTrue(upgraded.health()["schema_current"])


if __name__ == "__main__":
    unittest.main()

