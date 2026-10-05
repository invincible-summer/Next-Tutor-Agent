import { useCallback, useEffect, useRef, useState } from "react";
import { useIsFocused } from "@react-navigation/native";

import { apiClient } from "@/lib/api";
import { useUiPrefs } from "@/stores/ui";
import { gradeFromApi, type ChatSessionDetail } from "../model/types";
import { useChatStore } from "../store/chatStore";

/** 首屏只取最近 40 条；更早历史经顶部按钮按需取全量（对齐 Web TAIL_INITIAL）。 */
export const TAIL_INITIAL = 40;

export interface ChatSessionController {
  /** URL 指向的会话加载失败（404/无权）时的会话 id；用于 NotFound 态。 */
  loadErrorFor: string | null;
  /** 未加载进窗口的更早消息条数。 */
  earlierCount: number;
  loadingEarlier: boolean;
  loadEarlier: () => Promise<void>;
  /** 会话所属工作区（新会话为路由 ws 参数或 null）。 */
  workspaceId: string | null;
  setWorkspaceId: (id: string | null) => void;
}

/**
 * 路由（URL）是当前会话的唯一事实源：深链/会话列表/返回键都汇入这里。
 * - 无 sessionId（裸 /tutor）→ newChat 复位（幂等：流式中不二次复位）；
 * - 有 sessionId → loadSession(tail=40)，世代防串由 store.generation 承担；
 * - 学段跟随会话（gradeFromApi）。
 */
export function useChatSession(
  urlSession: string | null,
  initialWorkspace?: string | null,
): ChatSessionController {
  const focused = useIsFocused();
  const [loadErrorFor, setLoadErrorFor] = useState<string | null>(null);
  const [earlierCount, setEarlierCount] = useState(0);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [workspaceId, setWorkspaceId] = useState<string | null>(
    initialWorkspace ?? null,
  );

  const loadedRef = useRef<string | null>(null);
  const bareHandledRef = useRef(false);

  useEffect(() => {
    if (!focused) {
      bareHandledRef.current = false;
      loadedRef.current = null;
      return;
    }
    setLoadErrorFor(null);
    if (initialWorkspace) setWorkspaceId(initialWorkspace);
  }, [initialWorkspace]);

  useEffect(() => {
    if (!urlSession) {
      // 裸路由 = 新对话页：复位一次（流式中的深链自动发送不被二次复位打断）。
      if (bareHandledRef.current) return;
      bareHandledRef.current = true;
      const st = useChatStore.getState();
      if (st.sessionId || st.streaming) {
        st.aborter?.abort();
        st.newChat();
      }
      useUiPrefs.getState().setGrade(useUiPrefs.getState().defaultGrade);
      setEarlierCount(0);
      loadedRef.current = null;
      return;
    }
    bareHandledRef.current = false;
    if (loadedRef.current === urlSession) return;
    loadedRef.current = urlSession;
    if (useChatStore.getState().sessionId === urlSession) return; // 页内已绑定
    useChatStore.getState().aborter?.abort();
    let alive = true;
    apiClient()
      .chat.loadSession<ChatSessionDetail>(urlSession, TAIL_INITIAL)
      .then((detail) => {
        if (!alive) return;
        useChatStore
          .getState()
          .loadFull(
            detail.messages ?? [],
            detail.knowledge_files ?? [],
            urlSession,
          );
        const total = detail.message_total ?? (detail.messages ?? []).length;
        setEarlierCount(Math.max(0, total - (detail.messages ?? []).length));
        if (detail.grade !== undefined) {
          useUiPrefs.getState().setGrade(gradeFromApi(detail.grade));
        }
        setWorkspaceId(detail.workspace_id ?? null);
      })
      .catch(() => {
        if (alive) setLoadErrorFor(urlSession);
      });
    return () => {
      alive = false;
    };
  }, [urlSession, focused]);

  const loadEarlier = useCallback(async () => {
    if (!urlSession || loadingEarlier) return;
    setLoadingEarlier(true);
    try {
      const full =
        await apiClient().chat.loadSession<ChatSessionDetail>(urlSession);
      // 会话可能已被切走：仅当仍指向同一会话时才替换消息。
      if (useChatStore.getState().sessionId === urlSession) {
        useChatStore.getState().setMessages(full.messages ?? []);
      }
      setEarlierCount(0);
    } catch {
      // 保持现状，可重试。
    } finally {
      setLoadingEarlier(false);
    }
  }, [urlSession, loadingEarlier]);

  return {
    loadErrorFor,
    earlierCount,
    loadingEarlier,
    loadEarlier,
    workspaceId,
    setWorkspaceId,
  };
}
