import React, { useState } from "react";
import { ScrollView, View } from "react-native";
import * as DocumentPicker from "expo-document-picker";
import { File } from "expo-file-system";
import type {
  DiagramMaterial,
  MaterialInput,
  MaterialParameterization,
} from "@next-tutor/api-client";
import { ApiError, STATIC_PARAMETERIZATION } from "@next-tutor/api-client";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { useAction, useServerQuery } from "@/lib/server-state";
import { confirm } from "@/lib/feedback";
import { shareBytes, deletePickedFiles } from "@/platform/files";
import {
  Button,
  Card,
  Field,
  TextArea,
  TextField,
  SegmentedControl,
  Sheet,
  ListRow,
} from "@/ui";
import {
  Body,
  Hint,
  Label,
  Pager,
  QueryState,
  SheetBody,
  SheetHeader,
} from "@/ui/Elements";
import { FeatureShell } from "@/ui/FeatureShell";
import { SvgCanvas } from "@/ui/SvgCanvas";
import { AdaptivePane } from "@/shell/adaptive/AdaptivePane";
import { useAdaptive } from "@/shell/adaptive/window-class";
import { useDirtyGuard } from "@/lib/useDirtyGuard";
export function DiagramsScreen() {
  const c = useCopy();
  const action = useAction();
  const adaptive = useAdaptive();
  const [scope, setScope] = useState("builtin");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState<{
    title: string;
    svg: string;
    material?: DiagramMaterial;
  } | null>(null);
  const [edit, setEdit] = useState(false);
  const [step, setStep] = useState("metadata");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [svg, setSvg] = useState("");
  const [params, setParams] = useState<MaterialParameterization>(
    STATIC_PARAMETERIZATION,
  );
  const [values, setValues] = useState<Record<string, string>>({});
  const [requirement, setRequirement] = useState("");
  const [baseMaterial, setBaseMaterial] = useState<DiagramMaterial | null>(
    null,
  );
  const [conflict, setConflict] = useState<DiagramMaterial | null>(null);
  const [previewSvg, setPreviewSvg] = useState("");
  const [source, setSource] = useState<"manual" | "upload" | "llm">("manual");
  const [baseline, setBaseline] = useState("");
  const draftSnapshot = JSON.stringify({
    title,
    description,
    svg,
    params,
    values,
  });
  const dirty = edit && baseline !== draftSnapshot;
  useDirtyGuard(dirty);
  const closeEditor = () => {
    if (action.pending) return;
    if (!dirty) setEdit(false);
    else
      void confirm(
        c("放弃未保存的修改？", "Discard unsaved changes?"),
        c(
          "图示草稿尚未保存到服务端。",
          "This diagram draft has not been saved.",
        ),
        c("放弃", "Discard"),
        c("继续编辑", "Keep editing"),
      ).then((ok) => {
        if (ok) setEdit(false);
      });
  };
  const parameterValues = () =>
    Object.fromEntries(
      Object.entries(values).map(([key, value]) => [
        key,
        ["number", "integer"].includes(params.parameters[key]?.type ?? "")
          ? Number(value)
          : value,
      ]),
    );
  const importSvg = async () => {
    const picked = await DocumentPicker.getDocumentAsync({
      type: ["image/svg+xml", "text/plain"],
      copyToCacheDirectory: true,
    });
    if (picked.canceled) return;
    const file = picked.assets[0]!;
    await action.run(
      async () => {
        try {
          if ((file.size ?? 0) > 2_000_000) throw new Error("svg_too_large");
          const content = await new File(file.uri).text();
          if (content.length > 2_000_000 || !/<svg[\s>]/i.test(content))
            throw new Error("invalid_svg");
          return content;
        } finally {
          deletePickedFiles([file.uri]);
        }
      },
      (content) => {
        setSvg(content);
        setSource("upload");
        setPreviewSvg("");
      },
    );
  };
  const query = useServerQuery(["diagrams", scope, q, page], async (s) => {
    if (scope === "builtin") {
      const r = await apiClient().diagrams.listAssets({ q, page: page - 1 }, s);
      return {
        ...r,
        items: r.items.map((a) => ({
          id: a.id,
          title: a.title,
          svg: a.illustration?.svg || "",
          material: undefined,
        })),
      };
    }
    const r = await apiClient().diagrams.listMaterials(
      scope as "private" | "public",
      q,
      page - 1,
      s,
    );
    return {
      ...r,
      per: r.per ?? 12,
      items: r.items.map((a) => ({
        id: a.id,
        title: a.title,
        svg: a.illustration.svg,
        material: a,
      })),
    };
  });
  const openEditor = (m?: DiagramMaterial) => {
    setTitle(m?.title ?? "");
    setDescription(m?.description ?? "");
    setSvg(m?.svg ?? "");
    setParams(m?.parameterization ?? STATIC_PARAMETERIZATION);
    setValues({});
    setRequirement("");
    setBaseMaterial(m?.scope === "private" ? m : null);
    setConflict(null);
    setPreviewSvg(m?.illustration.svg ?? "");
    setSource("manual");
    setBaseline(
      JSON.stringify({
        title: m?.title ?? "",
        description: m?.description ?? "",
        svg: m?.svg ?? "",
        params: m?.parameterization ?? STATIC_PARAMETERIZATION,
        values: {},
      }),
    );
    setStep("metadata");
    setEdit(true);
  };
  const save = () =>
    void action.run(
      async () => {
        const preview = await apiClient().diagrams.previewMaterial(
          svg,
          undefined,
          params,
          Object.fromEntries(
            Object.entries(values).map(([k, v]) => [
              k,
              ["number", "integer"].includes(params.parameters[k]?.type ?? "")
                ? Number(v)
                : v,
            ]),
          ),
        );
        const existing = baseMaterial;
        const input: MaterialInput = {
          title: title.trim(),
          description,
          subject: existing?.subject ?? "",
          aliases: existing?.aliases ?? [],
          svg: preview.svg,
          parameterization: preview.parameterization,
          scope: "private",
          source,
          enabled: true,
          ...(existing ? { base_revision: existing.revision } : {}),
        };
        try {
          return await apiClient().diagrams.saveMaterial(input, existing?.id);
        } catch (error) {
          if (error instanceof ApiError && error.status === 409 && existing) {
            try {
              setConflict(await apiClient().diagrams.getMaterial(existing.id));
            } catch {}
          }
          throw error;
        }
      },
      (m) => {
        setDetail({ title: m.title, svg: m.illustration.svg, material: m });
        setEdit(false);
        setConflict(null);
      },
    );
  const details = (
    <Body>
      {" "}
      {detail ? (
        <>
          <SvgCanvas svg={detail.svg} alt={detail.title} height={320} />
          <Hint>{detail.material?.description}</Hint>
          <Button
            title={
              detail.material?.scope === "private"
                ? c("编辑此素材", "Edit material")
                : c("保存个人副本", "Save a personal copy")
            }
            onPress={() => {
              if (detail.material) openEditor(detail.material);
              else {
                openEditor();
                setTitle(detail.title);
                setSvg(detail.svg);
              }
            }}
          />
          <Button
            title={c("导出 SVG", "Export SVG")}
            variant="outline"
            onPress={() =>
              void action.run(() =>
                shareBytes(detail.svg, detail.title + ".svg", "image/svg+xml"),
              )
            }
          />
          {detail.material?.scope === "private" ? (
            <>
              <Hint>V{detail.material.revision}</Hint>
              <Button
                title={c("删除素材", "Delete material")}
                variant="ghost"
                onPress={() =>
                  void confirm(
                    c("删除此素材？", "Delete this material?"),
                    c(
                      "引用此版本的后续任务可能不可用。",
                      "Future tasks referring to this version may become unavailable.",
                    ),
                    c("删除", "Delete"),
                  ).then((ok) => {
                    if (ok)
                      void action.run(
                        () =>
                          apiClient().diagrams.deleteMaterial(
                            detail.material!.id,
                            detail.material!.revision,
                          ),
                        () => setDetail(null),
                      );
                  })
                }
              />
            </>
          ) : null}
        </>
      ) : null}
    </Body>
  );
  const editorControls = (
    <View style={{ gap: 20 }}>
      {" "}
      <SegmentedControl
        items={[
          { key: "metadata", label: c("基本信息", "Details") },
          { key: "parameters", label: c("参数", "Parameters") },
          { key: "source", label: "SVG" },
        ]}
        active={step}
        onChange={setStep}
      />
      {step === "metadata" ? (
        <>
          <Field label={c("标题", "Title")}>
            <TextField
              value={title}
              onChangeText={setTitle}
              accessibilityLabel={c("标题", "Title")}
            />
          </Field>
          <Field label={c("用途说明", "Description")}>
            <TextArea
              value={description}
              onChangeText={setDescription}
              accessibilityLabel={c("用途说明", "Description")}
            />
          </Field>
          <Field label={c("用 AI 起草", "Draft with AI")}>
            <TextArea
              value={requirement}
              onChangeText={setRequirement}
              placeholder={c(
                "描述结构与表达重点",
                "Describe structure and meaning",
              )}
              accessibilityLabel={c("图示需求", "Diagram requirement")}
            />
          </Field>
          <Button
            title={c("生成草稿", "Generate draft")}
            variant="outline"
            disabled={!requirement.trim()}
            loading={action.pending}
            onPress={() =>
              void action.run(
                () =>
                  apiClient().diagrams.generateMaterial(
                    requirement,
                    svg,
                    undefined,
                    params,
                  ),
                (r) => {
                  setSvg(r.svg);
                  setPreviewSvg(r.illustration.svg);
                  setSource("llm");
                  setParams(r.parameterization ?? STATIC_PARAMETERIZATION);
                },
              )
            }
          />
        </>
      ) : step === "parameters" ? (
        <>
          {Object.entries(params.parameters).map(([key, p]) => (
            <Field key={key} label={p.description || key} hint={p.unit}>
              <TextField
                value={values[key] ?? String(p.default)}
                onChangeText={(v) => setValues((old) => ({ ...old, [key]: v }))}
                keyboardType={
                  p.type === "number" || p.type === "integer"
                    ? "numeric"
                    : "default"
                }
                accessibilityLabel={p.description || key}
              />
            </Field>
          ))}
          <Hint>
            {c(
              "参数的范围与绑定由服务端校验。",
              "Your server validates parameter ranges and bindings.",
            )}
          </Hint>
          <Button
            title={c("预览参数结果", "Preview parameters")}
            variant="outline"
            loading={action.pending}
            onPress={() =>
              void action.run(
                () =>
                  apiClient().diagrams.previewMaterial(
                    svg,
                    undefined,
                    params,
                    Object.fromEntries(
                      Object.entries(values).map(([k, v]) => [
                        k,
                        ["number", "integer"].includes(
                          params.parameters[k]?.type ?? "",
                        )
                          ? Number(v)
                          : v,
                      ]),
                    ),
                  ),
                (r) => setPreviewSvg(r.illustration.svg),
              )
            }
          />
          {adaptive.maxPanes === 1 && previewSvg ? (
            <SvgCanvas svg={previewSvg} height={220} alt={title} />
          ) : null}
        </>
      ) : (
        <>
          <Button
            title={c("导入 SVG 文件", "Import an SVG file")}
            variant="outline"
            loading={action.pending}
            onPress={() => void importSvg()}
          />
          <Field label={c("SVG 源码", "SVG source")}>
            <TextArea
              value={svg}
              onChangeText={setSvg}
              accessibilityLabel={c("SVG 源码", "SVG source")}
              style={{ minHeight: 260 }}
            />
          </Field>
        </>
      )}
      <Button
        title={c("保存素材", "Save material")}
        loading={action.pending}
        disabled={!title.trim() || !svg.trim() || !!conflict}
        onPress={save}
      />
    </View>
  );
  const conflictContent = conflict ? (
    <Card style={{ gap: 12 }}>
      <Label>
        {c("素材已在其他地方更新", "This material changed elsewhere")}
      </Label>
      <Hint>
        {c(
          "你的草稿仍保留。查看服务端版本后，选择继续保存草稿或使用服务端版本。",
          "Your draft is preserved. Review the server version before choosing how to continue.",
        )}
      </Hint>
      <Label>
        {conflict.title} · V{conflict.revision}
      </Label>
      <Hint>{conflict.description}</Hint>
      <SvgCanvas
        svg={conflict.illustration.svg}
        alt={conflict.title}
        height={200}
      />
      <Button
        title={c(
          "保留草稿，基于新版本继续",
          "Keep draft and use the new revision",
        )}
        variant="outline"
        onPress={() => {
          setBaseMaterial(conflict);
          setConflict(null);
        }}
      />
      <Button
        title={c("使用服务端版本", "Use server version")}
        variant="ghost"
        onPress={() => openEditor(conflict)}
      />
    </Card>
  ) : null;
  return (
    <FeatureShell title={c("图示库", "Diagram library")} scroll={false}>
      <AdaptivePane
        inspector={
          detail && !edit ? (
            <ScrollView keyboardShouldPersistTaps="handled">
              {details}
            </ScrollView>
          ) : undefined
        }
        inspectorWidth={360}
      >
        <ScrollView
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={{ flexGrow: 1 }}
        >
          <Body>
            <SegmentedControl
              items={[
                { key: "builtin", label: c("内置", "Built-in") },
                { key: "public", label: c("公共", "Public") },
                { key: "private", label: c("我的", "Mine") },
              ]}
              active={scope}
              onChange={(v) => {
                setScope(v);
                setPage(1);
              }}
            />
            <View style={{ flexDirection: "row", gap: 8 }}>
              <TextField
                value={q}
                onChangeText={(v) => {
                  setQ(v);
                  setPage(1);
                }}
                placeholder={c("搜索图示", "Search diagrams")}
                accessibilityLabel={c("搜索图示", "Search diagrams")}
                style={{ flex: 1 }}
              />
              <Button
                title={c("创作", "Create")}
                onPress={() => {
                  setDetail(null);
                  openEditor();
                }}
              />
            </View>
            <QueryState query={query} empty={!query.data?.items.length}>
              <View style={{ gap: 16 }}>
                {query.data?.items.map((item) => (
                  <Card key={item.id} style={{ gap: 12 }}>
                    <Label>{item.title}</Label>
                    {item.svg ? (
                      <SvgCanvas svg={item.svg} height={180} alt={item.title} />
                    ) : null}
                    <Button
                      title={c("查看与调整", "View and adjust")}
                      variant="outline"
                      onPress={() =>
                        void action.run(async () => {
                          if (item.material)
                            return {
                              title: item.title,
                              svg: item.svg,
                              material: item.material,
                            };
                          const r = await apiClient().diagrams.getAsset(
                            item.id,
                          );
                          return {
                            title: r.title,
                            svg: r.illustration?.svg ?? item.svg,
                          };
                        }, setDetail)
                      }
                    />
                  </Card>
                ))}
              </View>
              <Pager
                page={page}
                total={query.data?.total ?? 0}
                pageSize={query.data?.per ?? 12}
                onChange={setPage}
              />
            </QueryState>
          </Body>
        </ScrollView>
      </AdaptivePane>
      <Sheet
        open={!!detail && !edit && adaptive.maxPanes === 1}
        onClose={() => setDetail(null)}
        label={c("图示详情", "Diagram details")}
      >
        <SheetHeader
          title={detail?.title ?? ""}
          onClose={() => setDetail(null)}
        />
        <SheetBody>{details}</SheetBody>
      </Sheet>
      <Sheet
        open={edit}
        onClose={closeEditor}
        label={c("编辑图示素材", "Edit diagram material")}
        width={adaptive.maxPanes >= 2 ? 1160 : 580}
      >
        <SheetHeader
          title={c("编辑图示素材", "Edit diagram material")}
          onClose={closeEditor}
        />
        {adaptive.maxPanes === 1 ? (
          <SheetBody>
            {conflictContent}
            {editorControls}
            {previewSvg && step !== "parameters" ? (
              <SvgCanvas svg={previewSvg} alt={title} height={260} />
            ) : null}
          </SheetBody>
        ) : (
          <View
            style={{
              flexDirection: "row",
              height: Math.max(240, Math.min(640, adaptive.height * 0.9 - 180)),
            }}
          >
            <ScrollView
              keyboardShouldPersistTaps="handled"
              style={{ width: adaptive.maxPanes === 3 ? 320 : "44%" }}
              contentContainerStyle={{ padding: 20, gap: 20 }}
            >
              {conflictContent}
              {editorControls}
            </ScrollView>
            <ScrollView
              style={{ flex: 1, minWidth: 0 }}
              contentContainerStyle={{ padding: 20, gap: 16 }}
            >
              <Label>{c("图示预览", "Diagram preview")}</Label>
              {previewSvg ? (
                <SvgCanvas svg={previewSvg} alt={title} height={400} />
              ) : (
                <Hint>
                  {c(
                    "导入图示或生成草稿后，可在这里预览。",
                    "Import a diagram or generate a draft to preview it here.",
                  )}
                </Hint>
              )}
              <Button
                title={c("更新预览", "Update preview")}
                variant="outline"
                disabled={!svg.trim()}
                loading={action.pending}
                onPress={() =>
                  void action.run(
                    () =>
                      apiClient().diagrams.previewMaterial(
                        svg,
                        undefined,
                        params,
                        parameterValues(),
                      ),
                    (r) => setPreviewSvg(r.illustration.svg),
                  )
                }
              />
            </ScrollView>
            {adaptive.maxPanes === 3 ? (
              <ScrollView
                style={{ width: 240 }}
                contentContainerStyle={{ padding: 20, gap: 16 }}
              >
                <Label>{title || c("未命名图示", "Untitled diagram")}</Label>
                <Hint>
                  {description ||
                    c(
                      "添加用途说明，让图示更容易复用。",
                      "Describe how this diagram can be reused.",
                    )}
                </Hint>
                <Hint>
                  {baseMaterial
                    ? `V${baseMaterial.revision}`
                    : c("个人新素材", "New personal material")}
                </Hint>
                <Hint>
                  {c(
                    "保存前由服务端校验 SVG 与参数绑定。",
                    "The server validates SVG and parameter bindings before saving.",
                  )}
                </Hint>
              </ScrollView>
            ) : null}
          </View>
        )}
      </Sheet>
    </FeatureShell>
  );
}
