from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from .secret_management import assert_secret_free


SCHEMA_VERSION = 1
RELEASE_GATES = (
    "tests",
    "migrations_test_db",
    "security_scans",
    "benchmark_subset",
    "cli",
    "api",
    "clean_install",
    "upgrade",
    "changelog",
)
GATE_STATUSES = ("passed", "failed", "unavailable")
VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
RUN_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
MAX_EVIDENCE_AGE_SECONDS = 24 * 60 * 60
MAX_CLOCK_SKEW = timedelta(minutes=5)


def _text(value: object, *, field: str, maximum: int = 2_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} must be bounded non-empty text")
    normalized = value.strip()
    assert_secret_free(normalized, field)
    return normalized


def _text_list(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise ValueError(f"{field} must contain 1 to 16 items")
    result = tuple(_text(item, field=field, maximum=512) for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"{field} contains duplicates")
    return result


def _timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from None
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ReleaseGateEvidence:
    gate: str
    status: str
    command: str
    exit_code: int | None
    run_ref: str | None
    artifact_sha256: str | None
    completed_at: str
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.gate not in RELEASE_GATES:
            raise ValueError("release gate is invalid")
        if self.status not in GATE_STATUSES:
            raise ValueError("release gate status is invalid")
        _timestamp(self.completed_at, field="completed_at")
        if self.status == "unavailable":
            if (
                self.exit_code is not None
                or self.run_ref is not None
                or self.artifact_sha256 is not None
            ):
                raise ValueError("unavailable gate cannot carry run results")
            return
        if type(self.exit_code) is not int:
            raise ValueError("completed gate requires an integer exit_code")
        if self.run_ref is None or not RUN_REF.fullmatch(self.run_ref):
            raise ValueError("completed gate requires a valid run_ref")
        if self.artifact_sha256 is None or not SHA256.fullmatch(
            self.artifact_sha256
        ):
            raise ValueError("completed gate requires an artifact SHA-256")
        if self.status == "passed" and self.exit_code != 0:
            raise ValueError("passed gate requires exit_code 0")
        if self.status == "failed" and self.exit_code == 0:
            raise ValueError("failed gate requires a nonzero exit_code")

    def as_dict(self) -> dict[str, object]:
        return {
            "gate": self.gate,
            "status": self.status,
            "command": self.command,
            "exit_code": self.exit_code,
            "run_ref": self.run_ref,
            "artifact_sha256": self.artifact_sha256,
            "completed_at": self.completed_at,
            "evidence": list(self.evidence),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "ReleaseGateEvidence":
        expected = {
            "gate",
            "status",
            "command",
            "exit_code",
            "run_ref",
            "artifact_sha256",
            "completed_at",
            "evidence",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("release gate evidence has an invalid shape")
        for field in ("gate", "status", "completed_at"):
            if not isinstance(payload[field], str):
                raise ValueError(f"{field} must be text")
        for field in ("run_ref", "artifact_sha256"):
            if payload[field] is not None and not isinstance(payload[field], str):
                raise ValueError(f"{field} must be text or null")
        return cls(
            gate=payload["gate"],
            status=payload["status"],
            command=_text(payload["command"], field="gate command", maximum=512),
            exit_code=payload["exit_code"],
            run_ref=payload["run_ref"],
            artifact_sha256=payload["artifact_sha256"],
            completed_at=payload["completed_at"],
            evidence=_text_list(payload["evidence"], field="gate evidence"),
        )


@dataclass(frozen=True)
class ReleaseEvidenceManifest:
    version: str
    tag: str
    commit_sha: str
    created_at: str
    gates: tuple[ReleaseGateEvidence, ...]
    tag_absent: bool
    tag_check_ref: str
    immutable_release_enabled: bool
    immutability_check_ref: str

    def __post_init__(self) -> None:
        if not VERSION.fullmatch(self.version):
            raise ValueError("version must be a three-part release version")
        if self.tag != f"v{self.version}":
            raise ValueError("tag must equal v plus version")
        if not COMMIT_SHA.fullmatch(self.commit_sha):
            raise ValueError("commit_sha must be 40 lowercase hex characters")
        if type(self.tag_absent) is not bool:
            raise ValueError("tag_absent must be boolean")
        if type(self.immutable_release_enabled) is not bool:
            raise ValueError("immutable_release_enabled must be boolean")
        if not RUN_REF.fullmatch(self.tag_check_ref):
            raise ValueError("tag_check_ref must be a valid evidence reference")
        if not RUN_REF.fullmatch(self.immutability_check_ref):
            raise ValueError(
                "immutability_check_ref must be a valid evidence reference"
            )
        created = _timestamp(self.created_at, field="created_at")
        now = datetime.now(timezone.utc)
        if created > now + MAX_CLOCK_SKEW or (
            now - created
        ).total_seconds() > MAX_EVIDENCE_AGE_SECONDS:
            raise ValueError("release manifest is stale or from the future")
        if tuple(item.gate for item in self.gates) != RELEASE_GATES:
            raise ValueError("gates must cover every release gate in order")
        for item in self.gates:
            completed = _timestamp(item.completed_at, field="completed_at")
            age = (created - completed).total_seconds()
            if age < 0 or age > MAX_EVIDENCE_AGE_SECONDS:
                raise ValueError("release gate evidence is stale or from the future")

    @property
    def ready_to_tag(self) -> bool:
        return (
            all(item.status == "passed" for item in self.gates)
            and self.tag_absent
            and self.immutable_release_enabled
        )

    @property
    def blockers(self) -> tuple[str, ...]:
        blockers = [
            f"gate:{item.gate}:{item.status}"
            for item in self.gates
            if item.status != "passed"
        ]
        if not self.tag_absent:
            blockers.append("tag:already_exists")
        if not self.immutable_release_enabled:
            blockers.append("github:immutable_release_disabled")
        return tuple(blockers)

    @property
    def review_hash(self) -> str:
        encoded = json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "version": self.version,
                "tag": self.tag,
                "commit_sha": self.commit_sha,
                "created_at": self.created_at,
                "gates": [item.as_dict() for item in self.gates],
                "tag_absent": self.tag_absent,
                "tag_check_ref": self.tag_check_ref,
                "immutable_release_enabled": self.immutable_release_enabled,
                "immutability_check_ref": self.immutability_check_ref,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "version": self.version,
            "tag": self.tag,
            "commit_sha": self.commit_sha,
            "created_at": self.created_at,
            "gates": [item.as_dict() for item in self.gates],
            "tag_absent": self.tag_absent,
            "tag_check_ref": self.tag_check_ref,
            "immutable_release_enabled": self.immutable_release_enabled,
            "immutability_check_ref": self.immutability_check_ref,
            "ready_to_tag": self.ready_to_tag,
            "blockers": list(self.blockers),
            "review_hash": self.review_hash,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "ReleaseEvidenceManifest":
        expected = {
            "schema_version",
            "version",
            "tag",
            "commit_sha",
            "created_at",
            "gates",
            "tag_absent",
            "tag_check_ref",
            "immutable_release_enabled",
            "immutability_check_ref",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("release evidence manifest has an invalid shape")
        if payload["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unsupported release manifest schema_version")
        for field in (
            "version",
            "tag",
            "commit_sha",
            "created_at",
            "tag_check_ref",
            "immutability_check_ref",
        ):
            if not isinstance(payload[field], str):
                raise ValueError(f"{field} must be text")
        if not isinstance(payload["gates"], list):
            raise ValueError("gates must be a list")
        return cls(
            version=payload["version"],
            tag=payload["tag"],
            commit_sha=payload["commit_sha"],
            created_at=payload["created_at"],
            gates=tuple(
                ReleaseGateEvidence.from_dict(item) for item in payload["gates"]
            ),
            tag_absent=payload["tag_absent"],
            tag_check_ref=payload["tag_check_ref"],
            immutable_release_enabled=payload["immutable_release_enabled"],
            immutability_check_ref=payload["immutability_check_ref"],
        )


def validate_manifest(path: str | Path) -> ReleaseEvidenceManifest:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ReleaseEvidenceManifest.from_dict(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate fresh, complete release evidence without tagging."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("manifest")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = validate_manifest(args.manifest)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {"valid": True, **manifest.as_dict()}, indent=2, sort_keys=True
        )
    )
    return 0 if manifest.ready_to_tag else 1


if __name__ == "__main__":
    raise SystemExit(main())
