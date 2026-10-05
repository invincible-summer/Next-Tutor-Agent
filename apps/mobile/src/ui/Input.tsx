import React, { useState } from "react";
import {
  StyleSheet,
  Text,
  TextInput,
  View,
  type TextInputProps,
} from "react-native";

import { useTheme } from "./ThemeProvider";
import { alpha } from "./theme";

interface FieldProps {
  label?: string;
  hint?: string | undefined;
  error?: string;
  children: React.ReactNode;
}

/** 表单字段容器：label + 控件 + hint/error。 */
export function Field({ label, hint, error, children }: FieldProps) {
  const { theme } = useTheme();
  return (
    <View style={styles.field}>
      {label ? (
        <Text
          style={[
            theme.type.label,
            { color: theme.colors["fg-secondary"], marginBottom: 6 },
          ]}
        >
          {label}
        </Text>
      ) : null}
      {children}
      {error ? (
        <Text
          style={[
            theme.type.caption,
            { color: theme.colors.danger, marginTop: 4 },
          ]}
        >
          {error}
        </Text>
      ) : hint ? (
        <Text
          style={[
            theme.type.caption,
            { color: theme.colors["fg-tertiary"], marginTop: 4 },
          ]}
        >
          {hint}
        </Text>
      ) : null}
    </View>
  );
}

type TextFieldProps = TextInputProps & { invalid?: boolean };

/** 文本输入：圆角 8、focus 时 accent 描边 + 浅光晕（对齐 Web INPUT_CLS）。 */
export function TextField({
  invalid = false,
  style,
  ...props
}: TextFieldProps) {
  const { theme } = useTheme();
  const [focused, setFocused] = useState(false);
  return (
    <TextInput
      {...props}
      onFocus={(e) => {
        setFocused(true);
        props.onFocus?.(e);
      }}
      onBlur={(e) => {
        setFocused(false);
        props.onBlur?.(e);
      }}
      placeholderTextColor={theme.colors["fg-tertiary"]}
      style={[
        theme.type.body,
        {
          borderRadius: 14,
          borderWidth: 1,
          borderColor: invalid
            ? theme.colors.danger
            : focused
              ? theme.colors.accent
              : theme.colors.border,
          backgroundColor: theme.colors.surface,
          color: theme.colors.fg,
          paddingHorizontal: 12,
          paddingVertical: 10,
          minHeight: 52,
        },
        focused && !invalid
          ? {
              shadowColor: theme.colors.accent,
              shadowOpacity: 0.14,
              shadowRadius: 3,
              shadowOffset: { width: 0, height: 0 },
            }
          : null,
        style,
      ]}
    />
  );
}

export function TextArea({ invalid = false, style, ...props }: TextFieldProps) {
  return (
    <TextField
      {...props}
      invalid={invalid}
      multiline
      textAlignVertical="top"
      style={[{ minHeight: 96 }, style]}
    />
  );
}

const styles = StyleSheet.create({
  field: { alignSelf: "stretch" },
});

export { alpha };
