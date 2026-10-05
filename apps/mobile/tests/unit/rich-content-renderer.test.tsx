import React from "react";
import { render } from "@testing-library/react-native";

// ThemeProvider 依赖 prefs（AsyncStorage）；测试内换成内存实现，隔离外部环境。
jest.mock("@/platform/prefs", () => ({
  loadPref: jest.fn(async () => null),
  savePref: jest.fn(async () => undefined),
  PREF_KEYS: { theme: "theme", fontScale: "fontScale" },
}));

import { ThemeProvider } from "@/ui/ThemeProvider";
import { RichContentRenderer } from "@/ui/rich-content";

const SAMPLE = [
  "# 勾股定理",
  "",
  "在**直角三角形**中，$a^2+b^2=c^2$，即 \\(c=\\sqrt{a^2+b^2}\\)。",
  "",
  "```py",
  "c = (a*a + b*b) ** 0.5",
  "```",
  "",
  "> 直角边：a、b",
  "",
  "- 步骤一",
  "  - 子步骤",
  "- 步骤二",
  "",
  "| 边 | 值 |",
  "| :- | -: |",
  "| 斜边 | 5 |",
  "",
  "$$",
  "c^2 = a^2 + b^2",
  "$$",
  "",
  "详见[维基](https://example.com)。",
].join("\n");

describe("RichContentRenderer 冒烟", () => {
  test("混合块渲染不崩溃，关键文本可达", async () => {
    const { findByLabelText } = await render(
      <ThemeProvider>
        <RichContentRenderer text={SAMPLE} />
      </ThemeProvider>,
    );
    expect(await findByLabelText(SAMPLE)).toBeTruthy();
  });

  test("流式模式渲染光标且不崩溃", async () => {
    const { findByText } = await render(
      <ThemeProvider>
        <RichContentRenderer text="正在输出 **部分**" streaming />
      </ThemeProvider>,
    );
    expect(await findByText("正在输出 ")).toBeTruthy();
  });

  test("空文本 + 流式渲染独立光标", async () => {
    const { findByText } = await render(
      <ThemeProvider>
        <RichContentRenderer text="" streaming />
      </ThemeProvider>,
    );
    expect(await findByText("▍")).toBeTruthy();
  });
});
