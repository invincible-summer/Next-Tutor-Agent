/** Keep the trusted compiler document and its CSP intact inside an opaque sandbox. */
export function classroomDocument(html: string, nonce: string): string {
  if (!/^[a-zA-Z0-9-]{8,128}$/.test(nonce))
    throw new Error("invalid_frame_nonce");
  const hashes = [...new Set(html.match(/'sha256-[A-Za-z0-9+/=]+'/g) ?? [])];
  if (!hashes.length || !html.includes("Content-Security-Policy"))
    throw new Error("untrusted_classroom_document");
  // srcdoc inherits its parent's CSP. Admit only the compiler's published script hashes.
  const csp = `default-src 'none'; frame-src about:; script-src 'nonce-${nonce}' ${hashes.join(" ")}; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'`;
  const document = JSON.stringify(html).replace(/</g, "\\u003c");
  const script = `var frame=document.getElementById('lesson'),port=null;function send(data){window.ReactNativeWebView.postMessage(JSON.stringify({nonce:${JSON.stringify(nonce)},payload:data}));}window.addEventListener('message',function(e){var d=e.data;if(e.source!==frame.contentWindow||!d||d.type!=='classroom_ready'||typeof d.nonce!=='string'||d.nonce.length>256)return;if(port)port.close();var ch=new MessageChannel();port=ch.port1;port.onmessage=function(m){var v=m.data;if(!v||typeof v!=='object')return;if(v.type==='page_selected'&&Number.isInteger(v.order)&&typeof v.slide_id==='string')send({type:'page_selected',order:v.order,slide_id:v.slide_id});};port.start();frame.contentWindow.postMessage({type:'classroom_init',nonce:d.nonce},'*',[ch.port2]);send({type:'ready'});});window.__ntGoto=function(n,order){if(n!==${JSON.stringify(nonce)}||!Number.isInteger(order)||order<1)return;if(port)port.postMessage({type:'goto_page',order:order});};frame.srcdoc=${document};window.addEventListener('pagehide',function(){if(port)port.close();});`;
  return `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="${csp.replace(/"/g, "&quot;")}"><style>html,body,iframe{width:100%;height:100%;margin:0;border:0;overflow:hidden}</style></head><body><iframe id="lesson" title="Lesson slides" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe><script nonce="${nonce}">${script}</script></body></html>`;
}
export function readFrameMessage(
  raw: string,
  nonce: string,
  slides: { slide_id: string; order: number }[],
):
  | { type: "ready" }
  | { type: "page_selected"; order: number; slide_id: string }
  | null {
  if (raw.length > 4096) return null;
  try {
    const v = JSON.parse(raw);
    if (v.nonce !== nonce || !v.payload || typeof v.payload !== "object")
      return null;
    const p = v.payload;
    if (p.type === "ready") return { type: "ready" };
    if (
      p.type === "page_selected" &&
      Number.isInteger(p.order) &&
      slides.some((s) => s.order === p.order && s.slide_id === p.slide_id)
    )
      return { type: "page_selected", order: p.order, slide_id: p.slide_id };
  } catch {}
  return null;
}
export function checkpointQuestionRef(
  question: Record<string, unknown>,
): string | null {
  const id = question.question_id;
  const revision = question.question_revision;
  if (
    typeof id !== "string" ||
    !id ||
    typeof revision !== "number" ||
    !Number.isInteger(revision) ||
    revision < 1
  )
    return null;
  return `${id}:${revision}`;
}
