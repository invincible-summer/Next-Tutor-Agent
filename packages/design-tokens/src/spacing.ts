/** Spacing tokens: 4pt scale, expressed in dp/px. */
import raw from "../data/tokens.json" with { type: "json" };

export const SPACING_UNIT = raw.spacing.unit;
export const SPACING_SCALE = Object.freeze([...raw.spacing.scale]) as readonly number[];

export function space(multiplier: number): number {
  return SPACING_UNIT * multiplier;
}
