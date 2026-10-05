import React, { useEffect } from "react";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { I18nProvider } from "./I18nProvider";
import { QueryProvider } from "./QueryProvider";
import { AuthProvider } from "./AuthProvider";
import { WorkspaceProvider } from "./WorkspaceProvider";
import { AssistantSheet } from "@/features/assistant/AssistantSheet";
import { PrivacyCover } from "@/shell/PrivacyCover";
import { AssistantLauncher } from "@/shell/AssistantLauncher";
import { purgeLegacyDrafts } from "@/features/chat/lib/drafts";
import { ThemeProvider } from "@/ui/ThemeProvider";
import { ToastProvider } from "@/ui/Toast";
import { useUiPrefs } from "@/stores/ui";

/** 挂载时回源 UI 偏好（学段/回答语言）。 */
function UiPrefsHydrator() {
  const hydrate = useUiPrefs((s) => s.hydrate);
  useEffect(() => {
    void hydrate();
    void purgeLegacyDrafts();
  }, [hydrate]);
  return null;
}

/** 根 Provider 组合：手势 → SafeArea → Theme → I18n → Query → Auth → Toast。 */
export function AppProviders({ children }: { children: React.ReactNode }) {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <ThemeProvider>
          <I18nProvider>
            <QueryProvider>
              <AuthProvider>
                <ToastProvider>
                  <WorkspaceProvider>
                    <UiPrefsHydrator />
                    {children}
                    <AssistantLauncher />
                    <AssistantSheet />
                    <PrivacyCover />
                  </WorkspaceProvider>
                </ToastProvider>
              </AuthProvider>
            </QueryProvider>
          </I18nProvider>
        </ThemeProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
