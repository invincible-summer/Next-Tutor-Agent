/** Host presentation polish also applies to previously published courseware.
 * Fixed CSS only: no lesson text, external URLs or script is interpolated. */
export const PRESENTATION_STYLE = `
html body { background: transparent; }
html body .stage { box-shadow: 0 4px 28px #00000008; }
html body .slide .stage h1 { letter-spacing: -.025em; text-wrap: balance; }
html body .slide:not(.layout-title) .stage h1 { margin-bottom: 28px; }
html body .slide.layout-key_points .stage .body-area > .block.paragraph,
html body .slide.layout-key_points .stage .body-area > .block.bullets { grid-column: 1 / -1; }
html body .span-em { text-decoration: none; }
html body .slide .stage .body-area { gap: 18px; scrollbar-width: thin; }
html body .slide .stage .block { line-height: 1.7; transition: opacity .28s ease, background .28s ease, box-shadow .28s ease; }
html body .slide .stage .block.paragraph { border-color: transparent; background: transparent; box-shadow: none; padding: 6px 4px; }
html body .slide .stage .block.bullets { padding: 22px 26px; }
html body .slide .stage .block.bullets ul { gap: 14px; }
html body .slide .stage .block.focus { outline: none; box-shadow: inset 4px 0 0 var(--cc-accent); border-color: var(--cc-border); }
html body .slide .stage .block.formula { padding: 24px 28px; border-color: transparent; }
html body .slide .stage .block.formula .formula-box { padding: 12px 6px; }
html body .slide-footer { font-size: 14px; letter-spacing: .02em; }
html body .page-no { font-variant-numeric: tabular-nums; }
html body .kicker { font-size: 14px; letter-spacing: .14em; }
html body .slide.current .body-area { animation: lesson-reveal .3s ease both; }
@keyframes lesson-reveal { from { opacity: .3; translate: 0 6px; } to { opacity: 1; translate: 0 0; } }
html[data-reading="1"] body #viewport .slide .stage { padding: 28px 24px; max-width: 880px; border-radius: 0; box-shadow: none; min-height: 100vh; }
html[data-reading="1"] body #viewport .slide .stage h1 { font-size: 28px; line-height: 1.4; margin: 8px 0 24px; }
html[data-reading="1"] body #viewport .slide .body-area { display: flex; flex-direction: column; gap: 16px; max-height: none; max-width: none; overflow: visible; }
html[data-reading="1"] body #viewport .slide .block { font-size: 17px; line-height: 1.85; padding: 16px; }
html[data-reading="1"] body #viewport .slide .block.formula .formula-box { font-size: 21px; overflow-x: auto; }
html[data-reading="1"] body #viewport .slide .block.image { min-height: 0; }
html[data-reading="1"] body #viewport .slide .block.image img { min-height: 0; max-height: 320px; }
html[data-reading="1"] body #viewport .slide .slide-footer { margin-top: 24px; font-size: 11px; }
@media (prefers-reduced-motion: reduce) { html body .slide.current .body-area { animation: none; } html body .slide .stage .block { transition: none; } }
`;
