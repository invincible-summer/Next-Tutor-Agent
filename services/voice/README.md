# services/voice — MeloTTS 本地 TTS sidecar

承载本地 CPU 中文 TTS 合成的独立 FastAPI 进程：把 torch/MeloTTS 依赖完全隔离在主后端之外，主服务经 localhost HTTP（`app/voice/tts/melotts.py`）调用。

域设计（协议、启动判定、并发与许可边界）见 [docs/architecture/voice.md](../../docs/architecture/voice.md)，本 README 只做导航。

## Owns

- `app.py` — sidecar 入口：`GET /health` 健康检查、`POST /tts`（请求 `{"text","speed"}` 返回 44.1 kHz WAV）；`TTS(language="ZH", device="cpu")`。
- `melo_bootstrap.py` — 固定 revision 的 MeloTTS 引导：非中文 cleaner/BERT backend 用 fail-loud stubs（不装日/韩语言包），并按消息定向屏蔽固定依赖栈必发的两条 FutureWarning（`resume_download`、`weight_norm`），新弃用告警仍正常出现。
- `requirements.txt` — sidecar 独立依赖清单（CPU-only PyTorch + 中文 MeloTTS 直接运行依赖）。

## Does not own

- provider 抽象、工厂与共享并发 → `services/api/app/voice/tts/`（调用方）。
- 安装与启动编排 → [deploy/self-hosted/install_voice.sh](../../deploy/self-hosted/install_voice.sh)、[deploy/self-hosted/edu-voice-sidecar.service](../../deploy/self-hosted/edu-voice-sidecar.service)、`scripts/dev/start.sh`（启动判定、端口回退 8130–8132、90s 健康检查）。
- 电话 WS 协议、切句与朗读清洗 → `services/api/app/api/v1/voice.py`、`app/voice/`。

## Design

- 独立 venv：`deploy/self-hosted/install_voice.sh` 创建 `.venv/`；MeloTTS 源码经 `vendor/MeloTTS` 以 sys.path 挂载而非 `pip install`（其 setup.py 钩子会下载 1 GB 日文 unidic 字典）。
- 运行形态：`HF_HUB_OFFLINE=1` + `HF_HOME=models/hf`，模型缓存预取固定 revision 后离线 warmup，防 `main` 漂移。
- `vendor/`、`models/`、`.venv/` 均为部署期产物、gitignored（ADR-0001）；许可证与 SBOM 边界见 `docs/VOICE_LICENSES.md`。主服务本身不含 ML 依赖，sidecar 缺失时语音降级为文字路径，不阻塞主服务。

## Key entry points

- `app.py` — `uvicorn app:app --host 127.0.0.1 --port 8130`（由启动脚本拉起）
- `melo_bootstrap.py` — 模型引导与告警屏蔽
- 安装入口：仓库根执行 `bash deploy/self-hosted/install_voice.sh`
