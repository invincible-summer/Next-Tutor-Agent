"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { LabWorkbench } from "@/components/pages/tools/chem-lab-3d/LabWorkbench";

/**
 * /tools/lab/chemistry?stage=<stageId> — 沉浸式 3D 实验台（新）。
 * 只认 stage 参数；旧 session/at/experiment 不再解析。未知关卡由
 * LabWorkbench 显示极简“关卡不存在”并引导回目录。
 */
function StageRoute() {
  const params = useSearchParams();
  const stageId = params.get("stage") ?? "";
  return <LabWorkbench stageId={stageId} />;
}

export default function ChemistryLabPage() {
  return (
    <Suspense fallback={<div className="h-full bg-bg" />}>
      <StageRoute />
    </Suspense>
  );
}
