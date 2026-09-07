"use client";
// 学段 × 学科两级联动下拉（M5.8 学科目录，GET /knowledge/catalog）。
// catalog 加载失败时优雅回退为学科自由文本输入（level 维持父级传入值不变）。
import { useEffect, useMemo, useState } from "react";
import { getKnowledgeCatalog } from "@/lib/api-modules";
import type { CatalogStage } from "@/lib/types-modules";
import { cn } from "@/lib/cn";
import { FIELD_CLS } from "@/components/ui/Input";

type Tr = (key: string, fallback?: string) => string;

const FIELD = FIELD_CLS;

export function SubjectSelect({
  tr,
  level,
  subject,
  onChange,
  disabled = false,
  className,
}: {
  tr: Tr;
  /** 当前学段（父级持有；catalog 失败时保持不变，作为 grade 语义兜底） */
  level: string;
  subject: string;
  onChange: (level: string, subject: string) => void;
  disabled?: boolean;
  className?: string;
}) {
  const [stages, setStages] = useState<CatalogStage[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getKnowledgeCatalog()
      .then((r) => {
        if (cancelled) return;
        if (r.status === "ok" && Array.isArray(r.stages) && r.stages.length > 0) setStages(r.stages);
        else setFailed(true);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 当前学段不在目录里（如账户年级写法不同）时保留原值作为一个额外选项，避免选中态丢失
  const levelOptions = useMemo(() => {
    const opts = (stages ?? []).map((s) => s.level);
    if (level && !opts.includes(level)) opts.unshift(level);
    return opts;
  }, [stages, level]);

  const subjectOptions = useMemo(
    () => stages?.find((s) => s.level === level)?.subjects ?? [],
    [stages, level],
  );

  // 回退：自由文本学科输入（原行为）
  if (failed) {
    return (
      <label className={cn("flex flex-col gap-1.5", className)}>
        <span className="text-xs text-muted">{tr("catalog.subject", "学科")}</span>
        <input
          value={subject}
          onChange={(e) => onChange(level, e.target.value)}
          placeholder={tr("catalog.subjectPh", "")}
          disabled={disabled}
          className={FIELD}
        />
      </label>
    );
  }

  const loading = stages === null;
  return (
    <div className={cn("grid grid-cols-1 gap-3 sm:grid-cols-2", className)}>
      <label className="flex flex-col gap-1.5">
        <span className="text-xs text-muted">{tr("catalog.level", "学段")}</span>
        <select
          value={level}
          disabled={disabled || loading}
          onChange={(e) => onChange(e.target.value, "")}
          className={cn(FIELD, "cursor-pointer")}
        >
          {loading && <option value="">{tr("catalog.loading", "…")}</option>}
          {!loading && level === "" && <option value="">{tr("catalog.levelPh", "…")}</option>}
          {levelOptions.map((lv) => (
            <option key={lv} value={lv}>
              {lv}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1.5">
        <span className="text-xs text-muted">{tr("catalog.subject", "学科")}</span>
        <select
          value={subject}
          disabled={disabled || loading || !level}
          onChange={(e) => onChange(level, e.target.value)}
          className={cn(FIELD, "cursor-pointer")}
        >
          <option value="">{tr("catalog.anySubject", "（不限）")}</option>
          {subjectOptions.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
