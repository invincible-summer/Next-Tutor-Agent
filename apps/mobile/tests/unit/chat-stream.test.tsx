import React from "react";
import { renderHook, act } from "@testing-library/react-native";

import type { ApiClient, ChatStreamEvent } from "@next-tutor/api-client";

import { useChatStream } from "@/features/chat/hooks/useChatStream";
import { resetChatStore, useChatStore } from "@/features/chat/store/chatStore";
import { I18nProvider } from "@/providers/I18nProvider";

function wrapper({ children }: { children: React.ReactNode }) {
  return <I18nProvider>{children}</I18nProvider>;
}

/** 手工推进的 SSE 事件队列：push 事件、end 结束、fail 抛错。 */
function controllableStream() {
  const queue: (ChatStreamEvent | Error | null)[] = [];
  let notify: (() => void) | null = null;
  const stream: AsyncGenerator<ChatStreamEvent> = (async function* () {
    for (;;) {
      while (queue.length > 0) {
        const item = queue.shift()!;
        if (item === null) return;
        if (item instanceof Error) throw item;
        yield item;
      }
      await new Promise<void>((resolve) => {
        notify = resolve;
      });
      notify = null;
    }
  })();
  return {
    stream,
    push: (...events: ChatStreamEvent[]) => {
      queue.push(...events);
      notify?.();
    },
    fail: (error: Error) => {
      queue.push(error);
      notify?.();
    },
    end: () => {
      queue.push(null);
      notify?.();
    },
  };
}

function fakeClient(events: ReturnType<typeof controllableStream>) {
  const streamCalls: unknown[] = [];
  const client = {
    chat: {
      stream: (body: unknown, _signal?: unknown) => {
        streamCalls.push(body);
        return events.stream;
      },
      listSessions: async () => ({ sessions: [] }),
      attachLibraryFiles: async () => ({ results: [], session_id: "s-x" }),
    },
  } as unknown as ApiClient;
  return { client, streamCalls };
}

async function flushTimers(ms = 60) {
  await act(async () => {
    jest.advanceTimersByTime(ms);
    await Promise.resolve();
  });
}

describe("useChatStream", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    resetChatStore();
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  test("完整流：answer 聚合 → done 提交 assistant 消息并绑定 session", async () => {
    const events = controllableStream();
    const { client, streamCalls } = fakeClient(events);
    const bound: string[] = [];
    const { result } = await renderHook(() => useChatStream(client), {
      wrapper,
    });

    let sent: Promise<void>;
    await act(async () => {
      sent = result.current.send("你好", undefined, {
        onSessionBound: (sid) => bound.push(sid),
      });
      await Promise.resolve();
    });
    expect(useChatStore.getState().streaming).toBe(true);
    expect(useChatStore.getState().messages[0]).toMatchObject({
      role: "user",
      content: "你好",
    });
    expect(streamCalls).toHaveLength(1);

    await act(async () => {
      events.push(
        { type: "step", step: "thinking" },
        { type: "answer", delta: "你" },
        { type: "answer", delta: "好" },
      );
      await Promise.resolve();
    });
    await flushTimers();
    expect(useChatStore.getState().pendingAnswer).toBe("你好");

    await act(async () => {
      events.push(
        { type: "done", session_id: "s-1" },
        { type: "history_saved", session_id: "s-1" },
      );
      events.end();
      await Promise.resolve();
      await sent;
    });
    const s = useChatStore.getState();
    expect(s.streaming).toBe(false);
    expect(s.messages).toHaveLength(2);
    expect(s.messages[1]).toMatchObject({ role: "assistant", content: "你好" });
    expect(s.sessionId).toBe("s-1");
    expect(bound).toEqual(["s-1"]);
  });

  test("工具事件序：tool_start → progress → result 落到同一张卡", async () => {
    const events = controllableStream();
    const { client } = fakeClient(events);
    const { result } = await renderHook(() => useChatStream(client), {
      wrapper,
    });

    let sent: Promise<void>;
    await act(async () => {
      sent = result.current.send("出题");
      await Promise.resolve();
    });
    await act(async () => {
      events.push(
        { type: "tool_start", tool: "generate_quiz" },
        { type: "tool_progress", message: "检索中" } as ChatStreamEvent,
        {
          type: "tool_result",
          tool: "generate_quiz",
          result: { status: "success" },
        } as never,
        { type: "answer", delta: "做好了" },
        { type: "done", session_id: "s-2" },
      );
      events.end();
      await Promise.resolve();
      await sent;
    });
    const msg = useChatStore.getState().messages[1];
    expect(msg?.toolCalls).toHaveLength(1);
    expect(msg?.toolCalls?.[0]).toMatchObject({ name: "generate_quiz" });
    expect(msg?.content).toBe("做好了");
  });

  test("世代守卫：流式中 newChat 后 done 的残余写入被丢弃", async () => {
    const events = controllableStream();
    const { client } = fakeClient(events);
    const { result } = await renderHook(() => useChatStream(client), {
      wrapper,
    });

    let sent: Promise<void>;
    await act(async () => {
      sent = result.current.send("旧会话消息");
      await Promise.resolve();
    });
    await act(async () => {
      events.push({ type: "answer", delta: "旧回答" });
      await Promise.resolve();
    });
    await flushTimers();
    // 切走会话（世代 +1）
    await act(async () => {
      useChatStore.getState().aborter?.abort();
      useChatStore.getState().newChat();
      events.push({ type: "done", session_id: "s-old" });
      await Promise.resolve();
      await sent;
    });
    const s = useChatStore.getState();
    expect(s.sessionId).toBeNull();
    expect(s.messages).toHaveLength(0);
  });

  test("error 事件写成 assistant 错误消息", async () => {
    const events = controllableStream();
    const { client } = fakeClient(events);
    const { result } = await renderHook(() => useChatStream(client), {
      wrapper,
    });

    let sent: Promise<void>;
    await act(async () => {
      sent = result.current.send("hi");
      await Promise.resolve();
    });
    await act(async () => {
      events.push({ type: "error", code: "rate_limited", message: "太快了" });
      await Promise.resolve();
      await sent;
    });
    const s = useChatStore.getState();
    expect(s.streaming).toBe(false);
    const last = s.messages[s.messages.length - 1];
    expect(last?.role).toBe("assistant");
    expect(last?.content).toContain("太快了");
  });

  test("手动 stop：abort 提交部分回答", async () => {
    const events = controllableStream();
    // abort 时让 generator 抛 AbortError（模拟 expo/fetch 语义）
    const client = {
      chat: {
        stream: (_body: unknown, signal?: AbortSignal | null) =>
          (async function* (): AsyncGenerator<ChatStreamEvent> {
            yield { type: "answer", delta: "部分" };
            await new Promise((_, reject) => {
              signal?.addEventListener("abort", () => {
                const err = new Error("aborted");
                err.name = "AbortError";
                reject(err);
              });
            });
          })(),
        listSessions: async () => ({ sessions: [] }),
        attachLibraryFiles: async () => ({ results: [], session_id: "s" }),
      },
    } as unknown as ApiClient;
    const { result } = await renderHook(() => useChatStream(client), {
      wrapper,
    });

    let sent: Promise<void>;
    await act(async () => {
      sent = result.current.send("问");
      await Promise.resolve();
    });
    await flushTimers();
    await act(async () => {
      result.current.stop();
      await sent;
    });
    const s = useChatStore.getState();
    const last = s.messages[s.messages.length - 1];
    expect(last?.role).toBe("assistant");
    expect(last?.content).toBe("部分");
    expect(s.streaming).toBe(false);
  });
});
