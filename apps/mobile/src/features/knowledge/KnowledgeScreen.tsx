import React, { useEffect, useMemo, useState } from "react";
import { ScrollView, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import Svg, { G, Path, Rect, Text as SvgText } from "react-native-svg";
import { layoutDag, edgePath, splitLabel } from "@next-tutor/domain";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { useAction, useServerQuery, str, record } from "@/lib/server-state";
import { useWorkspace } from "@/providers/WorkspaceProvider";
import { domainRoute } from "@/shell/routes";
import { useAdaptive } from "@/shell/adaptive/window-class";
import { AdaptivePane } from "@/shell/adaptive/AdaptivePane";
import {
  Button,
  Card,
  Chip,
  TextField,
  ListRow,
  SegmentedControl,
  Sheet,
  EmptyState,
  ErrorState,
  useTheme,
} from "@/ui";
import {
  Body,
  Hint,
  Label,
  Pager,
  QueryState,
  Section,
  SheetBody,
  SheetHeader,
} from "@/ui/Elements";
import { FeatureShell } from "@/ui/FeatureShell";
import { ZoomViewport } from "@/ui/ZoomViewport";
import type { KnowledgeNode } from "@next-tutor/api-client";
export function KnowledgeScreen() {
  const c = useCopy();
  const { theme } = useTheme();
  const router = useRouter();
  const scope = useWorkspace();
  const action = useAction();
  const adaptive = useAdaptive();
  const params = useLocalSearchParams<{ conceptId?: string }>();
  const [q, setQ] = useState("");
  const [level, setLevel] = useState("");
  const [subject, setSubject] = useState("");
  const [view, setView] = useState("graph");
  const [selected, setSelected] = useState<string | null>(
    params.conceptId ?? null,
  );
  const [page, setPage] = useState(1);
  const [chapter, setChapter] = useState<{ id: string; name: string } | null>(
    null,
  );
  const [filterOpen, setFilterOpen] = useState(false);
  useEffect(() => {
    setChapter(null);
    setSelected(null);
    setPage(1);
  }, [scope.id, scope.textbookIds.join("|"), level, subject]);
  useEffect(() => {
    setSelected(params.conceptId ?? null);
  }, [params.conceptId]);
  const selectNode = (node: KnowledgeNode) => {
    if (node.kind === "chapter") {
      setChapter({ id: node.id, name: node.name });
      setQ("");
      setSelected(null);
      setPage(1);
    } else setSelected(node.id);
  };
  const catalog = useServerQuery(["knowledge-catalog"], (s) =>
    apiClient().knowledge.catalog(s),
  );
  const graph = useServerQuery(
    ["graph", scope.id, scope.textbookIds, q, level, subject, chapter?.id],
    (s) =>
      apiClient().knowledge.graph(
        {
          view: q.trim() ? "search" : chapter ? "chapter" : "overview",
          ...(chapter && !q.trim() ? { chapterId: chapter.id } : {}),
          q,
          level,
          subject,
          ...(scope.id ? { workspaceId: scope.id } : {}),
          ...(scope.textbookIds[0] ? { textbookId: scope.textbookIds[0] } : {}),
        },
        s,
      ),
  );
  const concept = useServerQuery(
    ["concept", selected, scope.id],
    (s) =>
      apiClient().knowledge.concept(
        selected!,
        scope.id ? { workspaceId: scope.id } : {},
        s,
      ),
    { enabled: !!selected },
  );
  const geometry = useMemo(
    () =>
      layoutDag(
        graph.data?.nodes ?? [],
        (graph.data?.edges ?? []).map((e) => ({
          ...e,
          type: e.type ?? "related",
        })),
        48,
      ),
    [graph.data],
  );
  const details = (
    <Body>
      <QueryState
        query={concept}
        empty={concept.data?.status !== "ok" || !concept.data?.concept}
      >
        <Section title={concept.data?.concept?.name ?? c("知识点", "Concept")}>
          <Hint>{concept.data?.concept?.description}</Hint>
          <Hint>{str(record(concept.data?.evaluation).statement)}</Hint>
        </Section>
        {concept.data?.edges
          ? Object.entries(concept.data.edges)
              .filter(([, links]) => links?.length)
              .map(([key, links]) => (
                <Section
                  key={key}
                  title={c(
                    (
                      {
                        prerequisites: "前置知识",
                        unlocks: "接下来",
                        related: "相关知识",
                        parents: "所属",
                        children: "组成",
                        applications: "应用",
                        misconceptions: "易混点",
                      } as Record<string, string>
                    )[key] ?? key,
                    key,
                  )}
                >
                  {links.map((n) => (
                    <ListRow
                      key={n.id}
                      title={n.name}
                      onPress={() =>
                        selectNode(
                          graph.data?.nodes.find((item) => item.id === n.id) ??
                            n,
                        )
                      }
                    />
                  ))}
                </Section>
              ))
          : null}
        <Button
          title={c("请老师讲讲", "Talk it through")}
          onPress={() =>
            router.push(
              domainRoute({
                kind: "chat",
                text: concept.data?.concept?.name ?? "",
                ...(scope.id ? { workspaceId: scope.id } : {}),
              }),
            )
          }
        />
        <Button
          title={c("记入笔记", "Make a note")}
          variant="outline"
          loading={action.pending}
          onPress={() =>
            void action.run(
              () =>
                apiClient().notes.createNote({
                  title: concept.data?.concept?.name ?? "",
                  content: concept.data?.concept?.description ?? "",
                }),
              (r) => router.push(domainRoute({ kind: "note", id: r.note.id })),
            )
          }
        />
      </QueryState>
    </Body>
  );
  const filters = (
    <Body>
      <Section title={c("筛选知识", "Filter knowledge")}>
        <Chip
          label={c("全部学段", "All levels")}
          active={!level}
          onPress={() => setLevel("")}
        />
        {catalog.data?.stages?.map((stage) => (
          <Chip
            key={stage.level}
            label={stage.level}
            active={level === stage.level}
            onPress={() => setLevel(stage.level)}
          />
        ))}
        <Chip
          label={c("全部学科", "All subjects")}
          active={!subject}
          onPress={() => setSubject("")}
        />
        {catalog.data?.stages
          ?.filter((s) => !level || s.level === level)
          .flatMap((s) => s.subjects)
          .filter((s, i, a) => a.indexOf(s) === i)
          .map((s) => (
            <Chip
              key={s}
              label={s}
              active={subject === s}
              onPress={() => setSubject(s)}
            />
          ))}
      </Section>
    </Body>
  );
  return (
    <FeatureShell
      title={c("知识之间的连接", "Connections in knowledge")}
      scroll={false}
    >
      <AdaptivePane
        master={
          adaptive.maxPanes === 3 ? (
            <ScrollView keyboardShouldPersistTaps="handled">
              <QueryState query={catalog}>{filters}</QueryState>
            </ScrollView>
          ) : undefined
        }
        inspector={
          selected ? (
            <ScrollView keyboardShouldPersistTaps="handled">
              {details}
            </ScrollView>
          ) : undefined
        }
      >
        <ScrollView
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={{ flexGrow: 1 }}
        >
          <Body>
            {chapter ? (
              <Section
                title={chapter.name}
                action={c("章节总览", "All chapters")}
                onAction={() => {
                  setChapter(null);
                  setSelected(null);
                  setQ("");
                  setPage(1);
                }}
              >
                <Hint>
                  {c(
                    "沿着章节中的连接探索，也可以点击知识点查看详情。",
                    "Explore this chapter and select a concept for details.",
                  )}
                </Hint>
              </Section>
            ) : null}
            <TextField
              value={q}
              onChangeText={(v) => {
                setQ(v);
                setPage(1);
              }}
              accessibilityLabel={c("搜索知识点", "Search concepts")}
              placeholder={c("搜索一个概念", "Find a concept")}
            />
            <SegmentedControl
              items={[
                { key: "graph", label: c("图谱", "Graph") },
                { key: "list", label: c("知识列表", "Concept list") },
              ]}
              active={view}
              onChange={setView}
            />
            {adaptive.maxPanes < 3 ? (
              <View
                style={{
                  flexDirection: "row",
                  gap: 8,
                  flexWrap: "wrap",
                  alignItems: "center",
                }}
              >
                <Button
                  title={c("学段与学科", "Level and subject")}
                  variant="outline"
                  onPress={() => setFilterOpen(true)}
                />
                <Hint>
                  {[level, subject].filter(Boolean).join(" · ") ||
                    c("全部知识", "All knowledge")}
                </Hint>
              </View>
            ) : null}
            {graph.data?.status === "disabled" ? (
              <EmptyState
                title={c("图谱服务暂未启用", "The graph service is disabled")}
                hint={c(
                  "仍可以继续辅导、阅读资料和整理笔记。",
                  "You can continue tutoring, reading materials, and taking notes.",
                )}
              />
            ) : graph.data?.status === "error" ? (
              <ErrorState
                title={c(
                  "暂时无法读取知识图谱",
                  "The knowledge graph could not be loaded",
                )}
                retryLabel={c("重试", "Retry")}
                onRetry={() => void graph.refetch()}
              />
            ) : (
              <QueryState query={graph} empty={!graph.data?.nodes.length}>
                {view === "graph" ? (
                  <Card style={{ padding: 0 }}>
                    <ZoomViewport>
                      <Svg
                        width="100%"
                        height="100%"
                        viewBox={`0 0 ${Math.max(1, geometry.w)} ${Math.max(1, geometry.h)}`}
                        accessible={false}
                      >
                        {graph.data?.edges.map((e, i) => {
                          const a = geometry.byId.get(e.from),
                            b = geometry.byId.get(e.to);
                          return a && b ? (
                            <Path
                              key={i}
                              d={edgePath(a.cx, a.cy + 24, b.cx, b.cy - 24)}
                              stroke={theme.colors.border}
                              strokeWidth={2}
                              fill="none"
                            />
                          ) : null;
                        })}
                        {geometry.items.map(({ n, cx, cy, w }) => (
                          <G key={n.id} onPress={() => selectNode(n)}>
                            <Rect
                              x={cx - w / 2}
                              y={cy - 24}
                              width={w}
                              height={48}
                              rx={12}
                              fill={
                                selected === n.id
                                  ? theme.colors["accent-soft"]
                                  : theme.colors.surface
                              }
                              stroke={theme.colors.accent}
                              strokeWidth={selected === n.id ? 2 : 1}
                            />
                            {splitLabel(n.name, w - 20).map((t, i, all) => (
                              <SvgText
                                key={i}
                                x={cx}
                                y={cy + (all.length === 1 ? 5 : i * 15 - 4)}
                                fill={theme.colors.fg}
                                fontSize={12}
                                textAnchor="middle"
                              >
                                {t}
                              </SvgText>
                            ))}
                          </G>
                        ))}
                      </Svg>
                    </ZoomViewport>
                  </Card>
                ) : (
                  <Card>
                    {graph.data?.nodes
                      .slice((page - 1) * 15, page * 15)
                      .map((n) => (
                        <ListRow
                          key={n.id}
                          title={n.name}
                          subtitle={n.description}
                          onPress={() => selectNode(n)}
                        />
                      ))}
                    <Pager
                      page={page}
                      total={graph.data?.nodes.length ?? 0}
                      pageSize={15}
                      onChange={setPage}
                    />
                  </Card>
                )}
              </QueryState>
            )}
            <Hint>
              {c(
                "可使用双指缩放，也可以切换知识列表逐项阅读。",
                "Pinch to zoom, or use the concept list to read each item.",
              )}
            </Hint>
          </Body>
        </ScrollView>
      </AdaptivePane>
      <Sheet
        open={filterOpen}
        onClose={() => setFilterOpen(false)}
        label={c("筛选知识", "Filter knowledge")}
      >
        <SheetHeader
          title={c("筛选知识", "Filter knowledge")}
          onClose={() => setFilterOpen(false)}
        />
        <SheetBody>
          <QueryState query={catalog}>{filters}</QueryState>
          <Button
            title={c("查看知识", "Explore knowledge")}
            onPress={() => setFilterOpen(false)}
          />
        </SheetBody>
      </Sheet>
      <Sheet
        open={!!selected && adaptive.maxPanes === 1}
        onClose={() => setSelected(null)}
        label={c("知识点详情", "Concept details")}
      >
        <SheetHeader
          title={c("知识点详情", "Concept details")}
          onClose={() => setSelected(null)}
        />
        <SheetBody>{details}</SheetBody>
      </Sheet>
    </FeatureShell>
  );
}
