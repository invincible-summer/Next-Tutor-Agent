"""Render docs/show/Next-Tutor-Agent-show.pdf into a portable HTML manual.

Two output shapes share one template:

- split assets (default):  docs/show/html/index.html + pages/p-NN.png
  served by GET /docs/show and GET /docs/show/pages/{n} (backend/app/api/v1/docs.py);
- --single-file OUT: one self-contained HTML (base64 PNGs) for static hosts
  such as GitHub Pages or the frontend demo bundle.

Visual fidelity is the priority (the deck uses embedded CJK font subsets and
custom layouts), so pages are rasterised at 2x instead of extracted as text.

Usage:
  python3 scripts/build_show_html.py                       # split assets
  python3 scripts/build_show_html.py --single-file OUT.html
"""
from __future__ import annotations

import argparse
import base64
import html
import sys
from pathlib import Path

import fitz  # PyMuPDF

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PDF = _PROJECT_ROOT / "docs" / "show" / "Next-Tutor-Agent-show.pdf"
_OUT_DIR = _PROJECT_ROOT / "docs" / "show" / "html"
_TITLE = "Next Tutor Agent · 项目展示"
_SCALE = 2  # 960x540 slides -> 1920x1080 PNGs

_PAGE_TEMPLATE = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #101418; color: #e7ecf2;
         font: 14px/1.6 system-ui, "PingFang SC", "Microsoft YaHei", sans-serif; }}
  header {{ position: sticky; top: 0; z-index: 10; display: flex; align-items: center; gap: 12px;
            padding: 10px 16px; background: rgba(16,20,24,.92); backdrop-filter: blur(6px);
            border-bottom: 1px solid #232a31; }}
  header h1 {{ font-size: 15px; font-weight: 600; margin: 0; flex: 1; min-width: 0;
               white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  header .tag {{ font-size: 11px; color: #8b97a3; white-space: nowrap; }}
  nav button {{ background: #1b2127; color: #e7ecf2; border: 1px solid #2a323a; border-radius: 6px;
                padding: 4px 12px; font-size: 13px; cursor: pointer; }}
  nav button:hover {{ border-color: #4d8dff; color: #4d8dff; }}
  nav input {{ width: 52px; background: #1b2127; color: #e7ecf2; border: 1px solid #2a323a;
               border-radius: 6px; padding: 4px 6px; font-size: 13px; text-align: center; }}
  main {{ max-width: 1080px; margin: 0 auto; padding: 20px 16px 48px; }}
  figure {{ margin: 0 0 28px; }}
  figure img {{ display: block; width: 100%; height: auto; border-radius: 8px;
                border: 1px solid #232a31; box-shadow: 0 8px 28px rgba(0,0,0,.35); }}
  figcaption {{ text-align: center; font-size: 12px; color: #8b97a3; margin-top: 8px; }}
  #progress {{ position: fixed; left: 0; top: 0; height: 2px; width: 0;
               background: #4d8dff; transition: width .15s; }}
  @media print {{
    header, #progress {{ display: none; }}
    body {{ background: #fff; }}
    figure img {{ border: none; box-shadow: none; }}
  }}
</style>
</head>
<body>
<div id="progress"></div>
<header>
  <h1>{title}</h1>
  <span class="tag">{page_count} 页 · PDF 手册</span>
  <nav>
    <button type="button" data-act="prev" aria-label="上一页">‹ 上一页</button>
    <input id="page-jump" type="number" min="1" max="{page_count}" value="1" aria-label="页码">
    <button type="button" data-act="next" aria-label="下一页">下一页 ›</button>
  </nav>
</header>
<main id="pages">
{figures}
</main>
<script>
(function () {{
  var total = {page_count};
  var pages = Array.prototype.slice.call(document.querySelectorAll("figure[data-page]"));
  var jump = document.getElementById("page-jump");
  var bar = document.getElementById("progress");
  function goTo(n) {{
    n = Math.min(Math.max(n, 1), total);
    var target = pages[n - 1];
    if (target) target.scrollIntoView({{ behavior: "smooth", block: "start" }});
    jump.value = n;
  }}
  document.querySelector("nav").addEventListener("click", function (e) {{
    var act = e.target.getAttribute && e.target.getAttribute("data-act");
    if (act === "prev") goTo(parseInt(jump.value, 10) - 1);
    if (act === "next") goTo(parseInt(jump.value, 10) + 1);
  }});
  jump.addEventListener("change", function () {{ goTo(parseInt(jump.value, 10) || 1); }});
  document.addEventListener("keydown", function (e) {{
    if (e.target === jump) return;
    if (e.key === "ArrowLeft" || e.key === "PageUp") goTo(parseInt(jump.value, 10) - 1);
    if (e.key === "ArrowRight" || e.key === "PageDown") goTo(parseInt(jump.value, 10) + 1);
  }});
  window.addEventListener("scroll", function () {{
    var h = document.documentElement;
    var ratio = h.scrollTop / Math.max(1, h.scrollHeight - h.clientHeight);
    bar.style.width = (ratio * 100).toFixed(2) + "%";
    var mid = h.scrollTop + h.clientHeight * 0.4, cur = 1;
    for (var i = 0; i < pages.length; i++) {{
      if (pages[i].offsetTop <= mid) cur = i + 1; else break;
    }}
    jump.value = cur;
  }}, {{ passive: true }});
}})();
</script>
</body>
</html>
"""


def _figure(page_no: int, src: str, page_count: int) -> str:
    return (
        f'<figure data-page="{page_no}" id="p{page_no}">'
        f'<img src="{src}" alt="第 {page_no} 页" loading="lazy" decoding="async">'
        f"<figcaption>{page_no} / {page_count}</figcaption>"
    )


def build(pdf: Path, out_dir: Path, *, single_file: Path | None, scale: int = _SCALE) -> None:
    doc = fitz.open(pdf)
    count = doc.page_count
    figures: list[str] = []
    blobs: list[bytes] = []
    for i, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
        png = pix.tobytes("png")
        blobs.append(png)
        if not single_file:
            pages_dir = out_dir / "pages"
            pages_dir.mkdir(parents=True, exist_ok=True)
            (pages_dir / f"p-{i:02d}.png").write_bytes(png)
            figures.append(_figure(i, f"pages/p-{i:02d}.png", count))
        else:
            b64 = base64.b64encode(png).decode("ascii")
            figures.append(_figure(i, f"data:image/png;base64,{b64}", count))
    html_text = _PAGE_TEMPLATE.format(
        title=html.escape(_TITLE), page_count=count, figures="\n".join(figures)
    )
    if single_file:
        single_file.parent.mkdir(parents=True, exist_ok=True)
        single_file.write_text(html_text, encoding="utf-8")
        print(f"single-file html -> {single_file} ({single_file.stat().st_size/1e6:.1f} MB)")
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "index.html").write_text(html_text, encoding="utf-8")
        total = sum(len(b) for b in blobs)
        print(f"split html -> {out_dir}/index.html, {len(blobs)} pages, {total/1e6:.1f} MB pngs")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--single-file", type=Path, default=None,
                    help="emit one self-contained HTML at this path instead of split assets")
    ap.add_argument("--pdf", type=Path, default=_PDF)
    args = ap.parse_args()
    if not args.pdf.exists():
        print(f"pdf not found: {args.pdf}", file=sys.stderr)
        return 1
    build(args.pdf, _OUT_DIR, single_file=args.single_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
