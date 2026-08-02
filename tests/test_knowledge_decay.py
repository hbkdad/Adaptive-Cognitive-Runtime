from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

from acr_runtime import AdaptiveRuntime
from acr_runtime.cli import main
from acr_runtime.knowledge_decay import DecayMode, KnowledgeDecayPolicy
from acr_runtime.memory import MemoryCreate, MemoryStatus, MemoryType


BASELINE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at(days: int) -> str:
    return (BASELINE + timedelta(days=days)).isoformat()


class KnowledgeDecayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "acr.db"
        self.runtime = AdaptiveRuntime(self.path)
        self.policy = KnowledgeDecayPolicy()

    def tearDown(self) -> None:
        self.runtime.close()
        self.temp_dir.cleanup()

    def add(
        self,
        memory_type: MemoryType,
        *,
        valid_from: str = at(0),
        valid_until: str | None = None,
        supersedes: str | None = None,
    ):
        return self.runtime.db.memories.create(
            MemoryCreate(
                type=memory_type,
                content=f"{memory_type.value} knowledge",
                scope="alpha",
                subject=f"{memory_type.value} subject",
                source_type="test",
                evidence=("test:knowledge-decay",),
                status=MemoryStatus.CONFIRMED,
                valid_from=valid_from,
                valid_until=valid_until,
                supersedes=supersedes,
            )
        )

    def test_profiles_cover_every_memory_type_with_distinct_decay(self):
        profiles = {
            memory_type: self.policy.profile_for(memory_type)
            for memory_type in MemoryType
        }

        self.assertEqual(set(profiles), set(MemoryType))
        self.assertEqual(
            profiles[MemoryType.DECISION].mode,
            DecayMode.SUPERSESSION_ONLY,
        )
        self.assertIsNone(
            profiles[MemoryType.DECISION].half_life_days()
        )
        self.assertLess(
            profiles[MemoryType.TEMPORARY].half_life_days(),
            profiles[MemoryType.SEMANTIC].half_life_days(),
        )
        self.assertLess(
            profiles[MemoryType.SEMANTIC].half_life_days(),
            profiles[MemoryType.PREFERENCE].half_life_days(),
        )

    def test_decisions_persist_while_volatile_memory_decays(self):
        decision = self.add(MemoryType.DECISION)
        temporary = self.add(MemoryType.TEMPORARY)

        stable = self.policy.assess(decision, assessed_at=at(180))
        decayed = self.policy.assess(temporary, assessed_at=at(1))

        self.assertEqual(stable.recency_score, 1.0)
        self.assertFalse(stable.review_due)
        self.assertEqual(stable.source_freshness_state, "unavailable")
        self.assertEqual(decayed.recency_score, 0.5)
        self.assertTrue(decayed.review_due)

    def test_explicit_validity_overrides_age_profile(self):
        future = self.add(MemoryType.PREFERENCE, valid_from=at(10))
        expired = self.add(
            MemoryType.DECISION,
            valid_from=at(0),
            valid_until=at(5),
        )

        before = self.policy.assess(future, assessed_at=at(0))
        after = self.policy.assess(expired, assessed_at=at(5))

        self.assertEqual(before.validity_state, "not_yet_valid")
        self.assertEqual(before.recency_score, 0.0)
        self.assertEqual(after.validity_state, "expired")
        self.assertEqual(after.recency_score, 0.0)

    def test_historical_assessment_preserves_pre_supersession_truth(self):
        old = self.add(MemoryType.DECISION, valid_from=at(0))
        self.add(
            MemoryType.DECISION,
            valid_from=at(10),
            supersedes=old.id,
        )
        superseded = self.runtime.db.memories.get(old.id)

        historical = self.policy.assess(superseded, assessed_at=at(5))
        current = self.policy.assess(superseded, assessed_at=at(10))

        self.assertEqual(historical.validity_state, "current")
        self.assertEqual(historical.recency_score, 1.0)
        self.assertEqual(current.validity_state, "expired")
        self.assertEqual(current.recency_score, 0.0)

    def test_cli_reports_policy_without_claiming_source_freshness(self):
        record = self.add(MemoryType.ENVIRONMENT)
        self.runtime.close()
        output = StringIO()

        with redirect_stdout(output):
            self.assertEqual(
                main(
                    [
                        "--db",
                        str(self.path),
                        "memory",
                        "half-life",
                        record.id,
                        "--at",
                        at(7),
                    ]
                ),
                0,
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["memory_type"], "environment")
        self.assertEqual(payload["half_life_days"], 7.0)
        self.assertEqual(payload["recency_score"], 0.5)
        self.assertEqual(payload["source_freshness_state"], "unavailable")
        self.runtime = AdaptiveRuntime(self.path)


if __name__ == "__main__":
    unittest.main()
