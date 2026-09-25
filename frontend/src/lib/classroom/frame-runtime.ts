/* 课堂 frame runtime（plan.md §9.4/§9.5）。
 *
 * 独立 DOM runtime：仅接页切换、块显隐、高亮、主题/阅读模式指令；
 * 无业务 fetch、无 token、无 localStorage。源码为单一 IIFE 闭包，
 * 由 tsconfig.classroom.json 以 module=none 编译为经典脚本。
 *
 * 握手：frame 加载后向 parent 发送 classroom_ready{nonce}；父页面校验
 * event.source 后回发 {type:"classroom_init", nonce, port}；此后一切
 * 通信只走该 MessagePort。offline/print 模式无父页面时启用内置翻页控件。
 */
(function () {
  "use strict";

  interface InitMessage {
    type: string;
    nonce?: string;
  }
  interface PortMessage {
    type: string;
    order?: number;
    visible?: string[];
    focus?: string[];
    reading?: boolean;
  }
  interface KatexGlobal {
    render: (tex: string, el: Element, opts?: Record<string, unknown>) => void;
  }

  function getKatex(): KatexGlobal | undefined {
    return (window as unknown as { katex?: KatexGlobal }).katex;
  }

  const mode = (document.documentElement.getAttribute("data-mode") || "online");
  // 脚本位于 <head>：DOM 查询必须延迟到 DOMContentLoaded 之后
  let slides: HTMLElement[] = [];
  let current = 0;
  let nonce = "";
  let port: MessagePort | null = null;

  function queryDom(): void {
    slides = Array.prototype.slice.call(
      document.querySelectorAll(".slide")) as HTMLElement[];
  }

  function makeNonce(): string {
    let out = "";
    let i: number;
    if (window.crypto && typeof window.crypto.getRandomValues === "function") {
      const bytes = new Uint8Array(16);
      window.crypto.getRandomValues(bytes);
      for (i = 0; i < bytes.length; i++) {
        out += ("0" + bytes[i].toString(16)).slice(-2);
      }
    } else {
      for (i = 0; i < 16; i++) {
        out += ("0" + Math.floor(Math.random() * 256).toString(16))
          .slice(-2);
      }
    }
    return out;
  }

  function send(message: unknown, transfer?: MessagePort[]): void {
    if (port) {
      port.postMessage(message);
      return;
    }
    if (window.parent !== window) {
      window.parent.postMessage(message, "*", transfer || []);
    }
  }

  function renderMath(root: HTMLElement): void {
    const nodes = root.querySelectorAll<HTMLElement>("[data-katex]");
    for (let i = 0; i < nodes.length; i++) {
      const el = nodes[i];
      if (el.getAttribute("data-rendered") === "1") continue;
      const tex = el.getAttribute("data-katex") || "";
      try {
        const engine = getKatex();
        if (engine && tex) {
          engine.render(tex, el, {
            trust: false,
            strict: "ignore",
            maxSize: 25,
            maxExpand: 40,
            displayMode: el.classList.contains("formula-box")
          });
        } else if (tex) {
          el.textContent = tex;
        }
      } catch {
        el.textContent = tex; // 公式渲染失败兜底为可读原文
      }
      el.setAttribute("data-rendered", "1");
    }
  }

  function scaleStage(slide: HTMLElement): void {
    const stage = slide.querySelector<HTMLElement>(".stage");
    if (!stage) return;
    const reading = document.documentElement.getAttribute("data-reading") === "1";
    if (reading) {
      stage.style.transform = "";
      return;
    }
    const vw = window.innerWidth || 1280;
    let vh = window.innerHeight || 720;
    // offline 模式底部有内置控制条：缩放预留其高度，避免遮挡页脚
    if (mode === "offline") vh -= 84;
    const scale = Math.min(vw / 1280, vh / 720, 1.15);
    stage.style.transform = "scale(" + scale + ")";
  }

  function measure(slide: HTMLElement): void {
    const blocks = slide.querySelectorAll<HTMLElement>("[data-block-id]");
    const report: Array<Record<string, unknown>> = [];
    const stage = slide.querySelector<HTMLElement>(".stage");
    const stageRect = stage ? stage.getBoundingClientRect() : null;
    for (let i = 0; i < blocks.length; i++) {
      const el = blocks[i];
      const rect = el.getBoundingClientRect();
      report.push({
        id: el.getAttribute("data-block-id"),
        top: Math.round(rect.top),
        left: Math.round(rect.left),
        right: Math.round(rect.right),
        bottom: Math.round(rect.bottom),
        overflow: !!(stageRect && (
          rect.bottom > stageRect.bottom + 1 ||
          rect.right > stageRect.right + 1))
      });
    }
    send({ type: "layout_measurement", order: current, blocks: report });
  }

  function show(order: number): void {
    if (!slides.length) return;
    let index = order - 1;
    if (index < 0 || index >= slides.length) index = 0;
    for (let i = 0; i < slides.length; i++) {
      slides[i].classList.toggle("current", i === index);
    }
    current = index;
    const slide = slides[index];
    renderMath(slide);
    scaleStage(slide);
    const indicator = document.getElementById("page-indicator");
    if (indicator) {
      indicator.textContent = (index + 1) + " / " + slides.length;
    }
    send({
      type: "page_selected",
      order: index + 1,
      slide_id: slide.getAttribute("data-slide-id")
    });
    window.setTimeout(function () { measure(slide); }, 60);
  }

  function next(): void { show(Math.min(current + 2, slides.length)); }
  function prev(): void { show(Math.max(current, 1)); }

  function setBlockState(visible: string[], focus: string[]): void {
    const slide = slides[current];
    if (!slide) return;
    const visSet: Record<string, boolean> = {};
    const focSet: Record<string, boolean> = {};
    let i: number;
    for (i = 0; i < visible.length; i++) visSet[visible[i]] = true;
    for (i = 0; i < focus.length; i++) focSet[focus[i]] = true;
    const blocks = slide.querySelectorAll<HTMLElement>("[data-block-id]");
    for (i = 0; i < blocks.length; i++) {
      const el = blocks[i];
      const id = el.getAttribute("data-block-id") || "";
      el.classList.toggle("pending", !visSet[id]);
      el.classList.toggle("focus", !!focSet[id]);
    }
  }

  function setReading(on: boolean): void {
    document.documentElement.setAttribute("data-reading", on ? "1" : "0");
    const slide = slides[current];
    if (slide) scaleStage(slide);
  }

  function offlineControls(): void {
    const controls = document.getElementById("controls");
    if (!controls) return;
    controls.addEventListener("click", function (ev: Event) {
      const target = ev.target as HTMLElement;
      const action = target.getAttribute("data-action");
      if (action === "next") next();
      else if (action === "prev") prev();
    });
    document.addEventListener("keydown", function (ev: KeyboardEvent) {
      if (ev.key === "ArrowRight" || ev.key === "PageDown") next();
      else if (ev.key === "ArrowLeft" || ev.key === "PageUp") prev();
    });
  }

  window.addEventListener("message", function (ev: MessageEvent) {
    const data = ev.data as InitMessage | null;
    if (!data || typeof data !== "object") return;
    if (data.type === "classroom_init") {
      if (typeof data.nonce !== "string" || data.nonce !== nonce) return;
      const ports = (ev as MessageEvent & { ports?: MessagePort[] }).ports;
      if (!ports || !ports.length) return;
      port = ports[0];
      port.onmessage = function (msg: MessageEvent) {
        const cmd = msg.data as PortMessage;
        if (!cmd || typeof cmd !== "object") return;
        if (cmd.type === "goto_page" && typeof cmd.order === "number") {
          show(cmd.order);
        } else if (cmd.type === "set_block_state") {
          setBlockState(cmd.visible || [], cmd.focus || []);
        } else if (cmd.type === "set_reading") {
          setReading(!!cmd.reading);
        } else if (cmd.type === "measure") {
          measure(slides[current]);
        }
      };
      // 内置控件让位给父页面控制
      const controls = document.getElementById("controls");
      if (controls) controls.style.display = "none";
      send({ type: "initialized" });
      show(current + 1);
    }
  });

  document.addEventListener("click", function (ev: Event) {
    let target = ev.target as HTMLElement | null;
    while (target && target !== document.body) {
      if (target.classList && target.classList.contains("source-mark")) {
        const sid = target.getAttribute("data-source-id");
        if (sid) send({ type: "source_clicked", source_id: sid });
        return;
      }
      target = target.parentElement as HTMLElement | null;
    }
  });

  window.addEventListener("resize", function () {
    const slide = slides[current];
    if (slide) scaleStage(slide);
  });

  function boot(): void {
    queryDom();
    nonce = makeNonce();
    if (mode === "offline") offlineControls();
    show(1);
    if (window.parent !== window) {
      send({ type: "classroom_ready", nonce: nonce });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
