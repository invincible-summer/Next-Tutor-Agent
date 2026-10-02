import { DEMO_TOKEN, DEMO_TOKEN_KEY, SITE_BASE_PATH, demoReadOnly } from "./demo";

interface Manifest {
  responses: Record<string, string>;
  assets: Record<string, { url: string; type: string }>;
  routes: { sessions: string[]; notes: string[]; workspaces: string[]; runs?: { workspaceId: string; lessonId: string; runId: string }[] };
}
let manifestRequest: Promise<Manifest> | undefined;
const payloads = new Map<string, Promise<unknown>>();
const root = `${SITE_BASE_PATH}/demo/`;
async function checkedJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`Demo snapshot unavailable: ${response.status}`);
  return response.json();
}
function manifest(): Promise<Manifest> {
  return manifestRequest ??= checkedJson<Manifest>(root + "manifest.json").catch((error) => {
    manifestRequest = undefined;
    throw error;
  });
}

export async function demoLessonRuns(workspaceId: string, lessonId: string): Promise<string[]> {
  const index = await manifest();
  return (index.routes.runs || []).filter((run) => run.workspaceId === workspaceId && run.lessonId === lessonId).map((run) => run.runId);
}
function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}
function canonical(path: string, params: URLSearchParams): string {
  const query = new URLSearchParams([...params.entries()].filter(([key]) => key !== "student_id").sort(([a], [b]) => a.localeCompare(b))).toString();
  return path + (query ? "?" + query : "");
}
function record(value: unknown): Record<string, unknown> {
  return value as Record<string, unknown>;
}
function rows(value: unknown): Record<string, unknown>[] {
  return value as Record<string, unknown>[];
}

/** Local API adapter. Every write fails before making a network request. */
export async function demoFetch(input: string, init?: RequestInit): Promise<Response> {
  if (init?.signal?.aborted) throw init.signal.reason;
  const url = new URL(input, typeof window === "undefined" ? "https://demo.invalid" : window.location.href);
  const apiIndex = url.pathname.indexOf("/api/v1");
  if (apiIndex < 0) throw new Error("The demo only supports its static API snapshots");
  const path = url.pathname.slice(apiIndex + 7).replace(/\/$/, "") || "/";
  const method = (init?.method || "GET").toUpperCase();
  const loggedIn = typeof window !== "undefined" && localStorage.getItem(DEMO_TOKEN_KEY) === DEMO_TOKEN;
  if (path === "/auth/status") return json({ auth_required: true, guest_allowed: false });
  if (path === "/assistant/capabilities" && method === "GET") return json({ enabled: false, voice_enabled: false });
  if (path === "/voice/status" && method === "GET") return json({ enabled: false, available: false, configured: false, provider: "off" });
  if (path === "/auth/logout" && method === "POST") return json({ status: "ok" });
  if (path === "/auth/login" && method === "POST") {
    const body = JSON.parse(typeof init?.body === "string" ? init.body : "{}");
    if (body.email?.trim().toLowerCase() !== "example@example.com" || body.password !== "example") {
      return json({ detail: "invalid_credentials" }, 401);
    }
    const response = await snapshot("/auth/me");
    return json({ token: DEMO_TOKEN, user: record(await response.json()).user });
  }
  if (method !== "GET" && method !== "HEAD") {
    demoReadOnly();
    throw new Error("只读演示：不能修改数据或调用 AI / Read-only demo");
  }
  if (!loggedIn && path !== "/docs/content") return json({ detail: "not_authenticated" }, 401);
  const index = await manifest();
  const exactKey = canonical(path, url.searchParams);
  const asset = index.assets[exactKey];
  if (asset) {
    const response = await fetch(asset.url.startsWith("https://") ? asset.url : root + asset.url, { signal: init?.signal });
    if (!response.ok) throw new Error(`Demo asset unavailable: ${response.status}`);
    return response;
  }
  if (index.responses[exactKey]) return snapshot(exactKey);
  const params = new URLSearchParams(url.searchParams);
  // Queries that change presentation operate on complete exported lists.
  const presentation = ["offset", "limit", "tail", "q", "state", "textbook_id", "concept_key", "source_kind", "source_session_ref", "before", "view", "chapter_id", "level", "subject", "page", "page_size", "status"];
  for (const name of presentation) {
    if (path === "/knowledge/graph" && name === "textbook_id") continue;
    params.delete(name);
  }
  if (path.startsWith("/ux/")) { params.delete("grade"); params.delete("lang"); params.delete("days"); }
  if (path.startsWith("/knowledge/concepts/")) params.delete("workspace_id");
  let baseKey = canonical(path, params);
  const fullLimit = canonical(path, new URLSearchParams([...params.entries(), ["limit", path.startsWith("/memory/") || path.startsWith("/evaluation/") ? "500" : "200"]]));
  if (!index.responses[baseKey] && index.responses[fullLimit]) baseKey = fullLimit;
  if (path === "/notes/search") {
    const vault = record(await (await snapshot("/notes/vault")).json());
    const term = (url.searchParams.get("q") || "").toLowerCase();
    return json({ results: rows(vault.notes).filter((note) => JSON.stringify(note).toLowerCase().includes(term)) });
  }
  if (!index.responses[baseKey]) {
    if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("edu-demo-missing", { detail: exactKey }));
    return json({ detail: "演示快照没有这项数据 / Not available in the demo snapshot" }, 404);
  }
  let data: unknown = await (await snapshot(baseKey)).json();
  if (path === "/knowledge/graph") data = graphView(record(data), url.searchParams);
  if (path === "/ux/activity" && url.searchParams.has("days")) {
    const result = record(data);
    result.days = rows(result.days).slice(-Number(url.searchParams.get("days")));
  }
  if (path.startsWith("/chat/sessions/") && !path.includes("/files/")) {
    const result = record(data);
    const tail = Number(url.searchParams.get("tail") || 0);
    if (tail > 0) result.messages = rows(result.messages).slice(-tail);
  }
  if (path === "/memory/episodes") {
    const result = record(data);
    const before = Number(url.searchParams.get("before") || Infinity);
    const all = rows(result.episodes).filter((episode) => Number(episode.ts) < before);
    const limit = Number(url.searchParams.get("limit") || 50);
    result.episodes = all.slice(0, limit);
    result.has_more = all.length > limit;
  } else if (Array.isArray(data)) {
    data = data.slice(0, Number(url.searchParams.get("limit") || data.length));
  } else if (record(data).items && Array.isArray(record(data).items)) {
    const result = record(data);
    let items = rows(result.items);
    for (const name of ["state", "textbook_id", "concept_key", "source_kind", "source_session_ref"]) {
      const value = url.searchParams.get(name);
      if (value) items = items.filter((item) => item[name] === value
        || (name === "source_kind" && item.kind === value)
        || (name === "concept_key" && (record(item.concept_ref || {}).key === value || (item.concept_refs as string[] | undefined)?.includes(value)))
        || record(item.concept_ref || {})[name] === value);
    }
    const term = url.searchParams.get("q")?.toLowerCase();
    if (term) items = items.filter((item) => JSON.stringify(item).toLowerCase().includes(term));
    const limit = Number(url.searchParams.get("limit") || url.searchParams.get("page_size") || 20);
    const offset = Number(url.searchParams.get("offset") || ((Number(url.searchParams.get("page") || 1) - 1) * limit));
    data = { ...result, total: items.length, offset, limit, items: items.slice(offset, offset + limit) };
  }
  return json(data);
}

async function snapshot(key: string): Promise<Response> {
  const index = await manifest();
  const path = index.responses[key];
  if (!path) return json({ detail: "snapshot_not_found" }, 404);
  let pending = payloads.get(path);
  if (!pending) {
    pending = checkedJson(root + path).catch((error) => { payloads.delete(path); throw error; });
    payloads.set(path, pending);
  }
  // Response serialization gives each caller its own mutable copy.
  return json(await pending);
}

function graphView(data: Record<string, unknown>, params: URLSearchParams): Record<string, unknown> {
  let nodes = rows(data.nodes || []);
  let edges = rows(data.edges || []);
  for (const field of ["level", "subject"]) {
    const value = params.get(field);
    if (value) nodes = nodes.filter((node) => node[field] === value);
  }
  const view = params.get("view") || "full";
  const descendants = (id: string): Set<string> => {
    const ids = new Set([id]);
    let changed = true;
    while (changed) {
      changed = false;
      for (const edge of edges) {
        if (edge.type === "PART_OF" && ids.has(String(edge.to)) && !ids.has(String(edge.from))) {
          ids.add(String(edge.from)); changed = true;
        }
      }
    }
    return ids;
  };
  if (view === "chapter") {
    const ids = descendants(params.get("chapter_id") || "");
    nodes = nodes.filter((node) => ids.has(String(node.id)));
  } else if (view === "overview") {
    const original = nodes;
    nodes = nodes.filter((node) => node.kind === "chapter").map((node) => {
      const ids = descendants(String(node.id));
      return { ...node, metadata: { ...record(node.metadata || {}),
        concept_count: original.filter((n) => ids.has(String(n.id)) && n.kind === "concept").length,
        section_count: original.filter((n) => ids.has(String(n.id)) && n.kind === "section").length } };
    });
    edges = [];
  } else if (view === "search" && params.get("q")) {
    const term = params.get("q")!.toLowerCase();
    const ids = new Set(nodes.filter((n) => [n.name, n.description, n.aliases].join(" ").toLowerCase().includes(term)).map((n) => String(n.id)));
    let changed = true;
    while (changed) {
      changed = false;
      for (const e of edges) if (e.type === "PART_OF" && ids.has(String(e.from)) && !ids.has(String(e.to))) { ids.add(String(e.to)); changed = true; }
    }
    nodes = nodes.filter((n) => ids.has(String(n.id)));
  }
  const ids = new Set(nodes.map((node) => String(node.id)));
  edges = edges.filter((edge) => ids.has(String(edge.from)) && ids.has(String(edge.to)));
  return { ...data, nodes, edges, view };
}
