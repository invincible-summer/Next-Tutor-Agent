"use client";

/**
 * Original lab environment: wall + windows + safety sign + tile wainscot,
 * a metal shelf/rack behind the rear slot band, and the wide bench the front
 * slots work on. Purely decorative — everything is `pointer-events:none`
 * and `aria-hidden`; interactive objects always sit above it and are visibly
 * different (stroke, contrast, hit outlines).
 *
 * Proportions are derived from the experiment's own slot bands, never from
 * per-experiment hard-coded layouts.
 */
export interface EnvironmentFrame {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface LabEnvironmentProps {
  frame: EnvironmentFrame;
  rearBand: { top: number; bottom: number } | null;
  frontBand: { top: number; bottom: number } | null;
  bounds: { width: number; height: number };
}

export function LabEnvironment({ frame, rearBand, frontBand, bounds }: LabEnvironmentProps) {
  const rear = rearBand ?? { top: 90, bottom: 280 };
  const front = frontBand ?? { top: 400, bottom: bounds.height - 24 };
  const benchTopY = rear.bottom + 14;
  const benchBottomY = Math.max(front.bottom + 26, benchTopY + 150);
  const wallBottomY = benchTopY;
  const right = frame.x + frame.width;

  // Windows stay in the upper third of the wall zone.
  const windowTop = frame.y + 26;
  const windowHeight = Math.min((wallBottomY - frame.y) * 0.34, 150);
  const windowGap = frame.x + 40;
  const windowWidth = Math.min(220, (right - windowGap * 2 - 60) / 2);
  const window2X = right - windowGap - windowWidth;

  return (
    <g aria-hidden="true" pointerEvents="none">
      <defs>
        <linearGradient id="lab-env-window" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--lab-window)" />
          <stop offset="100%" stopColor="var(--lab-window-bar)" stopOpacity="0.55" />
        </linearGradient>
        <linearGradient id="lab-env-bench" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--lab-bench-top-back)" />
          <stop offset="18%" stopColor="var(--lab-bench-top)" />
          <stop offset="100%" stopColor="var(--lab-bench-top)" />
        </linearGradient>
        <linearGradient id="lab-env-shadow" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--lab-shadow)" stopOpacity="0.9" />
          <stop offset="100%" stopColor="var(--lab-shadow)" stopOpacity="0" />
        </linearGradient>
        <pattern id="lab-env-tiles" width="46" height="24" patternUnits="userSpaceOnUse">
          <path d="M0 24h46M46 0v24" fill="none" stroke="var(--lab-tile-line)" strokeWidth="1" />
        </pattern>
      </defs>

      {/* Back wall */}
      <rect x={frame.x} y={frame.y} width={frame.width} height={wallBottomY - frame.y}
        fill="var(--lab-wall)" />
      <rect x={frame.x} y={wallBottomY - 140} width={frame.width} height="140"
        fill="url(#lab-env-tiles)" />
      <rect x={frame.x} y={wallBottomY - 142} width={frame.width} height="3"
        fill="var(--lab-shelf-edge)" opacity="0.55" />

      {/* Windows (upper third only) */}
      {[windowGap, window2X].map((wx) => (
        <g key={wx}>
          <rect x={wx} y={windowTop} width={windowWidth} height={windowHeight} rx="8"
            fill="url(#lab-env-window)" stroke="var(--lab-window-bar)" strokeWidth="3" />
          <line x1={wx + windowWidth / 2} y1={windowTop + 2} x2={wx + windowWidth / 2}
            y2={windowTop + windowHeight - 2} stroke="var(--lab-window-bar)" strokeWidth="2.5" />
          <line x1={wx + 4} y1={windowTop + windowHeight * 0.42} x2={wx + windowWidth - 4}
            y2={windowTop + windowHeight * 0.42} stroke="var(--lab-window-bar)" strokeWidth="2" />
          <rect x={wx + 8} y={windowTop + 8} width={windowWidth * 0.3} height={windowHeight * 0.18}
            rx="4" fill="#ffffff" opacity="0.18" />
        </g>
      ))}

      {/* Safety sign between/next to windows */}
      <g transform={`translate(${(windowGap + windowWidth + window2X) / 2 - 22} ${windowTop + windowHeight * 0.55})`}>
        <rect x="0" y="0" width="44" height="34" rx="5" fill="var(--lab-wall-low)"
          stroke="var(--lab-drop-warn)" strokeWidth="1.6" />
        <path d="M22 7 L32 24 L12 24 Z" fill="none" stroke="var(--lab-drop-warn)" strokeWidth="2" />
        <circle cx="22" cy="20.4" r="1.4" fill="var(--lab-drop-warn)" />
        <line x1="22" y1="22.6" x2="22" y2="17.6" stroke="var(--lab-drop-warn)" strokeWidth="1.4" />
      </g>

      {/* Metal shelf rack behind the rear band */}
      <g>
        <rect x={frame.x + 10} y={rear.top - 22} width={frame.width - 20} height="10" rx="3"
          fill="var(--lab-shelf)" />
        <rect x={frame.x + 10} y={rear.top - 12} width={frame.width - 20} height="4"
          fill="var(--lab-shelf-edge)" />
        {/* uprights */}
        {[0.08, 0.5, 0.92].map((fx) => (
          <rect key={fx} x={frame.x + 10 + (frame.width - 20) * fx - 3} y={frame.y + 24}
            width="6" height={rear.top - frame.y - 22} fill="var(--lab-steel)" opacity="0.75" rx="2" />
        ))}
        {/* shelf board under the rear objects */}
        <rect x={frame.x + 6} y={rear.bottom} width={frame.width - 12} height="14" rx="4"
          fill="var(--lab-shelf)" />
        <rect x={frame.x + 6} y={rear.bottom + 10} width={frame.width - 12} height="5"
          fill="var(--lab-shelf-edge)" opacity="0.8" />
      </g>

      {/* Bench: back ridge, work surface, front face and lip */}
      <rect x={frame.x} y={benchTopY} width={frame.width} height={benchBottomY - benchTopY}
        fill="url(#lab-env-bench)" />
      <rect x={frame.x} y={benchTopY} width={frame.width} height="6" rx="3"
        fill="var(--lab-bench-edge)" opacity="0.5" />
      {/* backsplash ridge */}
      <rect x={frame.x} y={benchTopY - 26} width={frame.width} height="26" rx="4"
        fill="var(--lab-bench-top-back)" />
      <rect x={frame.x} y={benchTopY - 27} width={frame.width} height="3"
        fill="var(--lab-bench-edge)" opacity="0.6" />
      {/* front face + edge */}
      <rect x={frame.x} y={benchBottomY} width={frame.width}
        height={Math.max(26, frame.y + frame.height - benchBottomY - 18)} fill="var(--lab-bench-front)" />
      <rect x={frame.x} y={benchBottomY} width={frame.width} height="5" rx="2"
        fill="var(--lab-bench-edge)" />
      <rect x={frame.x} y={benchBottomY + 12} width={frame.width}
        height={frame.y + frame.height - benchBottomY - 12} fill="url(#lab-env-shadow)" />
    </g>
  );
}
