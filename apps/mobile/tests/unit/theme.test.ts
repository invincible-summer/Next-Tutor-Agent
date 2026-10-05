import { darkColors, lightColors } from "@next-tutor/design-tokens";

import { alpha, buildTheme } from "@/ui/theme";

describe("theme", () => {
  test("light/dark 颜色与 design-tokens 一致", () => {
    const light = buildTheme("light");
    const dark = buildTheme("dark");
    expect(light.colors.accent).toBe("rgb(37, 109, 102)");
    expect(dark.colors.accent).toBe("rgb(86, 173, 161)");
    expect(light.colors.bg).toBe("rgb(246, 245, 241)");
    expect(dark.colors.bg).toBe("rgb(16, 18, 22)");
    expect(light.colors.onAccent).toBe("#ffffff");
    expect(dark.colors.onAccent).toBe("rgb(16, 32, 30)");
    // 全部 token 键都落进主题
    expect(Object.keys(lightColors)).toEqual(Object.keys(darkColors));
    for (const key of Object.keys(lightColors)) {
      expect(light.colors).toHaveProperty(key);
      expect(dark.colors).toHaveProperty(key);
    }
  });

  test("fontScale 影响字号阶梯", () => {
    const base = buildTheme("light", 1);
    const large = buildTheme("light", 1.5);
    expect(large.type.body.fontSize).toBeCloseTo(
      (base.type.body.fontSize ?? 0) * 1.5,
    );
  });

  test("200% typography scales line heights together and keeps system title fonts", () => {
    const base = buildTheme("light");
    const large = buildTheme("dark", 2);
    for (const key of Object.keys(base.type) as (keyof typeof base.type)[]) {
      expect(large.type[key].fontSize).toBeCloseTo(
        (base.type[key].fontSize ?? 0) * 2,
      );
      expect(large.type[key].lineHeight).toBeCloseTo(
        (base.type[key].lineHeight ?? 0) * 2,
      );
    }
    expect(large.type.title.fontFamily).toBeUndefined();
    expect(large.type.body.fontFamily).toBeUndefined();
  });

  test("alpha 叠加透明度", () => {
    expect(alpha("rgb(37, 109, 102)", 0.4)).toBe("rgba(37, 109, 102, 0.4)");
    expect(alpha("invalid", 0.5)).toBe("invalid");
  });
});
