# 安全策略

## 报告安全问题

请勿通过公开 issue 报告安全漏洞。通过 GitHub 仓库页面的 **Security → Report a vulnerability**（私密安全公告）提交报告，或联系仓库 owner。报告时请包含：受影响组件（api / web / voice sidecar / 部署模板）、复现步骤或请求样例、影响评估。

我们会在确认后尽快回复；修复将在协调后发布。

## 报告时请勿包含

- 任何真实用户的私人数据（笔记、会话、学习画像、trace）；
- 真实账号凭证、API key、JWT secret 或密码；
- 生产实例的内部路径或配置原文（脱敏后再附）。

## 支持范围

仅支持仓库最近发布版本线上的安全问题；已归档/历史版本不承诺回补。部署模板（`deploy/`）中的 systemd/nginx 配置默认值也在支持范围内——若加固不充分请按同样方式报告。

## 部署侧安全基线

自部署实例请遵循 [`docs/operations/deployment.md`](../docs/operations/deployment.md) 的生产清单：`AUTH_MODE=1`、强 `AUTH_JWT_SECRET`、受限 `CORS_ORIGINS`、nginx 对 SSE 关闭 buffering、`.env` 权限 600。安全相关的架构边界（鉴权矩阵、路径防护、限流、直连网络策略）见 [`docs/architecture/backend-runtime.md`](../docs/architecture/backend-runtime.md)。
