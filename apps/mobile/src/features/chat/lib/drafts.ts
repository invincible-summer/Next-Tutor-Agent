import AsyncStorage from "@react-native-async-storage/async-storage";
import { registerSessionCleanup } from "@/lib/session-lifecycle";
export interface ComposerDraft {
  body: string;
  pendingFileNames: string[];
}
const memory = new Map<string, ComposerDraft>();
export function draftKey(
  owner: string,
  sessionId: string | null,
  workspaceId?: string | null,
): string {
  return `${owner || "anon"}:${sessionId ?? (workspaceId ? "ws:" + workspaceId : "new")}`;
}
export function saveDraft(key: string, draft: ComposerDraft): void {
  if (!draft.body && draft.pendingFileNames.length === 0) memory.delete(key);
  else memory.set(key, draft);
}
export function peekDraft(key: string): ComposerDraft | null {
  return memory.get(key) ?? null;
}
export async function loadDraft(key: string): Promise<ComposerDraft | null> {
  return peekDraft(key);
}
export function clearDraft(key: string): void {
  memory.delete(key);
}
export function resetDraftsForTests(): void {
  memory.clear();
}
export async function purgeLegacyDrafts(): Promise<void> {
  const keys = (await AsyncStorage.getAllKeys()).filter((key) =>
    key.startsWith("nt.draft."),
  );
  await Promise.all(keys.map((key) => AsyncStorage.removeItem(key)));
}
registerSessionCleanup(() => {
  memory.clear();
  return purgeLegacyDrafts();
});
