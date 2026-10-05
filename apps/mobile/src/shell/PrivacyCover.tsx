import React, { useEffect, useState } from "react";
import { AppState, Modal, Text, View } from "react-native";
import { BookOpen } from "lucide-react-native";
import { useTheme } from "@/ui/ThemeProvider";
export function PrivacyCover() {
  const { theme } = useTheme();
  const [covered, setCovered] = useState(AppState.currentState !== "active");
  useEffect(() => {
    const listener = AppState.addEventListener("change", (state) =>
      setCovered(state !== "active"),
    );
    return () => listener.remove();
  }, []);
  return (
    <Modal
      visible={covered}
      animationType="none"
      presentationStyle="fullScreen"
      statusBarTranslucent
      navigationBarTranslucent
    >
      <View
        testID="privacy-cover"
        accessibilityLabel="Next Tutor"
        style={{
          flex: 1,
          alignItems: "center",
          justifyContent: "center",
          gap: 20,
          backgroundColor: theme.colors.bg,
        }}
      >
        <BookOpen size={48} color={theme.colors.accent} />
        <Text style={[theme.type.title, { color: theme.colors.fg }]}>
          Next Tutor
        </Text>
      </View>
    </Modal>
  );
}
