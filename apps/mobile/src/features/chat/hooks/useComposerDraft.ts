import { useEffect, useRef, useState } from "react";

import {
  clearDraft,
  draftKey,
  loadDraft,
  peekDraft,
  saveDraft,
  type ComposerDraft,
} from "../lib/drafts";

export interface ComposerDraftController {
  text: string;
  setText: (text: string) => void;
  /** 仅文件名（RN 附件二进制不可持久化；展示「重选」提示用）。 */
  pendingFileNames: string[];
  setPendingFileNames: (names: string[]) => void;
  /** 发送成功后调用：清掉当前键草稿。 */
  commitSent: () => void;
}

/**
 * 按 owner+session 键控的草稿（对齐 Web §3.2）：
 * 切键时先把旧状态存回再取新键；500ms 防抖持久化；卸载立即存回。
 */
export function useComposerDraft(
  owner: string,
  sessionId: string | null,
  workspaceId?: string | null,
): ComposerDraftController {
  const key = draftKey(owner, sessionId, workspaceId);
  const [text, setText] = useState(() => peekDraft(key)?.body ?? "");
  const [pendingFileNames, setPendingFileNames] = useState<string[]>(
    () => peekDraft(key)?.pendingFileNames ?? [],
  );
  const keyRef = useRef(key);
  const restoredRef = useRef<string | null>(peekDraft(key) ? key : null);
  const latestRef = useRef<ComposerDraft>({ body: text, pendingFileNames });
  latestRef.current = { body: text, pendingFileNames };

  // 键切换：存旧取新（异步回源 AsyncStorage 一次）。
  useEffect(() => {
    const prevKey = keyRef.current;
    if (prevKey !== key) {
      saveDraft(prevKey, latestRef.current);
      keyRef.current = key;
      restoredRef.current = peekDraft(key) ? key : null;
      const cached = peekDraft(key);
      setText(cached?.body ?? "");
      setPendingFileNames(cached?.pendingFileNames ?? []);
    }
    if (restoredRef.current === key) return;
    restoredRef.current = key;
    let cancelled = false;
    void loadDraft(key).then((draft) => {
      if (cancelled || !draft) return;
      setText(draft.body);
      setPendingFileNames(draft.pendingFileNames);
    });
    return () => {
      cancelled = true;
    };
  }, [key]);

  // 防抖持久化。
  useEffect(() => {
    if (restoredRef.current !== keyRef.current) return;
    const timer = setTimeout(
      () => saveDraft(keyRef.current, latestRef.current),
      500,
    );
    return () => clearTimeout(timer);
  }, [text, pendingFileNames]);

  // 卸载立即存回。
  useEffect(
    () => () => {
      saveDraft(keyRef.current, latestRef.current);
    },
    [],
  );

  return {
    text,
    setText,
    pendingFileNames,
    setPendingFileNames,
    commitSent: () => clearDraft(keyRef.current),
  };
}
