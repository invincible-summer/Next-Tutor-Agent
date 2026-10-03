# GitHub Pages 只读演示

演示地址：<https://invincible-summer.github.io/Next-Tutor-Agent/>（canonical base path 为 `/Next-Tutor-Agent`，与仓库名一致；仓库改名前的旧 URL `/The-Next-Tutor-Agent` 由 GitHub 自动重定向）。登录页预填
`example@example.com / example`；这只是选择公开示范账户的本地入口，不提供真实认证或私有账户访问。

演示站使用完整 Next.js 前端，`NEXT_PUBLIC_DEMO_MODE=1` 启用静态导出，
`NEXT_PUBLIC_BASE_PATH=/Next-Tutor-Agent` 设置项目路径。普通后端部署不设置这两个变量，行为保持原样。
界面可切换语言、主题、筛选和分页，可查看历史会话、笔记及修订、学习评价、教材知识图谱、课程和保存的上课记录。
演示请求层在发起网络请求之前拒绝所有数据写入和 AI 调用。
界面不显示常驻演示横幅；尝试 AI、编辑、上传、新建、归档、出题或提交时弹窗说明限制，
操作处理函数在修改本地状态前被拦截。聊天输入只读，课程讲稿沿用普通课堂的 13px 样式。
Pages 与 `./start.sh` 共用紧凑导航及全局字号：默认根字号 16px、导航栏 60px/224px、
品牌栏 56px、收起时导航按钮 36px；不为演示站额外缩放 UI。
课程使用冻结课件与讲稿预览，可切换历史版本、打开全部保存的上课记录并查看进度、课堂笔记和关联答疑。
不创建 Run、不申请租约、不更新进度。笔记以预览模式打开。

**base path 单一事实源**：`.github/workflows/pages.yml` 中的 `NEXT_PUBLIC_BASE_PATH` 环境变量是唯一权威值；本页与 `apps/web/playwright.pages.config.ts`、pages E2E 中的前缀必须与该值保持一致，不得在各处手写不同的仓库名。

## 合成演示数据（synthetic-only）

演示内容**全部为项目自写的合成数据**，唯一来源是 `fixtures/demo/`（见其 README 的
来源声明）：虚构教材库（合成 `.txt` 讲义、虚构书名与 `fx_*` 合成 id）、自写教学对话、
笔记、学习者状态与合成课堂讲义。仓库与 Pages 产物都**不包含任何真实教材、
OCR 文本、切片、向量索引或私有用户数据**，也不提供真实教材下载链接（ADR-0005）。

`scripts/demo/export_pages_demo.py` 在 TemporaryDirectory 沙箱中通过
`tests/storage_sandbox` 的运行根隔离写入：真实后端存储从不被读取。它用内部
store API 播种合成数据，再以 TestClient 捕获只读接口响应、笔记导出与课堂
HTML 帧（走真实渲染管线），输出与旧版 schema v1 兼容的
`apps/web/public/demo/manifest.json`。输出递归去掉内部推理、密码哈希、API 密钥
和 trace id。生成目录 `apps/web/public/demo/`、`out/` 不入版本库。
当前样本为 6 段对话、6 篇笔记、2 个工作区、2 门课程、2 份上课记录。

产物契约由 `scripts/repo/check_pages_artifact.py` 把关：manifest 完整、无
`.pdf/.epub` 等教材格式、无外部教材链接、无私有字段，硬预算 100 MB
（导出器自身在 50 MB 提前失败）。

## 本地构建与验证

先按 [../development/testing.md](../development/testing.md) 安装依赖（课堂帧渲染需要 `pnpm build:classroom` 产物）：

```bash
cd apps/web && pnpm build:classroom && cd ..
python3 -m unittest discover -s scripts/demo -p "test_*.py"
python3 scripts/demo/export_pages_demo.py
python3 scripts/repo/check_pages_artifact.py apps/web/public/demo
cd apps/web
pnpm check
NEXT_PUBLIC_DEMO_MODE=1 NEXT_PUBLIC_BASE_PATH=/Next-Tutor-Agent pnpm build
pnpm test:e2e:pages
```

`pnpm test:e2e:pages` 仅启动 Python 静态文件服务器，检查所有已导出页面的直接访问，
example 登录、只读操作、课件翻页和浅色/深色/窄桌面窗口。报告和截图在 `apps/web/test-results/`。
静态预览可单独运行 `python3 scripts/demo/serve_pages_demo.py`，地址为
`http://127.0.0.1:3040/Next-Tutor-Agent/`。

## CI

`.github/workflows/pages.yml` 在 main 更新或手动触发时先跑仓库卫生 guard 与
fixtures 契约测试，再导出数据、校验产物契约、检查前端、构建、运行静态浏览器
回归，然后上传和发布 Pages。仓库 Settings → Pages 的 Source 必须是 GitHub Actions。
失败时保留浏览器证据，不部署未经验证的产物。
