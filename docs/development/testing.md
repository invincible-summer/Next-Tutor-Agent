# 测试与 CI 维护

教学 SVG 素材与出题专项的架构说明见 [architecture/diagrams-illustration.md](../architecture/diagrams-illustration.md)（测评侧合同另见 [architecture/assessment.md](../architecture/assessment.md)），完整库存清单见 [reference/diagram-assets.md](../reference/diagram-assets.md)（脚本生成），当前验收基线见 [validation/diagram-library.md](../validation/diagram-library.md)。

素材改动先从仓库根运行 `python3 scripts/diagrams/build_catalog.py --check`，再在 `services/api` 运行相关回归：

```bash
# 共享目录与兼容题图：
python3 -m tests tests.diagrams.test_diagram_library tests.illustration.test_quiz_illustration tests.illustration.test_quiz_illustration_enrichment
# v2 语义装配、真实浏览器 PNG、发布门和鉴权任务生命周期：
python3 -m tests tests.illustration.test_illustration_v2 tests.illustration.test_illustration_jobs
# 全库适配、科学模型与素材子提示词：
python3 -m tests tests.diagrams.test_diagram_adapters tests.diagrams.test_diagram_expansion tests.diagrams.test_diagram_guidance
```

这些测试使用合成材料、fake LLM 和临时存储，v2 的测量与 PNG 仍调用实际 Node/Chromium，不需要真实模型凭证。新增素材必须生成目录与库存、检查两种风格；加入 v2 还须覆盖真实参数、动态端口/区域、事实绑定和科学关系，不能只验证 XML 可解析。

在 `apps/web` 运行 `node scripts/check-diagram-library.mjs /tmp/diagram-review`，检查全部正式素材的 Chromium 渲染与画布边界，逐张观看 `sheet-*.png` 和 `monochrome-*.png`；可追加 `--parameters` 检查参数端点。越界会返回失败，`review.json` 记录数量、边界问题和被参数约束拒绝的组合。人工审阅还要核对变形、位置、接头、刻度、液面和遮挡，自动通过不等于科学构图正确。

前端改动继续执行 `pnpm check`、自动生产构建和 `pnpm test:e2e tests/e2e/diagram-library.spec.ts tests/e2e/quiz-illustration.spec.ts`。

E2E 只保留一个运行机制：`cd apps/web && pnpm test:e2e`。

- 固定生产构建、单 worker、零重试。本地和 CI 使用同一配置；选择 spec、`--grep` 或 `--headed` 只改变测试范围/显示方式。移除独立 CI/i18n/live 配置与 DEV/PRODUCTION/FRESH/WORKERS 切换。静态 Pages 产物通过 `pnpm test:pages` 单独验收；真实模型管线使用下文的 Python 验收脚本。
- `run-e2e.mjs` 每次创建独立临时数据根，直接运行当前 backend 源码。keyless 模式不加载后端 `.env`，provider 指向 fake LLM；trace/vector 根也在临时目录。正常结束、启动失败、SIGINT/SIGTERM 时，先停止全部服务进程组再清理本次数据。运行锁阻止两次产品 E2E 争用构建缓存/报告。
- `build-front.mjs` 校验源码、public 资源、构建脚本、Next/TS 配置、包与依赖锁、环境文件及构建变量的内容哈希。只有完全一致才复用独立 `.next-e2e`，失败构建不写有效缓存标记；不影响普通 `.next`，不需要预先 `pnpm build`，不再重复构建。
- 服务就绪等待：fake LLM `/health`、backend `/api/v1/ready`、frontend `/login`。默认端口 8199 / 8124 / 3030，占用时自动选择空闲端口；显式指定 `E2E_LLM_PORT` / `E2E_BACKEND_PORT` / `E2E_FRONTEND_PORT`，Python 用 `E2E_PYTHON`。不再复制源码或删除固定 `/tmp` 目录。
- 一般测试 45 秒、断言 8 秒、动作 8 秒、导航 15 秒；本地 fake-provider 管线等待最多 20 秒。遍历全部真实图库的单项预算 120 秒。全套测试（含服务启动之后的浏览器运行）10 分钟，失败达到 5 项停止；编译独立限制 4 分钟。通知消失和缺失目标的就绪超时使用 Playwright 时钟触发，异步配图使用受控任务响应，保留异步反馈、音频暂停等有行为意义的等待，不降低权限/持久化/教学断言，不用自动重试掩盖失败。
- 浏览器回归使用独立账号，公有列表按 asset-id 或返回的总数验证；仍遍历全部素材分页、验证图片和参数变化，并保存浅色、深色、较窄桌面截图。测试缓存、v2 job/artifact/PNG 不得写生产根。

2026-10-04 本机复验：完整 119 项全部通过，零跳过、零重试；缓存命中后浏览器约 3 分 11 秒，含准备、服务启动和清理的完整命令约 3 分 20 秒。通知、缺失目标、学习计划和异步配图重点用例各连续通过 3 次（共 15 项）；启动失败和 SIGTERM 的进程/数据清理已验证。耗时为本机记录，CI 仍按上述固定预算执行。

## 真实 v2 配图验收

代码默认 `QUIZ_ILLUSTRATION_PIPELINE=shadow`；真实多场景配图验收与全目录审查已完成，当前基线见 [validation/diagram-library.md](../validation/diagram-library.md)。fake LLM 回归、目录结构检查或单轮成功不能作为真实模型全部通过或生产切换的结论。

先完成下面的 Node/Chromium 准备，并在本地 shell 或仓库根 `.env` 配置实际 quiz provider。显式从仓库根运行：

```bash
python3 scripts/acceptance/illustration/live.py --live-llm \
  --output /tmp/illustration-v2-round-1
# 对读数、角度、模态和概率等使用另一组数值：
python3 scripts/acceptance/illustration/live.py --live-llm --variation 1 \
  --output /tmp/illustration-v2-round-2
```

脚本只在这次验收进程中启用 `v2` 与 `active` 审核，以真实 provider 调用合成题目；运行根为自动清理的 `TemporaryDirectory`。输出目录必须位于仓库外，包含每次调用的答案通道 JSON、场景 SVG、最终与中间实际 PNG 和 `report.json`，不保存原始推理或凭证。

默认依次运行 24 类情境：原有 14 类装置/读数/几何/学科题，加气柱驻波、水合壳、上升流、DNA 复制、谱线红移、单位圆、条件概率、电桥、膜分离和水培循环。`--cases` 可选择单轮范围；学科扩充采用高中语境，示意像素不作为物理条件。调整模型或素材后，先复验失败场景，再复跑其余场景和另一组数值。

多轮复验使用不同输出目录保留每轮材料。逐张检查题意、仪器读数及单位、支撑/连接/浸没关系、遮挡、文字可读性和答案/量规一致性；检查必要图题具备完整、已审核材料，补充图没有增加条件，失败时没有交付半成品。报告的 `passed` 表示该轮产生了通过当前生成审核门的题目，仍须结合截图和题目内容确认效果；脚本不启动产品前后端，题目注册、任务权限和历史恢复另由 API 与浏览器回归验证。

冻结文字题补图使用 `python3 scripts/acceptance/illustration/cat.py --live-llm --mode both --output /tmp/cat-illustration-review`；`--variation 1` 改变科学数值与数据，`--cases` 限定合成情景。V1 启用实际 PNG 语义审查，V2 从空合同提取材料并走真实 Chromium/PNG 双审，能力门读取底层配置，全部存储隔离；任何失败返回非零退出码。不会生成新题或写生产评价数据。通用布局规则的离线回归为 `tests.diagrams.test_legacy_layout`，不能用某张图返回 ready 代替不同素材的空间关系和参数绑定检查。

已移除旧的同步 CAT/三调用 live 浏览器配置；真实 V1/V2 管线由上述隔离 Python 脚本验收，浏览器套件继续验证文字题先可答和异步题图到达。普通 CI 保持 keyless，只运行 fake provider 与离线浏览器；真实模型测试需显式本地执行。前端 unit 覆盖请求时限、同题单请求、服务器已完成后的恢复、换号及迟到结果，见 `tests/unit/test-illustration-enrichment.mjs`。

## 执行分层

普通提交只运行一套必需检查，耗时较大的可选能力放在独立回归中。两套工作流都不使用真实模型凭证，也不向生产存储写入数据。

| 工作流 / 检查 | 触发条件 | 内容 | 是否阻止合并 |
| --- | --- | --- | --- |
| `CI` / `Repository hygiene` | PR → main、main push、手动 | 仓库卫生 guard：tracked 文件与全历史禁教材/派生数据/运行根、大文件门禁、fixtures synthetic 契约；文档 guard（链接/布局/生成目录一致）。`--check-generated` 会经 `build_catalog.py` 导入后端代码，因此该 job 先安装 `requirements.txt` | 是（首个 job，失败阻断后续） |
| `CI` / `Plan backend shards` | 同上 | 运行 `scripts/repo/plan_backend_shards.py` 枚举 `services/api/tests` 并生成分片矩阵；新增测试域未登记时响亮失败 | 是 |
| `CI` / `Backend (<shard>)` | 同上 | BM25 环境的全部后端 unittest，按域拆成并行分片（矩阵）。渲染域（classroom、diagrams+illustration、api/core/identity/notes/voice 平台片）安装完整前端工具链与 Chromium，agents 各片仅装 Python 依赖 | 是 |
| `CI` / `Frontend and smoke` | 同上 | TypeScript、ESLint、Node 单元测试脚本、生产构建、关键浏览器旅程 | 是 |
| `CI` / `CI result` | 上述 job 全部完成后 | 仅当全部 job（含每个 backend 分片）都成功才成功；失败、取消、跳过均不能冒充通过 | **main 唯一 required check** |
| `Extended regression` / `Optional vector backend` | 每周一 02:17（UTC+8）、手动 | 安装 Chroma 向量依赖，运行 local RAG / hybrid RAG 回归 | 否，发布更新前检查 |
| `Extended regression` / `Full browser regression` | 同上 | 生产构建上的完整浏览器套件，包含编辑、恢复、冲突、语音、助手等路径 | 否，发布更新前检查 |

历史 tag 不触发 CI（工作流只监听 PR 与 main push），旧布局的里程碑 tag 因此不会再触发流水线。

backend 分片各限时 20 分钟，frontend 连同冒烟最多 25 分钟。浏览器使用同一 runner，冒烟和全量各最多 10 分钟，生产编译最多 4 分钟，完整浏览器 job 连同安装最多 25 分钟。超时是故障信号，应查看具体步骤和 trace，不能靠无限延长上限解决。

主分支保护使用 `CI result`，要求 PR，但不要求人工批准；管理员保留应急绕过能力。不要求每个 PR 都同步到 main 最新提交，不增加覆盖率百分比、操作系统矩阵或新的静态检查工具。

## 环境与准备

CI 使用 Ubuntu 24.04、Python 3.11、Node.js 22，pnpm 版本由 `apps/web/package.json` 的 `packageManager` 固定。Python 约束在 `services/api/constraints.txt`，前端依赖按 `pnpm-lock.yaml` 安装。共享准备步骤在 `.github/actions/setup-project/action.yml`，以布尔 inputs（`python`/`node`/`playwright`/`classroom`）声明各 job 实际需要的环境，避免后端纯 Python 分片安装前端工具链；Playwright Chromium 按 pnpm-lock 哈希缓存。浏览器证据统一经 `.github/actions/upload-browser-report` 上传。

从仓库根目录准备一个独立 Python 环境（以下以 Linux 为例）：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r services/api/requirements.txt -r services/api/requirements-test.txt
cd apps/web
pnpm install --frozen-lockfile
pnpm exec playwright install --with-deps chromium
pnpm build:classroom
```

后端课堂和 v2 题图回归都会调用真实 Node/Chromium 渲染器，须安装前端依赖与浏览器；课堂还需离线课件资源。缺失资源会导致 `renderer_unavailable` 或 `preview_unavailable`，不能用占位图片计为审核通过。`requirements-test.txt` 只包含轻量测试依赖；运行向量回归时额外安装 `requirements-vector.txt`。

## 本地命令

在已激活的 Python 环境中，从仓库根目录执行：

```bash
python scripts/repo/check_repository_hygiene.py
cd services/api
python -m tests
# 按模块或类运行，仍保留完整存储沙箱：
python -m tests tests.agents.learning_orchestration.test_api.TestAPIContracts
```

`python -m tests` 基于标准库 unittest，增加进程级临时存储兜底，并在退出时清理临时文件。每个涉及存储的测试仍须使用 `StorageSandboxTestCase`，或在有自定义基类时调用 `patch_all_storage_roots`；兜底不能替代用例之间的隔离。不要直接对运行中的服务执行测试。

前端检查与 CI 一致：

```bash
cd apps/web
pnpm check
# 完整浏览器回归，自动构建并复用经校验的缓存：
pnpm test:e2e
# 同一入口选定关键旅程：
pnpm test:e2e tests/e2e/auth-isolation.spec.ts tests/e2e/textbook-bm25.spec.ts \
  tests/e2e/strict-qa.spec.ts tests/e2e/grounded-quiz.spec.ts \
  tests/e2e/notes.spec.ts tests/e2e/classroom-workflow.spec.ts
```

`pnpm check` 包含类型检查、lint，以及播放器、i18n、助手导航、Pages 只读请求适配、题图恢复，以及 E2E 缓存/端口契约的轻量单元测试。`pnpm build` 统一使用 webpack 生产构建。生产模式的后端地址必须在**构建时**设置，不能仅在 `next start` 时改变。

`GitHub Pages demo` 工作流单独验证静态演示：从 `fixtures/demo/`
合成数据导出只读快照（临时沙箱，不读任何真实运行数据），
校验产物契约，构建完整静态前端（`next build` 内置完整 TypeScript 检查），并通过 `pnpm test:pages`
在纯文件服务器上检查全部导出页面、只读操作和课件翻页。通过后才发布 Pages，不依赖运行中的 API 或模型；
仓库卫生 guard 与 `pnpm check` 不在该工作流重复执行（同一次 push 的 `CI` 已覆盖）。
详情见 [`operations/pages-demo.md`](../operations/pages-demo.md)。

`pnpm test:e2e --list` 只枚举测试，不启动服务或构建。单文件调试仍走同一生产 runner，例如 `pnpm test:e2e tests/e2e/i18n.spec.ts --headed`；不需要预设模式变量。

向量回归：

```bash
python -m pip install -r services/api/requirements-vector.txt
cd services/api
python -m tests tests.agents.knowledge.test_local_rag tests.agents.knowledge.test_rag_hybrid
```

浏览器失败后查看 `apps/web/playwright-report/` 和 `apps/web/test-results/`。GitHub 上传 HTML 报告、截图和 trace，保留 7 天。本地和 CI 均不重试浏览器用例；`test-results/e2e-results.json` 记录耗时、跳过与失败数量。失败保留报告与 trace，定位具体响应/状态，禁止通过延长预算或自动重试掩盖问题。

## 保留与删减规则

- 保留鉴权、越权、跨用户/工作区隔离、持久化、核心教学链路、课堂发布/恢复/冲突和降级行为。测试数量不设人为上限。
- 按行为领域组织文件。同一领域的小文件合并，但保留不同边界条件；同样的 fixture、调用、断言才视为重复用例。
- 日常浏览器冒烟只包含 `auth-isolation`、`textbook-bm25`、`strict-qa`、`grounded-quiz`、`notes`、`classroom-workflow` 六个文件。新增 spec 默认进入完整回归；只有关键用户旅程才加入冒烟白名单。
- 删除只检验过时迁移完成状态、私人且未提交的运维文档、手写假界面样式或旧调试截图的测试。仓库安全检查继续保护运行数据边界（任何教材/派生数据不得入库）和单 worker 部署约束。
- 暂不维护手机端适配矩阵。真实课堂 HTML 的浅色/深色与桌面窄窗口检查继续保留。
- 浏览器模拟接口须遵循当前路由和数据状态；API 错误必须使测试失败，不能吞错或只检查 body 可见。等待可见状态/响应，避免立即读取异步数组或动画中间值。真实身份测试使用真实 JWT；模拟身份只能用于明确模拟了接口的用例。

本次整理合并了 12 个后端文件：prompt memory 生命周期相关的 3 个文件、workspace memory boundary、model info、navigation file preview、round count、session material cleanup、material retrieval、quiz strict relevance，以及教材 OCR/quality API。删除一个重复的无目标计划用例、两项依赖未提交运维文档的断言、重复的本人教材浏览用例、旧课堂假 HTML 视觉套件和 7 个硬编码调试脚本。对应的领域行为断言迁入现有领域文件；可选向量回归不再重复整套鉴权/部署检查。

| 保留的后端文件 | 合并进入的旧文件（均在 `services/api/tests/`） |
| --- | --- |
| `test_prompt_memory_lifecycle.py` | `test_memory_safety.py`、`test_legacy_prompt_memory.py`、`test_lifecycle_contracts.py` |
| `test_workspace_isolation.py` | `test_workspace_memory_boundary.py` |
| `test_bootstrap_readiness.py` | `test_model_info.py` |
| `test_library.py` | `test_navigation_file_preview.py` |
| `test_session_isolation.py` | `test_round_count.py`、`test_session_material_cleanup.py` |
| `test_rag_v2.py` | `test_material_retrieval.py` |
| `test_quiz_grounding.py` | `test_quiz_grounding_strict_relevance.py` |
| `test_textbook_api.py` | `test_textbook_ocr_api.py`、`test_textbook_quality_api.py` |

完整回归还修复了生产模式下 PDF 预览关闭/翻页时查询状态不同步、导出错误提示遮挡重试菜单两处界面问题。预览页码使用与 `useSearchParams` 同步的原生 history 更新（[Next.js 官方说明](https://nextjs.org/docs/app/getting-started/linking-and-navigating#native-history-api)）；真实交互回归继续检查关闭、重新导航和浏览器历史。

## 发布与 Pages 更新顺序

1. 先运行修改领域的测试，再运行后端完整套件、前端检查/生产构建、冒烟、完整浏览器和向量回归；最后检查 `git diff --check`。
2. 提交改动，确认目标提交的 GitHub `CI` 成功。在默认分支上手动运行 `Extended regression`，核对它与目标提交 SHA 一致。
3. main 只绑定 `CI result`，避免已删除的旧 job 名称留下永远 pending 的 required checks。
4. `GitHub Pages demo` 工作流在 main push 后自动重建静态演示站；产物契约（大小、无教材格式、manifest 完整）在 CI 内强制校验。

版本 tag（v1.0.0–v2.1.0）是版权清洗后的历史里程碑，指向旧布局的净化快照：不移动、不重打，也不要求其通过当前 CI（工作流不监听 tag）。日常开发不自动移动发布标签。

素材创作重点回归：`cd services/api && python3 -m tests tests.diagrams.test_diagram_materials tests.diagrams.test_diagram_guidance tests.illustration.test_illustration_jobs tests.identity.test_guest_access`；前端 `pnpm check` 后运行 `pnpm test:e2e tests/e2e/diagram-materials.spec.ts tests/e2e/quiz-illustration.spec.ts`。浏览器隔离后端引导合成管理员，验证公有发布、普通用户403/只读历史与私有列表隔离；图片渲染脚本和依赖同样位于隔离架构。

真实素材生成与使用验收：`python3 scripts/acceptance/illustration/materials.py --live-llm --output /tmp/material-live-acceptance`。它创建隔离临时运行根，实际调用配置服务生成容器/几何/流程草稿、手动修改、冻结新版本并使用指定素材出题，两项审图均看真实PNG。浏览器AI草稿替身不计为该服务验收，详细结果见[验收记录](../validation/diagram-library.md)。
