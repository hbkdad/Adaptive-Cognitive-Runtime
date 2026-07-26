from __future__ import annotations

import unittest

from acr_runtime.providers import (
    ChatMessage,
    ChatRequest,
    EmbeddingRequest,
    OllamaProvider,
)


class FakeOllamaTransport:
    def get_json(self, path):
        self.last_get = path
        return {
            "models": [
                {"name": "qwen-test:1b"},
                {"name": "nomic-embed-text:latest"},
            ]
        }

    def post_json(self, path, payload):
        self.last_post = (path, payload)
        if path == "/api/embed":
            return {
                "model": payload["model"],
                "embeddings": [[0.1, 0.2, 0.3]],
                "prompt_eval_count": 4,
                "total_duration": 1_000_000,
            }
        return {
            "model": payload["model"],
            "message": {"role": "assistant", "content": "local response"},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 12,
            "eval_count": 3,
            "total_duration": 2_000_000,
        }

    def post_stream(self, path, payload):
        yield {
            "model": payload["model"],
            "message": {"content": "local "},
            "done": False,
        }
        yield {
            "model": payload["model"],
            "message": {"content": "response"},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 12,
            "eval_count": 3,
        }


class OllamaProviderTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeOllamaTransport()
        self.provider = OllamaProvider(transport=self.transport)

    def test_remote_endpoint_requires_explicit_override(self):
        with self.assertRaises(ValueError):
            OllamaProvider("https://models.example.com")

    def test_lists_local_chat_and_embedding_capabilities(self):
        models = {item.model: item for item in self.provider.list_models()}
        self.assertTrue(models["qwen-test:1b"].capabilities.chat)
        self.assertFalse(models["qwen-test:1b"].capabilities.embeddings)
        self.assertFalse(models["nomic-embed-text:latest"].capabilities.chat)
        self.assertTrue(models["nomic-embed-text:latest"].capabilities.embeddings)

    def test_chat_uses_official_non_streaming_contract_and_actual_counts(self):
        response = self.provider.chat(
            ChatRequest(
                model="qwen-test:1b",
                messages=(ChatMessage(role="user", content="hello"),),
                max_output_tokens=20,
            )
        )
        path, payload = self.transport.last_post
        self.assertEqual(path, "/api/chat")
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["options"]["num_predict"], 20)
        self.assertEqual(response.content, "local response")
        self.assertEqual(response.usage.input_tokens, 12)
        self.assertEqual(response.usage.output_tokens, 3)
        self.assertFalse(response.usage.estimated)

    def test_streaming_and_embeddings_use_separate_contracts(self):
        chunks = list(
            self.provider.stream(
                ChatRequest(
                    model="qwen-test:1b",
                    messages=(ChatMessage(role="user", content="hello"),),
                )
            )
        )
        self.assertEqual("".join(chunk.content_delta for chunk in chunks), "local response")
        self.assertEqual(chunks[-1].usage.total_tokens, 15)

        embedding = self.provider.embed(
            EmbeddingRequest(
                model="nomic-embed-text:latest",
                inputs=("hello",),
            )
        )
        self.assertEqual(embedding.vectors, ((0.1, 0.2, 0.3),))
        self.assertEqual(embedding.usage.input_tokens, 4)


if __name__ == "__main__":
    unittest.main()

