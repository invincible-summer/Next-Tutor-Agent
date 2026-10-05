import React, { useState } from "react";
import { View } from "react-native";
import type { SelectedMaterialRef } from "@next-tutor/contracts";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { useServerQuery } from "@/lib/server-state";
import { Button, Card, Chip, TextField, Sheet, SegmentedControl } from "@/ui";
import {
  Hint,
  Label,
  Pager,
  QueryState,
  SheetBody,
  SheetHeader,
} from "@/ui/Elements";
import { SvgCanvas } from "@/ui/SvgCanvas";
export type MaterialChoice = SelectedMaterialRef & { title: string };
export function MaterialPicker({
  open,
  selected,
  onClose,
  onApply,
}: {
  open: boolean;
  selected: MaterialChoice[];
  onClose: () => void;
  onApply: (items: MaterialChoice[]) => void;
}) {
  const c = useCopy();
  const [scope, setScope] = useState("builtin");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [choice, setChoice] = useState(selected);
  const query = useServerQuery(
    ["materials-picker", scope, q, page],
    async (s) => {
      if (scope === "builtin") {
        const r = await apiClient().diagrams.listAssets(
          { q, page: page - 1 },
          s,
        );
        return {
          items: r.items.map((a) => ({
            asset_id: a.id,
            version: Number(a.version) || 1,
            title: a.title,
            svg: a.illustration?.svg || "",
          })),
          total: r.total,
          per: r.per,
        };
      }
      const r = await apiClient().diagrams.listMaterials(
        scope as "private" | "public",
        q,
        page - 1,
        s,
        { enabledOnly: true },
      );
      return {
        items: r.items.map((a) => ({
          asset_id: "material." + a.id,
          version: a.revision,
          title: a.title,
          svg: a.illustration?.svg || "",
        })),
        total: r.total,
        per: r.per ?? 12,
      };
    },
    { enabled: open },
  );
  return (
    <Sheet
      open={open}
      onClose={onClose}
      label={c("选择素材", "Choose materials")}
    >
      <SheetHeader
        title={c("选择素材", "Choose materials")}
        onClose={onClose}
      />
      <SheetBody>
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
        <TextField
          value={q}
          onChangeText={(v) => {
            setQ(v);
            setPage(1);
          }}
          placeholder={c("搜索素材", "Search materials")}
          accessibilityLabel={c("搜索素材", "Search materials")}
        />
        <QueryState query={query} empty={!query.data?.items.length}>
          {query.data?.items.map((item) => (
            <Card key={item.asset_id} style={{ gap: 10 }}>
              <Label>{item.title}</Label>
              {item.svg ? (
                <SvgCanvas svg={item.svg} height={140} alt={item.title} />
              ) : null}
              <Hint>V{item.version}</Hint>
              <Button
                title={
                  choice.some((m) => m.asset_id === item.asset_id)
                    ? c("已选择 · 移除", "Selected · Remove")
                    : c("选择", "Select")
                }
                variant="outline"
                onPress={() =>
                  setChoice((old) =>
                    old.some((m) => m.asset_id === item.asset_id)
                      ? old.filter((m) => m.asset_id !== item.asset_id)
                      : [
                          ...old,
                          {
                            asset_id: item.asset_id,
                            version: item.version,
                            title: item.title,
                          },
                        ],
                  )
                }
              />
            </Card>
          ))}
          <Pager
            page={page}
            total={query.data?.total ?? 0}
            pageSize={query.data?.per ?? 12}
            onChange={setPage}
          />
        </QueryState>
        <Button
          title={c(
            `使用 ${choice.length} 个素材`,
            `Use ${choice.length} materials`,
          )}
          onPress={() => {
            onApply(choice);
            onClose();
          }}
        />
      </SheetBody>
    </Sheet>
  );
}
