# @next-tutor/api-client

平台无关的 REST/SSE 客户端：传输抽象（注入 fetch/token/metadata）、错误
envelope、有界重试、SSE 解码与按领域的 API 方法。

- 通过 `ApiClientConfig` 注入 `baseUrl`/`fetchImpl`/`tokenProvider` 等，
  浏览器与移动端各自提供 adapter；本包不读环境变量、window 或 SecureStore。
- 领域模块：`auth`、`chat`、`workspace`、`guest`、`assessment`、
  `capabilities`、`classroom`、`diagrams`、`illustration`、`knowledge`、
  `learning`、`library`（含 `textbooks`）、`notes`、`voice`、
  `tools.illustration`。
- SSE decoder 统一处理跨 chunk 行、UTF-8 边界、多行 data、heartbeat 与
  done/abort；二进制响应用 `responseType: "bytes"` 解析。

架构文档：`docs/architecture/client-platform.md`。
