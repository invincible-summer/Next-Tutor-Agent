/** Host presentation polish also applies to previously published courseware.
 * Fixed CSS only: no lesson text, external URLs or script is interpolated. */
export const PRESENTATION_STYLE = `
html body { background: transparent; }
html body .stage { box-shadow: 0 4px 28px #00000008; }
html body .slide:not([data-composition]) .stage h1 { letter-spacing: -.025em; text-wrap: balance; }
html body .slide:not([data-composition]):not(.layout-title) .stage h1 { margin-bottom: 28px; }
html body .slide:not([data-composition]).layout-key_points .stage .body-area > .block.paragraph,
html body .slide:not([data-composition]).layout-key_points .stage .body-area > .block.bullets { grid-column: 1 / -1; }
html body .slide:not([data-composition]) .span-em { text-decoration: none; }
html body .slide:not([data-composition]) .stage .body-area { gap: 18px; scrollbar-width: thin; }
html body .slide:not([data-composition]) .stage .block { line-height: 1.7; transition: opacity .28s ease, background .28s ease, box-shadow .28s ease; }
html body .slide:not([data-composition]) .stage .block.paragraph { border-color: transparent; background: transparent; box-shadow: none; padding: 6px 4px; }
html body .slide:not([data-composition]) .stage .block.bullets { padding: 22px 26px; }
html body .slide:not([data-composition]) .stage .block.bullets ul { gap: 14px; }
html body .slide:not([data-composition]) .stage .block.focus { outline: none; box-shadow: inset 4px 0 0 var(--cc-accent); border-color: var(--cc-border); }
html body .slide:not([data-composition]) .stage .block.formula { padding: 24px 28px; border-color: transparent; }
html body .slide:not([data-composition]) .stage .block.formula .formula-box { padding: 12px 6px; }
html body .slide:not([data-composition]) .slide-footer { font-size: 14px; letter-spacing: .02em; }
html body .page-no { font-variant-numeric: tabular-nums; }
html body .kicker { font-size: 14px; letter-spacing: .14em; }
html body .slide:not([data-composition]).current .body-area { animation: lesson-reveal .3s ease both; }
@keyframes lesson-reveal { from { opacity: .3; translate: 0 6px; } to { opacity: 1; translate: 0 0; } }
@media (prefers-reduced-motion: reduce) { html body .slide:not([data-composition]).current .body-area { animation: none; } html body .slide:not([data-composition]) .stage .block { transition: none; } }
`;
