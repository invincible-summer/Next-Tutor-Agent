/**
 * Knowledge graph domain (mirrors `app/api/v1/knowledge.py`). Read-only
 * projections; the server answers `status: "disabled" | "not_found"` with
 * empty payloads instead of erroring, so callers branch on `status`. The
 * Web client's legacy `student_id` query parameter is dropped — the backend
 * resolves identity from the token and ignores it.
 */
import type { AbortSignalLike, QueryValue, Transport } from "./types.ts";

export type KnowledgeGraphView = "full" | "overview" | "chapter" | "search";

export interface KnowledgeGraphQuery {
  textbookId?: string;
  fileId?: string;
  level?: string;
  subject?: string;
  view?: KnowledgeGraphView;
  chapterId?: string;
  /** Search term (`view: "search"`). */
  q?: string;
  workspaceId?: string;
}

export interface KnowledgeNode {
  id: string;
  name: string;
  subject?: string;
  level?: string;
  difficulty?: number;
  description?: string;
  aliases?: string[];
  common_errors?: string[];
  origin?: string;
  kind?: string;
  metadata?: Record<string, unknown>;
  evaluation?: Record<string, unknown> | null;
  evaluation_coverage?: { evidenced: number; total: number };
  [key: string]: unknown;
}

export interface KnowledgeGraphResponse {
  status: "ok" | "disabled" | "not_found" | "error";
  nodes: KnowledgeNode[];
  edges: { from: string; to: string; type?: string }[];
  learned_edges?: number;
  coverage?: Record<string, unknown>[];
  view?: string;
  scope?: Record<string, unknown>;
  message?: string;
}

export interface KnowledgeConceptResponse {
  status: "ok" | "disabled" | "not_found" | "error";
  concept: (KnowledgeNode & {
    content?: Record<string, unknown>;
  }) | null;
  edges?: {
    prerequisites?: { id: string; name: string }[];
    unlocks?: { id: string; name: string }[];
    parents?: { id: string; name: string }[];
    children?: { id: string; name: string }[];
    related?: { id: string; name: string }[];
    applications?: { id: string; name: string }[];
    misconceptions?: { id: string; name: string }[];
  };
  evaluation?: Record<string, unknown> | null;
  teaching_log?: Record<string, unknown>[];
  memories?: Record<string, unknown>[];
  message?: string;
}

export interface KnowledgeTaxonomyResponse {
  status: "ok" | "disabled";
  levels?: Record<string, unknown>[];
  message?: string;
}

export interface KnowledgeCatalogResponse {
  status: "ok" | "disabled";
  stages?: { level: string; subjects: string[] }[];
  message?: string;
}

export interface KnowledgeCustomGraphsResponse {
  status: "ok" | "disabled";
  graphs?: Record<string, unknown>[];
}

export interface KnowledgeClient {
  /** GET /knowledge/graph — full/overview/chapter/search projections. */
  graph(query?: KnowledgeGraphQuery, signal?: AbortSignalLike | null): Promise<KnowledgeGraphResponse>;
  /** GET /knowledge/concepts/{id} — node detail with edges and evidence. */
  concept(
    conceptId: string,
    options?: { workspaceId?: string },
    signal?: AbortSignalLike | null,
  ): Promise<KnowledgeConceptResponse>;
  /** GET /knowledge/taxonomy — level/subject/group tree. */
  taxonomy(signal?: AbortSignalLike | null): Promise<KnowledgeTaxonomyResponse>;
  /** GET /knowledge/catalog — level → subjects summary. */
  catalog(signal?: AbortSignalLike | null): Promise<KnowledgeCatalogResponse>;
  /** GET /knowledge/custom — user-built lineage graphs (metadata only). */
  customGraphs(signal?: AbortSignalLike | null): Promise<KnowledgeCustomGraphsResponse>;
  /** DELETE /knowledge/custom/{topic_key} — archive a custom graph. */
  deleteCustomGraph<T = { status: string; topic_key: string; trash_item: unknown }>(
    topicKey: string,
  ): Promise<T>;
}

export function createKnowledgeClient(transport: Transport): KnowledgeClient {
  return {
    graph: (query = {}, signal) => {
      const wire: Record<string, QueryValue> = {
        textbook_id: query.textbookId,
        file_id: query.fileId,
        level: query.level,
        subject: query.subject,
        view: query.view,
        chapter_id: query.chapterId,
        q: query.q,
        workspace_id: query.workspaceId,
      };
      return transport
        .request<KnowledgeGraphResponse>("/knowledge/graph", { query: wire, signal })
        .then((result) => result.body);
    },
    concept: (conceptId, options = {}, signal) =>
      transport
        .request<KnowledgeConceptResponse>(
          `/knowledge/concepts/${encodeURIComponent(conceptId)}`,
          { query: { workspace_id: options.workspaceId }, signal },
        )
        .then((result) => result.body),
    taxonomy: (signal) =>
      transport.request<KnowledgeTaxonomyResponse>("/knowledge/taxonomy", { signal })
        .then((result) => result.body),
    catalog: (signal) =>
      transport.request<KnowledgeCatalogResponse>("/knowledge/catalog", { signal })
        .then((result) => result.body),
    customGraphs: (signal) =>
      transport.request<KnowledgeCustomGraphsResponse>("/knowledge/custom", { signal })
        .then((result) => result.body),
    deleteCustomGraph: <T>(topicKey: string) =>
      transport
        .request<T>(`/knowledge/custom/${encodeURIComponent(topicKey)}`, {
          method: "DELETE",
        })
        .then((result) => result.body),
  };
}
