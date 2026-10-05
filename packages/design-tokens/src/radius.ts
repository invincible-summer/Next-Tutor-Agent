/** Corner radius tokens in dp/px. */
import raw from "../data/tokens.json" with { type: "json" };

export const RADIUS = Object.freeze({ ...raw.radius }) as Readonly<{
  xs: number;
  sm: number;
  md: number;
  lg: number;
  xl: number;
  xxl: number;
}>;
