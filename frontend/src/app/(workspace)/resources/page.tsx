import { redirect } from "next/navigation";

// 资料中心落点：Tab 已路由段化（/resources/files | /resources/textbooks）。
// 这里保留旧深链兼容 —— /resources?tab=textbooks → 教材库，其余 → 文件库。
export default async function ResourcesPage({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string }>;
}) {
  const params = await searchParams;
  redirect(params.tab === "textbooks" ? "/resources/textbooks" : "/resources/files");
}
