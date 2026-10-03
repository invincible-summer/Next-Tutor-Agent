/* 课堂 frame runtime。
 *
 * 独立 DOM runtime：仅接页切换、块显隐、高亮、主题指令；
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
      let tex = (el.getAttribute("data-katex") || "").trim();
      tex = tex.replace(/^```(?:latex|tex|math)?\s*\n([\s\S]*?)\n```$/, "$1").trim();
      for (const [left, right] of [["$$", "$$"], ["\\[", "\\]"], ["\\(", "\\)"], ["$", "$"]]) {
        if (tex.startsWith(left) && tex.endsWith(right)) { tex = tex.slice(left.length, -right.length).trim(); break; }
      }
      tex = tex.replace(/\\begin\{(?:equation\*?|displaymath)\}/g, "")
        .replace(/\\end\{(?:equation\*?|displaymath)\}/g, "")
        .replace(/\\(begin|end)\{align\*?\}/g, "\\$1{aligned}")
        .replace(/\\label\{[^{}]*\}|\\nonumber\b|\\notag\b/g, "");
      try {
        const engine = getKatex();
        if (engine && tex) {
          engine.render(tex, el, {
            trust: false,
            strict: "ignore",
            maxSize: 25,
            maxExpand: 200,
            displayMode: el.classList.contains("formula-box")
          });
        } else if (tex) {
          el.textContent = tex;
        }
      } catch {
        el.textContent = tex; // 公式渲染失败兜底为可读原文
      }
      el.setAttribute("data-rendered", el.querySelector(".katex") ? "1" : "error");
    }
  }

  function fitMath(slide: HTMLElement): void {
    slide.querySelectorAll<SVGSVGElement>("svg.diagram").forEach((svg) => {
      const ratio = Math.min(svg.clientWidth / svg.viewBox.baseVal.width,
        svg.clientHeight / svg.viewBox.baseVal.height);
      if (!ratio) return;
      // Axis labels must remain readable when the plot occupies half a slide.
      svg.querySelectorAll<SVGTextElement>("text.axis-label,text.tick-label").forEach((label) => {
        label.style.fontSize = Math.max(20, 16 / ratio) + "px";
      });
      svg.querySelectorAll<HTMLElement>(".diagram-math-label").forEach((label) => {
        label.style.fontSize = Math.max(20, 18 / ratio) + "px";
      });
      svg.querySelectorAll<SVGTextElement>(".edge-label,.body-label,.force-label").forEach((label) => {
        label.style.fontSize = Math.max(24, 14 / ratio) + "px";
      });
      svg.querySelectorAll<SVGTextElement>(".flow-node-label[data-flow-label]").forEach((label) => {
        const node = label.previousElementSibling as SVGRectElement | null;
        if (!node?.matches("rect.flow-node")) return;
        const size = Math.max(32, 16 / ratio);
        const text = Array.from(label.dataset.flowLabel || "");
        const perLine = Math.max(1, Math.floor((node.width.baseVal.value - 18) / size));
        const lines = [];
        for (let i = 0; i < text.length; i += perLine) lines.push(text.slice(i, i + perLine).join(""));
        const cx = node.x.baseVal.value + node.width.baseVal.value / 2;
        const cy = node.y.baseVal.value + node.height.baseVal.value / 2;
        label.style.fontSize = size + "px";
        label.replaceChildren(...lines.map((line, i) => {
          const span = document.createElementNS("http://www.w3.org/2000/svg", "tspan");
          span.setAttribute("x", String(cx));
          span.setAttribute("y", String(cy + (i - (lines.length - 1) / 2) * size + size * .35));
          span.textContent = line;
          return span;
        }));
      });
    });
    slide.querySelectorAll<HTMLElement>("[data-katex]").forEach((el) => {
      el.style.fontSize = "";
      el.style.paddingBottom = "";
      const math = el.querySelector<HTMLElement>(".katex-html");
      if (!math) return;
      math.style.whiteSpace = "";
      const container = el.closest<HTMLElement>(".diagram-math-label,td,th,.step-body,li,.formula-box,.block,h1") || el;
      const css = getComputedStyle(container);
      const width = container.clientWidth - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight) - 4;
      const stage = el.closest<HTMLElement>(".stage");
      const scale = stage ? stage.getBoundingClientRect().width / stage.offsetWidth : 1;
      const actual = Math.max(math.scrollWidth, math.getBoundingClientRect().width / (scale || 1));
      // Keep authored equation rows intact. A long equation needs a wider
      // composition or an explicit aligned environment, never CSS glyph wraps.
      if (actual > width && width > 0) {
        el.style.fontSize = (parseFloat(getComputedStyle(el).fontSize) * width / actual) + "px";
      }
      // Deep fractions/limits can extend below KaTeX's inline box. Reserve
      // their measured depth so the following line/block cannot cover them.
      if (el.classList.contains("span-math") && el.clientHeight > 0
          && el.scrollHeight > el.clientHeight + 1) {
        el.style.paddingBottom = (el.scrollHeight - el.clientHeight + 1) + "px";
      }
    });
  }

  function fits(body: HTMLElement): boolean {
    return body.scrollHeight <= body.clientHeight + 2 && body.scrollWidth <= body.clientWidth + 2
      && Array.from(body.children).every((block) =>
        block.scrollHeight <= block.clientHeight + 2 && block.scrollWidth <= block.clientWidth + 2)
      && Array.from(body.querySelectorAll<HTMLElement>(".formula-box[data-katex]")).every((el) =>
        parseFloat(getComputedStyle(el).fontSize) >= 20);
  }

  function contentHeight(body: HTMLElement): number {
    const scale = (body.closest(".stage")?.getBoundingClientRect().width || 1280) / 1280;
    const top = body.getBoundingClientRect().top;
    return Math.max(0, ...Array.from(body.children).map((el) =>
      ((el as HTMLElement).hidden ? 0 : (el.getBoundingClientRect().bottom - top) / scale)));
  }

  function chooseComposition(slide: HTMLElement, body: HTMLElement): void {
    if (slide.dataset.composition !== "auto") return;
    const blocks = Array.from(body.children) as HTMLElement[];
    const focal = blocks.find((block) => block.dataset.focal === "1");
    const media = blocks.find((block) => block.matches(".image,.diagram"));
    // Keep DOM/narration order, including a later visual's explanation.
    if (focal && blocks.length > 1 && !focal.dataset.wide) {
      slide.dataset.composition = "sidebar";
    } else if (media === blocks[0] && blocks.length > 1 && !media?.dataset.wide) {
      slide.dataset.composition = "sidebar";
    } else if (slide.dataset.layout === "compare" && blocks.length > 1) {
      slide.dataset.composition = "columns";
    } else if (blocks.length >= 3 && !blocks.some((block) => block.matches(".code,.steps"))) {
      slide.dataset.composition = "editorial";
    } else slide.dataset.composition = "stack";
  }

  function arrangeSidebar(slide: HTMLElement, body: HTMLElement): void {
    if (slide.dataset.composition !== "sidebar" || body.children.length < 2
        || body.querySelector('[data-wide="1"]')) return;
    const blocks = Array.from(body.children) as HTMLElement[];
    const focal = blocks.find((block) => block.dataset.focal === "1") || blocks[0];
    slide.classList.add("layout-sidebar");
    const scale = (slide.querySelector<HTMLElement>(".stage")?.getBoundingClientRect().width || 1280) / 1280;
    const gap = parseFloat(getComputedStyle(slide).getPropertyValue("--block-gap")) || 20;
    const bottoms = [0, 0];
    blocks.forEach((block) => {
      const col = block === focal ? 0 : 1;
      const height = Math.ceil(block.getBoundingClientRect().height / scale);
      block.style.gridArea = (bottoms[col] + 1) + " / " + (col + 1)
        + " / span " + height + " / " + (col + 2);
      bottoms[col] += height + gap;
    });
  }

  // A SlideSpec is one physical slide. Never slice text, clone blocks, or
  // invent hidden subpages: narration, navigation and export share that unit.
  function layoutSlide(slide: HTMLElement): void {
    if (!document.documentElement.dataset.rendererVersion?.startsWith("2.")) return;
    if (!slide.classList.contains("current") || document.fonts.status === "loading") return;
    if (Array.from(slide.querySelectorAll("img")).some((img) => !img.complete)) return;
    const body = slide.querySelector<HTMLElement>(".body-area");
    if (!body) return;
    slide.style.setProperty("--side-rows", String(Math.max(1, body.children.length - 1)));
    if (!slide.dataset.preferredComposition) {
      chooseComposition(slide, body);
      slide.dataset.preferredComposition = slide.dataset.composition;
    }
    slide.dataset.composition = slide.dataset.preferredComposition;
    slide.classList.remove("layout-roomy", "layout-compact", "layout-dense", "layout-overflow", "layout-balanced", "layout-sidebar");
    Array.from(body.children).forEach((block) => {
      (block as HTMLElement).style.gridArea = "";
    });
    const heading = slide.querySelector<HTMLElement>("h1");
    if (heading) {
      heading.style.fontSize = "";
      while (heading.offsetHeight > 110 && parseFloat(getComputedStyle(heading).fontSize) > 30) {
        heading.style.fontSize = (parseFloat(getComputedStyle(heading).fontSize) - 2) + "px";
      }
    }
    fitMath(slide);
    arrangeSidebar(slide, body);
    if (fits(body)) {
      if (contentHeight(body) < body.clientHeight * .65) {
        slide.classList.add("layout-roomy");
        fitMath(slide);
        arrangeSidebar(slide, body);
        if (!fits(body)) {
          slide.classList.remove("layout-roomy");
          fitMath(slide);
          arrangeSidebar(slide, body);
        }
      }
    } else {
      // Try a better arrangement at the original type size before reducing it.
      const preferred = slide.dataset.preferredComposition || "stack";
      const focal = body.querySelector<HTMLElement>('[data-focal="1"]:not([data-wide="1"])');
      const wide = body.querySelector('[data-wide="1"]');
      const candidates = Array.from(new Set([preferred, ...(focal ? ["sidebar"] : []),
        ...(wide ? ["editorial"] : []), "columns", "sidebar", "stack"]));
      let found = false;
      for (const density of ["normal", "compact", "dense"]) {
        slide.classList.toggle("layout-compact", density !== "normal");
        slide.classList.toggle("layout-dense", density === "dense");
        slide.classList.remove("layout-balanced");
        Array.from(body.children).forEach((block) => { (block as HTMLElement).style.gridArea = ""; });
        for (const candidate of candidates) {
          slide.dataset.composition = candidate;
          slide.classList.remove("layout-sidebar");
          Array.from(body.children).forEach((block) => { (block as HTMLElement).style.gridArea = ""; });
          fitMath(slide);
          arrangeSidebar(slide, body);
          if (fits(body)) { found = true; break; }
        }
        if (found) break;
        // Independent columns preserve whole components without equal-row gaps.
        slide.classList.add("layout-balanced");
        slide.classList.remove("layout-sidebar");
        slide.dataset.composition = "columns";
        const bottoms = [0, 0];
        Array.from(body.children).forEach((node) => {
          const block = node as HTMLElement;
          const wide = block.dataset.wide === "1";
          const col = wide || bottoms[0] <= bottoms[1] ? 0 : 1;
          const start = wide ? Math.max(...bottoms) : bottoms[col];
          const endColumn = wide ? 3 : col + 2;
          block.style.gridArea = "auto / " + (col + 1) + " / auto / " + endColumn;
          fitMath(slide);
          const height = Math.ceil(block.getBoundingClientRect().height /
            ((slide.querySelector<HTMLElement>(".stage")?.getBoundingClientRect().width || 1280) / 1280));
          block.style.gridArea = (start + 1) + " / " + (col + 1) + " / span " + height + " / " + endColumn;
          bottoms[col] = start + height + 14;
          if (wide) bottoms[1] = bottoms[0];
        });
        if (fits(body)) { found = true; break; }
      }
      if (!found) {
        slide.classList.remove("layout-balanced", "layout-sidebar");
        Array.from(body.children).forEach((block) => { (block as HTMLElement).style.gridArea = ""; });
        slide.dataset.composition = preferred;
        // Legacy overfull slides remain intact and are reported by the checker.
        slide.classList.add("layout-overflow");
        fitMath(slide);
      }
    }
    slide.dataset.layoutReady = "1";
  }

  function prepareLayout(slide: HTMLElement): void {
    renderMath(slide);
    scaleStage(slide);
    layoutSlide(slide);
    void Promise.all([
      document.fonts.ready,
      ...Array.from(slide.querySelectorAll("img")).map((img) => img.decode().catch(() => {}))
    ]).then(() => layoutSlide(slide));
  }

  function scaleStage(slide: HTMLElement): void {
    const stage = slide.querySelector<HTMLElement>(".stage");
    if (!stage) return;
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
    prepareLayout(slide);
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
      el.classList.toggle("pending", !visSet["*"] && !visSet[id]);
      el.classList.toggle("focus", !!focSet[id]);
    }
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

  document.addEventListener("keydown", function (ev: KeyboardEvent) {
    if (mode !== "online" || !port || ev.altKey || ev.ctrlKey || ev.metaKey) return;
    const target = ev.target as HTMLElement | null;
    if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return;
    if ([" ", "ArrowLeft", "ArrowRight", "c", "C", "f", "F", "q", "Q"].indexOf(ev.key) < 0) return;
    ev.preventDefault();
    send({ type: "hotkey", key: ev.key });
  });

  window.addEventListener("resize", function () {
    const slide = document.querySelector<HTMLElement>(".slide.current");
    if (slide) prepareLayout(slide);
  });

  function makePrintPages(): void {
    if (!document.documentElement.dataset.rendererVersion?.startsWith("2.")) return;
    document.getElementById("print-pages")?.remove();
    document.documentElement.classList.add("print-measuring");
    const container = document.createElement("div");
    container.id = "print-pages";
    const selected = slides.find((slide) => slide.classList.contains("current"));
    const viewport = document.getElementById("viewport")!;
    viewport.style.setProperty("display", "flex", "important");
    slides.forEach((slide) => {
      slides.forEach((other) => other.classList.toggle("current", other === slide));
      renderMath(slide);
      layoutSlide(slide);
      const copy = slide.cloneNode(true) as HTMLElement;
      copy.querySelectorAll(".pending,.focus").forEach((el) => el.classList.remove("pending", "focus"));
      container.appendChild(copy);
    });
    slides.forEach((slide) => slide.classList.toggle("current", slide === selected));
    viewport.style.removeProperty("display");
    const overfull = Array.from(container.querySelectorAll<HTMLElement>(".layout-overflow"))
      .map((slide) => slide.dataset.order || "?");
    document.documentElement.dataset.printError = overfull.join("、");
    if (overfull.length) {
      // Never silently crop a legacy overfull page into a seemingly valid PDF.
      container.replaceChildren();
      const notice = document.createElement("p");
      notice.textContent = document.documentElement.lang.startsWith("en")
        ? "Cannot print: pages " + overfull.join(", ") + " exceed the page size. Simplify the content in the editor or regenerate the slides before printing."
        : "无法打印：第 " + overfull.join("、") + " 页内容超出单页容量。请在编辑器中精简内容或重新生成课件后打印。";
      notice.style.cssText = "padding:64px;font:28px/1.6 sans-serif;color:#222";
      container.appendChild(notice);
    }
    document.body.appendChild(container);
    document.documentElement.classList.remove("print-measuring");
  }

  async function preparePrint(): Promise<void> {
    slides.forEach(renderMath);
    await Promise.all([document.fonts.ready, ...Array.from(document.images).map((img) => img.decode().catch(() => {}))]);
    makePrintPages();
    document.documentElement.dataset.printReady = "1";
  }
  window.addEventListener("beforeprint", makePrintPages);
  window.addEventListener("afterprint", () => {
    const slide = slides.find((item) => item.classList.contains("current"));
    if (slide) prepareLayout(slide);
  });

  function boot(): void {
    queryDom();
    nonce = makeNonce();
    if (mode === "offline") offlineControls();
    show(1);
    if (mode === "print") void preparePrint();
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
