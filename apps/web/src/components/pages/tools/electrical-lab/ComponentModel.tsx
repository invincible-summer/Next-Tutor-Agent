"use client";

import { COMPONENT_DEFAULTS, formatElectrical, type ElectricalComponent } from "@next-tutor/domain";

const BAND_COLORS = ["#24272b", "#8a5031", "#df5145", "#ee9739", "#f3ce52", "#4b956b", "#457bb1", "#9978bb", "#889197", "#e9e7df"];
function resistorBands(value: number): string[] {
  let exponent = Math.max(-2, Math.floor(Math.log10(value)) - 1);
  let digits = Math.round(value / 10 ** exponent);
  if (digits > 99) { exponent++; digits = Math.round(value / 10 ** exponent); }
  return [BAND_COLORS[Math.floor(digits / 10)]!, BAND_COLORS[digits % 10]!, exponent < 0 ? (exponent === -1 ? "#c8a34f" : "#b5bec6") : BAND_COLORS[exponent]!, "#c8a34f"];
}

/** Compact physical packages with the same electrical terminals as the solver. */
export function ComponentModel({ component, selected, labels }: { component: ElectricalComponent; selected: boolean; labels: Record<string, string> }) {
  const line = { fill: "none", stroke: "#aab9c1", strokeWidth: 2.6, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  const leads = <path d="M-38 0H-18 M18 0H38" {...line} />;
  let body: React.ReactNode;
  switch (component.kind) {
    case "resistor": body = <>{leads}<path d="M-24-10 Q-29 0-24 10 L-17 10 Q-13 7-9 8 H9 Q13 7 17 10 H24 Q29 0 24-10 H17 Q13-7 9-8 H-9 Q-13-7-17-10Z" fill="#d8c296" stroke="#9c835c" strokeWidth="1.2" />{resistorBands(component.value).map((color, index) => <rect key={index} x={[-17, -8, 1, 18][index]} y="-9" width="4" height="18" rx=".7" fill={color} />)}<path d="M-20-5H20" stroke="#fff4d5" strokeOpacity=".45" strokeWidth="1.4" /></>; break;
    case "conductance": body = <>{leads}<rect x="-24" y="-12" width="48" height="24" rx="4" fill="#bcd0cc" stroke="#769f96" strokeWidth="1.2" /><path d="M-19-7H19" stroke="#e9f6f0" strokeWidth="1.5" /><text y="5" textAnchor="middle" fill="#28524c" fontSize="13" fontWeight="700">G</text></>; break;
    case "potentiometer": body = <>{leads}<path d="M0-28V-15" {...line} /><circle r="18" fill="#75949a" stroke="#b7cdd0" strokeWidth="1.2" /><circle r="12" fill="#2c4148" stroke="#536a70" strokeWidth="2" /><g transform={`rotate(${-120 + component.position * 240})`}><path d="M0-8V8" stroke="#b5c7ca" strokeWidth="3" strokeLinecap="round" /><circle cy="-15" r="2" fill="#f5d471" /></g></>; break;
    case "switch": body = <>{leads}<rect x="-25" y="-14" width="50" height="28" rx="5" fill="#273c44" stroke="#657e86" strokeWidth="1.3" /><circle cx="-19" r="2.5" fill="#a6babd" /><circle cx="19" r="2.5" fill="#a6babd" /><rect x="-13" y="-8" width="26" height="16" rx="3" fill="#0d1c22" /><rect x={component.closed ? "0" : "-12"} y="-7" width="12" height="14" rx="2" fill={component.closed ? "#64b8a8" : "#bbc6c6"} /><path d={component.closed ? "M4-4V4 M7-4V4" : "M-8-4V4 M-5-4V4"} stroke="#284740" strokeWidth="1" /></>; break;
    case "capacitor": body = <>{leads}<rect x="-15" y="-21" width="30" height="42" rx="6" fill="#375d79" stroke="#8aabc0" strokeWidth="1.2" /><path d="M8-16V16" stroke="#bfd4dd" strokeWidth="6" /><path d="M-10-14V14" stroke="#7094ac" strokeWidth="2" /><ellipse cy="-18" rx="13" ry="4" fill="#bac7ce" stroke="#8b9ca6" /><path d="M-4-20L4-16 M4-20L-4-16" stroke="#8297a4" strokeWidth=".9" /><text x="-5" y="4" textAnchor="middle" fill="#e7f0f5" fontSize="10" fontWeight="700">C</text></>; break;
    case "inductor": body = <>{leads}<rect x="-24" y="-9" width="48" height="18" rx="5" fill="#3a464b" stroke="#8c9b9d" />{[-18, -9, 0, 9, 18].map(x => <ellipse key={x} cx={x} rx="5.5" ry="13" fill="none" stroke="#c58a51" strokeWidth="3" />)}<path d="M-20-10H20" stroke="#f5c993" strokeWidth="1" opacity=".55" /></>; break;
    case "bulb": body = <>{leads}<ellipse cy="-5" rx="17" ry="22" fill="#d4f0ec" fillOpacity=".13" stroke="#b9dad8" strokeWidth="1.5" /><path d="M-8 11L-5-2 M8 11L5-2 M-5-2L-2 2 1-2 5-2" fill="none" stroke="#ebc26b" strokeWidth="1.5" /><path d="M-10 12H10V21H-10Z" fill="#bca56d" stroke="#7f724c" /><path d="M-9 15H9 M-9 18H9" stroke="#6b643f" strokeWidth="1.2" /><path d="M-8-21Q-13-17-13-9" {...line} stroke="#e9fffc" strokeWidth="2" opacity=".6" /></>; break;
    case "diode": body = <>{leads}<rect x="-22" y="-9" width="44" height="18" rx="4" fill="#253139" stroke="#687880" strokeWidth="1.2" /><rect x="11" y="-8" width="5" height="16" fill="#c8d0d2" /><path d="M-15-4H6" stroke="#56666d" strokeWidth="1.2" /><text x="-6" y="4" fill="#d4dfe2" fontSize="8" textAnchor="middle">D</text></>; break;
    case "led": body = <>{leads}<path d="M-13 8V-3A13 13 0 0 1 13-3V8Z" fill="#dc795e" fillOpacity=".72" stroke="#f3b095" strokeWidth="1.5" /><rect x="-16" y="8" width="32" height="5" rx="2" fill="#d88a6c" stroke="#f6bfa7" strokeWidth="1" /><path d="M-7-4V4 M7-4V4 M-7 0H7" stroke="#633e33" strokeWidth="1.7" /><path d="M-7-9Q-9-7-9-3" stroke="#ffe2c6" strokeWidth="2" strokeLinecap="round" /></>; break;
    case "npn": case "pnp": body = <><path d="M-32 0H-13 M0-30V-16 M0 16V30" {...line} /><path d="M12-17H-2A17 17 0 0 0-2 17H12Z" fill="#233038" stroke="#6c8188" strokeWidth="1.4" /><path d="M7-12H0Q-12-10-12 0" {...line} stroke="#586d74" strokeWidth="1" /><text x="-1" y="3" textAnchor="middle" fill="#d8e3e4" fontSize="7.5" fontWeight="600">{component.kind.toUpperCase()}</text></>; break;
    case "ground": body = <path d="M0-24V-6 M-18-6H18 M-11 1H11 M-4 8H4" {...line} stroke="#83b6b1" />; break;
    case "voltage": case "current": body = <>{leads}<circle r="23" fill="#1b343d" stroke="#7298a3" strokeWidth="1.4" />{component.kind === "voltage" ? <path d="M-6-8H6 M0-14V-2 M-6 9H6" {...line} stroke="#f4d06f" strokeWidth="2" /> : <path d="M-12 0H12 M5-7L12 0 5 7" {...line} stroke="#f4d06f" strokeWidth="2" />}</>; break;
    default: body = null;
  }
  if (component.kind === "junction") return <g transform={`translate(${component.x} ${component.y})`} className="electrical-junction-symbol"><circle r="4" fill={selected ? "#f4d06f" : "#57c9c0"} stroke="var(--el-panel)" strokeWidth="1.5" /><text x="11" y="-9" className="electrical-junction-label">{component.label}</text></g>;
  const value = component.kind === "switch" ? (component.closed ? labels.closed : labels.openSwitch) : component.kind === "ground" ? "0 V" : component.kind === "npn" || component.kind === "pnp" ? `β ${component.value}` : formatElectrical(component.value, COMPONENT_DEFAULTS[component.kind].unit);
  return <g transform={`translate(${component.x} ${component.y})`} className={`electrical-component-symbol ${selected ? "is-selected" : ""}`}>
    <g transform={`rotate(${component.rotation})`}><rect className="electrical-part-hit" x="-44" y="-34" width="88" height="68" rx="9" fill="transparent" /><rect className="electrical-part-outline" x="-44" y="-34" width="88" height="68" rx="9" fill="none" stroke={selected ? "#f4d06f" : "var(--el-teal)"} strokeWidth="1" strokeDasharray="3 3" />{body}</g>
    <text y="46" textAnchor="middle" className="electrical-component-label">{component.label}</text><text y="59" textAnchor="middle" className="electrical-component-value">{value}</text>
  </g>;
}
