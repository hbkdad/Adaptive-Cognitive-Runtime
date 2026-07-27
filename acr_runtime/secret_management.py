from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal, Mapping, Protocol, TypeVar

if TYPE_CHECKING:
    from .permissions import PermissionController

SecretProviderName = Literal["env", "keyring", "external"]
T = TypeVar("T")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

_HIGH_CONFIDENCE_PATTERNS = (
    (
        "openai_api_key",
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    ),
    (
        "github_token",
        re.compile(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|"
            r"github_pat_[A-Za-z0-9_]{40,255})\b"
        ),
    ),
    (
        "aws_access_key",
        re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    ),
    (
        "google_api_key",
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ),
    (
        "slack_token",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    ),
    (
        "private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
        ),
    ),
    (
        "authorization_bearer",
        re.compile(
            r"(?i)\b(?:authorization\s*:\s*)?bearer\s+"
            r"[A-Za-z0-9._~+/=-]{12,}"
        ),
    ),
    (
        "credentialed_url",
        re.compile(
            r"(?i)\b[a-z][a-z0-9+.-]*://"
            r"[^/\s:@]{1,128}:[^/\s@]{8,256}@"
        ),
    ),
)
_LABELED_SECRET_PATTERN = (
    "labeled_secret",
    re.compile(
        r"""(?ix)
        \b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|
        secret|password|passwd|pwd)\b
        \s*["']?\s*[:=]\s*["']?
        [A-Za-z0-9._~+/=-]{8,512}
        """
    ),
)
_REDACTION_ONLY_PATTERNS = (
    re.compile(
        r"""(?ix)
        \bkey\b\s*["']?\s*[:=]\s*["']?
        [A-Za-z0-9._~+/=-]{8,512}
        """
    ),
)


class SecretBoundaryError(ValueError):
    pass


def detect_secret_material(
    value: str, *, include_labeled: bool = True
) -> tuple[str, ...]:
    patterns = list(_HIGH_CONFIDENCE_PATTERNS)
    if include_labeled:
        patterns.append(_LABELED_SECRET_PATTERN)
    return tuple(
        name for name, pattern in patterns if pattern.search(value)
    )


def redact_secret_text(value: str) -> str:
    redacted = value
    for _, pattern in (*_HIGH_CONFIDENCE_PATTERNS, _LABELED_SECRET_PATTERN):
        redacted = pattern.sub("[REDACTED]", redacted)
    for pattern in _REDACTION_ONLY_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def redact_secret_value(value: object) -> object:
    if isinstance(value, str):
        return redact_secret_text(value)
    if isinstance(value, tuple):
        return tuple(redact_secret_value(item) for item in value)
    if isinstance(value, list):
        return [redact_secret_value(item) for item in value]
    if isinstance(value, dict):
        result: dict[object, object] = {}
        for key, item in value.items():
            key_text = str(key).casefold()
            result[key] = (
                "[REDACTED]"
                if any(
                    marker in key_text
                    for marker in (
                        "key", "token", "secret", "password", "passwd",
                    )
                )
                else redact_secret_value(item)
            )
        return result
    return value


def sanitize_secret_json(value: str) -> str:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return json.dumps(
            {"unparsed": redact_secret_text(value)}, sort_keys=True
        )
    return json.dumps(redact_secret_value(payload), sort_keys=True)


def assert_secret_free(value: str, boundary: str) -> None:
    findings = detect_secret_material(value)
    if findings:
        raise SecretBoundaryError(
            f"{boundary} rejects secret material: {','.join(findings)}"
        )


@dataclass(frozen=True)
class SecretReference:
    provider: SecretProviderName
    key: str

    def __post_init__(self) -> None:
        if self.provider not in {"env", "keyring", "external"}:
            raise ValueError("Unsupported secret provider")
        if self.provider == "env":
            valid = re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", self.key)
        else:
            valid = re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._/@:-]{0,255}", self.key
            )
        if valid is None:
            raise ValueError("Secret key is invalid or unbounded")

    @classmethod
    def parse(cls, value: str) -> "SecretReference":
        if ":" not in value:
            raise ValueError("Secret reference must be provider:key")
        provider, key = value.split(":", 1)
        return cls(provider=provider, key=key)

    @property
    def canonical(self) -> str:
        return f"{self.provider}:{self.key}"

    @property
    def reference_hash(self) -> str:
        return hashlib.sha256(self.canonical.encode("utf-8")).hexdigest()

    @property
    def resource_scope(self) -> str:
        return f"secret:{self.reference_hash}"

    def public_summary(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "reference_hash": self.reference_hash,
            "resource_scope": self.resource_scope,
        }


class SecretProvider(Protocol):
    name: SecretProviderName

    def get(self, key: str) -> str | None: ...


class EnvironmentSecretProvider:
    name: SecretProviderName = "env"

    def __init__(
        self, environ: Mapping[str, str] | None = None
    ) -> None:
        self.environ = os.environ if environ is None else environ

    def get(self, key: str) -> str | None:
        value = self.environ.get(key)
        return value if value else None


class KeyringSecretProvider:
    name: SecretProviderName = "keyring"

    def __init__(self, *, service_name: str = "acr-runtime") -> None:
        if (
            not service_name.strip()
            or service_name != service_name.strip()
            or len(service_name) > 128
        ):
            raise ValueError("Keyring service name is invalid")
        self.service_name = service_name

    def get(self, key: str) -> str | None:
        try:
            import keyring  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError(
                "OS keyring support requires the 'secrets' optional dependency"
            ) from error
        return keyring.get_password(self.service_name, key)


class ExternalSecretProvider:
    name: SecretProviderName = "external"

    def __init__(self, resolver: Callable[[str], str | None]) -> None:
        self.resolver = resolver

    def get(self, key: str) -> str | None:
        return self.resolver(key)


class SecretLease:
    """One-use secret buffer whose repr and public metadata never expose value."""

    __slots__ = (
        "id", "audit_id", "provider", "reference_hash", "_value", "_closed"
    )

    def __init__(
        self,
        value: str,
        *,
        audit_id: str,
        provider: SecretProviderName,
        reference_hash: str,
    ) -> None:
        if not value or len(value) > 65_536:
            raise ValueError("Resolved secret must contain 1..65536 characters")
        self.id = str(uuid.uuid4())
        self.audit_id = audit_id
        self.provider = provider
        self.reference_hash = reference_hash
        self._value = bytearray(value.encode("utf-8"))
        self._closed = False

    def __repr__(self) -> str:
        return (
            "SecretLease(id="
            f"'{self.id}', provider='{self.provider}', value=[REDACTED])"
        )

    @property
    def closed(self) -> bool:
        return self._closed

    def use(self, operation: Callable[[str], T]) -> T:
        if self._closed:
            raise RuntimeError("Secret lease is already closed")
        value = self._value.decode("utf-8")
        try:
            result = operation(value)
            if _returned_value_contains_secret(result, value):
                raise SecretBoundaryError(
                    "Secret operation attempted to return the leased value"
                )
            return result
        finally:
            value = ""
            self.close()

    def close(self) -> None:
        if not self._closed:
            for index in range(len(self._value)):
                self._value[index] = 0
            self._value.clear()
            self._closed = True

    def __enter__(self) -> "SecretLease":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _returned_value_contains_secret(
    result: object, secret: str, *, depth: int = 0
) -> bool:
    if depth > 8:
        return False
    if isinstance(result, str):
        return secret in result
    if isinstance(result, bytes):
        return secret.encode("utf-8") in result
    if isinstance(result, dict):
        return any(
            _returned_value_contains_secret(key, secret, depth=depth + 1)
            or _returned_value_contains_secret(item, secret, depth=depth + 1)
            for key, item in result.items()
        )
    if isinstance(result, (tuple, list, set, frozenset)):
        return any(
            _returned_value_contains_secret(item, secret, depth=depth + 1)
            for item in result
        )
    return False


class SecretManager:
    """Permission-gated resolution with value-free access auditing."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        permissions: "PermissionController",
        *,
        providers: tuple[SecretProvider, ...] | None = None,
    ) -> None:
        self.connection = connection
        self.permissions = permissions
        configured = providers or (
            EnvironmentSecretProvider(),
            KeyringSecretProvider(),
        )
        self.providers = {provider.name: provider for provider in configured}

    def _record(
        self,
        reference: SecretReference,
        *,
        subject_type: str,
        subject_id: str,
        decision: str,
        capability_decision_id: str,
    ) -> str:
        event_id = str(uuid.uuid4())
        self.connection.execute(
            """
            INSERT INTO secret_access_events (
                id, reference_hash, provider, subject_type, subject_id,
                decision, capability_decision_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id, reference.reference_hash, reference.provider,
                subject_type, subject_id, decision,
                capability_decision_id, _utc_now(),
            ),
        )
        self.connection.commit()
        return event_id

    def resolve(
        self,
        reference: SecretReference | str,
        *,
        subject_type: str,
        subject_id: str,
    ) -> SecretLease:
        parsed = (
            SecretReference.parse(reference)
            if isinstance(reference, str)
            else reference
        )
        from .permissions import CapabilityCheck

        decision = self.permissions.check(CapabilityCheck(
            subject_type=subject_type,
            subject_id=subject_id,
            capability="credential.use",
            resource_scope=parsed.resource_scope,
        ))
        if not decision["allowed"]:
            self._record(
                parsed,
                subject_type=subject_type,
                subject_id=subject_id,
                decision="denied",
                capability_decision_id=str(decision["id"]),
            )
            raise PermissionError("credential.use default deny")
        provider = self.providers.get(parsed.provider)
        if provider is None:
            audit_id = self._record(
                parsed,
                subject_type=subject_type,
                subject_id=subject_id,
                decision="provider_unavailable",
                capability_decision_id=str(decision["id"]),
            )
            raise LookupError(
                f"Secret provider unavailable; audit={audit_id}"
            )
        try:
            value = provider.get(parsed.key)
        except Exception:
            audit_id = self._record(
                parsed,
                subject_type=subject_type,
                subject_id=subject_id,
                decision="provider_error",
                capability_decision_id=str(decision["id"]),
            )
            raise RuntimeError(
                f"Secret provider failed; audit={audit_id}"
            ) from None
        if value is None:
            audit_id = self._record(
                parsed,
                subject_type=subject_type,
                subject_id=subject_id,
                decision="missing",
                capability_decision_id=str(decision["id"]),
            )
            raise LookupError(f"Secret is unavailable; audit={audit_id}")
        audit_id = self._record(
            parsed,
            subject_type=subject_type,
            subject_id=subject_id,
            decision="granted",
            capability_decision_id=str(decision["id"]),
        )
        return SecretLease(
            value,
            audit_id=audit_id,
            provider=parsed.provider,
            reference_hash=parsed.reference_hash,
        )

    def inspect(self, event_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM secret_access_events WHERE id=?", (event_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Unknown secret access event: {event_id}")
        return dict(row)


def scan_staged_git_secrets(
    repository: str | Path = ".",
) -> list[dict[str, object]]:
    root = Path(repository)
    names = subprocess.run(
        [
            "git", "diff", "--cached", "--diff-filter=ACMR",
            "--name-only", "-z",
        ],
        cwd=root,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout
    findings: list[dict[str, object]] = []
    for raw_name in names.split(b"\0"):
        if not raw_name:
            continue
        name = raw_name.decode("utf-8", errors="surrogateescape")
        blob = subprocess.run(
            ["git", "show", f":{name}"],
            cwd=root,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if blob.returncode != 0 or len(blob.stdout) > 2_000_000:
            continue
        if b"\0" in blob.stdout[:8_192]:
            continue
        content = blob.stdout.decode("utf-8", errors="replace")
        types = detect_secret_material(content, include_labeled=False)
        if types:
            findings.append({
                "path": name,
                "types": list(types),
                "content_sha256": hashlib.sha256(blob.stdout).hexdigest(),
            })
    return findings
