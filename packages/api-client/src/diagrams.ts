/**
 * Diagram materials domain (mirrors `app/api/v1/diagram_materials.py` and
 * the read-only catalog `diagram_library.py`). Ported from the Web's
 * `api-diagram-materials.ts`; errors surface as `ApiError` where `code`
 * carries the `material_*` / `admin_required` detail codes and 409
 * revision conflicts arrive as `ConflictError` (`material_revision_conflict`).
 */
import type { AbortSignalLike, QueryValue, Transport } from "./types.ts";

export type MaterialScope = "private" | "public";
export type MaterialSource = "upload" | "manual" | "llm";

export interface SvgParameter {
  type: "number" | "integer" | "string" | "color";
  role: "quantity" | "text" | "schematic" | "appearance";
  description: string;
  default: string | number;
  unit?: string;
  minimum?: number;
  maximum?: number;
  max_length?: number;
}

export interface SvgBinding {
  parameter: string;
  element_id: string;
  attribute: string;
  factor?: number;
  offset?: number;
}

export interface MaterialParameterization {
  schema_version: "1.0.0";
  parameters: Record<string, SvgParameter>;
  bindings: SvgBinding[];
}

export const STATIC_PARAMETERIZATION: MaterialParameterization = {
  schema_version: "1.0.0",
  parameters: {},
  bindings: [],
};

export interface MaterialInput {
  title: string;
  description: string;
  subject: string;
  aliases: string[];
  svg: string;
  scope: MaterialScope;
  source: MaterialSource;
  enabled: boolean;
  guidance_note?: string;
  parameterization?: MaterialParameterization;
  /** Required on PUT (CAS); absent on create. */
  base_revision?: number;
}

export interface MaterialIllustration {
  kind: string;
  svg: string;
  alt: string;
  caption: string;
  width: number;
  height: number;
  schema_version: number;
  sanitizer_version: number;
  content_hash: string;
}

export interface DiagramMaterial extends MaterialInput {
  id: string;
  revision: number;
  latest_revision?: number;
  revisions?: number[];
  content_hash: string;
  illustration: MaterialIllustration;
  validation: string;
  usage_guidance?: Record<string, unknown>;
  created_at?: number | string;
  updated_at?: number | string;
}

export interface MaterialTemplate {
  id: string;
  title: string;
  subject: string;
  description: string;
  svg: string;
  parameterization: MaterialParameterization;
}

export interface MaterialListFilters {
  subject?: string;
  enabledOnly?: boolean;
}

export interface DiagramAssetItem {
  id: string;
  title: string;
  english?: string;
  category?: string;
  family?: string;
  subjects?: string[];
  education_levels?: string[];
  asset_kind?: string;
  version?: string;
  illustration?: MaterialIllustration;
  [key: string]: unknown;
}

export interface DiagramAssetQuery {
  q?: string;
  category?: string;
  family?: string;
  subject?: string;
  educationLevel?: string;
  assetKind?: string;
  page?: number;
}

export interface DiagramsClient {
  // --- /diagram-materials (owned SVG materials) ---------------------------
  templates(signal?: AbortSignalLike | null): Promise<{ templates: MaterialTemplate[]; guide: string[] }>;
  listMaterials(
    scope: MaterialScope,
    q: string,
    page: number,
    signal?: AbortSignalLike | null,
    filters?: MaterialListFilters,
  ): Promise<{ items: DiagramMaterial[]; total: number; page?: number; per?: number }>;
  getMaterial(id: string, revision?: number, signal?: AbortSignalLike | null): Promise<DiagramMaterial>;
  previewMaterial(
    svg: string,
    signal?: AbortSignalLike | null,
    parameterization?: MaterialParameterization,
    params?: Record<string, string | number>,
  ): Promise<{ svg: string; illustration: MaterialIllustration; parameterization: MaterialParameterization; status?: string }>;
  generateMaterial(
    requirement: string,
    currentSvg?: string,
    signal?: AbortSignalLike | null,
    currentParameterization?: MaterialParameterization,
  ): Promise<{ svg: string; illustration: MaterialIllustration; parameterization?: MaterialParameterization; status?: string; source?: string }>;
  /** POST (create) or PUT (update, `id` + `base_revision` CAS). */
  saveMaterial(body: MaterialInput, id?: string, signal?: AbortSignalLike | null): Promise<DiagramMaterial>;
  /** DELETE with `base_revision` CAS query. */
  deleteMaterial(id: string, revision: number, signal?: AbortSignalLike | null): Promise<{ deleted: boolean }>;

  // --- /diagram-assets (read-only public catalog) --------------------------
  listAssets(
    query?: DiagramAssetQuery,
    signal?: AbortSignalLike | null,
  ): Promise<{ items: DiagramAssetItem[]; total: number; page: number; per: number; catalog_version?: number; categories?: Record<string, number> }>;
  assetTaxonomy(signal?: AbortSignalLike | null): Promise<Record<string, unknown>>;
  getAsset(id: string, signal?: AbortSignalLike | null): Promise<DiagramAssetItem>;
  previewAsset(
    id: string,
    params: Record<string, string | number>,
    profile?: "textbook" | "monochrome",
    signal?: AbortSignalLike | null,
  ): Promise<{ illustration: MaterialIllustration }>;
}

export function createDiagramsClient(transport: Transport): DiagramsClient {
  const materials = <T>(path: string, init: Parameters<Transport["request"]>[1]) =>
    transport.request<T>(`/diagram-materials${path}`, init).then((result) => result.body);
  return {
    templates: (signal) => materials<{ templates: MaterialTemplate[]; guide: string[] }>("/templates", { signal }),
    listMaterials: (scope, q, page, signal, filters) =>
      materials<{ items: DiagramMaterial[]; total: number; page?: number; per?: number }>("", {
        query: {
          scope,
          q,
          page,
          per: 12,
          subject: filters?.subject,
          enabled_only: filters?.enabledOnly ? true : undefined,
        },
        signal,
      }),
    getMaterial: (id, revision, signal) =>
      materials<DiagramMaterial>(`/${encodeURIComponent(id)}`, {
        query: revision ? { revision } : undefined,
        signal,
      }),
    previewMaterial: (svg, signal, parameterization = STATIC_PARAMETERIZATION, params = {}) =>
      materials("/preview", { method: "POST", json: { svg, parameterization, params }, signal }),
    generateMaterial: (requirement, currentSvg = "", signal, currentParameterization = STATIC_PARAMETERIZATION) =>
      materials("/generate", {
        method: "POST",
        json: { requirement, current_svg: currentSvg, current_parameterization: currentParameterization },
        signal,
      }),
    saveMaterial: (body, id, signal) =>
      materials<DiagramMaterial>(id ? `/${encodeURIComponent(id)}` : "", {
        method: id ? "PUT" : "POST",
        json: body,
        signal,
      }),
    deleteMaterial: (id, revision, signal) =>
      materials<{ deleted: boolean }>(`/${encodeURIComponent(id)}`, {
        method: "DELETE",
        query: { base_revision: revision },
        signal,
      }),
    listAssets: (query = {}, signal) => {
      const wire: Record<string, QueryValue> = {
        q: query.q,
        category: query.category,
        family: query.family,
        subject: query.subject,
        education_level: query.educationLevel,
        asset_kind: query.assetKind,
        page: query.page,
      };
      return transport
        .request<{ items: DiagramAssetItem[]; total: number; page: number; per: number; catalog_version?: number; categories?: Record<string, number> }>(
          "/diagram-assets",
          { query: wire, signal },
        )
        .then((result) => result.body);
    },
    assetTaxonomy: (signal) =>
      transport.request<Record<string, unknown>>("/diagram-assets/taxonomy", { signal })
        .then((result) => result.body),
    getAsset: (id, signal) =>
      transport.request<DiagramAssetItem>(`/diagram-assets/${encodeURIComponent(id)}`, { signal })
        .then((result) => result.body),
    previewAsset: (id, params, profile, signal) =>
      transport
        .request<{ illustration: MaterialIllustration }>(`/diagram-assets/${encodeURIComponent(id)}/preview`, {
          method: "POST",
          json: { params, profile: profile ?? "textbook" },
          signal,
        })
        .then((result) => result.body),
  };
}
