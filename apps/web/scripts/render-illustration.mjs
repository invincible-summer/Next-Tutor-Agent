/** Fixed offline Chromium renderer shared by instance metrics and PNG review. */
import { chromium } from "playwright";

let input = "";
for await (const chunk of process.stdin) {
  input += chunk;
  if (Buffer.byteLength(input) > 4 * 1024 * 1024) throw new Error("render_budget_exceeded");
}
const request = JSON.parse(input);
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 960, height: 640 }, deviceScaleFactor: request.clip ? 2 : 1 });
  await page.route("**/*", route => route.abort());
  if (request.mode === "measure") {
    const bounds = [];
    for (const svg of request.svgs ?? []) {
      await page.setContent(`<style>body{margin:0;font-family:sans-serif}</style>${svg}`);
      await page.evaluate(() => document.fonts.ready);
      bounds.push(await page.evaluate(() => {
        const box = document.querySelector("svg").getBBox();
        return [box.x, box.y, box.width, box.height];
      }));
    }
    const textMetrics = await page.evaluate(texts => texts.map(({ text, size }) => {
      const canvas = document.createElement("canvas");
      const context = canvas.getContext("2d");
      context.font = `${size}px sans-serif`;
      const metric = context.measureText(text);
      return { width: metric.width, ascent: metric.actualBoundingBoxAscent, descent: metric.actualBoundingBoxDescent };
    }), request.texts ?? []);
    process.stdout.write(JSON.stringify({ bounds, textMetrics, renderer: "chromium-v2" }));
  } else if (request.mode === "label_clearance") {
    const clear = await page.evaluate(async request => {
      const canvas = document.createElement("canvas");
      canvas.width = request.width;
      canvas.height = request.height;
      const context = canvas.getContext("2d", { willReadFrequently: true });
      context.fillStyle = "white";
      context.fillRect(0, 0, canvas.width, canvas.height);
      const image = new Image();
      image.src = `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(request.svg)))}`;
      await image.decode();
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      return request.candidates.map(([x, y, width, height]) => {
        const left = Math.floor(x - 3), top = Math.floor(y - 3);
        const w = Math.ceil(width + 6), h = Math.ceil(height + 6);
        if (left < 4 || top < 4 || left + w > canvas.width - 4 || top + h > canvas.height - 4) return false;
        const pixels = context.getImageData(left, top, w, h).data;
        for (let index = 0; index < pixels.length; index += 4) {
          if (Math.min(pixels[index], pixels[index + 1], pixels[index + 2]) < 235) return false;
        }
        return true;
      });
    }, request);
    process.stdout.write(JSON.stringify({ clear, renderer: "chromium-v2" }));
  } else if (request.mode === "render") {
    await page.setViewportSize({ width: request.width, height: request.height });
    await page.setContent(`<style>html,body{margin:0;background:white}svg{display:block;width:100%;height:100%}</style>${request.svg}`);
    await page.evaluate(() => document.fonts.ready);
    const box = await page.evaluate(() => {
      const b = document.querySelector("svg").getBBox();
      return [b.x, b.y, b.width, b.height];
    });
    const png = await page.screenshot({ type: "png", ...(request.clip ? { clip: request.clip } : {}) });
    process.stdout.write(JSON.stringify({ png: png.toString("base64"), bounds: box, renderer: "chromium-v2" }));
  } else throw new Error("unsupported_render_mode");
} finally {
  await browser.close();
}
