import { expect, test, type Page } from "@playwright/test";

async function mockSite(page: Page, options: { guest?: boolean; role?: string; theme?: string; cloudVoice?: boolean } = {}) {
  let user = { id: "account-test-user", username: "Learner", email: "learner@example.com", role: options.role ?? "student", created_at: 1790000000, last_login_at: 1790100000,
    profile: { name: "测试同学", grade: "本科", school: "", subjects: ["数学"], avatar: "", prefs: { ocr_parallel: true, tts_speed: 0.9, classroom: { captions: true } } } };
  if (options.cloudVoice) Object.assign(user.profile.prefs.classroom, { voice_policy: "auto", voice_id: "zh-CN-XiaoxiaoNeural" });
  let failSave = false;
  const writes: Record<string, unknown>[] = [];
  let avatarBytes: Buffer | null = null;
  const avatarWrites: Buffer[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(({ guest, theme }) => {
    if (!localStorage.getItem("edu-agent-lang")) localStorage.setItem("edu-agent-lang", "zh");
    if (!localStorage.getItem("edu-agent-theme")) localStorage.setItem("edu-agent-theme", theme);
    if (!guest && !sessionStorage.getItem("mock-account-initialized")) {
      localStorage.setItem("edu-agent-token", "mock-account-token");
      sessionStorage.setItem("mock-account-initialized", "1");
    }
  }, { guest: options.guest ?? false, theme: options.theme ?? "light" });
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/auth/status") return json({ auth_required: false, guest_allowed: true });
    if (path === "/guest/session") return json({ token: "temporary-account-test", expires_in: 1800 });
    if (path === "/guest/textbooks") return json({ items: [] });
    if (path === "/auth/me") return json({ user });
    if (path === "/assistant/capabilities") return json({ enabled: false });
    if (path === "/classroom/capabilities") return json({ enabled: true, tts: { local_enabled: true, voices: [{ voice_id: "zh-CN-XiaoxiaoNeural", display_name: "云端语音" }, { voice_id: "melo-zh", display_name: "本地语音" }] } });
    if (path === "/ux/motivation") return json({ streak_days: 3, active_days: 3, milestones: [3, 7, 14], next_milestone: 7 });
    if (path === "/ux/profile") return json({ event_count: 0 });
    if (path === "/student/profile") return json({ status: "disabled" });
    if (path === "/model-info") return json({ llm_model: "Test model", multimodal_configured: true, voice_models: { local: { model: "MeloTTS-Chinese", enabled: true, voice: "melo-zh", languages: ["zh-CN"] }, cloud: { model: "Azure Speech", configured: true, voices: ["zh-CN-XiaoxiaoNeural", "en-US-JennyNeural"] }, automatic_priority: "local" } });
    if (path === "/chat/sessions/old-chat") return json({ session_id: "old-chat", grade: "高中", messages: [], knowledge_files: [] });
    if (path === "/chat/sessions") return json({ sessions: [] });
    if (path === "/workspaces") return json({ workspaces: [] });
    if (path === "/textbooks") return json({ textbooks: [] });
    if (path === "/library") return json({ folders: [], files: [] });
    if (path === "/ux/greeting") return json({ greeting: "你好" });
    if (path === "/sidebar") return json({ sessions: [], workspaces: [], details: {} });
    if (path === "/auth/logout") return json({ status: "ok" });
    if (path === "/user/avatar") {
      if (route.request().method() === "GET") return avatarBytes ? route.fulfill({ body: avatarBytes, contentType: "image/png" }) : json({}, 404);
      if (route.request().method() === "PUT") {
        if (failSave) return json({ detail: "save_failed" }, 500);
        const raw = route.request().postDataBuffer()!;
        avatarBytes = raw.subarray(raw.indexOf("\r\n\r\n") + 4, raw.lastIndexOf("\r\n--"));
        avatarWrites.push(avatarBytes);
        user.profile.avatar = "avatar:mock-revision";
      } else { avatarBytes = null; user.profile.avatar = ""; }
      return json({ profile: user.profile });
    }
    if (path === "/user/profile") {
      const body = route.request().postDataJSON();
      writes.push(body);
      if (failSave) return json({ detail: "save_failed" }, 500);
      user = { ...user, profile: { ...user.profile, ...body, prefs: { ...user.profile.prefs, ...body.prefs } } };
      return json({ profile: user.profile });
    }
    return json({ status: "disabled" });
  });
  return { writes, avatarWrites, errors, failNextSave: () => { failSave = true; }, recoverSave: () => { failSave = false; } };
}

const menu = (page: Page) => page.getByRole("button", { name: "我的账户" });

test("system theme follows OS changes, explicit choice wins and preference survives reload", async ({ page }) => {
  const { errors } = await mockSite(page, { theme: "system" });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/settings");
  const themes = page.getByRole("group", { name: "主题", exact: true });
  await expect(themes.getByRole("button", { name: "跟随系统", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("html")).toHaveClass(/dark/);
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).not.toHaveClass(/dark/);
  await themes.getByRole("button", { name: "深色", exact: true }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveClass(/dark/);
  await themes.getByRole("button", { name: "跟随系统", exact: true }).click();
  await page.reload();
  await expect(themes.getByRole("button", { name: "跟随系统", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("html")).not.toHaveClass(/dark/);
  expect(errors).toEqual([]);
});

test("account level persists and new chats use it without changing existing chat levels", async ({ page }) => {
  const { writes, errors, failNextSave, recoverSave } = await mockSite(page);
  await page.goto("/settings?section=learning");
  const grades = page.getByRole("group", { name: "默认学段", exact: true });
  failNextSave(); await grades.getByRole("button", { name: "初中", exact: true }).click();
  await expect(page.getByText("保存失败，请重试", { exact: true })).toBeVisible();
  await expect(grades.getByRole("button", { name: "本科", exact: true })).toHaveAttribute("aria-pressed", "true");
  recoverSave(); await grades.getByRole("button", { name: "初中", exact: true }).click();
  await expect(page.getByText("已保存", { exact: true })).toBeVisible();
  expect(writes.at(-1)).toMatchObject({ grade: "初中" });
  await page.reload();
  await expect(grades.getByRole("button", { name: "初中", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.goto("/chat/old-chat");
  await expect(page.getByRole("combobox")).toHaveValue("高中");
  await page.goto("/chat");
  await expect(page.getByRole("combobox")).toHaveValue("初中");
  expect(errors).toEqual([]);
});

test("guests can choose a temporary level without opening personal settings", async ({ page }) => {
  const { writes, errors } = await mockSite(page, { guest: true });
  await page.goto("/chat");
  await page.getByRole("combobox", { name: "学段", exact: true }).selectOption("小学");
  await expect(page.getByRole("combobox", { name: "学段", exact: true })).toHaveValue("小学");
  expect(writes).toHaveLength(0);
  expect(errors).toEqual([]);
});

test("about shows cloud and local voice models and voice defaults to local priority", async ({ page }) => {
  await mockSite(page, { cloudVoice: true });
  await page.goto("/settings?section=about");
  await expect(page.getByText("MeloTTS-Chinese", { exact: true })).toBeVisible();
  await expect(page.getByText("Azure Speech", { exact: true })).toBeVisible();
  await expect(page.getByText("zh-CN-XiaoxiaoNeural / en-US-JennyNeural")).toBeVisible();
  await page.getByRole("link", { name: "语音与课堂", exact: true }).click();
  await expect(page.getByRole("radio", { name: "自动（本地优先）" })).toBeChecked();
  await expect(page.getByRole("combobox")).toHaveValue("melo-zh");
});

async function avatarFile(page: Page) {
  const data = await page.evaluate(() => {
    const canvas = document.createElement("canvas"); canvas.width = 400; canvas.height = 200;
    const context = canvas.getContext("2d")!;
    context.fillStyle = "red"; context.fillRect(0, 0, 200, 200);
    context.fillStyle = "blue"; context.fillRect(200, 0, 200, 200);
    return canvas.toDataURL("image/png").split(",")[1];
  });
  return { name: "avatar.png", mimeType: "image/png", buffer: Buffer.from(data, "base64") };
}

test("avatar crop uploads the selected square, updates private displays and removal clears it", async ({ page }) => {
  const { avatarWrites, errors } = await mockSite(page);
  await page.goto("/account");
  await page.getByLabel("上传头像", { exact: true }).setInputFiles(await avatarFile(page));
  await expect(page.getByText("裁剪头像", { exact: true })).toBeVisible();
  await page.getByRole("slider", { name: "水平位置", exact: true }).focus(); await page.keyboard.press("Home");
  await page.screenshot({ animations: "disabled", path: "../../acceptance-reports/screenshots/avatar-crop-light-1440.png" });
  await page.getByRole("button", { name: "保存头像", exact: true }).click();
  await expect(page.locator("header img[src^='blob:']")).toBeVisible();
  expect(avatarWrites).toHaveLength(1);
  const pixels = await page.evaluate(async (base64) => {
    // Decode the synthetic image locally: fetching data: is intentionally
    // blocked by the application's production connect-src policy.
    const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
    const blob = new Blob([bytes], { type: "image/png" });
    const bitmap = await createImageBitmap(blob);
    const context = new OffscreenCanvas(256, 256).getContext("2d")!;
    context.drawImage(bitmap, 0, 0);
    return { width: bitmap.width, height: bitmap.height, color: [...context.getImageData(128, 128, 1, 1).data] };
  }, avatarWrites[0].toString("base64"));
  expect(pixels).toEqual({ width: 256, height: 256, color: [255, 0, 0, 255] });
  await page.getByRole("button", { name: "移除头像", exact: true }).click();
  await expect(page.locator("header img")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "移除头像", exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("invalid avatar is rejected and failed uploads keep the crop for retry", async ({ page }) => {
  const { avatarWrites, errors, failNextSave, recoverSave } = await mockSite(page, { theme: "dark" });
  await page.goto("/account");
  await page.getByLabel("上传头像", { exact: true }).setInputFiles({ name: "bad.png", mimeType: "image/png", buffer: Buffer.from("invalid") });
  await expect(page.getByText("请选择 5 MB 以内", { exact: false })).toBeVisible();
  await page.getByLabel("上传头像", { exact: true }).setInputFiles(await avatarFile(page));
  await expect(page.getByText("裁剪头像", { exact: true })).toBeVisible();
  await page.screenshot({ animations: "disabled", path: "../../acceptance-reports/screenshots/avatar-crop-dark-1440.png" });
  failNextSave(); await page.getByRole("button", { name: "保存头像", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "保存失败" })).toBeVisible();
  expect(avatarWrites).toHaveLength(0);
  recoverSave(); await page.getByRole("button", { name: "保存头像", exact: true }).click();
  await expect(page.locator("header img[src^='blob:']")).toBeVisible();
  await menu(page).click(); await page.getByRole("menuitem", { name: "退出登录" }).click();
  await expect(page.locator("header img")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("navigation is consolidated and account menu supports keyboard and sign-out", async ({ page }) => {
  const { errors } = await mockSite(page);
  await page.goto("/account");
  await expect(page.locator("header a[href='/dashboard']")).toHaveCount(0);
  await expect(page.locator("aside a[href='/profile'], aside a[href='/insights']")).toHaveCount(0);
  await expect(page.locator("aside a[href='/dashboard']")).toHaveCount(1);
  await expect(page.locator("aside a[href='/settings']")).toHaveCount(0);
  await expect(page.locator("header a[href='/settings']")).toBeVisible();
  await expect(page.locator("header a[href='/docs']")).toBeVisible();
  await expect(page.locator("header a[href='/profile?section=motivation']")).toBeVisible();
  await expect(page.getByText("learner@example.com").first()).toBeVisible();
  await expect(page.getByText("暂无记录")).toHaveCount(0);
  await expect(page.getByText("未填写")).toBeVisible();
  await menu(page).click();
  await page.mouse.move(50, 50);
  const firstItem = page.getByRole("menuitem", { name: "账户资料" });
  await expect(firstItem).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  await page.getByRole("menuitem", { name: "学习画像" }).hover();
  await expect(firstItem).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  await page.keyboard.press("Escape");
  await menu(page).focus(); await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menuitem", { name: "账户资料" })).toBeFocused();
  await expect(firstItem).not.toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menuitem", { name: "学习画像" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(menu(page)).toBeFocused();
  await menu(page).click();
  await page.getByRole("menuitem", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login\?redirect=/);
  await page.goto("/chat");
  await expect(page.getByTestId("guest-chat")).toBeVisible();
  await menu(page).click();
  await expect(page.getByRole("menu")).not.toContainText("learner@example.com");
  await expect(page.getByRole("menuitem", { name: "登录 / 注册" })).toBeVisible();
  expect(errors).toEqual([]);
});

for (const theme of ["light", "dark"]) {
  test(`settings notifications preserve layout and help supports pointer and keyboard (${theme})`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    const { errors, writes, failNextSave, recoverSave } = await mockSite(page, { theme });
    await page.goto("/settings?section=learning");
    const section = page.locator("[data-settings-section]");
    const dimensions = () => section.evaluate((element) => ({ width: element.clientWidth, height: element.clientHeight }));
    const grades = page.getByRole("group", { name: "默认学段", exact: true });
    await expect(grades.getByRole("button", { name: "本科", exact: true })).toHaveAttribute("aria-pressed", "true");
    const before = await dimensions();
    await page.clock.install();
    await grades.getByRole("button", { name: "初中", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText("已保存");
    expect(await dimensions()).toEqual(before);
    await expect(section.getByRole("status")).toHaveCount(0);
    const gradeHelp = page.getByRole("button", { name: "说明：默认学段", exact: true });
    await gradeHelp.hover();
    await expect(page.getByRole("tooltip", { name: /学段保存到你的账户/ })).toBeVisible();
    await page.mouse.move(50, 50);
    const optionHelp = grades.getByRole("button", { name: "说明：初中", exact: true });
    await optionHelp.focus();
    await expect(page.getByRole("tooltip", { name: "新对话按初中阶段组织讲解与练习。", exact: true })).toBeVisible();
    await optionHelp.click();
    expect(writes).toHaveLength(1);
    await page.screenshot({ animations: "disabled", style: "nextjs-portal { display: none; }", path: `../../acceptance-reports/screenshots/settings-feedback-${theme}-1440.png` });
    failNextSave();
    await grades.getByRole("button", { name: "高中", exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: "保存失败，请重试" })).toBeVisible();
    await expect(grades.getByRole("button", { name: "初中", exact: true })).toHaveAttribute("aria-pressed", "true");
    expect(await dimensions()).toEqual(before);
    recoverSave();
    await page.getByRole("button", { name: "关闭提示" }).click();
    await page.goto("/settings?section=processing");
    const ocrBefore = await dimensions();
    await page.getByRole("switch", { name: "教材 OCR 并行加速" }).click();
    await expect(page.getByRole("status")).toHaveText("已保存");
    expect(await dimensions()).toEqual(ocrBefore);
    await page.goto("/settings?section=voice");
    await page.getByText("只看讲稿", { exact: true }).click();
    await expect(page.getByRole("radio", { name: "只看讲稿" })).toBeChecked();
    const voiceBefore = await dimensions();
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByRole("button", { name: "保存", exact: true })).toBeEnabled();
    await expect(page.getByRole("status")).toHaveText("已保存");
    expect(await dimensions()).toEqual(voiceBefore);
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.getByRole("button", { name: "说明：只看讲稿", exact: true }).hover();
    await expect(page.getByRole("tooltip", { name: "只显示课堂讲稿，不生成朗读音频。", exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ animations: "disabled", style: "nextjs-portal { display: none; }", path: `../../acceptance-reports/screenshots/settings-feedback-${theme}-1024.png` });
    await page.clock.fastForward(3_000);
    await expect(page.getByRole("status")).toHaveCount(0);
    expect(errors).toEqual([]);
  });
}

test("assistant setting saves use floating feedback and expose help for each control", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const { errors } = await mockSite(page);
  let prefs = { response_length: "standard", tone: "neutral", default_scope: "follow_page", proactive_enabled: false, revision: 1 };
  let subscriptions: { subscription_id: string; kind: string; local_time: string; weekdays: number[]; enabled: boolean; revision: number }[] = [];
  await page.route("**/api/v1/assistant/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/capabilities")) return route.fulfill({ json: { enabled: true, schema_version: 1, protocol_version: 1, features: {}, limits: {} } });
    if (path.endsWith("/preferences")) {
      if (route.request().method() === "PUT") prefs = { ...prefs, ...route.request().postDataJSON(), revision: prefs.revision + 1 };
      return route.fulfill({ json: prefs });
    }
    if (path.endsWith("/subscriptions")) {
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON();
        const subscription = { subscription_id: "mock-subscription", kind: body.kind, local_time: body.local_time, weekdays: [1, 2, 3, 4, 5], enabled: true, revision: 1 };
        subscriptions.push(subscription);
        return route.fulfill({ json: subscription });
      }
      return route.fulfill({ json: { items: subscriptions } });
    }
    if (path.endsWith("/subscriptions/mock-subscription")) {
      if (route.request().method() === "DELETE") { subscriptions = []; return route.fulfill({ status: 204 }); }
      subscriptions[0] = { ...subscriptions[0], ...route.request().postDataJSON(), revision: subscriptions[0].revision + 1 };
      return route.fulfill({ json: subscriptions[0] });
    }
    return route.fulfill({ json: { items: [], unread_count: 0 } });
  });
  await page.goto("/settings?section=assistant");
  await page.getByRole("combobox", { name: "回答长度", exact: true }).selectOption("detailed");
  await expect(page.getByRole("status")).toHaveText("已保存");
  const section = page.locator("[data-settings-section]");
  await expect(section.getByRole("status")).toHaveCount(0);
  const before = await section.boundingBox();
  await page.getByRole("combobox", { name: "回答语气", exact: true }).selectOption("encouraging");
  await expect(page.getByRole("status")).toHaveText("已保存");
  const after = await section.boundingBox();
  expect(before).not.toBeNull();
  expect(after).not.toBeNull();
  for (const dimension of ["x", "y", "width", "height"] as const) {
    expect(Math.abs(after![dimension] - before![dimension])).toBeLessThan(1);
  }
  for (const label of ["回答长度", "回答语气", "默认查询范围", "主动服务总开关", "订阅管理", "提醒时间", "查看最近投递"]) {
    await page.getByRole("button", { name: `说明：${label}`, exact: true }).focus();
    await expect(page.getByRole("tooltip")).toBeVisible();
  }
  await page.getByRole("button", { name: "添加订阅", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("设置已更新");
  const enabled = page.getByRole("checkbox", { name: "启用", exact: true });
  await expect(enabled).toBeChecked();
  await enabled.click();
  await expect(enabled).not.toBeChecked();
  await expect(page.getByRole("status")).toHaveText("设置已更新");
  await page.getByRole("button", { name: "说明：退订", exact: true }).click();
  expect(subscriptions).toHaveLength(1);
  await page.getByRole("button", { name: "退订", exact: true }).click();
  await expect(enabled).toHaveCount(0);
  await expect(page.getByRole("status")).toHaveText("设置已更新");
  expect(errors).toEqual([]);
});

test("English settings help and consecutive updates retain a single notification until its timer resets", async ({ page }) => {
  const { errors } = await mockSite(page);
  await page.addInitScript(() => localStorage.setItem("edu-agent-lang", "en"));
  await page.goto("/settings");
  await page.getByRole("button", { name: "Help: Theme", exact: true }).focus();
  await expect(page.getByRole("tooltip", { name: /Change the interface colors/ })).toBeVisible();
  await page.clock.install();
  const themes = page.getByRole("group", { name: "Theme", exact: true });
  await themes.getByRole("button", { name: "Dark", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("Settings updated");
  await page.clock.fastForward(2500);
  await themes.getByRole("button", { name: "Light", exact: true }).click();
  await expect(page.getByRole("status")).toHaveCount(1);
  await page.clock.fastForward(1000);
  await expect(page.getByRole("status")).toHaveText("Settings updated");
  await page.clock.fastForward(2000);
  await expect(page.getByRole("status")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("guest login keeps query parameters and account-only preferences stay unavailable", async ({ page }) => {
  await mockSite(page, { guest: true });
  await page.goto("/settings?section=voice");
  await expect(page).toHaveURL(/\/login\?redirect=%2Fsettings%3Fsection%3Dvoice/);
  await expect(page.getByRole("slider")).toHaveCount(0);
});

test("legacy profile links land on their new pages and preferences preserve siblings", async ({ page }) => {
  const { writes, errors, failNextSave } = await mockSite(page);
  await page.goto("/profile?section=account"); await expect(page).toHaveURL(/\/account$/);
  await page.getByRole("button", { name: "编辑资料" }).click();
  await page.getByLabel("姓名", { exact: true }).fill("新名字");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await menu(page).click(); await expect(page.getByRole("menu")).toContainText("新名字"); await page.keyboard.press("Escape");
  await page.goto("/profile?section=voice"); await expect(page).toHaveURL(/\/settings\?section=voice/);
  await page.getByText("只看讲稿", { exact: true }).click();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("已保存", { exact: true })).toBeVisible();
  expect(writes.at(-1)).toMatchObject({ prefs: { classroom: { captions: true, voice_policy: "silent" } } });
  await page.getByRole("link", { name: "资料处理", exact: true }).click();
  const toggle = page.getByRole("switch", { name: "教材 OCR 并行加速" });
  await expect(toggle).toHaveAttribute("aria-checked", "true");
  failNextSave(); await toggle.click();
  await expect(page.getByText("设置失败，请重试")).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-checked", "true");
  expect(errors).toEqual([]);
});

for (const legacy of [false, true]) {
test(`unsaved classroom changes can cancel category and history navigation${legacy ? " (history fallback)" : ""}`, async ({ page }) => {
  if (legacy) await page.addInitScript(() => Object.defineProperty(window, "navigation", { value: undefined }));
  await mockSite(page);
  await page.goto("/settings");
  await page.getByRole("link", { name: "语音与课堂", exact: true }).click();
  await page.getByText("只看讲稿", { exact: true }).click();
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("link", { name: "通用", exact: true }).click();
  await expect(page).toHaveURL(/section=voice/);
  await expect(page.getByRole("radio", { name: "只看讲稿" })).toBeChecked();
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.evaluate(() => window.history.back());
  await expect(page).toHaveURL(/section=voice/);
  await expect(page.getByRole("radio", { name: "只看讲稿" })).toBeChecked();
});
}

for (const theme of ["light", "dark"]) {
  test(`desktop visual ${theme}, expanded rail and narrow window`, async ({ page }) => {
    const { errors } = await mockSite(page, { role: "admin", theme });
    await page.goto("/account");
    await expect(page.getByRole("main").getByRole("heading", { name: "账户资料", exact: true })).toBeVisible();
    await page.screenshot({ animations: "disabled", path: `../../acceptance-reports/screenshots/account-preferences-details-${theme}-1440.png` });
    await page.goto("/settings");
    // The Next.js issues badge overlaps this control in dev; use its keyboard path.
    await page.getByRole("button", { name: "展开导航" }).focus();
    await page.keyboard.press("Enter");
    await menu(page).click();
    await page.screenshot({ animations: "disabled", path: `../../acceptance-reports/screenshots/account-preferences-menu-${theme}-1440.png` });
    await page.keyboard.press("Escape");
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.getByRole("group", { name: "字号", exact: true }).getByRole("button", { name: "特大", exact: true }).click();
    await expect(page.locator("header a[href='/settings']")).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ animations: "disabled", path: `../../acceptance-reports/screenshots/account-preferences-settings-${theme}-1024.png` });
    expect(errors).toEqual([]);
  });
}
