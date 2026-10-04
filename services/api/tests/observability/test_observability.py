"""Request-id middleware, redaction defaults, lazy OTel degradation."""
from __future__ import annotations

import os
import unittest

from tests.support.storage_sandbox import StorageSandboxTestCase


class RequestIdTest(StorageSandboxTestCase):
    def _client(self):
        from app.main import create_app
        from fastapi.testclient import TestClient

        return TestClient(create_app())

    def test_client_supplied_id_is_sanitized_and_echoed(self) -> None:
        client = self._client()
        good = "req-client-abc12345"
        resp = client.get("/api/v1/health",
                          headers={"X-Request-ID": good})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("X-Request-ID"), good)

    def test_invalid_id_is_replaced(self) -> None:
        from app.observability.request_id import sanitize_request_id

        # Wire-safe but shape-invalid values go through the real stack…
        client = self._client()
        for bad in ("short", "has spaces in it", "x" * 81):
            resp = client.get("/api/v1/health", headers={"X-Request-ID": bad})
            echoed = resp.headers.get("X-Request-ID", "")
            self.assertNotEqual(echoed, bad)
            self.assertTrue(echoed.startswith("req_"), echoed)
        # …while header-unrepresentable junk is checked at the sanitizer
        # level (httpx refuses to even send non-ascii/control headers).
        for bad in ("", "é中文id" * 30, "inject\r\nSet-Cookie: x=1"):
            self.assertIsNone(sanitize_request_id(bad))

    def test_missing_id_is_generated(self) -> None:
        client = self._client()
        resp = client.get("/api/v1/health")
        self.assertTrue(resp.headers.get("X-Request-ID", "").startswith("req_"))

    def test_contextvar_resolves_inside_request(self) -> None:
        from app.observability.request_id import current_request_id

        seen: list[str] = []

        from fastapi import FastAPI

        probe = FastAPI()

        @probe.get("/probe")
        def probe_route() -> dict:
            seen.append(current_request_id())
            return {"rid": current_request_id()}

        from app.observability.request_id import RequestIdMiddleware
        probe.add_middleware(RequestIdMiddleware)
        from fastapi.testclient import TestClient

        client = TestClient(probe)
        resp = client.get("/probe", headers={"X-Request-ID": "req-probe-12345"})
        self.assertEqual(seen, ["req-probe-12345"])
        self.assertEqual(resp.json()["rid"], "req-probe-12345")
        self.assertEqual(resp.headers["X-Request-ID"], "req-probe-12345")
        # outside a request scope the contextvar is empty
        self.assertEqual(current_request_id(), "")


class RedactionTest(StorageSandboxTestCase):
    def test_secret_keys_collapse(self) -> None:
        from app.observability.redaction import redact

        payload = redact({
            "password": "hunter2longer",
            "Authorization": "Bearer abc.def.ghi",
            "refresh_token": "rt_opaque-secret-value",
            "nested": {"api_key": "sk-live-12345678", "keep": "visible"},
            "list": [{"token": "eyJhbgsig.payload.sig1234"}],
        })
        self.assertNotIn("hunter2longer", str(payload))
        self.assertNotIn("rt_opaque-secret-value", str(payload))
        self.assertNotIn("sk-live-12345678", str(payload))
        self.assertEqual(payload["nested"]["keep"], "visible")
        self.assertTrue(str(payload["password"]).startswith("<redacted"))

    def test_content_keys_log_size_only(self) -> None:
        from app.observability.redaction import redact

        payload = redact({"prompt": "请你帮我讲解二次方程",
                          "answer": "x=1"})
        self.assertNotIn("二次方程", str(payload))
        self.assertTrue(str(payload["prompt"]).startswith("<content>:"))

    def test_jwt_and_bearer_shapes_scrubbed_from_strings(self) -> None:
        from app.observability.redaction import redact

        jwtish = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c3JfMSJ9.c2lnbmF0dXJl"
        text = f"token={jwtish} more bearer {jwtish} trailing sk-abc12345678"
        out = redact({"detail": text})["detail"]
        self.assertNotIn(jwtish, out)
        self.assertNotIn("sk-abc12345678", out)


class TracingTest(StorageSandboxTestCase):
    def test_disabled_by_default_and_span_is_transparent(self) -> None:
        from app.observability import tracing

        tracing.reset_tracing_state()
        saved = os.environ.pop("OTEL_TRACES_ENABLED", None)
        try:
            self.assertFalse(tracing.tracing_enabled())
            ran = False
            with tracing.domain_span("rag.retrieve", {"k": 1}):
                ran = True
            self.assertTrue(ran)
        finally:
            if saved is not None:
                os.environ["OTEL_TRACES_ENABLED"] = saved

    def test_enabled_without_crashing_regardless_of_lane(self) -> None:
        from app.observability import tracing

        tracing.reset_tracing_state()
        os.environ["OTEL_TRACES_ENABLED"] = "1"
        try:
            from fastapi import FastAPI

            app = FastAPI()
            # Either the optional lane is installed (True) or the hook
            # degrades (False) — both are valid outcomes, never an error.
            self.assertIsInstance(tracing.init_tracing(app), bool)
            with tracing.domain_span("llm.call"):
                pass
        finally:
            tracing.shutdown_tracing()
            tracing.reset_tracing_state()
            os.environ.pop("OTEL_TRACES_ENABLED", None)


if __name__ == "__main__":
    unittest.main()
