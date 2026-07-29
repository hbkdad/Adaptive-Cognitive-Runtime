from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from acr_runtime import AdaptiveRuntime, BackupManager, Settings
from acr_runtime.cli import main
from acr_runtime.secret_management import SecretBoundaryError


class BackupRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.skills = self.state / "skills"
        self.benchmarks = self.root / "benchmarks"
        self.database = self.state / "acr.db"
        self.settings = Settings(
            database=self.database,
            state_dir=self.state,
            skills_dir=self.skills,
            provider="ollama",
            ollama_url="http://127.0.0.1:11434",
            ollama_model="test-model",
        )
        with AdaptiveRuntime(settings=self.settings) as runtime:
            self.memory_id = runtime.remember(
                "semantic",
                "Backups use a coherent SQLite snapshot.",
                scope="backup-test",
            )
        skill = self.skills / "generated" / "backup-check"
        skill.mkdir(parents=True)
        (skill / "SKILL.yaml").write_text(
            json.dumps(
                {
                    "id": "backup-check",
                    "name": "Backup check",
                    "version": "0.1.0",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.benchmarks.mkdir()
        (self.benchmarks / "case.jsonl").write_text(
            '{"id":"case-1","expected":"verified"}\n',
            encoding="utf-8",
        )
        self.manager = BackupManager(
            self.settings, benchmarks_dir=self.benchmarks
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_create_verify_and_restore_round_trip(self):
        backup = self.root / "checkpoint.acrb"
        created = self.manager.create(backup)

        self.assertTrue(created["verified"])
        self.assertTrue(created["restorable"])
        self.assertEqual(created["compatibility"], "compatible_current")
        self.assertEqual(created["secret_values_included"], False)
        self.assertEqual(
            set(created["learning_history"]),
            {
                "learning_runs",
                "learning_stage_results",
                "learning_memory_candidates",
                "learning_routing_improvements",
                "learning_regressions",
            },
        )
        verified = self.manager.verify(backup)
        self.assertEqual(
            verified["archive_sha256"], created["archive_sha256"]
        )

        target = self.root / "restored"
        restored = self.manager.restore(backup, target)
        self.assertTrue(restored["restored"])
        self.assertTrue(restored["activation_required"])
        self.assertTrue((target / "skills/generated/backup-check/SKILL.yaml").is_file())
        self.assertTrue((target / "benchmarks/case.jsonl").is_file())
        configuration = json.loads(
            (target / "configuration.json").read_text(encoding="utf-8")
        )
        self.assertEqual(configuration["provider"], "ollama")
        self.assertIn("dotenv_files", configuration["excluded"])
        with AdaptiveRuntime(target / "acr.db") as runtime:
            self.assertIsNotNone(runtime.db.memories.get(self.memory_id))

    def test_secret_material_aborts_backup_without_output(self):
        (self.skills / "generated/backup-check/instructions.md").write_text(
            "api_key = abcdefghijklmnopqrstuvwxyz123456\n",
            encoding="utf-8",
        )
        backup = self.root / "secret.acrb"

        with self.assertRaises(SecretBoundaryError):
            self.manager.create(backup)
        self.assertFalse(backup.exists())

        (self.skills / "generated/backup-check/instructions.md").unlink()
        (self.skills / "generated/backup-check/.env.example").write_text(
            "PLACEHOLDER=not-a-secret\n",
            encoding="utf-8",
        )
        with self.assertRaises(SecretBoundaryError):
            self.manager.create(backup)
        self.assertFalse(backup.exists())
        (self.skills / "generated/backup-check/.env.example").unlink()

        connection = sqlite3.connect(self.database)
        try:
            connection.execute("CREATE TABLE unsafe_backup_test(value TEXT)")
            connection.execute(
                "INSERT INTO unsafe_backup_test(value) VALUES (?)",
                ("sk-" + "proj-" + "abcdefghijklmnopqrstuvwxyz123456",),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(SecretBoundaryError):
            self.manager.create(backup)
        self.assertFalse(backup.exists())

    def test_hash_tampering_and_traversal_are_rejected(self):
        backup = self.root / "checkpoint.acrb"
        self.manager.create(backup)
        with zipfile.ZipFile(backup, "r") as original:
            members = {
                info.filename: original.read(info.filename)
                for info in original.infolist()
            }
        members["benchmarks/case.jsonl"] += b'{"tampered":true}\n'
        tampered = self.root / "tampered.acrb"
        with zipfile.ZipFile(tampered, "w") as archive:
            for name, content in members.items():
                archive.writestr(name, content)
        with self.assertRaisesRegex(ValueError, "size mismatch|hash mismatch"):
            self.manager.verify(tampered)

        traversal = self.root / "traversal.acrb"
        with zipfile.ZipFile(traversal, "w") as archive:
            archive.writestr("../outside", b"unsafe")
        with self.assertRaisesRegex(ValueError, "unsafe member"):
            self.manager.verify(traversal)

    def test_newer_schema_is_verified_but_not_restorable(self):
        backup = self.root / "checkpoint.acrb"
        self.manager.create(backup)
        with zipfile.ZipFile(backup, "r") as archive:
            members = {
                info.filename: archive.read(info.filename)
                for info in archive.infolist()
            }
        database = self.root / "newer.db"
        database.write_bytes(members["database/acr.db"])
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) "
                "VALUES (999, '2099-01-01T00:00:00Z')"
            )
            connection.commit()
        finally:
            connection.close()
        database_bytes = database.read_bytes()
        members["database/acr.db"] = database_bytes
        manifest = json.loads(members["manifest.json"])
        manifest["schema_version"] = 999
        for entry in manifest["entries"]:
            if entry["path"] == "database/acr.db":
                entry["size"] = len(database_bytes)
                entry["sha256"] = hashlib.sha256(database_bytes).hexdigest()
        members["manifest.json"] = (
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        newer = self.root / "newer.acrb"
        with zipfile.ZipFile(newer, "w") as archive:
            for name, content in members.items():
                archive.writestr(name, content)

        result = self.manager.verify(newer)
        self.assertTrue(result["verified"])
        self.assertFalse(result["restorable"])
        self.assertEqual(
            result["compatibility"], "incompatible_newer_schema"
        )
        with self.assertRaisesRegex(ValueError, "not restorable"):
            self.manager.restore(newer, self.root / "newer-restore")

    def test_cli_commands_and_existing_target_guard(self):
        backup = self.root / "cli.acrb"
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                main(
                    [
                        "--db",
                        str(self.database),
                        "backup",
                        str(backup),
                        "--benchmarks-dir",
                        str(self.benchmarks),
                    ]
                ),
                0,
            )
        self.assertTrue(json.loads(output.getvalue())["created"])

        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                main(["verify-backup", str(backup)]),
                0,
            )
        self.assertTrue(json.loads(output.getvalue())["verified"])

        target = self.root / "cli-restore"
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                main(["restore", str(backup), str(target)]),
                0,
            )
        self.assertTrue(json.loads(output.getvalue())["restored"])
        with self.assertRaises(FileExistsError):
            self.manager.restore(backup, target)


if __name__ == "__main__":
    unittest.main()
