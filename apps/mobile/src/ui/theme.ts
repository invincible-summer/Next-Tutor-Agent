import { Platform, type TextStyle, type ViewStyle } from "react-native";
import {
  colorsFor,
  rgbTriplet,
  RADIUS,
  SPACING_SCALE,
  MOTION,
  type ColorKey,
  type ThemeName,
} from "@next-tutor/design-tokens";

/**
 * 主题系统：唯一事实源是 @next-tutor/design-tokens。
 * Web 的 CSS 变量在这里映射为 RN 可用的颜色字符串 / 阴影对象 / 字体族。
 * 暖纸底、清晰的系统字体、青绿主色。
 */

export type { ThemeName };
export type ThemePreference = "system" | ThemeName;

function toRgb(name: ThemeName, key: ColorKey): string {
  const [r, g, b] = rgbTriplet(colorsFor(name)[key]);
  return `rgb(${r}, ${g}, ${b})`;
}

export type AppColors = Record<ColorKey, string> & {
  /** accent 之上的文字按深浅主题保证对比度。 */
  onAccent: string;
};

function buildColors(name: ThemeName): AppColors {
  const keys = Object.keys(colorsFor(name)) as ColorKey[];
  const out = {} as Record<ColorKey, string>;
  for (const key of keys) out[key] = toRgb(name, key);
  return { ...out, onAccent: name === "dark" ? "rgb(16, 32, 30)" : "#ffffff" };
}

/** 给 rgb(r,g,b) 颜色叠加透明度；无法解析时原样返回。 */
export function alpha(color: string, opacity: number): string {
  const m = /^rgb\(\s*(\d+),\s*(\d+),\s*(\d+)\s*\)$/.exec(color);
  if (!m) return color;
  return `rgba(${m[1]}, ${m[2]}, ${m[3]}, ${opacity})`;
}

// --- 阴影：token 里是 CSS box-shadow 语法，RN 需要结构化写法 ----------------

export interface ShadowStyle {
  shadowColor: string;
  shadowOpacity: number;
  shadowRadius: number;
  shadowOffset: { width: number; height: number };
  elevation: number;
}

function buildShadows(
  name: ThemeName,
): Record<"sm" | "md" | "lg", ShadowStyle> {
  // 透明度/扩散语义对齐 tokens.json 的 sm/md/lg 三档。
  const dark = name === "dark";
  return {
    sm: {
      shadowColor: dark ? "#000" : "rgb(30, 26, 18)",
      shadowOpacity: dark ? 0.25 : 0.05,
      shadowRadius: 2,
      shadowOffset: { width: 0, height: 1 },
      elevation: 1,
    },
    md: {
      shadowColor: dark ? "#000" : "rgb(30, 26, 18)",
      shadowOpacity: dark ? 0.3 : 0.08,
      shadowRadius: 8,
      shadowOffset: { width: 0, height: 3 },
      elevation: 3,
    },
    lg: {
      shadowColor: dark ? "#000" : "rgb(30, 26, 18)",
      shadowOpacity: dark ? 0.35 : 0.12,
      shadowRadius: 16,
      shadowOffset: { width: 0, height: 6 },
      elevation: 6,
    },
  };
}

// --- 字体：标题与正文采用系统 sans，代码采用 mono --------

export interface FontSet {
  /** undefined = RN 系统默认 sans（iOS SF/PingFang，Android Roboto/Noto）。 */
  sans: string | undefined;
  mono: string;
}

const FONTS: FontSet = {
  sans: undefined,
  mono: Platform.select({
    ios: "Menlo",
    android: "monospace",
    default: "monospace",
  })!,
};

// --- 字号阶梯（Web 约定移植：正文 14→移动端 15，徽章 10-11，统计大字 tabular） ---

export interface TypeScale {
  display: TextStyle;
  title: TextStyle;
  titleSmall: TextStyle;
  body: TextStyle;
  bodyStrong: TextStyle;
  label: TextStyle;
  caption: TextStyle;
  code: TextStyle;
}

function buildType(scale: number): TypeScale {
  const s = (n: number) => Math.round(n * scale * 10) / 10;
  return {
    display: {
      fontSize: s(30),
      lineHeight: s(39),
      fontWeight: "700",
      letterSpacing: -0.6,
    },
    title: {
      fontSize: s(24),
      lineHeight: s(32),
      fontWeight: "700",
      letterSpacing: -0.4,
    },
    titleSmall: { fontSize: s(18), lineHeight: s(26), fontWeight: "600" },
    body: { fontSize: s(16), lineHeight: s(24), fontWeight: "400" },
    bodyStrong: { fontSize: s(16), lineHeight: s(24), fontWeight: "600" },
    label: { fontSize: s(13), lineHeight: s(18), fontWeight: "500" },
    caption: { fontSize: s(12), lineHeight: s(16), fontWeight: "400" },
    code: { fontSize: s(13), lineHeight: s(19), fontFamily: FONTS.mono },
  };
}

// --- 汇总 --------------------------------------------------------------------

export interface AppTheme {
  name: ThemeName;
  isDark: boolean;
  colors: AppColors;
  radius: typeof RADIUS;
  /** 4pt 网格间距。 */
  space: (multiplier: number) => number;
  motion: typeof MOTION;
  fonts: FontSet;
  type: TypeScale;
  shadow: Record<"sm" | "md" | "lg", ShadowStyle>;
  /** 卡片通用样式（纸面卡片：surface + 1px border + radius 10 + sm 阴影）。 */
  card: ViewStyle;
}

/** fontScale：设置页字号档位 1 / 1.25 / 1.5 / 1.75（对齐 Web --fs-scale）。 */
export function buildTheme(name: ThemeName, fontScale = 1): AppTheme {
  const space = (n: number) => n * 4;
  return {
    name,
    isDark: name === "dark",
    colors: buildColors(name),
    radius: RADIUS,
    space,
    motion: MOTION,
    fonts: FONTS,
    type: buildType(fontScale),
    shadow: buildShadows(name),
    card: {
      backgroundColor: buildColors(name).surface,
      borderColor: buildColors(name)["border-light"],
      borderWidth: 1,
      borderRadius: 16,
    },
  };
}

export { RADIUS, MOTION, SPACING_SCALE };
