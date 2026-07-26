from __future__ import annotations

from dataclasses import dataclass

from .memory import (
    MemoryReader,
    MemoryRecord,
    MemoryStatus,
    parse_timestamp,
    utc_now,
)

TRUSTED_HISTORY_STATUSES = (
    MemoryStatus.CONFIRMED,
    MemoryStatus.SUPERSEDED,
    MemoryStatus.ARCHIVED,
)
RESOLVABLE_STATUSES = (
    MemoryStatus.CONFIRMED,
    MemoryStatus.SUPERSEDED,
)


@dataclass(frozen=True)
class TemporalResolution:
    subject: str
    scope: str
    as_of: str
    preferred: MemoryRecord | None
    alternatives: tuple[MemoryRecord, ...]
    unresolved_conflict: bool
    reason: str


@dataclass(frozen=True)
class MemoryHistory:
    subject: str
    scope: str
    records: tuple[MemoryRecord, ...]


class TemporalMemory:
    """Resolve trusted memory without erasing historical evidence."""

    def __init__(self, reader: MemoryReader) -> None:
        self.reader = reader

    @staticmethod
    def _linked(left: MemoryRecord, right: MemoryRecord) -> bool:
        return (
            left.supersedes == right.id
            or left.superseded_by == right.id
            or right.supersedes == left.id
            or right.superseded_by == left.id
        )

    def current(
        self, subject: str, *, scope: str = "global"
    ) -> TemporalResolution:
        return self.at(subject, utc_now(), scope=scope)

    def at(
        self, subject: str, timestamp: str, *, scope: str = "global"
    ) -> TemporalResolution:
        moment = parse_timestamp(timestamp)
        records = self.reader.subject_records(
            subject,
            scope=scope,
            statuses=RESOLVABLE_STATUSES,
        )
        valid = [
            record
            for record in records
            if parse_timestamp(record.valid_from) <= moment
            and (
                record.valid_until is None
                or moment < parse_timestamp(record.valid_until)
            )
        ]
        valid.sort(
            key=lambda record: (
                record.scope == scope,
                parse_timestamp(record.valid_from),
                parse_timestamp(record.created_at),
                record.id,
            ),
            reverse=True,
        )
        if not valid:
            return TemporalResolution(
                subject=subject,
                scope=scope,
                as_of=moment.isoformat(),
                preferred=None,
                alternatives=(),
                unresolved_conflict=False,
                reason="no_trusted_memory_valid_at_time",
            )
        preferred, *alternatives = valid
        conflicts = [
            record
            for record in alternatives
            if record.content.strip().casefold()
            != preferred.content.strip().casefold()
            and record.scope == preferred.scope
            and not self._linked(preferred, record)
        ]
        reason = "latest_valid_from"
        if conflicts:
            reason += f"; unresolved_conflicts={len(conflicts)}"
        return TemporalResolution(
            subject=subject,
            scope=scope,
            as_of=moment.isoformat(),
            preferred=preferred,
            alternatives=tuple(alternatives),
            unresolved_conflict=bool(conflicts),
            reason=reason,
        )

    def history(
        self, subject: str, *, scope: str = "global"
    ) -> MemoryHistory:
        records = self.reader.subject_records(
            subject,
            scope=scope,
            statuses=TRUSTED_HISTORY_STATUSES,
        )
        return MemoryHistory(subject=subject, scope=scope, records=records)
