# ADR-0022: 作图器三模式独立画布与独立存档（移除立体几何构造）

- 状态：accepted
- 日期：2026-10-10

## Context

ADR-0021 落地的几何作图器按"四视图共享一份文档"设计（`geometry2d/functions2d/geometry3d/functions3d` 共用文档、参数表、撤销栈与 2D 视图）。实际使用暴露出四类问题：

1. **立体几何模式不可用**：3D 构造工具（向量/长方体/球/平面）没有任何舞台交互实现，`objects3d` 只能程序化创建，形成死代码与虚假入口。
2. **模式共享幕布造成混乱**：两个 2D 模式共享同一 `view2d` 与同一画布内容——平面几何里出现函数坐标轴与曲线，二维函数里看到几何对象；用户预期是"切换模式即切换幕布"。
3. **保存语义不符预期**：用户在四个模式里的绘图是不同作品，共享一份文档/存档槽位导致"保存了一个模式却连带全部模式"的困惑。
4. **函数输入主链路损坏**：空表达式 `addPlot2D` 被校验拒绝且错误被静默吞掉，"添加函数"点击无任何反馈（plan D5 的草稿行从未实现）。

## Decision

1. **模式收敛为三个**：`functions2d`（默认）、`geometry2d`、`functions3d`。删除 `geometry3d` 模式与 `objects3d` 构造对象全链路（domain 类型/校验/命令、3D 舞台 solids/planes/vectors 构建、工具轨与文案）。3D 保留函数绘图（显式/参数/隐式曲面与空间曲线）；`math3d.ts` 的纯向量/坐标系数学保留。
2. **每模式独立画布**：`functions2d` 画坐标轴+刻度+曲线+自由点；`geometry2d` 只画网格纸与几何对象（无坐标轴、无曲线）；`functions3d` 独占 Three 舞台。文档各持自己的 `view2d`/`camera3d`。
3. **每模式独立文档与存档槽位**：web 层 `useModeWorkbenchStates()` 为每个模式维护独立 bundle（document/history/drafts/baseline/dirty）。存档升级为 **v2**：`activeIds: Record<DrawingMode, string>`；v1 存档（单一 `activeId`）在读取时按文档自身模式归位迁移。
4. **兼容策略**：文档 `schemaVersion` 维持 1；导入/加载校验对旧载荷宽容——`objects3d` 字段接受并丢弃，`activeMode: "geometry3d"` 归一化为 `"functions3d"`（旧立体几何文档保留其中 3D 函数部分）。`setActiveMode` 命令删除（模式不再是文档命令）；新增 `createMathDocument(name, mode)`。
5. **交互修复随本 ADR 一并落地**：视图命令（`setView2D`/`setCamera3D`）不再进入撤销历史且 undo/redo 保持视图连续；2D 滚轮缩放改为原生非被动监听（`preventDefault` 生效）；拖点实时预览（DAG 下游跟随，pointerup 单次提交）；3D 相机手势结束后回写文档、编辑不再拉回旧相机；画布右上角提供缩放/回到原点/适配视图浮层。

ADR-0021 的其余决定（Three.js 固定版本、纯 TS domain 内核、Worker、localStorage 本机存档、无后端）不受影响；本 ADR 取代其"四视图共享一份文档"的范围约定。

## Consequences

- 旧存档中的立体几何构造对象在迁移后不可恢复（功能整体移除，git 历史可回溯）；3D 函数内容完整保留。
- "四模式共享文档"意味着跨模式参数联动（如同一个参数 `a` 驱动 2D/3D）不再可能；如未来需要，应作为显式的跨文档引用另立设计。
- 每模式独立撤销栈使快捷键语义随活动模式切换，与独立保存语义一致。
