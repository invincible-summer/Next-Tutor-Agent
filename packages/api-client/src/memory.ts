/**
 * Memory domain (mirrors `app/api/v1/memory.py`): the memory page's AI-memory
 * region plus the legacy audit stores.
 *
 * - `promptProfile`/`setPromptWindow`/`promptSessionStatus` expose the bounded
 *   cross-chat prompt-memory profile (never transcripts or raw attachment
 *   text); the owner picks a 5–30 session window.
 * - `episodes`/`semantic` are legacy audit reads: production turns no longer
 *   append episodic events or consolidate semantic facts; the server answers
 *   `status: "disabled" | "error"` envelopes instead of failing, so callers
 *   branch on `status` (same convention as the knowledge module).
 * - `procedural` is the active sliding-window strategy aggregate.
 *
 * The Web client's legacy `student_id` query parameter is dropped — the
 * backend resolves identity from the token and ignores it.
 */
import type { AbortSignalLike, Transport } from "./types.ts";

export type MemoryStatus = "ok" | "disabled" | "error";

export interface MemoryEpisode {
  id: string;
  ts: number;
  summary?: string;
  event_type?: string;
  concept?: string;
  subject?: string;
  score?: number | null;
  emotion?: string;
  importance?: number;
  scope?: string;
  [key: string]: unknown;
}

export interface MemoryEpisodesResponse {
  status: MemoryStatus;
  episodes?: MemoryEpisode[];
  has_more?: boolean;
  message?: string;
}

export interface SemanticFact {
  id?: string;
  category?: string;
  scope?: string;
  fact?: string;
  text?: string;
  confidence?: number;
  evidence?: string[];
  superseded_by?: string | null;
  created_at?: number;
  [key: string]: unknown;
}

export interface MemorySemanticResponse {
  status: MemoryStatus;
  facts?: SemanticFact[];
  message?: string;
}

export interface ProceduralStrategy {
  strategy: string;
  subject?: string;
  scope?: string;
  success_rate?: number;
  trials?: number;
  last_used_ts?: number;
  [key: string]: unknown;
}

export interface MemoryProceduralResponse {
  status: MemoryStatus;
  strategies?: ProceduralStrategy[];
  message?: string;
}

export interface PromptMemorySession {
  session_id: string;
  workspace_id?: string;
  created_at?: number;
  updated_at?: number;
  has_contribution?: boolean;
  [key: string]: unknown;
}

export interface PromptMemoryProfile {
  status: string;
  window_size: number;
  max_window: number;
  core_profile?: Record<string, string>;
  recent_sessions?: PromptMemorySession[];
  compacted_session_count?: number;
  compacted_attribution_count?: number;
  legacy_compacted_attribution_unknown?: number;
  compaction_generation?: number;
  last_compacted_at?: number;
  directive_chars?: number;
  [key: string]: unknown;
}

/** Attribution state of one chat inside the prompt-memory profile. */
export type PromptMemorySessionStatus = "recent" | "compacted" | "legacy_unknown" | "none";

export interface MemoryClient {
  /** GET /memory/episodes — legacy episodic audit records, newest-first;
   *  `before` (Unix time) pages backwards. */
  episodes(
    options?: { limit?: number; before?: number },
    signal?: AbortSignalLike | null,
  ): Promise<MemoryEpisodesResponse>;
  /** GET /memory/semantic — legacy semantic facts incl. superseded entries. */
  semantic(signal?: AbortSignalLike | null): Promise<MemorySemanticResponse>;
  /** GET /memory/procedural — which teaching strategies worked (sliding-window
   *  success rate + trial count). */
  procedural(signal?: AbortSignalLike | null): Promise<MemoryProceduralResponse>;
  /** GET /memory/prompt-profile — bounded active cross-chat profile. */
  promptProfile(signal?: AbortSignalLike | null): Promise<PromptMemoryProfile>;
  /** PUT /memory/prompt-profile/window — choose the 5–30 session window. */
  setPromptWindow(windowSize: number): Promise<PromptMemoryProfile>;
  /** GET /memory/prompt-profile/sessions/{sessionId} — attribution status of
   *  one chat (used by archive/forget flows). */
  promptSessionStatus(
    sessionId: string,
    signal?: AbortSignalLike | null,
  ): Promise<{ status: PromptMemorySessionStatus }>;
}

export function createMemoryClient(transport: Transport): MemoryClient {
  return {
    episodes: (options = {}, signal) =>
      transport
        .request<MemoryEpisodesResponse>("/memory/episodes", {
          query: { limit: options.limit, before: options.before },
          signal,
        })
        .then((result) => result.body),
    semantic: (signal) =>
      transport
        .request<MemorySemanticResponse>("/memory/semantic", { signal })
        .then((result) => result.body),
    procedural: (signal) =>
      transport
        .request<MemoryProceduralResponse>("/memory/procedural", { signal })
        .then((result) => result.body),
    promptProfile: (signal) =>
      transport
        .request<PromptMemoryProfile>("/memory/prompt-profile", { signal })
        .then((result) => result.body),
    setPromptWindow: (windowSize) =>
      transport
        .request<PromptMemoryProfile>("/memory/prompt-profile/window", {
          method: "PUT",
          json: { window_size: windowSize },
        })
        .then((result) => result.body),
    promptSessionStatus: (sessionId, signal) =>
      transport
        .request<{ status: PromptMemorySessionStatus }>(
          `/memory/prompt-profile/sessions/${encodeURIComponent(sessionId)}`,
          { signal },
        )
        .then((result) => result.body),
  };
}
