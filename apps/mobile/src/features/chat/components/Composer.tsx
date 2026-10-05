import { useAuth } from "@/providers/AuthProvider";
import { useServerQuery } from "@/lib/server-state";
import { deletePickedFiles } from "@/platform/files";
import { VoiceInput } from "@/features/voice/VoiceInput";
import { useChatUi } from "@/stores/chat-ui";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import * as DocumentPicker from "expo-document-picker";
import * as ImagePicker from "expo-image-picker";
import * as Haptics from "expo-haptics";
import {
  ArrowUp,
  FileText,
  GraduationCap,
  Paperclip,
  ScanLine,
  Square,
  X,
} from "lucide-react-native";

import { useTheme } from "@/ui/ThemeProvider";
import { alpha } from "@/ui/theme";
import { Sheet } from "@/ui/Sheet";
import { ListRow } from "@/ui/ListRow";
import { useI18n } from "@/providers/I18nProvider";
import { apiClient } from "@/lib/api";
import { GRADE_LABEL_KEYS, GRADES, gradeForApi, type Grade } from "@/lib/grade";
import { useUiPrefs } from "@/stores/ui";
import { useChatStore } from "../store/chatStore";
import type { AttachmentMeta } from "../model/types";

/** RN 侧待上传附件（DocumentPicker/ImagePicker asset 的窄化）。 */
export interface PendingFile {
  uri: string;
  name: string;
  mimeType?: string | undefined;
}

const DOC_EXTENSIONS = /\.(pdf|docx|pptx|txt|md|markdown)$/i;
const IMAGE_EXTENSIONS = /\.(png|jpe?g|webp|bmp|tiff)$/i;

function uploadFailures(
  results: { filename: string; error?: string | null | undefined }[],
): string | null {
  const failed = results.filter((r) => r.error);
  return failed.length > 0
    ? failed.map((r) => `${r.filename}: ${r.error}`).join("；")
    : null;
}

interface ComposerProps {
  onSend: (message: string, attachments?: AttachmentMeta[]) => void;
  disabled: boolean;
  onStop?: (() => void) | undefined;
  /** 深链预填（/tutor?q=...）：每个不同值应用一次。 */
  prefill?: string | null;
  /** 新会话的工作区绑定（上传建会话时随 query 传递）。 */
  workspaceId?: string | null;
  draft: {
    text: string;
    setText: (text: string) => void;
    pendingFileNames: string[];
    setPendingFileNames: (names: string[]) => void;
    commitSent: () => void;
  };
}

/** 悬浮卡片式输入框（对齐 Web ChatInput）：工具行 + 学段胶囊 + 附件 + 发送。 */
export function Composer({
  onSend,
  disabled,
  onStop,
  prefill,
  workspaceId,
  draft,
}: ComposerProps) {
  const { theme } = useTheme();
  const auth = useAuth();
  const capabilities = useServerQuery(
    ["capabilities"],
    (s) => apiClient().capabilities.get(s),
    { public: true },
  );
  const canUpload =
    auth.state.status === "signed-in" &&
    capabilities.data?.upload.available === true;
  const alive = useRef(true);
  const uploads = useRef<AbortController | null>(null);
  const localFiles = useRef(new Set<string>());
  const submitLock = useRef(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      uploads.current?.abort();
      deletePickedFiles([...localFiles.current]);
    };
  }, []);
  const quote = useChatUi((s) => s.quote);
  useEffect(() => {
    if (quote) {
      draft.setText(`> ${quote.replace(/\n/g, "\n> ")}\n\n`);
      useChatUi.getState().setQuote("");
    }
  }, [quote, draft.setText]);
  const { t } = useI18n();
  const { grade, setGrade } = useUiPrefs();
  const sessionId = useChatStore((s) => s.sessionId);

  const [pending, setPending] = useState<PendingFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [ocrLoading, setOcrLoading] = useState(false);
  const [ocrText, setOcrText] = useState("");
  const [imageAttachments, setImageAttachments] = useState<AttachmentMeta[]>(
    [],
  );
  const [gradeSheetOpen, setGradeSheetOpen] = useState(false);
  const inputRef = useRef<TextInput>(null);
  const prefillRef = useRef<string | null>(null);

  const { text, setText, pendingFileNames, setPendingFileNames, commitSent } =
    draft;

  // 深链预填：每个不同值应用一次。
  useEffect(() => {
    if (!prefill || prefillRef.current === prefill) return;
    prefillRef.current = prefill;
    setText(prefill);
    inputRef.current?.focus();
  }, [prefill, setText]);

  // 持久层只有文件名：提示哪些附件需重选。
  useEffect(() => {
    if (pendingFileNames.length > 0 && pending.length === 0) {
      setUploadError(
        t(
          "chat.draft.refiles",
          `上次未发送的附件需重新选择：${pendingFileNames.join("、")}`,
        ),
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pickDocuments = useCallback(async () => {
    setUploadError(null);
    const result = await DocumentPicker.getDocumentAsync({
      type: "*/*",
      multiple: true,
      copyToCacheDirectory: true,
    });
    if (result.canceled) return;
    result.assets.forEach((a) => localFiles.current.add(a.uri));
    const accepted = result.assets
      .filter((a) => DOC_EXTENSIONS.test(a.name))
      .map((a) => ({ uri: a.uri, name: a.name, mimeType: a.mimeType }));
    if (accepted.length === 0) {
      setUploadError(t("chat.input.unsupported"));
      return;
    }
    setPending((prev) => [...prev, ...accepted]);
    setPendingFileNames([]);
  }, [t, setPendingFileNames]);

  const pickImage = useCallback(async () => {
    setUploadError(null);
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images"],
      quality: 0.85,
    });
    if (result.canceled) return;
    const asset = result.assets[0];
    if (!asset) return;
    localFiles.current.add(asset.uri);
    const name = asset.fileName ?? `image-${Date.now()}.jpg`;
    if (
      asset.mimeType &&
      !/^image\/(png|jpe?g|webp|bmp|tiff)$/i.test(asset.mimeType) &&
      !IMAGE_EXTENSIONS.test(name)
    ) {
      setUploadError(t("chat.input.unsupported"));
      return;
    }
    // 图片走同一上传通道：OCR 文本经 preview_text 回传（对齐 Web）。
    setOcrLoading(true);
    const controller = new AbortController();
    uploads.current = controller;
    const generation = useChatStore.getState().generation;
    try {
      const form = new FormData();
      form.append("files", {
        uri: asset.uri,
        name,
        type: asset.mimeType ?? "image/jpeg",
      } as unknown as Blob);
      const res = await apiClient().chat.upload<{
        results: AttachmentMeta[];
        session_id: string;
      }>(form, {
        signal: controller.signal,
        sessionId: sessionId ?? undefined,
        grade: gradeForApi(grade),
        workspaceId: sessionId ? undefined : workspaceId,
      });
      if (
        !alive.current ||
        controller.signal.aborted ||
        generation !== useChatStore.getState().generation
      )
        return;
      useChatStore.getState().setSessionId(res.session_id);
      const ok = res.results.filter((r) => !r.error);
      useChatStore.getState().addFiles(ok);
      setImageAttachments((prev) => [...prev, ...ok]);
      const preview = ok[0]?.preview_text?.trim() ?? "";
      if (preview) {
        setOcrText(preview);
        inputRef.current?.focus();
      } else {
        const failures = uploadFailures(res.results);
        if (failures) setUploadError(failures);
      }
    } catch (error) {
      setUploadError(
        `OCR: ${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      if (alive.current) setOcrLoading(false);
      deletePickedFiles([asset.uri]);
      localFiles.current.delete(asset.uri);
    }
  }, [grade, sessionId, workspaceId, t]);

  const handleSubmit = useCallback(async () => {
    if (disabled || submitLock.current) return;
    submitLock.current = true;
    const generation = useChatStore.getState().generation;
    const controller = new AbortController();
    uploads.current = controller;
    try {
      let uploadedOk = true;
      let uploadedAttachments: AttachmentMeta[] = [];
      if (pending.length > 0) {
        setUploading(true);
        try {
          const form = new FormData();
          for (const file of pending) {
            form.append("files", {
              uri: file.uri,
              name: file.name,
              type: file.mimeType ?? "application/octet-stream",
            } as unknown as Blob);
          }
          const res = await apiClient().chat.upload<{
            results: AttachmentMeta[];
            session_id: string;
          }>(form, {
            signal: controller.signal,
            sessionId: sessionId ?? undefined,
            grade: gradeForApi(grade),
            workspaceId: sessionId ? undefined : workspaceId,
          });
          uploadedAttachments = res.results.filter((r) => !r.error);
          if (
            !alive.current ||
            controller.signal.aborted ||
            generation !== useChatStore.getState().generation
          )
            return;
          useChatStore.getState().setSessionId(res.session_id);
          useChatStore.getState().addFiles(uploadedAttachments);
          const failures = uploadFailures(res.results);
          if (failures) {
            setUploadError(failures);
            uploadedOk = false;
            setImageAttachments((prev) => [...prev, ...uploadedAttachments]);
            const failed = new Set(
              res.results.filter((r) => r.error).map((r) => r.filename),
            );
            setPending((prev) => prev.filter((f) => failed.has(f.name)));
          }
        } catch (error) {
          setUploadError(
            error instanceof Error ? error.message : String(error),
          );
          uploadedOk = false;
        }
        setUploading(false);
      }
      const trimmed = text.trim();
      if (!uploadedOk) return;
      const ocrTrimmed = ocrText.trim();
      if (!trimmed && !ocrTrimmed) return;
      const finalMsg = ocrTrimmed
        ? `<ocr_material>${ocrTrimmed}</ocr_material>\n\n${trimmed || t("chat.input.image.prompt", "请帮我解答这道图片中的题目")}`
        : trimmed;
      const turnAttachments = [
        ...uploadedAttachments,
        ...imageAttachments,
      ].filter(
        (item, index, all) => all.findIndex((x) => x.id === item.id) === index,
      );
      void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(
        () => undefined,
      );
      onSend(
        finalMsg,
        turnAttachments.length > 0 ? turnAttachments : undefined,
      );
      setText("");
      setPending([]);
      setOcrText("");
      setImageAttachments([]);
      commitSent();
      deletePickedFiles(pending.map((f) => f.uri));
      pending.forEach((f) => localFiles.current.delete(f.uri));
    } finally {
      submitLock.current = false;
      if (alive.current) setUploading(false);
    }
  }, [
    disabled,
    pending,
    text,
    ocrText,
    imageAttachments,
    sessionId,
    grade,
    workspaceId,
    t,
    onSend,
    setText,
    commitSent,
  ]);

  const canSend =
    !disabled &&
    !uploading &&
    !ocrLoading &&
    (text.trim().length > 0 || ocrText.trim().length > 0);

  return (
    <View style={styles.root}>
      {uploadError ? (
        <Text
          style={[
            theme.type.caption,
            { color: theme.colors.danger, marginBottom: 6 },
          ]}
        >
          {uploadError}
        </Text>
      ) : null}
      <View
        style={[
          styles.card,
          {
            borderColor: theme.colors.border,
            backgroundColor: theme.colors.surface,
          },
          theme.shadow.sm,
        ]}
      >
        {/* OCR 预览卡：不污染输入框，随消息一并提交 */}
        {ocrLoading ? (
          <View
            style={[
              styles.ocrBox,
              {
                borderColor: alpha(theme.colors.accent, 0.25),
                backgroundColor: alpha(theme.colors["accent-soft"], 0.3),
              },
            ]}
          >
            <ActivityIndicator size={12} color={theme.colors.accent} />
            <Text
              style={[
                theme.type.caption,
                { color: theme.colors["fg-secondary"], marginLeft: 8 },
              ]}
            >
              {t("chat.input.ocr")}
            </Text>
          </View>
        ) : null}
        {ocrText && !ocrLoading ? (
          <View
            style={[
              styles.ocrBoxColumn,
              {
                borderColor: alpha(theme.colors.accent, 0.25),
                backgroundColor: alpha(theme.colors["accent-soft"], 0.3),
              },
            ]}
          >
            <View style={styles.ocrHead}>
              <ScanLine size={12} color={theme.colors.accent} />
              <Text
                style={[
                  theme.type.caption,
                  {
                    color: theme.colors["accent-strong"],
                    fontWeight: "500",
                    flex: 1,
                    marginLeft: 6,
                  },
                ]}
              >
                {t("chat.ocr.preview")}
              </Text>
              <Pressable
                onPress={() => setOcrText("")}
                accessibilityRole="button"
                accessibilityLabel={t("common.cancel")}
                hitSlop={8}
                style={{
                  minWidth: 48,
                  minHeight: 48,
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <X size={12} color={theme.colors.muted} />
              </Pressable>
            </View>
            <Text
              style={[
                theme.type.caption,
                {
                  color: theme.colors["fg-secondary"],
                  marginTop: 4,
                  lineHeight: 18,
                },
              ]}
              numberOfLines={4}
            >
              {ocrText}
            </Text>
          </View>
        ) : null}

        {/* 附件 chips */}
        {pending.length > 0 ? (
          <View style={styles.chips}>
            {pending.map((file, i) => (
              <View
                key={`${file.uri}-${i}`}
                style={[
                  styles.chip,
                  { backgroundColor: theme.colors["surface-hover"] },
                ]}
              >
                <FileText size={11} color={alpha(theme.colors.accent, 0.7)} />
                <Text
                  style={[
                    theme.type.caption,
                    { color: theme.colors["fg-secondary"], maxWidth: 160 },
                  ]}
                  numberOfLines={1}
                >
                  {file.name}
                </Text>
                <Pressable
                  onPress={() =>
                    setPending((prev) => prev.filter((_, j) => j !== i))
                  }
                  accessibilityRole="button"
                  accessibilityLabel={`${t("common.delete")} ${file.name}`}
                  hitSlop={6}
                  style={{
                    minWidth: 48,
                    minHeight: 48,
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <X size={11} color={theme.colors.muted} />
                </Pressable>
              </View>
            ))}
          </View>
        ) : null}

        <TextInput
          ref={inputRef}
          value={text}
          onChangeText={setText}
          editable={!disabled}
          placeholder={t("chat.input.placeholder")}
          placeholderTextColor={theme.colors.muted}
          multiline
          style={[
            theme.type.body,
            styles.input,
            { color: theme.colors.fg, maxHeight: 140 },
          ]}
          accessibilityLabel={t("chat.input.placeholder")}
        />

        {/* 工具行：学段胶囊 + 附件 + 识题 + 发送/停止 */}
        <View style={styles.toolbar}>
          <Pressable
            onPress={() => setGradeSheetOpen(true)}
            disabled={disabled}
            accessibilityRole="button"
            accessibilityLabel={`${t("chat.grade.label")}: ${t(GRADE_LABEL_KEYS[grade])}`}
            style={({ pressed }) => [
              styles.gradeCapsule,
              {
                backgroundColor: pressed
                  ? theme.colors["surface-hover"]
                  : alpha(theme.colors["surface-hover"], 0.7),
                opacity: disabled ? 0.5 : 1,
              },
            ]}
          >
            <GraduationCap size={13} color={theme.colors.muted} />
            <Text
              style={[
                theme.type.caption,
                { color: theme.colors["fg-secondary"], marginLeft: 5 },
              ]}
            >
              {t(GRADE_LABEL_KEYS[grade])}
            </Text>
          </Pressable>

          <Pressable
            onPress={() => void pickDocuments()}
            disabled={disabled || uploading || !canUpload}
            accessibilityRole="button"
            accessibilityLabel={t("chat.input.upload.title")}
            style={({ pressed }) => [
              styles.toolBtn,
              { opacity: disabled || uploading ? 0.4 : pressed ? 0.6 : 1 },
            ]}
          >
            {uploading ? (
              <ActivityIndicator size={15} color={theme.colors.muted} />
            ) : (
              <Paperclip size={15} color={theme.colors.muted} />
            )}
          </Pressable>
          <Pressable
            onPress={() => void pickImage()}
            disabled={disabled || ocrLoading || !canUpload}
            accessibilityRole="button"
            accessibilityLabel={t("chat.input.ocr")}
            style={({ pressed }) => [
              styles.toolBtn,
              { opacity: disabled || ocrLoading ? 0.4 : pressed ? 0.6 : 1 },
            ]}
          >
            {ocrLoading ? (
              <ActivityIndicator size={15} color={theme.colors.muted} />
            ) : (
              <ScanLine size={15} color={theme.colors.muted} />
            )}
          </Pressable>

          <VoiceInput
            disabled={disabled || uploading}
            onText={(voiceText) =>
              setText(text ? text + "\n" + voiceText : voiceText)
            }
          />

          <View style={{ marginLeft: "auto" }}>
            {disabled && onStop ? (
              <Pressable
                onPress={() => {
                  void Haptics.impactAsync(
                    Haptics.ImpactFeedbackStyle.Medium,
                  ).catch(() => undefined);
                  onStop();
                }}
                accessibilityRole="button"
                accessibilityLabel={t("chat.stop")}
                style={[
                  styles.sendBtn,
                  { backgroundColor: theme.colors.danger },
                  theme.shadow.sm,
                ]}
              >
                <Square
                  size={13}
                  color={theme.colors.onAccent}
                  fill={theme.colors.onAccent}
                />
              </Pressable>
            ) : (
              <Pressable
                onPress={() => void handleSubmit()}
                disabled={!canSend}
                accessibilityRole="button"
                accessibilityLabel={t("chat.send")}
                style={({ pressed }) => [
                  styles.sendBtn,
                  {
                    backgroundColor: theme.colors.accent,
                    opacity: !canSend ? 0.4 : 1,
                  },
                  theme.shadow.sm,
                  pressed && canSend
                    ? { backgroundColor: theme.colors["accent-strong"] }
                    : null,
                ]}
              >
                <ArrowUp size={16} color={theme.colors.onAccent} />
              </Pressable>
            )}
          </View>
        </View>
      </View>
      <Text
        style={[
          theme.type.caption,
          {
            color: alpha(theme.colors.muted, 0.7),
            textAlign: "center",
            marginTop: 6,
            fontSize: 10.5,
          },
        ]}
      >
        {t("chat.input.hint")}
      </Text>

      {/* 学段选择 Sheet（服务端中文契约值不翻译，标签走 i18n） */}
      <Sheet
        open={gradeSheetOpen}
        onClose={() => setGradeSheetOpen(false)}
        heightRatio={0.5}
        accessibilityLabel={t("chat.grade.label")}
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
          {t("chat.grade.label")}
        </Text>
        {GRADES.map((g) => (
          <ListRow
            key={g}
            title={t(GRADE_LABEL_KEYS[g as Grade])}
            right={
              g === grade ? (
                <View
                  style={[
                    styles.gradeDot,
                    { backgroundColor: theme.colors.accent },
                  ]}
                />
              ) : undefined
            }
            onPress={() => {
              setGrade(g);
              setGradeSheetOpen(false);
              // 会话内切换学段 → 立即 PATCH 持久化（「自动」转空串）；
              // 新会话只写 store，首轮发送随 stream 落库。
              if (sessionId) {
                void apiClient()
                  .chat.patchSession(sessionId, { grade: gradeForApi(g) })
                  .catch(() => undefined);
              }
            }}
          />
        ))}
      </Sheet>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { paddingHorizontal: 12, paddingTop: 4 },
  card: { borderWidth: 1, borderRadius: 14 },
  ocrBox: {
    flexDirection: "row",
    alignItems: "center",
    marginHorizontal: 12,
    marginTop: 12,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  ocrBoxColumn: {
    marginHorizontal: 12,
    marginTop: 12,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  ocrHead: { flexDirection: "row", alignItems: "center" },
  chips: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginHorizontal: 12,
    marginTop: 12,
  },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  input: { paddingHorizontal: 14, paddingTop: 12, paddingBottom: 4 },
  toolbar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 0,
    flexWrap: "wrap",
    paddingHorizontal: 8,
    paddingBottom: 8,
    paddingTop: 4,
  },
  gradeCapsule: {
    flexDirection: "row",
    alignItems: "center",
    minHeight: 48,
    borderRadius: 999,
    paddingHorizontal: 8,
  },
  toolBtn: {
    width: 48,
    height: 48,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 18,
  },
  sendBtn: {
    width: 48,
    height: 48,
    borderRadius: 10,
    alignItems: "center",
    justifyContent: "center",
  },
  gradeDot: { width: 8, height: 8, borderRadius: 4 },
});
