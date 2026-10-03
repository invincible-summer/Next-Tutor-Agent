# voice — 电话式语音对话（浏览器 Speech Recognition + MeloTTS）

以「电话通话」形态在聊天页提供语音对话：push-to-talk 输入由浏览器原生 `SpeechRecognition` 识别，后端只接收最终文本并复用普通聊天轮；回答经子句切分与 TTS 流水线以 PCM16 顺序下发，同时驱动板书黑板。本模块同时拥有跨模块共享的统一 TTS service（课堂/站内助手复用）与 MeloTTS sidecar。

## Purpose / Scope（职责与边界）

- 输入：浏览器 STT 是唯一输入路径；后端不接收电话输入 PCM，不安装、不加载、不启动任何 STT 引擎；WebSocket 只接收 `utterance_end.text`，语音文本进入既有 `run_turn`，与普通聊天共用会话、记忆、RAG、工具和持久化。
- 输出：回答按子句级切片，MeloTTS sidecar 逐片合成 WAV、后端转 PCM16 下发；前端按采样率 FIFO 顺序播放并可随时停止播报；表格不逐格朗读，改为口播引导语 + 整块 markdown 上黑板（`board_table`）。
- 统一 TTS service：`voice/tts/service.py` 集中 provider 配置、能力、健康与跨电话/课堂的共享并发保护；课堂与站内助手的语音都经它解析音色与 provider（见 [classroom.md](./classroom.md)、[site-assistant.md](./site-assistant.md)）。
- MeloTTS sidecar：`services/voice/` 独立 FastAPI 进程承载本地 CPU 合成。
- 不负责：聊天轮语义（见 [conversation.md](./conversation.md)）；课堂段级音频缓存策略（属课堂模块）；浏览器 STT 本身的可用性（厂商平台/服务边界）。

## Owned code（拥有的代码路径）

后端主服务 `services/api/app/`：

| 路径 | 职责 |
|------|------|
| `api/v1/voice.py` | `GET /voice/status`、`POST /voice/ticket`（限流 30）、`/voice/ws` WebSocket 会话（一次凭证建连、`_run_turn` 合成流水线、`_resolve_tts_speed`） |
| `voice/base.py` | TTS provider contract、`VoiceProviderError`、`TTSResult` |
| `voice/tts/service.py` | 统一 TTS service：provider 工厂（off/stub/melo/azure/auto）、云/Melo 共享信号量、`resolve_classroom_tts` 与课堂/助手共用的通用档案解析、Azure voices 缓存与音色 allowlist |
| `voice/tts/stub.py` / `melotts.py` / `azure.py` | 回归 stub / localhost HTTP 调 MeloTTS sidecar / Azure Speech REST（SSML、音色校验） |
| `voice/sentences.py` | 流式子句切分 `take_speech_cuts`（数学/表格不可切区、弱标点与硬上限） |
| `voice/speak_text.py` | Markdown/LaTeX 朗读清洗（`normalize_math_delimiters`、`_read_math` 分级口语化） |
| `voice/loudness.py` / `wav.py` | 响度归一与 sidecar WAV 解码 |

Sidecar `services/voice/`：`app.py`（`GET /health`、`POST /tts`，请求 `{"text","speed"}` 返回 44.1 kHz WAV）、`melo_bootstrap.py`（固定 revision MeloTTS 引导：非中文 cleaner/BERT backend 用 fail-loud stubs，定向屏蔽固定依赖栈的两条 FutureWarning）、`requirements.txt`；`vendor/`、`models/`、`.venv/` 均为部署期产物、gitignored（ADR-0001）。

部署：`deploy/install_voice.sh`（CPU-only PyTorch、中文 MeloTTS 直接运行依赖、固定 revision 源码、模型缓存与一次中文 warmup）、`deploy/edu-voice-sidecar.service`、`scripts/dev/start.sh::start_voice_sidecar`（启动判定、端口回退 8130–8132、PID 与 90s 健康检查）。

前端 `apps/web/src/`：`lib/voice/useVoiceCall.ts`（通话状态机与 WS 客户端）、`lib/voice/browser-recognition.ts`（浏览器识别封装）、`components/chat/VoiceCallLayer.tsx`（通话 UI、板书黑板与播放 FIFO）。许可声明见 `docs/VOICE_LICENSES.md` 与 `THIRD-PARTY-NOTICES.md`。

## Public contracts（对外契约：API 端点/SSE/WS/数据结构）

- `GET /api/v1/voice/status`：固定 `stt="browser"`，并如实返回 TTS provider 状态。
- `POST /api/v1/voice/ticket`：登录后换取一次性 ticket（每 IP 30 次/分钟）；浏览器以单次 `?ticket=` 建立 WebSocket，非浏览器客户端可直接带 Authorization header；WebSocket 在 accept 前验证登录票据（语音须登录，无游客）。
- WS 消息协议（`/api/v1/voice/ws`）：

```text
C→S {"type":"start","session_id":string|null,"workspace_id":string|null,"lang":string}
S→C {"type":"session_bound","session_id":string}
C→S {"type":"utterance_end","text":string}
S→C {"type":"stt_start"}
S→C {"type":"stt_result","text":string}
S→C step / {"type":"tool_start","name":string} / {"type":"tool_result","result":object}
S→C {"type":"answer_delta","content":string} / {"type":"retry","attempt":number}
S→C {"type":"tts_start","seq":number,"text":string,"sample_rate":number}
S→C <binary PCM16> / {"type":"tts_end","seq":number}
S→C {"type":"board_table","markdown":string,"hold_ms":number}（表格不朗读时）
S→C {"type":"turn_end","session_id":string,"tts_ok":boolean}
C→S {"type":"end"}  S→C {"type":"bye"}
```

- 语义：同一连接只允许一轮并行执行——重复提交返回 `busy`，空文本返回 `empty_transcript`，二进制上行帧返回 `binary_audio_unsupported`（不缓存、不转码、不触发 STT），TTS 失败保留文字回答并发送 `tts_error`；`tool_start`/`tool_result` 完整透传（仅 name + 结果载荷），思考内容不通过电话协议输出；每次 send 写一个完整帧（uvicorn/websockets sans-io 单事件循环步），文字流与音频流相互独立、可能交错，但帧永不撕裂，`turn_end` 仍在最后一帧音频之后。
- `start.lang` 取 `zh-CN` 或 `en-US`，决定浏览器识别语言；电话 provider 的解析不按语言切换（`auto` 即本地 MeloTTS），按 locale 在本地/云之间择优的是课堂/助手的档案解析。
- `end` 的 drain 语义：轮次在途时服务端立即停止合成（`audio_off` 后入队句子只排水不合成），但 LLM 文字流跑到自然完成并照常落盘，客户端继续收 `answer_delta` 直到 `turn_end`，随后 `bye` 并以 1000 关闭；重复 `end` 幂等忽略。
- 呈现等价：通话中的消息流与文字轮一样渲染题目卡与知识检索命中来源卡（工具载荷完整透传）；LLM 通道瞬时故障经 `retry` 事件告知前端，与文字聊天的重试语义一致。
- 个人语速：`profile.prefs.tts_speed`（设置页 `/settings?section=voice` 滑杆 0.5–1.5，经 `PUT /user/profile` 浅合并落盘）；WS 建连时按身份解析并夹取到 sidecar 合法区间 0.5–2.0（非法值/游客回落实例默认 `VOICE_TTS_SPEED`），逐片 `synthesize(chunk, speed=…)` 覆盖，修改后下次拨号生效（每连接解析一次）。

## State & storage（状态与存储布局，含 runtime data 路径）

- 服务端无录音、无输入 PCM 缓冲、无 STT 状态；旧语音识别包与繁简转换数据已移除。ticket 为一次性内存凭证。
- 语音轮与普通聊天共用会话持久化（转写与回答写入现有 chat store，见 [conversation.md](./conversation.md)）。
- 个人设置 `prefs.tts_speed` 落在账号 profile；板书/通话 UI 状态只在浏览器内存。
- Sidecar 模型与源码缓存（`services/voice/vendor|models|.venv`）是部署产物，不属于用户运行数据，全部 gitignored。

## Main flows（关键流程）

### 一次通话轮（`api/v1/voice.py::_run_turn`）

1. 建连：`POST /voice/ticket` 换一次性 ticket → `?ticket=` 建立 WebSocket（accept 前验票）→ 客户端发 `start`（可携 `session_id`/`workspace_id`/`lang`，`session_id=null` 时绑定新会话）→ 服务端回 `session_bound`；会话所有权校验拒绝外来会话。
2. `utterance_end.text` 回显 `stt_start`/`stt_result`，进入既有 `run_turn`；步骤与工具事件实时透传。
2. 回答流经 `take_speech_cuts` 切成子句进入无界队列（刻意不设上界：队列只存句子文本、受回答 max_tokens 封顶，PCM 从不入队；有界队列曾把文字流与合成速率耦合造成整批冻结）；轮次循环持续消费生成器、`answer_delta` 实时下发。
3. 单个 worker 任务从队列取子句、合成并顺序发送 `tts_start` + 二进制 + `tts_end` 三帧；客户端播第 N 片时 worker 已在合成第 N+1 片。每片 WAV 解码与响度归一（数十万采样级）经 `asyncio.to_thread` 移出事件循环。跨帧不加发送锁（sans-io 栈帧级并发安全）；`turn_end` 顺序由 done 后入队 sentinel 并 join worker 保证；单 worker 保证每轮至多一个在途 sidecar 请求、`seq` 严格递增。
4. 失败策略 fail-open：一次合成失败发送 `tts_error`，本轮其余子句跳过合成仅保留文字，worker 继续排水清空队列。
5. 超过 240 字的朗读文本由 speak worker 按标点分块顺序合成：首块 `tts_start.text` 带原句供板书，后续块传空串不重复上板。

### 切句与公式朗读（`sentences.py` + `speak_text.py`）

- `normalize_math_delimiters` 先把 `\(...\)`/`\[...\]` 统一折成 `$`/`$$`（与前端 markdown 管线对齐；流式增量中「开符号已到、闭符号未到」同样折算，未闭合公式像未闭合 `$$` 一样锁住切句缓冲；`\\[2mm]` 行距命令有反斜杠守卫）。
- `_read_math` 分级口语化：数集与 SI 单位（`\text{m/s}` → 米每秒）、无花括号形式、`\frac`/`\sqrt`/`\boxed`/偏导数等带参结构（由内向外折叠）、区间/绝对值/ASCII 比较、正负号上下标、下标与底数连读（`W_{max}` → Wmax，不读「下标」）、结构化上下标（`\lim_`/`\sum_`/`\log_`）各自口语化、二元/一元负号区分；未知命令保留字母念英文。
- 切句规则：弱标点 ≥24 字即切、无标点硬上限 120 字；markdown 表格是不可切区（行首 `|` 进入表格模式，表内 `$` 不翻转数学配对，120 硬上限不适用）；`_force_split` 数学感知（不在 `$` 段内部下刀，硬上限 280 字兜住 sidecar 400 字限制）。规则以真实会话语料回归。
- 表格块：worker 先发 `{"type":"board_table","markdown":…,"hold_ms":7000}`，口播替换为固定引导语（zh「请看这个表格。」/en 对应句）走正常合成，随后继续消费队列；该判断在合成失败熔断之前——sidecar 挂了黑板照常显示表格；`answer_delta` 原样透传（聊天记录仍是完整表格）。

### 前端（`useVoiceCall.ts` + `VoiceCallLayer.tsx`）

- 浏览器不支持 `SpeechRecognition` / `webkitSpeechRecognition` 时显示明确错误，不启动任何服务器识别回退（输入侧不创建服务器输入音频采集链）。
- 状态机 `idle/connecting/ready/recording/recognizing/thinking/speaking/ended`；识别参数固定 `continuous=true`、`interimResults=true`、`maxAlternatives=1`，语言 `zh-CN` 或 `en-US`，只累积 `isFinal` 结果；浏览器提前结束连续识别时在按钮仍按住的情况下异步重启并保留已累积文本；松手、识别错误、权限拒绝、空文本、重复 `onend`、挂断和卸载均幂等处理。
- 挂断 drain：轮次在途时音频立即停、通话 UI 隐藏但 WS 存活，`answer_delta` 继续写完；`turn_end` 落定后拆线；超时（90s）/断网/服务端提前收线由 `onTurnAborted` 兜底提交半截回答并解除 `streaming`——任何路径下 `chat.streaming` 不悬空（旧的立即取消行为曾把输入框与电话按钮永久禁用）。
- 播放：AudioContext 播放下行 PCM；句子文本随每帧 PCM 一起进播放 FIFO（`pcmMetaRef` 与音频队列严格平行），`drainQueue` 出队、那一帧真正开始发声时才把所属句子暴露给黑板（流水线刻意提前合成若干句，直接上板会比声音早好几句）。
- 板书：只消费块状公式段（`displayMathSegments` 提取 `$$…$$`/`\[…\]` 完整段，行内公式不上板）；黑板严格左右居中（max-w 760px）、固定占 3/7 页面高度、三块等分黑板（`flex-1 min-h-0` + 板内各自滚动）；块式段从上往下写第一块空板，三块都满时擦掉写得最早的那块；`board_table` 到达时三块黑板作废、整版表格即时完整渲染（GFM、无书写动画），驻留 `hold_ms`（下限 7s）内句子上板被抑制、音频照常，窗口过后表格继续驻留直到新公式（`clearBoardTable`）或新表格（替换并重置窗口）需要板面；右上角挂纯装饰「手机模拟」（窄屏隐藏），通话结束后刷新会话。

### MeloTTS sidecar（`services/voice/`）

- 独立 FastAPI 进程，挂载固定 revision 的 `vendor/MeloTTS`，`TTS(language="ZH", device="cpu")`；`GET /health` 健康检查，`POST /tts` 返回 WAV(44.1 kHz)。
- `melo_bootstrap.py` 对未启用的非中文 backend 使用 fail-loud stubs（不安装日/韩语言包、不下载额外模型），并按消息定向屏蔽固定版本依赖栈必发的两条 FutureWarning（`resume_download`、`torch.nn.utils.weight_norm`）——非按类别一刀切，新弃用告警仍正常出现。
- 主服务本身不含 ML/STT 依赖；sidecar 由启动脚本拉起（见 Configuration），不阻塞主服务启动。

### 统一 TTS service（`voice/tts/service.py`）

- 电话 provider `off|stub|melo|azure|auto`：`auto` 按可用性在本地/云间择优；初始化失败失败关闭不崩溃。课堂与站内助手通过同一 service 解析 provider/音色（run 显式 > 个人偏好 > 实例默认），返回不可变 profile，绝不修改全局 factory 实例的音色。
- 跨电话与课堂的共享并发保护：云端全局并发 = `CLASSROOM_TTS_CLOUD_CONCURRENCY`（默认 2），Melo 全局并发 = 1（单模型无并发保护）；并发原语按事件循环缓存（单 worker uvicorn 单循环；测试的每次 `asyncio.run` 新循环会失效重建）。
- Azure voices list 缓存（TTL 1h）由显式刷新填充（lifespan 后台任务/部署脚本），能力读取永不发网络请求；管理员配置的 `CLASSROOM_TTS_VOICE_ZH/EN` 是唯一批准音色集合。

## Dependencies（依赖与被依赖）

- 依赖：聊天内核 `run_turn`（会话/记忆/RAG/工具/持久化全部复用）；身份层（ticket/Authorization、WS accept 前验票，语音须登录）；MeloTTS sidecar（localhost HTTP）；Azure Speech（可选云通道）；`start.sh` 启动判定与端口回退。
- 被依赖：课堂音频（`classroom/audio.py` 本地回退与自动策略的本地轨）、站内助手面板语音（`agents/site_assistant/voice.py`）都消费统一 TTS service 的 provider/并发/音色约束。
- 部署依赖：nginx 为 `/api/v1/voice/` 透传 WebSocket Upgrade（SSE/WS 反代 `proxy_buffering off`，见 [backend-runtime.md](./backend-runtime.md)）。
- 资源边界：浏览器 STT 不占用服务器识别模型内存，也不需要服务器录音缓存；部署侧仅需 MeloTTS sidecar 的 CPU venv 与 Hugging Face 模型缓存。

## Invariants / security boundaries（不变量与安全边界）

- **无服务器 STT 铁律**：后端不接收输入 PCM、不装任何识别引擎；二进制上行一律 `binary_audio_unsupported`。浏览器 STT 是厂商平台/服务边界，不是本项目 MIT 发行物——不能承诺永久免费或无条件商用。
- 单连接单轮（`busy`）；每轮至多一个在途 sidecar 请求（单 worker + Melo 全局信号量 1）；`seq` 严格递增；帧级完整、`turn_end` 在最后一帧音频之后。
- TTS fail-open：sidecar 失败保留文字回答并发 `tts_error`，绝不阻塞或丢失回答。
- 内存边界：合成队列刻意无界但只存句子文本（受回答 max_tokens 封顶，量级几 KB），PCM 音频从不入队；每片 WAV 后处理经 `asyncio.to_thread` 移出事件循环，不阻塞其他用户的流。
- 原始 CoT 不进语音链路（thinking 分支显式丢弃）；工具事件只透传 name 与结果载荷。
- 任何断线/超时/提前收线路径都不允许 `chat.streaming` 悬空（兜底提交半截回答）。
- 隐私/许可边界：浏览器识别可能调用厂商在线服务，商业/隐私/地域条款由浏览器厂商决定（`docs/VOICE_LICENSES.md`）；仓库只提交 sidecar 集成代码、固定依赖与许可证声明；变更浏览器目标、MeloTTS revision、模型、语言、sidecar 依赖或发布形态时必须重做服务条款/许可证审计并发布 SBOM（ADR-0001）。

## Configuration（环境变量与开关）

| 变量 | 默认 | 说明 |
|------|------|------|
| `VOICE_TTS_PROVIDER` | `off` | 电话 TTS provider：`off/stub/melo/azure/auto`；`melo`/`auto` 触发启动脚本拉起 sidecar |
| `VOICE_TTS_BASE_URL` | `http://127.0.0.1:8130` | MeloTTS sidecar 地址（localhost；启动脚本端口回退 8130–8132 时自动指向选中端口） |
| `VOICE_TTS_SPEED` | `0.9` | 实例默认语速（略慢于原速）；个人 `prefs.tts_speed` 0.5–1.5 覆盖，夹取区间 0.5–2.0 |
| `AZURE_SPEECH_KEY`/`AZURE_SPEECH_REGION`/`AZURE_SPEECH_ENDPOINT` | — | 电话/课堂云端 TTS（`azure`/`auto`） |
| `CLASSROOM_TTS_CLOUD_CONCURRENCY` | `2` | 共享云端合成并发（电话与课堂共用） |

Sidecar 启动判定（`scripts/dev/start.sh`，任一成立即启动）：`VOICE_TTS_PROVIDER=melo|auto`；或 `CLASSROOM_ENABLED=1` 且 `CLASSROOM_LOCAL_TTS_ENABLED=1` 且课堂策略为 local/auto（cloud 时须 `CLASSROOM_TTS_LOCAL_FALLBACK=1`）。venv 缺失时 fail-open：只提示 `bash deploy/install_voice.sh`，语音降级为文字路径，绝不临时安装大型模型。

## Observability（trace/日志/指标）

- `GET /voice/status` 如实返回 `stt=browser` 与 TTS provider 状态；前端据此展示电话入口。
- Sidecar `GET /health`；启动脚本后台 90s 健康确认（首次加载模型 20–60s 属正常），未就绪只提示不回滚，并把 sidecar PID/端口写入运行时文件（`/tmp/edu_voice_pid`、`/tmp/edu_voice_port`）供停止与健康检查复用。
- 合成失败经 `tts_error` 事件到达前端（本轮降级文字）；主服务日志记录 provider 初始化失败（失败关闭）。
- 安装脚本预取已审计 revision 后以 HF/Transformers offline 模式执行 warmup，避免模型 `main` 漂移；部署包若携带下载结果仍须保留模型卡、LICENSE/NOTICE 与实际 SBOM。

## Tests / acceptance（测试索引）

- `services/api/tests/test_voice.py`（90 用例）：子句切分（弱标点/硬上限/数学与表格不可切区/流式余量）、朗读清洗与公式口语化、TTS WAV 解码、响度归一、ticket 与鉴权、会话所有权、会话持久化与 TTS fail-open；WebSocket 回归使用 stub TTS + canned `run_turn`（所有走 turn 的测试必须 patch `get_llm`/`_build_tools`），覆盖 `status` 固定 `stt=browser`、无 PCM 的 `utterance_end.text` 全链路、`empty_transcript`、`binary_audio_unsupported`、`busy`、坏 ticket / header 直连 / 外来会话 / `end` 语义。
- `services/api/tests/test_voice_azure.py`：Azure provider 与统一 TTS service（音色 allowlist、共享并发、档案解析）回归。
- 浏览器：`apps/web/e2e/voice-smoke.spec.ts`（通话 UI、板书黑板与 drain 收尾冒烟）。

## Related ADRs

- ADR-0001 source-only 仓库（vendor/模型缓存/venv 不入库，许可证与 SBOM 边界）
- ADR-0004 single-worker（合成流水线为单事件循环 asyncio 任务；共享并发原语按事件循环缓存）
