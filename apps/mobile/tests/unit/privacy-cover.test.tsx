import React from "react";
import { AppState, type AppStateStatus } from "react-native";
import { act, render } from "@testing-library/react-native";
import { ThemeProvider } from "@/ui/ThemeProvider";
import { PrivacyCover } from "@/shell/PrivacyCover";

describe("background content cover", () => {
  test("inactive/background covers content and active state restores it", async () => {
    const previous = AppState.currentState;
    AppState.currentState = "active";
    let update: ((state: AppStateStatus) => void) | undefined;
    const remove = jest.fn();
    const listener = jest
      .spyOn(AppState, "addEventListener")
      .mockImplementation((_type, callback) => {
        update = callback;
        return { remove };
      });
    try {
      const ui = await render(
        <ThemeProvider>
          <PrivacyCover />
        </ThemeProvider>,
      );
      expect(ui.queryByTestId("privacy-cover")).toBeNull();
      await act(() => update!("inactive"));
      expect(ui.getByTestId("privacy-cover")).toBeTruthy();
      await act(() => update!("background"));
      expect(ui.getByTestId("privacy-cover")).toBeTruthy();
      await act(() => update!("active"));
      expect(ui.queryByTestId("privacy-cover")).toBeNull();
      await ui.unmount();
      expect(remove).toHaveBeenCalled();
    } finally {
      listener.mockRestore();
      AppState.currentState = previous;
    }
  });
});
