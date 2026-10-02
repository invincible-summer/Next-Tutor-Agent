/* 课程卡主题色板（列表页与 /course Hub 共用，单一事实源）。
 * 键为 theme_id 去掉 @版本 后缀；未知主题回退 academic_clear。 */

export interface CardPalette {
  bg: string;
  text: string;
  accent: string;
  surface: string;
}

export const CARD_PALETTE: Record<string, CardPalette> = {
  academic_clear: { bg: "#FAF7F0", text: "#1B2A4A", accent: "#2456C6", surface: "#FFFFFF" },
  chalk_focus: { bg: "#21372F", text: "#F5F1E6", accent: "#F2D478", surface: "#2A443B" },
  visual_story: { bg: "#FFFFFF", text: "#22252A", accent: "#C2542D", surface: "#F7F5F1" },
  lab_notebook: { bg: "#FBFCF7", text: "#16324F", accent: "#0F766E", surface: "#FFFFFF" },
  gentle_beginner: { bg: "#FFF9F2", text: "#3D3A50", accent: "#7C6FD9", surface: "#FFFFFF" },
};

export function cardPaletteFor(themeId: string | null | undefined): CardPalette {
  return CARD_PALETTE[(themeId ?? "academic_clear").split("@")[0]]
    ?? CARD_PALETTE.academic_clear;
}
