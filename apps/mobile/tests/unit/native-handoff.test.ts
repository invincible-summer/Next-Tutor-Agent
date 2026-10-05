import type { AssistantDraft } from "@next-tutor/contracts/assistant";
import { clearPrivateState } from "@/lib/session-lifecycle";
import { prepareNativeDraft, takeNativeDraft } from "@/stores/native-handoff";

function draft(changes: Partial<AssistantDraft> = {}): AssistantDraft {
  return {
    draft_id: "synthetic-draft-1",
    conversation_id: "synthetic-conversation-1",
    action_id: "synthetic-action-1",
    prefill: {
      kind: "note",
      title: "Synthetic note",
      markdown: "Two plus three equals five.",
    },
    created_at: "2026-10-04T00:00:00.000Z",
    expires_at: new Date(Date.now() + 60_000).toISOString(),
    consumed: false,
    ...changes,
  };
}

describe("private native assistant handoff", () => {
  beforeEach(async () => {
    jest.useFakeTimers();
    await clearPrivateState();
  });
  afterEach(() => {
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("a valid draft is delivered once and acknowledges only delivery", () => {
    const value = draft();
    const ready = jest.fn();
    prepareNativeDraft("note", value, ready);
    expect(ready).not.toHaveBeenCalled();
    expect(takeNativeDraft("note")).toBe(value);
    jest.runOnlyPendingTimers();
    expect(ready).toHaveBeenCalledTimes(1);
    expect(takeNativeDraft("note")).toBeNull();
    expect(ready).toHaveBeenCalledTimes(1);
  });

  test.each([
    { consumed: true },
    { expires_at: new Date(Date.now() - 60_000).toISOString() },
    { expires_at: "invalid-expiration" },
  ])(
    "expired, malformed or consumed drafts cannot be delivered %#",
    (changes) => {
      const ready = jest.fn();
      prepareNativeDraft("note", draft(changes), ready);
      expect(takeNativeDraft("note")).toBeNull();
      expect(takeNativeDraft("note")).toBeNull();
      expect(ready).not.toHaveBeenCalled();
    },
  );

  test("note and lesson entry points receive only their own pending draft", () => {
    const note = draft();
    const lesson = draft({
      draft_id: "synthetic-lesson-draft",
      prefill: {
        kind: "lesson",
        workspace_id: "synthetic-workspace",
        topic: "Addition",
      },
    });
    prepareNativeDraft("note", note, jest.fn());
    prepareNativeDraft("lesson", lesson, jest.fn());
    expect(takeNativeDraft("lesson")).toBe(lesson);
    expect(takeNativeDraft("note")).toBe(note);
  });

  test("logout/account switch purges both drafts without calling navigation receipts", async () => {
    const ready = jest.fn();
    prepareNativeDraft("note", draft(), ready);
    prepareNativeDraft("lesson", draft(), ready);
    await clearPrivateState();
    expect(takeNativeDraft("note")).toBeNull();
    expect(takeNativeDraft("lesson")).toBeNull();
    expect(ready).not.toHaveBeenCalled();
  });

  test("logout cancels a delivery receipt queued for the next animation frame", async () => {
    const ready = jest.fn();
    prepareNativeDraft("note", draft(), ready);
    expect(takeNativeDraft("note")).not.toBeNull();
    await clearPrivateState();
    jest.runOnlyPendingTimers();
    expect(ready).not.toHaveBeenCalled();
  });
});
