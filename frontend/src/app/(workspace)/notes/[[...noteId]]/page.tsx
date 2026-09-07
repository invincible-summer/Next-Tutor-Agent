"use client";
// /notes/[[...noteId]]：optional catchall 让「仓库首页 ↔ 具体笔记」切换不
// 重绘页面（与 /chat/[[...sessionId]] 同一套技巧）。
import { useParams } from "next/navigation";
import { NotesView } from "@/components/pages/notes/NotesView";

export default function NotesPage() {
  const params = useParams<{ noteId?: string[] }>();
  const segments = params?.noteId;
  const id = segments && segments.length > 0 ? decodeURIComponent(segments[0]) : undefined;
  return <NotesView noteId={id} />;
}
