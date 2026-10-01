import { apiFetch } from "./api-fetch";
import { clearQuizAnswerDrafts } from "./quiz-drafts";

let token: string | null = null;
let pending: Promise<string> | null = null;
let generation = 0;
let base = "";

export function getGuestToken(): string | null { return token; }

export function endGuestSession(): void {
  const hadGuest = !!token || !!pending;
  const old = token;
  if (hadGuest) clearQuizAnswerDrafts();
  token = null;
  pending = null;
  generation += 1;
  if (old && base) {
    void apiFetch(`${base}/guest/session`, {
      method: "DELETE", headers: { "X-Guest-Token": old }, keepalive: true,
    }).catch(() => {});
  }
  if (hadGuest && typeof window !== "undefined") window.dispatchEvent(new Event("edu-guest-ended"));
}

export function ensureGuestSession(apiBase: string): Promise<string> {
  base = apiBase;
  if (token) return Promise.resolve(token);
  if (pending) return pending;
  const expected = generation;
  const creating = apiFetch(`${apiBase}/guest/session`, { method: "POST" }).then(async (res) => {
    if (!res.ok) throw new Error("guest_unavailable");
    const data = await res.json() as { token: string };
    if (generation !== expected) {
      void apiFetch(`${apiBase}/guest/session`, {
        method: "DELETE", headers: { "X-Guest-Token": data.token }, keepalive: true,
      }).catch(() => {});
      throw new Error("guest_session_expired");
    }
    token = data.token;
    return token;
  }).finally(() => { if (pending === creating) pending = null; });
  pending = creating;
  return creating;
}
