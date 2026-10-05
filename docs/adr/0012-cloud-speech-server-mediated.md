# ADR-0012: 云语音由服务端中转（server-mediated speech）

- 状态：accepted
- 日期：2026-10-04
- 上下文：云语音栈选型（Azure Speech 服务端中转；MeloTTS 保留本地可选）

## 背景

浏览器语音通话（`/api/v1/voice/ws`）的语音识别一直在客户端完成：
Web SpeechRecognition 产出最终文本后才进后端。移动端（Expo）没有等价的
系统级识别 API，且企业部署要求 Azure Speech 凭证只存在服务器 secret
manager（§14.4），移动包/EAS env 不得携带任何 Speech secret。

## 决策

1. **服务端 STT provider 抽象**：`app/voice/stt/{base,service,azure,stub}.py`
   镜像 TTS 结构。`SPEECH_STT_PROVIDER = off | stub | azure | iflytek |
   deepgram | auto`；`auto` 按已配置凭证选择，否则关闭——不伪可用。
   Azure、讯飞与 Deepgram STT 与 TTS 共享 cloud semaphore
   （`CLASSROOM_TTS_CLOUD_CONCURRENCY`）。
2. **新 REST 端点（§14.2）**：
   - `GET /api/v1/speech/capabilities`：只读投影，零网络请求、零凭证回显；
   - `POST /api/v1/speech/transcriptions`：multipart 一次 utterance，服务端
     校验格式白名单/大小/时长声明后调用 provider，返回
     `{text, language, duration_ms, provider_class}`；原始音频不落存储、
     不回传；
   - `POST /api/v1/speech/synthesis`：受控 text/language/voice/speed，音色
     仅来自管理员批准集合（复用 `tts/service.resolve_tts_profile`），输出
     WAV。
   三个端点均要求认证用户（转写/合成消耗云成本，游客不可用）。
3. **`GET /api/v1/capabilities` 聚合（§10.3）**：chat / upload / classroom /
   cloud_stt / cloud_tts / assistant / `illustration.quiz` /
   `illustration.scenario` / `illustration.v3` / `diagram.materials`，每项
   `{available, reason}`，reason 为稳定 code；判定复用各域现有函数，聚合
   层零复制。只读无状态，登录与游客均可读。
4. **移动端语音流（§14.3）**：录音 → `/speech/transcriptions` → transcript
   （发送前可取消/重录）→ 既有 chat SSE → 按句 `/speech/synthesis` →
   expo-audio 顺序播放；stop/cancel 立即停后续合成请求。首版不做移动端
   WS 语音通话（协议更简单且完全复用 chat）。
5. **兼容**：`/api/v1/voice/ws` 与 Web 端浏览器识别路径保留不动。
6. **`services/voice`（MeloTTS sidecar）定位（§14.5）**：self-hosted/local
   开发可选组件；移动端文档不要求安装它。仅在企业产品确认完全不提供
   本地部署时另立 ADR 再删除。

## 安全边界

- Azure 凭证只经服务器配置（`AZURE_SPEECH_KEY/REGION`，可选批准域
  endpoint）；401/403 归为配置错误不重试；429 遵守 Retry-After 单次重试；
  5xx/网络最多重试一次；`RecognitionStatus != Success` 或空文本绝不伪成功。
- 讯飞使用 `IFLYTEK_APP_ID/API_KEY/API_SECRET` 生成官方 WebSocket
  `host date request-line` HMAC-SHA256 鉴权 URL；Deepgram 使用
  `DEEPGRAM_API_KEY` 的 `Token` 头访问 `/v1/listen` 与 `/v1/speak`。
  两者均只允许固定官方域，密钥不进入移动端或能力响应。讯飞听写要求
  PCM 16 kHz/16-bit/mono；无凭证或协议错误均 fail closed。
- STT 错误码（`stt_unavailable/stt_config/stt_rate_limited/stt_transient`）
  继承 `VoiceProviderError` 家族语义，端点映射 415/400/429/503。
- 转写音频仅在内存中过一道校验，不写运行数据根（无新每用户存储根，
  无需 orphan_cleanup 登记）。

## 后果

- 移动端语音不依赖浏览器能力；同一契约可用于桌面 Web 的后续升级。
- 服务端成为 STT 成本与并发的唯一控制点（rate limit + 共享 semaphore）。
- 后端文档见 `docs/architecture/voice.md`；运维配置见
  `docs/development/enterprise-infra.md` 与 `.env.example`。
