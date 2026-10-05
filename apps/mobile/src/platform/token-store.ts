import * as SecureStore from "expo-secure-store";
import type { AuthResponse, RefreshResponse } from "@next-tutor/api-client";

const LEGACY_KEY = "auth.token";
const REFRESH_KEY = "auth.refresh";
let cached: string | null | undefined;
let refreshToken: string | null | undefined;
let expiresAt = 0;
let epoch = 0;
let refreshing: Promise<string | null> | null = null;
let credentialWrites: Promise<void> = Promise.resolve();

function persist(write: () => Promise<void>): Promise<void> {
  const next = credentialWrites.catch(() => {}).then(write);
  credentialWrites = next;
  return next;
}

/** Access tokens stay in memory. File-mode deployments retain their legacy Keychain token. */
export async function getToken(): Promise<string | null> {
  if (cached !== undefined) return cached;
  const current = epoch;
  const value = await SecureStore.getItemAsync(LEGACY_KEY);
  if (current === epoch && cached === undefined) cached = value;
  return cached ?? null;
}
export async function getRefreshToken(): Promise<string | null> {
  if (refreshToken !== undefined) return refreshToken;
  const current = epoch;
  const value = await SecureStore.getItemAsync(REFRESH_KEY);
  if (current === epoch && refreshToken === undefined) refreshToken = value;
  return refreshToken ?? null;
}
export async function setToken(token: string | null): Promise<void> {
  epoch++;
  cached = token;
  refreshToken = null;
  expiresAt = 0;
  refreshing = null;
  await persist(async () => {
    await SecureStore.deleteItemAsync(REFRESH_KEY);
    if (token) await SecureStore.setItemAsync(LEGACY_KEY, token);
    else await SecureStore.deleteItemAsync(LEGACY_KEY);
  });
}
async function saveRotation(
  value: RefreshResponse,
  current: number,
): Promise<void> {
  if (current !== epoch) return;
  refreshToken = value.refresh_token;
  cached = value.access_token;
  expiresAt = Date.now() + value.expires_in * 1000;
  await persist(async () => {
    if (current !== epoch) return;
    await SecureStore.setItemAsync(REFRESH_KEY, value.refresh_token);
    await SecureStore.deleteItemAsync(LEGACY_KEY);
  });
}
export async function setCredentials(value: AuthResponse): Promise<void> {
  epoch++;
  refreshing = null;
  if (value.access_token && value.refresh_token) {
    await saveRotation(
      {
        access_token: value.access_token,
        refresh_token: value.refresh_token,
        expires_in: value.expires_in ?? 900,
      },
      epoch,
    );
  } else await setToken(value.token);
}
export async function validToken(
  refresh: (token: string) => Promise<RefreshResponse>,
  force = false,
): Promise<string | null> {
  const token = await getToken();
  const refreshValue = await getRefreshToken();
  if (!refreshValue || (!force && token && Date.now() < expiresAt - 30_000))
    return token;
  if (refreshing) return refreshing;
  const current = epoch;
  const operation = (async () => {
    const response = await refresh(refreshValue);
    if (current !== epoch) return null;
    await saveRotation(response, current);
    if (current !== epoch) return null;
    return cached ?? null;
  })().finally(() => {
    if (refreshing === operation) refreshing = null;
  });
  refreshing = operation;
  return refreshing;
}
export function __resetTokenCacheForTests(): void {
  cached = undefined;
  refreshToken = undefined;
  expiresAt = 0;
  refreshing = null;
  epoch++;
}
