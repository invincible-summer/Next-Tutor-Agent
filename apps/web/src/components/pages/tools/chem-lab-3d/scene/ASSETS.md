# 化学实验台 3D · 原创资产登记

所有视觉资产均为**代码程序化生成**（Three.js 0.186.1 原生几何/材质/CanvasTexture），
未使用任何外部模型文件（GLTF/OBJ/FBX）、网络贴图、HDR 环境贴图、现成粒子素材或
他人开源实验台资源。登记口径见 `plan.md` §3.6 与 §10。

| 资产 | 生成文件 | 来源与工艺 | 外部素材 | 版权结论 |
| :-- | :-- | :-- | :-- | :-- |
| 玻璃器轮廓烧瓶/烧杯/量筒/锥形瓶/三口瓶/洗气瓶/冷凝器/沉降瓶/U形管/集气瓶/观察球/膨胀球/储液槽 | `EquipmentFactory.ts` + `EquipmentModels.ts` | 参数化 `LatheGeometry` 旋转剖面（自绘轮廓点）、`TubeGeometry` 螺旋内芯，共享 ref-count 几何 | 无 | 项目原创 |
| 金属件（铁架台立杆/夹爪/旋钮、阀门黄铜手柄、泵壳/喷嘴、台面金属包边、架柱） | `EquipmentFactory.ts`、`Environment.ts` | `CylinderGeometry`/`TorusGeometry`/`RoundedBox`（`ExtrudeGeometry` 圆角矩形），`MeshStandardMaterial` metalness 0.55–0.82 | 无 | 项目原创 |
| 软管/玻璃接管曲线 | `TubeSystem.ts` | 端口法线控制点 + 重力垂弧的 `CatmullRomCurve3` → `TubeGeometry` | 无 | 项目原创 |
| 演示液/液体层 | `EquipmentModels.ts`（`buildLiquidLathe`） | 按容器内轮廓参数化生成的封闭旋转体 + 弯月面 | 无 | 项目原创 |
| 量筒刻度贴图 | `EquipmentModels.ts`（`graduatedScaleTexture`） | Canvas 2D 自绘刻线与数字（演示刻度，非科学量具） | 无 | 项目原创 |
| 试剂瓶标签贴图 | `EquipmentModels.ts`（`reagentLabelTexture`） | Canvas 2D 色带 + 自创虚构物料短名（演示液 A/B 等） | 无 | 项目原创 |
| 舞台环境（台面/柜体/器材架/墙面/灯带/架上陈列） | `Environment.ts` | 程序化盒体/圆柱/圆角拉伸 + 确定性 seed 布局 | 无 | 项目原创 |
| 灯光配置（亮/暗主题） | `Environment.ts` | 暖主光 + 冷边光 + 半球环境光；无 HDR | 无 | 项目原创 |
| 火焰/气泡/蒸汽/沉淀/爆发等演出（Phase D） | `Effects.ts`（待建） | 计划：参数化变形 mesh + `InstancedMesh` 粒子池 | 无 | 项目原创 |

- 生成日期：2026-10-10 起（阶段 B 首建；后续新增资产请同步登记）。
- 每份程序贴图的绘制逻辑保存在源码中（`scaleTexture` 调用点），可完整追溯。
- 许可：随仓库整体授权；Three.js 运行时许可见根 `THIRD-PARTY-NOTICES.md`
  （three@0.186.1，MIT；用途已含本化学 3D 模拟台）。
- 内容边界：所有“试剂/反应/爆发”均为虚构演示物料与演出效果，无真实危险配方、
  可复现危险操作步骤或安全判定文案。
