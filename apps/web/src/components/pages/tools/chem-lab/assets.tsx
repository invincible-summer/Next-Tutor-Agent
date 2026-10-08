"use client";

/**
 * Hand-drawn SVG outlines for the twelve pack equipment kinds. Every asset is
 * drawn in a normalised 100×100 art box and scaled into its slot placement;
 * strokes use `vector-effect="non-scaling-stroke"` so line weight survives
 * non-uniform scaling. Liquid, precipitate, bubbles, steam and turbidity are
 * rendered from RenderFrame-derived props only — an animation never invents a
 * phenomenon that is not present in the frame (reduced-motion turns motion
 * off and keeps the static state).
 */
import { useId } from "react";
import type { VesselVisual } from "./chem-lab-renderer";

const GLASS_STROKE = "currentColor";
const GLASS_FILL = "rgba(148, 178, 210, 0.10)";
const GLASS_FILL_STRONG = "rgba(148, 178, 210, 0.18)";

interface ArtBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface VesselArtDef {
  outline: string;
  inner: string;
  innerBox: ArtBox;
}

/** Vessel silhouettes + inner cavities in the 100×100 art box. */
const VESSEL_ART: Record<string, VesselArtDef> = {
  "beaker.small": {
    outline:
      "M22 12 L22 84 Q22 92 30 92 L70 92 Q78 92 78 84 L78 12 M22 12 L66 12 L78 6",
    inner: "M26 18 L26 82 Q26 88 32 88 L68 88 Q74 88 74 82 L74 18 Z",
    innerBox: { x: 26, y: 18, w: 48, h: 70 },
  },
  conical_flask: {
    outline:
      "M42 8 L42 34 L18 84 Q14 92 24 92 L76 92 Q86 92 82 84 L58 34 L58 8 M42 8 L58 8",
    inner: "M45 13 L45 37 L24 84 Q22 88 27 88 L73 88 Q78 88 76 84 L55 37 L55 13 Z",
    innerBox: { x: 24, y: 13, w: 52, h: 75 },
  },
  gas_cylinder: {
    outline: "M32 92 L32 18 Q32 8 50 8 Q68 8 68 18 L68 92 M28 92 L72 92",
    inner: "M36 88 L36 20 Q36 13 50 13 Q64 13 64 20 L64 88 Z",
    innerBox: { x: 36, y: 13, w: 28, h: 75 },
  },
  graduated_cylinder: {
    outline:
      "M40 8 L40 84 Q40 88 44 88 L56 88 Q60 88 60 84 L60 8 M40 8 L60 8 M34 96 L66 96 M40 88 L34 96 M60 88 L66 96",
    inner: "M43 13 L43 82 Q43 84 45 84 L55 84 Q57 84 57 82 L57 13 Z",
    innerBox: { x: 43, y: 13, w: 14, h: 71 },
  },
  reagent_bottle: {
    outline:
      "M40 16 L40 28 Q24 34 24 48 L24 84 Q24 92 32 92 L68 92 Q76 92 76 84 L76 48 Q76 34 60 28 L60 16 M40 16 L60 16 M38 8 L62 8 L62 16 L38 16 Z",
    inner: "M43 21 L43 32 Q29 38 29 50 L29 82 Q29 88 35 88 L65 88 Q71 88 71 82 L71 50 Q71 38 57 32 L57 21 Z",
    innerBox: { x: 29, y: 21, w: 42, h: 67 },
  },
};

export function isVesselKind(kind: string): boolean {
  return kind in VESSEL_ART;
}

/** Deterministic scatter (stable per vessel + index) for bubble positions. */
function scatter(seed: string, index: number): number {
  let hash = 2166136261;
  const text = `${seed}:${index}`;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return ((hash >>> 0) % 1000) / 1000;
}

const MAX_BUBBLES = 12;
const MAX_STEAM = 3;

/** Liquid body + in-liquid phenomena, clipped to the vessel cavity. */
function VesselContents({
  kind,
  visual,
  clipId,
}: {
  kind: string;
  visual: VesselVisual;
  clipId: string;
}) {
  const art = VESSEL_ART[kind];
  if (!art) return null;
  const { innerBox: box } = art;
  const fill = Math.min(1, Math.max(0, visual.fillRatio));
  const liquidHeight = box.h * fill;
  const surfaceY = box.y + box.h - liquidHeight;
  const liquidColor = visual.liquidColor ?? "#b9d4e8";
  const liquidOpacity = 0.28 + visual.opacity * 0.62;
  const bubbleCount = visual.bubbles ? Math.min(MAX_BUBBLES, 2 + Math.round(visual.bubbles.rate * 10)) : 0;
  const bubbleRadius = 1.1 + (visual.bubbles?.size ?? 0) * 2.2;
  const precipitateHeight = Math.min(box.h * 0.3, (visual.precipitate?.amount ?? 0) * box.h * 0.22);
  const suspension = visual.precipitate && visual.precipitate.texture !== "layer";
  const seed = visual.vesselId;

  return (
    <g clipPath={`url(#${clipId})`}>
      {liquidHeight > 0.5 && (
        <rect
          x={box.x}
          y={surfaceY}
          width={box.w}
          height={liquidHeight}
          fill={liquidColor}
          opacity={liquidOpacity}
        />
      )}
      {liquidHeight > 0.5 && (
        <rect
          x={box.x}
          y={surfaceY - 0.6}
          width={box.w}
          height={1.4}
          fill={liquidColor}
          opacity={Math.min(1, liquidOpacity + 0.25)}
          rx={0.7}
        />
      )}
      {visual.turbidity > 0.03 && liquidHeight > 0.5 && (
        <rect
          x={box.x}
          y={surfaceY}
          width={box.w}
          height={liquidHeight}
          fill="#e8e4da"
          opacity={visual.turbidity * 0.55}
        />
      )}
      {visual.precipitate && precipitateHeight > 0.4 && (
        <rect
          x={box.x}
          y={box.y + box.h - precipitateHeight}
          width={box.w}
          height={precipitateHeight}
          fill={visual.precipitate.color ?? "#d6d3cb"}
          opacity={0.85}
          rx={1}
        />
      )}
      {suspension &&
        Array.from({ length: 8 }, (_, i) => (
          <circle
            key={`speck-${i}`}
            cx={box.x + box.w * (0.15 + 0.7 * scatter(seed, i))}
            cy={surfaceY + liquidHeight * (0.15 + 0.7 * scatter(seed, i + 40))}
            r={0.9 + scatter(seed, i + 80) * 0.9}
            fill={visual.precipitate?.color ?? "#d6d3cb"}
            opacity={0.65}
          />
        ))}
      {bubbleCount > 0 &&
        Array.from({ length: bubbleCount }, (_, i) => {
          const cx = box.x + box.w * (0.18 + 0.64 * scatter(seed, i + 7));
          const delay = scatter(seed, i + 13) * 2.4;
          const duration = 2.6 - (visual.bubbles?.rate ?? 0) * 1.4;
          return (
            <circle
              key={`bubble-${i}`}
              className="chem-lab-bubble"
              cx={cx}
              cy={box.y + box.h - 3}
              r={bubbleRadius * (0.7 + scatter(seed, i + 21) * 0.6)}
              fill="rgba(255,255,255,0.75)"
              stroke="rgba(120,150,180,0.5)"
              strokeWidth={0.3}
              style={{
                animationDelay: `${delay.toFixed(2)}s`,
                animationDuration: `${duration.toFixed(2)}s`,
                ["--rise" as string]: `${Math.max(6, liquidHeight - 4).toFixed(1)}`,
              }}
            />
          );
        })}
    </g>
  );
}

function SteamWisps({ visual, kind }: { visual: VesselVisual; kind: string }) {
  const art = VESSEL_ART[kind];
  if (!art || visual.steam <= 0.04) return null;
  const { innerBox: box } = art;
  const surfaceY = box.y + box.h - box.h * Math.min(1, Math.max(0, visual.fillRatio));
  const top = Math.min(surfaceY, box.y + 4);
  return (
    <g opacity={Math.min(0.8, visual.steam)}>
      {Array.from({ length: MAX_STEAM }, (_, i) => {
        const cx = box.x + box.w * (0.3 + 0.4 * scatter(visual.vesselId, i + 3));
        return (
          <path
            key={`steam-${i}`}
            className="chem-lab-steam"
            d={`M${cx} ${top - 2} q-2.5 -4 0 -8 q2.5 -4 0 -8`}
            fill="none"
            stroke="rgba(150,165,180,0.7)"
            strokeWidth={1.1}
            strokeLinecap="round"
            style={{ animationDelay: `${(i * 0.9).toFixed(2)}s` }}
          />
        );
      })}
    </g>
  );
}

function GraduationTicks({ kind, visual }: { kind: string; visual: VesselVisual | null }) {
  const art = VESSEL_ART[kind];
  if (!art || !visual?.graduations.length) return null;
  const { innerBox: box } = art;
  return (
    <g stroke={GLASS_STROKE} strokeWidth={0.7} opacity={0.55}>
      {visual.graduations.map((fraction) => {
        const y = box.y + box.h - box.h * fraction;
        return <line key={fraction} x1={box.x} y1={y} x2={box.x + Math.min(9, box.w * 0.45)} y2={y} />;
      })}
    </g>
  );
}

/** Vessel: glass silhouette + contents. Rendered inside a scaled art group. */
export function VesselArt({
  kind,
  visual,
  heating = false,
}: {
  kind: string;
  visual: VesselVisual | null;
  heating?: boolean;
}) {
  const clipId = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const art = VESSEL_ART[kind];
  if (!art) return null;
  const highlight = visual?.highlight ?? null;
  return (
    <g>
      {heating && (
        <ellipse
          cx={50}
          cy={94}
          rx={30}
          ry={4}
          fill="rgba(232, 120, 60, 0.30)"
          className="chem-lab-heat-glow"
        />
      )}
      <defs>
        <clipPath id={clipId}>
          <path d={art.inner} />
        </clipPath>
      </defs>
      <path d={art.inner} fill={GLASS_FILL} stroke="none" />
      {visual && <VesselContents kind={kind} visual={visual} clipId={clipId} />}
      <GraduationTicks kind={kind} visual={visual} />
      <path
        d={art.outline}
        fill="none"
        stroke={GLASS_STROKE}
        strokeWidth={1.6}
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={0.9}
      />
      {visual && <SteamWisps visual={visual} kind={kind} />}
      {highlight && (
        <path
          d={art.outline}
          fill="none"
          stroke={highlight.startsWith("safety") ? "#d9553f" : "#e0a23c"}
          strokeWidth={2.4}
          strokeLinecap="round"
          strokeLinejoin="round"
          opacity={0.85}
          className="chem-lab-highlight"
        />
      )}
    </g>
  );
}

/** Instrument / device line art in the same 100×100 art box. */
export function InstrumentArt({ kind, active = false }: { kind: string; active?: boolean }) {
  const stroke = GLASS_STROKE;
  const common = {
    fill: "none" as const,
    stroke,
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    opacity: 0.9,
  };
  switch (kind) {
    case "dropper":
      return (
        <g {...common}>
          <path d="M50 6 Q62 6 62 18 Q62 30 50 30 Q38 30 38 18 Q38 6 50 6 Z" fill={GLASS_FILL_STRONG} />
          <path d="M45 30 L45 56 L47 56 L47 88" />
          <path d="M55 30 L55 56 L53 56 L53 88" />
          <path d="M47 88 L50 96 L53 88" />
          {active && <circle cx={50} cy={62} r={3.4} fill="rgba(120,160,210,0.5)" stroke="none" />}
        </g>
      );
    case "pipette":
      return (
        <g {...common}>
          <path d="M46 6 L54 6 L54 12 L51 12 L51 78 L50 92 L49 78 L49 12 L46 12 Z" fill={GLASS_FILL} />
          {[24, 38, 52, 66].map((y) => (
            <line key={y} x1={49} y1={y} x2={54} y2={y} strokeWidth={1} opacity={0.6} />
          ))}
          {active && <rect x={49.5} y={40} width={1.6} height={38} fill="rgba(120,160,210,0.5)" stroke="none" />}
        </g>
      );
    case "thermometer":
      return (
        <g {...common}>
          <path d="M47 8 L53 8 L53 74 Q58 78 58 84 Q58 92 50 92 Q42 92 42 84 Q42 78 47 74 Z" fill={GLASS_FILL} />
          <circle cx={50} cy={84} r={4.6} fill="rgba(214, 89, 67, 0.65)" stroke="none" />
          <rect x={48.6} y={30} width={2.8} height={50} fill="rgba(214, 89, 67, 0.55)" stroke="none" />
          {[18, 30, 42, 54, 66].map((y) => (
            <line key={y} x1={53} y1={y} x2={58} y2={y} strokeWidth={1} opacity={0.6} />
          ))}
        </g>
      );
    case "ph_probe":
      return (
        <g {...common}>
          <rect x={38} y={6} width={24} height={34} rx={4} fill={GLASS_FILL_STRONG} />
          <rect x={42} y={11} width={16} height={9} rx={1.5} fill="rgba(120,160,210,0.35)" stroke="none" />
          <circle cx={50} cy={30} r={3} />
          <path d="M50 40 L50 58" />
          <path d="M46 58 L46 90 Q46 94 50 94 Q54 94 54 90 L54 58" fill={GLASS_FILL} />
          <circle cx={50} cy={88} r={2.2} fill="rgba(120,160,210,0.55)" stroke="none" />
        </g>
      );
    case "stir_rod":
      return (
        <g {...common}>
          <path d="M50 4 L50 92" strokeWidth={3.2} opacity={0.45} />
          <path d="M50 4 L50 92" />
          <circle cx={50} cy={94} r={2.4} fill={GLASS_FILL_STRONG} />
        </g>
      );
    case "delivery_tube":
      return (
        <g {...common}>
          <path d="M6 42 L6 62 Q6 74 18 74 L58 74 Q70 74 70 62 L70 40" strokeWidth={5} opacity={0.28} />
          <path d="M6 42 L6 62 Q6 74 18 74 L58 74 Q70 74 70 62 L70 40" />
          <path d="M70 40 L70 30 M6 42 L6 32" opacity={0.6} />
          {active && (
            <circle className="chem-lab-gas-dot" cx={38} cy={74} r={2.4} fill="rgba(255,255,255,0.8)" stroke="none" />
          )}
        </g>
      );
    case "hotplate":
      return (
        <g {...common}>
          <rect x={8} y={42} width={84} height={40} rx={6} fill={GLASS_FILL_STRONG} />
          <line x1={14} y1={42} x2={86} y2={42} strokeWidth={2.4} />
          <circle cx={26} cy={66} r={6} />
          <path d="M26 66 L26 61" strokeWidth={1.4} />
          <circle cx={80} cy={64} r={2.6} fill={active ? "rgba(224, 122, 60, 0.9)" : "none"} />
          {active && (
            <g stroke="rgba(232, 120, 60, 0.85)" strokeWidth={1.6}>
              <path d="M40 34 q3 -5 0 -9" />
              <path d="M50 34 q3 -5 0 -9" />
              <path d="M60 34 q3 -5 0 -9" />
            </g>
          )}
        </g>
      );
    default:
      return (
        <g {...common}>
          <rect x={30} y={30} width={40} height={40} rx={6} fill={GLASS_FILL} />
        </g>
      );
  }
}

/** CSS for the few continuous animations; motion-reduce turns them static. */
export const CHEM_LAB_STAGE_CSS = `
.chem-lab-bubble { animation: chem-lab-bubble-rise 2.2s linear infinite; }
@keyframes chem-lab-bubble-rise {
  0% { transform: translateY(0); opacity: 0; }
  12% { opacity: 0.9; }
  100% { transform: translateY(calc(-1px * var(--rise, 40))); opacity: 0; }
}
.chem-lab-steam { animation: chem-lab-steam-wisp 2.8s ease-in-out infinite; }
@keyframes chem-lab-steam-wisp {
  0%, 100% { opacity: 0.15; transform: translateY(0); }
  50% { opacity: 0.75; transform: translateY(-2px); }
}
.chem-lab-heat-glow { animation: chem-lab-heat-pulse 2.4s ease-in-out infinite; }
@keyframes chem-lab-heat-pulse { 0%, 100% { opacity: 0.45; } 50% { opacity: 0.9; } }
.chem-lab-highlight { animation: chem-lab-highlight-pulse 1.8s ease-in-out infinite; }
@keyframes chem-lab-highlight-pulse { 0%, 100% { opacity: 0.35; } 50% { opacity: 0.9; } }
.chem-lab-gas-dot { animation: chem-lab-gas-flow 1.6s linear infinite; }
@keyframes chem-lab-gas-flow { 0% { transform: translateX(-24px); opacity: 0; } 30% { opacity: 0.9; } 100% { transform: translateX(24px); opacity: 0; } }
@media (prefers-reduced-motion: reduce) {
  .chem-lab-bubble, .chem-lab-steam, .chem-lab-heat-glow, .chem-lab-highlight, .chem-lab-gas-dot {
    animation: none !important;
  }
  .chem-lab-bubble { opacity: 0.7; }
  .chem-lab-steam { opacity: 0.4; }
}
`;
