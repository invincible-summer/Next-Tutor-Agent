"""Voice: push-to-talk WebSocket protocol."""
from __future__ import annotations
import io
import math
import json
import struct
import wave
import unittest
from array import array
from unittest.mock import patch
from tests.support.storage_sandbox import StorageSandboxTestCase
from app.voice.sentences import split_sentences, take_complete, take_speech_cuts
from app.voice.speak_text import to_speakable
from app.voice.wav import wav_to_pcm16
"""Voice layer regressions for browser text input and MeloTTS output."""
if __name__ == "__main__":
    unittest.main()
class TestVoiceWebSocket(StorageSandboxTestCase):
    """Browser text protocol with a canned run_turn and stub TTS."""

    def setUp(self) -> None:
        super().setUp()
        from app.core.config import settings
        from app.voice.tts import reset_tts_provider
        self._reset_tts = reset_tts_provider
        patcher = patch.object(settings, "voice_tts_provider", "stub")
        patcher.start()
        self._patches.append(patcher)
        # Keyless hermeticity: CI checkouts carry no root .env, so the real
        # _build_tools -> get_llm() raises OpenAI "Missing credentials" and
        # kills the turn task before the patched run_turn can stream. These
        # tests exercise the voice transport only; chat tools are never
        # consulted by the canned turns.
        tools_patcher = patch("app.api.v1.chat._build_tools",
                              lambda *args, **kwargs: [])
        tools_patcher.start()
        self._patches.append(tools_patcher)

        from app.main import create_app
        from fastapi.testclient import TestClient
        self.client = TestClient(create_app())
        from app.identity.store import create_user
        from app.identity.security import create_token
        self.voice_user = create_user("voice-fixture@test.local", "", "unused")
        self.auth_headers = {"Authorization": "Bearer " + create_token(self.voice_user.id)}

    def tearDown(self) -> None:
        self._reset_tts()
        super().tearDown()

    async def _canned_turn(self, user_message, session, tools, llm=None,
                           progress_cb=None, lang="zh", output_language=None,
                           attachments=None, student_id=""):
        yield {"type": "step", "step": "thinking"}
        yield {"type": "answer", "content": "勾股定理是对的。", "is_delta": True}
        yield {"type": "answer", "content": "两直角边的平方和等于斜边平方！", "is_delta": True}
        from app.core.session import save_session
        save_session(session)
        yield {"type": "done", "thinking": "", "answer": "…",
               "tool_calls": [], "trace_id": "trace_voice_test"}

    def _ticket(self) -> str:
        resp = self.client.post("/api/v1/voice/ticket", headers=self.auth_headers)
        self.assertEqual(resp.status_code, 200)
        return resp.json()["ticket"]

    def _receive_turn(self, ws, session_id):
        events = []
        while True:
            msg = ws.receive()
            if msg.get("bytes") is not None:
                events.append(("audio", len(msg["bytes"])))
                continue
            if msg.get("text") is None:
                continue
            event = json.loads(msg["text"])
            events.append(event["type"])
            if event["type"] == "error":
                self.fail(f"unexpected error event: {event}")
            if event["type"] == "turn_end":
                self.assertEqual(event["session_id"], session_id)
                self.assertTrue(event["tts_ok"])
                return events

    def _receive_until(self, ws, stop_type):
        """Collect raw events (dicts + audio tuples) until `stop_type` arrives."""
        events = []
        while True:
            msg = ws.receive()
            if msg.get("bytes") is not None:
                events.append(("audio", len(msg["bytes"])))
                continue
            if msg.get("text") is None:
                continue
            event = json.loads(msg["text"])
            self.assertNotEqual(event["type"], "error",
                                f"unexpected error event: {event}")
            events.append(event)
            if event["type"] == stop_type:
                return events

    def test_status_reports_browser_stt_and_tts(self):
        data = self.client.get("/api/v1/voice/status", headers=self.auth_headers).json()
        self.assertEqual(data, {"enabled": True, "stt": "browser", "tts": "stub"})

    def test_status_disabled_tts_still_reports_browser_stt(self):
        from app.core.config import settings
        from app.voice.tts import reset_tts_provider

        with patch.object(settings, "voice_tts_provider", "off"):
            reset_tts_provider()
            data = self.client.get("/api/v1/voice/status", headers=self.auth_headers).json()
        reset_tts_provider()
        self.assertEqual(data, {"enabled": False, "stt": "browser", "tts": None})

    def test_text_turn_without_pcm(self):
        with patch("app.agents.chat_agent.run_turn", self._canned_turn):
            with self.client.websocket_connect(
                    f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                ws.send_json({"type": "start", "session_id": None})
                bound = ws.receive_json()
                self.assertEqual(bound["type"], "session_bound")
                sid = bound["session_id"]
                ws.send_json({"type": "utterance_end", "text": "我是谁"})
                self.assertEqual(ws.receive_json(), {"type": "stt_start"})
                self.assertEqual(ws.receive_json(),
                                 {"type": "stt_result", "text": "我是谁"})
                events = self._receive_turn(ws, sid)

        self.assertIn("answer_delta", events)
        self.assertEqual(events.count("answer_delta"), 2)
        self.assertEqual(events.count("tts_start"), 2)
        self.assertEqual(events.count("tts_end"), 2)
        audio_frames = [event for event in events if isinstance(event, tuple)]
        self.assertEqual(len(audio_frames), 2)
        self.assertTrue(all(size > 0 for _tag, size in audio_frames))

        from app.core.session import load_session
        persisted = load_session(sid)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.student_id, self.voice_user.id)

    def test_pipeline_streams_text_ahead_of_slow_tts(self):
        """Synthesis must not stall the LLM stream: with slow clips every
        answer_delta is sent before the first tts_start, each tts_start +
        binary + tts_end trio stays contiguous, and turn_end follows the
        last audio frame."""
        import asyncio
        from app.voice.tts.stub import StubTTS

        original = StubTTS.synthesize

        async def slow_synthesize(provider, text, *, speed=None):
            await asyncio.sleep(0.15)
            return await original(provider, text, speed=speed)

        async def multi_sentence_turn(user_message, session, tools, llm=None,
                                      progress_cb=None, lang="zh",
                                      output_language=None, attachments=None,
                                      student_id=""):
            yield {"type": "step", "step": "thinking"}
            for i in range(6):
                yield {"type": "answer", "content": f"第{i}个要点讲解完毕。",
                       "is_delta": True}
            yield {"type": "done", "thinking": "", "answer": "…",
                   "tool_calls": [], "trace_id": "trace_voice_pipe"}

        with patch("app.agents.chat_agent.run_turn", multi_sentence_turn), \
                patch.object(StubTTS, "synthesize", slow_synthesize):
            with self.client.websocket_connect(
                    f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                ws.send_json({"type": "start", "session_id": None})
                ws.receive_json()["session_id"]
                ws.send_json({"type": "utterance_end", "text": "讲六个要点"})
                frames = []  # ("text", event) | ("audio", size)
                while True:
                    msg = ws.receive()
                    if msg.get("bytes") is not None:
                        frames.append(("audio", len(msg["bytes"])))
                        continue
                    if msg.get("text") is None:
                        continue
                    event = json.loads(msg["text"])
                    self.assertNotEqual(event["type"], "error")
                    frames.append(("text", event))
                    if event["type"] == "turn_end":
                        self.assertTrue(event["tts_ok"])
                        break

        def text_positions(etype):
            return [i for i, f in enumerate(frames)
                    if f[0] == "text" and f[1]["type"] == etype]

        deltas = text_positions("answer_delta")
        self.assertEqual(len(deltas), 6)
        # The core pipeline property: the generator was never parked behind
        # a synthesis await (the old serial loop interleaved them).
        first_tts = text_positions("tts_start")[0]
        self.assertLess(max(deltas), first_tts)

        seqs = []
        i = 0
        while i < len(frames):
            kind, payload = frames[i]
            if kind == "text" and payload["type"] == "tts_start":
                self.assertEqual(frames[i + 1][0], "audio",
                                 "binary frame must follow its tts_start")
                end = frames[i + 2]
                self.assertEqual(end[0], "text")
                self.assertEqual(end[1]["type"], "tts_end")
                self.assertEqual(end[1]["seq"], payload["seq"])
                seqs.append(payload["seq"])
                i += 3
                continue
            i += 1
        self.assertEqual(len(seqs), 6)
        self.assertEqual(len(set(seqs)), 6)
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(frames[-1][1]["type"], "turn_end")

    def test_trailing_remainder_without_terminator_is_spoken(self):
        async def trailing_turn(user_message, session, tools, llm=None,
                                progress_cb=None, lang="zh",
                                output_language=None, attachments=None,
                                student_id=""):
            yield {"type": "answer", "content": "最后一段没有句号",
                   "is_delta": True}
            yield {"type": "done", "thinking": "", "answer": "…",
                   "tool_calls": [], "trace_id": "trace_voice_tail"}

        with patch("app.agents.chat_agent.run_turn", trailing_turn):
            with self.client.websocket_connect(
                    f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                ws.send_json({"type": "start", "session_id": None})
                ws.receive_json()["session_id"]
                ws.send_json({"type": "utterance_end", "text": "收尾"})
                events = []
                while True:
                    msg = ws.receive()
                    if msg.get("bytes") is not None:
                        events.append(("audio", len(msg["bytes"])))
                        continue
                    event = json.loads(msg["text"])
                    self.assertNotEqual(event["type"], "error")
                    events.append(event)
                    if event["type"] == "turn_end":
                        break

        starts = [e for e in events if isinstance(e, dict)
                  and e["type"] == "tts_start"]
        audio = [e for e in events if isinstance(e, tuple)]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["text"], "最后一段没有句号")
        self.assertEqual(len(audio), 1)
        self.assertEqual(events[-1]["type"], "turn_end")

    def test_table_turn_speaks_cue_and_pins_board(self):
        """表格不逐格朗读：整块 markdown 走 board_table 事件，口播一句固定
        引导语，后续句子照常合成（流水线不停顿），answer_delta 原样透传。"""
        from app.voice.tts.stub import StubTTS

        table = ("| 物理量 | 单位 |\n|---|---|\n"
                 "| 力 | 牛 |\n| 功 | 焦 |\n")

        async def table_turn(user_message, session, tools, llm=None,
                             progress_cb=None, lang="zh", output_language=None,
                             attachments=None, student_id=""):
            yield {"type": "answer", "content": "对比表如下：\n",
                   "is_delta": True}
            yield {"type": "answer", "content": table, "is_delta": True}
            yield {"type": "answer", "content": "下一句继续讲解。",
                   "is_delta": True}
            yield {"type": "done", "thinking": "", "answer": "…",
                   "tool_calls": [], "trace_id": "trace_voice_table"}

        speeds = []
        original = StubTTS.synthesize

        async def recording_synthesize(provider, text, *, speed=None):
            speeds.append(speed)
            return await original(provider, text, speed=speed)

        with patch("app.agents.chat_agent.run_turn", table_turn), \
                patch.object(StubTTS, "synthesize", recording_synthesize), \
                patch("app.api.v1.voice._resolve_tts_speed", return_value=0.75):
            with self.client.websocket_connect(
                    f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                ws.send_json({"type": "start", "session_id": None})
                ws.receive_json()["session_id"]
                ws.send_json({"type": "utterance_end", "text": "讲对比表"})
                events = []
                while True:
                    msg = ws.receive()
                    if msg.get("bytes") is not None:
                        events.append(("audio", len(msg["bytes"])))
                        continue
                    event = json.loads(msg["text"])
                    self.assertNotEqual(event["type"], "error")
                    events.append(event)
                    if event["type"] == "turn_end":
                        self.assertTrue(event["tts_ok"])
                        break

        boards = [e for e in events if isinstance(e, dict)
                  and e["type"] == "board_table"]
        self.assertEqual(len(boards), 1)
        self.assertTrue(boards[0]["markdown"].startswith("| 物理量"))
        self.assertIn("| 力 | 牛 |", boards[0]["markdown"])
        self.assertEqual(boards[0]["hold_ms"], 7000)
        # 口播顺序：前导正文 → 引导语（表格被换掉）→ 表格后正文。
        starts = [e for e in events if isinstance(e, dict)
                  and e["type"] == "tts_start"]
        self.assertEqual([s["text"] for s in starts],
                         ["对比表如下：", "请看这个表格。", "下一句继续讲解。"])
        self.assertEqual(sum(1 for e in events if isinstance(e, tuple)), 3)
        self.assertEqual(events[-1]["type"], "turn_end")
        # 每用户语速逐片传给 provider。
        self.assertEqual(speeds, [0.75, 0.75, 0.75])

    def test_text_stream_never_parks_behind_stalled_synthesis(self):
        """合成队列绝不能把文字流和音频耦合：第一片合成挂起、12 句全部
        入队时，LLM 生成器仍被完整消费（旧的有界队列在第 8 句后就把
        生产者连同 answer_delta 一起冻结）。"""
        import asyncio
        import threading
        from app.voice.tts.stub import StubTTS

        original = StubTTS.synthesize
        release = threading.Event()
        generator_done = threading.Event()

        async def gated_synthesize(provider, text, *, speed=None):
            while not release.is_set():
                await asyncio.sleep(0.02)
            return await original(provider, text, speed=speed)

        async def twelve_sentence_turn(user_message, session, tools, llm=None,
                                       progress_cb=None, lang="zh",
                                       output_language=None, attachments=None,
                                       student_id=""):
            for i in range(12):
                yield {"type": "answer", "content": f"第{i}个要点讲解完毕。",
                       "is_delta": True}
            generator_done.set()
            yield {"type": "done", "thinking": "", "answer": "…",
                   "tool_calls": [], "trace_id": "trace_voice_unbounded"}

        try:
            with patch("app.agents.chat_agent.run_turn", twelve_sentence_turn), \
                    patch.object(StubTTS, "synthesize", gated_synthesize):
                with self.client.websocket_connect(
                        f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                    ws.send_json({"type": "start", "session_id": None})
                    ws.receive_json()["session_id"]
                    ws.send_json({"type": "utterance_end", "text": "讲十二个要点"})
                    # 合成仍挂起时，整个生成器必须已被消费完（5 秒远高于
                    # 逐 delta 循环成本；有界队列会在这里永远停车）。
                    self.assertTrue(generator_done.wait(timeout=5),
                                    "producer parked behind stalled synthesis")
                    release.set()
                    frames = []
                    while True:
                        msg = ws.receive()
                        if msg.get("bytes") is not None:
                            frames.append(("audio", len(msg["bytes"])))
                            continue
                        if msg.get("text") is None:
                            continue
                        event = json.loads(msg["text"])
                        self.assertNotEqual(event["type"], "error")
                        frames.append(("text", event))
                        if event["type"] == "turn_end":
                            self.assertTrue(event["tts_ok"])
                            break
        finally:
            release.set()

        deltas = [i for i, f in enumerate(frames)
                  if f[0] == "text" and f[1]["type"] == "answer_delta"]
        starts = [i for i, f in enumerate(frames)
                  if f[0] == "text" and f[1]["type"] == "tts_start"]
        self.assertEqual(len(deltas), 12)
        self.assertEqual(len(starts), 12)
        # 生成器在第一片合成完成前就跑完了：全部文字先于全部音频。
        self.assertLess(max(deltas), min(starts))
        self.assertEqual(sum(1 for f in frames if f[0] == "audio"), 12)
        self.assertEqual(frames[-1][1]["type"], "turn_end")

    def test_tts_failure_keeps_text_turn_alive(self):
        from app.voice.base import VoiceProviderError

        async def failing_synthesize(_provider, text, *, speed=None):
            raise VoiceProviderError("sidecar down", code="tts_unavailable")

        with patch("app.agents.chat_agent.run_turn", self._canned_turn), \
                patch("app.voice.tts.stub.StubTTS.synthesize", failing_synthesize):
            with self.client.websocket_connect(
                    f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                ws.send_json({"type": "start", "session_id": None})
                sid = ws.receive_json()["session_id"]
                ws.send_json({"type": "utterance_end", "text": "我是谁"})
                events = []
                tts_ok = None
                while True:
                    msg = ws.receive()
                    if msg.get("bytes") is not None:
                        self.fail("a failed TTS provider must not emit audio")
                    event = json.loads(msg["text"])
                    events.append(event)
                    if event["type"] == "turn_end":
                        tts_ok = event["tts_ok"]
                        self.assertEqual(event["session_id"], sid)
                        break

        self.assertTrue(any(e["type"] == "answer_delta" for e in events))
        self.assertEqual(sum(e["type"] == "tts_error" for e in events), 1)
        self.assertFalse(tts_ok)
        self.assertFalse(any(e["type"] == "error" for e in events))

    def test_binary_audio_is_rejected_without_stt_fallback(self):
        with self.client.websocket_connect(
                f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
            ws.send_json({"type": "start", "session_id": None})
            self.assertEqual(ws.receive_json()["type"], "session_bound")
            ws.send_bytes(b"not an accepted input frame")
            self.assertEqual(ws.receive_json(), {
                "type": "error", "code": "binary_audio_unsupported"})

    def test_empty_text_returns_empty_transcript(self):
        with self.client.websocket_connect(
                f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
            ws.send_json({"type": "start", "session_id": None})
            self.assertEqual(ws.receive_json()["type"], "session_bound")
            ws.send_json({"type": "utterance_end", "text": "  "})
            self.assertEqual(ws.receive_json(), {"type": "stt_start"})
            self.assertEqual(ws.receive_json(),
                             {"type": "error", "code": "empty_transcript"})

    def test_ticket_is_single_use(self):
        ticket = self._ticket()
        with self.client.websocket_connect(f"/api/v1/voice/ws?ticket={ticket}"):
            pass
        with self.assertRaises(Exception):
            with self.client.websocket_connect(f"/api/v1/voice/ws?ticket={ticket}"):
                pass

    def test_bad_ticket_rejected(self):
        with self.assertRaises(Exception):
            with self.client.websocket_connect("/api/v1/voice/ws?ticket=nope"):
                pass

    def test_header_auth_skips_ticket(self):
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password
        user = id_store.create_user("voice@test.local", "voice",
                                    hash_password("pw123456"))
        token = create_token(user.id)
        with self.client.websocket_connect(
                "/api/v1/voice/ws",
                headers={"Authorization": f"Bearer {token}"}) as ws:
            ws.send_json({"type": "ping"})
            self.assertEqual(ws.receive_json()["type"], "pong")

    def test_busy_rejects_overlapping_text_turn(self):
        async def pending_turn(*args, **kwargs):
            yield {"type": "answer", "content": "未完成"}
            await asyncio.sleep(0.2)

        import asyncio
        with patch("app.agents.chat_agent.run_turn", pending_turn):
            with self.client.websocket_connect(
                    f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                ws.send_json({"type": "start", "session_id": None})
                self.assertEqual(ws.receive_json()["type"], "session_bound")
                ws.send_json({"type": "utterance_end", "text": "第一句"})
                self.assertEqual(ws.receive_json()["type"], "stt_start")
                self.assertEqual(ws.receive_json()["type"], "stt_result")
                self.assertEqual(ws.receive_json()["type"], "answer_delta")
                ws.send_json({"type": "utterance_end", "text": "第二句"})
                self.assertEqual(ws.receive_json(), {"type": "error", "code": "busy"})

    def test_foreign_session_invisible(self):
        from app.core.session import TutorSession, save_session
        other = TutorSession(grade="")
        other.student_id = "usr_somebodyelse"
        other.session_id = "sess_foreign_voice"
        save_session(other)
        with self.client.websocket_connect(
                f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
            ws.send_json({"type": "start", "session_id": "sess_foreign_voice"})
            event = ws.receive_json()
            self.assertEqual(event["type"], "error")
            self.assertEqual(event["code"], "session_not_found")

    def test_end_control_closes(self):
        with self.client.websocket_connect(
                f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
            ws.send_json({"type": "start", "session_id": None})
            ws.receive()
            ws.send_json({"type": "end"})
            self.assertEqual(ws.receive_json()["type"], "bye")

    def test_tool_events_carry_card_payloads(self):
        """tool_result 必须完整透传：通话中的题目卡/知识检索卡依赖载荷，
        只有 thinking 允许留在服务端。"""
        knowledge_payload = {"status": "ok", "data": {
            "query": "勾股定理", "count": 1, "omitted_count": 0,
            "results": [{"file_id": "f1", "filename": "教材.pdf", "page": 3,
                         "excerpt": "勾股定理：a²+b²=c²"}]}}
        quiz_payload = {"status": "ok", "data": {"questions": [
            {"id": "q1", "type": "single", "stem": "1+1=?", "options": ["1", "2"]}]}}

        async def tool_turn(user_message, session, tools, llm=None,
                            progress_cb=None, lang="zh", output_language=None,
                            attachments=None, student_id=""):
            yield {"type": "tool_start", "name": "knowledge_search",
                   "args": {"query": "勾股定理"}}
            yield {"type": "tool_result", "result": knowledge_payload}
            yield {"type": "answer", "content": "查到了。", "is_delta": True}
            yield {"type": "tool_start", "name": "generate_quiz", "args": {}}
            yield {"type": "tool_result", "result": quiz_payload}
            yield {"type": "done", "thinking": "", "answer": "…",
                   "tool_calls": [], "trace_id": "trace_voice_cards"}

        with patch("app.agents.chat_agent.run_turn", tool_turn):
            with self.client.websocket_connect(
                    f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                ws.send_json({"type": "start", "session_id": None})
                ws.receive_json()
                ws.send_json({"type": "utterance_end", "text": "出题"})
                events = self._receive_until(ws, "turn_end")

        starts = [e["name"] for e in events if isinstance(e, dict)
                  and e["type"] == "tool_start"]
        self.assertEqual(starts, ["knowledge_search", "generate_quiz"])
        results = [e["result"] for e in events if isinstance(e, dict)
                   and e["type"] == "tool_result"]
        self.assertEqual(results, [knowledge_payload, quiz_payload])

    def test_retry_event_matches_sse_naming(self):
        """retry 状态帧与 SSE 命名对齐：前端只监听 type=="retry"。"""

        async def retry_turn(user_message, session, tools, llm=None,
                             progress_cb=None, lang="zh", output_language=None,
                             attachments=None, student_id=""):
            yield {"type": "retry", "attempt": 1, "reason": "timeout"}
            yield {"type": "answer", "content": "好了。", "is_delta": True}
            yield {"type": "done", "thinking": "", "answer": "…",
                   "tool_calls": [], "trace_id": "trace_voice_retry"}

        with patch("app.agents.chat_agent.run_turn", retry_turn):
            with self.client.websocket_connect(
                    f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                ws.send_json({"type": "start", "session_id": None})
                ws.receive_json()
                ws.send_json({"type": "utterance_end", "text": "重试"})
                events = self._receive_until(ws, "turn_end")

        self.assertEqual([e for e in events if isinstance(e, dict)
                          and e["type"] == "retry"],
                         [{"type": "retry", "attempt": 1}])
        self.assertFalse(any(isinstance(e, dict) and e["type"] == "status"
                             for e in events))

    def test_end_during_turn_finishes_text_and_closes(self):
        """轮次在途时挂断（end）：音频立即停（end 后入队的句子不再合成），
        文字流跑完并落盘，客户端依次收到剩余 answer_delta → turn_end →
        bye，随后连接以 1000 关闭——聊天不会悬空在流式态。"""
        import asyncio
        import threading
        from app.voice.tts.stub import StubTTS

        release = threading.Event()
        original = StubTTS.synthesize

        async def gated_synthesize(provider, text, *, speed=None):
            while not release.is_set():
                await asyncio.sleep(0.02)
            return await original(provider, text, speed=speed)

        async def gated_turn(user_message, session, tools, llm=None,
                             progress_cb=None, lang="zh", output_language=None,
                             attachments=None, student_id=""):
            yield {"type": "answer", "content": "第一句讲完了。", "is_delta": True}
            # 文字流先到、合成仍挂起：此时客户端挂断。
            while not release.is_set():
                await asyncio.sleep(0.02)
            yield {"type": "answer", "content": "后面还有。", "is_delta": True}
            from app.core.session import save_session
            session.messages.append({"role": "user", "content": user_message})
            session.messages.append({"role": "assistant",
                                     "content": "第一句讲完了。后面还有。"})
            save_session(session)
            yield {"type": "done", "thinking": "", "answer": "…",
                   "tool_calls": [], "trace_id": "trace_voice_drain"}

        try:
            with patch("app.agents.chat_agent.run_turn", gated_turn), \
                    patch.object(StubTTS, "synthesize", gated_synthesize):
                with self.client.websocket_connect(
                        f"/api/v1/voice/ws?ticket={self._ticket()}") as ws:
                    ws.send_json({"type": "start", "session_id": None})
                    sid = ws.receive_json()["session_id"]
                    ws.send_json({"type": "utterance_end", "text": "讲两句"})
                    frames = []
                    while True:
                        msg = ws.receive()
                        if msg.get("bytes") is not None:
                            frames.append(("audio", len(msg["bytes"])))
                            continue
                        if msg.get("text") is None:
                            continue
                        event = json.loads(msg["text"])
                        self.assertNotEqual(event["type"], "error")
                        frames.append(("text", event))
                        if event["type"] == "answer_delta":
                            # 第一个增量已落账（文字先于挂起的音频）：挂断。
                            ws.send_json({"type": "end"})
                            release.set()
                        if event["type"] == "bye":
                            break
                    # 挂断后服务端仍写完文字并优雅收线，最后关闭连接。
                    close = ws.receive()
                    self.assertEqual(close.get("type"), "websocket.close")
                    self.assertEqual(close.get("code"), 1000)
        finally:
            release.set()

        def texts(etype):
            return [f[1] for f in frames if f[0] == "text"
                    and f[1]["type"] == etype]
        def positions(etype):
            return [i for i, f in enumerate(frames)
                    if f[0] == "text" and f[1]["type"] == etype]

        self.assertEqual(len(texts("answer_delta")), 2)
        # 只有挂断前入队的第一句合成过；end 后入队的句子被排水丢弃。
        self.assertEqual([s["text"] for s in texts("tts_start")], ["第一句讲完了。"])
        self.assertEqual(sum(1 for f in frames if f[0] == "audio"), 1)
        self.assertLess(max(positions("answer_delta")), positions("bye")[-1])
        self.assertLess(positions("turn_end")[0], positions("bye")[0])
        # 轮次完整落盘：刷新/重载后文字与卡片都在。
        from app.core.session import load_session
        persisted = load_session(sid)
        self.assertIsNotNone(persisted)
        assistant = [m for m in persisted.messages if m.get("role") == "assistant"]
        self.assertTrue(assistant)
        self.assertEqual(assistant[-1]["content"], "第一句讲完了。后面还有。")
