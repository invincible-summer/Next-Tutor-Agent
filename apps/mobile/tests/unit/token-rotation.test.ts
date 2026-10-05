import * as SecureStore from "expo-secure-store";
import type { RefreshResponse } from "@next-tutor/api-client";
import {
  __resetTokenCacheForTests,
  getRefreshToken,
  getToken,
  setCredentials,
  setToken,
  validToken,
} from "@/platform/token-store";

const rotated = (suffix: string): RefreshResponse => ({
  access_token: `access-${suffix}`,
  refresh_token: `refresh-${suffix}`,
  expires_in: 900,
});
const credentials = (suffix: string) => ({
  token: `legacy-${suffix}`,
  ...rotated(suffix),
  user: {
    id: "synthetic-user",
    email: "fixture@example.invalid",
    username: "Fixture",
    role: "student",
    created_at: 0,
    last_login_at: 0,
    profile: {
      name: "Fixture",
      grade: "",
      school: "",
      subjects: [],
      avatar: "",
    },
  },
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

describe("rotating mobile credentials", () => {
  beforeEach(async () => {
    await setToken(null);
    __resetTokenCacheForTests();
    jest.clearAllMocks();
  });

  test("access stays in memory; only refresh is recovered after a cold start", async () => {
    await setCredentials(credentials("first"));
    expect(await getToken()).toBe("access-first");
    expect(await SecureStore.getItemAsync("auth.token")).toBeNull();
    expect(await SecureStore.getItemAsync("auth.refresh")).toBe(
      "refresh-first",
    );
    __resetTokenCacheForTests();
    expect(await getToken()).toBeNull();
    const refresh = jest.fn(async () => rotated("next"));
    expect(await validToken(refresh)).toBe("access-next");
    expect(refresh).toHaveBeenCalledWith("refresh-first");
    expect(await getRefreshToken()).toBe("refresh-next");
  });

  test("concurrent forced refreshes share one network operation", async () => {
    await setCredentials(credentials("first"));
    const response = deferred<RefreshResponse>();
    const refresh = jest.fn(() => response.promise);
    const requests = Array.from({ length: 10 }, () =>
      validToken(refresh, true),
    );
    await Promise.resolve();
    await Promise.resolve();
    expect(refresh).toHaveBeenCalledTimes(1);
    response.resolve(rotated("next"));
    expect(await Promise.all(requests)).toEqual(Array(10).fill("access-next"));
  });

  test("a late refresh cannot resurrect credentials after logout", async () => {
    await setCredentials(credentials("first"));
    const response = deferred<RefreshResponse>();
    const refresh = jest.fn(() => response.promise);
    const request = validToken(refresh, true);
    await Promise.resolve();
    await Promise.resolve();
    await setToken(null);
    response.resolve(rotated("late"));
    expect(await request).toBeNull();
    expect(await getToken()).toBeNull();
    expect(await getRefreshToken()).toBeNull();
    expect(await SecureStore.getItemAsync("auth.refresh")).toBeNull();
  });

  test("a late refresh cannot replace a newly signed-in account", async () => {
    await setCredentials(credentials("first"));
    const response = deferred<RefreshResponse>();
    const request = validToken(() => response.promise, true);
    await Promise.resolve();
    await Promise.resolve();
    await setCredentials(credentials("second"));
    response.resolve(rotated("late"));
    expect(await request).toBeNull();
    expect(await getToken()).toBe("access-second");
    expect(await SecureStore.getItemAsync("auth.refresh")).toBe(
      "refresh-second",
    );
  });

  test("temporary refresh failure preserves the stored refresh for a retry", async () => {
    await setCredentials(credentials("first"));
    const response = deferred<RefreshResponse>();
    const request = validToken(() => response.promise, true);
    response.reject(new Error("synthetic offline"));
    await expect(request).rejects.toThrow("synthetic offline");
    expect(await getRefreshToken()).toBe("refresh-first");
    expect(await SecureStore.getItemAsync("auth.refresh")).toBe(
      "refresh-first",
    );
    expect(await validToken(async () => rotated("retry"), true)).toBe(
      "access-retry",
    );
  });

  test("an unexpired access token does not rotate on ordinary reads", async () => {
    await setCredentials(credentials("first"));
    const refresh = jest.fn(async () => rotated("unused"));
    expect(await validToken(refresh)).toBe("access-first");
    expect(refresh).not.toHaveBeenCalled();
  });

  test("file-mode legacy login survives cold start without a refresh request", async () => {
    await setToken("legacy-file-mode");
    __resetTokenCacheForTests();
    const refresh = jest.fn(async () => rotated("unused"));
    expect(await validToken(refresh, true)).toBe("legacy-file-mode");
    expect(refresh).not.toHaveBeenCalled();
    expect(await getRefreshToken()).toBeNull();
  });
});
