/* Offline regressions for a later focal block, intact equations and full-width packing. */
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
from tests.test_classroom_composition import hierarchy_revision
from app.classroom.render.compiler import compile_html
r = hierarchy_revision()
r.brief.theme_id = '${theme}'
print(compile_html(r))
`], { cwd: '../../services/api', encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 });
    const page = await browser.newPage();
    await page.route('**/*', route => route.abort());
    await page.setContent(html.replace('</head>', `<style>${hostStyle}</style></head>`));
    for (const width of [1280, 960, 1280]) {
      await page.setViewportSize({ width, height: width * 9 / 16 });
      for (const index of [0, 1, 2]) {
        await page.evaluate(index => {
          document.querySelectorAll('#viewport .slide').forEach((slide, i) => slide.classList.toggle('current', i === index));
          window.dispatchEvent(new Event('resize'));
        }, index);
        await page.evaluate(() => document.fonts.ready);
        await page.waitForFunction(() => document.querySelector('.slide.current')?.dataset.layoutReady === '1');
        const metrics = await page.locator('.slide.current').evaluate(slide => {
          const body = slide.querySelector('.body-area');
          const blocks = [...body.children];
          const focal = body.querySelector('[data-focal]');
          const wide = body.querySelector('[data-wide]');
          return {
            mode: slide.dataset.composition,
            packed: slide.classList.contains('layout-balanced'),
            overflow: body.scrollHeight > body.clientHeight + 2 || body.scrollWidth > body.clientWidth + 2,
            nestedOverflow: blocks.some(b => b.scrollHeight > b.clientHeight + 2 || b.scrollWidth > b.clientWidth + 2),
            overlaps: blocks.some((a, i) => blocks.slice(i + 1).some(b => {
              const x = a.getBoundingClientRect(), y = b.getBoundingClientRect();
              return Math.min(x.right, y.right) - Math.max(x.left, y.left) > 2 &&
                Math.min(x.bottom, y.bottom) - Math.max(x.top, y.top) > 2;
            })),
            focalWidth: focal?.getBoundingClientRect().width / body.getBoundingClientRect().width,
            focalIndex: blocks.indexOf(focal),
            sideGap: blocks.length === 3 ? (blocks[2].getBoundingClientRect().top -
              blocks[1].getBoundingClientRect().bottom) / (slide.querySelector('.stage').getBoundingClientRect().width / 1280) : null,
            wideWidth: wide?.getBoundingClientRect().width / body.getBoundingClientRect().width,
            math: [...body.querySelectorAll('.formula-box')].map(el => ({
              size: parseFloat(getComputedStyle(el).fontSize),
              whiteSpace: getComputedStyle(el.querySelector('.katex-html')).whiteSpace,
              rendered: el.dataset.rendered
            }))
          };
        });
        assert.equal(metrics.overflow, false, JSON.stringify(metrics));
        assert.equal(metrics.nestedOverflow, false, JSON.stringify(metrics));
        assert.equal(metrics.overlaps, false, JSON.stringify(metrics));
        if (index === 0) {
          assert.equal(metrics.mode, 'sidebar');
          assert.equal(metrics.focalIndex, 1, 'keep teaching DOM order');
          assert.ok(metrics.focalWidth > .55, 'give the data table more space than its explanation');
        } else if (index === 1) {
          assert.equal(metrics.packed, true, 'exercise the fallback that used to discard wide intent');
          assert.ok(metrics.wideWidth > .95, 'wide equation must stay wide after packing');
          for (const math of metrics.math) {
            assert.ok(math.size >= 20, JSON.stringify(math));
            assert.equal(math.whiteSpace, 'nowrap', 'preserve authored equation rows');
            assert.equal(math.rendered, '1');
          }
        } else {
          assert.equal(metrics.focalIndex, 0);
          assert.ok(metrics.sideGap >= 0 && metrics.sideGap <= 30, 'a tall first focal block must not create a gap between its explanations');
        }
      }
    }
    await page.close();
  }
  console.log('PASS: later focal table, wide fallback, equation readability, no overlap, light/dark, repeated resize');
} finally { await browser.close(); }
