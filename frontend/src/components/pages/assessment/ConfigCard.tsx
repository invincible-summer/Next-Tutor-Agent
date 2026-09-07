"use client";

// idle 阶段：发起自适应测评的配置卡。
// 学段 × 学科下拉来自 M5.8 学科目录（catalog 失败时 SubjectSelect 回退自由文本）。
// 概念输入支持两种方式：自由文本（模糊归因兜底）或从知识谱系选择（稳定归因到
// 图谱节点）；层级焦点默认"自动"——由出题 LLM 结合认知档案综合判断（反僵化）。
import { useState } from "react";
import { Play, ClipboardList } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { SubjectSelect } from "@/components/ui/SubjectSelect";
import {
  GenealogyConceptPicker,
  type PickedConcept,
} from "@/components/shared/GenealogyConceptPicker";
import type { PageTr } from "./common";

const BLOOM_OPTIONS = [
  { value: "", zh: "自动（按认知档案智能判断）", en: "Auto (profile-aware)" },
  { value: "remember", zh: "记忆", en: "Remember" },
  { value: "understand", zh: "理解", en: "Understand" },
  { value: "apply", zh: "应用", en: "Apply" },
  { value: "analyze", zh: "分析", en: "Analyze" },
  { value: "evaluate", zh: "评价", en: "Evaluate" },
  { value: "create", zh: "创造", en: "Create" },
];

export function ConfigCard({
  tr,
  grade,
  busy,
  lang,
  onStart,
}: {
  tr: PageTr;
  /** 账户学段：作为学段下拉的初始值（catalog 失败时即 grade 语义兜底） */
  grade: string;
  lang: "zh" | "en";
  busy: boolean;
  onStart: (concept: string, subject: string, level: string, bloomFocus?: string) => void;
}) {
  const [concept, setConcept] = useState("");
  const [level, setLevel] = useState(grade);
  const [subject, setSubject] = useState("");
  const [bloom, setBloom] = useState("");
  const [picked, setPicked] = useState<PickedConcept[]>([]);

  const onPickedChange = (next: PickedConcept[]) => {
    setPicked(next);
    // 谱系选择 → 概念取最后一个选中项（单概念测评），仍可自由文本覆盖
    setConcept(next.length ? next[next.length - 1].name : "");
  };

  const submit = () => {
    const c = concept.trim();
    if (!c || busy) return;
    onStart(c, subject.trim(), level, bloom);
  };

  return (
    <Card>
      <CardHeader
        icon={<ClipboardList size={16} />}
        title={tr("config.title")}
        desc={tr("config.desc")}
      />
      <div className="flex flex-col gap-3">
        <label className="flex flex-col gap-1.5">
          <span className="text-xs text-muted">{tr("config.concept")}</span>
          <input
            value={concept}
            onChange={(e) => setConcept(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            placeholder={tr("config.conceptPh")}
            disabled={busy}
            className="h-9 rounded-[8px] border border-border px-3 text-sm"
          />
        </label>
        <SubjectSelect
          tr={tr}
          level={level}
          subject={subject}
          disabled={busy}
          onChange={(lv, s) => {
            setLevel(lv);
            setSubject(s);
          }}
        />
        <label className="flex flex-col gap-1.5">
          <span className="text-xs text-muted">{tr("config.bloom")}</span>
          <select
            value={bloom}
            disabled={busy}
            onChange={(e) => setBloom(e.target.value)}
            className="h-9 rounded-[8px] border border-border bg-surface px-3 text-sm text-fg"
          >
            {BLOOM_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {lang === "en" ? o.en : o.zh}
              </option>
            ))}
          </select>
        </label>
        <GenealogyConceptPicker selected={picked} onChange={onPickedChange} />
      </div>
      <div className="mt-4 flex justify-end">
        <Button
          size="lg"
          icon={<Play size={15} />}
          disabled={busy || !concept.trim()}
          onClick={submit}
        >
          {busy ? tr("config.starting") : tr("config.start")}
        </Button>
      </div>
    </Card>
  );
}
