/**
 * 学段契约（对齐 Web lib/types.ts）：UI 用「自动」token，后端事实源是空串。
 * 发往后端前用 gradeForApi 转成 ""；读回用 gradeFromApi 转成「自动」展示。
 */
export type Grade = "自动" | "小学" | "初中" | "高中" | "本科";

export const AUTO_GRADE: Grade = "自动";

export const GRADES: Grade[] = ["自动", "小学", "初中", "高中", "本科"];

export const GRADE_LABEL_KEYS: Record<Grade, string> = {
  自动: "auth.grade.auto",
  小学: "auth.grade.primary",
  初中: "auth.grade.middle",
  高中: "auth.grade.high",
  本科: "auth.grade.undergraduate",
};

export function gradeForApi(grade: Grade | string | undefined | null): string {
  return grade === AUTO_GRADE ? "" : (grade ?? "");
}

export function gradeFromApi(grade: string | undefined | null): Grade {
  return grade ? (grade as Grade) : AUTO_GRADE;
}
