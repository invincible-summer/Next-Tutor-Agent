import React from "react";
import { StyleSheet } from "react-native";
import { fireEvent, render } from "@testing-library/react-native";
import { AssistantLauncher } from "@/shell/AssistantLauncher";
import { ThemeProvider } from "@/ui/ThemeProvider";

let mockStatus = "signed-in";
let mockSegments = ["(main)"];
let mockOpen = false;
const mockSetOpen = jest.fn();
jest.mock("expo-router", () => ({ useSegments: () => mockSegments }));
jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 20, bottom: 20, left: 0, right: 0 }),
}));
jest.mock("@/providers/AuthProvider", () => ({
  useAuth: () => ({ state: { status: mockStatus } }),
}));
jest.mock("@/stores/assistant-ui", () => ({
  useAssistantUi: () => ({ open: mockOpen, setOpen: mockSetOpen }),
}));
jest.mock("@/lib/copy", () => ({ useCopy: () => (zh: string) => zh }));

describe("quiet navigation assistant entry", () => {
  beforeEach(() => {
    mockStatus = "signed-in";
    mockSegments = ["(main)"];
    mockOpen = false;
    mockSetOpen.mockClear();
  });

  test("handle remains bordered and partly hidden with a reachable 48 dp hit area", async () => {
    const ui = await render(
      <ThemeProvider>
        <AssistantLauncher />
      </ThemeProvider>,
    );
    const handle = ui.getByTestId("assistant-edge-handle");
    const style = StyleSheet.flatten(handle.props.style);
    expect(style.width).toBeGreaterThanOrEqual(48);
    expect(style.minHeight).toBeGreaterThanOrEqual(48);
    expect(style.right).toBeLessThan(0);
    expect(style.borderWidth).toBe(1);
    // The outward offset is recovered by its inward-only hitSlop.
    expect(
      style.width + style.right + handle.props.hitSlop.left,
    ).toBeGreaterThanOrEqual(48);
    expect(handle.props.accessibilityRole).toBe("button");
    expect(handle.props.accessibilityLabel).toBe("打开导航助手");
    await fireEvent.press(handle);
    expect(mockSetOpen).toHaveBeenCalledWith(true);
  });

  test.each(["signed-out", "loading", "error"])(
    "is absent in %s state",
    async (status) => {
      mockStatus = status;
      const ui = await render(
        <ThemeProvider>
          <AssistantLauncher />
        </ThemeProvider>,
      );
      expect(ui.queryByTestId("assistant-edge-handle")).toBeNull();
    },
  );

  test("auth screen and the open panel hide the edge handle", async () => {
    mockSegments = ["(auth)"];
    const ui = await render(
      <ThemeProvider>
        <AssistantLauncher />
      </ThemeProvider>,
    );
    expect(ui.queryByTestId("assistant-edge-handle")).toBeNull();
    mockSegments = ["(main)"];
    mockOpen = true;
    await ui.rerender(
      <ThemeProvider>
        <AssistantLauncher />
      </ThemeProvider>,
    );
    expect(ui.queryByTestId("assistant-edge-handle")).toBeNull();
  });
});
