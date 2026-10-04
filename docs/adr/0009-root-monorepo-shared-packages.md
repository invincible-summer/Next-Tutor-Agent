# ADR-0009：根 pnpm monorepo 与共享客户端包

- 状态：accepted
- 日期：2026-10-04

## Context

改造前 `apps/web` 自带 pnpm workspace 与 lockfile，类型生成物散落
Web 源码内（classroom/assistant 各自脚本），传输层（fetch 封装、重试、
SSE 解析）只存在于 Web。移动端出现后，任何"每个平台复制一份"的做法都会
让契约漂移与重试/鉴权语义分叉。

## Decision

- 仓库根建立唯一 pnpm workspace：`pnpm-workspace.yaml` 收纳
  `apps/*` 与 `packages/*`，唯一 lockfile 在根；构建脚本决策显式写入
  `allowBuilds`（pnpm 11 默认 strict-dep-builds）。
- 共享包固定五个：`packages/contracts`（服务端 Pydantic 生成的 TS 契约
  + 协议类型）、`packages/api-client`（注入式平台无关 REST/SSE 客户端）、
  `packages/domain`（跨端纯逻辑）、`packages/design-tokens`（令牌事实源）、
  `packages/i18n`（跨端文案协议）。不再增设其他客户端共享包。
- 契约生成收敛到 `scripts/contracts/`（生成、OpenAPI 快照、契约 lint），
  生成物只落在 `packages/contracts/src/generated/`，`--check` 进 CI；
  Web 内旧的生成器与生成文件删除，改从 `@next-tutor/contracts` 引用。
- 共享包 TS 基线全开 `noUncheckedIndexedAccess` 与
  `exactOptionalPropertyTypes`；`apps/web` 存量按包暂缓并在其 tsconfig
  标注，后续统一收紧。共享包测试用 Node 内置 `node --test`。
- Web 通过 `transpilePackages` 直接编译包内 TS 源，按域渐进迁移到
  `@next-tutor/api-client`；未迁移域保留 Web 本地实现。

## Consequences

- 客户端契约只有一个事实源：schema 改动必须走生成链，手工改生成物
  会被 `--check` 拒绝。
- 传输语义（重试、401 单飞、SSE 解码、幂等恢复）由共享包统一演进，
  平台差异压缩为 adapter（浏览器 fetch vs expo/fetch、token 存取）。
- CI 与本地脚本以根 lockfile 为指纹；apps/web 不再有自己的 lock，
  任何缓存 key 都指向根 `pnpm-lock.yaml`。
