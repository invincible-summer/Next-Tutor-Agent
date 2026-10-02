/** Render every shipped asset in Chromium; export review sheets and bounds. */
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { chromium } from "playwright";

const root = resolve(import.meta.dirname, "../..");
const output = process.argv[2] ? resolve(process.argv[2]) : mkdtempSync(`${tmpdir()}/diagram-review-`);
const parameters = process.argv.includes("--parameters");
mkdirSync(output, { recursive: true });
const exported = JSON.parse(execFileSync(process.env.E2E_PYTHON || "python3", ["-c", `
import json
from app.diagrams.catalog import catalog
from app.diagrams.compiler import preview_asset
from app.diagrams.schema import DiagramError
rows=[]; rejected=[]
for a in catalog()[1].values():
 d=a.draw(preview=True)
 preview_asset(a.id)
 preview_asset(a.id,profile='monochrome')
 rows.append({'id':a.id,'title':a.title,'english':a.english,'svg':d.svg(),'monochrome_svg':a.draw(preview=True,monochrome=True).svg()})
 if ${parameters ? "True" : "False"}:
  for key,spec in a.parameter_schema().items():
   endpoints=[spec['minimum'],spec['maximum']] if spec['type'] in {'number','integer'} else [False,True] if spec['type']=='boolean' else spec.get('choices',[])
   for value in endpoints:
    try:
     d=a.draw({key:value},preview=True)
     preview_asset(a.id,{key:value})
     preview_asset(a.id,{key:value},profile='monochrome')
     rows.append({'id':a.id+'::'+key+'='+str(value),'title':a.title+' / '+key+'='+str(value),'english':a.english,'svg':d.svg(),'monochrome_svg':a.draw({key:value},preview=True,monochrome=True).svg()})
    except DiagramError as exc:
     # Cross-parameter constraints may reject an endpoint with other defaults.
     rejected.append({'id':a.id,'parameter':key,'value':value,'code':str(exc)})
print(json.dumps({'rows':rows,'rejected':rejected},ensure_ascii=False))
`], { cwd: root, env: { ...process.env, PYTHONPATH: `${root}/backend` }, maxBuffer: 80*1024*1024 }));
const { rows, rejected } = exported;
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1160 } });
const escape = value => value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");
const bounds = [];
try {
  for (let start=0; start<rows.length; start+=40) {
    const batch = rows.slice(start, start+40);
    await page.setContent(`<style>*{box-sizing:border-box}body{margin:0;padding:16px;background:#eff2f5;font:12px sans-serif;color:#26364a}.grid{display:grid;grid-template-columns:repeat(8,1fr);gap:10px}.card{background:white;border:1px solid #dce3ea;border-radius:10px;overflow:hidden;height:220px}.art{height:177px;padding:8px}svg{display:block;width:100%;height:100%}.name{padding:0 8px;white-space:nowrap;overflow:hidden}.id{padding:4px 8px;color:#66788d;font-size:10px}</style><div class="grid">${batch.map(a => `<div class="card" data-id="${escape(a.id)}"><div class="art">${a.svg}</div><div class="name">${escape(a.title)}</div><div class="id">${escape(a.id)}</div></div>`).join("")}</div>`);
    await page.evaluate(() => document.fonts.ready);
    bounds.push(...await page.evaluate(() => [...document.querySelectorAll(".card")].flatMap(card => {
      const svg = card.querySelector("svg"), box=svg.getBBox(), viewport=svg.viewBox.baseVal;
      return box.x < -1 || box.y < -1 || box.x+box.width > viewport.width+1 || box.y+box.height > viewport.height+1
        ? [{ id: card.dataset.id, box: { x: box.x, y: box.y, width: box.width, height: box.height }, viewport: { width: viewport.width, height: viewport.height } }] : [];
    })));
    await page.screenshot({ path: `${output}/sheet-${String(start/40+1).padStart(2,"0")}.png`, fullPage: true });
    await page.evaluate(pictures => {
      for (const picture of pictures) document.querySelector(`[data-id="${picture.id}"] .art`).innerHTML = picture.monochrome_svg;
    }, batch);
    bounds.push(...await page.evaluate(() => [...document.querySelectorAll(".card")].flatMap(card => {
      const svg=card.querySelector("svg"), box=svg.getBBox(), viewport=svg.viewBox.baseVal;
      return box.x < -1 || box.y < -1 || box.x+box.width > viewport.width+1 || box.y+box.height > viewport.height+1
        ? [{ id: card.dataset.id, profile: "monochrome", box: { x: box.x, y: box.y, width: box.width, height: box.height } }] : [];
    })));
    await page.screenshot({ path: `${output}/monochrome-${String(start/40+1).padStart(2,"0")}.png`, fullPage: true });
  }
  writeFileSync(`${output}/review.json`, JSON.stringify({ count: rows.length, profiles: ["textbook", "monochrome"], renderFailures: 0, bounds, rejected }, null, 2));
  console.log(JSON.stringify({ output, count: rows.length, bounds, rejected }, null, 2));
  if (bounds.length) process.exitCode = 1;
} finally { await browser.close(); }
