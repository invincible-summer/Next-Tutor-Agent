import type { RichBlock, RichInline, RichListItem } from "./parser";
import { MATH_CSS, MATH_JS } from "./math-assets";
export const escapeHtml = (s: string) =>
  s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
export function safeContentLink(url: string): boolean {
  return /^https?:\/\/[^\s]+$/i.test(url) && url.length <= 2048;
}
function inline(nodes: RichInline[]): string {
  return nodes
    .map((n) => {
      switch (n.type) {
        case "text":
          return escapeHtml(n.text);
        case "break":
          return "<br>";
        case "code":
          return "<code>" + escapeHtml(n.text) + "</code>";
        case "math":
          return '<span data-math="' + escapeHtml(n.tex) + '"></span>';
        case "bold":
          return "<strong>" + inline(n.children) + "</strong>";
        case "italic":
          return "<em>" + inline(n.children) + "</em>";
        case "strike":
          return "<del>" + inline(n.children) + "</del>";
        case "link":
          return safeContentLink(n.url)
            ? '<a href="' + escapeHtml(n.url) + '">' + inline(n.label) + "</a>"
            : inline(n.label);
      }
    })
    .join("");
}
function list(items: RichListItem[]): string {
  // Individual values retain explicit numbering and mixed ordered/unordered lists.
  return items
    .map((item) => {
      const tag = item.ordered ? "ol" : "ul";
      const start = item.ordered
        ? ' start="' + (Number.parseInt(item.marker, 10) || 1) + '"'
        : "";
      return (
        "<" +
        tag +
        start +
        "><li>" +
        inline(item.content) +
        list(item.children) +
        "</li></" +
        tag +
        ">"
      );
    })
    .join("");
}
export function richHtml(blocks: RichBlock[]): string {
  return blocks
    .map((b) => {
      switch (b.type) {
        case "paragraph":
          return "<p>" + inline(b.children) + "</p>";
        case "heading":
          return (
            "<h" + b.level + ">" + inline(b.children) + "</h" + b.level + ">"
          );
        case "code":
          return "<pre><code>" + escapeHtml(b.code) + "</code></pre>";
        case "hr":
          return "<hr>";
        case "quote":
          return "<blockquote>" + richHtml(b.blocks) + "</blockquote>";
        case "math":
          return (
            '<div class="math" data-display="1" data-math="' +
            escapeHtml(b.tex) +
            '"></div>'
          );
        case "list":
          return list(b.items);
        case "table":
          return (
            '<div class="table"><table><thead><tr>' +
            b.header
              .map(
                (h, i) =>
                  '<th style="text-align:' +
                  (b.align[i] ?? "left") +
                  '">' +
                  inline(h) +
                  "</th>",
              )
              .join("") +
            "</tr></thead><tbody>" +
            b.rows
              .map(
                (row) =>
                  "<tr>" +
                  row
                    .map(
                      (c, i) =>
                        '<td style="text-align:' +
                        (b.align[i] ?? "left") +
                        '">' +
                        inline(c) +
                        "</td>",
                    )
                    .join("") +
                  "</tr>",
              )
              .join("") +
            "</tbody></table></div>"
          );
      }
    })
    .join("");
}
export function hasMath(blocks: RichBlock[]): boolean {
  const span = (n: RichInline): boolean =>
    n.type === "math" ||
    ("children" in n && n.children.some(span)) ||
    (n.type === "link" && n.label.some(span));
  const itemMath = (i: RichListItem): boolean =>
    i.content.some(span) || i.children.some(itemMath);
  return blocks.some(
    (b) =>
      b.type === "math" ||
      ("children" in b && b.children.some(span)) ||
      (b.type === "quote" && hasMath(b.blocks)) ||
      (b.type === "table" &&
        [...b.header, ...b.rows.flat()].some((c) => c.some(span))) ||
      (b.type === "list" && b.items.some(itemMath)),
  );
}
export function mathDocument(
  blocks: RichBlock[],
  nonce: string,
  color: string,
  background: string,
  size: number,
): string {
  const script = `function send(v){window.ReactNativeWebView.postMessage(JSON.stringify(Object.assign({nonce:${JSON.stringify(nonce)}},v)));}document.querySelectorAll('[data-math]').forEach(function(e){try{katex.render(e.dataset.math,e,{displayMode:e.dataset.display==='1',throwOnError:false,trust:false,strict:'warn',maxExpand:1000});}catch(err){e.textContent=e.dataset.math;}});document.addEventListener('click',function(e){var a=e.target.closest('a');if(a){e.preventDefault();send({type:'link',url:a.getAttribute('href')});}});function measure(){send({type:'height',height:Math.ceil(document.documentElement.scrollHeight)});}new ResizeObserver(measure).observe(document.body);document.fonts.ready.then(measure);measure();`;
  return (
    '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src &apos;none&apos;; script-src &apos;nonce-' +
    nonce +
    '&apos;; style-src &apos;unsafe-inline&apos;; font-src data:; img-src &apos;none&apos;; connect-src &apos;none&apos;; base-uri &apos;none&apos;"><style>' +
    MATH_CSS +
    `body{margin:0;background:${background};color:${color};font: ${size}px/1.6 -apple-system,Roboto,sans-serif;overflow-wrap:anywhere}p{margin:0 0 12px}h1,h2,h3,h4{line-height:1.4}pre,.table,.math{overflow-x:auto}pre{padding:12px;border:1px solid currentColor;border-radius:12px}code{font-family:monospace}table{border-collapse:collapse}th,td{border:1px solid #a5aba9;padding:8px}a{color:inherit}blockquote{padding-left:12px;border-left:3px solid #77968e}.katex{font-size:1.05em}` +
    '</style><script nonce="' +
    nonce +
    '">' +
    MATH_JS +
    "</script></head><body>" +
    richHtml(blocks) +
    '<script nonce="' +
    nonce +
    '">' +
    script +
    "</script></body></html>"
  );
}
