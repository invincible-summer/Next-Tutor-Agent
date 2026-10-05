/**
 * Account profile domain (mirrors `app/api/v1/user.py`): the M0 profile
 * document — display name / grade band / school / subjects / preference bag —
 * plus the avatar lifecycle. Distinct from `auth.ts` (`/auth/*` session
 * lifecycle and the `/auth/me` account record): these endpoints read and
 * mutate the profile the teaching stack personalizes on. The server
 * whitelist-validates `prefs`; `avatar` is rejected on the JSON update
 * endpoint (`use_avatar_upload_endpoint`) and flows only through the
 * multipart upload.
 */
import type { AbortSignalLike, FormDataLike, Transport } from "./types.ts";

/** Preference bag (`prefs`); the server whitelist-validates known keys. */
export interface ProfilePrefs {
  ocr_parallel?: boolean;
  tts_speed?: number;
  quiz_svg_enabled?: boolean;
  quiz_critic_enabled?: boolean;
  quiz_illustration_review_enabled?: boolean;
  quiz_illustration_mode?: "v1" | "v2" | "v3";
  classroom?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface UserProfile {
  name: string;
  grade: string;
  school: string;
  subjects: string[];
  /** Server-side avatar revision marker (`avatar:<rev>`), "" when unset. */
  avatar: string;
  prefs?: ProfilePrefs;
  [key: string]: unknown;
}

/**
 * Mutable fields accepted by PUT /user/profile (only provided fields change).
 * `avatar` is deliberately absent — the server rejects it here; avatars go
 * through `uploadAvatar` / `deleteAvatar`.
 */
export interface ProfilePatch {
  name?: string;
  grade?: string;
  school?: string;
  subjects?: string[];
  prefs?: ProfilePrefs;
}

export interface ProfileResponse<TProfile = UserProfile> {
  status: string;
  profile: TProfile;
  quiz_svg_available?: boolean;
  [key: string]: unknown;
}

export interface ProfileClient {
  /** GET /user/profile — profile document + quiz_svg feature flag. */
  get<TProfile = UserProfile>(signal?: AbortSignalLike | null): Promise<ProfileResponse<TProfile>>;
  /** PUT /user/profile — partial update; only provided fields change. */
  update<TProfile = UserProfile>(patch: ProfilePatch): Promise<ProfileResponse<TProfile>>;
  /**
   * PUT /user/avatar — multipart upload. The caller appends the image as the
   * `file` part on a platform form (browser FormData / RN form-data); this
   * package never constructs FormData itself.
   */
  uploadAvatar<TProfile = UserProfile>(form: FormDataLike): Promise<ProfileResponse<TProfile>>;
  /** DELETE /user/avatar — removes the stored avatar. */
  deleteAvatar<TProfile = UserProfile>(): Promise<ProfileResponse<TProfile>>;
  deleteAccount(password: string): Promise<{ status: string }>;
  /**
   * GET /user/avatar?version= — raw PNG bytes. `version` is the profile's
   * avatar revision, forwarded as a cache-busting query (the server ignores
   * it); 404 `avatar_not_found` when none is stored.
   */
  avatar(version?: string, signal?: AbortSignalLike | null): Promise<ArrayBuffer>;
}

export function createProfileClient(transport: Transport): ProfileClient {
  return {
    deleteAccount: (password) => transport.request<{ status: string }>("/user/account", { method: "DELETE", json: { password } }).then(result => result.body),
    get: <TProfile>(signal?: AbortSignalLike | null) =>
      transport
        .request<ProfileResponse<TProfile>>("/user/profile", { signal })
        .then((result) => result.body),
    update: <TProfile>(patch: ProfilePatch) =>
      transport
        .request<ProfileResponse<TProfile>>("/user/profile", {
          method: "PUT",
          json: patch,
        })
        .then((result) => result.body),
    uploadAvatar: <TProfile>(form: FormDataLike) =>
      transport
        .request<ProfileResponse<TProfile>>("/user/avatar", {
          method: "PUT",
          body: form,
        })
        .then((result) => result.body),
    deleteAvatar: <TProfile>() =>
      transport
        .request<ProfileResponse<TProfile>>("/user/avatar", { method: "DELETE" })
        .then((result) => result.body),
    avatar: (version, signal) =>
      transport
        .request<ArrayBuffer>("/user/avatar", {
          query: { version },
          responseType: "bytes",
          signal,
        })
        .then((result) => result.body),
  };
}
