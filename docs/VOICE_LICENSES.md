# 语音栈许可证与审计规程（VOICE_LICENSES）

本文件是语音栈（浏览器语音输入、本地 MeloTTS sidecar、课堂云端 TTS）的
权威审计入口：钉住来源、复核规程与发布检查表。汇总性声明见
[`THIRD-PARTY-NOTICES.md`](../THIRD-PARTY-NOTICES.md)；仓库许可证原文存于
[`licenses/`](../licenses/)。

## 1. 组成与边界

| 层 | 来源 | 是否随仓库分发 |
|---|---|---|
| 语音输入（STT） | 浏览器 `SpeechRecognition` 平台 API | 否（浏览器/厂商服务，条款随浏览器） |
| 本地 TTS（电话/课堂回退） | `deploy/install_voice.sh` 部署时下载 | 否（gitignored 目录） |
| 课堂云端 TTS（首发 Azure） | 管理员配置 `AZURE_SPEECH_KEY/REGION` | 否（在线服务，按用量计费） |

仓库不捆绑任何模型权重；部署或再分发后，相应开源义务随之生效。

## 2. 本地 MeloTTS 栈（钉住清单）

安装脚本按固定 revision 下载（`deploy/install_voice.sh`；可用
`VOICE_MELO_REF` 等环境变量在审计后整体换版）：

| 组件 | 钉住 revision | 许可证 |
|---|---|---|
| MeloTTS 源码 | `209145371cff8fc3bd60d7be902ea69cbdb7965a` | MIT（© 2024 MyShell.ai） |
| MeloTTS-Chinese 模型 | `af5d207a364ea4208c6f589c89f57f88414bdd16` | MIT（model card） |
| bert-base-multilingual-uncased | `7cbf9a625e29989f6b9c6c2fa68234c304f7e38f` | Apache-2.0 |
| bert-base-uncased（tokenizer） | `86b5e0934494bd15c9632b12f734a8a67f723594` | Apache-2.0 |

sidecar venv 依赖（CPU torch 等）钉住在 `backend/voice_sidecar/requirements.txt`；
许可边界表见 THIRD-PARTY-NOTICES「Voice sidecar dependencies」。

**运行约束（与 §11.6 一致）**：CPU-only、`HF_HUB_OFFLINE=1`、
`TRANSFORMERS_OFFLINE=1`、固定 revision、不临时拉取模型；缺 venv/模型时
保持"文字课堂"，不得自动下载补齐。

## 3. 课堂云端 TTS（Azure Speech，阶段 F 首发）

- 凭证只由管理员经部署环境（`.env`/systemd）配置：
  `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION`；可选官方资源域
  `AZURE_SPEECH_ENDPOINT`（仅接受 `https://*.api.cognitiveservices.azure.com`，
  普通用户不能携带任意 base URL）。
- 本项目不转售、不代理 Azure 语音服务；最终部署者与微软之间适用
  [Microsoft 服务协议](https://www.microsoft.com/en-us/servicesagreement)
  与 [隐私声明](https://privacy.microsoft.com/privacystatement)，语音
  数据处理以微软当期产品条款为准（部署前自行复核，本仓库不承诺任何
  免费额度或数据驻留保证）。
- 请求格式为公开 REST 契约（SSML + `riff-24khz-16bit-mono-pcm`）；本项目
  构造的 SSML 只含 `speak/voice/prosody` 受控结构并全量转义，不接受模型
  或客户端提供的原始 SSML。
- 用量按实际请求字符数/次数记录于用户私有运行数据（`owner.json`）；
  本项目不硬编码价格，费用估算只在管理员提供单价时进行。
- 允许音色仅限管理员配置并经官方 voices list 验证的
  `CLASSROOM_TTS_VOICE_ZH/EN`；voices list 只投影 ShortName/语言/展示名。

## 4. 复核与发布检查表

1. `deploy/install_voice.sh` 全新环境安装，记录实际下载的 revision 哈希
   与模型缓存清单，与本文件 §2 一致。
2. `python3 -m unittest tests.test_voice tests.test_voice_azure
   tests.test_classroom_audio`（backend/ 下）全绿。
3. 需要再分发时：保留上游 LICENSE/NOTICE/model card 与 revision 哈希，
   按实际安装版本生成 SBOM；Torch 等 multi-license 打包以 wheel 内
   NOTICE 为准，不得只标 MIT。
4. 涉及 Azure 的部署：在部署文档记录 region/endpoint、启用音色清单与
   复核日期；删除凭证后确认课堂正确回落"文字课堂"而非伪语音。
5. 浏览器 STT：随浏览器版本变化，发布前在目标浏览器复核可用性与
   厂商条款链接是否仍有效。

审计记录（最近在上）：

- 2026-09-26（阶段 F）：新增课堂云端 TTS（Azure REST）一节；其余边界
  沿用 2026-08-30 的 THIRD-PARTY-NOTICES 审计。
