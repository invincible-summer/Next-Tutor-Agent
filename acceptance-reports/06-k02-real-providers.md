# K02 真实 provider 烟测记录（plan.md §19.6）

日期：2026-09-26　结论：**未验收（缺真实凭证）**

按 §19.6 规定，缺真实凭证时如实记录该项未验收，不把 stub 成功记为
生产验收通过。本次开发环境未配置任何真实 provider 凭证（.env /
backend/.env 均未设置）：

| 项 | 凭证 | 状态 |
|---|---|---|
| Azure Speech zh/en 口播 | AZURE_SPEECH_KEY / AZURE_SPEECH_REGION | 未配置 → 未验收 |
| 云失败回退本地（Melo 中文） | 本地 sidecar | 未在本机启动 → 未验收 |
| Pexels / Pixabay 选中图本地保存与署名 | PEXELS_API_KEY / PIXABAY_API_KEY | 未配置 → 未验收 |
| Tavily 有日期检索结果 | TAVILY_API_KEY | 未配置 → 未验收 |
| 真实 LLM 生成课程（P50/P95） | LLM_API_KEY | 未配置 → 未验收 |

替代覆盖（stub/mock 层面，不冒充真实验收）：

- 云 TTS 契约/错误分类/回退：`tests/test_voice_azure.py`、
  `tests/test_classroom_audio.py`（Azure adapter 用 httpx.MockTransport）。
- 图库/检索适配器与限流/署名：`tests/test_classroom_images.py`、
  `tests/test_classroom_research.py`（仅人工构造响应 fixture）。
- 无外部服务全链路（仅教材 → 文字课堂）：后端 2237 测试与
  E2E `classroom-create.spec.ts` 空教材路径。

后续管理员部署时执行：配置真实 key → `CLASSROOM_ENABLED=1` +
`CLASSROOM_ALLOWED_USERS=<测试账号>` → 按 §20.3 记录实际请求数/用量，
补齐本记录（zh/en 口播、署名与日期来源、P50/P95）。
