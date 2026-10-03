# scripts/diagrams — 图库生成物重建

从 `app/diagrams/` 渲染器源码确定性重建图库目录与素材包。两个脚本都是纯离线的确定性生成器：给定同一份渲染器代码，输出字节一致（`--check` 即据此校验）。

| 脚本 | 作用 | 输出（允许且必须入库） |
|------|------|------------------------|
| `build_catalog.py` | 重建 `services/api/assets/diagram_library/catalog.json` 与 [docs/reference/diagram-assets.md](../../docs/reference/diagram-assets.md) | 版本化目录 + 参考清单 |
| `build_packages.py` | 逐素材重建 `materials/<asset>/{asset.svg, material.json, usage_guide.json}` | 1130 个内置素材包 |

## 契约

- **联网**：否（只读本仓库渲染器源码，不下载、不调用模型）。
- **真实 LLM 费用**：无。
- **输入**：仓库内项目自绘渲染器代码（synthetic，无外部素材）。
- **输出**：SVG、元数据与目录为 tracked 生成物，允许且必须入库，须通过生成器重建。`usage_guide.json` 是每份素材独立维护的短提示词源码；生成器仅在缺少它时创建空指南，保留已有内容，修改提示文字须更新指南版本。
- **推荐调用位置**：改动 `app/diagrams/` 任何渲染器后本地手工运行重建；CI 由 `scripts/repo/check_documentation.py --check-generated` 调用 `build_catalog.py --check` 守护目录与源一致。
- **对应文档**：[docs/architecture/diagrams-illustration.md](../../docs/architecture/diagrams-illustration.md)、验收记录 [docs/validation/diagram-library.md](../../docs/validation/diagram-library.md)。

## 审核 ledger（review.json）

`assets/diagram_library/review.json` 按素材记录审核状态与 `source_hash`；`source_hash` 绑定渲染器源码与本目录生成脚本。源码变化会使依赖该来源的素材审核过期；按 [docs/validation/diagram-library.md](../../docs/validation/diagram-library.md) 重渲染检查默认图和参数边界，审阅变化的实图，复核未变化图与已有审查证据一致后重绑 ledger，再运行 `build_catalog.py` 重建。只调整指南不改变 SVG 时无需重绑绘制来源。
