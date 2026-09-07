# 语音依赖、模型与商业使用边界

**核查日期：2026-08-30。** 本文件记录当前代码和固定版本的工程审计结果，不是
法律意见，也不替代发布主体对目标地区、浏览器服务、模型数据和最终制品的法律审查。

## 1. 当前链路

```text
浏览器 SpeechRecognition / webkitSpeechRecognition
    → 最终文本（WebSocket JSON；不上行电话 PCM）
    → Edu_Agent 会话、LLM、工具和持久化
    → 可选 MeloTTS sidecar
    → 浏览器播放下行 TTS PCM
```

- **唯一 STT** 是浏览器平台 API。仓库没有服务器端 STT provider、Whisper 模型或
  PCM→STT 回退；二进制上行会被拒绝。
- **TTS** 默认关闭；`VOICE_TTS_PROVIDER=melo` 时使用本地 MeloTTS 中文/中英混排
  sidecar。`stub` 只用于测试。
- WebSocket 不把电话输入 PCM 发给 Edu_Agent 后端；这不代表音频一定留在设备上。
  目标浏览器可能使用厂商在线服务处理音频。

## 2. “开源、免费、可商用”应如何表述

`SpeechRecognition` / `webkitSpeechRecognition` 是 Web 平台接口，不是本项目下载、
链接或再分发的软件包，因此没有可由本项目转授的“MIT SpeechRecognition 许可”。
调用接口本身也不等于取得浏览器或其远程识别服务的免费商业授权。兼容性、是否联网、
收费、地域/账号限制、音频处理、保留和跨境规则由实际浏览器、版本、组织策略和厂商
条款决定。本项目只承诺自己的协议边界，不宣称 Chrome、Edge 或其他识别服务永久
免费、离线或无条件适合商用。核查入口：

- [MDN SpeechRecognition](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition)
  与 [Web Speech API draft](https://wicg.github.io/speech-api/#speechreco-section)；
- [Google Terms](https://policies.google.com/terms) / [Google Privacy](https://policies.google.com/privacy)；
- [Microsoft Services Agreement](https://www.microsoft.com/en-us/servicesagreement) /
  [Microsoft Privacy Statement](https://www.microsoft.com/en-us/privacy/privacystatement)。

MeloTTS 等开源代码的宽松许可证通常允许商业使用，但前提是履行版权、许可证、NOTICE
等条件。模型权重、训练数据、Python wheel、native library、语料和商标是独立边界。
“部署时下载”只是不由本 Git 仓库直接携带模型，**不会自动免除这些义务**。

## 3. 仓库中有什么，部署时下载什么

仓库提交：

- Edu_Agent 的浏览器/后端集成源码；
- `backend/voice_sidecar/` 包装代码和固定依赖清单；
- `deploy/install_voice.sh`；
- 本文件、第三方声明和必要许可证全文。

部署脚本下载到 `.gitignore` 排除目录：

| 本地目录 | 内容 | 是否应提交 |
|---|---|---|
| `backend/vendor/MeloTTS/` | 固定 commit 的上游 MeloTTS 源码 checkout | 否 |
| `backend/models/voice/hf/` | MeloTTS-Chinese、BERT 权重/tokenizer 和 HF 缓存 | 否 |
| `backend/voice_sidecar/.venv/` | Python wheels、native libraries、NLTK 数据 | 否 |

因此建议继续采用“仓库只写依赖和归属、部署时下载”的方式。若发布 Docker 镜像、离线
安装包、虚拟机镜像或预装服务器，这些文件仍进入发布物，发布方仍是在再分发它们，
必须把相应许可证、模型卡、NOTICE 和 SBOM 一起处理。

## 4. 固定源码与模型

| 组件 | 用途 | 固定 revision | 核查结果与义务 |
|---|---|---|---|
| [MeloTTS](https://github.com/myshell-ai/MeloTTS) | TTS 推理源码 | `209145371cff8fc3bd60d7be902ea69cbdb7965a` | 上游 LICENSE 为 MIT；允许使用、修改和销售副本，但分发源码/实质部分时保留版权与许可全文。仓库保留 `licenses/melotts-MIT.txt` |
| [MeloTTS-Chinese](https://huggingface.co/myshell-ai/MeloTTS-Chinese) | `checkpoint.pth` 与配置 | `af5d207a364ea4208c6f589c89f57f88414bdd16` | 该 revision 的 HF metadata/model card 标示 `license: mit`；分发权重时保留模型卡、README、许可、revision/hash。该标签不自动证明训练数据、第三方内容或商标权利 |
| [`bert-base-multilingual-uncased`](https://huggingface.co/google-bert/bert-base-multilingual-uncased) | 中文/英文特征模型和 tokenizer | `7cbf9a625e29989f6b9c6c2fa68234c304f7e38f` | 模型卡标示 Apache-2.0；保留 Apache 条款、版权、模型卡和 NOTICE（如有） |
| [`bert-base-uncased`](https://huggingface.co/google-bert/bert-base-uncased) | 嵌入英文所需 tokenizer | `86b5e0934494bd15c9632b12f734a8a67f723594` | 模型卡标示 Apache-2.0；当前只下载 tokenizer 文件，仍保留来源、模型卡和许可 |

安装脚本先按上述 revision 下载，再把 HF/Transformers 切到 offline 模式执行 warmup，
以避免 warmup 漂移到更新的 `main`。修改任何 revision、语言或模型都需要重新审计。

## 5. Sidecar 直接依赖

版本以 `backend/voice_sidecar/requirements.txt` 为准。下表是直接依赖的上游许可
类别，不是传递依赖或 wheel 内 native code 的完整 SBOM。

| 直接依赖 | 当前作用 | 上游许可/发布边界 |
|---|---|---|
| `fastapi==0.141.1`, `pydantic==2.13.5` | HTTP 与请求校验 | MIT |
| `uvicorn==0.52.4` | ASGI server | BSD-3-Clause |
| `torch==2.11.0`, `torchaudio==2.11.0` | CPU 推理与音频 import/runtime | PyTorch wheel 是 Apache/BSD/BSL/MIT 等组合归属；torchaudio 为 BSD-2-Clause并含第三方材料。保留实际 wheel 全部 notices，不能标成单一 MIT |
| `transformers==4.27.4`, `huggingface_hub==0.36.2` | 模型/tokenizer 加载与缓存 | Apache-2.0 |
| `numpy==1.26.4`, `scipy==1.17.1`, `soundfile==0.14.0` | 数组、WAV、数值处理 | BSD-3-Clause；binary wheels 可能带额外 native notices。`soundfile`/`libsndfile` 发布边界可能包含 LGPL-2.1 |
| `librosa==0.11.0` | mel/spectrogram | ISC |
| `tqdm==4.70.0` | 推理进度包装 | MPL-2.0 AND MIT；保留实际发行包许可 |
| `jieba==0.42.1`, `pypinyin==0.55.0`, `cn2an==0.5.24` | 中文分词、拼音、数字规范化 | MIT |
| `g2p_en==2.1.0`, `nltk==3.10.3` | 中英混排 G2P、词性与字典读取 | Apache-2.0；同时处理下述 NLTK data 许可 |

传递依赖会包含 `tokenizers`、`requests`、`filelock`、`inflect`、`python-crfsuite`、
Numba/LLVM、OpenBLAS 等，具体闭包由目标 Python/OS/wheel 决定。发布镜像或 venv 前，
必须从实际环境收集 `*.dist-info/licenses`、`LICENSE`、`COPYING`、`NOTICE` 和 native
library notices，并生成锁定版本的 SBOM；本表不能替代该工作。

### NLTK 数据

安装脚本下载：

- `averaged_perceptron_tagger` 与 `averaged_perceptron_tagger_eng`：NLTK data index
  标示 MIT；
- `cmudict`：NLTK data index 记录“research or commercial purpose unrestricted”，并
  请求注明 Carnegie Mellon University 来源。发布方应保留该数据随附 README/归属，
  不用 NLTK Python 包的 Apache-2.0 去替代语料自身说明。

### 明确排除的上游可选路径

当前中文 sidecar 不安装或执行日/韩/法/西等上游路径。`melo_bootstrap.py` 用
fail-loud stubs 阻止这些路径（也屏蔽当前 ZH 路径不用的 `cached_path` S3 下载分支）；
安装脚本每次以 `venv --clear` 重建环境，避免旧的全语言 MeloTTS 环境残留
`MeCab`/unidic、`pykakasi`、`fugashi`、`gruut*`、`g2pkk`、`sentencepiece` 等包。
启用新语言或直接 `pip install` 上游 MeloTTS 时必须重新做代码、词典、语料和模型审计。

## 6. Git 权重审计

2026-08-30 使用以下范围核查：当前 index/HEAD、`git log --all`、
`git rev-list --objects --all`、大 blob、未被 refs/reflog 引用且不小于 1 MiB 的 blob、
Git LFS，以及 GitHub API
返回的远端 `main` 完整 tree。结果：

- 当前 Git 跟踪文件和所有本地可达 refs 中未发现
  `.bin/.pt/.pth/.safetensors/.onnx/.ckpt/.gguf/.tflite` 模型文件；
- GitHub 远端 `main` 中也未发现上述模型文件；
- 远端 `.npz` 仅为 `knowledge/public_vector_artifacts/shard-*.npz` 公开教材检索向量，
  不是语音、LLM、TTS 或 STT 权重；
- 历史提交曾有 Whisper/whisper.cpp 集成源码及许可文本，但未发现 Whisper 模型参数；
- 本机存在部署时下载的 `backend/models/`、MeloTTS checkout 和 sidecar venv；它们
  均被 `.gitignore` 命中，不是 Git 内容（缓存大小会随安装时间和平台变化）。

这是对当前可访问仓库、refs 和远端主分支的可复现审计，不是对已删除的未知远端
fork、备份或外部制品的绝对证明。以后若发现权重进入已推送历史，单纯添加
`.gitignore` 不会删除历史对象，必须另行评估 `git filter-repo` 和凭据/制品处置。

核心复核命令：

```bash
git ls-files | rg -i '\.(bin|pt|pth|safetensors|onnx|ckpt|gguf|tflite)$'
git rev-list --objects --all | rg -i '\.(bin|pt|pth|safetensors|onnx|ckpt|gguf|tflite)$'
git lfs ls-files
git check-ignore -v backend/models/voice/example.safetensors \
  backend/vendor/MeloTTS backend/voice_sidecar/.venv
```

## 7. 发布清单

1. 不提交 `.env`、`backend/vendor/`、`backend/models/`、sidecar venv 或模型缓存。
2. 分发 MeloTTS 源码时保留 `melotts-MIT.txt`；分发模型时保留模型卡、README、
   许可、固定 revision 和校验信息。
3. 对实际 Python/wheel/native/NLTK/HF 制品生成 SBOM，并保留所有 notice。
4. 对浏览器识别服务单独完成用户告知、麦克风权限、隐私、数据跨境、厂商服务及
   商业条款审查；不将其描述为本项目提供的 MIT 或永久免费服务。
5. 任何模型、版本、语言、浏览器目标或打包方式变化后重新核查。

统一第三方声明（GitHub 风格 THIRD-PARTY-NOTICES.md，许可全文在根 `licenses/`）见
[`../THIRD-PARTY-NOTICES.md`](../THIRD-PARTY-NOTICES.md)。
