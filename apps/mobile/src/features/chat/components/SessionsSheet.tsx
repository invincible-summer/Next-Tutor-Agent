import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  SectionList,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import {
  CirclePlus,
  FolderOpen,
  Layers,
  MessageSquareText,
  Pencil,
  Trash,
} from "lucide-react-native";

import { useTheme } from "@/ui/ThemeProvider";
import { alpha } from "@/ui/theme";
import { Sheet, Dialog } from "@/ui/Sheet";
import { ListRow } from "@/ui/ListRow";
import { Button } from "@/ui/Button";
import { TextField } from "@/ui/Input";
import { useI18n } from "@/providers/I18nProvider";
import { apiClient } from "@/lib/api";
import { useToast } from "@/ui/Toast";
import type { SessionItem, WorkspaceItem } from "../model/types";
import { useChatStore } from "../store/chatStore";

interface SessionSection {
  key: string;
  title: string;
  workspaceId: string | null;
  data: SessionItem[];
}

function formatRelative(epochSec: number, lang: string): string {
  const diff = Date.now() / 1000 - epochSec;
  if (diff < 60) return lang === "en" ? "just now" : "刚刚";
  if (diff < 3600)
    return `${Math.floor(diff / 60)}${lang === "en" ? "m ago" : " 分钟前"}`;
  if (diff < 86400)
    return `${Math.floor(diff / 3600)}${lang === "en" ? "h ago" : " 小时前"}`;
  if (diff < 86400 * 7)
    return `${Math.floor(diff / 86400)}${lang === "en" ? "d ago" : " 天前"}`;
  const d = new Date(epochSec * 1000);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/**
 * 会话 Sheet（非常驻，header 按钮打开）：按工作区分组的会话列表 +
 * 重命名/移动/删除（含记忆遗忘选项）+ 新工作区。
 */
export function SessionsSheet({
  open,
  onClose,
  currentSessionId,
}: {
  open: boolean;
  onClose: () => void;
  currentSessionId: string | null;
}) {
  const { theme } = useTheme();
  const { t, lang } = useI18n();
  const router = useRouter();
  const toast = useToast();
  const sessions = useChatStore((s) => s.sessions);
  const setSessions = useChatStore((s) => s.setSessions);

  const [workspaces, setWorkspaces] = useState<WorkspaceItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [renameTarget, setRenameTarget] = useState<SessionItem | null>(null);
  const [renameText, setRenameText] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<SessionItem | null>(null);
  const [forgetMemory, setForgetMemory] = useState(false);
  const [moveTarget, setMoveTarget] = useState<SessionItem | null>(null);
  const [createWsOpen, setCreateWsOpen] = useState(false);
  const [createWsName, setCreateWsName] = useState("");
  const [actionBusy, setActionBusy] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [sessionsRes, wsRes] = await Promise.all([
        apiClient().chat.listSessions<{ sessions: SessionItem[] }>(),
        apiClient().workspace.list<{ workspaces: WorkspaceItem[] }>(),
      ]);
      setSessions(sessionsRes.sessions ?? []);
      setWorkspaces(wsRes.workspaces ?? []);
    } catch {
      toast(t("common.error.generic"), "error");
    } finally {
      setLoading(false);
    }
  }, [setSessions, toast, t]);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  const sections = useMemo<SessionSection[]>(() => {
    const byWorkspace = new Map<string, SessionItem[]>();
    const loose: SessionItem[] = [];
    for (const s of sessions) {
      if (s.workspace_id) {
        const list = byWorkspace.get(s.workspace_id) ?? [];
        list.push(s);
        byWorkspace.set(s.workspace_id, list);
      } else {
        loose.push(s);
      }
    }
    const out: SessionSection[] = [];
    for (const ws of workspaces) {
      const data = byWorkspace.get(ws.workspace_id);
      if (data?.length) {
        out.push({
          key: ws.workspace_id,
          title: ws.name,
          workspaceId: ws.workspace_id,
          data,
        });
      }
    }
    if (loose.length > 0) {
      out.push({
        key: "__loose__",
        title: t("sessions.move.none"),
        workspaceId: null,
        data: loose,
      });
    }
    return out;
  }, [sessions, workspaces, t]);

  const openSession = useCallback(
    (sessionId: string) => {
      onClose();
      router.push({
        pathname: "/(main)/tutor/[sessionId]",
        params: { sessionId: encodeURIComponent(sessionId) },
      });
    },
    [onClose, router],
  );

  const doRename = useCallback(async () => {
    if (!renameTarget || !renameText.trim()) return;
    setActionBusy(true);
    try {
      await apiClient().chat.renameSession(
        renameTarget.session_id,
        renameText.trim(),
      );
      setRenameTarget(null);
      await refresh();
    } catch {
      toast(t("common.error.generic"), "error");
    } finally {
      setActionBusy(false);
    }
  }, [renameTarget, renameText, refresh, toast, t]);

  const doDelete = useCallback(async () => {
    if (!deleteTarget) return;
    setActionBusy(true);
    try {
      await apiClient().chat.deleteSession(
        deleteTarget.session_id,
        forgetMemory,
      );
      if (deleteTarget.session_id === currentSessionId) {
        useChatStore.getState().newChat();
        router.replace("/(main)/tutor");
      }
      setDeleteTarget(null);
      setForgetMemory(false);
      await refresh();
    } catch {
      toast(t("common.error.generic"), "error");
    } finally {
      setActionBusy(false);
    }
  }, [deleteTarget, forgetMemory, currentSessionId, refresh, router, toast, t]);

  const doMove = useCallback(
    async (workspaceId: string) => {
      if (!moveTarget) return;
      setActionBusy(true);
      try {
        await apiClient().workspace.moveSession(
          workspaceId,
          moveTarget.session_id,
        );
        setMoveTarget(null);
        await refresh();
      } catch {
        toast(t("common.error.generic"), "error");
      } finally {
        setActionBusy(false);
      }
    },
    [moveTarget, refresh, toast, t],
  );

  const doCreateWorkspace = useCallback(async () => {
    if (!createWsName.trim()) return;
    setActionBusy(true);
    try {
      const created = await apiClient().workspace.create(createWsName.trim());
      setCreateWsOpen(false);
      setCreateWsName("");
      if (moveTarget) {
        await doMove(created.workspace_id);
      } else {
        await refresh();
      }
    } catch {
      toast(t("common.error.generic"), "error");
    } finally {
      setActionBusy(false);
    }
  }, [createWsName, moveTarget, doMove, refresh, toast, t]);

  return (
    <>
      <Sheet
        open={open}
        onClose={onClose}
        heightRatio={0.78}
        accessibilityLabel={t("chat.sessions")}
      >
        <View style={styles.headerRow}>
          <Text
            style={[theme.type.titleSmall, { color: theme.colors.fg, flex: 1 }]}
          >
            {t("chat.sessions")}
          </Text>
          <Pressable
            onPress={() => {
              onClose();
              router.push("/(main)/tutor");
            }}
            accessibilityRole="button"
            accessibilityLabel={t("chat.new")}
            style={({ pressed }) => [
              styles.newChatBtn,
              {
                backgroundColor: pressed
                  ? theme.colors["accent-strong"]
                  : theme.colors["accent-soft"],
              },
            ]}
          >
            <CirclePlus size={14} color={theme.colors["accent-strong"]} />
            <Text
              style={[
                theme.type.label,
                {
                  color: theme.colors["accent-strong"],
                  marginLeft: 5,
                  fontSize: 12.5,
                },
              ]}
            >
              {t("chat.new")}
            </Text>
          </Pressable>
        </View>

        {loading && sessions.length === 0 ? (
          <View style={styles.loadingWrap}>
            <ActivityIndicator color={theme.colors.accent} />
          </View>
        ) : sections.length === 0 ? (
          <Text
            style={[
              theme.type.body,
              {
                color: theme.colors.muted,
                textAlign: "center",
                paddingVertical: 32,
              },
            ]}
          >
            {t("sessions.empty")}
          </Text>
        ) : (
          <SectionList
            sections={sections}
            keyExtractor={(item) => item.session_id}
            stickySectionHeadersEnabled={false}
            contentContainerStyle={{ paddingBottom: 12 }}
            renderSectionHeader={({ section }) => (
              <View style={styles.sectionHeader}>
                {section.workspaceId ? (
                  <FolderOpen size={12} color={theme.colors["accent-strong"]} />
                ) : (
                  <Layers size={12} color={theme.colors.muted} />
                )}
                <Text
                  style={[
                    theme.type.caption,
                    {
                      color: section.workspaceId
                        ? theme.colors["accent-strong"]
                        : theme.colors.muted,
                      fontWeight: "600",
                      marginLeft: 6,
                    },
                  ]}
                >
                  {section.title}
                </Text>
              </View>
            )}
            renderItem={({ item }) => (
              <View
                style={[
                  styles.sessionRowWrap,
                  item.session_id === currentSessionId
                    ? {
                        backgroundColor: alpha(
                          theme.colors["accent-soft"],
                          0.5,
                        ),
                        borderRadius: 8,
                      }
                    : null,
                ]}
              >
                <ListRow
                  title={item.title || t("sessions.untitled")}
                  subtitle={formatRelative(item.updated_at, lang)}
                  left={
                    <MessageSquareText size={15} color={theme.colors.muted} />
                  }
                  onPress={() => openSession(item.session_id)}
                  right={
                    <View style={styles.rowActions}>
                      <Pressable
                        onPress={() => {
                          setRenameTarget(item);
                          setRenameText(item.title);
                        }}
                        accessibilityRole="button"
                        accessibilityLabel={t("sessions.rename")}
                        hitSlop={8}
                        style={({ pressed }) => [
                          { opacity: pressed ? 0.5 : 1, padding: 6 },
                        ]}
                      >
                        <Pencil size={14} color={theme.colors.muted} />
                      </Pressable>
                      <Pressable
                        onPress={() => setMoveTarget(item)}
                        accessibilityRole="button"
                        accessibilityLabel={t("sessions.move")}
                        hitSlop={8}
                        style={({ pressed }) => [
                          { opacity: pressed ? 0.5 : 1, padding: 6 },
                        ]}
                      >
                        <FolderOpen size={14} color={theme.colors.muted} />
                      </Pressable>
                      <Pressable
                        onPress={() => {
                          setDeleteTarget(item);
                          setForgetMemory(false);
                        }}
                        accessibilityRole="button"
                        accessibilityLabel={t("sessions.delete")}
                        hitSlop={8}
                        style={({ pressed }) => [
                          { opacity: pressed ? 0.5 : 1, padding: 6 },
                        ]}
                      >
                        <Trash size={14} color={theme.colors.danger} />
                      </Pressable>
                    </View>
                  }
                />
              </View>
            )}
          />
        )}
      </Sheet>

      {/* 重命名对话框 */}
      <Dialog
        open={renameTarget !== null}
        onClose={() => setRenameTarget(null)}
      >
        <Text
          style={[
            theme.type.titleSmall,
            { color: theme.colors.fg, marginBottom: 12 },
          ]}
        >
          {t("sessions.rename.title")}
        </Text>
        <TextField
          value={renameText}
          onChangeText={setRenameText}
          placeholder={t("sessions.rename.placeholder")}
          autoFocus
        />
        <View style={styles.dialogActions}>
          <Button
            title={t("common.cancel")}
            variant="ghost"
            size="sm"
            onPress={() => setRenameTarget(null)}
          />
          <Button
            title={t("common.save")}
            size="sm"
            loading={actionBusy}
            disabled={!renameText.trim()}
            onPress={() => void doRename()}
          />
        </View>
      </Dialog>

      {/* 删除确认（含记忆遗忘选项） */}
      <Dialog
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
      >
        <Text style={[theme.type.titleSmall, { color: theme.colors.fg }]}>
          {t("sessions.delete.title")}
        </Text>
        <Text
          style={[
            theme.type.body,
            { color: theme.colors["fg-secondary"], marginTop: 8 },
          ]}
        >
          {t("sessions.delete.desc")}
        </Text>
        <Pressable
          onPress={() => setForgetMemory((v) => !v)}
          accessibilityRole="checkbox"
          accessibilityState={{ checked: forgetMemory }}
          style={styles.forgetRow}
        >
          <View
            style={[
              styles.checkbox,
              {
                borderColor: forgetMemory
                  ? theme.colors.accent
                  : theme.colors.border,
                backgroundColor: forgetMemory
                  ? theme.colors.accent
                  : "transparent",
              },
            ]}
          >
            {forgetMemory ? (
              <Text style={{ color: theme.colors.onAccent, fontSize: 11 }}>
                ✓
              </Text>
            ) : null}
          </View>
          <Text
            style={[
              theme.type.body,
              { color: theme.colors["fg-secondary"], marginLeft: 8, flex: 1 },
            ]}
          >
            {t("sessions.delete.forget")}
          </Text>
        </Pressable>
        <View style={styles.dialogActions}>
          <Button
            title={t("common.cancel")}
            variant="ghost"
            size="sm"
            onPress={() => setDeleteTarget(null)}
          />
          <Button
            title={t("common.delete")}
            variant="danger"
            size="sm"
            loading={actionBusy}
            onPress={() => void doDelete()}
          />
        </View>
      </Dialog>

      {/* 移动到工作区 */}
      <Sheet
        open={moveTarget !== null}
        onClose={() => setMoveTarget(null)}
        heightRatio={0.55}
        accessibilityLabel={t("sessions.move.title")}
      >
        <Text
          style={[
            theme.type.titleSmall,
            {
              color: theme.colors.fg,
              paddingHorizontal: 16,
              paddingVertical: 10,
            },
          ]}
        >
          {t("sessions.move.title")}
        </Text>
        {workspaces.map((ws) => (
          <ListRow
            key={ws.workspace_id}
            title={ws.name}
            left={
              <FolderOpen size={15} color={theme.colors["accent-strong"]} />
            }
            onPress={() => void doMove(ws.workspace_id)}
          />
        ))}
        <ListRow
          title={t("sessions.new.workspace")}
          left={<CirclePlus size={15} color={theme.colors.accent} />}
          onPress={() => setCreateWsOpen(true)}
        />
      </Sheet>

      {/* 新工作区 */}
      <Dialog open={createWsOpen} onClose={() => setCreateWsOpen(false)}>
        <Text
          style={[
            theme.type.titleSmall,
            { color: theme.colors.fg, marginBottom: 12 },
          ]}
        >
          {t("sessions.workspace.create")}
        </Text>
        <TextField
          value={createWsName}
          onChangeText={setCreateWsName}
          placeholder={t("sessions.workspace.name")}
          autoFocus
        />
        <View style={styles.dialogActions}>
          <Button
            title={t("common.cancel")}
            variant="ghost"
            size="sm"
            onPress={() => setCreateWsOpen(false)}
          />
          <Button
            title={t("common.confirm")}
            size="sm"
            loading={actionBusy}
            disabled={!createWsName.trim()}
            onPress={() => void doCreateWorkspace()}
          />
        </View>
      </Dialog>
    </>
  );
}

const styles = StyleSheet.create({
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  newChatBtn: {
    flexDirection: "row",
    alignItems: "center",
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  loadingWrap: { paddingVertical: 40, alignItems: "center" },
  sectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 16,
    paddingTop: 14,
    paddingBottom: 4,
  },
  sessionRowWrap: { marginHorizontal: 8 },
  rowActions: { flexDirection: "row", alignItems: "center", gap: 2 },
  dialogActions: {
    flexDirection: "row",
    justifyContent: "flex-end",
    gap: 8,
    marginTop: 16,
  },
  forgetRow: {
    flexDirection: "row",
    alignItems: "center",
    marginTop: 14,
    minHeight: 36,
  },
  checkbox: {
    width: 18,
    height: 18,
    borderRadius: 4,
    borderWidth: 1.5,
    alignItems: "center",
    justifyContent: "center",
  },
});
