from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from acr_runtime import AdaptiveRuntime, Settings
from acr_runtime.deployment_profile import (
    ZERO_CLOUD_UNAVAILABLE,
    deployment_policy,
    is_ollama_cloud_model,
)


class ZeroCloudProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def settings(self, **environment: str) -> Settings:
        values = {
            "ACR_DEPLOYMENT_PROFILE": "zero-cloud",
            "ACR_STATE_DIR": str(self.root / ".acr"),
            "ACR_SKILLS_DIR": str(self.root / ".acr" / "skills"),
            "ACR_OLLAMA_URL": "http://127.0.0.1:11434",
        }
        values.update(environment)
        return Settings.from_env(environ=values)

    def test_profile_is_closed_and_identifies_unavailable_features(self) -> None:
        policy = deployment_policy("zero-cloud")
        self.assertTrue(policy.zero_cloud)
        self.assertTrue(policy.sqlite_required)
        self.assertTrue(policy.filesystem_skills_required)
        self.assertEqual(policy.local_embeddings, "ollama_optional")
        self.assertEqual(policy.telemetry_destination, "sqlite_only")
        self.assertEqual(policy.external_network, "denied_except_loopback")
        self.assertFalse(policy.cloud_api_required)
        self.assertEqual(
            policy.unavailable_without_external_services,
            ZERO_CLOUD_UNAVAILABLE,
        )
        self.assertTrue(is_ollama_cloud_model("gpt-oss:120b-cloud"))
        self.assertTrue(is_ollama_cloud_model("glm-5:CLOUD"))
        self.assertFalse(is_ollama_cloud_model("qwen2.5-coder:7b"))

    def test_zero_cloud_rejects_cloud_providers_and_remote_ollama(self) -> None:
        with self.assertRaisesRegex(ValueError, "only no model provider or Ollama"):
            self.settings(ACR_PROVIDER="openai")
        with self.assertRaisesRegex(ValueError, "cloud model names"):
            self.settings(
                ACR_PROVIDER="ollama",
                ACR_OLLAMA_MODEL="glm-5:cloud",
            )

        for url in (
            "https://models.example.test",
            "http://127.0.0.1.evil.test:11434",
            "http://user@127.0.0.1:11434",
            "http://127.0.0.1:bad",
            "http://127.0.0.1:11434/api",
        ):
            with self.subTest(url=url), self.assertRaisesRegex(
                ValueError, "root loopback"
            ):
                self.settings(ACR_OLLAMA_URL=url)

    def test_none_or_local_ollama_are_valid_model_boundaries(self) -> None:
        deterministic = self.settings()
        self.assertIsNone(deterministic.provider)

        ollama = self.settings(
            ACR_PROVIDER="ollama",
            ACR_OLLAMA_URL="http://localhost:11434/",
        )
        self.assertEqual(ollama.provider, "ollama")
        self.assertEqual(ollama.ollama_url, "http://localhost:11434")

    def test_core_memory_skill_and_context_remain_functional(self) -> None:
        settings = self.settings()
        with AdaptiveRuntime(settings=settings) as runtime:
            memory_id = runtime.remember(
                "semantic",
                "The zero-cloud profile keeps memory in local SQLite.",
                scope="project:zero-cloud",
                confidence=0.99,
            )
            skill_id = runtime.register_skill(
                "local-sqlite-check",
                "Inspect the local SQLite schema.",
                description="Local database diagnostics",
                tags=["sqlite", "local"],
                trusted=True,
            )
            bundle = runtime.compile_context(
                "Inspect local SQLite memory",
                scope="project:zero-cloud",
                token_budget=160,
            )

        selected = {block.source_id for block in bundle.blocks}
        self.assertIn(memory_id, selected)
        self.assertIn(skill_id, selected)

    def test_public_configuration_proves_the_local_boundaries(self) -> None:
        summary = self.settings(ACR_PROVIDER="ollama").public_summary()
        deployment = summary["deployment"]
        self.assertEqual(deployment["name"], "zero-cloud")
        self.assertEqual(deployment["allowed_model_providers"], ["none", "ollama"])
        self.assertEqual(deployment["telemetry_destination"], "sqlite_only")
        self.assertFalse(deployment["cloud_api_required"])


if __name__ == "__main__":
    unittest.main()
