import type { ElectricalComponentKind } from "@next-tutor/domain";

/** Small, original circuit symbols for the parts shelf and the quick tray. */
export function PartGlyph({ kind }: { kind: ElectricalComponentKind }) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  let graphic: React.ReactNode;
  switch (kind) {
    case "resistor": graphic = <path d="M2 12h5l3-5 4 10 4-10 4 10 3-5h7" {...common} />; break;
    case "conductance": case "potentiometer": graphic = <><path d="M2 12h6m18 0h6" {...common} /><rect x="8" y="7" width="18" height="10" rx="1" {...common} />{kind === "potentiometer" && <path d="M17 2v10m-3-3 3 3 3-3" {...common} />}</>; break;
    case "capacitor": graphic = <path d="M2 12h12m0-7v14m6-14v14m0-7h12" {...common} />; break;
    case "inductor": graphic = <path d="M2 12h4c0-9 6-9 6 0 0-9 6-9 6 0 0-9 6-9 6 0h8" {...common} />; break;
    case "switch": graphic = <><path d="M2 15h9m11 0h10M11 15l10-9" {...common} /><circle cx="11" cy="15" r="2" {...common} /><circle cx="22" cy="15" r="2" {...common} /></>; break;
    case "bulb": graphic = <><path d="M2 12h6m18 0h6" {...common} /><circle cx="17" cy="12" r="8" {...common} /><path d="m13 8 8 8m0-8-8 8" {...common} /></>; break;
    case "diode": case "led": graphic = <><path d="M2 12h8m14 0h8M10 5v14l12-7-12-7m14 0v14" {...common} />{kind === "led" && <path d="m25 5 3-3m-7 1 3-3" {...common} />}</>; break;
    case "ground": graphic = <path d="M17 2v10m-10 0h20m-17 5h14m-10 5h6" {...common} />; break;
    case "npn": case "pnp": graphic = <><circle cx="18" cy="12" r="10" {...common} /><path d="M2 12h12m0-5v10m0-7 8-5v-3m-8 12 8 5v3m-4-6 4 3-1-4" {...common} /></>; break;
    case "junction": graphic = <><path d="M2 12h30M17 2v20" {...common} /><circle cx="17" cy="12" r="3" fill="currentColor" /></>; break;
    default: graphic = <><circle cx="17" cy="12" r="9" {...common} /><path d="M2 12h6m18 0h6" {...common} /><path d={kind === "voltage" ? "M13 9h8m-4-4v8m-4 4h8" : "M11 12h12m-4-4 4 4-4 4"} {...common} /></>;
  }
  return <svg viewBox="0 0 34 24" width="34" height="24" aria-hidden="true">{graphic}</svg>;
}
