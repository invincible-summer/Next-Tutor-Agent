/**
 * UX intelligence domain (mirrors `app/api/v1/ux.py` plus the dashboard's
 * teaching-log / recent-quiz / model-info reads): read-only projections
 * behind the home surfaces — interaction profile, motivation/streak,
 * per-day activity, personalized greeting, cross-session quiz history and
 * the public model banner. The Web client's legacy `student_id` query
 * parameter is dropped — the backend resolves identity from the token and
 * ignores it. Payloads are server projections; methods are generic so
 * platforms can layer richer view types over these defaults.
 */
import type { AbortSignalLike, Transport } from "./types.ts";

export interface UxInteractionStyle {
  /** encouraging | neutral | formal */
  tone: string;
  /** concise | medium | detailed */
  detail_level: string;
  visual_preference: boolean;
  /** steady | fast | slow */
  pacing: string;
  /** low | medium | high */
  patience: string;
  [key: string]: unknown;
}

/** GET /ux/profile — the data behind the 学习画像 panel. */
export interface UxProfileSummary {
  student_id: string;
  style: UxInteractionStyle;
  motivation: { last_nudge_ts: number; last_milestone_surfaced: number };
  recent_feedback_counts: Record<string, number>;
  avg_response_length: number;
  abandon_signals: number;
  event_count: number;
  updated_at: number;
  [key: string]: unknown;
}

/** GET /ux/motivation — streak / milestone summary. */
export interface UxMotivation {
  streak_days: number;
  next_milestone: number | null;
  milestones: number[];
  active_days: number;
  [key: string]: unknown;
}

export interface UxActivityDay {
  date: string;
  answers: number;
  teachings: number;
  reviews: number;
  [key: string]: unknown;
}

/** GET /ux/activity — per-day counts for the dashboard chart. */
export interface UxActivity {
  days: UxActivityDay[];
  /** "aggregated" live ledgers vs "legacy_episodes" compatibility fallback. */
  source: "aggregated" | "legacy_episodes" | "none" | string;
  streak_days: number;
  longest_streak: number;
  last_active_day: string;
  active_days: number;
  [key: string]: unknown;
}

/** GET /ux/greeting — personalized opener for a new/empty session. */
export interface UxGreeting {
  greeting: string;
  lang: string;
  [key: string]: unknown;
}

export interface TeachingLogEntry {
  mode: string;
  outcome: string;
  ts: number;
  note: string;
  [key: string]: unknown;
}

export interface TeachingLogConcept {
  current_mode: string;
  current_outcome: string;
  last_ts: number;
  /** Newest-first within a concept (UI-friendly). */
  entries: TeachingLogEntry[];
  [key: string]: unknown;
}

/**
 * GET /student/teaching-log — cross-turn teaching memory (M3). The teaching
 * engine answers `status: "disabled" | "error"` with no `concepts` instead
 * of failing, so callers branch on `status`.
 */
export interface TeachingLogResponse {
  status: "ok" | "disabled" | "error" | string;
  concepts?: Record<string, TeachingLogConcept>;
  message?: string;
  [key: string]: unknown;
}

/** Cross-session recent-quiz row (assessment-center history list). */
export interface RecentQuizQuestion {
  /** attempt_id (server identity, single-submission key). */
  id: string;
  /** ISO timestamp. */
  ts: string;
  session_id: string;
  question_id: string;
  question_revision: number;
  topic: string;
  knowledge_point: string;
  type: string;
  stem: string;
  verdict: string;
  student_answer?: string;
  evaluation_status?: string;
  availability?: string;
  [key: string]: unknown;
}

/** GET /quiz/recent — cross-session question history (per-student cap 100). */
export interface RecentQuizQuestionsResponse {
  status: string;
  questions: RecentQuizQuestion[];
  [key: string]: unknown;
}

/**
 * GET /model-info — non-sensitive model configuration banner (names and
 * availability booleans only; the server never returns keys).
 */
export interface ModelInfo {
  llm_model: string;
  multimodal_configured: boolean;
  multimodal_model: string;
  voice_models?: {
    cloud: { model: string; configured: boolean; voices: string[] };
    local: { model: string; enabled: boolean; voice: string; languages: string[] };
    phone_provider: string;
    automatic_priority: string;
  };
  [key: string]: unknown;
}

export interface UxGreetingOptions {
  /** BCP-47-ish UI language (server default "zh"). */
  lang?: string;
  /** Grade band (server default "" = auto). */
  grade?: string;
}

export interface UxClient {
  /** GET /ux/profile — interaction style / feedback / abandon profile. */
  profile<T = UxProfileSummary>(signal?: AbortSignalLike | null): Promise<T>;
  /** GET /ux/motivation — streak / milestone summary. */
  motivation<T = UxMotivation>(signal?: AbortSignalLike | null): Promise<T>;
  /** GET /ux/activity?days= — per-day activity window (default 14, ≤ 90). */
  activity<T = UxActivity>(days?: number, signal?: AbortSignalLike | null): Promise<T>;
  /** GET /ux/greeting?lang=&grade= — personalized empty-session opener. */
  greeting<T = UxGreeting>(
    options?: UxGreetingOptions,
    signal?: AbortSignalLike | null,
  ): Promise<T>;
  /** GET /student/teaching-log?limit_per_concept= — cross-turn teaching memory. */
  teachingLog<T = TeachingLogResponse>(
    options?: { limitPerConcept?: number },
    signal?: AbortSignalLike | null,
  ): Promise<T>;
  /** GET /quiz/recent?limit= — cross-session quiz history. */
  recentQuizQuestions<T = RecentQuizQuestionsResponse>(
    options?: { limit?: number },
    signal?: AbortSignalLike | null,
  ): Promise<T>;
  /** GET /model-info — public model configuration banner. */
  modelInfo<T = ModelInfo>(signal?: AbortSignalLike | null): Promise<T>;
}

export function createUxClient(transport: Transport): UxClient {
  return {
    profile: <T>(signal?: AbortSignalLike | null) =>
      transport.request<T>("/ux/profile", { signal }).then((result) => result.body),
    motivation: <T>(signal?: AbortSignalLike | null) =>
      transport.request<T>("/ux/motivation", { signal }).then((result) => result.body),
    activity: <T>(days = 14, signal?: AbortSignalLike | null) =>
      transport
        .request<T>("/ux/activity", { query: { days }, signal })
        .then((result) => result.body),
    greeting: <T>(options: UxGreetingOptions = {}, signal?: AbortSignalLike | null) =>
      transport
        .request<T>("/ux/greeting", {
          query: { lang: options.lang ?? "zh", grade: options.grade ?? "" },
          signal,
        })
        .then((result) => result.body),
    teachingLog: <T>(options: { limitPerConcept?: number } = {}, signal?: AbortSignalLike | null) =>
      transport
        .request<T>("/student/teaching-log", {
          query: { limit_per_concept: options.limitPerConcept },
          signal,
        })
        .then((result) => result.body),
    recentQuizQuestions: <T>(options: { limit?: number } = {}, signal?: AbortSignalLike | null) =>
      transport
        .request<T>("/quiz/recent", { query: { limit: options.limit }, signal })
        .then((result) => result.body),
    modelInfo: <T>(signal?: AbortSignalLike | null) =>
      transport.request<T>("/model-info", { signal }).then((result) => result.body),
  };
}
