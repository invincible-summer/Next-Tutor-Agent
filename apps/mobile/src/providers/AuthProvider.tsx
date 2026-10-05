import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ApiError,
  type AuthUserLike,
  type Principal,
  type RegisterPayload,
} from "@next-tutor/api-client";
import { apiClient, refreshAccess } from "@/lib/api";
import { onAuthEvent } from "@/lib/auth-events";
import { clearPrivateState } from "@/lib/session-lifecycle";
import {
  clearGuestSession,
  getGuestToken,
  setGuestToken,
} from "@/platform/guest-session";
import {
  getRefreshToken,
  getToken,
  setCredentials,
  setToken,
} from "@/platform/token-store";

export type AuthState =
  | { status: "loading" }
  | { status: "error"; error: unknown }
  | { status: "signed-out"; authRequired: boolean; guestAllowed: boolean }
  | { status: "guest" }
  | { status: "signed-in"; user: AuthUserLike; principal: Principal | null };
interface AuthContextValue {
  state: AuthState;
  owner: string;
  retry: () => Promise<void>;
  signIn: (email: string, password: string) => Promise<void>;
  register: (payload: RegisterPayload) => Promise<void>;
  continueAsGuest: () => Promise<void>;
  signOut: () => Promise<void>;
}
const AuthContext = createContext<AuthContextValue | null>(null);
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  const generation = useRef(0);
  const establish = useCallback(
    async (user: AuthUserLike, current = generation.current) => {
      const principal = await apiClient()
        .auth.principal()
        .then((r) => r.principal)
        .catch((error) => {
          if (error instanceof ApiError && error.status === 409) return null;
          throw error;
        });
      if (current === generation.current)
        setState({ status: "signed-in", user, principal });
    },
    [],
  );
  const hydrate = useCallback(async () => {
    const current = ++generation.current;
    setState({ status: "loading" });
    try {
      if ((await getToken()) || (await getRefreshToken())) {
        await refreshAccess();
        const { user } = await apiClient().auth.me();
        if (current === generation.current) await establish(user, current);
        return;
      }
      const status = await apiClient().auth.status();
      if (current === generation.current)
        setState({
          status: "signed-out",
          authRequired: status.auth_required,
          guestAllowed: status.guest_allowed,
        });
    } catch (error) {
      if (current !== generation.current) return;
      if (error instanceof ApiError && error.status === 401) {
        await setToken(null);
        await clearPrivateState();
        setState({
          status: "signed-out",
          authRequired: true,
          guestAllowed: false,
        });
      } else setState({ status: "error", error });
    }
  }, [establish]);
  useEffect(() => {
    void hydrate();
  }, [hydrate]);
  useEffect(
    () =>
      onAuthEvent(() => {
        generation.current++;
        clearGuestSession();
        void clearPrivateState().then(() => hydrate());
      }),
    [hydrate],
  );
  const signIn = useCallback(
    async (email: string, password: string) => {
      const current = ++generation.current;
      const response = await apiClient().auth.login({ email, password });
      if (current !== generation.current) return;
      await clearPrivateState();
      clearGuestSession();
      await setCredentials(response);
      await establish(response.user, current);
    },
    [establish],
  );
  const register = useCallback(
    async (payload: RegisterPayload) => {
      const current = ++generation.current;
      const response = await apiClient().auth.register(payload);
      if (current !== generation.current) return;
      await clearPrivateState();
      clearGuestSession();
      await setCredentials(response);
      await establish(response.user, current);
    },
    [establish],
  );
  const continueAsGuest = useCallback(async () => {
    const current = ++generation.current;
    const session = await apiClient().guest.createSession();
    if (current !== generation.current) return;
    await clearPrivateState();
    setGuestToken(session.token);
    setState({ status: "guest" });
  }, []);
  const signOut = useCallback(async () => {
    const current = ++generation.current;
    // Revoke this rotating session before removing the credential.
    if (state.status === "signed-in" && state.principal?.auth_session_id) {
      await apiClient()
        .auth.revokeSession(state.principal.auth_session_id)
        .catch(() => {});
    }
    const guest = getGuestToken();
    if (guest)
      await apiClient()
        .guest.deleteSession(guest)
        .catch(() => {});
    if (current !== generation.current) return;
    clearGuestSession();
    await setToken(null);
    await clearPrivateState();
    if (current !== generation.current) return;
    setState({ status: "signed-out", authRequired: true, guestAllowed: false });
    const status = await apiClient()
      .auth.status()
      .catch(() => null);
    if (status && current === generation.current)
      setState({
        status: "signed-out",
        authRequired: status.auth_required,
        guestAllowed: status.guest_allowed,
      });
  }, [state, hydrate]);
  const owner =
    state.status === "signed-in"
      ? `${state.principal?.tenant_id ?? "file"}:${state.user.id}`
      : state.status;
  const value = useMemo(
    () => ({
      state,
      owner,
      retry: hydrate,
      signIn,
      register,
      continueAsGuest,
      signOut,
    }),
    [state, owner, hydrate, signIn, register, continueAsGuest, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider");
  return value;
}
