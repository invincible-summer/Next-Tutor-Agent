"use client";

import { Suspense } from "react";
import { IllustrationWorkspace } from "@/components/pages/tools/IllustrationWorkspace";

export default function IllustrationPage() {
  return <Suspense fallback={<div className="h-full bg-bg" />}><IllustrationWorkspace /></Suspense>;
}
