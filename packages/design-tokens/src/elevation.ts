/** Elevation tokens: shadow strings per theme, level 0 = flat. */
import raw from "../data/tokens.json" with { type: "json" };

export type ShadowStep = "sm" | "md" | "lg";
export type ShadowSet = Readonly<Record<ShadowStep, string>>;

export const ELEVATION_LEVELS = Object.freeze([...raw.elevation.levels]) as readonly number[];

export const lightShadows: ShadowSet = Object.freeze(raw.shadow.light) as ShadowSet;
export const darkShadows: ShadowSet = Object.freeze(raw.shadow.dark) as ShadowSet;
