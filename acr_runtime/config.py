from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .deployment_profile import (
    DEFAULT_PROFILE,
    DeploymentPolicy,
    deployment_policy,
)


@dataclass(frozen=True)
class Settings:
    """Typed, secret-safe runtime configuration."""

    database: Path
    state_dir: Path
    skills_dir: Path
    provider: str | None
    ollama_url: str
    ollama_model: str | None = None
    deployment_profile: str = DEFAULT_PROFILE

    def __post_init__(self) -> None:
        self.profile_policy.validate(
            provider=self.provider,
            ollama_url=self.ollama_url,
            ollama_model=self.ollama_model,
        )

    @property
    def profile_policy(self) -> DeploymentPolicy:
        return deployment_policy(self.deployment_profile)

    @classmethod
    def from_env(
        cls,
        *,
        database: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> "Settings":
        values = os.environ if environ is None else environ
        state_dir = Path(values.get("ACR_STATE_DIR", ".acr"))
        database_path = Path(
            database if database is not None else values.get("ACR_DATABASE", state_dir / "acr.db")
        )
        skills_dir = Path(values.get("ACR_SKILLS_DIR", state_dir / "skills"))
        provider = values.get("ACR_PROVIDER") or None
        ollama_url = values.get("ACR_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
        ollama_model = values.get("ACR_OLLAMA_MODEL") or None
        deployment_profile = values.get(
            "ACR_DEPLOYMENT_PROFILE", DEFAULT_PROFILE
        )
        return cls(
            database=database_path,
            state_dir=state_dir,
            skills_dir=skills_dir,
            provider=provider,
            ollama_url=ollama_url,
            ollama_model=ollama_model,
            deployment_profile=deployment_profile,
        )

    def ensure_local_directories(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.database.parent.mkdir(parents=True, exist_ok=True)

    def public_summary(self) -> dict[str, object]:
        """Return configuration that is safe to print in diagnostics."""
        return {
            "database": str(self.database),
            "state_dir": str(self.state_dir),
            "skills_dir": str(self.skills_dir),
            "provider": self.provider,
            "ollama_url": self.ollama_url,
            "ollama_model": self.ollama_model,
            "deployment": self.profile_policy.as_dict(),
        }
