import { resolveMaxPanes } from "@/shell/adaptive/window-class";
import { windowHeightClass, windowWidthClass } from "@next-tutor/design-tokens";

describe("adaptive pane 策略", () => {
  test("compact/medium 宽度单 pane", () => {
    expect(resolveMaxPanes("compact", "medium")).toBe(1);
    expect(resolveMaxPanes("medium", "expanded")).toBe(1);
  });

  test("expanded 双 pane，large/extraLarge 三 pane", () => {
    expect(resolveMaxPanes("expanded", "medium")).toBe(2);
    expect(resolveMaxPanes("large", "expanded")).toBe(3);
    expect(resolveMaxPanes("extraLarge", "expanded")).toBe(3);
  });

  test("CompactHeight 任何宽度都单 pane", () => {
    expect(resolveMaxPanes("expanded", "compact")).toBe(1);
    expect(resolveMaxPanes("extraLarge", "compact")).toBe(1);
  });

  test.each([
    [390, "compact"],
    [430, "compact"],
    [599, "compact"],
    [600, "medium"],
    [768, "medium"],
    [839, "medium"],
    [840, "expanded"],
    [1024, "expanded"],
    [1199, "expanded"],
    [1200, "large"],
    [1366, "large"],
    [1599, "large"],
    [1600, "extraLarge"],
  ])("shared width %s resolves to %s", (width, expected) => {
    expect(windowWidthClass(Number(width))).toBe(expected);
  });

  test("phone landscape keeps one content pane even at desktop width", () => {
    expect(windowHeightClass(479)).toBe("compact");
    expect(windowHeightClass(480)).toBe("medium");
    for (const width of [390, 600, 840, 1366, 1600])
      expect(
        resolveMaxPanes(windowWidthClass(width), windowHeightClass(390)),
      ).toBe(1);
  });
});
