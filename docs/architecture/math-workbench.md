# Math Workbench（几何作图器）

`/tools/geometry` 的浏览器本地数学作图工作台：二维函数、平面几何、三维函数与基础微积分/方程求解。无 AI、无后端 API、无数据库（ADR-0021）；三个模式各持独立画布与独立存档（ADR-0022），模式切换经 dirty 保存提示、右栏按模式分域（ADR-0023）。正式部署受现有 `(workspace)` 登录门控；Pages 演示只读。

- 代码：`packages/domain/src/math-workbench/`（纯 TS 数学内核）、`apps/web/src/components/pages/tools/math-workbench/`（工作台 UI）、`apps/web/src/app/(workspace)/tools/geometry/`（路由与文案）。
- 测试：domain `tests/math-workbench-*.test.ts`；web `tests/unit/test-math-workbench.mjs`、`tests/unit/test-math-workbench-file-state.mjs`（显式接入 `test:unit` 串联）；E2E `tests/e2e/math-workbench{,-file-guard,-3d}.spec.ts`。

## 依赖方向

```
apps/web /tools/geometry (UI, i18n, input, commands, local storage)
   ├── stage2d (SVG view + interaction)
   ├── stage3d (Three.js renderer, 仅 3D 模式懒加载)
   ├── worker (重网格计算: 协议+调度+纯 domain 任务)
   └── @next-tutor/domain math-workbench (纯 TS 数学真理)
```

domain 不 import React/DOM/Three/Worker/localStorage/API。Three 只存在于 `apps/web` 的 `three/` 展示层；3D chunk 经 client 边界内的 `next/dynamic({ssr:false})` 懒加载，2D 模式与其他页面不请求 Three 代码。

## 数学内核（domain）

- `expression.ts`：手写 tokenizer → Pratt parser → 符号校验 → 有界求值器。用户输入的唯一路径；无 eval/动态 import/成员访问/字符串。别名 π→pi、θ→theta、×→*、÷→/、`**`→`^` 仅在规范化层；`2x` 仅做数字紧邻归一化，`xy` 永远报错而非猜测乘法。优先级：`-x^2`=`-(x^2)`、`2^3^2` 右结合。上限（字符/token/AST 节点/深度/参数数）与预算集中在 `diagnostics.ts`。
- `model.ts` / `document.ts`：文档 schema v1（纯数学定义，不存场景快照/坐标缓存）；allowlist 导入校验（有限数、id 唯一、类型枚举、引用完整无环、表达式重编译）。旧载荷兼容：`objects3d` 接受并丢弃，`activeMode: "geometry3d"` 归一化为 `"functions3d"`（ADR-0022）。命令 reducer（`batch` 原子）；`semanticFingerprint` 只覆盖数学/样式子集，排除 `revision`/`activeMode`/`view2d`/`camera3d`；`createMathDocument(name, mode)` 按模式建新文档。
- `calculus.ts` / `equations.ts`：符号一阶/偏导（有界化简、不可导点诊断）、尺度自适应中心差分、预算受限自适应 Simpson 定积分（奇点/不收敛返回诊断而非假成功）；线性/二次精确分支（稳定公式、`sign(0)` 修正）、≤3×3 主元消元、区间扫描+安全割线求根（极点残差拒绝、切触根探测）、受限 `left=right` 方程解析。
- `geometry2d.ts`：解析基元/构造/交点（线线、线圆、圆圆，含相切/平行/同心退化）、构造 DAG 解析（交点双解按 branch/锚点稳定选支）、测量与变换。
- `plot2d.ts`：显式/反函数自适应采样（渐近线按"正负号翻转+量级超视口"拆分 polyline，`tan(x)` 不跨接）、参数/极坐标、marching squares 隐式等值线（鞍点中心值判决、角点零值偏移消除断链）。
- `math3d.ts`：右手系数学空间（z 向上）向量/ray/plane 数学与标准几何体度量、`mathToThree/threeToMath` 唯一适配（行列式 +1）。3D 构造对象解析已随 geometry3d 模式移除。
- `surfaces3d.ts` / `implicit3d.ts`：显式曲面（规则网格、间断/过大单元丢弃、法线中心差分）、参数曲面（wrapU/wrapV 精确缝合、退化三角形剔除、∂r/∂u×∂r/∂v 法线）、marching tetrahedra 隐式等值面（0–6 主对角线六四面体、全局边键顶点合并、∇F 法线、分辨率硬上限）；tile 分块保证透明排序索引连续；所有输出双精度计算后 finite 检查再转 Float32Array。
- `presets.ts`：全部自制数学示例（马鞍面/高斯峰/波纹/环面/隐式球/螺线），无第三方资产。

## Web 工作台

- `MathWorkbench.tsx`：单工作台编排。64px 顶栏（返回/标题/dirty 指示/撤销重做/右上文件组）、52px 一级工具轨（工具按钮带 `geometry-tool-<id>` testid）、默认收起的 208–232px 二级模式栏（遮罩 overlay，不占画布宽）、右侧面板（独立滚动；桌面默认展开，**≤599px 默认折叠为底部胶囊按钮**，画布优先，胶囊标签显示当前页签名）、34px 状态栏。**每模式独立 bundle**（ADR-0022）：`useModeWorkbenchStates()` 为 `functions2d/geometry2d/functions3d` 各维护一份 document/history/drafts/baseline/dirty。**模式切换经 dirty 检查**（ADR-0023）：`switch-mode` 意图走三选一弹窗（保存并切换 / 直接切换 / 取消），"直接切换"不销毁数据也不清崩溃恢复草稿；`Alt+1/2/3` 同路径；上次使用的模式记在 localStorage。所有文件操作（新建/打开/保存/另存/删除/导入）作用于当前模式的文档；打开属于其他模式的文档会同步切换模式。
- **右栏页签按模式分域**（ADR-0023）：`functions2d = [函数, 数学工具]`；`geometry2d = [对象, 属性]`；`functions3d = [函数]`（单页签渲染为标题）。其他模式的页签残留回落到新模式首页签；跨模式不出现无关页签（平面几何无函数/计算页，函数模式无对象/属性页）。
- 舞台按模式切换幕布：`functions2d` 画坐标轴+刻度+曲线+自由点（计算器"标记到图上"产物）；`geometry2d` 只画网格纸与几何对象（无坐标轴、无曲线）；`functions3d` 独占 Three 舞台。各文档各持 `view2d`/`camera3d`。
- 文档状态机（D6/D7）：`useWorkbenchState` 维护 working document、savedBaseline、表达式草稿（按 `plotId:field` 键）、待提交函数草稿行（pendingPlots，空行不是错误、不置 dirty）、历史（上限 100）。`isDirty = 无基线 || 语义指纹漂移 || 未提交草稿 || 非空待提交行`。**视图命令（`setView2D`/`setCamera3D`）不进撤销历史**，undo/redo 恢复快照时携带当前视图保持连续。保存为用户明确触发的版本化本机快照：`localStorage` 写入后 read-back 校验，失败保持 dirty 并展示 `保存失败`。Ctrl/Cmd+S/O/N/Z（含 Shift）/Y，输入法 composition 期间不触发。命令失败不再静默：`lastError` 经 toast 显示错误码。
- 三选一未保存确认（`UnsavedChangesDialog` + `requestDestructiveTransition`）：结构化 `FileIntent`（leave/new/open/replace-import/replace-preset/switch-mode），单飞 pendingIntent；switch-mode 变体文案为"直接切换"且不清恢复草稿（ADR-0023）；保存失败弹窗保持打开。浏览器刷新/关闭经全局 `useUnsavedChanges`（原生提示）；应用内导航/文件操作/模式切换走自定义 Modal。
- 崩溃恢复：dirty 期间防抖写 `sessionStorage` recovery key（与存档 key 分离，按文档 id 隔离），重开时非阻断提示条"恢复/忽略"，恢复若覆盖已有更改仍走三选一。
- `storage.ts`：存档 **v2** `next-tutor.math-workbench.v1:<owner>`（`activeIds: Record<DrawingMode, string>` 每模式一个活动文档槽位，≤40 文档）；v1 存档读取时把旧 `activeId` 按文档自身模式归位迁移。文件管理器列表带模式徽章并默认只筛当前模式。导入严验并转为未保存新文档（新 id 不覆盖原文件），JSON 导出为独立备份；删除二次确认。
- 函数输入（`functions2d`/`functions3d` 的"函数"页签）："添加函数"创建**面板本地草稿行**（类型选择器+按类型分字段+显式"添加"按钮，Enter 同样提交；空行不是错误），首次进入自动出现一条空草稿行；提交前 `compileExpression` 校验，非法留在草稿并显示错误码；定义域在提交后的详情区编辑。已提交行单行化：`[可见性][色点][按类型分字段（多字段纵向堆叠）][▸ 详情][×]`；"▸ 详情"抽屉（chevron 图标）含类型（2D 可切换）、**每条曲线的变量范围**（2D x/y/t/θ，3D x/y、u/v、box、t，空=默认区间）、色板、线宽（3D 另有质量/透明度/网格）。`FormulaInput.tsx` 两种形态：完整形态（符号条 × ÷ ^ √ π θ 括号 + 内联 KaTeX 预览，用于数学工具）；紧凑形态（行内无符号条，聚焦时才出现**浮层预览**，不推挤布局），`(` 自动补全/选区包裹、`Esc` 还原本行草稿；KaTeX 预览经 `latex.ts` 将 domain AST 序列化（`/` 渲染分数、`^` 上标；输入仍是普通文本）；提交时剥离 `y =`/`x =`/`z =` 前缀，隐式类型支持 `F(x,y)=c` 拆分。
- **数学工具面板**（`AnalysisPanel.tsx`，仅 `functions2d`；面板在页签切换间保持挂载以保留状态）：顶部四段选器（求值/导数/定积分/解方程）+ **函数来源选择**（下拉取当前文档的显式曲线 `y = …`，或手动输入）。四个工具各有专属结构化结果卡（不再拼纯文本）：求值 = KaTeX 表达式 + 大号数值；导数 = 符号导数 KaTeX + 在 x= 处数值（符号失败诚实回退 `≈` 数值）+ "绘制导数曲线"（导数作为虚线显式曲线入图）；定积分 = KaTeX `∫ₐᵇ f dx` + `estimate ± errorEstimate` + 收敛/求值次数徽章（区间默认取来源曲线定义域）；解方程 = 方程 KaTeX + 根列表（单根/重根徽章、精确解/区间数值解标注、逐根"标记"与全部标记，标记以结构化 `RootSet` 驱动并合成单个 `batch` 一次撤销）。所有输入 Enter 即运行；诊断码以列表呈现。
- **对象/属性面板**（`GeometryInspector.tsx`，仅 `geometry2d`）：对象列表行 = `[可见性][类型徽章][行内标签编辑][解析摘要（坐标/线段长/半径，来自 resolveGeometry2D）][×]`，点击选中；属性面板 = 标签编辑、类型+构造输入摘要（如 `线段 A – B`）、自由点 x/y 可直接键入（派生构造只读）、线段长/圆半径读数、色板/线宽/**锁定**开关/级联删除。
- `Stage2D.tsx`：SVG 坐标纸（1/2/5×10ⁿ 刻度、默认视图 `scale 0.028`、轴名附近的刻度自动避让）、按模式过滤的内容渲染、构造工具（点/线段/圆/测量/文本）、网格吸附；指针交互区分选择/拖动/平移/缩放。**构造语义**（ADR-0023）：构造点击先命中已有点（14px 屏距磁性容差，悬停显示虚线光环）则复用其 id，点工具点击已有点=选中；新点+图形合并为单个 `batch` 命令——一次撤销；支持点击-点击与拖拽画线两种手势，构造中有虚线橡皮筋预览，`Esc` 取消构造（再按取消选择）、`Delete`/`Backspace` 删除选中对象；零长度/同点重复点击被忽略；构造状态随工具与文档派生失效（`activePending`，无 effect 重置）。**滚轮缩放为原生非被动监听**（`preventDefault` 生效、以光标为锚）；拖点期间对"替换了被拖点坐标的 objects2d"实时重解析渲染（DAG 下游跟随），pointerup 一次语义提交；文本注释双击行内编辑（工具创建时直接进入编辑态）；左下角光标读数 + 构造提示文案。2D 导出为重组的安全 SVG（预览/光环等瞬态元素带 `data-no-export` 排除）。
- 画布右上角**视图控制浮层**（`geometry-view-controls`）：2D 放大/缩小/回到原点（默认视图）/适配视图（内容包围盒）；3D 重置视角/适配（接线 `controller.fit()`）。
- `Stage3D.tsx` + `three/`：`ThreeSceneController` 唯一拥有 WebGLRenderer/Scene/Camera/OrbitControls（invalidate-on-demand、ResizeObserver+DPR≤2、context lost/restored、完整 dispose）；文档相机**只在挂载时应用一次**，之后由 OrbitControls 持有，手势结束（防抖）回写 `setCamera3D`——文档编辑不再把相机拉回旧位置；`scene-objects.ts` 将 domain 网格经 Z-up 适配一次性转入 BufferGeometry（不透明写深度、透明不写深度、DoubleSide、相交透明为近似），仅构建环境网格/坐标轴与函数曲面/曲线（构造实体已随 geometry3d 移除）；WebGL2 不可用时降级为明确提示，2D/计算不受影响；PNG 导出为显式 renderOnce 后 toBlob（不依赖 preserveDrawingBuffer）。
- `worker/`：`protocol.ts` 判别任务（id+documentId+revision）；`math-worker.ts` 纯 domain 任务运行器（大网格以 transferable ArrayBuffer 返回，批次间 `setTimeout(0)` 真实让出事件循环使取消可达）；`scheduler.ts` 限量并发、revision stale guard、错误时 terminate+重建（上限 6 次）与主线程回退，UI 不悬挂。
- i18n：`tools/geometry/strings.ts` zh/en 成对（D10 文案清单）；语言切换只换文字。稳定 testid：`geometry-workbench/-mode-toggle/-mode-panel/-mode-<mode>/-tool-<id>/-stage-2d/-stage-3d/-formula-editor/-pending-plot/-view-controls/-right-panel/-file-open/-file-save/-file-new/-file-menu/-unsaved-indicator/-unsaved-dialog/-calc-run/-analysis-<tool>/-analysis-result/-analysis-root/-analysis-mark/-object-row`。函数行结构 `[可见性][色点][分字段表达式][▸ 详情 chevron][×]`，详情区含类型/定义域/色板/线宽（带数值读数）；深色主题下曲线描边整体提亮（palette 色为浅色纸面调校）。

## 明确不支持 / 边界

- 无符号解的方程只返回 `symbolic_unsupported` 或数值区间解（带残差与"当前区间未找到"语义），不宣称全根保证。
- 相交透明曲面的绘制顺序是物体级近似（首版无 OIT/depth peeling）；UI 文案不宣称精确透明。
- 浏览器本地存档不跨设备同步；服务端同步/移动端原生作图需另立 ADR。
- 每模式独立文档意味着参数/命名函数不跨模式共享（ADR-0022 的取舍）；如需跨模式联动应另立显式设计。
- 数学工具面板只在 `functions2d` 提供（单变量分析）；3D 曲面的求导/积分/方程求解如需支持应另立设计。
- 立体几何构造（3D 点/向量/平面/多面体）已移除；3D 能力聚焦函数绘图。隐藏线双 pass 与 3D 拖动 gizmo 属于后续增强路径。
- KaTeX 预览是只读渲染：输入语言仍是 domain 表达式语法（÷/×/π 等记号由编译层归一化），不是 LaTeX 编辑器。

## 维护

- Three.js 升级流程见 ADR-0021（release notes → @types 相容 → LICENSE 核验 → SBOM 重建 → 3D 回归）。
- 数学预算与容差只改 `packages/domain/src/math-workbench/diagnostics.ts`。
- 新 UI 文案先入 `strings.ts` 双语对；稳定 testid 变更需同步 E2E。
