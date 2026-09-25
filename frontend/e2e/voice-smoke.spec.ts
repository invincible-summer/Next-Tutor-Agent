/**
 * Flow 8（plan.md §26）：Voice 协议冒烟。
 * CI 不测真实麦克风/浏览器 SpeechRecognition，只测协议闭环：
 * ticket 单次消费 -> WS connect -> start -> utterance_end text ->
 * answer_delta/turn_end。TTS provider 走 fake/失败降级（text-only）。
 */
import { test, expect } from "@playwright/test";
import { request as pwRequest } from "@playwright/test";
import { BACKEND, BACKEND_WS, registerAndLogin, loginViaStorage } from "./helpers";

test("voice ticket -> WS 协议闭环", async ({ page }) => {
  const api = await pwRequest.newContext();
  const a = await registerAndLogin(api);

  // UI：打开语音层所在的聊天页（真实前端加载 voice 客户端代码）
  await loginViaStorage(page, a.token);
  await page.goto("/chat");
  await expect(page.locator("textarea").first())
    .toBeVisible({ timeout: 20_000 });

  // 协议冒烟（页面上下文里执行 WS 握手，复用已登录 cookie/token 语义）
  const events = await page.evaluate(async ({ backend, wsBase, token }) => {
    const t = await fetch(`${backend}/api/v1/voice/ticket`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`,
                 "Content-Type": "application/json" },
      body: "{}",
    }).then((r) => r.json());
    if (!t.ticket) return { error: "no ticket", body: t };
    const ws = new WebSocket(
      wsBase + "/api/v1/voice/ws?ticket=" + encodeURIComponent(t.ticket));
    const seen: string[] = [];
    const waiters: Array<() => void> = [];
    let resolveOpen: () => void;
    const opened = new Promise<void>((r) => (resolveOpen = r));
    ws.onopen = () => resolveOpen();
    ws.onmessage = (ev) => {
      const kind = JSON.parse(ev.data).type;
      seen.push(kind);
      waiters.splice(0).forEach((w) => w());
    };
    const timeout = new Promise((_, rej) =>
      setTimeout(() => rej(new Error("ws timeout: " + seen.join(","))), 45_000));
    await Promise.race([opened, timeout]);
    ws.send(JSON.stringify({ type: "start" }));
    // 浏览器 STT 不在 CI 里跑：直接注入最终识别文本（协议与生产一致）
    await new Promise((r) => setTimeout(r, 300));
    ws.send(JSON.stringify({ type: "utterance_end", text: "你好老师" }));
    // 等回答通道事件（answer_delta 或 turn_end；TTS 失败是合法 text-only 降级）
    await Promise.race([
      new Promise<void>((resolve) => {
        const check = () => {
          if (seen.some((k) => k === "turn_end" || k === "answer_delta"
                        || k === "error")) resolve();
          else waiters.push(check);
        };
        check();
      }),
      timeout,
    ]);
    try { ws.close(); } catch { /* already closed */ }
    return { seen };
  }, { backend: BACKEND, wsBase: BACKEND_WS, token: a.token });

  expect(events.error).toBeUndefined();
  const seen: string[] = events.seen ?? [];
  expect(seen).toContain("stt_start");
  expect(seen.some((k) =>
    k === "turn_end" || k === "answer_delta" || k === "tts_start")).toBeTruthy();
  await api.dispose();
});

test("voice ticket 单次消费（重放被拒）", async ({ page }) => {
  const api = await pwRequest.newContext();
  const a = await registerAndLogin(api);
  await loginViaStorage(page, a.token);
  await page.goto("/chat");
  const result = await page.evaluate(async ({ backend, wsBase, token }) => {
    const t = await fetch(`${backend}/api/v1/voice/ticket`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`,
                 "Content-Type": "application/json" },
      body: "{}",
    }).then((r) => r.json());
    if (!t.ticket) return { error: "no ticket" };
    const url = wsBase + "/api/v1/voice/ws?ticket=" + encodeURIComponent(t.ticket);
    const first = new Promise<number>((resolve) => {
      const ws = new WebSocket(url);
      ws.onopen = () => { resolve(0); ws.close(); };
      ws.onerror = () => resolve(-1);
    });
    const okFirst = await first;
    const second = new Promise<number>((resolve) => {
      const ws2 = new WebSocket(url);
      ws2.onopen = () => { resolve(0); ws2.close(); };
      ws2.onerror = () => resolve(-1);
      ws2.onclose = (ev: CloseEvent) => resolve(ev.code);
    });
    const okSecond = await second;
    return { okFirst, okSecond };
  }, { backend: BACKEND, wsBase: BACKEND_WS, token: a.token });
  // 第一次连接成功；同一 ticket 的第二次连接被拒（单次消费）
  expect(result.error).toBeUndefined();
  expect(result.okFirst).toBe(0);
  expect(result.okSecond).not.toBe(0);
  await api.dispose();
});
