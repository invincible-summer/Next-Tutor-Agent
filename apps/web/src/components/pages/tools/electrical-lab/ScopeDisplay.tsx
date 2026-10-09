"use client";
import { useMemo } from "react";
import type { SimulationFrame } from "@next-tutor/domain";
import { formatElectrical } from "@next-tutor/domain";

export function ScopeDisplay({ frame, labels, voltsA, voltsB, trigger, cursors }: { frame: SimulationFrame; labels: Record<string, string>; voltsA: number; voltsB: number; trigger: number; cursors: boolean }) {
  const width = 440; const height = 172;
  const traces = useMemo(() => {
    const samples = frame.samples; if (!samples.length) return { a: "", b: "" };
    const y = (value: number | null, div: number) => value == null ? height / 2 : Math.max(5, Math.min(height - 5, height / 2 - (value / (div * 4)) * (height / 2)));
    const make = (key: "a" | "b", div: number) => samples.map((sample, index) => `${(index / Math.max(1, samples.length - 1)) * width},${y(sample[key], div)}`).join(" ");
    return { a: make("a", voltsA), b: make("b", voltsB) };
  }, [frame.samples, voltsA, voltsB]);
  const stats = useMemo(() => {
    const values = frame.samples.map(s => s.a).filter((v): v is number => v != null); if (!values.length) return null;
    const max = Math.max(...values); const min = Math.min(...values); const rms = Math.sqrt(values.reduce((sum, v) => sum + v * v, 0) / values.length); return { max, min, pp: max - min, rms };
  }, [frame.samples]);
  return <div className="electrical-scope-screen" data-testid="electrical-scope-screen">
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={labels.scope}>
      <rect width={width} height={height} fill="#0a171a" />
      {Array.from({ length: 11 }, (_, i) => <line key={`v${i}`} x1={i * width / 10} y1="0" x2={i * width / 10} y2={height} className="scope-grid" />)}
      {Array.from({ length: 9 }, (_, i) => <line key={`h${i}`} x1="0" y1={i * height / 8} x2={width} y2={i * height / 8} className="scope-grid" />)}
      <line x1="0" y1={height / 2 - (trigger / (voltsA * 4)) * height / 2} x2={width} y2={height / 2 - (trigger / (voltsA * 4)) * height / 2} className="scope-trigger" />
      {traces.a && <polyline points={traces.a} fill="none" className="scope-trace scope-trace-a" />}
      {traces.b && <polyline points={traces.b} fill="none" className="scope-trace scope-trace-b" />}
      {!traces.a && <text x={width / 2} y={height / 2} textAnchor="middle" className="scope-empty">{labels.noTrace}</text>}
      {cursors && <><line x1={width * .32} y1="0" x2={width * .32} y2={height} className="scope-cursor" /><line x1={width * .68} y1="0" x2={width * .68} y2={height} className="scope-cursor" /></>}
    </svg>
    <div className="scope-readout"><span className="scope-a-label">A {formatElectrical(frame.scope.a, "V")}</span><span className="scope-b-label">B {formatElectrical(frame.scope.b, "V")}</span>{stats && <span>pp {formatElectrical(stats.pp, "V")} · RMS {formatElectrical(stats.rms, "V")}</span>}</div>
  </div>;
}
