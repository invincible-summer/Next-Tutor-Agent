"use client";

/**
 * The twelve pack equipment kinds, drawn as layered SVG in each node's own
 * world box (real footprint proportions — no normalised-box distortion).
 * Vessel layering follows the plan: shell → cavity → fluid (LabPhenomena) →
 * particulate → glass front → highlights → annotations. Instruments draw
 * their own structure; every reading shown comes from the RenderFrame.
 * Unknown kinds fall back to a neutral outline + readable caption — never a
 * silent disappearance.
 */
import { useId } from "react";
import type { SceneNode } from "./scene-model";
import {
  LabPhenomena,
  VESSEL_PROFILES,
  cavityPath,
  cavityRect,
  heatUnderGlow,
} from "./LabPhenomena";

export interface LabEquipmentProps {
  node: SceneNode;
  selected: boolean;
  held: boolean;
  dimmed: boolean;
  /** Vessel is under an active heat command (frame/engine truth). */
  heating: boolean;
  /** Hotplate has at least one vessel heating on it. */
  hotplateActive: boolean;
  /** Deterministic phenomena seed: sessionId:objectId:revision. */
  seed: string;
}

const GLASS_STROKE = "var(--lab-glass-rim)";

function scaleWall(wall: string, w: number, h: number): string {
  return wall.replace(/(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)/g, (_m, x, y) => {
    return `${(Number(x) * w).toFixed(2)} ${(Number(y) * h).toFixed(2)}`;
  });
}

/** Highlight kind → ring colour class (safety is the only danger tone). */
function highlightRing(highlight: string | null): { stroke: string; dashed: boolean } | null {
  if (!highlight) return null;
  if (highlight.startsWith("safety")) return { stroke: "var(--lab-drop-bad)", dashed: false };
  return { stroke: "var(--lab-drop-warn)", dashed: false };
}

export function LabEquipment({
  node, selected, held, dimmed, heating, hotplateActive, seed,
}: LabEquipmentProps) {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const { width: w, height: h } = node.world;
  const opacity = dimmed ? 0.35 : 1;
  const ring = highlightRing(node.vessel?.highlight ?? null);

  return (
    <g opacity={opacity}>
      {/* two-layer contact shadow: soft spread + dark core under the base */}
      <ellipse
        cx={w / 2}
        cy={h - 1}
        rx={w * 0.46}
        ry={Math.max(4, h * 0.045)}
        fill="var(--lab-shadow-soft)"
      />
      <ellipse
        cx={w / 2}
        cy={h - 2}
        rx={w * 0.34}
        ry={Math.max(2.5, h * 0.028)}
        fill="var(--lab-shadow)"
      />
      {node.known && node.vessel
        ? vesselBody(node, uid, heating, seed)
        : node.known
          ? instrumentBody(node, hotplateActive)
          : unknownBody(node)}
      {ring && (
        <rect
          className="lab-scene__highlight"
          x={-4}
          y={-4}
          width={w + 8}
          height={h + 8}
          rx={10}
          fill="none"
          stroke={ring.stroke}
          strokeWidth={2.2}
          vectorEffect="non-scaling-stroke"
        />
      )}
      {selected && (
        <rect
          x={-7}
          y={-7}
          width={w + 14}
          height={h + 14}
          rx={12}
          fill="none"
          stroke="var(--color-accent, #2f6f6a)"
          strokeWidth={2.2}
          strokeDasharray="5 6"
          vectorEffect="non-scaling-stroke"
        />
      )}
      {held && (
        <rect
          x={-5}
          y={-5}
          width={w + 10}
          height={h + 10}
          rx={10}
          fill="rgba(96, 140, 180, 0.10)"
          stroke="var(--color-accent, #2f6f6a)"
          strokeWidth={1.8}
          vectorEffect="non-scaling-stroke"
        />
      )}
    </g>
  );
}

// ---------------------------------------------------------------------------
// Vessels (5 kinds)
// ---------------------------------------------------------------------------

function vesselBody(node: SceneNode, uid: string, heating: boolean, seed: string) {
  const profile = VESSEL_PROFILES[node.kind];
  const { width: w, height: h } = node.world;
  if (!profile || !node.vessel) return null;
  const box = cavityRect(profile, w, h);
  const clipId = `lab-cavity-${uid}`;
  const wallPath = scaleWall(profile.wall, w, h);

  return (
    <g>
      {heatUnderGlow(w, node.vessel.temperatureBand)}
      <defs>
        <clipPath id={clipId}>
          <path d={cavityPath(profile, w, h)} />
        </clipPath>
        <linearGradient id={`lab-shine-${uid}`} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="var(--lab-glass-shine)" stopOpacity="0.55" />
          <stop offset="28%" stopColor="var(--lab-glass-shine)" stopOpacity="0.10" />
          <stop offset="72%" stopColor="var(--lab-glass-shine)" stopOpacity="0" />
          <stop offset="100%" stopColor="var(--lab-glass-shine)" stopOpacity="0.22" />
        </linearGradient>
        {/* liquid body reads deeper at the bottom of the cavity */}
        <linearGradient id={`lab-liquid-depth-${uid}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ffffff" stopOpacity="0.10" />
          <stop offset="45%" stopColor="#000000" stopOpacity="0" />
          <stop offset="100%" stopColor="#000000" stopOpacity="0.14" />
        </linearGradient>
      </defs>
      {/* shell back — faint side shading gives the glass roundness */}
      <path d={cavityPath(profile, w, h)} fill="var(--lab-glass-body)" />
      {/* fluid + particulate + bubbles, strictly inside the real cavity */}
      <g clipPath={`url(#${clipId})`}>
        <LabPhenomena kind={node.kind} w={w} h={h} visual={node.vessel} seed={seed} />
        {node.vessel.fillRatio > 0.01 && (
          <rect x={box.x} y={box.y} width={box.w} height={box.h}
            fill={`url(#lab-liquid-depth-${uid})`} />
        )}
      </g>
      {/* graduations from the pack def (readable at focus zoom) */}
      {node.vessel.graduations.length > 0 && (
        <g stroke={GLASS_STROKE} strokeWidth={0.9} opacity={0.6}>
          {node.vessel.graduations.map((fraction, index) => {
            const y = box.y + box.h * (1 - fraction);
            const long = index % 2 === 0;
            return (
              <line
                key={fraction}
                x1={box.x + 1}
                y1={y}
                x2={box.x + (long ? Math.min(12, box.w * 0.5) : 7)}
                y2={y}
              />
            );
          })}
        </g>
      )}
      {/* glass front + wall: outer thickness pass then crisp wall line */}
      <g clipPath={`url(#${clipId})`}>
        <rect x={box.x} y={box.y} width={box.w} height={box.h} fill={`url(#lab-shine-${uid})`} />
        {/* crisp specular streak on the left inner wall */}
        <rect
          x={box.x + box.w * 0.1}
          y={box.y + box.h * 0.12}
          width={Math.max(1.6, box.w * 0.07)}
          height={box.h * 0.62}
          rx={Math.max(1, box.w * 0.035)}
          fill="var(--lab-glass-shine)"
          opacity={0.4}
        />
      </g>
      <path
        d={wallPath}
        fill="none"
        stroke={GLASS_STROKE}
        strokeWidth={3}
        strokeOpacity={0.16}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d={wallPath}
        fill="none"
        stroke={GLASS_STROKE}
        strokeWidth={1.6}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {profile.rim && (
        <g>
          <ellipse
            cx={profile.rim.cx * w}
            cy={profile.rim.cy * h}
            rx={profile.rim.rx * w}
            ry={profile.rim.ry * h}
            fill="none"
            stroke={GLASS_STROKE}
            strokeWidth={1.5}
            opacity={0.9}
          />
          <ellipse
            cx={profile.rim.cx * w}
            cy={profile.rim.cy * h}
            rx={profile.rim.rx * w * 0.82}
            ry={profile.rim.ry * h * 0.72}
            fill="none"
            stroke={GLASS_STROKE}
            strokeWidth={0.9}
            opacity={0.4}
          />
        </g>
      )}
      {profile.spout && (
        <path
          d={`M${0.13 * w} ${0.08 * h} q${-0.05 * w} ${-0.01 * h} ${-0.06 * w} ${0.045 * h}`}
          fill="none"
          stroke={GLASS_STROKE}
          strokeWidth={1.6}
          strokeLinecap="round"
        />
      )}
      {kindExtras(node, box)}
      {heating && (
        <ellipse
          className="lab-scene__heat"
          cx={w / 2}
          cy={h - 4}
          rx={w * 0.3}
          ry={4}
          fill="rgba(232, 120, 60, 0.35)"
        />
      )}
    </g>
  );
}

/** Per-kind annotations on top of the shared vessel body. */
function kindExtras(node: SceneNode, box: { x: number; y: number; w: number; h: number }) {
  const { width: w, height: h } = node.world;
  switch (node.kind) {
    case "reagent_bottle": {
      // cap + label band; remaining volume is read from the liquid itself.
      return (
        <g>
          <rect x={0.36 * w} y={0.0} width={0.28 * w} height={0.055 * h} rx={3}
            fill="var(--lab-steel-dark)" />
          <rect x={0.385 * w} y={0.055 * h} width={0.23 * w} height={0.02 * h} fill="var(--lab-steel)" />
          <rect x={box.x + 1} y={0.52 * h} width={box.w - 2} height={0.24 * h} rx={3}
            fill="rgba(250, 248, 242, 0.75)" stroke="var(--lab-tile-line)" />
          <line x1={box.x + 4} y1={0.585 * h} x2={box.x + box.w - 4} y2={0.585 * h}
            stroke="rgba(93,84,70,0.35)" strokeWidth={1.4} />
          <line x1={box.x + 4} y1={0.625 * h} x2={box.x + box.w - 10} y2={0.625 * h}
            stroke="rgba(93,84,70,0.28)" strokeWidth={1.2} />
          <line x1={box.x + 4} y1={0.665 * h} x2={box.x + box.w - 6} y2={0.665 * h}
            stroke="rgba(93,84,70,0.22)" strokeWidth={1.1} />
        </g>
      );
    }
    case "graduated_cylinder": {
      // foot already in the wall path; add a base plate shadow line.
      return (
        <line x1={0.2 * w} y1={0.972 * h} x2={0.8 * w} y2={0.972 * h}
          stroke={GLASS_STROKE} strokeWidth={1.4} opacity={0.7} />
      );
    }
    case "gas_cylinder": {
      // collection tube collar + water-seal line at the top opening.
      return (
        <g>
          <rect x={0.3 * w} y={0.02 * h} width={0.4 * w} height={0.045 * h} rx={2.5}
            fill="var(--lab-steel)" />
          <line x1={0.27 * w} y1={0.14 * h} x2={0.73 * w} y2={0.14 * h}
            stroke="rgba(120,160,210,0.4)" strokeWidth={1.2} strokeDasharray="4 3" />
        </g>
      );
    }
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Instruments / devices (7 kinds)
// ---------------------------------------------------------------------------

function instrumentBody(node: SceneNode, hotplateActive: boolean) {
  const { width: w, height: h } = node.world;
  const stroke = GLASS_STROKE;
  const common = {
    fill: "none" as const,
    stroke,
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  switch (node.kind) {
    case "dropper": {
      const loadFraction = node.capacityUL > 0 ? Math.min(1, node.loadUL / node.capacityUL) : 0;
      return (
        <g {...common}>
          {/* rubber bulb */}
          <path
            d={`M${0.28 * w} ${0.02 * h} Q${0.78 * w} ${0.02 * h} ${0.74 * w} ${0.14 * h} Q${0.7 * w} ${0.24 * h} ${0.5 * w} ${0.25 * h} Q${0.3 * w} ${0.24 * h} ${0.26 * w} ${0.14 * h} Z`}
            fill="rgba(160, 120, 100, 0.35)"
          />
          {/* taper + tube */}
          <path d={`M${0.38 * w} ${0.25 * h} L${0.34 * w} ${0.34 * h} L${0.4 * w} ${0.36 * h} L${0.4 * w} ${0.86 * h} L${0.6 * w} ${0.86 * h} L${0.6 * w} ${0.36 * h} L${0.66 * w} ${0.34 * h} L${0.62 * w} ${0.25 * h}`} />
          <path d={`M${0.4 * w} ${0.86 * h} L${0.46 * w} ${0.985 * h} L${0.54 * w} ${0.985 * h} L${0.6 * w} ${0.86 * h}`} />
          {loadFraction > 0 && (
            <rect
              x={0.43 * w}
              y={(0.86 - 0.5 * loadFraction) * h}
              width={0.14 * w}
              height={0.5 * loadFraction * h}
              fill="var(--lab-liquid-default)"
              opacity={0.65}
              stroke="none"
            />
          )}
        </g>
      );
    }
    case "pipette": {
      const loadFraction = node.capacityUL > 0 ? Math.min(1, node.loadUL / node.capacityUL) : 0;
      return (
        <g {...common}>
          <ellipse cx={0.5 * w} cy={0.035 * h} rx={0.3 * w} ry={0.032 * h} fill="var(--lab-steel-dark)" />
          <path d={`M${0.36 * w} ${0.07 * h} L${0.64 * w} ${0.07 * h} L${0.52 * w} ${0.13 * h} L${0.52 * w} ${0.74 * h} L${0.5 * w} ${0.985 * h} L${0.48 * w} ${0.74 * h} L${0.48 * w} ${0.13 * h} Z`} fill="var(--lab-glass-body)" />
          {[0.26, 0.4, 0.54, 0.68].map((fy) => (
            <line key={fy} x1={0.52 * w} y1={fy * h} x2={0.62 * w} y2={fy * h} strokeWidth={1} opacity={0.55} />
          ))}
          {loadFraction > 0 && (
            <path
              d={`M${0.49 * w} ${0.78 * h} L${0.49 * w} ${(0.78 - 0.6 * loadFraction) * h} L${0.51 * w} ${(0.78 - 0.6 * loadFraction) * h} L${0.51 * w} ${0.78 * h} L${0.5 * w} ${0.9 * h} Z`}
              fill="var(--lab-liquid-default)"
              opacity={0.7}
              stroke="none"
            />
          )}
        </g>
      );
    }
    case "thermometer": {
      return (
        <g {...common}>
          <path
            d={`M${0.36 * w} ${0.02 * h} L${0.36 * w} ${0.78 * h} Q${0.36 * w} ${0.86 * h} ${0.5 * w} ${0.9 * h} Q${0.64 * w} ${0.86 * h} ${0.64 * w} ${0.78 * h} L${0.64 * w} ${0.02 * h}`}
            fill="var(--lab-glass-body)"
          />
          <circle cx={0.5 * w} cy={0.86 * h} r={0.16 * w} fill="rgba(214, 89, 67, 0.6)" stroke="none" />
          <rect x={0.44 * w} y={0.3 * h} width={0.12 * w} height={0.56 * h} rx={0.05 * w}
            fill="rgba(214, 89, 67, 0.5)" stroke="none" />
          {[0.18, 0.32, 0.46, 0.6].map((fy) => (
            <line key={fy} x1={0.64 * w} y1={fy * h} x2={0.78 * w} y2={fy * h} strokeWidth={1} opacity={0.55} />
          ))}
        </g>
      );
    }
    case "ph_probe": {
      const reading = node.instrument?.reading ?? "";
      return (
        <g {...common}>
          <rect x={0.08 * w} y={0.02 * h} width={0.84 * w} height={0.26 * h} rx={5}
            fill="var(--lab-steel)" stroke="var(--lab-steel-dark)" />
          <rect x={0.16 * w} y={0.06 * h} width={0.68 * w} height={0.1 * h} rx={2}
            fill={reading ? "#20302a" : "#2a2f33"} stroke="none" />
          {reading && (
            <text x={0.5 * w} y={0.145 * h} textAnchor="middle" fontSize={Math.max(7, 0.16 * w)}
              fill="#8fe3c0" className="tnum" style={{ fontVariantNumeric: "tabular-nums" }}>
              {reading}
            </text>
          )}
          <circle cx={0.5 * w} cy={0.235 * h} r={0.07 * w} fill={reading ? "#7fc79b" : "#9aa3ab"} stroke="none" />
          <path d={`M${0.5 * w} ${0.28 * h} L${0.5 * w} ${0.6 * h}`} />
          <path
            d={`M${0.32 * w} ${0.6 * h} L${0.32 * w} ${0.9 * h} Q${0.32 * w} ${0.985 * h} ${0.5 * w} ${0.985 * h} Q${0.68 * w} ${0.985 * h} ${0.68 * w} ${0.9 * h} L${0.68 * w} ${0.6 * h}`}
            fill="var(--lab-glass-body)"
          />
          <circle cx={0.5 * w} cy={0.92 * h} r={0.09 * w} fill="rgba(120,160,210,0.5)" stroke="none" />
        </g>
      );
    }
    case "stir_rod": {
      return (
        <g>
          <line x1={0.5 * w} y1={0.02 * h} x2={0.5 * w} y2={0.96 * h}
            stroke={stroke} strokeWidth={0.36 * w} strokeLinecap="round" opacity={0.4} />
          <line x1={0.5 * w} y1={0.02 * h} x2={0.5 * w} y2={0.96 * h}
            stroke={stroke} strokeWidth={0.14 * w} strokeLinecap="round" />
          <line x1={0.4 * w} y1={0.08 * h} x2={0.4 * w} y2={0.9 * h}
            stroke="var(--lab-glass-shine)" strokeWidth={0.06 * w} strokeLinecap="round" />
        </g>
      );
    }
    case "delivery_tube": {
      // U-tube between the def's two ports; connection curves are drawn by
      // the scene from real port anchors — this is the resting shape.
      return (
        <g {...common}>
          <path
            d={`M${0.08 * w} ${0.12 * h} L${0.08 * w} ${0.5 * h} Q${0.08 * w} ${0.92 * h} ${0.5 * w} ${0.92 * h} Q${0.92 * w} ${0.92 * h} ${0.92 * w} ${0.5 * h} L${0.92 * w} ${0.12 * h}`}
            strokeWidth={Math.max(5, 0.16 * h)}
            opacity={0.3}
          />
          <path
            d={`M${0.08 * w} ${0.12 * h} L${0.08 * w} ${0.5 * h} Q${0.08 * w} ${0.92 * h} ${0.5 * w} ${0.92 * h} Q${0.92 * w} ${0.92 * h} ${0.92 * w} ${0.5 * h} L${0.92 * w} ${0.12 * h}`}
          />
          <line x1={0.04 * w} y1={0.12 * h} x2={0.12 * w} y2={0.12 * h} />
          <line x1={0.88 * w} y1={0.12 * h} x2={0.96 * w} y2={0.12 * h} />
        </g>
      );
    }
    case "hotplate": {
      const active = hotplateActive;
      return (
        <g>
          {/* ceramic heating surface */}
          <rect x={0.04 * w} y={0.12 * h} width={0.92 * w} height={0.3 * h} rx={4}
            fill={active ? "#5a4a44" : "#3f4a52"} stroke="var(--lab-steel-dark)" strokeWidth={1.4} />
          {active && (
            <ellipse className="lab-scene__heat" cx={0.5 * w} cy={0.27 * h} rx={0.36 * w} ry={0.1 * h}
              fill="rgba(232, 120, 60, 0.55)" />
          )}
          {/* metal body */}
          <rect x={0.08 * w} y={0.42 * h} width={0.84 * w} height={0.52 * h} rx={6}
            fill="var(--lab-steel)" stroke="var(--lab-steel-dark)" strokeWidth={1.6} />
          {/* knob */}
          <circle cx={0.24 * w} cy={0.68 * h} r={0.11 * h} fill="var(--lab-steel-dark)" stroke="none" />
          <line x1={0.24 * w} y1={0.68 * h} x2={0.24 * w} y2={0.585 * h} stroke="#e8e4da" strokeWidth={1.6} />
          {/* indicator */}
          <circle cx={0.78 * w} cy={0.68 * h} r={0.055 * h}
            fill={active ? "rgba(224, 122, 60, 0.95)" : "#6d757d"} stroke="none" />
          {active && (
            <g stroke="rgba(232, 120, 60, 0.85)" strokeWidth={1.5} fill="none" strokeLinecap="round">
              <path d={`M${0.36 * w} ${0.1 * h} q${0.03 * w} ${-0.1 * h} 0 ${-0.16 * h}`} />
              <path d={`M${0.5 * w} ${0.08 * h} q${0.03 * w} ${-0.1 * h} 0 ${-0.16 * h}`} />
              <path d={`M${0.64 * w} ${0.1 * h} q${0.03 * w} ${-0.1 * h} 0 ${-0.16 * h}`} />
            </g>
          )}
        </g>
      );
    }
    default:
      return unknownBody(node);
  }
}

/** Neutral fallback for kinds the art dispatch does not know. */
function unknownBody(node: SceneNode) {
  const { width: w, height: h } = node.world;
  return (
    <g>
      <rect x={0.12 * w} y={0.12 * h} width={0.76 * w} height={0.76 * h} rx={8}
        fill="var(--lab-glass-body)" stroke="var(--lab-steel-dark)" strokeWidth={1.8}
        strokeDasharray="7 5" />
      <line x1={0.2 * w} y1={0.5 * h} x2={0.8 * w} y2={0.5 * h}
        stroke="var(--lab-steel-dark)" strokeWidth={1.4} />
      <text x={0.5 * w} y={0.44 * h} textAnchor="middle" fontSize={Math.min(13, w * 0.16)}
        fill="var(--lab-steel-dark)">
        ?
      </text>
    </g>
  );
}
