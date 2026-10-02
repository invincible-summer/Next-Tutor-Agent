import { PageSkeleton } from "@/components/ui/EmptyState";

/** 路由级加载骨架：路由分包（JS chunk）加载期间即时反馈，
 *  复用各页面统一的 PageSkeleton，视觉语言一致。 */
export default function WorkspaceLoading() {
  return (
    <div className="page-in mx-auto h-full max-w-[1200px] p-6">
      <PageSkeleton />
    </div>
  );
}
