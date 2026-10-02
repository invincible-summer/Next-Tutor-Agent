"""Content-led compositions on a single 1280×720 canvas.

Design intent is validated data. Geometry, typography and containment belong to
this compiler and the measured runtime, shared by playback and HTML/PDF exports.
"""

V2_CSS = """
.slide[data-composition] { --body-size: 27px; --block-gap: 20px; }
.slide[data-composition] .stage {
  padding: 42px 56px 28px; border-radius: 0;
  background: var(--cc-bg); box-shadow: none;
}
.slide[data-composition] .stage::before {
  content: ''; position: absolute; top: 0; left: 56px;
  height: 6px; width: 76px; background: var(--cc-accent);
}
.slide[data-composition] .stage h1 {
  flex: none; font-size: 44px; line-height: 1.2; margin: 6px 0 26px;
  max-width: 1120px; letter-spacing: -.025em; text-wrap: balance;
  border: 0; padding: 0;
}
.slide[data-composition] .body-area {
  display: grid; grid-template-columns: minmax(0, 1fr);
  grid-auto-rows: min-content; align-content: start; align-items: start;
  gap: var(--block-gap); flex: 1; min-height: 0; overflow: hidden;
  padding: 3px 4px 6px;
}
.slide[data-composition] .block {
  font-size: var(--body-size); line-height: 1.5; padding: 16px 20px;
  border: 1px solid transparent; border-radius: 10px; box-shadow: none;
  background: transparent; min-width: 0; max-width: 100%; overflow: visible;
  overflow-wrap: anywhere;
}
.slide[data-surface="soft"] .block { background: var(--cc-surface); }
.slide[data-surface="outlined"] .block { border-color: var(--cc-border); }
.slide[data-composition] .block.paragraph { padding: 8px 4px; }
.slide[data-composition] .block.paragraph p { white-space: pre-wrap; }
.slide[data-composition] .span-em { text-decoration: none; font-weight: 700; }
.slide[data-composition] .span-math { display: inline-block; max-width: 100%; }
.slide[data-composition] .block.bullets ul { gap: 12px; }
.slide[data-composition] .block.bullets li { padding-left: 24px; }
.slide[data-composition] .block.bullets li::before {
  height: 7px; width: 7px; top: .65em; border-radius: 50%;
}
.slide[data-composition] .block.callout {
  background: transparent; border: 0;
  border-left: 3px solid var(--cc-accent); padding: 16px 22px;
}
.slide[data-composition] .block[data-emphasis="1"] {
  background: var(--cc-accent-soft); border: 0;
  border-left: 4px solid var(--cc-accent); padding: 20px 24px;
}
.slide[data-composition] .block.formula { background: transparent; }
.slide[data-composition] .block[data-focal="1"].formula { background: var(--cc-surface-alt); }
.slide[data-composition] .block[data-focal="1"] .formula-box { font-size: 38px; }
.slide[data-composition] .block[data-focal="1"].paragraph { font-size: 32px; }
.slide[data-composition] .block[data-focal="1"].table table { font-size: 26px; }
.slide[data-composition] .formula-box { font-size: 30px; padding: 8px 4px; }
.slide[data-composition] .formula-label { font-size: 18px; }
.slide[data-composition] .katex-display { margin: .2em 0; }
/* TeX remains atomic; never wrap glyph bases or break fractions in CSS. */
.slide[data-composition] .block.image { width: 100%; max-width: 520px; justify-self: center; }
.slide[data-composition] .block.image img { height: 240px; max-height: 260px; min-height: 0; }
.slide[data-composition] .image-missing { min-height: 160px; }
.slide[data-composition] figcaption { font-size: 18px; line-height: 1.4; margin-top: 8px; }
.slide[data-composition] .block.diagram { display: block; padding: 12px; background: var(--cc-surface); max-height: none; }
.flow-map { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.flow-card { min-width: 0; border: 1px solid var(--cc-border); background: var(--cc-accent-soft); padding: 16px; border-radius: 8px; }
.flow-card strong { font-size: 23px; line-height: 1.4; }
.flow-card ul { list-style: none; font-size: 18px; line-height: 1.45; margin-top: 8px; }
.slide[data-composition] svg.diagram { display: block; max-height: 350px; }
.slide[data-composition] .block[data-focal="1"] svg.diagram { max-height: 390px; }
.slide[data-composition] .block.table { padding: 0; }
.slide[data-composition] .block.table table { font-size: 23px; }
.slide[data-composition] .block.table th,
.slide[data-composition] .block.table td { padding: 12px 10px; }
.slide[data-composition] .block.steps { padding: 12px 4px; }
.slide[data-composition] .block.steps ol { gap: 16px; }
.slide[data-composition] .block.steps .step-label { flex: 0 1 23%; }
.slide[data-composition] .block.checkpoint { background: var(--cc-accent-soft); padding: 28px; }
.block.code { background: var(--cc-surface-alt) !important; border-color: var(--cc-border); }
.code-language { color: var(--cc-muted); font-size: 15px; letter-spacing: .08em; margin-bottom: 12px; }
.block.code pre { white-space: pre-wrap; overflow-wrap: anywhere; tab-size: 4; }
.block.code code { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 22px; line-height: 1.5; }
.code-line { display: inline; }
.slide[data-composition] .slide-footer {
  min-height: 30px; margin-top: 16px; padding-top: 10px; font-size: 14px;
}
.slide[data-composition] .page-no { font-variant-numeric: tabular-nums; }
.slide[data-composition="columns"] .body-area,
.slide[data-composition="editorial"] .body-area { grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 30px; }
.slide[data-composition="sidebar"] .body-area { grid-template-columns: minmax(0, 1.45fr) minmax(0, 1fr); column-gap: 32px; }
.slide[data-composition="sidebar"] .body-area > :first-child:not([data-wide="1"]) { grid-column: 1; grid-row: 1 / span var(--side-rows, 2); }
.slide[data-composition="sidebar"] .body-area > :not(:first-child):not([data-wide="1"]) { grid-column: 2; }
.slide[data-composition="sidebar"] .body-area:has(> [data-focal="1"]:not([data-wide="1"])) > :first-child:not([data-focal="1"]) { grid-row: auto; }
.slide[data-composition="sidebar"] .body-area:has(> [data-focal="1"]:not([data-wide="1"])) > :not([data-focal="1"]):not([data-wide="1"]) { grid-column: 2; }
.slide[data-composition="sidebar"] .body-area > [data-focal="1"]:not([data-wide="1"]) {
  grid-column: 1; grid-row: 1 / span var(--side-rows, 2);
}
.slide[data-composition="editorial"] .body-area > :first-child,
.slide[data-composition] .body-area > [data-wide="1"],
.slide[data-composition] .body-area > :only-child { grid-column: 1 / -1; }
.slide[data-composition="editorial"] .body-area > :first-child { border-bottom: 2px solid var(--cc-border); padding-bottom: 22px; }
.slide[data-composition][data-density="compact"],
.slide[data-composition].layout-compact { --block-gap: 12px; --body-size: 25px; }
.slide[data-composition][data-density="airy"] { --block-gap: 26px; --body-size: 30px; }
.slide[data-composition].layout-compact .block { padding-top: 10px; padding-bottom: 10px; line-height: 1.42; }
.slide[data-composition].layout-compact .block.bullets ul,
.slide[data-composition].layout-compact .block.steps ol { gap: 8px; }
.slide[data-composition].layout-compact .stage h1 { margin-bottom: 18px; }
.slide[data-composition].layout-compact .block[data-focal="1"] .formula-box { font-size: 32px; }
.slide[data-composition].layout-compact .block[data-focal="1"].paragraph { font-size: 27px; }
.slide[data-composition].layout-roomy { --block-gap: 28px; --body-size: 30px; }
.slide[data-composition].layout-roomy .body-area { align-content: center; }
.slide[data-composition].layout-roomy .block { padding-top: 16px; padding-bottom: 16px; }
.slide[data-theme="chalk_focus@2"] .stage { background-image: radial-gradient(#f5f1e609 1px, transparent 1px); background-size: 26px 26px; }
.slide[data-theme="lab_notebook@2"] .stage { background-image: linear-gradient(#0f766e09 1px, transparent 1px); background-size: 32px 32px; }
/* Keep paragraphs visually quiet; reserve a surface for the key result. */
.slide[data-composition] .block.paragraph { background: transparent; border-color: transparent; }
.slide[data-composition].layout-dense { --body-size: 22px; --block-gap: 10px; }
.slide[data-composition].layout-dense .block { padding-top: 8px; padding-bottom: 8px; line-height: 1.4; }
.slide[data-composition].layout-dense .block.steps ol,
.slide[data-composition].layout-dense .block.bullets ul { gap: 6px; }
.slide[data-composition].layout-dense .formula-box { font-size: 26px; padding: 4px; }
.slide[data-composition].layout-dense .block[data-focal="1"] .formula-box { font-size: 28px; }
.slide[data-composition].layout-dense .block[data-focal="1"].paragraph { font-size: var(--body-size); }
.slide[data-composition].layout-dense .block.table table { font-size: 20px; }
.slide[data-composition].layout-dense .block.code code { font-size: 20px; }
.slide[data-composition].layout-balanced .body-area,
.slide[data-composition].layout-sidebar .body-area { grid-auto-rows: 1px; row-gap: 0; }
.slide[data-composition].layout-balanced .body-area > .block,
.slide[data-composition].layout-sidebar .body-area > .block { align-self: start; }
.slide[data-composition].layout-overflow .body-area { overflow: auto; }
#print-pages { display: none; }
@media print {
  @page { size: 1280px 720px; margin: 0; }
  html body { height: auto; print-color-adjust: exact; -webkit-print-color-adjust: exact; }
  html.print-measuring #viewport .slide { display: none !important; }
  html.print-measuring #viewport .slide.current { display: block !important; }
  html.print-measuring #viewport .slide .stage { width: 1280px; height: 720px; min-height: 0; }
  html body #viewport, html body #controls { display: none !important; }
  #print-pages { display: block; }
  #print-pages .slide { display: block !important; break-after: page; break-inside: avoid; }
  #print-pages .slide:last-child { break-after: auto; }
  #print-pages .slide .stage { width: 1280px !important; height: 720px !important; min-height: 0; max-width: none; transform: none !important; margin: 0; }
  #print-pages .slide .body-area { overflow: hidden; }
  #print-pages .block.pending { visibility: visible; opacity: 1; }
  #print-pages .block.focus { outline: none; }
}
"""
