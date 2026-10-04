/**
 * Color tokens: RGB triplets as "R G B" strings, matching the CSS custom
 * properties in the generated web stylesheet. Consumers compose them with
 * their platform color APIs (e.g. `rgb(${token})` / alpha variants).
 */
import raw from "../data/tokens.json" with { type: "json" };

export type ColorKey =
  | "bg"
  | "surface"
  | "surface-hover"
  | "surface-sunken"
  | "border"
  | "border-light"
  | "fg"
  | "fg-secondary"
  | "fg-tertiary"
  | "muted"
  | "accent"
  | "accent-strong"
  | "accent-soft"
  | "accent2"
  | "accent2-strong"
  | "accent2-soft"
  | "success"
  | "warning"
  | "danger"
  | "info";

export type ThemeName = "light" | "dark";

export type ColorPalette = Readonly<Record<ColorKey, string>>;

export const lightColors: ColorPalette = Object.freeze(raw.color.light) as ColorPalette;
export const darkColors: ColorPalette = Object.freeze(raw.color.dark) as ColorPalette;

export function colorsFor(theme: ThemeName): ColorPalette {
  return theme === "dark" ? darkColors : lightColors;
}

/** Parse an "R G B" triplet into numeric components. */
export function rgbTriplet(value: string): readonly [number, number, number] {
  const parts = value.trim().split(/\s+/).map(Number);
  if (parts.length !== 3 || parts.some(n => !Number.isInteger(n) || n < 0 || n > 255)) {
    throw new Error(`invalid color triplet: ${value}`);
  }
  const [r, g, b] = parts as [number, number, number];
  return [r, g, b];
}
