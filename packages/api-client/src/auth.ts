/**
 * Auth domain (M0 endpoints): register/login/logout/me/status. The wire
 * shapes mirror `app/api/v1/auth.py`; `user` stays generic so each platform
 * can layer its richer profile types (Web `AuthUser`, future mobile user)
 * without this package duplicating them.
 */
import type { AbortSignalLike, Transport } from "./types.ts";

export interface AuthUserLike {
  id: string;
  email: string;
  username: string;
  role: string;
  created_at: number;
  last_login_at: number;
  profile: {
    name: string;
    grade: string;
    school: string;
    subjects: string[];
    avatar: string;
    [key: string]: unknown;
  };
}

export interface AuthStatusResponse {
  auth_required: boolean;
  guest_allowed: boolean;
  using_default_secret?: boolean;
}

export interface AuthResponse<TUser = AuthUserLike> {
  token: string;
  user: TUser;
}

export interface RegisterPayload {
  email: string;
  password: string;
  username?: string;
  name?: string;
  grade?: string;
  subjects?: string[];
  school?: string;
}

export interface LoginPayload {
  email: string;
  password: string;
}

export interface AuthClient {
  status(signal?: AbortSignalLike | null): Promise<AuthStatusResponse>;
  register<TUser = AuthUserLike>(payload: RegisterPayload): Promise<AuthResponse<TUser>>;
  login<TUser = AuthUserLike>(payload: LoginPayload): Promise<AuthResponse<TUser>>;
  logout(): Promise<void>;
  me<TUser = AuthUserLike>(): Promise<{ user: TUser }>;
}

export function createAuthClient(transport: Transport): AuthClient {
  return {
    status: (signal) =>
      transport
        .request<AuthStatusResponse>("/auth/status", {
          signal,
          headers: { Accept: "application/json" },
        })
        .then((result) => result.body),
    register: <TUser>(payload: RegisterPayload) =>
      transport
        .request<AuthResponse<TUser>>("/auth/register", { method: "POST", json: payload })
        .then((result) => result.body),
    login: <TUser>(payload: LoginPayload) =>
      transport
        .request<AuthResponse<TUser>>("/auth/login", { method: "POST", json: payload })
        .then((result) => result.body),
    logout: () =>
      transport
        .request("/auth/logout", { method: "POST", responseType: "none" })
        .then(() => undefined),
    me: <TUser>() =>
      transport.request<{ user: TUser }>("/auth/me").then((result) => result.body),
  };
}
