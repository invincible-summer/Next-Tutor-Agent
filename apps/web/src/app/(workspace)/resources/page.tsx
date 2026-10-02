"use client";
import { useEffect, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";

function ResourcesRedirect() {
  const router = useRouter();
  const params = useSearchParams();
  const tab = params.get("tab");
  useEffect(() => { router.replace(tab === "textbooks" ? "/resources/textbooks" : "/resources/files"); }, [router, tab]);
  return null;
}

export default function ResourcesPage() {
  return <Suspense><ResourcesRedirect /></Suspense>;
}
