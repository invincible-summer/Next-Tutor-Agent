/* Focused, offline renderer acceptance: one logical slide, no DOM slicing. */
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync } from 'node:fs';
import { chromium } from 'playwright';

const hostStyle = readFileSync('src/components/classroom/presentation-style.ts', 'utf8').split('`')[1];
const browser = await chromium.launch({ headless: true });
try {
  for (const theme of ['academic_clear@2', 'chalk_focus@2']) {
    const html = execFileSync(process.env.E2E_PYTHON || 'python3', ['-c', `
from tests.test_classroom_overflow import dense_revision
from app.classroom.render.compiler import compile_html
r = dense_revision()
r.brief.theme_id = '${theme}'
print(compile_html(r))
`], { cwd: '../../services/api', encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 });
    const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
    await page.route('**/*', (route) => route.abort());
    await page.setContent('<body style="margin:0"><iframe style="border:0;width:100vw;height:100vh"></iframe></body>');
    await page.evaluate((html) => {
      window.addEventListener('message', (ev) => {
        if (ev.data?.type !== 'classroom_ready') return;
        const channel = new MessageChannel();
        window.coursePort = channel.port1;
        ev.source.postMessage({ type: 'classroom_init', nonce: ev.data.nonce }, '*', [channel.port2]);
      });
      document.querySelector('iframe').srcdoc = html;
    }, html.replace("</head>", `<style>${hostStyle}</style></head>`));
    const frame = page.frames().find((f) => f.parentFrame());
    await frame.waitForFunction(() => document.querySelector('.slide')?.dataset.layoutReady === '1');
    await frame.evaluate(() => document.fonts.ready);
    assert.equal(await frame.locator('.slide').count(), 1);
    assert.equal(await frame.locator('.build-controls').count(), 0);
    assert.equal(await frame.locator('.paragraph').count(), 1, 'never clone or slice a paragraph');
    assert.equal(await frame.locator('.paragraph').textContent(), '明确系统边界，逐一分析外力、方向和作用时间，判断近似条件是否成立。'.repeat(48));
    assert.equal(await frame.locator('.layout-overflow').count(), 1, 'report excess instead of hiding it');
    assert.equal(await frame.locator('.formula .katex').count(), 1);
    await page.evaluate(() => window.coursePort.postMessage({ type: 'set_block_state', visible: ['*'], focus: ['blk_000000000000000000000002'] }));
    await frame.waitForFunction(() => document.querySelector('.formula').classList.contains('focus'));
    assert.equal(await frame.locator('.formula[hidden]').count(), 0);
    mkdirSync('../acceptance-reports/screenshots', { recursive: true });
    await page.screenshot({ path: `../acceptance-reports/screenshots/course-overflow-${theme.startsWith('chalk') ? 'dark' : 'light'}.png` });
    await page.setViewportSize({ width: 960, height: 540 });
    await frame.waitForFunction(() => Math.abs(document.querySelector(".stage").getBoundingClientRect().width - 960) < 2);
    const bounds = await frame.locator('.stage').boundingBox();
    assert.ok(bounds.x >= -1 && bounds.y >= -1 && bounds.x + bounds.width <= 961 && bounds.y + bounds.height <= 541);
    await page.evaluate(() => window.coursePort.postMessage({ type: 'set_reading', reading: true }));
    await page.waitForTimeout(50);
    assert.equal(await frame.evaluate(() => document.documentElement.dataset.reading), undefined, 'removed reading command cannot reflow slides');
    await page.close();
  }
  console.log('PASS: no slicing, overflow detection, intact math, narration focus, light/dark and narrow canvas');
} finally {
  await browser.close();
}
