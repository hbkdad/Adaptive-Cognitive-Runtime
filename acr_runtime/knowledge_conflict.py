from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .memory import MemoryReader, MemoryRecord, MemoryStatus, parse_timestamp

SOURCE_RELIABILITY = {
    "user": 0.95,
    "test": 0.95,
    "file": 0.85,
    "decision_record": 0.85,
    "runtime": 0.80,
    "model": 0.55,
    "legacy": 0.50,
}


@dataclass(frozen=True)
class ConflictAnalysis:
    left_id: str
    right_id: str
    classification: str
    preferred_id: str | None
    reason: str
    comparison: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "left_id": self.left_id,
            "right_id": self.right_id,
            "classification": self.classification,
            "preferred_id": self.preferred_id,
            "reason": self.reason,
            "comparison": self.comparison,
        }


class KnowledgeConflictEngine:
    """Explains contradictory claims without inventing a winner."""

    def __init__(self, reader: MemoryReader) -> None:
        self.reader = reader

    @staticmethod
    def _normalized(value: str) -> str:
        return " ".join(value.casefold().split())

    @staticmethod
    def _end(record: MemoryRecord) -> datetime:
        return (
            parse_timestamp(record.valid_until)
            if record.valid_until
            else datetime.max.replace(tzinfo=timezone.utc)
        )

    @classmethod
    def _overlap(cls, left: MemoryRecord, right: MemoryRecord) -> bool:
        return max(
            parse_timestamp(left.valid_from),
            parse_timestamp(right.valid_from),
        ) < min(cls._end(left), cls._end(right))

    @staticmethod
    def _reliability(record: MemoryRecord) -> float:
        return SOURCE_RELIABILITY.get(record.source_type or "", 0.5)

    def compare(
        self, left: MemoryRecord | str, right: MemoryRecord | str
    ) -> ConflictAnalysis:
        if isinstance(left, str):
            loaded = self.reader.get(left)
            if loaded is None:
                raise LookupError(f"Unknown memory: {left}")
            left = loaded
        if isinstance(right, str):
            loaded = self.reader.get(right)
            if loaded is None:
                raise LookupError(f"Unknown memory: {right}")
            right = loaded
        if left.id == right.id:
            raise ValueError("Conflict comparison requires two distinct memories")
        linked = (
            left.supersedes == right.id
            or left.superseded_by == right.id
            or right.supersedes == left.id
            or right.superseded_by == left.id
        )
        preferred: str | None = None
        if self._normalized(left.content) == self._normalized(right.content):
            classification = "no_conflict"
            reason = "normalized_claims_match"
        elif linked:
            classification = "one_supersedes_another"
            if left.supersedes == right.id or right.superseded_by == left.id:
                preferred = left.id
            else:
                preferred = right.id
            reason = "explicit_supersession_link"
        elif left.scope != right.scope:
            classification = "both_valid_different_scopes"
            reason = "claims_are_bound_to_different_explicit_scopes"
        elif not self._overlap(left, right):
            classification = "both_valid_different_times"
            reason = "validity_intervals_do_not_overlap"
        else:
            classification = "unresolved_contradiction"
            reason = "overlapping_unlinked_claims_require_review"
        comparison = {
            "evidence": {
                "left_count": len(left.evidence),
                "right_count": len(right.evidence),
                "shared": sorted(set(left.evidence) & set(right.evidence)),
            },
            "timestamps": {
                "left_valid_from": left.valid_from,
                "left_valid_until": left.valid_until,
                "right_valid_from": right.valid_from,
                "right_valid_until": right.valid_until,
                "overlap": self._overlap(left, right),
            },
            "reliability": {
                "left": self._reliability(left),
                "right": self._reliability(right),
                "left_source_type": left.source_type,
                "right_source_type": right.source_type,
            },
            "confidence": {
                "left": left.confidence,
                "right": right.confidence,
            },
            "scope": {
                "left": left.scope,
                "right": right.scope,
                "same": left.scope == right.scope,
            },
        }
        return ConflictAnalysis(
            left.id, right.id, classification, preferred, reason, comparison
        )

    def analyze_subject(self, subject: str, *, scope: str) -> dict[str, object]:
        if not subject.strip() or not scope.strip():
            raise ValueError("Conflict subject and scope cannot be empty")
        records = self.reader.subject_records(
            subject,
            scope=scope,
            statuses=(MemoryStatus.CONFIRMED, MemoryStatus.SUPERSEDED),
        )
        analyses = []
        for index, left in enumerate(records):
            for right in records[index + 1 :]:
                if self._normalized(left.content) != self._normalized(right.content):
                    analyses.append(self.compare(left, right).as_dict())
        return {
            "subject": subject,
            "scope": scope,
            "records": len(records),
            "conflicts": analyses,
            "requires_review": any(
                item["classification"] == "unresolved_contradiction"
                for item in analyses
            ),
        }
