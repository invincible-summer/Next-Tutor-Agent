import React, { useState } from "react";
import { Text } from "react-native";
import { useRouter } from "expo-router";
import * as Clipboard from "expo-clipboard";
import { useAuth } from "@/providers/AuthProvider";
import { useCopy } from "@/lib/copy";
import { useAction } from "@/lib/server-state";
import { apiClient } from "@/lib/api";
import { domainRoute } from "@/shell/routes";
import { Button, Sheet, useTheme } from "@/ui";
import { SheetBody, SheetHeader } from "@/ui/Elements";
export function MessageMenu({
  text,
  open,
  onClose,
  onQuote,
}: {
  text: string;
  open: boolean;
  onClose: () => void;
  onQuote: () => void;
}) {
  const c = useCopy();
  const { theme } = useTheme();
  const router = useRouter();
  const action = useAction();
  const { state } = useAuth();
  const [select, setSelect] = useState(false);
  return (
    <Sheet
      open={open}
      onClose={onClose}
      label={c("消息操作", "Message actions")}
    >
      <SheetHeader title={c("消息操作", "Message actions")} onClose={onClose} />
      <SheetBody>
        <Button
          title={c("复制", "Copy")}
          variant="outline"
          onPress={() => void Clipboard.setStringAsync(text).then(onClose)}
        />
        <Button
          title={c("引用到输入框", "Quote in composer")}
          variant="outline"
          onPress={() => {
            onQuote();
            onClose();
          }}
        />
        <Button
          title={c("保存为笔记", "Save as note")}
          variant="outline"
          loading={action.pending}
          disabled={state.status !== "signed-in"}
          onPress={() =>
            void action.run(
              () =>
                apiClient().notes.createNote({
                  title: text.replace(/^#+\s*/, "").slice(0, 32),
                  content: text,
                }),
              (r) => {
                onClose();
                router.push(domainRoute({ kind: "note", id: r.note.id }));
              },
            )
          }
        />
        <Button
          title={c("选择文本", "Select text")}
          variant="ghost"
          onPress={() => setSelect(true)}
        />
        {select ? (
          <Text
            selectable
            style={[theme.type.body, { color: theme.colors.fg }]}
          >
            {text}
          </Text>
        ) : null}
      </SheetBody>
    </Sheet>
  );
}
