/**
 * Guest domain: opaque, page-scoped sessions that are never persisted
 * (mirrors `app/api/v1/guest.py`). Guests authenticate via the
 * transport-level `X-Guest-Token` provider; these calls manage the
 * session lifecycle itself.
 */
import type { Transport } from "./types.ts";

export interface GuestSession {
  token: string;
  expires_in: number;
}

export interface GuestTextbookItem {
  id: string;
  title?: string;
  [key: string]: unknown;
}

export interface GuestClient {
  /** POST /guest/session — create an ephemeral guest context. */
  createSession(): Promise<GuestSession>;
  /** DELETE /guest/session — best-effort dispose; token passed explicitly. */
  deleteSession(token: string): Promise<void>;
  /** GET /guest/textbooks — public textbooks visible to guests. */
  textbooks(): Promise<{ items: GuestTextbookItem[] }>;
}

export function createGuestClient(transport: Transport): GuestClient {
  return {
    createSession: () =>
      transport
        .request<GuestSession>("/guest/session", { method: "POST" })
        .then((result) => result.body),
    deleteSession: (token) =>
      transport
        .request("/guest/session", {
          method: "DELETE",
          headers: { "X-Guest-Token": token },
          responseType: "none",
        })
        .then(() => undefined),
    textbooks: () =>
      transport
        .request<{ items: GuestTextbookItem[] }>("/guest/textbooks")
        .then((result) => result.body),
  };
}
