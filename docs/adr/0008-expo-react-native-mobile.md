# ADR-0008：Expo/React Native 单移动代码库

- 状态：accepted（决策冻结；应用仓库 `apps/mobile` 尚未建立，建库时直接执行本决定）
- 日期：2026-10-04

## Context

平台化改造要求移动端与 Web 共享 API 客户端、契约与领域逻辑。候选技术栈有
React Native（Expo 管理）、Flutter 与 WebView/Capacitor 壳。Web 已是
React + TypeScript，共享层的价值取决于两端语言与组件模型能否互通；
Markdown/KaTeX 渲染、SSE 长连接与富文本交互是核心学习体验，不能降级为
内嵌网页。

## Decision

- 移动端唯一代码库：Expo SDK 57 / React Native 0.86 / TypeScript，
  Expo Router 只做路由装配；不引入 Flutter，不做全 WebView/Capacitor 壳。
- 一个 `apps/mobile`，与 `apps/web` 平级，通过根 pnpm workspace 消费
  `packages/{contracts,api-client,domain,design-tokens,i18n}`；移动端
  API 出口统一 `expo/fetch`，不得在移动端复制 Web 的 API 封装。
- React Native 与 Web 的 React 版本由各自平台锁定，不共享 React 运行时；
  依赖只通过 workspace 包共享，版本策略见 `package.json` engines 约束。
- 语音采用云语音（服务器 STT/TTS），移动端不做本地语音；Markdown 数学
  渲染沿用 Web/KaTeX 语义的移动适配，不另起渲染体系。
- 自适应布局按窗口尺寸类合同统一维护（手机/平板/横竖屏/折叠屏），
  断点事实源在 `packages/design-tokens`。

## Consequences

- 跨端逻辑只能进入共享包（契约、传输、纯领域逻辑、令牌），平台代码
  各自持有 UI state、timer 生命周期与导航；共享包禁止读 window/env。
- Expo 升级节奏受 SDK 版本约束，React Native 主版本升级需同步评估
  workspace 依赖兼容性。
- 本 ADR 先于应用落地冻结，建库 PR 必须从根 workspace 共享包起步，
  不得先在 mobile 内复制 API 代码再回头抽取。
