/**
 * Flow 8：Voice 协议冒烟。
 * CI 不测真实麦克风/浏览器 SpeechRecognition，只测协议闭环：
 * ticket 单次消费 -> WS connect -> start -> utterance_end text ->
 * answer_delta/turn_end。TTS provider 走 fake/失败降级（text-only）。
 */
import { test, expect } from "@playwright/test";
import { request as pwRequest } from "@playwright/test";
import { BACKEND, BACKEND_WS, registerAndLogin, loginViaStorage } from "./support/helpers";

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
    let timer: ReturnType<typeof setTimeout>;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error("ws timeout: " + seen.join(","))), 15_000);
    });
    const waitFor = (predicate: () => boolean) => Promise.race([
      new Promise<void>((resolve) => {
        const check = () => {
          if (predicate()) resolve();
          else waiters.push(check);
        };
        check();
      }), timeout,
    ]);
    try {
      await Promise.race([opened, timeout]);
      ws.send(JSON.stringify({ type: "start" }));
      // Acknowledge the actual session binding, rather than guessing 300ms.
      await waitFor(() => seen.includes("session_bound") || seen.includes("error"));
      if (seen.includes("error")) throw new Error("voice session binding failed");
      ws.send(JSON.stringify({ type: "utterance_end", text: "你好老师" }));
      // Observe the complete turn; a first delta alone cannot prove completion.
      await waitFor(() => seen.includes("turn_end") || seen.includes("error"));
    } finally {
      clearTimeout(timer!);
      ws.close();
    }
    return { seen };
  }, { backend: BACKEND, wsBase: BACKEND_WS, token: a.token });

  expect(events.error).toBeUndefined();
  const seen: string[] = events.seen ?? [];
  expect(seen).toContain("stt_start");
  expect(seen).toContain("answer_delta");
  expect(seen).toContain("turn_end");
  expect(seen).not.toContain("error");
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
