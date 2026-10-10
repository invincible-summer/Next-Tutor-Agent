/**
 * Wholly original mathematical sample definitions — pure math text, no
 * frozen vertex data, no third-party scenes or assets. Presets are document
 * fragments the workbench merges through the normal command pipeline.
 */

import { createMathDocument } from "./document.ts";
import type { MathWorkbenchDocument, PlotDefinition3D } from "./model.ts";

export interface WorkbenchPreset {
  id: string;
  /** zh/en display names; expressions stay in standard math notation. */
  nameZh: string;
  nameEn: string;
  descriptionZh: string;
  descriptionEn: string;
  apply: (doc: MathWorkbenchDocument) => MathWorkbenchDocument;
}

const style = { color: "#2f7d6e", width: 2, opacity: 1, dashed: false } as const;

/** Distributive Omit so discriminated-union plot definitions keep their branch. */
type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;
type NewPlot3D = DistributiveOmit<PlotDefinition3D, "id">;

function surfacePreset(
  id: string,
  nameZh: string, nameEn: string,
  descriptionZh: string, descriptionEn: string,
  plot: NewPlot3D,
): WorkbenchPreset {
  return {
    id, nameZh, nameEn, descriptionZh, descriptionEn,
    apply: (doc) => ({ ...doc, plots3d: [...doc.plots3d, { ...plot, id: `plot-${id}` } as PlotDefinition3D] }),
  };
}

export const WORKBENCH_PRESETS: readonly WorkbenchPreset[] = [
  surfacePreset(
    "saddle", "马鞍面", "Saddle surface",
    "双曲抛物面 z = x² − y²，观察两个方向的曲率相反。", "Hyperbolic paraboloid z = x² − y² with opposite curvatures.",
    { kind: "explicitSurface", label: "z = x^2 - y^2", visible: true, style, expression: "x^2 - y^2", xDomain: { min: -3, max: 3 }, yDomain: { min: -3, max: 3 }, quality: "normal", opacity: 1, showGrid: true },
  ),
  surfacePreset(
    "gaussian", "高斯峰", "Gaussian peak",
    "钟形曲面 z = e^(−(x²+y²))，中心最高并向四周快速衰减。", "Bell surface z = e^(−(x²+y²)) peaking at the center.",
    { kind: "explicitSurface", label: "z = exp(-(x^2+y^2))", visible: true, style, expression: "exp(-(x^2+y^2))", xDomain: { min: -3, max: 3 }, yDomain: { min: -3, max: 3 }, quality: "normal", opacity: 1, showGrid: true },
  ),
  surfacePreset(
    "ripple", "波纹面", "Ripple surface",
    "径向波 z = sin(√(x²+y²))，从中心向外扩散的同心波。", "Radial waves z = sin(√(x²+y²)).",
    { kind: "explicitSurface", label: "z = sin(sqrt(x^2+y^2))", visible: true, style, expression: "sin(sqrt(x^2+y^2))", xDomain: { min: -8, max: 8 }, yDomain: { min: -8, max: 8 }, quality: "normal", opacity: 1, showGrid: true },
  ),
  surfacePreset(
    "torus", "参数环面", "Parametric torus",
    "用 u,v 参数方程生成的圆环面。", "A torus generated from u,v parametric equations.",
    { kind: "parametricSurface", label: "torus", visible: true, style, xExpression: "(2+cos(v))*cos(u)", yExpression: "(2+cos(v))*sin(u)", zExpression: "sin(v)", uDomain: { min: 0, max: 6.283185307179586 }, vDomain: { min: 0, max: 6.283185307179586 }, wrapU: true, wrapV: true, quality: "normal", opacity: 1, showGrid: true },
  ),
  surfacePreset(
    "implicit-sphere", "隐式球面", "Implicit sphere",
    "由方程 x²+y²+z²=1 直接定义的等值面。", "Isosurface defined directly by x²+y²+z²=1.",
    { kind: "implicitSurface", label: "x^2+y^2+z^2=1", visible: true, style, expression: "x^2+y^2+z^2", iso: 1, box: { x: { min: -1.4, max: 1.4 }, y: { min: -1.4, max: 1.4 }, z: { min: -1.4, max: 1.4 } }, resolution: 24, opacity: 0.9 },
  ),
  surfacePreset(
    "helix", "三维螺线", "3D helix",
    "空间曲线 x=cos t, y=sin t, z=t/4。", "Space curve x=cos t, y=sin t, z=t/4.",
    { kind: "parametricCurve", label: "helix", visible: true, style, xExpression: "cos(t)", yExpression: "sin(t)", zExpression: "t/4", tDomain: { min: 0, max: 12.566370614359172 }, samples: 600 },
  ),
];

/** Demo documents used by the read-only Pages export. */
export function presetDocument(presetId: string): MathWorkbenchDocument | null {
  const preset = WORKBENCH_PRESETS.find((p) => p.id === presetId);
  if (!preset) return null;
  const doc = createMathDocument(preset.nameZh);
  doc.activeMode = "functions3d";
  return preset.apply(doc);
}
