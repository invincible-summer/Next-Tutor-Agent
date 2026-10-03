import { API_BASE } from "./api";
import { apiFetch } from "./api-fetch";
import type { QuestionIllustrationData } from "./types";

export interface DiagramAsset {
  id: string;
  title: string;
  english: string;
  category: string;
  family: string;
  aliases: string[];
  features: string[];
  version: number;
  license: string;
  subjects: string[];
  education_levels: string[];
  asset_kind: string;
  topics: string[];
  provenance: {
    origin: string;
    creation_method: string;
    source_module: string;
    source_symbol: string;
    source_hash: string;
    external_graphics: string[];
    derived_from: string[];
    knowledge_references: { url: string; purpose: string; type: string }[];
    statement: string;
  };
  review: {
    status: string;
    automatic: string;
    structure: string;
    visual: string;
    reviewer_type: string;
    reviewed_at?: string;
  };
  illustration: QuestionIllustrationData;
  v2?: {
    capabilities: string[];
    nominal_geometry: { ports?: Record<string, { point: [number, number]; kind: string }>; regions?: Record<string, unknown> };
    parameters: Record<string, ParameterSpec & { condition_bearing: boolean; unit?: string }>;
  } | null;
}
export interface AssetPage {
  catalog_version: string;
  catalog_total: number;
  categories: Record<string, number>;
  families: Record<string, number>;
  subjects: Record<string, number>;
  total: number;
  page: number;
  per: number;
  items: DiagramAsset[];
}
export interface TaxonomyItem { id: string; zh: string; en: string }
export interface DiagramTaxonomy {
  subjects: TaxonomyItem[];
  education_levels: TaxonomyItem[];
  asset_kinds: TaxonomyItem[];
  families: TaxonomyItem[];
  subject_groups: (TaxonomyItem & { subjects: string[] })[];
}
export interface ParameterSpec {
  type: "number" | "integer" | "boolean" | "color" | "string" | "list" | "enum";
  minimum?: number;
  maximum?: number;
  default?: unknown;
  required?: boolean;
  choices?: string[];
}
export interface AssetDetail extends DiagramAsset {
  size: [number, number];
  parameters: Record<string, ParameterSpec>;
  sample_params: Record<string, unknown>;
  anchors: Record<string, [number, number]>;
}
async function read<T>(path: string, init?: RequestInit, signal?: AbortSignal): Promise<T> {
  const response = await apiFetch(`${API_BASE}/diagram-assets${path}`, { ...init, signal });
  if (!response.ok) throw new Error(String(response.status));
  return response.json();
}
export function listDiagramAssets(query: URLSearchParams, signal?: AbortSignal) {
  return read<AssetPage>(`?${query}`, undefined, signal);
}
export function getDiagramTaxonomy(signal?: AbortSignal) {
  return read<DiagramTaxonomy>("/taxonomy", undefined, signal);
}
export function getDiagramAsset(id: string, signal?: AbortSignal) {
  return read<AssetDetail>(`/${encodeURIComponent(id)}`, undefined, signal);
}
export function previewDiagramAsset(id: string, params: Record<string, unknown>, profile: string) {
  return read<{ illustration: QuestionIllustrationData }>(`/${encodeURIComponent(id)}/preview`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ params, profile }),
  });
}
