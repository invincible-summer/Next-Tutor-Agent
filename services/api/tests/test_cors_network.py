"""Local deployment CORS and direct-network transport regressions."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch


class TestLocalCorsOrigins(unittest.TestCase):
    def test_default_origins_cover_all_start_sh_frontend_ports(self):
        from app.main import _cors_origins
        old = os.environ.pop("CORS_ORIGINS", None)
        try:
            origins = _cors_origins()
        finally:
            if old is not None:
                os.environ["CORS_ORIGINS"] = old
        for port in (3000, 3001, 3030):
            self.assertIn(f"http://localhost:{port}", origins)
            self.assertIn(f"http://127.0.0.1:{port}", origins)
            self.assertIn(f"http://0.0.0.0:{port}", origins)

    def test_explicit_origins_override_dev_defaults(self):
        from app.main import _cors_origins
        with patch.dict(os.environ, {
            "CORS_ORIGINS": "https://tutor.example.com,http://localhost:3999"
        }):
            self.assertEqual(_cors_origins(), [
                "https://tutor.example.com", "http://localhost:3999"
            ])


class TestDirectNetworkClients(unittest.TestCase):
    def test_main_llm_client_does_not_inherit_proxy_environment(self):
        from app.core import llm_async
        with patch.object(llm_async.httpx, "AsyncClient") as http_client, \
                patch.object(llm_async, "AsyncOpenAI"):
            llm_async.AsyncLLMClient(
                base_url="https://api.example.com/v1", api_key="test-key",
                model="test-model")
        self.assertFalse(http_client.call_args.kwargs["trust_env"])

    def test_embedding_client_does_not_inherit_proxy_environment(self):
        from app.core import embedding
        with patch.object(embedding.httpx, "AsyncClient") as http_client, \
                patch.object(embedding, "AsyncOpenAI"):
            embedding.EmbeddingClient(
                base_url="https://embedding.example.com/v1",
                api_key="test-key", model="test-embedding")
        self.assertFalse(http_client.call_args.kwargs["trust_env"])


if __name__ == "__main__":
    unittest.main()
