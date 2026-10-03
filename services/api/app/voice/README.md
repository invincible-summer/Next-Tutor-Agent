# voice — 语音后端（切句 / 朗读清洗 / TTS provider）

语音通话的后端面：把 LLM 文字流切成可朗读子句并口语化公式，经统一 TTS service（Azure 云端 / MeloTTS 本地 sidecar / 回归 stub）合成下发；同一 service 供课堂与站内助手复用。

域设计（WS 协议帧序、切句规则、并发模型、配置表）见 [docs/architecture/voice.md](../../../../docs/architecture/voice.md)，本 README 只做导航。

## Owns

- 统一 TTS service：`tts/service.py` — provider 工厂（`off/stub/melo/azure/auto`）、云/Melo 共享并发信号量、Azure voices 缓存与音色 allowlist、`resolve_classroom_tts` 及课堂/助手共用的档案解析（返回不可变 profile）。
- Provider 实现：`tts/azure.py`（Azure Speech REST、SSML、音色校验）、`tts/melotts.py`（localhost HTTP 调 sidecar）、`tts/stub.py`（回归 stub）；契约在 `base.py`（`VoiceProviderError`、`TTSResult`）。
- 朗读文本处理：`sentences.py`（流式子句切分 `take_speech_cuts`：数学/表格不可切区、弱标点与硬上限）、`speak_text.py`（Markdown/LaTeX 清洗、`normalize_math_delimiters`、`_read_math` 分级口语化）。
- 音频后处理：`loudness.py`（响度归一）、`wav.py`（sidecar WAV 解码）。

## Does not own

- WS 端点与 ticket 换发（`/voice/status`、`/voice/ticket`、`/voice/ws`）→ `app/api/v1/voice.py`。
- MeloTTS sidecar 进程（合成本体、独立 venv）→ `services/voice/`。
- 聊天轮语义与会话持久化 → 复用 `run_turn`（`app/agents/`）。
- 课堂段级音频缓存与 run 记账 → `app/classroom/audio.py`、`runs.py`；站内助手语音入口 → `app/agents/site_assistant/voice.py`。
- 浏览器 STT 与通话 UI（状态机、板书、播放 FIFO）→ `apps/web/src/lib/voice/`、`components/chat/VoiceCallLayer.tsx`。

## Design

无服务器 STT 铁律：后端只接收 `utterance_end.text`，二进制上行一律 `binary_audio_unsupported`；单连接单轮、每轮至多一个在途 sidecar 请求、`seq` 严格递增；TTS fail-open——一次合成失败只发 `tts_error`，文字回答永不丢失。思考内容（CoT）不进语音链路。

## Tests

`services/api/tests/`：`test_voice.py`（约 90 用例：子句切分、朗读清洗与公式口语化、WAV 解码、响度归一、ticket/鉴权、WS 回归使用 stub TTS + canned `run_turn`）、`test_voice_azure.py`（Azure provider 与统一 TTS service：音色 allowlist、共享并发、档案解析）。浏览器冒烟 `apps/web/e2e/voice-smoke.spec.ts`。

## Key entry points

- `tts/service.py` — provider 工厂、共享并发与档案解析
- `sentences.py::take_speech_cuts` — 流式子句切分
- `speak_text.py` — 朗读清洗与公式口语化
- `base.py` — provider contract
