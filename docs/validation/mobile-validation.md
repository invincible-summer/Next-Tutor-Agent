# Mobile validation

记录日期：2026-10-05。应用架构见 [mobile-app](../architecture/mobile-app.md)，操作步骤见 [mobile-dev](../development/mobile-dev.md)，执行资产见 [Maestro flows](../../apps/mobile/.maestro/README.md)。

## 证据范围

本轮验证属于开发环境验证。这里将代码与打包证据、设备实际执行、尚未执行的发布门分别记录。受控离线 KaTeX fallback 已落地；候选原生 Markdown/数学引擎的视觉、辅助阅读和性能准入尚未完成。

| 检查                         | 当前证据                                                                                                        |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------- |
| TypeScript                   | `pnpm --filter @next-tutor/mobile check` 已执行                                                                 |
| Jest                         | 18 suites / 411 tests 全部通过，进程正常退出                                                                    |
| Markdown/TeX                 | 240 种项目自编的公式 × 格式组合；解析/TeX 保全/HTML 转义测试通过                                                |
| Auth                         | 内存 access、安全 refresh、single flight、logout/account-switch epoch、冷启动、临时失败保留和 legacy token 测试 |
| Navigation                   | 产品 path/参数 allowlist、pending destination 单次恢复、typed assistant target 拒绝外部/未知目标                |
| Classroom                    | compiler HTML/CSP/hash 保全、监听安装顺序、消息 nonce/已知 slide、checkpoint revision 引用                      |
| Native controls              | 必需题图阻断、补充图可答、UTF-8 预算、停止后不可重试、助手半隐藏边框与触控尺寸、AppState 隐私遮罩显示/恢复      |
| Adaptive/theme               | 代表宽度/断点、compact height 单栏、深浅主题令牌、200% 字号/行高纯函数测试                                      |
| Maestro assets               | 18 YAML 已解析且直接 subflow 引用存在；设备运行未执行                                                           |
| Android/iOS Metro export     | Android/iOS Hermes export 均通过；其意义限于 JS/assets 打包                                                     |
| Device binary / screenshots  | 未执行；本机缺少 emulator、Maestro、Xcode 与 EAS CLI，只有 adb                                                  |
| True-device a11y/performance | 未执行                                                                                                          |

单元环境的 SecureStore、AsyncStorage、WebView 与图标使用测试边界 mock。240 组合未进行 Android/iOS 真实视觉比较，不代表 KaTeX 渲染正确性、VoiceOver/TalkBack 读序或 500 消息性能通过。YAML 解析不代表 Maestro command 实际执行。

## 页面覆盖

当前应用提供欢迎/登录/注册、五区导航与工作区/来源，聊天、资源、测评、情景配图、图示库、笔记、知识图谱、计划、洞察/记忆、个人资料/归档/设备会话和课堂 UI。应用范围与后端协议边界见架构文档。

原生 release 需要对每项核心页面实际执行：空、加载、错误、权限拒绝、后台恢复、成功状态和窄/宽窗口。上传只展示真实受理/处理中状态；检查同一账号 Web/Mobile 的服务器数据一致，以及注销后查询/媒体/草稿清理。能力关闭时要有可理解的降级入口。

## 设备矩阵

以下矩阵目前全部待安装 binary 后执行。窗口按运行时尺寸记录；不将品牌型号写成业务分支。

| 窗口/设备                        | 布局重点                                 |
| -------------------------------- | ---------------------------------------- |
| 390×844 类 compact iPhone        | 底部导航、键盘、Sheet、长内容            |
| 430×932 大手机                   | 字号/长标题和触控范围                    |
| Android 中档 compact 真机        | native SVG/WebView、流式列表、录音和内存 |
| 600–839 portrait tablet/折叠展开 | rail、单栏与辅助 Sheet                   |
| 840–1199 landscape tablet        | 两栏、独立滚动和检查面板                 |
| 1024/1366 与 iPad 全屏           | 阅读宽度、两/三栏与列表/详情             |
| iPad half/third window           | 缩窗后隐藏 inspector，已编辑状态保留     |
| phone landscape <480 dp 高度     | 强制单栏、键盘与操作可达                 |

每类至少覆盖 light/dark、zh/en；关键流程再覆盖系统 200%/最大可访问字体与应用阅读字号、VoiceOver/TalkBack、Reduce Motion。检查全文阅读顺序、角色/状态、非颜色信息、概念列表替代、至少 48 dp 工具触控，以及导航助手半隐藏后的可触达区域。

## 可靠性和安全设备场景

- **身份/租户**：同账号重装恢复；refresh rotation；过期的流式请求安全结束、读取 server snapshot；不自动重发用户 turn；logout/换账号后无旧缓存、草稿、媒体或迟到内容。
- **来源/上传**：真实系统选择器、取消/权限拒绝、弱网上传取消、受理状态、失败重试、原件分享后的临时文件清理。
- **测评**：账户默认模式、V1/V2/V3、服务端 combined-review 状态、必需图阻断、补充图文字先行、已停止不触发生成、失败仅对可重试 job 展示操作。
- **情景配图**：V1 不提交素材；V2/V3 仅 id/version；内置/个人选材；响应丢失后同 request_id 恢复；base_revision 冲突回源；以历史 source_revision 修改；失败保留上一次成功图；后台恢复、删除与迟到结果。
- **笔记**：两客户端 409 保留草稿与最新版本；手动合并；离开未保存页确认；版本/反向链接/复习/导出与归档。
- **课堂**：与 Web 同 revision 视觉；运行固定版本；opaque iframe/CSP 在实际 WebView 中可正常握手；未知 nonce/slide/导航拒绝；租约接管、CAS、断点、播放/暂停/后台/锁屏、检查点提交与保存笔记。
- **助手**：所有区域的边缘入口、预览/确认、允许目标导航、页面就绪回执、交接草稿、不支持命令的能力说明；同一请求丢包恢复。
- **语音**：服务端 fake STT→保留文本→发送→native TTS；拒绝麦克风仍能文字交互；provider 超时/关闭有状态；录音取消/完成/logout 清理；客户端无 provider 密钥。
- **跨租户**：高价值 ID 访问由服务器 403/404 拒绝；移动 UI 路由 allowlist 单元测试不能代替服务端权限测试。

## 性能与视觉门

1. 用同一公开 revision 逐张对照 Android/iOS native SVG 与 Web；遍历当前 1,119 个正式素材及参数边界。结构/XML 单元通过不能代替 native 视觉验收。
2. 比较 200+ 合成 Markdown/GFM/TeX fixtures 的两平台截图与辅助阅读顺序，包括长公式、宽表、图片链接降级与流式半成品。
3. 在中档 Android 与 iPhone release binary 展示 500 条混合 Markdown/TeX 消息，记录内存峰值、滚动帧率、OOM/崩溃、消息重排和输入响应。当前 grouped WebView 方案必须单独测量其代价。
4. 验证生成 SVG 手势、课件切页、后台恢复与多栏列表不会导致不可达操作或泄露状态。
5. 记录 release build、SDK/RN/native modules、设备/OS、窗口/主题/语言/字号、fixture 版本和 commit SHA。阈值与异常复现写入实际报告，不用开发机耗时推断真机结论。

## 发布判定

当前**不能据此宣布 Android/iOS 原生生产发布验收通过**。完成设备 build、Maestro 核心/回归、窗口/a11y/performance/security 门后，更新证据与截图目录；将 binary、受保护 commit/tag、CI aggregate、许可证/SBOM 和回归报告绑定到同一 SHA。GitHub source release 与应用商店发布分别记录状态。
