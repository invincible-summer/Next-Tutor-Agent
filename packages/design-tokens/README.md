# @next-tutor/design-tokens

品牌与自适应设计 token 的事实源（颜色/间距/圆角/字体/阴影/动效/布局），
保留 Web「纸墨书院」视觉身份，light/dark 双主题。

- Web 侧通过 `scripts/dev/generate_design_tokens.mjs`（根
  `pnpm tokens:generate`）生成 `apps/web/src/styles/tokens.generated.css`；
  Mobile 直接 import token 常量。

架构文档：`docs/architecture/client-platform.md`。
