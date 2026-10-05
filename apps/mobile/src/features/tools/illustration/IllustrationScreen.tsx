import { randomUUID } from "expo-crypto";
import React, { useEffect, useRef, useState } from "react";
import { ScrollView, View } from "react-native";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/providers/AuthProvider";
import { useAdaptive } from "@/shell/adaptive/window-class";
import {
  ApiError,
  NetworkError,
  type SubmitTurnPayload,
} from "@next-tutor/api-client";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { useServerQuery, useAction } from "@/lib/server-state";
import { confirm, errorMessage } from "@/lib/feedback";
import { shareBytes } from "@/platform/files";
import {
  Button,
  Card,
  Chip,
  Field,
  TextArea,
  SegmentedControl,
  Sheet,
  ListRow,
  useToast,
} from "@/ui";
import {
  Body,
  Hint,
  Label,
  QueryState,
  Section,
  SheetBody,
  SheetHeader,
} from "@/ui/Elements";
import { FeatureShell, CapabilityGate } from "@/ui/FeatureShell";
import { SvgCanvas } from "@/ui/SvgCanvas";
import { AdaptivePane } from "@/shell/adaptive/AdaptivePane";
import {
  MaterialPicker,
  type MaterialChoice,
} from "@/features/diagrams/MaterialPicker";
type PendingTurn = { sessionId: string; payload: SubmitTurnPayload };
export function IllustrationScreen() {
  const c = useCopy();
  const cache = useQueryClient();
  const { owner } = useAuth();
  const adaptive = useAdaptive();
  const toast = useToast();
  const action = useAction();
  const [id, setId] = useState<string | null>(null);
  const [mode, setMode] = useState("v2");
  const [message, setMessage] = useState("");
  const [materials, setMaterials] = useState<MaterialChoice[]>([]);
  const [picker, setPicker] = useState(false);
  const [history, setHistory] = useState(false);
  const [revision, setRevision] = useState<number | null>(null);
  const [source, setSource] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const retry = useRef<PendingTurn | null>(null);
  const request = useRef<AbortController | null>(null);
  const list = useServerQuery(["illustration-sessions"], (s) =>
    apiClient().tools.illustration.listSessions(s),
  );
  const session = useServerQuery(
    ["illustration-session", id],
    (s) => apiClient().tools.illustration.getSession(id!, s),
    { enabled: !!id },
  );
  const job = useServerQuery(
    ["illustration-job", session.data?.active_job_id],
    (s) =>
      apiClient().tools.illustration.getJob(session.data!.active_job_id!, s),
    { enabled: !!session.data?.active_job_id, poll: 1500 },
  );
  const seen = useRef<string | null>(null);
  useEffect(() => {
    if (
      job.data &&
      (job.data.status === "ready" || job.data.status === "failed")
    ) {
      const key = job.data.job_id + job.data.status;
      if (seen.current !== key) {
        seen.current = key;
        void session.refetch();
        void list.refetch();
      }
    }
  }, [job.data, session.refetch, list.refetch]);
  useEffect(
    () => () => {
      request.current?.abort();
    },
    [],
  );
  const active =
    !!session.data?.active_job_id &&
    job.data?.status !== "ready" &&
    job.data?.status !== "failed";
  const image =
    session.data?.revisions.find((r) => r.revision === revision) ||
    session.data?.revisions.at(-1);
  const switchSession = (value: string | null) => {
    request.current?.abort();
    setBusy(false);
    setId(value);
    setRevision(null);
    setSource(null);
    setError("");
    setMessage("");
    retry.current = null;
    setHistory(false);
    seen.current = null;
  };
  async function send() {
    if (busy || active) return;
    setBusy(true);
    setError("");
    const controller = new AbortController();
    request.current = controller;
    try {
      let turn = retry.current;
      if (!turn) {
        let current = session.data;
        if (!id) {
          current = await apiClient().tools.illustration.createSession(
            "",
            controller.signal,
          );
          if (controller.signal.aborted) return;
          setId(current.session_id);
        }
        if (!current) throw new Error("session_required");
        turn = {
          sessionId: current.session_id,
          payload: {
            message: message.trim(),
            mode: mode as "v1" | "v2" | "v3",
            selected_materials:
              mode === "v1"
                ? []
                : materials.map(({ asset_id, version }) => ({
                    asset_id,
                    version,
                  })),
            base_revision: current.revision,
            ...(source !== null ? { source_revision: source } : {}),
            request_id: randomUUID(),
          },
        };
        retry.current = turn;
      }
      await apiClient().tools.illustration.submitTurnWithRecovery(
        turn.sessionId,
        turn.payload,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      retry.current = null;
      setMessage("");
      setSource(null);
      setRevision(null);
      const snapshot = await apiClient().tools.illustration.getSession(
        turn.sessionId,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      cache.setQueryData(
        [owner, "illustration-session", turn.sessionId],
        snapshot,
      );
      await list.refetch();
    } catch (e) {
      if (controller.signal.aborted) return;
      setError(errorMessage(e, c));
      if (e instanceof ApiError && e.status === 409) {
        retry.current = null;
        if (retry.current) await session.refetch();
        else
          void cache.invalidateQueries({
            queryKey: [owner, "illustration-session"],
          });
        setError(
          c(
            "会话状态已变化。已重新载入，请确认后再次发送。",
            "The session changed. It has been reloaded; review it before sending again.",
          ),
        );
      }
    } finally {
      if (request.current === controller) {
        request.current = null;
        setBusy(false);
      }
    }
  }
  const sessions = (
    <Body>
      <Section
        title={c("创作会话", "Sessions")}
        action={c("新建", "New")}
        onAction={() => switchSession(null)}
      >
        <QueryState query={list} empty={!list.data?.items.length}>
          {list.data?.items.map((s) => (
            <ListRow
              key={s.session_id}
              title={s.title || c("未命名创作", "Untitled illustration")}
              subtitle={`V${s.revision}`}
              onPress={() => switchSession(s.session_id)}
            />
          ))}
        </QueryState>
      </Section>
    </Body>
  );
  const canvas = (
    <Body>
      <Section title={c("当前图示", "Your illustration")}>
        {image?.illustration?.svg ? (
          <>
            <SvgCanvas
              svg={image.illustration.svg}
              alt={image.illustration.alt}
              height={360}
            />
            <Hint>
              {image.mode.toUpperCase()} · V{image.revision}
            </Hint>
            <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
              <Button
                title={c("以此版本继续修改", "Continue from this version")}
                variant="outline"
                disabled={active || busy}
                onPress={() => setSource(image.revision)}
              />
              <Button
                title={c("导出 SVG", "Export SVG")}
                variant="ghost"
                loading={action.pending}
                onPress={() =>
                  void action.run(() =>
                    shareBytes(
                      image.illustration!.svg,
                      `illustration-v${image.revision}.svg`,
                      "image/svg+xml",
                    ),
                  )
                }
              />
            </View>
          </>
        ) : (
          <Card style={{ gap: 12, padding: 24 }}>
            <Label>
              {c("想法，从一句描述开始", "An idea starts with a description")}
            </Label>
            <Hint>
              {c(
                "例如：画一幅解释光合作用的流程图。结果准备好后会显示在这里。",
                "Try: illustrate how photosynthesis works. Your result will appear here.",
              )}
            </Hint>
          </Card>
        )}
      </Section>
      {session.data?.revisions.length ? (
        <Section title={c("版本", "Revisions")}>
          <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
            {session.data.revisions.map((r) => (
              <Chip
                key={r.revision}
                label={`V${r.revision} · ${r.mode.toUpperCase()}`}
                active={image?.revision === r.revision}
                onPress={() => setRevision(r.revision)}
              />
            ))}
          </View>
        </Section>
      ) : null}
    </Body>
  );
  return (
    <FeatureShell
      scroll={false}
      title={c("情景配图", "Scenario illustration")}
      right={
        <Button
          title={c("会话", "Sessions")}
          variant="ghost"
          onPress={() => setHistory(true)}
        />
      }
    >
      <CapabilityGate name="illustration.scenario">
        <AdaptivePane
          master={<ScrollView>{sessions}</ScrollView>}
          inspector={<ScrollView>{canvas}</ScrollView>}
          masterWidth={240}
          inspectorWidth={360}
        >
          <ScrollView keyboardShouldPersistTaps="handled">
            <Body>
              <SegmentedControl
                items={["v1", "v2", "v3"].map((key) => ({
                  key,
                  label: key.toUpperCase(),
                }))}
                active={mode}
                onChange={(value) => {
                  if (!busy && !active && !retry.current) setMode(value);
                }}
              />
              {adaptive.maxPanes < 3 ? canvas : null}
              {active ? (
                <Card style={{ gap: 8 }}>
                  <Label>{c("正在创作", "Creating")}</Label>
                  <Hint>
                    {
                      {
                        preparing: c("准备需求", "Preparing"),
                        retrieving: c("检索素材", "Finding materials"),
                        composing: c("组织画面", "Composing"),
                        rendering: c("渲染图示", "Rendering"),
                        reviewing: c("检查图示", "Reviewing"),
                        ready: c("完成", "Ready"),
                        failed: c("失败", "Failed"),
                      }[job.data?.stage ?? "preparing"]
                    }
                  </Hint>
                  <Hint>
                    {c(
                      "离开页面不会取消服务端任务，返回后会同步状态。",
                      "Leaving this page keeps the server job running. Its state will sync when you return.",
                    )}
                  </Hint>
                </Card>
              ) : null}
              {job.data?.status === "failed" ? (
                <Card style={{ gap: 12 }}>
                  <Hint>
                    {c(
                      "本次生成未完成，保留上次成功图示。",
                      "This generation did not complete. Your last successful illustration is kept.",
                    )}
                  </Hint>
                  {job.data.failure?.retryable ? (
                    <Button
                      title={c("重试本次生成", "Retry generation")}
                      loading={action.pending}
                      onPress={() =>
                        void action.run(
                          () =>
                            apiClient().tools.illustration.retryJob(
                              job.data!.job_id,
                            ),
                          () => void session.refetch(),
                        )
                      }
                    />
                  ) : null}
                </Card>
              ) : null}
              <Section title={c("创作对话", "Conversation")}>
                {session.data?.turns.map((t) => (
                  <Card key={t.turn_id} style={{ gap: 8 }}>
                    <Label>{t.message}</Label>
                    <Hint>
                      {t.mode.toUpperCase()} · {t.status}
                    </Hint>
                  </Card>
                ))}
              </Section>
              {source !== null ? (
                <Card style={{ gap: 8 }}>
                  <Hint>
                    {c(
                      `基于历史 V${source} 修改；提交仍以当前会话版本校验。`,
                      `Continue from historical V${source}; the current session revision is used for conflict checking.`,
                    )}
                  </Hint>
                  <Button
                    title={c("改为最新版本", "Use latest version")}
                    variant="ghost"
                    onPress={() => setSource(null)}
                  />
                </Card>
              ) : null}
              {mode !== "v1" ? (
                <Section title={c("素材", "Materials")}>
                  <Hint>
                    {materials.length
                      ? materials
                          .map((m) => `${m.title} · V${m.version}`)
                          .join("、")
                      : c(
                          "自动检索素材",
                          "Materials are retrieved automatically",
                        )}
                  </Hint>
                  <Button
                    title={c("选择素材", "Choose materials")}
                    variant="outline"
                    disabled={active || busy}
                    onPress={() => setPicker(true)}
                  />
                </Section>
              ) : null}
              <Field label={c("描述你的想法", "Describe your idea")}>
                <TextArea
                  value={message}
                  onChangeText={setMessage}
                  editable={!busy && !active && !retry.current}
                  accessibilityLabel={c("配图描述", "Illustration description")}
                  placeholder={c(
                    "你想画什么？或希望如何修改？",
                    "What would you like to draw or change?",
                  )}
                />
              </Field>
              {error ? <Hint>{error}</Hint> : null}
              <Button
                testID="illustration-send"
                title={
                  retry.current
                    ? c("恢复 / 重试发送", "Recover / Retry sending")
                    : c("生成图示", "Create illustration")
                }
                disabled={active || (!message.trim() && !retry.current)}
                loading={busy}
                onPress={() => void send()}
              />
              {id ? (
                <Button
                  title={c("删除会话", "Delete session")}
                  variant="ghost"
                  disabled={busy}
                  onPress={() =>
                    void confirm(
                      c("删除此创作会话？", "Delete this session?"),
                      c(
                        "会话与所有版本将被移除。",
                        "The session and all revisions will be removed.",
                      ),
                      c("删除", "Delete"),
                    ).then((ok) => {
                      if (ok)
                        void action.run(
                          () =>
                            apiClient().tools.illustration.deleteSession(id),
                          () => {
                            switchSession(null);
                          },
                        );
                    })
                  }
                />
              ) : null}
            </Body>
          </ScrollView>
        </AdaptivePane>
        <Sheet
          open={history}
          onClose={() => setHistory(false)}
          label={c("创作会话", "Sessions")}
        >
          <SheetHeader
            title={c("创作会话", "Sessions")}
            onClose={() => setHistory(false)}
          />
          <SheetBody>{sessions}</SheetBody>
        </Sheet>
        {picker ? (
          <MaterialPicker
            open
            selected={materials}
            onApply={setMaterials}
            onClose={() => setPicker(false)}
          />
        ) : null}
      </CapabilityGate>
    </FeatureShell>
  );
}
