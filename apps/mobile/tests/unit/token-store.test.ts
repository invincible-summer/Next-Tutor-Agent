import {
  __resetTokenCacheForTests,
  getToken,
  setToken,
} from "@/platform/token-store";

describe("token-store（SecureStore + 内存缓存）", () => {
  beforeEach(async () => {
    await setToken(null);
    __resetTokenCacheForTests();
  });

  test("写入后可读取；置空即删除", async () => {
    __resetTokenCacheForTests();
    expect(await getToken()).toBeNull();
    await setToken("tk-1");
    __resetTokenCacheForTests();
    expect(await getToken()).toBe("tk-1");
    await setToken(null);
    __resetTokenCacheForTests();
    expect(await getToken()).toBeNull();
  });
});
