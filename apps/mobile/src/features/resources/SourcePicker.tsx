import React, { useState } from "react";
import { View } from "react-native";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { useServerQuery } from "@/lib/server-state";
import { useAuth } from "@/providers/AuthProvider";
import { useWorkspace } from "@/providers/WorkspaceProvider";
import { Button, Card, Chip, ListRow, Sheet, TextField } from "@/ui";
import {
  Body,
  Hint,
  Pager,
  QueryState,
  Section,
  SheetBody,
  SheetHeader,
} from "@/ui/Elements";
export function SourceContent() {
  const c = useCopy();
  const scope = useWorkspace();
  const { state } = useAuth();
  const [q, setQ] = useState("");
  const [bookPage, setBookPage] = useState(1);
  const [filePage, setFilePage] = useState(1);
  const books = useServerQuery(["textbooks"], (s) =>
    apiClient().library.textbooks.list(s),
  );
  const guest = useServerQuery(
    ["guest-textbooks"],
    () => apiClient().guest.textbooks(),
    { enabled: state.status === "guest", public: true },
  );
  const library = useServerQuery(["library"], (s) =>
    apiClient().library.list(s),
  );
  const toggle = (id: string, type: "book" | "file") => {
    const set = type === "book" ? scope.textbookIds : scope.fileIds;
    const next = set.includes(id) ? set.filter((i) => i !== id) : [...set, id];
    scope.setSources(
      type === "book" ? next : scope.textbookIds,
      type === "file" ? next : scope.fileIds,
    );
  };
  const visibleBooks =
    (state.status === "guest"
      ? guest.data?.items
      : books.data?.textbooks
    )?.filter((book) =>
      (book.title ?? "")
        .toLocaleLowerCase()
        .includes(q.trim().toLocaleLowerCase()),
    ) ?? [];
  const visibleFiles =
    library.data?.files.filter((file) =>
      file.filename.toLocaleLowerCase().includes(q.trim().toLocaleLowerCase()),
    ) ?? [];
  return (
    <Body>
      <Hint>
        {c(
          "选择这次辅导的资料。工作区资料仍由服务端授权。",
          "Choose sources for this conversation. Your server controls workspace access.",
        )}
      </Hint>
      <TextField
        value={q}
        onChangeText={(value) => {
          setQ(value);
          setBookPage(1);
          setFilePage(1);
        }}
        accessibilityLabel={c("搜索来源", "Search sources")}
        placeholder={c("搜索来源", "Search sources")}
      />
      <Section title={c("教材来源", "Textbooks")}>
        <QueryState
          query={state.status === "guest" ? guest : books}
          empty={!visibleBooks.length}
        >
          {visibleBooks.slice((bookPage - 1) * 12, bookPage * 12).map((b) => (
            <Chip
              key={b.id}
              label={b.title ?? c("公共教材", "Public textbook")}
              active={scope.textbookIds.includes(b.id)}
              onPress={() => toggle(b.id, "book")}
            />
          ))}
          <Pager
            page={bookPage}
            total={visibleBooks.length}
            pageSize={12}
            onChange={setBookPage}
          />
        </QueryState>
      </Section>
      {state.status === "signed-in" ? (
        <Section title={c("个人文件", "Personal files")}>
          <QueryState query={library} empty={!visibleFiles.length}>
            {visibleFiles.slice((filePage - 1) * 12, filePage * 12).map((f) => (
              <Chip
                key={f.id}
                label={f.filename}
                active={scope.fileIds.includes(f.id)}
                onPress={() => toggle(f.id, "file")}
              />
            ))}
            <Pager
              page={filePage}
              total={visibleFiles.length}
              pageSize={12}
              onChange={setFilePage}
            />
          </QueryState>
        </Section>
      ) : null}
      <Button
        title={c("清空选择", "Clear selection")}
        variant="ghost"
        onPress={() => scope.setSources([], [])}
      />
    </Body>
  );
}
export function SourcePicker({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const c = useCopy();
  return (
    <Sheet
      open={open}
      onClose={onClose}
      label={c("辅导来源", "Tutoring sources")}
    >
      <SheetHeader
        title={c("辅导来源", "Tutoring sources")}
        onClose={onClose}
      />
      <SheetBody>
        <SourceContent />
      </SheetBody>
    </Sheet>
  );
}
