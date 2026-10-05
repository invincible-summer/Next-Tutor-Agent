import { en } from "@/lib/i18n/dicts/base.en";
import { zh } from "@/lib/i18n/dicts/base.zh";
import { makeTranslator } from "@/lib/i18n";
import { registerDicts } from "@/lib/i18n/registry";

describe("i18n 字典", () => {
  test("base zh/en 键集一致", () => {
    expect(Object.keys(zh).sort()).toEqual(Object.keys(en).sort());
  });

  test("关键键存在", () => {
    for (const key of [
      "nav.home",
      "nav.tutor",
      "common.retry",
      "auth.signIn.action",
    ]) {
      expect(zh[key]).toBeTruthy();
      expect(en[key]).toBeTruthy();
    }
  });

  test("注册表合并：feature 字典并入翻译器", () => {
    registerDicts(
      { "test.registered": "已注册" },
      { "test.registered": "registered" },
    );
    expect(makeTranslator("zh")("test.registered")).toBe("已注册");
    expect(makeTranslator("en")("test.registered")).toBe("registered");
    // base 键不受新增注册影响
    expect(makeTranslator("zh")("nav.home")).toBe("首页");
  });

  test("fallback 链：dict → fallback → key", () => {
    const t = makeTranslator("zh");
    expect(t("missing.key", "回退文案")).toBe("回退文案");
    expect(t("missing.key")).toBe("missing.key");
  });
});
