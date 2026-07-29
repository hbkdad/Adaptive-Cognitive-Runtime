from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from acr_runtime.cli import main
from acr_runtime.documentation_agent import ARTIFACTS, DocumentationAgent


class DocumentationAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(__file__).parents[1]
        self.proposals = Path(self.temporary.name) / "proposals"
        self.published = Path(self.temporary.name) / "published"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_proposal_contains_exact_source_derived_artifact_set(self):
        proposal = DocumentationAgent(self.root).propose(self.proposals)
        candidate = Path(proposal.candidate_dir)
        manifest = json.loads(
            (candidate / "manifest.json").read_text(encoding="utf-8")
        )

        self.assertEqual(
            {path.name for path in candidate.iterdir()},
            {*ARTIFACTS, "manifest.json"},
        )
        self.assertEqual(
            set(manifest["artifact_hashes"]), set(ARTIFACTS)
        )
        self.assertIn(
            "acr_runtime.service",
            (candidate / "architecture-map.md").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "`GET` | `/health`",
            (candidate / "api-reference.md").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "acr docs propose-reference",
            (candidate / "cli-reference.md").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "`memories`",
            (candidate / "memory-schema.md").read_text(encoding="utf-8"),
        )

    def test_review_rejects_tampering_and_source_drift(self):
        agent = DocumentationAgent(self.root)
        proposal = agent.propose(self.proposals)
        candidate = Path(proposal.candidate_dir)
        self.assertTrue(
            agent.review(candidate, published_dir=self.published)["fresh"]
        )

        architecture = candidate / "architecture-map.md"
        architecture.write_text(
            architecture.read_text(encoding="utf-8") + "tampered\n",
            encoding="utf-8",
        )
        review = agent.review(candidate, published_dir=self.published)
        self.assertFalse(review["fresh"])
        self.assertIsNone(review["review_hash"])

    def test_publish_requires_exact_fresh_review_and_explicit_approval(self):
        agent = DocumentationAgent(self.root)
        proposal = agent.propose(self.proposals)
        review = agent.review(
            proposal.candidate_dir, published_dir=self.published
        )

        with self.assertRaises(PermissionError):
            agent.publish(
                proposal.candidate_dir,
                review_hash=review["review_hash"],
                approved=False,
                destination=self.published,
            )
        with self.assertRaises(PermissionError):
            agent.publish(
                proposal.candidate_dir,
                review_hash="0" * 64,
                approved=True,
                destination=self.published,
            )
        result = agent.publish(
            proposal.candidate_dir,
            review_hash=review["review_hash"],
            approved=True,
            destination=self.published,
        )
        self.assertEqual(set(result["published"]), set(ARTIFACTS))
        self.assertEqual(
            {path.name for path in self.published.iterdir()},
            set(ARTIFACTS),
        )
        self.assertFalse(
            agent.review(
                proposal.candidate_dir, published_dir=self.published
            )["publishable"]
        )

    def test_source_change_makes_existing_proposal_stale(self):
        repository = Path(self.temporary.name) / "repository"
        (repository / "docs").mkdir(parents=True)
        shutil.copytree(self.root / "acr_runtime", repository / "acr_runtime")
        agent = DocumentationAgent(repository)
        proposal = agent.propose(self.proposals)
        execution = repository / "acr_runtime" / "execution.py"
        execution.write_text(
            execution.read_text(encoding="utf-8") + "\n# source drift\n",
            encoding="utf-8",
        )

        review = agent.review(
            proposal.candidate_dir, published_dir=self.published
        )
        self.assertFalse(review["fresh"])
        self.assertIsNone(review["review_hash"])

    def test_cli_propose_and_review_are_machine_readable(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "docs", "propose-reference", str(self.root),
                "--output", str(self.proposals),
            ]), 0)
        proposal = json.loads(output.getvalue())
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "docs", "review-reference", proposal["candidate_dir"],
                "--repository", str(self.root),
                "--published", str(self.published),
            ]), 0)
        review = json.loads(output.getvalue())
        self.assertTrue(review["fresh"])
        self.assertTrue(review["publishable"])


if __name__ == "__main__":
    unittest.main()
