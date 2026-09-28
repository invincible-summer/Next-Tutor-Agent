/* 课堂 E2E 共享路由夹具（plan.md §19.3）。
 *
 * page.route 拦截课堂/工作区 API（无后端参与），提供：
 * - 可变课程状态（revision 编辑器 409/删页钳位等场景在测试内直接改）
 * - 主题化 frame HTML（visual/export spec 用）
 * - JobPublic/LessonDetailPublic/RunPublic 等合法 DTO 工厂
 */
import type { Page, Route } from "@playwright/test";

export const WS_ID = "ws-e2e-classroom";
export const LESSON_ID = "les-e2e-edit";
export const RUN_ID = "run-e2e-run";
export const JOB_ID = "job-e2e-job";

export const SEGS = [
  { id: "seg-e2e-1", text: "第一段：把两个碰撞的小车看成一个系统。" },
  { id: "seg-e2e-2", text: "第二段：内力成对出现，总动量不变。" },
  { id: "seg-e2e-3", text: "第三段：只要合外力冲量可以忽略。" },
  { id: "seg-e2e-4", text: "第四段：这就是动量守恒的适用条件。" },
];

export interface SlideMock {
  id: string;
  order: number;
  title: string;
  segIdx: number[];
  checkpoint?: "reflect" | "question";
  /** 带 image block（换图流程需要）。 */
  withImage?: boolean;
}

export const SLIDES_2: SlideMock[] = [
  { id: "s-e2e-slide01", order: 1, title: "系统与内力", segIdx: [0, 1],
    checkpoint: "question" },
  { id: "s-e2e-slide02", order: 2, title: "守恒条件", segIdx: [2, 3] },
];

export interface MutableLessonState {
  slides: SlideMock[];
  revision: number;
  themeId: string;
  lifecycle: string;
  latestJobState: string;
  runState: "pending" | "answered" | "skipped";
}

/** 主题化 frame：theme 色板注入背景，供 visual spec 断言与截图。 */
export function themedFrame(themeName: string, accent = "#2563eb",
                            bg = "#ffffff"): string {
  return `<!doctype html><html><head><meta charset="utf-8">
<style>
  body { margin:0; background:${bg}; font-family:serif; }
  .slide { padding:24px; }
  .slide h1 { color:${accent}; font-size:28px; }
  .katex-mock { font-style:italic; color:${accent}; }
</style></head><body>
<div class="slide" data-theme="${themeName}">
  <h1>动量守恒 · ${themeName}</h1>
  <p class="katex-mock">Δp⃗ = F⃗·Δt</p>
  <p>合外力为零时，系统总动量保持不变。</p>
</div>
<script>
  parent.postMessage({ type: "classroom_ready", nonce: "n0nce" }, "*");
  window.addEventListener("message", (e) => {
    if (e.data && e.data.type === "classroom_init" && e.ports && e.ports[0]) {
      const port = e.ports[0];
      port.onmessage = (m) => {
        if (m.data && m.data.type === "goto_page") {
          port.postMessage({ type: "page_selected", order: m.data.order });
        }
      };
      port.start();
    }
  });
</script>
</body></html>`;
}

export function jobPublic(over: Record<string, unknown> = {}): object {
  return {
    job_id: JOB_ID, lesson_id: LESSON_ID, state: "running",
    phase: "author_slides", state_revision: 4,
    progress: { completed_slides: 1, total_slides: 2 },
    warnings: [], last_error: null, cancel_requested: false,
    start_mode: "automatic",
    created_at: "2026-09-26T00:00:00Z",
    updated_at: "2026-09-26T00:00:00Z", next_actions: [],
    ...over,
  };
}

export function revisionSlides(state: MutableLessonState): object[] {
  return state.slides.map((s) => ({
    slide_id: s.id, order: s.order, title: s.title,
    layout: "key_points", learning_objective_ids: [],
    blocks: [
      { id: `blk-e2e-${s.order}1`, kind: "paragraph",
        spans: [{ kind: "text", text: s.title }] },
      ...(s.withImage ? [{
        id: `blk-e2e-${s.order}2`, kind: "image",
        asset_id: "ast_e2e_1", alt: "碰撞实验", caption: "两小车碰撞",
      }] : []),
    ],
    segments: s.segIdx.map((i) => ({
      segment_id: SEGS[i].id, role: "explain",
      display_text: SEGS[i].text, spoken_text: SEGS[i].text,
      show_block_ids: [`blk-e2e-${s.order}1`], focus_block_ids: [],
      pause_after_ms: 0, source_ids: [], estimated_ms: 700,
    })),
    claims: [], source_ids: [], transition: "auto", estimated_seconds: 1,
  }));
}

export function lessonDetail(state: MutableLessonState,
                             over: Record<string, unknown> = {}): object {
  return {
    lesson_id: LESSON_ID, workspace_id: WS_ID, title: "动量守恒入门",
    lifecycle: state.lifecycle, latest_ready_revision: state.revision,
    published_revisions: [state.revision], latest_job: null,
    revision: {
      revision: state.revision, schema_version: 1,
      brief: { topic: "动量守恒入门", theme_id: state.themeId,
               duration_minutes: 15 },
      slides: revisionSlides(state),
      objectives: [], glossary: [],
      source_records: [
        { source_id: "src-e2e-1", kind: "textbook", title: "大学物理 第一册",
          section_path: ["第3章"], printed_page: "80", url: "", domain: "" }],
      assets: [], checkpoints: state.slides
        .filter((s) => s.checkpoint)
        .map((s) => ({
          checkpoint_id: `ckp-e2e-${s.order}00000000000${s.order}`,
          slide_id: s.id, kind: s.checkpoint,
          prompt: s.checkpoint === "question"
            ? "下列哪种情况系统动量守恒？" : "想一想：为什么内力不改变总动量？",
          reflection_seconds: s.checkpoint === "reflect" ? 8 : null,
          optional: false, question: null,
        })),
      renderer_version: "test", content_hash: "0".repeat(64),
      created_at: "2026-09-26T00:00:00Z",
    },
    pending: null,
    recent_run: {
      run_id: RUN_ID, lesson_id: LESSON_ID, lesson_revision: state.revision,
      status: "paused",
      cursor: { slide_id: SLIDES_2[0].id, segment_id: SEGS[0].id,
                chunk_index: 0, offset_ms: 0,
                last_completed_segment_id: null },
      cursor_slide_order: 1, resume_anchor: null,
      audio_profile: { policy: "auto", provider: "azure",
        voice_id: "zh-CN-XiaoxiaoNeural", language: "zh-CN",
        allow_local_fallback: true, playback_speed: 0.9, version: 1 },
      qa_session_id: null, visited_slide_count: 1, completed_kind: "",
      created_at: "2026-09-26T00:00:00Z",
      updated_at: "2026-09-26T00:00:00Z",
      lease: { held: false, expired: true, client_id: null,
               lease_epoch: 0, expires_at: null },
      cursor_index: 0, segment_total: SEGS.length,
      tts_local_locked: false, tts_fallback_notified: false,
    },
    ...over,
  };
}

export const CAPABILITIES_FULL = {
  enabled: true, allowed: true, reason: "",
  limits: { max_pages: 24, max_revisions: 20 },
  tts: { configured: true, available: true, reason: "",
         policy: "auto", voices: [
           { voice_id: "zh-CN-XiaoxiaoNeural", display_name: "晓晓（zh）" }] },
  research: { configured: true, available: true, reason: "" },
  images: { configured: true, available: true, reason: "" },
  renderer: { available: true, renderer_version: "test" },
};

export function templatesPayload(): object {
  const theme = (id: string, name: string, accent: string) =>
    ({ theme_id: id, name_zh: name, name_en: name, description_zh: "",
       description_en: "", accent, preview_kind: "swatch" });
  return {
    pedagogy: [
      { pedagogy_id: "concept_deep@1", name_zh: "概念精讲", name_en: "",
        description_zh: "吃透概念", description_en: "" },
    ],
    themes: [
      theme("academic_clear@1", "学术清晰", "#2563eb"),
      theme("warm_chalk@1", "暖粉笔记", "#d97706"),
      theme("ink_minimal@1", "墨色极简", "#111827"),
      theme("nature_field@1", "自然野趣", "#059669"),
      theme("starry_night@1", "星空夜话", "#7c3aed"),
    ],
    defaults: { duration_minutes: 15, pedagogy_id: "concept_deep@1",
                theme_id: "academic_clear@1", image_density: "balanced",
                checkpoint_density: "standard" },
  };
}

export const WORKSPACE_DETAIL = {
  workspace_id: WS_ID, name: "大学物理", session_ids: [],
  knowledge_files: [
    { id: "file_e2e_tb1", library_file_id: "file_e2e_tb1",
      filename: "大学物理 第一册.pdf" },
  ],
  public_memory: "", public_memory_updated_at: 0,
  created_at: 0, updated_at: 0,
};

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json",
                  body: JSON.stringify(body) });

/** 列表/详情/能力/模板/工作区/教材 的基础路由（编辑器与创建流共用）。 */
export async function routeBase(
  page: Page, state: MutableLessonState, opts: {
    frameHtml?: string;
    listStatus?: string;
  } = {},
): Promise<void> {
  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();

    if (path === "/api/v1/workspaces" && method === "GET") {
      return json(route, { workspaces: [] });
    }
    const wsMatch = path.match(/^\/api\/v1\/workspaces\/([^/]+)$/);
    if (wsMatch && method === "GET") {
      return json(route, WORKSPACE_DETAIL);
    }
    if (path === "/api/v1/chat/sessions" && method === "GET") {
      return json(route, { sessions: [] });
    }
    if (path === "/api/v1/textbooks" && method === "GET") {
      return json(route, { textbooks: [
        { id: "tb_e2e_1", title: "大学物理 第一册",
          file_ids: ["file_e2e_tb1"], volumes: [] }] });
    }
    if (path === "/api/v1/textbooks/tb_e2e_1" && method === "GET") {
      return json(route, { id: "tb_e2e_1", title: "大学物理 第一册",
        status: "ready", outline: [
          { chapter: "3.4 动量守恒" }, { chapter: "3.5 碰撞" }] });
    }
    if (path === "/api/v1/classroom/capabilities") {
      return json(route, CAPABILITIES_FULL);
    }
    if (path === "/api/v1/classroom/templates") {
      return json(route, templatesPayload());
    }
    if (path === `/api/v1/workspaces/${WS_ID}/classroom/lessons`
        && method === "GET") {
      return json(route, {
        items: [{
          lesson_id: LESSON_ID, workspace_id: WS_ID, title: "动量守恒入门",
          status: opts.listStatus ?? "ready",
          latest_ready_revision: state.revision,
          latest_job: null, brief: null, extra: { slide_count:
            state.slides.length },
          updated_at: "2026-09-26T00:00:00Z",
        }],
        total: 1, page: 1, page_size: 5, resume: null,
      });
    }
    if (path === `/api/v1/workspaces/${WS_ID}/classroom/lessons/${LESSON_ID}`
        && method === "GET") {
      return json(route, lessonDetail(state));
    }
    if (path.endsWith(`/lessons/${LESSON_ID}/revisions/${state.revision}`
                       + "/frame") && method === "GET") {
      return route.fulfill({
        status: 200, contentType: "text/html; charset=utf-8",
        body: opts.frameHtml ?? themedFrame("academic_clear"),
      });
    }
    return route.fallback();
  });
}
