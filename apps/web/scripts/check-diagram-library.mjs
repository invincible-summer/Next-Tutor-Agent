/** Render every shipped asset in Chromium; export review sheets and bounds. */
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { chromium } from "playwright";

const root = resolve(import.meta.dirname, "../../..");
const output = process.argv[2] ? resolve(process.argv[2]) : mkdtempSync(`${tmpdir()}/diagram-review-`);
const parameters = process.argv.includes("--parameters");
const individual = process.argv.includes("--individual");
const ids = process.argv.find(value => value.startsWith("--ids="))?.slice(6).split(",") || [];
mkdirSync(output, { recursive: true });
mkdirSync(`${output}/assets`, { recursive: true });
const exported = JSON.parse(execFileSync(process.env.E2E_PYTHON || "python3", ["-c", `
import json
from app.diagrams.catalog import catalog
from app.core.quiz_illustration import normalize_svg
from app.diagrams.provenance import source_record
from app.diagrams.schema import DiagramError
rows=[]; rejected=[]
for a in catalog()[1].values():
 if ${JSON.stringify(ids)} and a.id not in ${JSON.stringify(ids)}:
  continue
 d=a.draw(preview=True)
 # Offline audit may render stale sources; publication keeps its review gate.
 normalize_svg(d.svg(),components=True)
 normalize_svg(a.draw(preview=True,monochrome=True).svg(),components=True)
 rows.append({'id':a.id,'title':a.title,'english':a.english,'source_hash':source_record(a.id,a.renderer,a.variant)['source_hash'],'review':a.review,'svg':d.svg(),'monochrome_svg':a.draw(preview=True,monochrome=True).svg()})
 if ${parameters ? "True" : "False"}:
  for key,spec in a.parameter_schema().items():
   endpoints=[spec['minimum'],spec['maximum']] if spec['type'] in {'number','integer'} else [False,True] if spec['type']=='boolean' else spec.get('choices',[])
   for value in endpoints:
    try:
     d=a.draw({key:value},preview=True)
     normalize_svg(d.svg(),components=True)
     normalize_svg(a.draw({key:value},preview=True,monochrome=True).svg(),components=True)
     rows.append({'id':a.id+'::'+key+'='+str(value),'title':a.title+' / '+key+'='+str(value),'english':a.english,'svg':d.svg(),'monochrome_svg':a.draw({key:value},preview=True,monochrome=True).svg()})
    except DiagramError as exc:
     # Cross-parameter constraints may reject an endpoint with other defaults.
     rejected.append({'id':a.id,'parameter':key,'value':value,'code':str(exc)})
print(json.dumps({'rows':rows,'rejected':rejected},ensure_ascii=False))
`], { cwd: root, env: { ...process.env, EDU_TEST_KEYLESS: "1", PYTHONPATH: `${root}/services/api` }, maxBuffer: 80*1024*1024 }));
const { rows, rejected } = exported;
writeFileSync(`${output}/manifest.json`, JSON.stringify(rows.map(row => Object.fromEntries(
  Object.entries(row).filter(([key]) => !["svg", "monochrome_svg"].includes(key)))), null, 2));
for (const row of rows) {
  const name = row.id.replaceAll(/[^a-zA-Z0-9._-]/g, "_");
  writeFileSync(`${output}/assets/${name}.svg`, row.svg);
  writeFileSync(`${output}/assets/${name}-monochrome.svg`, row.monochrome_svg);
}
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1160 } });
const escape = value => value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");
const bounds = [];
const textIssues = [];
try {
  for (let start=0; start<rows.length; start+=40) {
    const batch = rows.slice(start, start+40);
    await page.setContent(`<style>*{box-sizing:border-box}body{margin:0;padding:16px;background:#eff2f5;font:12px sans-serif;color:#26364a}.grid{display:grid;grid-template-columns:repeat(8,1fr);gap:10px}.card{background:white;border:1px solid #dce3ea;border-radius:10px;overflow:hidden;height:220px}.art{height:177px;padding:8px}svg{display:block;width:100%;height:100%}.name{padding:0 8px;white-space:nowrap;overflow:hidden}.id{padding:4px 8px;color:#66788d;font-size:10px}</style><div class="grid">${batch.map(a => `<div class="card" data-id="${escape(a.id)}"><div class="art">${a.svg}</div><div class="name">${escape(a.title)}</div><div class="id">${escape(a.id)}</div></div>`).join("")}</div>`);
    await page.evaluate(() => document.fonts.ready);
    textIssues.push(...await page.evaluate(() => [...document.querySelectorAll(".card")].flatMap(card => {
      const nodes = [...card.querySelectorAll("text")];
      const result = [];
      for (let i=0; i<nodes.length; i++) {
        const a=nodes[i].getBoundingClientRect();
        for (let j=i+1; j<nodes.length; j++) {
          const b=nodes[j].getBoundingClientRect();
          if (Math.min(a.right,b.right)-Math.max(a.left,b.left)>0.5 && Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>0.5)
            result.push({id:card.dataset.id,texts:[nodes[i].textContent,nodes[j].textContent]});
        }
      }
      return result;
    })));
    bounds.push(...await page.evaluate(() => [...document.querySelectorAll(".card")].flatMap(card => {
      const svg = card.querySelector("svg"), box=svg.getBBox(), viewport=svg.viewBox.baseVal;
      return box.x < -1 || box.y < -1 || box.x+box.width > viewport.width+1 || box.y+box.height > viewport.height+1
        ? [{ id: card.dataset.id, box: { x: box.x, y: box.y, width: box.width, height: box.height }, viewport: { width: viewport.width, height: viewport.height } }] : [];
    })));
    await page.screenshot({ path: `${output}/sheet-${String(start/40+1).padStart(2,"0")}.png`, fullPage: true });
    if (individual) for (const row of batch) await page.locator(`[data-id="${row.id}"] .art`).screenshot({ path: `${output}/assets/${row.id.replaceAll(/[^a-zA-Z0-9._-]/g,"_")}.png` });
    await page.evaluate(pictures => {
      for (const picture of pictures) document.querySelector(`[data-id="${picture.id}"] .art`).innerHTML = picture.monochrome_svg;
    }, batch);
    bounds.push(...await page.evaluate(() => [...document.querySelectorAll(".card")].flatMap(card => {
      const svg=card.querySelector("svg"), box=svg.getBBox(), viewport=svg.viewBox.baseVal;
      return box.x < -1 || box.y < -1 || box.x+box.width > viewport.width+1 || box.y+box.height > viewport.height+1
        ? [{ id: card.dataset.id, profile: "monochrome", box: { x: box.x, y: box.y, width: box.width, height: box.height } }] : [];
    })));
    await page.screenshot({ path: `${output}/monochrome-${String(start/40+1).padStart(2,"0")}.png`, fullPage: true });
    if (individual) for (const row of batch) await page.locator(`[data-id="${row.id}"] .art`).screenshot({ path: `${output}/assets/${row.id.replaceAll(/[^a-zA-Z0-9._-]/g,"_")}-monochrome.png` });
  }
  writeFileSync(`${output}/review.json`, JSON.stringify({ count: rows.length, profiles: ["textbook", "monochrome"], renderFailures: 0, bounds, textIssues, rejected }, null, 2));
  console.log(JSON.stringify({ output, count: rows.length, bounds: bounds.length, textIssues: textIssues.length, rejected: rejected.length }, null, 2));
  if (bounds.length || textIssues.length) process.exitCode = 1;
} finally { await browser.close(); }
