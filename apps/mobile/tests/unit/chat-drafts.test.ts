import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  clearDraft,
  draftKey,
  loadDraft,
  peekDraft,
  purgeLegacyDrafts,
  resetDraftsForTests,
  saveDraft,
} from "@/features/chat/lib/drafts";
import { clearPrivateState } from "@/lib/session-lifecycle";
beforeEach(async () => {
  resetDraftsForTests();
  await AsyncStorage.clear();
  jest.clearAllMocks();
});
describe("private composer drafts", () => {
  test("keys isolate user, tenant, workspace and session", () => {
    expect(draftKey("tenant-a:u1", "s1")).toBe("tenant-a:u1:s1");
    expect(draftKey("u1", null, "w9")).toBe("u1:ws:w9");
    expect(draftKey("u1", null)).toBe("u1:new");
  });
  test("drafts stay in memory and are cleared after a process restart", async () => {
    saveDraft("u1:s1", { body: "private draft", pendingFileNames: ["a.pdf"] });
    expect(peekDraft("u1:s1")?.body).toBe("private draft");
    expect(AsyncStorage.setItem).not.toHaveBeenCalled();
    resetDraftsForTests();
    expect(await loadDraft("u1:s1")).toBeNull();
  });
  test("empty drafts and explicitly discarded drafts are removed", () => {
    saveDraft("u1:s1", { body: "", pendingFileNames: [] });
    expect(peekDraft("u1:s1")).toBeNull();
    saveDraft("u1:s1", { body: "x", pendingFileNames: [] });
    clearDraft("u1:s1");
    expect(peekDraft("u1:s1")).toBeNull();
  });
  test("legacy disk drafts are purged while preferences survive", async () => {
    await AsyncStorage.setItem("nt.draft.u1:s1", "private");
    await AsyncStorage.setItem("nt.theme", "dark");
    await purgeLegacyDrafts();
    expect(await AsyncStorage.getItem("nt.draft.u1:s1")).toBeNull();
    expect(await AsyncStorage.getItem("nt.theme")).toBe("dark");
  });
  test("logout clears all in-memory drafts", async () => {
    saveDraft("u1:s1", { body: "private", pendingFileNames: [] });
    await clearPrivateState();
    expect(await loadDraft("u1:s1")).toBeNull();
  });
});
