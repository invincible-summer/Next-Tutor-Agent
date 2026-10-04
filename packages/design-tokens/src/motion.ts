/** Motion duration tokens; consumers must honor the platform reduce-motion setting. */
import raw from "../data/tokens.json" with { type: "json" };

export const MOTION = Object.freeze({ ...raw.motion }) as Readonly<{
  fastMs: number;
  baseMs: number;
  moderateMs: number;
  slowMs: number;
}>;
