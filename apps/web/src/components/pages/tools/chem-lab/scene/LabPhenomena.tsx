"use client";

/**
 * Frame-driven phenomena inside vessel cavities: liquid body with meniscus,
 * turbidity, precipitate bed/suspension, bubbles, steam. Every value comes
 * from the VesselVisual projection of the authoritative RenderFrame — an
 * animation may interpolate *between* two frames but never invents a
 * phenomenon the frame does not carry.
 *
 * This module also owns the vessel *cavity profiles*: for each pack vessel
 * kind, the inner-cavity rectangle (as fractions of the node box) and a
 * half-width function fy→width used to pour liquid into the true glass
 * silhouette (a conical flask's surface narrows toward the shoulder).
 */
import type { VesselVisual } from "../chem-lab-renderer";
import { scatter } from "./scene-geometry.ts";

export interface CavityBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface VesselProfile {
  /** Inner cavity as fractions of the node's world box. */
  cavity: CavityBox;
  /** Cavity half-width at height fraction fy (0 = cavity bottom, 1 = top),
   * as a fraction of the cavity width (0..0.5). */
  halfAt: (fy: number) => number;
  /** Meniscus dips toward the centre (beaker-like) or climbs (tube-like). */
  meniscus: "dip" | "climb";
  /** Wall path (glass outline) in node-box fractions. */
  wall: string;
  /** Optional top rim ellipse (cx, cy, rx) in fractions + whether a spout exists. */
  rim: { cx: number; cy: number; rx: number; ry: number } | null;
  spout: boolean;
  /** Bottom rounding radius as a fraction of the node width. */
  bottomRadius: number;
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

export const VESSEL_PROFILES: Record<string, VesselProfile> = {
  "beaker.small": {
    cavity: { x: 0.16, y: 0.1, w: 0.68, h: 0.84 },
    halfAt: (fy) => 0.5 - 0.03 * clamp01(fy),
    meniscus: "dip",
    wall: "M0.14 0.08 L0.15 0.9 Q0.15 0.97 0.24 0.97 L0.76 0.97 Q0.85 0.97 0.85 0.9 L0.86 0.08",
    rim: { cx: 0.5, cy: 0.08, rx: 0.36, ry: 0.035 },
    spout: true,
    bottomRadius: 0.07,
  },
  reagent_bottle: {
    cavity: { x: 0.2, y: 0.24, w: 0.6, h: 0.68 },
    halfAt: (fy) => (fy > 0.78 ? 0.24 - 0.1 * (fy - 0.78) / 0.22 : 0.5 - 0.06 * Math.pow((0.78 - fy) / 0.78, 2)),
    meniscus: "dip",
    wall: "M0.4 0.02 L0.4 0.16 Q0.22 0.22 0.21 0.36 L0.21 0.88 Q0.21 0.97 0.32 0.97 L0.68 0.97 Q0.79 0.97 0.79 0.88 L0.79 0.36 Q0.78 0.22 0.6 0.16 L0.6 0.02",
    rim: null,
    spout: false,
    bottomRadius: 0.07,
  },
  graduated_cylinder: {
    cavity: { x: 0.37, y: 0.07, w: 0.26, h: 0.84 },
    halfAt: () => 0.5,
    meniscus: "climb",
    wall: "M0.36 0.04 L0.36 0.86 Q0.36 0.9 0.3 0.93 L0.22 0.96 Q0.2 0.985 0.28 0.985 L0.72 0.985 Q0.8 0.985 0.78 0.96 L0.7 0.93 Q0.64 0.9 0.64 0.86 L0.64 0.04",
    rim: { cx: 0.5, cy: 0.04, rx: 0.14, ry: 0.028 },
    spout: false,
    bottomRadius: 0.05,
  },
  conical_flask: {
    cavity: { x: 0.12, y: 0.15, w: 0.76, h: 0.8 },
    halfAt: (fy) => (fy < 0.62 ? 0.5 - 0.38 * (fy / 0.62) : 0.12),
    meniscus: "dip",
    wall: "M0.43 0.02 L0.43 0.28 L0.17 0.84 Q0.13 0.96 0.26 0.96 L0.74 0.96 Q0.87 0.96 0.83 0.84 L0.57 0.28 L0.57 0.02",
    rim: { cx: 0.5, cy: 0.02, rx: 0.075, ry: 0.024 },
    spout: false,
    bottomRadius: 0.09,
  },
  gas_cylinder: {
    cavity: { x: 0.24, y: 0.09, w: 0.52, h: 0.8 },
    halfAt: (fy) => (fy > 0.94 ? 0.5 - 0.34 * (fy - 0.94) / 0.06 : 0.5),
    meniscus: "climb",
    wall: "M0.26 0.06 L0.26 0.82 Q0.26 0.94 0.5 0.94 Q0.74 0.94 0.74 0.82 L0.74 0.06",
    rim: { cx: 0.5, cy: 0.06, rx: 0.25, ry: 0.03 },
    spout: false,
    bottomRadius: 0.18,
  },
};

/** Absolute cavity rect in node-local units. */
export function cavityRect(profile: VesselProfile, w: number, h: number) {
  return {
    x: profile.cavity.x * w,
    y: profile.cavity.y * h,
    w: profile.cavity.w * w,
    h: profile.cavity.h * h,
  };
}

/** Cavity polygon (sampled from the profile) for clip paths. */
export function cavityPath(profile: VesselProfile, w: number, h: number): string {
  const box = cavityRect(profile, w, h);
  const cx = box.x + box.w / 2;
  const steps = 12;
  const right: string[] = [];
  const left: string[] = [];
  for (let i = 0; i <= steps; i += 1) {
    const fy = i / steps;
    const half = profile.halfAt(fy) * box.w;
    const y = box.y + box.h * (1 - fy);
    right.push(`${(cx + half).toFixed(2)} ${y.toFixed(2)}`);
    left.unshift(`${(cx - half).toFixed(2)} ${y.toFixed(2)}`);
  }
  return `M${right.join(" L")} L${left.join(" L")} Z`;
}

/** Liquid polygon at fill fraction (0..1) — follows the glass profile. */
function liquidPath(profile: VesselProfile, w: number, h: number, fill: number): string {
  const box = cavityRect(profile, w, h);
  const cx = box.x + box.w / 2;
  const surfaceFy = clamp01(fill);
  const surfaceHalf = profile.halfAt(surfaceFy) * box.w;
  const surfaceY = box.y + box.h * (1 - surfaceFy);
  const dip = profile.meniscus === "dip" ? box.h * 0.018 : -box.h * 0.014;
  const steps = 8;
  const right: string[] = [];
  const left: string[] = [];
  for (let i = 0; i <= steps; i += 1) {
    const fy = surfaceFy * (i / steps);
    const half = profile.halfAt(fy) * box.w;
    const y = box.y + box.h * (1 - fy);
    right.push(`${(cx + half).toFixed(2)} ${y.toFixed(2)}`);
    left.unshift(`${(cx - half).toFixed(2)} ${y.toFixed(2)}`);
  }
  return [
    `M${(cx - surfaceHalf).toFixed(2)} ${(surfaceY + dip).toFixed(2)}`,
    `Q${cx.toFixed(2)} ${(surfaceY + dip * 2.4).toFixed(2)} ${(cx + surfaceHalf).toFixed(2)} ${surfaceY.toFixed(2)}`,
    `L${right.join(" L")}`,
    `L${left.join(" L")} Z`,
  ].join(" ");
}

const MAX_BUBBLES = 12;
const MAX_STEAM = 3;

export interface LabPhenomenaProps {
  kind: string;
  w: number;
  h: number;
  visual: VesselVisual | null;
  /** Deterministic seed: `${sessionId}:${vesselId}:${revision}`. */
  seed: string;
}

/** Contents of one vessel, clipped to its real cavity. */
export function LabPhenomena({ kind, w, h, visual, seed }: LabPhenomenaProps) {
  const profile = VESSEL_PROFILES[kind];
  if (!profile || !visual) return null;
  const box = cavityRect(profile, w, h);
  const fill = clamp01(visual.fillRatio);
  // Legibility floor: a real-but-tiny fraction (e.g. 4 mL in a 250 mL beaker)
  // still draws a visible base pool — never a hairline. Amounts stay truthful
  // via the volume labels/readouts; only the minimal *visible* height clamps.
  const fillShape = fill > 0.015 ? Math.max(fill, 0.05) : fill;
  const liquid = liquidPath(profile, w, h, fillShape);
  const color = visual.liquidColor ?? "var(--lab-liquid-default)";
  const liquidOpacity = 0.38 + visual.opacity * 0.55;
  const surfaceY = box.y + box.h * (1 - fillShape);
  const cx = box.x + box.w / 2;
  const surfaceHalf = profile.halfAt(clamp01(fillShape)) * box.w;

  const bubbleCount = visual.bubbles ? Math.min(MAX_BUBBLES, 2 + Math.round(visual.bubbles.rate * 10)) : 0;
  const bubbleRadius = (visual.bubbles?.size ?? 0) * Math.min(box.w, box.h) * 0.05
    + Math.min(box.w, 14) * 0.04;
  const precipitateHeight = Math.min(
    box.h * 0.32,
    (visual.precipitate?.amount ?? 0) * box.h * 0.24,
  );
  const suspension = visual.precipitate && visual.precipitate.texture !== "layer";

  return (
    <g>
      {fill > 0.005 && (
        <path d={liquid} fill={color} opacity={liquidOpacity} />
      )}
      {fill > 0.01 && (
        <>
          <path
            d={`M${cx - surfaceHalf} ${surfaceY} Q${cx} ${surfaceY - box.h * 0.03} ${cx + surfaceHalf} ${surfaceY}`}
            fill="none"
            stroke={color}
            strokeOpacity={Math.min(1, liquidOpacity + 0.3)}
            strokeWidth={Math.max(1.2, box.w * 0.05)}
          />
          {/* thin light reflection just above the meniscus */}
          <path
            d={`M${cx - surfaceHalf * 0.72} ${surfaceY - 1.4} Q${cx} ${surfaceY - 1.4 - box.h * 0.016} ${cx + surfaceHalf * 0.72} ${surfaceY - 1.4}`}
            fill="none"
            stroke="var(--lab-glass-shine)"
            strokeOpacity={0.6}
            strokeWidth={Math.max(0.8, box.w * 0.028)}
            strokeLinecap="round"
          />
        </>
      )}
      {visual.turbidity > 0.03 && fill > 0.02 && (
        <path d={liquid} fill="#e8e4da" opacity={visual.turbidity * 0.55} />
      )}
      {visual.precipitate && precipitateHeight > 0.5 && (
        <path
          d={`M${box.x} ${box.y + box.h} L${box.x} ${box.y + box.h - precipitateHeight} Q${cx} ${box.y + box.h - precipitateHeight - 2} ${box.x + box.w} ${box.y + box.h - precipitateHeight} L${box.x + box.w} ${box.y + box.h} Z`}
          fill={visual.precipitate.color ?? "#d6d3cb"}
          opacity={0.88}
        />
      )}
      {suspension &&
        Array.from({ length: 8 }, (_, i) => (
          <circle
            key={`speck-${i}`}
            cx={box.x + box.w * (0.14 + 0.72 * scatter(seed, i))}
            cy={surfaceY + (box.y + box.h - surfaceY) * (0.12 + 0.76 * scatter(seed, i + 40))}
            r={0.9 + scatter(seed, i + 80) * 1.1}
            fill={visual.precipitate?.color ?? "#d6d3cb"}
            opacity={0.6}
          />
        ))}
      {bubbleCount > 0 &&
        fill > 0.02 &&
        Array.from({ length: bubbleCount }, (_, i) => {
          const bx = box.x + box.w * (0.16 + 0.68 * scatter(seed, i + 7));
          const delay = scatter(seed, i + 13) * 2.4;
          const duration = 2.6 - (visual.bubbles?.rate ?? 0) * 1.4;
          return (
            <circle
              key={`bubble-${i}`}
              className="lab-scene__bubble"
              cx={bx}
              cy={box.y + box.h - 3}
              r={bubbleRadius * (0.7 + scatter(seed, i + 21) * 0.6)}
              fill="rgba(255,255,255,0.75)"
              stroke="rgba(120,150,180,0.5)"
              strokeWidth={0.35}
              style={{
                animationDelay: `${delay.toFixed(2)}s`,
                animationDuration: `${duration.toFixed(2)}s`,
                ["--lab-rise" as string]: `${Math.max(6, box.h * fill - 4).toFixed(1)}`,
              }}
            />
          );
        })}
      {visual.steam > 0.04 &&
        Array.from({ length: MAX_STEAM }, (_, i) => (
          <path
            key={`steam-${i}`}
            className="lab-scene__steam"
            d={`M${cx + (i - 1) * box.w * 0.24} ${box.y - 2} q${-box.w * 0.12} ${-box.h * 0.05} 0 ${-box.h * 0.1} q${box.w * 0.12} ${-box.h * 0.05} 0 ${-box.h * 0.1}`}
            fill="none"
            stroke="rgba(150,165,180,0.7)"
            strokeWidth={1.2}
            strokeLinecap="round"
            style={{ animationDelay: `${(i * 0.9).toFixed(2)}s` }}
          />
        ))}
    </g>
  );
}

/** Vessel temperature band → warm under-glow, only when the frame says so. */
export function heatUnderGlow(w: number, temperatureBand: string): React.ReactNode {
  if (temperatureBand !== "hot" && temperatureBand !== "warm") return null;
  return (
    <ellipse
      className="lab-scene__heat"
      cx={w / 2}
      cy={0.965}
      rx={w * 0.36}
      ry={0.028}
      fill="rgba(232, 120, 60, 0.32)"
    />
  );
}
