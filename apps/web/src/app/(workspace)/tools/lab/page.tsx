"use client";

import { Suspense } from "react";
import { ChemLabWorkspace } from "@/components/pages/tools/chem-lab/ChemLabWorkspace";

/**
 * /tools/lab — 模拟实验室目录（首期只有化学）。无参数时 ChemLabWorkspace
 * 渲染目录视图：实验卡片 + 我的会话；?experiment= 与 ?session= 深链由
 * /tools/lab/chemistry 承载。
 */
export default function LabPage() {
  return (
    <Suspense fallback={<div className="h-full bg-bg" />}>
      <ChemLabWorkspace />
    </Suspense>
  );
}
