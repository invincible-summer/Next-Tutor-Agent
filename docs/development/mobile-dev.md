# Mobile development

应用事实见 [mobile-app](../architecture/mobile-app.md)，设备门与已执行记录见 [mobile-validation](../validation/mobile-validation.md)。

## 环境

- 使用根 package.json 声明的 Node 22 和 pnpm 11，依赖仅由根 pnpm-lock.yaml 管理。
- Expo SDK、React Native 和 native module 版本按移动 package.json 锁定；新增 Expo module 时与对应 SDK 的 bundledNativeModules 对齐。
- Android 使用 Android Studio/SDK、可用模拟器或 USB 调试设备；iOS 原生编译与 simulator 需要 macOS/Xcode。
- 使用 development client 或 preview binary 验证原生插件。Expo Go 不能替代该应用的设备验收。

从仓库根运行：

```bash
pnpm install --frozen-lockfile
pnpm mobile:start
pnpm mobile:check
pnpm mobile:test --runInBand
```

依赖与项目配置检查使用已锁定的工具：

```bash
pnpm --filter @next-tutor/mobile exec expo install --check
pnpm --filter @next-tutor/mobile exec expo-doctor
```

expo-doctor 是移动 devDependency，固定为 1.20.4 并受根 lockfile 约束；不要通过 npx 获取未锁定版本。SDK check 要求 Expo/RN/native peer 版本兼容，Doctor 检查项目配置、原生依赖及社区包元数据。

## API 地址与身份

公开配置通过构建/启动环境变量注入：

```bash
EXPO_PUBLIC_API_BASE_URL=http://192.168.1.20:8000/api/v1 pnpm mobile:start
```

真机 localhost 指向手机本身。Android USB 开发可先运行 `adb reverse tcp:8000 tcp:8000`，使默认开发地址连到本机 API；iOS/局域网设备使用可达的开发主机地址。确保 API 的认证与 capabilities 配置符合当前测试。生产地址使用 HTTPS；production EAS profile 在配置求值时拒绝非 HTTPS 和 localhost 地址。

EXPO_PUBLIC_* 进入客户端包，只放 API 地址等公开值。模型、对象存储、Azure/其他 speech provider 密钥保持在服务端配置中。冷启动网络失败保留安全存储凭据并展示重试入口；401 按认证流程处理。独立测试账户与数据根避免修改生产学习数据。

## 本地原生构建

安装 SDK/平台工具后，在移动目录执行：

```bash
cd apps/mobile
pnpm exec expo run:android
# 仅 macOS:
pnpm exec expo run:ios
```

这些命令可能生成 android/ios 工程；工程来自 Expo 配置与插件，改动原生依赖/权限后重新生成并构建 development client。不要把一次 Metro JS 导出当作原生 release build。需要模拟器的 runner 应先列出并启动实际安装的 AVD，不能仅凭 adb 可执行就宣称 Android 设备环境可用。

EAS build 配置在 `apps/mobile/eas.json`：

| Profile     | 用途                            |
| ----------- | ------------------------------- |
| development | development client，内部分发    |
| preview     | 内部预览                        |
| e2e         | Android APK、iOS simulator 构建 |
| production  | 正式 channel                    |

装好 EAS CLI 并完成项目/签名配置后，可从移动目录调用 `eas build --profile e2e --platform android` 或对应 iOS 命令。仓库未在应用包中存放签名和商店凭据。当前 runtimeVersion 采用 appVersion policy；原生依赖变更需新 binary 与对应 runtime，OTA 发布与回滚必须经过设备回归和同 SHA 的发布记录。

## Metro 导出

仅检查两平台 JS/assets 打包，将输出放到仓库外：

```bash
pnpm --filter @next-tutor/mobile exec expo export --platform android --output-dir /tmp/next-tutor-mobile-android
pnpm --filter @next-tutor/mobile exec expo export --platform ios --output-dir /tmp/next-tutor-mobile-ios
```

导出不会执行原生模块、系统文件选择器、真实音频、锁屏控制或设备布局。设备能力需安装 binary 检查。

## CNG 与 CI

在可丢弃的 checkout 中生成两平台原生配置，不执行依赖安装或编译：

```bash
pnpm --filter @next-tutor/mobile exec expo prebuild --clean --no-install --platform all
git diff --exit-code -- apps/mobile/package.json pnpm-lock.yaml
```

`--clean` 重建被忽略的 android/ios 工程；如果保留手动原生实验，请先移到工作树外。CNG 不应修改锁定的依赖清单，配置/插件产生的漂移必须修正后再次检查。Linux runner 能完成两平台配置生成；iOS binary 的编译与设备运行仍需 macOS/Xcode。

CI 的必需 `Mobile` job 通过 setup-project 安装 Node/pnpm（python=false），依次执行 SDK check、本地 Doctor、TypeScript、正常退出的 Jest、Android/iOS Hermes export、CNG 和清单漂移检查。`CI result` 将 Mobile 作为 core job 聚合。这个 lane 的结果属于配置和 JS/assets 证据；原生 build、Maestro、设备图像、辅助阅读、后台音频和性能仍按验收文档独立执行。

## 资产生成

品牌图为项目自绘矢量，由 Sharp 生成应用 PNG；修改源脚本后重建：

```bash
pnpm mobile:icons
node scripts/mobile/bundle_math.mjs
```

数学脚本读取移动端已锁定的 KaTeX dist，把 JavaScript 与 WOFF2 字体内嵌至 `src/ui/rich-content/math-assets.ts`，并复制原始 MIT 许可到 `assets/licenses/KaTeX.txt`。升级 KaTeX 后同步再生成，重新检查安全 fixtures、离线显示和依赖声明。不要引入远程 CDN。品牌图也不包含教材或用户内容。

## 编辑与验证

修改 UI 时先检查对应 feature 与 shared contract：

1. 路由文件仅装配页面，交互留在 feature。
2. API 使用共享客户端；生成、掌握与权限状态使用服务端公开投影。
3. 表单使用公共 Input/Field/Sheet；列表分页与空/错/加载状态保留公共组件。
4. 在 390、430、600、768、840、1024、1366 dp 和 <480 dp 高度检查内容可达性；light/dark、zh/en、系统大字与 Reduce Motion 各自检查。
5. 对变化运行 scoped 类型/单元测试，再对合成部署运行对应 Maestro flow。性能、CSP/bridge 和原生视觉依照 [验收矩阵](../validation/mobile-validation.md) 单独记录。

组件测试使用内存 SecureStore/AsyncStorage、原生控件和 WebView/图标边界 mock。不要用 WebView mock 的成功推断真实渲染。测试语料位于移动 tests，必须为项目自编合成内容。测试与截图不得写 production runtime root 或提交用户数据。

Maestro 环境、必要合成内容与命令见 [flows README](../../apps/mobile/.maestro/README.md)。实际报告和截图输出到 /tmp 或 runner artifact 目录，并记录 commit、binary、OS、窗口、主题、语言、字体和执行时间。
