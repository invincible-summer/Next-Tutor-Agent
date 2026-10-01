/* Offline visual acceptance for composition, safe code and equal-size PDF pages. */
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { chromium } from 'playwright';

const python = process.env.E2E_PYTHON || 'python3';
const hostStyle = readFileSync('src/components/classroom/presentation-style.ts', 'utf8').split('`')[1];
function fixture(name, theme = 'academic_clear@2', mode = 'online') {
  return execFileSync(python, ['-c', `
import json
from tests.test_classroom_composition import gallery_revision, code_revision
from app.classroom.render.compiler import compile_html
r = ${name}()
r.brief.theme_id = '${theme}'
print(json.dumps({'html': compile_html(r, mode='${mode}'), 'code': [b.code for s in r.slides for b in s.blocks if b.kind == 'code']}))
`], { cwd: '../backend', encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 });
}
const browser = await chromium.launch({ headless: true });
const temp = mkdtempSync(join(tmpdir(), 'classroom-composition-'));
try {
  mkdirSync('../acceptance-reports/screenshots', { recursive: true });
  for (const theme of ['academic_clear@2', 'chalk_focus@2']) {
    const data = JSON.parse(fixture('gallery_revision', theme));
    const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
    await page.route('**/*', route => route.abort());
    await page.setContent(data.html.replace('</head>', `<style>${hostStyle}</style></head>`));
    for (let index = 0; index < 5; index++) {
      await page.evaluate(index => {
        document.querySelectorAll('#viewport .slide').forEach((slide, i) => slide.classList.toggle('current', i === index));
        window.dispatchEvent(new Event('resize'));
      }, index);
      await page.waitForFunction(() => document.querySelector('.slide.current')?.dataset.layoutReady === '1');
      await page.evaluate(() => document.fonts.ready);
      const metrics = await page.evaluate(() => {
        const slide = document.querySelector('.slide.current');
        const stage = slide.querySelector('.stage');
        const body = slide.querySelector('.body-area');
        return { mode: slide.dataset.composition, builds: Number(slide.dataset.buildCount) || 1,
          width: stage.offsetWidth, height: stage.offsetHeight,
          overflow: body.scrollHeight > body.clientHeight + 2 || body.scrollWidth > body.clientWidth + 2 };
      });
      assert.deepEqual([metrics.width, metrics.height], [1280, 720]);
      assert.equal(metrics.overflow, false, JSON.stringify(metrics));
      assert.equal(metrics.builds, 1, `ordinary content should fit one page: ${index + 1}`);
      if (index < 4) assert.equal(metrics.mode, ['editorial', 'columns', 'sidebar', 'stack'][index], 'respect fitting model composition');
      await page.screenshot({ animations: 'disabled', path: `../acceptance-reports/screenshots/course-composition-${index + 1}-${theme.startsWith('chalk') ? 'dark' : 'light'}.png` });
    }
    await page.setViewportSize({ width: 960, height: 540 });
    await page.waitForFunction(() => Math.abs(document.querySelector('.slide.current .stage').getBoundingClientRect().width - 960) < 2);
    assert.equal(await page.locator('#viewport .code code').textContent(), data.code[0]);
    assert.equal(await page.locator('img').count(), 0, 'a useful course does not require illustration');
    await page.close();
  }
  const code = JSON.parse(fixture('code_revision'));
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  await page.route('**/*', route => route.abort());
  await page.setContent(code.html);
  await page.waitForFunction(() => document.querySelector('.slide')?.dataset.layoutReady === '1');
  assert.equal(await page.locator('.build-controls').count(), 0);
  assert.equal(await page.locator('#viewport .code').count(), 1, 'never split a code block');
  assert.equal(await page.locator('#viewport .code code').textContent(), code.code[0]);
  assert.equal(await page.locator('.layout-overflow').count(), 1, 'diagnose overfull source');
  await page.pdf({ path: join(temp, 'invalid.pdf'), preferCSSPageSize: true });
  assert.equal(await page.evaluate(() => document.documentElement.dataset.printError), '1');
  assert.equal(await page.locator('#print-pages > .slide').count(), 0, 'never export silently cropped source');
  assert.match(await page.locator('#print-pages').textContent(), /无法打印/);
  await page.close();
  // The dedicated print endpoint must prepare every logical page as well.
  const printPage = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  await printPage.setContent(JSON.parse(fixture('gallery_revision', 'academic_clear@2', 'print')).html);
  await printPage.waitForSelector('#print-pages', { state: 'attached' });
  assert.equal(await printPage.locator('#print-pages > .slide').count(), 5);
  await printPage.pdf({ path: join(temp, 'gallery.pdf'), preferCSSPageSize: true });
  assert.equal(await printPage.evaluate(() => document.documentElement.dataset.printError), '');
  const sizes = JSON.parse(execFileSync(python, ['-c', `
import fitz,json,sys
with fitz.open(sys.argv[1]) as pdf:
    print(json.dumps([[p.rect.width,p.rect.height] for p in pdf]))
`, join(temp, 'gallery.pdf')], { encoding: 'utf8' }));
  assert.equal(sizes.length, 5, 'one physical page per source slide');
  for (const size of sizes) assert.deepEqual(size, [960, 540]);
  await printPage.close();
  console.log('PASS: five compositions, light/dark, narrow canvas, intact code, overflow diagnostics, fixed-size PDF');
} finally {
  await browser.close();
  rmSync(temp, { recursive: true, force: true });
}
