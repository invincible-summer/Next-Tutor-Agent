# ADR-0021: Three.js 数学作图工作台

- 状态：accepted
- 日期：2026-10-09
- 备注："四视图共享一份文档"的范围约定已被 [ADR-0022](./0022-math-workbench-three-modes.md) 取代（三模式独立文档与存档、移除立体几何构造模式）；其余决定仍有效。

## Context

工具助手需要一个覆盖平面几何、二维/三维函数绘制、立体几何与基础微积分/方程求解的几何作图器。二维图形可以用 SVG 稳妥表达，但三维函数曲面需要真正的深度/透明/遮挡处理。备选方案：

- **纯 SVG 伪 3D**：零新增依赖，但无法稳妥处理遮挡、半透明与曲面规模。
- **自研原生 WebGL2**：最大底层控制，但需要长期维护 shader、资源、宽线、拾取、矩阵与相机，维护成本与本项目差异化目标不匹配。
- **Three.js `WebGPUReader`**：官方仍标 experimental，Material/TSL 接口仍在演进。
- **完整 CAS/三维数学套件**：能力广但引入额外许可证、体积与算法复杂度。

Three.js 运行时包为 MIT（零 npm 运行时依赖），在仓库 `licenses/policy.json` 的 `auto_allowed` 内，可闭源免费/收费商用；`@types/three` 为社区类型包，其类型级传递依赖已逐包核验（MIT/Apache-2.0）。

## Decision

- 新增浏览器本地几何作图器 `/tools/geometry`：二维用 SVG，三维用**固定精确版本** `three@0.186.1` 的 `WebGLRenderer`（WebGL2），`@types/three@0.186.0` 仅作 devDependency。
- Three.js 只存在于 `apps/web` 的 3D presentation 层（懒加载，仅 3D 模式请求）；`packages/domain/src/math-workbench/` 保持纯 TS 数学内核（表达式解析、微积分、方程、几何构造、网格采样），不 import Three/React/DOM，可在 Node 测试。
- 重计算放 Web Worker（纯 domain 任务），可取消/可降级；不使用 SharedArrayBuffer/COOP/COEP。
- 不接 AI、聊天、后端 API、数据库；文档为本机 `localStorage` 版本化数学定义（按 owner 隔离），JSON/SVG/PNG 导入导出；正式部署仍受现有登录门控约束。
- 升级 Three 版本必须走独立审查（release notes、@types 相容、LICENSE 校验、SBOM 重建、3D 视觉回归），不自动追更；不引入 Three 官方 examples 的模型/图片/字体等第三方演示资产。

## Consequences

- 仓库新增 `three`/`@types/three` 精确锁定依赖，notices/SBOM/inventory 需随安装重建（已通过 `check_license_policy` 与 `generate_notices --check`）。
- 3D 代码块仅沉浸式作图页懒加载，不影响其他页面首屏 bundle；无 WebGL2 环境下降级为 2D/计算可用并明确提示。
- Three 的物体级透明排序对相交透明曲面只是近似，UI 需明示"透明预览为近似"；首版不自研 OIT/depth peeling、不加 postprocessing 依赖。
- 数学正确性（求导、积分、方程、几何构造、网格法线）由 domain 层测试保证，与渲染层解耦；未来服务端同步或移动端原生作图另立 ADR。
