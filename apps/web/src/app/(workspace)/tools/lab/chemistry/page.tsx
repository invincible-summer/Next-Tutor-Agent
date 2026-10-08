"use client";

import { Suspense } from "react";
import { ChemLabWorkspace } from "@/components/pages/tools/chem-lab/ChemLabWorkspace";

export default function ChemistryLabPage() {
  return (
    <Suspense fallback={<div className="h-full bg-bg" />}>
      <ChemLabWorkspace />
    </Suspense>
  );
}
