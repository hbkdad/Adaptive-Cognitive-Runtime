from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


DEFAULT_PROFILE = "default"
ZERO_CLOUD_PROFILE = "zero-cloud"
SUPPORTED_PROFILES = (DEFAULT_PROFILE, ZERO_CLOUD_PROFILE)
ZERO_CLOUD_PROVIDERS = (None, "ollama")
ZERO_CLOUD_UNAVAILABLE = (
    "cloud_model_apis",
    "remote_embedding_apis",
    "external_telemetry_export",
    "hosted_state_synchronization",
    "automated_external_research_fetching",
)


def is_ollama_cloud_model(model: str) -> bool:
    normalized = model.strip().casefold()
    return normalized.endswith(":cloud") or normalized.endswith("-cloud")


def _is_loopback_service_url(value: str) -> bool:
    parsed = urlparse(value)
    try:
        parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


@dataclass(frozen=True)
class DeploymentPolicy:
    """Closed deployment-profile policy derived from trusted configuration."""

    name: str
    sqlite_required: bool
    filesystem_skills_required: bool
    local_embeddings: str
    allowed_model_providers: tuple[str, ...]
    telemetry_destination: str
    external_network: str
    cloud_api_required: bool
    unavailable_without_external_services: tuple[str, ...]

    @property
    def zero_cloud(self) -> bool:
        return self.name == ZERO_CLOUD_PROFILE

    @property
    def allow_ollama_cloud_models(self) -> bool:
        return not self.zero_cloud

    def validate(
        self,
        *,
        provider: str | None,
        ollama_url: str,
        ollama_model: str | None,
    ) -> None:
        if not self.zero_cloud:
            return
        if provider not in ZERO_CLOUD_PROVIDERS:
            raise ValueError(
                "zero-cloud profile permits only no model provider or Ollama"
            )
        if not _is_loopback_service_url(ollama_url):
            raise ValueError(
                "zero-cloud profile requires a root loopback Ollama URL"
            )
        if ollama_model is not None and is_ollama_cloud_model(ollama_model):
            raise ValueError(
                "zero-cloud profile rejects Ollama cloud model names"
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "sqlite_required": self.sqlite_required,
            "filesystem_skills_required": self.filesystem_skills_required,
            "local_embeddings": self.local_embeddings,
            "allowed_model_providers": list(self.allowed_model_providers),
            "telemetry_destination": self.telemetry_destination,
            "external_network": self.external_network,
            "cloud_api_required": self.cloud_api_required,
            "unavailable_without_external_services": list(
                self.unavailable_without_external_services
            ),
        }


def deployment_policy(name: str) -> DeploymentPolicy:
    if name not in SUPPORTED_PROFILES:
        raise ValueError(
            "ACR_DEPLOYMENT_PROFILE must be one of: "
            + ", ".join(SUPPORTED_PROFILES)
        )
    if name == ZERO_CLOUD_PROFILE:
        return DeploymentPolicy(
            name=name,
            sqlite_required=True,
            filesystem_skills_required=True,
            local_embeddings="ollama_optional",
            allowed_model_providers=("none", "ollama"),
            telemetry_destination="sqlite_only",
            external_network="denied_except_loopback",
            cloud_api_required=False,
            unavailable_without_external_services=ZERO_CLOUD_UNAVAILABLE,
        )
    return DeploymentPolicy(
        name=name,
        sqlite_required=True,
        filesystem_skills_required=True,
        local_embeddings="ollama_optional",
        allowed_model_providers=("configured",),
        telemetry_destination="sqlite_only",
        external_network="configuration_governed",
        cloud_api_required=False,
        unavailable_without_external_services=(),
    )
