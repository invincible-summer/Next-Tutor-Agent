/** Print the standalone server export, outside the app's scroll/overlay tree.
 * KaTeX and its fonts are embedded by the server; images are frozen data URLs.
 * No global print rules or fixed-position paper can truncate later pages. */
export async function printWorksheetHtml(content: string): Promise<void> {
  const frame = document.createElement("iframe");
  frame.title = "Worksheet print";
  frame.setAttribute("aria-hidden", "true");
  frame.tabIndex = -1;
  Object.assign(frame.style, { position: "fixed", width: "0", height: "0", border: "0", left: "-10000px" });
  const loaded = new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error("worksheet_print_timeout")), 10000);
    frame.onload = () => { window.clearTimeout(timer); resolve(); };
    frame.onerror = () => { window.clearTimeout(timer); reject(new Error("worksheet_print_failed")); };
  });
  frame.srcdoc = content;
  document.body.append(frame);
  try {
    await loaded;
    const sheet = frame.contentDocument;
    const printWindow = frame.contentWindow;
    if (!sheet || !printWindow) throw new Error("worksheet_print_failed");
    await sheet.fonts.ready;
    await Promise.all(Array.from(sheet.images).map((image) => image.decode().catch(() => undefined)));
    const mathNodes = Array.from(sheet.querySelectorAll<HTMLElement>("[data-katex]"));
    const mathReady = mathNodes.every((node) => Boolean(node.querySelector(".katex")));
    if (!mathReady || sheet.querySelector(".katex-error")) throw new Error("worksheet_math_invalid");
    await new Promise<void>((resolve) => printWindow.requestAnimationFrame(() => resolve()));
    // Some browsers return from print() immediately. Keep the frame alive
    // until the dialog closes; the fallback also covers browsers without
    // afterprint when the user cancels the dialog.
    const cleanup = () => frame.remove();
    printWindow.addEventListener("afterprint", cleanup, { once: true });
    window.setTimeout(cleanup, 120000);
    printWindow.focus();
    printWindow.print();
  } catch (error) {
    frame.remove();
    throw error;
  }
}
