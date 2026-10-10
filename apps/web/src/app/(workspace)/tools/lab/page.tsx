"use client";

import { ChemLabCatalog } from "@/components/pages/tools/chem-lab-3d/ChemLabCatalog";

/**
 * /tools/lab — 化学模拟实验台目录。关卡只是场景主题和器材组合，不是
 * 步骤、测评或会话入口；进入后由 3D 工作台承载自由拼装。
 */
export default function LabPage() {
  return <ChemLabCatalog />;
}
