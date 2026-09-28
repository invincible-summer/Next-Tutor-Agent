"""v2 classroom presentation styles.

The v1 compiler remains intact for published lessons. New lessons reuse the
same safe block renderer and frame protocol, with layout-specific composition
and an intentionally scrollable body when a page contains substantial text.
"""

V2_CSS = """
.slide[data-theme$="@2"] .stage {
  border-radius: 18px;
  padding: 42px 56px 30px;
  background: var(--cc-bg);
  box-shadow: inset 0 0 0 1px var(--cc-border);
}
.slide[data-theme$="@2"] .stage::before {
  content: "";
  position: absolute;
  left: 56px;
  top: 0;
  width: 80px;
  height: 7px;
  border-radius: 0 0 7px 7px;
  background: var(--cc-accent);
}
.slide[data-theme$="@2"] .stage h1 {
  position: relative;
  flex: none;
  max-width: 1080px;
  margin: 8px 0 22px;
  font-size: calc(var(--cc-fs-title) * .95);
  line-height: 1.22;
  letter-spacing: -.025em;
  border: 0;
  padding: 0;
}
.slide[data-theme$="@2"] .body-area {
  min-height: 0;
  gap: 14px;
  padding: 2px 12px 10px 2px;
  overflow: auto;
  scrollbar-color: var(--cc-border) transparent;
  scrollbar-width: thin;
  align-content: start;
}
.slide[data-theme$="@2"] .block {
  flex-shrink: 0;
  border: 1px solid var(--cc-border);
  border-radius: 12px;
  padding: 15px 20px;
  background: var(--cc-surface);
  font-size: calc(var(--cc-fs-body) * .86);
  line-height: 1.48;
  overflow: visible;
  box-shadow: 0 3px 12px rgba(0, 0, 0, .025);
}
.slide[data-theme$="@2"] .block p { display: inline; }
.slide[data-theme$="@2"] .block.bullets ul { gap: 7px; }
.slide[data-theme$="@2"] .block.bullets li { padding-left: 25px; }
.slide[data-theme$="@2"] .block.bullets li::before {
  width: 8px; height: 8px; top: .65em; border-radius: 50%;
}
.slide[data-theme$="@2"] .block.callout {
  border-left: 5px solid var(--cc-accent);
  background: var(--cc-accent-soft);
  box-shadow: none;
}
.slide[data-theme$="@2"] .block.formula {
  background: var(--cc-surface-alt);
}
.slide[data-theme$="@2"] .block.formula .formula-box {
  font-size: calc(var(--cc-fs-body) * 1.02);
}
.slide[data-theme$="@2"] .slide-footer {
  min-height: 32px;
  margin-top: 12px;
  padding-top: 8px;
  font-size: 15px;
}
.slide[data-theme$="@2"] .page-no {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 66px;
  height: 28px;
  border: 1px solid var(--cc-border);
  border-radius: 20px;
  font-variant-numeric: tabular-nums;
}

/* Nine layouts compose the same trusted blocks in different reading orders. */
.slide.layout-title[data-theme$="@2"] .stage {
  justify-content: center;
  padding: 70px 100px;
}
.slide.layout-title[data-theme$="@2"] .stage h1 {
  max-width: 950px;
  margin: auto 0 18px;
  font-size: calc(var(--cc-fs-title) * 1.65);
  line-height: 1.15;
}
.slide.layout-title[data-theme$="@2"] .body-area {
  flex: none;
  max-height: 285px;
  max-width: 860px;
}
.slide.layout-title[data-theme$="@2"] .block {
  border: 0;
  border-left: 4px solid var(--cc-accent);
  border-radius: 0;
  padding: 4px 0 4px 20px;
  background: transparent;
  box-shadow: none;
}
.slide.layout-title[data-theme$="@2"] .slide-footer { margin-top: auto; }
.slide.layout-key_points[data-theme$="@2"] .body-area {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  grid-auto-rows: min-content;
}
.slide.layout-key_points[data-theme$="@2"] .body-area > :only-child,
.slide.layout-key_points[data-theme$="@2"] .body-area > .block.formula,
.slide.layout-key_points[data-theme$="@2"] .body-area > .block.table,
.slide.layout-key_points[data-theme$="@2"] .body-area > .block.diagram {
  grid-column: 1 / -1;
}
.slide.layout-image_explain[data-theme$="@2"] .body-area {
  display: grid;
  grid-template-columns: 1.18fr 1fr;
  grid-auto-rows: min-content;
}
.slide.layout-image_explain[data-theme$="@2"] .block.image {
  grid-column: 1; grid-row: 1 / span 8;
  min-height: 430px;
}
.slide.layout-image_explain[data-theme$="@2"] .block.image img {
  min-height: 300px; max-height: 400px;
}
.slide.layout-compare[data-theme$="@2"] .body-area {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  grid-auto-rows: min-content;
}
.slide.layout-compare[data-theme$="@2"] .block.table { grid-column: 1 / -1; }
.slide.layout-derivation[data-theme$="@2"] .block.formula {
  border-left: 5px solid var(--cc-accent);
  padding: 20px 26px;
}
.slide.layout-worked_example[data-theme$="@2"] .block.steps {
  border-left: 5px solid var(--cc-accent);
}
.slide.layout-timeline[data-theme$="@2"] .block.bullets {
  border: 0; background: transparent; box-shadow: none;
  padding: 6px 16px;
}
.slide.layout-timeline[data-theme$="@2"] .block.bullets ul {
  border-left: 4px solid var(--cc-border); gap: 12px;
}
.slide.layout-timeline[data-theme$="@2"] .block.bullets li {
  margin-left: -11px; padding: 10px 16px 10px 29px;
  border-radius: 10px; background: var(--cc-surface);
}
.slide.layout-timeline[data-theme$="@2"] .block.bullets li::before {
  left: 0; width: 14px; height: 14px;
  border: 3px solid var(--cc-bg);
}
.slide.layout-checkpoint[data-theme$="@2"] .body-area,
.slide.layout-summary[data-theme$="@2"] .body-area {
  justify-content: center;
}
.slide.layout-checkpoint[data-theme$="@2"] .block.checkpoint {
  border: 2px solid var(--cc-accent);
  border-radius: 20px;
  padding: 32px 40px;
}
.slide.layout-summary[data-theme$="@2"] .block.bullets {
  border-top: 5px solid var(--cc-accent);
}
.slide[data-theme="chalk_focus@2"] .stage {
  background-image: radial-gradient(rgba(245,241,230,.045) 1px, transparent 1px);
  background-size: 26px 26px;
}
.slide[data-theme="lab_notebook@2"] .stage {
  background-image: linear-gradient(rgba(15,118,110,.045) 1px, transparent 1px),
    linear-gradient(90deg,rgba(15,118,110,.045) 1px,transparent 1px);
  background-size: 32px 32px;
}
html[data-reading="1"] .slide[data-theme$="@2"] .stage {
  width: 100%; max-width: 880px; padding: 32px 24px;
  border-radius: 0; box-shadow: none;
}
html[data-reading="1"] .slide[data-theme$="@2"] .body-area {
  display: flex; flex-direction: column; max-height: none; overflow: visible;
}
html[data-reading="1"] .slide[data-theme$="@2"] .block {
  font-size: 22px;
}
html[data-reading="1"] .slide.layout-title[data-theme$="@2"] .stage h1 {
  font-size: 46px;
}
@media print {
  @page { size: 13.333in 7.5in; margin: 0; }
  .slide[data-theme$="@2"] { break-after: page; }
  .slide[data-theme$="@2"] .stage {
    width: 100%; min-height: 100vh; height: auto;
    border-radius: 0; box-shadow: none;
    break-inside: auto;
  }
  .slide[data-theme$="@2"] .body-area {
    overflow: visible; min-height: 0;
  }
  .slide[data-theme$="@2"] .block {
    break-inside: avoid;
  }
}
"""
