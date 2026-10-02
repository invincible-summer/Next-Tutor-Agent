/* Inspect a saved synthetic revision offline. No network or model calls. */
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { chromium } from 'playwright';
const [revisionArg, outputArg] = process.argv.slice(2);
if (!revisionArg || !outputArg) throw new Error('Usage: node scripts/inspect-classroom-course.mjs revision.json output-dir');
const revision = resolve(revisionArg);
const output = resolve(outputArg);
mkdirSync(output, { recursive: true });
const python = process.env.E2E_PYTHON || 'python3';
const hostStyle = readFileSync('src/components/classroom/presentation-style.ts', 'utf8').split('`')[1];
const browser = await chromium.launch({ headless: true });
const results = [];
try {
  for (const theme of ['academic_clear@2', 'chalk_focus@2']) {
    const label = theme.startsWith('chalk') ? 'dark' : 'light';
    const html = execFileSync(python, ['-c', `
import sys
from pathlib import Path
from app.schemas.classroom import LessonRevision
from app.classroom.render.compiler import compile_html
r = LessonRevision.model_validate_json(Path(sys.argv[1]).read_text())
r.brief.theme_id = sys.argv[2]
print(compile_html(r, mode='online'))
`, revision, theme], { cwd: '../../services/api', encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 });
    writeFileSync(join(output, `course-${label}.html`), html);
    const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
    await page.route('**/*', route => route.abort());
    await page.setContent(html.replace('</head>', `<style>${hostStyle}</style></head>`));
    const count = await page.locator('#viewport .slide').count();
    for (const width of [1280, 960]) {
      await page.setViewportSize({ width, height: width * 9 / 16 });
      for (let index = 0; index < count; index++) {
        await page.evaluate(index => {
          document.querySelectorAll('#viewport .slide').forEach((s, i) => s.classList.toggle('current', i === index));
          window.dispatchEvent(new Event('resize'));
        }, index);
        await page.waitForFunction(() => document.querySelector('.slide.current')?.dataset.layoutReady === '1');
        await page.evaluate(() => document.fonts.ready);
        const metrics = await page.locator('.slide.current').evaluate(slide => {
          const body = slide.querySelector('.body-area');
          const blocks = [...body.children];
          const focal = body.querySelector('[data-focal]');
          const formulaFonts = [...slide.querySelectorAll('.formula-box')].map(el => parseFloat(getComputedStyle(el).fontSize));
          return { title: slide.querySelector('h1').textContent, composition: slide.dataset.composition,
            overflow: slide.classList.contains('layout-overflow') || body.scrollHeight > body.clientHeight + 2 || body.scrollWidth > body.clientWidth + 2,
            nestedOverflow: blocks.some(block => block.scrollHeight > block.clientHeight + 2 || block.scrollWidth > block.clientWidth + 2),
            overlaps: blocks.some((a, i) => blocks.slice(i + 1).some(b => {
              const x = a.getBoundingClientRect(), y = b.getBoundingClientRect();
              return Math.min(x.right, y.right) - Math.max(x.left, y.left) > 2 &&
                Math.min(x.bottom, y.bottom) - Math.max(x.top, y.top) > 2;
            })),
            focalKind: focal?.className.split(' ').find(cls => cls !== 'block'),
            focalWidth: focal?.getBoundingClientRect().width / body.getBoundingClientRect().width,
            minimumFormulaFont: formulaFonts.length ? Math.min(...formulaFonts) : null,
            blockKinds: blocks.map(block => block.className.split(' ').find(cls => cls !== 'block')),
            math: slide.querySelectorAll('[data-katex]').length,
            mathErrors: slide.querySelectorAll('[data-katex]:not([data-rendered="1"])').length,
            bodyFont: getComputedStyle(slide).getPropertyValue('--body-size') };
        });
        results.push({ theme: label, width, page: index + 1, ...metrics });
        await page.screenshot({ animations: 'disabled', path: join(output, `${label}-${width}-${index + 1}.png`) });
      }
    }
    await page.pdf({ path: join(output, `course-${label}.pdf`), preferCSSPageSize: true });
    results.push({ theme: label, printError: await page.evaluate(() => document.documentElement.dataset.printError) });
    const sheet = await browser.newPage({ viewport: { width: 1280, height: Math.ceil(count / 2) * 380 } });
    const pictures = Array.from({ length: count }, (_, i) => `<figure><img src="data:image/png;base64,${readFileSync(join(output, `${label}-1280-${i + 1}.png`)).toString('base64')}"><figcaption>${i + 1}</figcaption></figure>`);
    await sheet.setContent(`<style>body{margin:0;background:#9aa0a5;display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:8px}figure{margin:0}img{width:100%}figcaption{text-align:center;font:16px sans-serif}</style>${pictures.join('')}`);
    await sheet.screenshot({ path: join(output, `overview-${label}.png`), fullPage: true });
    await sheet.close();
    await page.close();
  }
  writeFileSync(join(output, 'metrics.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results));
} finally { await browser.close(); }
