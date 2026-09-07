"""OpenAI 兼容门面（/models + /chat/completions）契约测试。

钉住的契约（清小搭广场接入规范）：
- 未配置 COMPAT_API_KEY → 503（门面关闭）；凭证错误 → 401
- 探测快道（max_tokens<=2）不启动 Agent，直接返回合法 OpenAI 结构
- 流式帧序：role 帧 → content/reasoning 帧 → stop 帧(带 usage) → [DONE]
- finish_reason 只用白名单值；usage 三字段齐全
"""
import json
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.api.v1 import compat as compat_mod  # noqa: E402

_KEY = "test-compat-key-0123456789"
_AUTH = {"Authorization": f"Bearer {_KEY}"}


async def _fake_run(user_message, session, tools, **kwargs):
    yield {"type": "thinking", "content": "想一下", "is_delta": True,
           "stage": "reasoning"}
    yield {"type": "answer", "content": "你好", "is_delta": True}
    yield {"type": "answer", "content": "同学", "is_delta": True}
    yield {"type": "done",
           "trace_summary": {"prompt_tokens": 10, "completion_tokens": 4,
                             "total_tokens": 14}}


def _sse_frames(raw: str) -> list[str]:
    return [blk.removeprefix("data: ").strip()
            for blk in raw.strip().split("\n\n") if blk.strip()]


class TestCompatFacade(unittest.TestCase):
    def setUp(self):
        self._p = patch.object(compat_mod.settings, "compat_api_key", _KEY)
        self._p.start()
        self.client = TestClient(create_app())

    def tearDown(self):
        self._p.stop()

    def test_models_ok_and_auth(self):
        r = self.client.get("/api/v1/models", headers=_AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["object"], "list")
        self.assertEqual(self.client.get("/api/v1/models").status_code, 401)
        self.assertEqual(
            self.client.get("/api/v1/models",
                            headers={"Authorization": "Bearer wrong"}).status_code,
            401)
        # x-api-key 头同样接受
        r2 = self.client.get("/api/v1/models", headers={"x-api-key": _KEY})
        self.assertEqual(r2.status_code, 200)

    def test_probe_fast_path_non_stream(self):
        r = self.client.post("/api/v1/chat/completions", headers=_AUTH, json={
            "messages": [{"role": "user", "content": "你好"}],
            "stream": False, "max_tokens": 1,
        })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["object"], "chat.completion")
        self.assertTrue(body["choices"][0]["message"]["content"])
        self.assertIn(body["choices"][0]["finish_reason"],
                      ("stop", "length", "tool_calls", "content_filter"))
        self.assertIn("usage", body)

    def test_probe_fast_path_stream_frame_order(self):
        with self.client.stream(
                "POST", "/api/v1/chat/completions", headers=_AUTH, json={
                    "messages": [{"role": "user", "content": "你好"}],
                    "stream": True, "max_tokens": 1,
                }) as r:
            self.assertEqual(r.status_code, 200)
            raw = "".join(r.iter_text())
        frames = _sse_frames(raw)
        self.assertEqual(frames[-1], "[DONE]")
        first = json.loads(frames[0])
        self.assertEqual(first["choices"][0]["delta"], {"role": "assistant"})
        stop = json.loads(frames[-2])
        self.assertEqual(stop["choices"][0]["finish_reason"], "stop")
        self.assertIn("usage", stop)

    def test_full_agent_non_stream(self):
        with patch("app.agents.supervisor.run", _fake_run), \
             patch("app.api.v1.chat._build_tools", lambda session: []), \
             patch("app.core.session.load_session", lambda sid: None):
            r = self.client.post("/api/v1/chat/completions", headers=_AUTH, json={
                "messages": [{"role": "user", "content": "讲一下浮力"}],
            })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["choices"][0]["message"]["content"], "你好同学")
        self.assertEqual(body["choices"][0]["finish_reason"], "stop")
        self.assertEqual(body["usage"]["total_tokens"], 14)

    def test_full_agent_stream_reasoning_and_done(self):
        with patch("app.agents.supervisor.run", _fake_run), \
             patch("app.api.v1.chat._build_tools", lambda session: []), \
             patch("app.core.session.load_session", lambda sid: None):
            with self.client.stream(
                    "POST", "/api/v1/chat/completions", headers=_AUTH, json={
                        "messages": [{"role": "user", "content": "讲一下浮力"}],
                        "stream": True,
                    }) as r:
                self.assertEqual(r.status_code, 200)
                raw = "".join(r.iter_text())
        frames = _sse_frames(raw)
        self.assertEqual(frames[-1], "[DONE]")
        deltas = [json.loads(f)["choices"][0]["delta"] for f in frames[:-1]]
        kinds = [next(iter(d)) if d else "empty" for d in deltas]
        # role 首帧恰好一次；reasoning 在 content 之前；stop 帧收尾
        self.assertEqual(kinds[0], "role")
        self.assertIn("reasoning", kinds)
        self.assertLess(kinds.index("reasoning"), kinds.index("content"))
        self.assertEqual(kinds[-1], "empty")
        contents = [d.get("content", "") for d in deltas]
        self.assertEqual("".join(contents), "你好同学")
        stop = json.loads(frames[-2])
        self.assertEqual(stop["usage"]["total_tokens"], 14)

    def test_empty_messages_400(self):
        r = self.client.post("/api/v1/chat/completions", headers=_AUTH, json={
            "messages": [{"role": "system", "content": "仅系统消息"}],
        })
        self.assertEqual(r.status_code, 400)


class TestCompatDisabled(unittest.TestCase):
    def test_503_when_key_unset(self):
        with patch.object(compat_mod.settings, "compat_api_key", ""):
            client = TestClient(create_app())
            self.assertEqual(client.get("/api/v1/models").status_code, 503)


if __name__ == "__main__":
    unittest.main()
