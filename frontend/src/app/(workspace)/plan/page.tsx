import { redirect } from "next/navigation";

// C13：/plan 已并入 /knowledge 的「教学计划」区（模式状态机 + 难度表盘 +
// 完整路径两栏 + 教学日志）。本路由仅保留 redirect，外部深链不断。
export default function PlanPage() {
  redirect("/knowledge");
}
