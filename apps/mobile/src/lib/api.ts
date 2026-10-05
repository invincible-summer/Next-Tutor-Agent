import {
  createApiClient,
  ApiError,
  type ApiClient,
  type FetchInitLike,
  type FetchLike,
} from "@next-tutor/api-client";
import { fetch as expoFetch } from "expo/fetch";
import { emitAuthEvent } from "./auth-events";
import {
  APP_VERSION,
  BUILD_NUMBER,
  CLIENT_PLATFORM,
  resolveApiBaseUrl,
} from "@/platform/config";
import { getGuestToken } from "@/platform/guest-session";
import {
  getRefreshToken,
  getToken,
  setToken,
  validToken,
} from "@/platform/token-store";

const mobileFetch: FetchLike = (input: string, init?: FetchInitLike) =>
  expoFetch(input, init as RequestInit | undefined) as ReturnType<FetchLike>;
function metadata() {
  return {
    platform: CLIENT_PLATFORM,
    version: APP_VERSION,
    build: BUILD_NUMBER,
  };
}
// Anonymous client prevents refresh from recursively invoking the authentication hook.
const authClient = () =>
  createApiClient({
    baseUrl: resolveApiBaseUrl(),
    fetchImpl: mobileFetch,
    clientMetadataProvider: metadata,
    defaultReadTimeoutMs: 30_000,
  });
export async function refreshAccess(force = false) {
  return validToken((token) => authClient().auth.refresh(token), force);
}
export function createMobileClient(): ApiClient {
  return createApiClient({
    baseUrl: resolveApiBaseUrl(),
    fetchImpl: mobileFetch,
    tokenProvider: () => refreshAccess(),
    guestTokenProvider: () => getGuestToken(),
    clientMetadataProvider: metadata,
    onUnauthorized: async () => {
      const token = await getToken();
      if (!token) {
        emitAuthEvent("guest-expired");
        return;
      }
      if (!(await getRefreshToken())) {
        await setToken(null);
        emitAuthEvent("unauthorized");
        return;
      }
      try {
        await refreshAccess(true);
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          await setToken(null);
          emitAuthEvent("unauthorized");
        }
        throw error;
      }
    },
    defaultReadTimeoutMs: 30_000,
  });
}
let sharedClient: ApiClient | null = null;
export function apiClient(): ApiClient {
  return (sharedClient ??= createMobileClient());
}
export function resetApiClient(): void {
  sharedClient = null;
}
export type { ApiClient };
