import type { AssistantDraft } from "@next-tutor/contracts/assistant";
import { registerSessionCleanup } from "@/lib/session-lifecycle";
type Kind = "note" | "lesson";
let epoch = 0;
const drafts = new Map<Kind, { draft: AssistantDraft; ready: () => void }>();
export function prepareNativeDraft(
  kind: Kind,
  draft: AssistantDraft,
  ready: () => void,
) {
  drafts.set(kind, { draft, ready });
}
export function takeNativeDraft(kind: Kind): AssistantDraft | null {
  const entry = drafts.get(kind);
  drafts.delete(kind);
  const expiry = entry ? Date.parse(entry.draft.expires_at) : NaN;
  if (
    !Number.isFinite(expiry) ||
    !entry ||
    entry.draft.consumed ||
    expiry <= Date.now()
  )
    return null;
  const generation = epoch;
  requestAnimationFrame(() => {
    if (generation === epoch) entry.ready();
  });
  return entry.draft;
}
registerSessionCleanup(() => {
  epoch++;
  drafts.clear();
});
