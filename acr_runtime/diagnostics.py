from __future__ import annotations

import json
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from .config import Settings
from .db import RuntimeDB
from .deployment_profile import is_ollama_cloud_model

CheckStatus = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: CheckStatus
    detail: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def discover_ollama_models(
    url: str,
    *,
    timeout_seconds: float = 1.0,
    allow_cloud_models: bool = True,
) -> tuple[str, list[str]]:
    executable = shutil.which("ollama")
    if executable is None:
        return "Ollama is not installed or is not on PATH", []

    request = urllib.request.Request(
        f"{url}/api/tags",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
        return "Ollama is installed but its local API is unavailable", []

    models = sorted(
        model["name"]
        for model in payload.get("models", [])
        if isinstance(model, dict) and isinstance(model.get("name"), str)
        and (
            allow_cloud_models
            or not is_ollama_cloud_model(model["name"])
        )
    )
    if not models:
        return "Ollama is running with no downloaded models", []
    return f"Ollama is running with {len(models)} model(s)", models


def _filesystem_check(path: Path) -> DoctorCheck:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix="acr-doctor-", delete=True):
            pass
    except OSError as error:
        return DoctorCheck("filesystem", "fail", f"{path}: {error}")
    return DoctorCheck("filesystem", "pass", f"{path} is writable")


def run_doctor(settings: Settings) -> list[DoctorCheck]:
    settings.ensure_local_directories()
    checks: list[DoctorCheck] = []
    profile = settings.profile_policy

    supported = sys.version_info >= (3, 11)
    checks.append(
        DoctorCheck(
            "python",
            "pass" if supported else "fail",
            f"{sys.version.split()[0]} (requires 3.11+)",
        )
    )
    checks.append(_filesystem_check(settings.state_dir))
    checks.append(
        DoctorCheck(
            "deployment_profile",
            "pass",
            (
                f"{profile.name}; external_network={profile.external_network}; "
                f"telemetry={profile.telemetry_destination}"
            ),
        )
    )

    try:
        with RuntimeDB(settings.database) as database:
            health = database.health()
    except Exception as error:
        checks.extend(
            [
                DoctorCheck("database", "fail", str(error)),
                DoctorCheck("migrations", "fail", "Database could not be inspected"),
                DoctorCheck("memory_store", "fail", "Database could not be inspected"),
            ]
        )
    else:
        checks.extend(
            [
                DoctorCheck(
                    "database",
                    "pass" if health["quick_check"] == "ok" else "fail",
                    f"{settings.database} quick_check={health['quick_check']}",
                ),
                DoctorCheck(
                    "migrations",
                    "pass" if health["schema_current"] else "fail",
                    f"schema {health['schema_version']}/{health['expected_schema_version']}",
                ),
                DoctorCheck(
                    "memory_store",
                    "pass" if health["fts5_available"] else "fail",
                    f"FTS5={'available' if health['fts5_available'] else 'unavailable'}",
                ),
            ]
        )

    provider_detail = (
        f"Configured provider: {settings.provider}"
        if settings.provider
        else "No model provider configured; deterministic core remains available"
    )
    checks.append(
        DoctorCheck("providers", "pass" if settings.provider else "warn", provider_detail)
    )

    ollama_detail, models = discover_ollama_models(
        settings.ollama_url,
        allow_cloud_models=profile.allow_ollama_cloud_models,
    )
    checks.append(
        DoctorCheck(
            "local_models",
            "pass" if models else "warn",
            f"{ollama_detail}; models={', '.join(models) if models else 'none'}",
        )
    )

    skill_ok = settings.skills_dir.is_dir()
    checks.append(
        DoctorCheck(
            "skill_directory",
            "pass" if skill_ok else "fail",
            f"{settings.skills_dir} is {'available' if skill_ok else 'unavailable'}",
        )
    )
    return checks
