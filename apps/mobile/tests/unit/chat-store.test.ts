import { resetChatStore, useChatStore } from "@/features/chat/store/chatStore";

beforeEach(() => resetChatStore());

describe("chatStore", () => {
  test("flushPending 批量写入流式累积", () => {
    useChatStore.getState().flushPending("想", "答");
    expect(useChatStore.getState().pendingThinking).toBe("想");
    expect(useChatStore.getState().pendingAnswer).toBe("答");
  });

  test("commitAssistant 落消息并复位 pending 态", () => {
    const st = useChatStore.getState();
    st.setMessages([{ role: "user", content: "问" }]);
    st.addToolStart("generate_quiz");
    st.setToolResult({
      tool: "generate_quiz",
      status: "success",
      data: {},
      text: "",
    });
    st.flushPending("思考", "回答");
    st.commitAssistant();
    const s = useChatStore.getState();
    expect(s.messages).toHaveLength(2);
    expect(s.messages[1]).toMatchObject({
      role: "assistant",
      content: "回答",
      thinking: "思考",
    });
    expect(s.messages[1]?.toolCalls).toHaveLength(1);
    expect(s.pendingAnswer).toBe("");
    expect(s.pendingToolCalls).toHaveLength(0);
    expect(s.activeTool).toBeNull();
  });

  test("setToolResult 落到最后一个 pending 工具调用并清 activeTool", () => {
    const st = useChatStore.getState();
    st.addToolStart("knowledge_search");
    st.addToolStart("generate_quiz");
    st.setToolResult({
      tool: "generate_quiz",
      status: "success",
      data: {},
      text: "",
    });
    const s = useChatStore.getState();
    expect(s.pendingToolCalls[0]?.result).toBeUndefined();
    expect(s.pendingToolCalls[1]?.result).toMatchObject({ status: "success" });
    expect(s.activeTool).toBeNull();
  });

  test("newChat / loadFull 递增 generation（防串会话世代号）", () => {
    const g0 = useChatStore.getState().generation;
    useChatStore.getState().newChat();
    expect(useChatStore.getState().generation).toBe(g0 + 1);
    useChatStore
      .getState()
      .loadFull([{ role: "user", content: "x" }], [], "s1");
    const s = useChatStore.getState();
    expect(s.generation).toBe(g0 + 2);
    expect(s.sessionId).toBe("s1");
    expect(s.streaming).toBe(false);
  });

  test("loadFull 复位悬空的 streaming/pending 态", () => {
    const st = useChatStore.getState();
    st.setStreaming(true);
    st.flushPending("t", "a");
    st.setRetry({ attempt: 1, reason: "x", visible: true });
    st.loadFull([], [], "s2");
    const s = useChatStore.getState();
    expect(s.streaming).toBe(false);
    expect(s.pendingAnswer).toBe("");
    expect(s.retry).toBeNull();
  });
});
