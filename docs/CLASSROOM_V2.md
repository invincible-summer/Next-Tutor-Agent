# 课堂模式第二版：实施计划与验收清单

更新：2026-09-27。范围是新生成的课程、课堂列表、备课、编辑、讲授与导出。
本文件可作为逐项实施和代码审查清单；既有课堂的数据协议与权限边界仍以
[`DESIGN.md`](DESIGN.md) P12 为准。

手动重试在保留已完成课件页的同时开启新预算窗口；课堂列表和详情可将课程
移入归档中心，并在原学习区恢复或从归档中心彻底删除。对应数据合同见
[`DESIGN.md`](DESIGN.md) P12.16。

## 入口与信息架构（2026-09-28 更新）

课程是一级模块「备课上课」(`/course`)，与聊天彻底分离：

- 全局导航 `lib/nav.ts` 新增 `/course`（`classroomOnly`，能力关闭时 SideNav
  自动隐藏）；原聊天侧栏的「课堂」入口和聊天页头的「对话|课堂」模式条已
  移除，课程页统一用 `components/classroom/Breadcrumb.tsx` 面包屑
  （备课上课 › 辅导区 › 课程）。聊天侧栏的「工作学习区」更名为「聊天辅导」。
- `/course` 课程中心按辅导区分组聚合（折叠状态存 localStorage
  `edu-agent-course-expanded`），含「继续学习」横滑卡、每组一键备课、
  查看全部进入 `/workspaces/[id]/classroom` 列表页；既有
  `/workspaces/[id]/classroom/*` 详情与播放路由不变。
- 播放器：音量滑杆（桌面 hover/focus 弹出、移动端更多菜单内嵌）与倍速
  持久化在 localStorage `edu-agent-player-prefs`；控制条「语音」面板
  （`player/VoicePanel.tsx`）支持上课中途切换 TTS 策略（自动/云端/本地/
  静音）、选音色、试听，走 `PUT …/runs/{id}/audio-profile`（CAS）并使
  未播放段音频缓存失效；讲稿面板 `player/ScriptPanel.tsx` 按页分组、
  点击跳段、当前段自动滚动；字幕条切句渐隐并预播下一句。
- 个人默认课堂语音：资料页「课堂语音」卡写 `prefs.classroom`，run 创建
  优先级为 请求 > 课程 brief > 用户偏好 > 系统默认。

## 目标、边界和完成定义

学生进入课程后，应先看到清楚的课件和当前讲解，再按需打开目录、全文讲稿、
提问、练习和设置。每页同时有可阅读的完整知识内容与对应的口头讲解；长页
可滚动，不能靠裁掉内容换取整齐。生成成本维持原有九阶段与 LLM 调用预算。

完成的判据：从选教材到生成、编辑、上课、暂停退出、恢复、插问、检查点、
打印 PDF 全程可操作；390px 手机、768px 平板、桌面宽屏及浅/深色界面无
水平溢出和控件遮挡；旧课程仍按旧渲染器打开；新课程使用第二版主题和布局。

| 边界 | 决定 |
|---|---|
| 内容载体 | 后端输出受约束的 `LessonRevision`，编译为安全 HTML；讲稿段与页面绑定 |
| 版本 | 新 Job 固定 `renderer_version=2.0.0` 和主题 `@2`；历史 Job、Revision、Run 维持 `1.0.0`/`@1`，不批量迁移 |
| 视觉 | 第二版 5 套主题、9 种布局；后端可信 block 渲染器与 iframe 通信协议沿用 |
| 导出 | HTML ZIP、Markdown 讲稿、浏览器打印/PDF；不生成 PPTX |
| 成本 | 单页写作仍每页一次 LLM 请求；不因排版额外调用模型；内容复核维持用户显式开关 |
| 资料 | 沿用工作区授权来源、图片下载净化和来源归属；旧课程不追改 |

## 可按顺序执行的工作包

### 1. 固定版本与兼容合同

1. 在 `schemas/classroom.py`、`service.py`、`revisions.py` 固定创建时的
   renderer/theme；继续上课必须使用 `run.lesson_revision` 加载详情和 frame。
2. 在 `pipeline.py` 按 Job 版本选 prompt，发布时保存版本；单页重写沿用
   当前 revision 的版本。历史 Revision 缺版本时按旧版解释。
3. 在 `render/compiler.py` 仅对 `2.x` 注入新版 CSS；旧 `@1` token、布局、
   交互和打印维持旧实现。编辑旧版时只展示可用的旧主题 ID。
4. 验收：同主题 `@1/@2` 编译结果各自可打开；旧 run 指向旧 revision；
   修改新版课程仍是新版；API 新建课程返回 `@2`。

### 2. 备课内容与素材归属

1. 在 `prompts/classroom.py` 注册大纲和单页写作 `2.0.0`；明确要求每页
   展示定义、条件、推导、例子和误区中与该页相关的内容，讲稿补充解释。
2. 大纲阶段保留全部模型规划的知识页，超过目标页数时写警告，超过
   `MAX_SLIDES` 时明确失败；不静默截断已规划内容。
3. 给每页按标题、要点、图示对象排序教材证据；图片阶段存
   `page_assets[order]`，同图复用时仍记录每页归属；单页写作只传该页素材。
4. 结构修正时若原布局无法容纳内容，改用通用要点布局，保留 block；保留
   公式、图表和检查点类型，不以缩短文本作为布局修复。
5. 验收：多页使用不同证据；同意图图像正确关联；超目标页数不丢最后一页；
   无图片服务可生成纯文本课；LLM 预算与已有上限相同。

### 3. 真正可用的课件版式

1. 在 `render/compiler_v2.py` 为 title、key_points、image_explain、compare、
   derivation、worked_example、timeline、checkpoint、summary 分别设置阅读顺序、
   栅格、标题尺度、公式/图表/练习容器，五主题通过 token 控制背景、表面、
   对比度和强调色。
2. 页面在演示模式固定画布比例，正文区域独立滚动；阅读模式单列显示全文，
   打印模式允许内容自然扩展与分页。移动端首屏切到阅读模式。
3. 排版检查对新版只判水平溢出和真正遮挡；允许有意的正文纵向滚动。
   HTML escape、CSP、KaTeX、iframe sandbox 与 MessagePort 校验不放宽。
4. 验收：5 主题 × 9 布局在 1280×720、960×540、390px 视口打开；长标题、
   长英文词、长公式、无图、表格与长推导完整可读；打印 PDF 可检索全文。

### 4. 上课界面和播放行为

1. 列表页显示更清楚的课程预览、状态、时长和继续上课入口；详情页让预览
   与编辑成为主任务；备课弹窗显示主题的实际视觉差异。
2. `AppShell` 在 learn 路由隐藏全局导航；讲授页保留课程标题、返回、
   页码和明确的控制条。桌面侧栏按需显示目录/讲稿/笔记；手机用单一抽屉
   和紧凑播放控件，讲稿可在文字模式完整阅读。
3. 播放器先载入 run 再按固定 revision 取详情。加载、空音频、租约冲突
   都显示可恢复的状态；跨标签接管只在用户点击后发生。
4. 音频 `ended` 才计听完并推进；跳页不记听完；检查点在当前页音频结束后
   阻挡自动翻页；退出课堂暂停并释放 lease，明确结束才结束 run。
5. 验收：刷新不断课；最后一页手动结束；暂停立即停声；无语音可逐段阅读；
   租约冲突不静默夺取；服务端 `completed_kind` 只有全段听完才是 listened。

### 5. 编辑与导出

1. 手机编辑器把目录、内容和预览放到独立页签/抽屉；展示文字与口头讲稿
   分开编辑，防止一次保存覆盖另一种文本。
2. `GET revisions/{n}/frame?mode=print` 编译已授权且固定版本的打印 HTML；
   编辑器同步打开空白窗口后请求内容，载入后调用浏览器打印。用户在打印
   对话框中选择“保存为 PDF”。导出失败给出可见提示。
3. 验收：改讲稿不改展示文字；旧课程仍可编辑旧主题；导出 HTML、讲稿、
   PDF 都取选中的 revision；长推导的打印页没有缺行。

### 6. 回归、视觉与发布

1. 后端优先运行课堂管线、渲染、API、run、导出回归；新测试继承
   `StorageSandboxTestCase`。再运行全后端测试，确认没有写入生产存储根。
2. 前端运行 `pnpm exec tsc --noEmit`、`pnpm exec eslint src/`、
   `pnpm exec next build --webpack`；浏览器运行 `e2e/classroom-*.spec.ts`。
3. 用真实编译 HTML 检查浅/深主题、390/768/1440px、长页滚动和 PDF；
   保存有代表性的截图到 `acceptance-reports/screenshots/`。
4. 上线前构建 `pnpm run build:classroom`。小流量用新课验证生成、播放、
   打印和历史课打开，再扩大。若新版样式出现问题，停止新课创建并保留
   历史课读取；已发布新版 revision 不改成旧格式。

### 视觉验收材料

以下文件使用后端真实 `LessonRevision` 编译器和 Chromium 制作，内容为
测试夹具，无学生资料。播放器截图由浏览器 E2E 生成。

| 场景 | 文件 | 人工检查点 |
|---|---|---|
| 学术主题桌面 | [新版要点页](../acceptance-reports/screenshots/classroom-v2-academic.png) | 两列要点与提示区、标题、页码 |
| 板书主题推导 | [新版推导页](../acceptance-reports/screenshots/classroom-v2-chalk.png) | 公式渲染、步骤、警示框和对比度 |
| 390px 阅读 | [新版手机页](../acceptance-reports/screenshots/classroom-v2-mobile.png) | 单列文字换行和正文滚动 |
| 长推导打印 | [PDF 样张](../acceptance-reports/classroom-v2-print-long.pdf) | 10 页；5 个推导步骤与结尾警示均可检索 |

## 文件责任和依赖顺序

| 顺序 | 主要文件 | 交付物 | 依赖 |
|---|---|---|---|
| A | `schemas/classroom.py`, `service.py`, `revisions.py` | 版本合同 | 无 |
| B | `prompts/classroom.py`, `pipeline.py`, `validation.py` | 完整内容与来源绑定 | A |
| C | `render/themes.py`, `render/compiler_v2.py`, `render/compiler.py` | 主题、布局、阅读与打印 | A、B |
| D | `components/classroom/*`, `app/.../classroom/*`, `lib/classroom/*` | 列表、备课、编辑、讲授 | A、C |
| E | `tests/test_classroom_*`, `e2e/classroom-*`, `acceptance-reports/` | 回归与视觉证据 | A–D |

## 发布检查表

- [x] 新课生成后每页有对应展示内容、讲稿、来源和图像归属。
- [x] 旧课/旧 run 正常打开，不切到新版主题或最新 revision。
- [x] 5×9 布局和长内容无水平溢出；手机阅读、桌面讲授无控件遮挡。
- [x] 文字、音频、检查点、断点和显式接管行为通过浏览器回归。
- [x] HTML ZIP、Markdown、打印/PDF 均使用当前选中的 revision。
- [x] 后端、前端构建、E2E 及 `git diff --check` 通过；截图已人工查看。
- [ ] 真实 Azure/图库/联网环境按部署凭证单独烟测；无凭证时禁用对应功能。

本地验收记录（2026-09-27）：后端全套 2269 项通过、4 项跳过；课堂后端
355 项通过；课堂浏览器 44 项通过，最终触控与面板调整后受影响的 23 项
再次通过。前端 TypeScript、ESLint、生产构建、课堂运行时代码构建以及播放
逻辑测试均通过。真实服务烟测属于部署环境步骤，需使用部署凭证执行。

### 备课稳定性后续改进

用户反馈重复点击重试且输出预算耗尽后，增补以下可执行验收项；详细合同见
[`DESIGN.md`](DESIGN.md) P12.14。

- [x] 写作页按剩余页数分配单次输出额度；检索计划、大纲、复核各有独立
  上限，总预算和请求次数上限不变。
- [x] 模型拒收的请求不扣输出 token，结果未知的超时仍保守记账。
- [x] 长要点和口头讲稿在 schema 前无损整理；新提示词不要求不存在的字段。
- [x] 临时服务故障自动续跑并复用已完成页，旧草稿的布局错误本地修正。
- [x] 真实耗尽的任务不再提供无效重试；前端展示错误、已存页面与重新备课入口。
- [x] 单页 JSON 被截断时只续写缺失尾部，并再次校验；预算已不足时即使
  错误类别是 `content_invalid` 也不提供无效重试。

增补后验收：课堂后端 364 项通过；补充提示词版本后的相关后端 52 项通过；
备课、编辑与导出的浏览器回归 15 项通过，前端类型检查、ESLint 与生产
构建通过。未执行真实供应商凭证烟测。

本次 JSON 截断续写改动完成了 Python 语法和 diff 格式检查；尚未对真实
模型服务做在线烟测。
