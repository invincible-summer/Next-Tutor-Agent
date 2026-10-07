"use client";

import { useEffect, useState, type FormEvent } from "react";
import Image from "next/image";
import Link from "next/link";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field, FIELD_CLS, Textarea } from "@/components/ui/Input";
import { useToast } from "@/components/ui/Toast";
import { useUIStore } from "@/lib/store";
import { getImageCapability, generateImage, type ImageCapability, type ImageAspectRatio, type ImageReferenceMode, type ImageGenerationResult } from "@/lib/api-image-generation";
import { MaterialPicker, type SelectedMaterial } from "./MaterialPicker";
import { AlertMark, BackMark, BusyMark, ComposeMark, MaterialAtlasMark, SceneWeaveMark } from "./ToolMarks";

/** Shared V4 generator used by the illustration workspace and worksheet editor. */
export function ImageGenerationStudio({
  initialPrompt = "",
  onGenerated,
  showBackLink = true,
}: {
  initialPrompt?: string;
  onGenerated?: (result: ImageGenerationResult) => void;
  showBackLink?: boolean;
}) {
  const lang = useUIStore((s) => s.lang);
  const zh = lang !== "en";
  const toast = useToast();
  const [capability, setCapability] = useState<ImageCapability | null>(null);
  const [prompt, setPrompt] = useState(initialPrompt);
  const [mode, setMode] = useState<ImageReferenceMode>("direct");
  const [aspect, setAspect] = useState<ImageAspectRatio>("16:9");
  const [materials, setMaterials] = useState<SelectedMaterial[]>([]);
  const [picker, setPicker] = useState(false);
  const [result, setResult] = useState<ImageGenerationResult | null>(null);
  const [busy, setBusy] = useState(false);
  const referencesAvailable = capability?.supports_reference !== false;
  useEffect(() => { const controller = new AbortController(); getImageCapability(controller.signal).then(setCapability).catch(() => setCapability(null)); return () => controller.abort(); }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!prompt.trim() || busy) return;
    setBusy(true);
    try {
      const value = await generateImage({ prompt: prompt.trim(), reference_mode: mode, reference_material_ids: materials.map((item) => item.asset_id), reference_material_versions: Object.fromEntries(materials.map((item) => [item.asset_id, item.version])), aspect_ratio: aspect });
      setResult(value); onGenerated?.(value); toast(zh ? "配图已生成，请在情景配图中继续优化。" : "Image generated. Continue in the illustration workspace.");
    } catch (error) { toast(error instanceof Error ? error.message : (zh ? "生图失败，请重试。" : "Image generation failed."), "error"); }
    finally { setBusy(false); }
  }
  return <div className="h-full overflow-y-auto bg-bg p-5 page-in"><div className="mx-auto max-w-3xl space-y-5">{showBackLink && <Link href="/tools/illustration" className="inline-flex items-center gap-1.5 text-xs text-muted hover:text-accent"><BackMark className="h-4 w-4" />{zh ? "返回情景配图" : "Back to illustration"}</Link>}<header><h1 className="mt-3 flex items-center gap-2 font-serif text-2xl font-semibold text-fg"><SceneWeaveMark className="h-7 w-7 text-accent" />{zh ? "V4 AI 配图" : "V4 AI image"}</h1><p className="mt-2 text-sm leading-6 text-muted">{zh ? "V4 已合并到情景配图工作区；这里保留旧链接的兼容入口。" : "V4 now lives in the illustration workspace; this page keeps the old link compatible."}</p></header><Card className="space-y-4"><div className="flex items-center justify-between gap-3"><span className="text-sm font-medium text-fg">{zh ? "服务端模型" : "Server model"}</span>{capability ? <span className={`rounded-full px-2 py-1 text-[10px] ${capability.configured ? "bg-success/10 text-success" : "bg-warning/10 text-warning"}`}>{capability.configured ? (zh ? "已配置" : "Configured") : (zh ? "未配置" : "Not configured")}</span> : <span className="text-xs text-muted">{zh ? "读取中…" : "Loading…"}</span>}</div>{capability?.configured ? <p className="text-xs text-muted">{capability.provider} · {capability.model} · {capability.protocol}</p> : <p className="flex items-center gap-2 text-xs text-warning"><AlertMark className="h-4 w-4" />{zh ? "请由部署方配置 IMAGE_API_* 环境变量。" : "Ask the deployment owner to configure IMAGE_API_* variables."}</p>}{capability?.configured && !referencesAvailable && <p className="text-xs text-warning">{zh ? "当前服务只支持直接生成，参考素材已由服务端关闭。" : "This server only supports direct generation; references are disabled."}</p>}<form onSubmit={submit} className="space-y-4"><Field label={zh ? "配图需求" : "Image brief"}><Textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} rows={6} placeholder={zh ? "描述主体、关系、标注和画幅；这段文字只作为生成指导。" : "Describe subjects, relationships, labels and aspect; this text is guidance only."} /></Field><div className="grid gap-3 sm:grid-cols-3"><Field label={zh ? "参考方式" : "References"}><select className={FIELD_CLS} value={mode} onChange={(event) => setMode(event.target.value as ImageReferenceMode)}><option value="direct">{zh ? "直接生成" : "Direct"}</option><option value="auto" disabled={!referencesAvailable}>{zh ? "自动参考素材" : "Auto references"}</option><option value="selected" disabled={!referencesAvailable}>{zh ? "选择素材" : "Choose materials"}</option></select></Field><Field label={zh ? "画幅" : "Aspect"}><select className={FIELD_CLS} value={aspect} onChange={(event) => setAspect(event.target.value as ImageAspectRatio)}><option value="16:9">16:9</option><option value="4:3">4:3</option><option value="1:1">1:1</option><option value="3:4">3:4</option></select></Field><div className="flex items-end"><Button type="submit" className="w-full" disabled={!prompt.trim() || busy || !capability?.configured || (!referencesAvailable && mode !== "direct")} icon={busy ? <BusyMark className="h-4 w-4 animate-spin" /> : <MaterialAtlasMark className="h-4 w-4" />}>{busy ? (zh ? "生成中…" : "Generating…") : (zh ? "生成" : "Generate")}</Button></div></div>{mode === "selected" && referencesAvailable && <div className="rounded-lg border border-border-light bg-bg p-3"><Button type="button" size="sm" variant="outline" onClick={() => setPicker(true)} icon={<ComposeMark className="h-4 w-4" />}>{zh ? `选择素材（已选 ${materials.length} 项）` : `Choose materials (${materials.length})`}</Button></div>}</form></Card>{result && <Card><p className="mb-3 text-xs text-muted">{result.provider} · {result.model}</p><Image src={result.image_url} alt={prompt} width={960} height={540} unoptimized className="w-full rounded-lg border border-border object-contain" /></Card>}</div>{picker && <MaterialPicker selected={materials} onClose={() => setPicker(false)} onApply={(items) => { setMaterials(items); setPicker(false); }} />}</div>;
}
