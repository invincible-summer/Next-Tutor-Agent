# Mobile asset scripts

| 脚本                        | 输出                                                  |
| --------------------------- | ----------------------------------------------------- |
| `generate_brand_assets.mjs` | 项目自绘书本/学习路径的移动图标与启动图               |
| `bundle_math.mjs`           | 离线 KaTeX JavaScript、内嵌 WOFF2 字体和原始 MIT 许可 |
| `generate_licenses.mjs`     | 第三方许可页数据（apps/mobile 生产依赖子图 × 仓库许可清单），`--check` 为 CI drift 门禁 |

执行与更新流程见 [移动端开发](../../docs/development/mobile-dev.md)，渲染边界见 [应用架构](../../docs/architecture/mobile-app.md)，设备准入见 [移动端验收](../../docs/validation/mobile-validation.md)。
