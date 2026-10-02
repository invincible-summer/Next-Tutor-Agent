/* Offline browser regression: non-code math, including SVG diagram labels. */
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { chromium } from 'playwright';
const python = process.env.E2E_PYTHON || 'python3';
const hostStyle = readFileSync('src/components/classroom/presentation-style.ts', 'utf8').split('`')[1];
const browser = await chromium.launch({ headless: true });
try {
  for (const theme of ['academic_clear@2', 'chalk_focus@2']) {
    const html = execFileSync(python, ['-c', `
from tests.test_classroom_inline_math import inline_math_revision
from app.classroom.render.compiler import compile_html
r = inline_math_revision()
r.brief.theme_id = '${theme}'
print(compile_html(r))
`], { cwd: '../../services/api', encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 });
    const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
    await page.route('**/*', route => route.abort());
    await page.setContent(html.replace('</head>', `<style>${hostStyle}</style></head>`));
    for (const width of [1280, 960]) {
      await page.setViewportSize({ width, height: width * 9 / 16 });
      for (let index = 0; index < 6; index++) {
        await page.evaluate(index => {
          document.querySelectorAll('#viewport .slide').forEach((s, i) => s.classList.toggle('current', i === index));
          window.dispatchEvent(new Event('resize'));
        }, index);
        await page.waitForFunction(() => document.querySelector('.slide.current')?.dataset.layoutReady === '1');
        await page.evaluate(() => document.fonts.ready);
        const math = await page.locator('.slide.current [data-katex]').evaluateAll(elements => elements.map(el => ({
          source: el.dataset.katex, rendered: el.dataset.rendered, glyphs: el.querySelector('.katex-html')?.textContent,
        })));
        assert.ok(math.length > 0);
        for (const item of math) {
          assert.equal(item.rendered, '1', JSON.stringify(item));
          assert.ok(item.glyphs, JSON.stringify(item));
        }
        if (index === 0) {
          const glyphs = await page.locator('.slide.current .paragraph .katex-html').allTextContents();
          assert.deepEqual(glyphs, ['δ', 'Δ', '△', '∇']);
        }
        assert.equal(await page.locator('.slide.current.layout-overflow').count(), 0, `${theme} slide ${index + 1} width ${width}`);
        const blockProblems = await page.locator('.slide.current .body-area > .block').evaluateAll(elements => {
          const problems = [];
          for (const [i, el] of elements.entries()) {
            if (el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2) problems.push(`overflow ${i}`);
            const a = el.getBoundingClientRect();
            for (const next of elements.slice(i + 1)) {
              const b = next.getBoundingClientRect();
              if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 2 &&
                  Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 2) problems.push(`overlap ${i}`);
            }
          }
          return problems;
        });
        assert.deepEqual(blockProblems, [], `${theme} slide ${index + 1}`);
        const overflow = await page.locator('.slide.current .diagram-math-label').evaluateAll(elements => elements.filter(el =>
          el.scrollWidth > el.clientWidth + 2 || el.scrollHeight > el.closest('foreignObject').height.baseVal.value + 2).length);
        assert.equal(overflow, 0, `${theme} slide ${index + 1}`);
      }
    }
    assert.equal(await page.locator('#viewport .code code').textContent(), String.raw`value = "\delta + $x^2$ + \Delta"`);
    assert.equal(await page.locator('#viewport .code code .katex').count(), 0);
    await page.close();
  }
  console.log('PASS: all non-code contexts, δ Δ △ ∇, literal code, light/dark, 1280/960');
} finally {
  await browser.close();
}
