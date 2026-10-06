# Mobile application

`apps/mobile` 是 Expo SDK 57 / React Native 0.86 的 Android、iOS 应用。Expo Router 负责装配路由；feature 持有原生页面、交互与平台生命周期；服务端拥有学习事实、权限、生成状态和版本。

相关决策：[Expo 单移动代码库](../adr/0008-expo-react-native-mobile.md)、[共享客户端包](../adr/0009-root-monorepo-shared-packages.md)、[轮换身份会话](../adr/0011-tenant-rotating-sessions.md)、[服务端云语音](../adr/0012-cloud-speech-server-mediated.md)。开发见 [mobile-dev](../development/mobile-dev.md)，当前证据见 [mobile-validation](../validation/mobile-validation.md)。

## 代码边界

| 位置            | 职责                                                  |
| --------------- | ----------------------------------------------------- |
| `src/app`       | 路由文件、嵌套 Stack 和稳定的五个一级入口             |
| `src/shell`     | 自适应导航、允许的目标路由、全局助手边缘入口          |
| `src/providers` | 身份、Query 缓存、工作区、主题、语言和全局浮层        |
| `src/platform`  | SecureStore、公开构建配置、非敏感偏好、临时文件和分享 |
| `src/features`  | 聊天、学习、资料、工具和个人页面                      |
| `src/ui`        | 按钮、表单、Sheet、状态、富文本、SVG 与公共布局       |
| `packages/*`    | REST/SSE、公开契约、DAG 布局、设计令牌和文案协议      |

移动端唯一 API 出口是 `lib/api.ts` 的共享 `@next-tutor/api-client`，通过 `expo/fetch` 注入网络适配和客户端平台/版本/构建元数据。请求、公开状态和错误码继续采用 [client-platform](client-platform.md) 定义的协议；原生 UI 只做展示与输入投影。

## 导航与窗口布局

一级入口固定为 Home、Tutor、Learn、Library、Me；每个区域内使用 Stack 展开详情页。Tools 属于 Library。欢迎入口以原生应用插画和登录/注册/游客操作进入，登录后的 Home 依次呈现继续学习、今日任务、最近内容、待复习、学习证据和快捷入口。继续学习按未完成课堂、活动测评、最近辅导、新学习入口的顺序读取服务端状态。

窗口信息来自实时 `useWindowDimensions` 与共享设计令牌：

| 当前宽度（dp） | 布局                                   |
| -------------- | -------------------------------------- |
| <600           | 底部导航、单内容栏、Sheet 展开辅助信息 |
| 600–839        | 窄导航 rail、单内容栏                  |
| 840–1199       | rail、最多两栏                         |
| 1200–1599      | 带标签 rail、最多三栏                  |
| ≥1600          | 最多三栏，阅读内容保持宽度约束         |

高度 <480 dp 强制单内容栏。旋转、折叠或 iPad 分屏通过同一组件树重新布局。图谱始终提供列表入口；会话、资料、笔记和课堂在空间足够时显示列表或检查面板。手机辅助面板通过 Sheet 保持可达。长表单在 Sheet 中编辑，Safe Area 与键盘避让由公共壳处理。

视觉采用暖纸底、青绿主色、系统 sans 字体、清晰边框与留白。按钮和工具操作保留至少 48 dp 的触控范围，正文基准为 16/24，深浅主题使用共享令牌。系统大字体与应用阅读字号同时作用于内容。Reduce Motion 关闭循环/入场和按压位移动效。

导航助手默认以有边框的右侧半隐藏把手贴靠安全边缘。外移部分通过向内扩展的 hitSlop 补足至少 48 dp 可触达区域。打开助手、处于认证页面或未建立身份时隐藏把手。

## 页面与领域语义

| 页面                   | 当前交互边界                                                                          |
| ---------------------- | ------------------------------------------------------------------------------------- |
| Tutor                  | 虚拟消息列表、流式取消、会话管理、来源选择、附件、OCR、原生语音、消息复制/引用/记笔记 |
| Materials / Workspaces | 教材/文件筛选、分页、上传取消、服务端受理状态、原件分享、来源绑定与有权限的管理       |
| Assessment             | 公开 QuestionPublic、V1/V2/V3 选择、服务器反馈/报告；必需题图未就绪时阻断作答         |
| Scenario illustration  | V1/V2/V3、素材引用、公开生成阶段、成功版本、历史来源继续、SVG 缩放/分享               |
| Diagrams               | 公共/内置/个人素材、参数化预览、复制与版本约束编辑                                    |
| Notes                  | vault、文件夹、Markdown 编辑/预览、显式保存、版本、反向链接、复习、导出、归档         |
| Knowledge              | 服务端图谱、共享 DAG 布局、原生 SVG、列表替代、概念详情与跨模块操作                   |
| Plan                   | 今日任务、学习目标、周编排、步骤、建议与复习队列                                      |
| Insights / Memory      | 工作区证据、解释性叙述、带原因的版本约束复核；记忆偏好和策略的可用性状态              |
| Classroom              | 创建/编辑/生成状态、冻结课件版本、原生播放控件、租约、恢复锚点、检查点与课堂笔记      |
| Me                     | 个人资料、头像、设备会话、外观/语言/字号、归档和账户操作                              |

测评与洞察使用公开证据投影，隐藏答案、量规与审核正文由服务端约束。掌握状态使用文字表达当前范围内的证据；单题反馈保留其单题语义。关闭或停止的练习不提供重新生成题图操作。

## 身份、缓存与草稿

- 企业 access token 保存在内存；轮换 refresh token 存入 SecureStore。冷启动先轮换再读取身份，刷新使用 single flight。
- 文件持久化部署兼容原来的 Keychain token。游客 token 只保存在内存，应用终止后结束；正式登录清除游客状态。
- Query key 以 tenant/user 所有者前缀隔离。退出或切换账户取消查询并清除私有缓存、流式任务、工作区、助手、临时文件和内存草稿。异步写入通过 owner/epoch 检查抑制迟到结果。
- AsyncStorage 只保存主题、语言、阅读字号和学段等非敏感偏好。聊天、笔记与助手交接草稿保存在内存，旧版聊天草稿存储在初始化时清理。应用终止会丢失尚未保存的编辑，UI 显示未保存状态并对离开编辑页做确认。
- 受保护深链接在身份建立前保留内存目标；恢复时仅允许已知相对产品路径和 ws/runId/conceptId/q 参数。q 仅用于辅导预填并限制长度。认证信息不进入目标参数。
- AppState 进入 inactive/background 时展示原生全屏隐私遮罩，回到 active 后恢复页面。应用切换器捕获时机与效果需分别在 Android/iOS 设备验证。

笔记提交携带服务端 revision，409 后读取最新版本并同时展示草稿与服务器内容，由用户选择合并基线或服务器版本。情景配图以稳定 request_id、当前 base_revision 和可选 source_revision 提交；素材只传 asset_id/version。网络响应丢失后通过共享客户端恢复同一轮次，409 必须回源；失败仍保留上一成功的公开 SVG。生成状态取自 durable job 读模型。

## 富文本与 SVG

`RichContentRenderer` 保持 Markdown/TeX 源和公共接口。无数学内容使用原生 Text/View，含数学内容按消息分组为一个离线 KaTeX WebView。当前离线资源由 `bundle_math.mjs` 将 KaTeX 与 WOFF2 字体嵌入包中，许可证保存在移动资产目录。

第三方开源许可页（设置入口）列出 apps/mobile 生产依赖子图的全部组件与许可条款：`generate_licenses.mjs` 从仓库许可清单 `licenses/inventory.json` 生成 `licenses.data.json`（按许可表达式去重的完整文本 + 逐包版权行），`--check` 是 CI drift 门禁——清单变更必须与生成数据同 commit。

富文本 AST 序列化转义正文与属性，链接允许 http/https，KaTeX 使用 trust=false 和展开预算。WebView 使用 nonce、禁止外部网络的 CSP、关闭共享 Cookie/普通存储/文件访问，并限制导航。高度与链接消息验证 nonce 后处理；超长内容启用受限内部滚动，流式 HTML 更新节流。原始文本保留为可访问标签。

目前覆盖标题、代码、列表、引用、表格、链接、图片链接降级、行内/块级公式和流式半成品。项目自有 parser 的支持范围受测试约束。200+ 合成格式测试验证解析/转义语义；它们不能建立真机视觉、辅助阅读或 500 条消息性能结论。候选原生 Markdown/数学引擎的设备准入仍待执行，当前实现采用受控离线 fallback。

公开 SVG 由 native SVG 与手势缩放组件展示。客户端限制体积/节点预算并拒绝脚本、foreignObject、事件属性和外部资源。素材科学语义与公开 artifact 冻结由服务端负责。全库存 Android/iOS native 渲染验收仍属于设备发布门。

## 课堂文档与音频

课堂显示同一 revision 的服务端 compiler HTML。外层 WebView 将完整 compiler 文档装入仅 allow-scripts 的 sandbox iframe，保留原 CSP 和脚本哈希；外层 CSP 仅允许自己的 nonce 与 compiler 发布的哈希，禁止外部连接、任意文件和导航。

外层在加载 srcdoc 前安装握手监听，只接受该 iframe 的 classroom_ready 和 compiler nonce，然后通过 MessageChannel 交换页面选择。原生桥包裹独立 nonce；native 再校验消息类型和 slide_id/order 是否属于当前版本。令牌和用户凭据不注入 DOM。课堂检查点引用绑定 question_id 与 question_revision。

播放使用 expo-audio 和服务端音频字节，不通过课件 DOM 处理身份或声音。run 固定开始时的课件 revision，恢复 cursor/resume_anchor；租约、lease_epoch、expected_state_revision、稳定事件标识和顺序控制用于进度写入。后台/前台回源、设备接管与冲突由服务端状态决定。主动播放时开启原生后台/锁屏控制；暂停、完成、退出及账户清理释放播放状态与临时音频。临时录音在完成、取消或退出时清理。云语音能力缺失或麦克风拒绝时保留文字操作。

## 全局助手

助手会话、通知、公开执行状态与 action preview 使用共享协议。目标通过 typed target allowlist 映射为产品路由，工作区和本地预填通过内存交接；页面提交后回执导航结果。服务端写操作先展示变更及副作用，再确认/执行。工作流任务提供预览、批准、停止与服务端允许的重试；通知订阅与回复长度偏好使用服务端接口。当前无法执行的原生命令提供能力状态或原页面入口，避免声称已经完成操作。

## 验收与发布边界

TypeScript、单元/组件测试、YAML 解析和 Metro export 是开发证据。设备 binary 构建、真实 Maestro 执行、Android/iOS 视觉/可访问性、跨窗口和性能门分别记录在 [验收文档](../validation/mobile-validation.md)。生产发布应将原生 binary、Git SHA、许可证/SBOM、设备回归和安全检查绑定到同一版本。
