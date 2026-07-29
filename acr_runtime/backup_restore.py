from __future__ import annotations

import codecs
import hashlib
import importlib.metadata
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from .config import Settings
from .migrations import EXPECTED_SCHEMA_VERSION
from .secret_management import SecretBoundaryError, detect_secret_material


BACKUP_FORMAT = "acr-backup-v1"
MAX_ENTRIES = 50_000
MAX_ENTRY_BYTES = 2 * 1024 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
MAX_CONTROL_BYTES = 4 * 1024 * 1024
COPY_CHUNK = 1024 * 1024
SECRET_SCAN_OVERLAP = 2_048
LEARNING_TABLES = (
    "learning_runs",
    "learning_stage_results",
    "learning_memory_candidates",
    "learning_routing_improvements",
    "learning_regressions",
)
SECRET_FILE_NAMES = frozenset(
    {
        ".env",
        "credentials",
        "credentials.json",
        "id_rsa",
        "id_ed25519",
        "secrets.json",
    }
)
SECRET_FILE_SUFFIXES = (
    ".env",
    ".key",
    ".p12",
    ".pem",
    ".pfx",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_version() -> str:
    try:
        return importlib.metadata.version("acr-runtime")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(COPY_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member(name: str) -> PurePosixPath:
    if (
        not name
        or "\\" in name
        or name.startswith("/")
        or name.endswith("/")
    ):
        raise ValueError("backup contains an invalid member path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("backup contains an unsafe member path")
    return path


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _learning_counts(connection: sqlite3.Connection) -> dict[str, int]:
    existing = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    return {
        table: (
            int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
                ).fetchone()[0]
            )
            if table in existing
            else 0
        )
        for table in LEARNING_TABLES
    }


def _database_evidence(path: Path) -> dict[str, object]:
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        table = connection.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type='table' AND name='schema_migrations'
            """
        ).fetchone()[0]
        if not table:
            raise ValueError("backup database has no migration history")
        schema_version = int(
            connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()[0]
        )
        return {
            "quick_check": quick_check,
            "schema_version": schema_version,
            "learning_history": _learning_counts(connection),
        }
    finally:
        connection.close()


def _scan_database_secrets(path: Path) -> None:
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        tables = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]
        for table in tables:
            columns = [
                str(row[1])
                for row in connection.execute(
                    f"PRAGMA table_info({_quote_identifier(table)})"
                )
                if any(
                    marker in str(row[2]).upper()
                    for marker in ("CHAR", "CLOB", "TEXT")
                )
            ]
            if not columns:
                continue
            projection = ", ".join(_quote_identifier(item) for item in columns)
            cursor = connection.execute(
                f"SELECT {projection} FROM {_quote_identifier(table)}"
            )
            while rows := cursor.fetchmany(500):
                for row in rows:
                    for column, value in zip(columns, row):
                        if isinstance(value, str) and detect_secret_material(
                            value, include_labeled=False
                        ):
                            raise SecretBoundaryError(
                                "backup database rejects secret material in "
                                f"{table}.{column}"
                            )
    finally:
        connection.close()


def _stream_secret_findings(handle: BinaryIO) -> tuple[str, ...]:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
    tail = ""
    findings: set[str] = set()
    while chunk := handle.read(COPY_CHUNK):
        text = tail + decoder.decode(chunk)
        findings.update(detect_secret_material(text))
        tail = text[-SECRET_SCAN_OVERLAP:]
    text = tail + decoder.decode(b"", final=True)
    findings.update(detect_secret_material(text))
    return tuple(sorted(findings))


def _scan_file_secrets(path: Path, *, boundary: str) -> None:
    with path.open("rb") as handle:
        findings = _stream_secret_findings(handle)
    if findings:
        raise SecretBoundaryError(
            f"{boundary} rejects secret material: {','.join(findings)}"
        )


@dataclass(frozen=True)
class _Source:
    archive_path: str
    source_path: Path
    component: str


class BackupManager:
    """Fixed-scope, hash-verified ACR backup and restore."""

    def __init__(
        self,
        settings: Settings,
        *,
        benchmarks_dir: str | Path = "benchmarks",
    ) -> None:
        self.settings = settings
        self.benchmarks_dir = Path(benchmarks_dir)

    @staticmethod
    def _tree_sources(
        root: Path,
        *,
        prefix: str,
        component: str,
    ) -> list[_Source]:
        if not root.exists():
            return []
        if not root.is_dir() or root.is_symlink():
            raise ValueError(f"{component} root must be a real directory")
        resolved_root = root.resolve()
        sources = []
        for candidate in sorted(root.rglob("*")):
            if candidate.is_symlink():
                raise ValueError(f"{component} cannot contain symbolic links")
            if not candidate.is_file():
                continue
            lowered = candidate.name.casefold()
            if (
                lowered in SECRET_FILE_NAMES
                or lowered.startswith(".env.")
                or lowered.startswith("secrets.")
                or lowered.endswith(SECRET_FILE_SUFFIXES)
            ):
                raise SecretBoundaryError(
                    f"{component} contains an excluded secret-file type"
                )
            resolved = candidate.resolve()
            if not resolved.is_relative_to(resolved_root):
                raise ValueError(f"{component} file escapes its source root")
            relative = candidate.relative_to(root).as_posix()
            archive_path = f"{prefix}/{relative}"
            _safe_member(archive_path)
            sources.append(_Source(archive_path, candidate, component))
        return sources

    def _configuration(self) -> bytes:
        payload = {
            "format": "acr-public-settings-v1",
            "database_name": self.settings.database.name,
            "state_directory_name": self.settings.state_dir.name,
            "skills_directory_name": self.settings.skills_dir.name,
            "provider": self.settings.provider,
            "ollama_url": self.settings.ollama_url,
            "ollama_model": self.settings.ollama_model,
            "excluded": [
                "environment_variable_values",
                "keyring_values",
                "external_secret_store_values",
                "api_tokens",
                "dotenv_files",
            ],
        }
        encoded = (
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
            + "\n"
        ).encode("utf-8")
        findings = detect_secret_material(encoded.decode("utf-8"))
        if findings:
            raise SecretBoundaryError(
                "backup configuration rejects secret material: "
                + ",".join(findings)
            )
        return encoded

    def create(self, output: str | Path) -> dict[str, object]:
        destination = Path(output)
        if destination.exists():
            raise FileExistsError(f"backup already exists: {destination}")
        if not self.settings.database.is_file():
            raise FileNotFoundError(
                f"runtime database does not exist: {self.settings.database}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        sources = [
            *self._tree_sources(
                self.settings.skills_dir,
                prefix="skills",
                component="skills",
            ),
            *self._tree_sources(
                self.benchmarks_dir,
                prefix="benchmarks",
                component="benchmarks",
            ),
        ]
        if len(sources) + 3 > MAX_ENTRIES:
            raise ValueError("backup contains too many files")
        with tempfile.TemporaryDirectory(
            prefix=".acr-backup-", dir=destination.parent
        ) as temporary:
            temporary_root = Path(temporary)
            snapshot = temporary_root / "acr.db"
            source_db = sqlite3.connect(self.settings.database)
            target_db = sqlite3.connect(snapshot)
            try:
                source_db.backup(target_db)
            finally:
                target_db.close()
                source_db.close()
            database = _database_evidence(snapshot)
            if database["quick_check"] != "ok":
                raise ValueError("SQLite backup failed quick_check")
            _scan_database_secrets(snapshot)
            for source in sources:
                if source.source_path.stat().st_size > MAX_ENTRY_BYTES:
                    raise ValueError(
                        f"backup entry is too large: {source.archive_path}"
                    )
                _scan_file_secrets(
                    source.source_path,
                    boundary=f"backup {source.component}",
                )
            configuration = self._configuration()
            all_sources = [
                _Source("database/acr.db", snapshot, "database"),
                *sources,
            ]
            entries = [
                {
                    "path": item.archive_path,
                    "component": item.component,
                    "size": item.source_path.stat().st_size,
                    "sha256": _sha256_file(item.source_path),
                }
                for item in all_sources
            ]
            entries.append(
                {
                    "path": "configuration/settings.json",
                    "component": "configuration",
                    "size": len(configuration),
                    "sha256": hashlib.sha256(configuration).hexdigest(),
                }
            )
            total_size = sum(int(item["size"]) for item in entries)
            if total_size > MAX_TOTAL_BYTES:
                raise ValueError("backup exceeds the uncompressed size limit")
            manifest = {
                "format": BACKUP_FORMAT,
                "created_at": _now(),
                "runtime_version": _runtime_version(),
                "schema_version": database["schema_version"],
                "components": {
                    "database": {
                        "path": "database/acr.db",
                        "quick_check": database["quick_check"],
                    },
                    "skills": {
                        "path": "skills/",
                        "file_count": sum(
                            item.component == "skills" for item in all_sources
                        ),
                    },
                    "configuration": {
                        "path": "configuration/settings.json",
                        "contains_secret_values": False,
                    },
                    "benchmarks": {
                        "path": "benchmarks/",
                        "file_count": sum(
                            item.component == "benchmarks" for item in all_sources
                        ),
                    },
                    "learning_history": {
                        "included_in": "database/acr.db",
                        "row_counts": database["learning_history"],
                    },
                },
                "secret_policy": {
                    "plaintext_secret_values_included": False,
                    "database_text_scan": "high_confidence_v1",
                    "file_scan": "high_confidence_and_labeled_v1",
                },
                "entries": sorted(entries, key=lambda item: str(item["path"])),
            }
            manifest_bytes = (
                json.dumps(
                    manifest,
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")
            partial = temporary_root / "backup.partial"
            with zipfile.ZipFile(
                partial,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
                strict_timestamps=True,
            ) as archive:
                archive.writestr("manifest.json", manifest_bytes)
                archive.writestr(
                    "configuration/settings.json", configuration
                )
                for item in all_sources:
                    archive.write(item.source_path, item.archive_path)
            result = self.verify(partial)
            os.link(partial, destination)
            partial.unlink()
        result["backup"] = str(destination)
        result["created"] = True
        return result

    @staticmethod
    def _archive_inventory(
        archive: zipfile.ZipFile,
    ) -> dict[str, zipfile.ZipInfo]:
        infos = archive.infolist()
        if not 1 <= len(infos) <= MAX_ENTRIES:
            raise ValueError("backup entry count is invalid")
        inventory: dict[str, zipfile.ZipInfo] = {}
        total = 0
        for info in infos:
            path = _safe_member(info.filename)
            name = path.as_posix()
            if name in inventory:
                raise ValueError("backup contains duplicate member paths")
            if info.flag_bits & 0x1:
                raise ValueError("encrypted ZIP members are unsupported")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise ValueError("backup cannot contain symbolic links")
            if info.file_size < 0 or info.file_size > MAX_ENTRY_BYTES:
                raise ValueError("backup member exceeds the size limit")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise ValueError("backup exceeds the uncompressed size limit")
            inventory[name] = info
        return inventory

    @staticmethod
    def _read_bounded(
        archive: zipfile.ZipFile, info: zipfile.ZipInfo
    ) -> bytes:
        if info.file_size > MAX_CONTROL_BYTES:
            raise ValueError("backup control member exceeds the size limit")
        with archive.open(info, "r") as handle:
            data = handle.read(info.file_size + 1)
        if len(data) != info.file_size:
            raise ValueError("backup member size does not match its metadata")
        return data

    def verify(self, source: str | Path) -> dict[str, object]:
        backup = Path(source)
        if not backup.is_file():
            raise FileNotFoundError(f"backup does not exist: {backup}")
        with tempfile.TemporaryDirectory(prefix=".acr-verify-") as temporary:
            database_path = Path(temporary) / "acr.db"
            with zipfile.ZipFile(backup, "r") as archive:
                inventory = self._archive_inventory(archive)
                manifest_info = inventory.get("manifest.json")
                if manifest_info is None:
                    raise ValueError("backup manifest is missing")
                try:
                    manifest = json.loads(
                        self._read_bounded(archive, manifest_info).decode("utf-8")
                    )
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise ValueError("backup manifest is invalid JSON") from None
                if not isinstance(manifest, dict) or manifest.get("format") != BACKUP_FORMAT:
                    raise ValueError("backup format is unsupported")
                raw_entries = manifest.get("entries")
                if not isinstance(raw_entries, list):
                    raise ValueError("backup manifest entries are invalid")
                expected: dict[str, dict[str, object]] = {}
                for item in raw_entries:
                    if (
                        not isinstance(item, dict)
                        or set(item) != {"path", "component", "size", "sha256"}
                        or not isinstance(item["path"], str)
                        or not isinstance(item["component"], str)
                        or type(item["size"]) is not int
                        or not isinstance(item["sha256"], str)
                    ):
                        raise ValueError("backup manifest entry is invalid")
                    name = _safe_member(item["path"]).as_posix()
                    if (
                        name == "manifest.json"
                        or name in expected
                        or len(item["sha256"]) != 64
                    ):
                        raise ValueError("backup manifest entry is unsafe")
                    expected_component = (
                        "database"
                        if name == "database/acr.db"
                        else (
                            "configuration"
                            if name == "configuration/settings.json"
                            else (
                                "skills"
                                if name.startswith("skills/")
                                else (
                                    "benchmarks"
                                    if name.startswith("benchmarks/")
                                    else None
                                )
                            )
                        )
                    )
                    if item["component"] != expected_component:
                        raise ValueError(
                            "backup manifest has an unsupported component"
                        )
                    expected[name] = item
                if not {
                    "database/acr.db",
                    "configuration/settings.json",
                } <= set(expected):
                    raise ValueError("backup required components are missing")
                actual = set(inventory) - {"manifest.json"}
                if set(expected) != actual:
                    raise ValueError("backup manifest inventory does not match ZIP")
                for name in sorted(expected):
                    info = inventory[name]
                    declared = expected[name]
                    if info.file_size != declared["size"]:
                        raise ValueError(f"backup size mismatch: {name}")
                    digest = hashlib.sha256()
                    with archive.open(info, "r") as handle:
                        while chunk := handle.read(COPY_CHUNK):
                            digest.update(chunk)
                    if digest.hexdigest() != declared["sha256"]:
                        raise ValueError(f"backup hash mismatch: {name}")
                    if name == "database/acr.db":
                        with archive.open(info, "r") as reader, database_path.open(
                            "wb"
                        ) as writer:
                            shutil.copyfileobj(reader, writer, COPY_CHUNK)
                    else:
                        with archive.open(info, "r") as handle:
                            findings = _stream_secret_findings(handle)
                        if findings:
                            raise SecretBoundaryError(
                                "backup archive rejects secret material in "
                                f"{name}: {','.join(findings)}"
                            )
            if not database_path.is_file():
                raise ValueError("backup database entry is missing")
            database = _database_evidence(database_path)
            if database["quick_check"] != "ok":
                raise ValueError("backup database failed quick_check")
            _scan_database_secrets(database_path)
            manifest_schema = manifest.get("schema_version")
            if type(manifest_schema) is not int:
                raise ValueError("backup schema version is invalid")
            if manifest_schema != database["schema_version"]:
                raise ValueError("backup database schema does not match manifest")
            components = manifest.get("components")
            if (
                not isinstance(components, dict)
                or set(components)
                != {
                    "database",
                    "skills",
                    "configuration",
                    "benchmarks",
                    "learning_history",
                }
            ):
                raise ValueError("backup component manifest is invalid")
            if components["database"] != {
                "path": "database/acr.db",
                "quick_check": "ok",
            }:
                raise ValueError("backup database component is invalid")
            if components["configuration"] != {
                "path": "configuration/settings.json",
                "contains_secret_values": False,
            }:
                raise ValueError("backup configuration component is invalid")
            if components["skills"] != {
                "path": "skills/",
                "file_count": sum(
                    name.startswith("skills/") for name in expected
                ),
            }:
                raise ValueError("backup skills component is invalid")
            if components["benchmarks"] != {
                "path": "benchmarks/",
                "file_count": sum(
                    name.startswith("benchmarks/") for name in expected
                ),
            }:
                raise ValueError("backup benchmarks component is invalid")
            learning = components.get("learning_history")
            if (
                not isinstance(learning, dict)
                or learning.get("included_in") != "database/acr.db"
                or learning.get("row_counts") != database["learning_history"]
            ):
                raise ValueError("backup learning-history counts do not match")
            secret_policy = manifest.get("secret_policy")
            if (
                not isinstance(secret_policy, dict)
                or secret_policy.get("plaintext_secret_values_included")
                is not False
            ):
                raise ValueError("backup secret policy is invalid")
            if manifest_schema > EXPECTED_SCHEMA_VERSION:
                compatibility = "incompatible_newer_schema"
                restorable = False
            elif manifest_schema < 1:
                compatibility = "incompatible_unversioned_schema"
                restorable = False
            elif manifest_schema < EXPECTED_SCHEMA_VERSION:
                compatibility = "compatible_migration_required"
                restorable = True
            else:
                compatibility = "compatible_current"
                restorable = True
        return {
            "backup": str(backup),
            "format": BACKUP_FORMAT,
            "archive_sha256": _sha256_file(backup),
            "entry_count": len(expected),
            "schema_version": manifest_schema,
            "runtime_schema_version": EXPECTED_SCHEMA_VERSION,
            "compatibility": compatibility,
            "restorable": restorable,
            "quick_check": database["quick_check"],
            "learning_history": database["learning_history"],
            "secret_values_included": False,
            "verified": True,
        }

    def restore(
        self,
        source: str | Path,
        target: str | Path,
    ) -> dict[str, object]:
        verification = self.verify(source)
        if not verification["restorable"]:
            raise ValueError(
                f"backup is not restorable: {verification['compatibility']}"
            )
        destination = Path(target)
        if destination.exists():
            raise FileExistsError(
                "restore target must not already exist"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}-restore-",
                dir=destination.parent,
            )
        )
        try:
            with zipfile.ZipFile(source, "r") as archive:
                inventory = self._archive_inventory(archive)
                manifest = json.loads(
                    self._read_bounded(
                        archive, inventory["manifest.json"]
                    ).decode("utf-8")
                )
                entries = {
                    str(item["path"]): item for item in manifest["entries"]
                }
                mapping: dict[str, Path] = {
                    "database/acr.db": staging / "acr.db",
                    "configuration/settings.json": (
                        staging / "configuration.json"
                    ),
                }
                for name in entries:
                    if name.startswith("skills/"):
                        mapping[name] = staging / "skills" / Path(
                            *PurePosixPath(name).parts[1:]
                        )
                    elif name.startswith("benchmarks/"):
                        mapping[name] = staging / "benchmarks" / Path(
                            *PurePosixPath(name).parts[1:]
                        )
                    elif name not in mapping:
                        raise ValueError(
                            f"backup component cannot be restored: {name}"
                        )
                for name, output in sorted(mapping.items()):
                    output.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    with archive.open(inventory[name], "r") as reader, output.open(
                        "xb"
                    ) as writer:
                        while chunk := reader.read(COPY_CHUNK):
                            digest.update(chunk)
                            writer.write(chunk)
                    if digest.hexdigest() != entries[name]["sha256"]:
                        raise ValueError(
                            f"backup changed during restore: {name}"
                        )
                (staging / "backup-manifest.json").write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            restored_database = _database_evidence(staging / "acr.db")
            if restored_database["quick_check"] != "ok":
                raise ValueError("restored database failed quick_check")
            if destination.exists():
                raise FileExistsError(
                    "restore target appeared during restoration"
                )
            os.replace(staging, destination)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise
        return {
            **verification,
            "restored": True,
            "target": str(destination),
            "database": str(destination / "acr.db"),
            "skills": str(destination / "skills"),
            "benchmarks": str(destination / "benchmarks"),
            "configuration": str(destination / "configuration.json"),
            "activation_required": True,
        }
