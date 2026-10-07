import { File, Paths } from "expo-file-system";
import * as Sharing from "expo-sharing";
import { registerSessionCleanup } from "@/lib/session-lifecycle";
const temporary = new Set<File>();
export function safeFilename(name: string): string {
  return (
    name
      .replace(/[^\p{L}\p{N}._ -]/gu, "_")
      .replace(/^\.+/, "")
      .slice(0, 100) || "download"
  );
}
export async function shareBytes(
  bytes: ArrayBuffer | string,
  name: string,
  mimeType: string,
): Promise<void> {
  const file = new File(Paths.cache, `nt-${Date.now()}-${safeFilename(name)}`);
  temporary.add(file);
  try {
    file.write(typeof bytes === "string" ? decodeDataUrl(bytes) ?? bytes : new Uint8Array(bytes));
    if (!(await Sharing.isAvailableAsync()))
      throw new Error("sharing_unavailable");
    await Sharing.shareAsync(file.uri, { mimeType, dialogTitle: name });
  } finally {
    if (file.exists) file.delete();
    temporary.delete(file);
  }
}

/** Convert image data URLs to real bytes before sharing with another app. */
function decodeDataUrl(value: string): Uint8Array | null {
  const match = value.match(/^data:[^;,]+;base64,(.*)$/s);
  if (!match) return null;
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  const output: number[] = [];
  let buffer = 0;
  let bits = 0;
  const encoded = match[1];
  if (encoded === undefined) return null;
  for (const char of encoded.replace(/\s/g, "")) {
    if (char === "=") break;
    const digit = alphabet.indexOf(char);
    if (digit < 0) return null;
    buffer = (buffer << 6) | digit;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      output.push((buffer >> bits) & 0xff);
    }
  }
  return new Uint8Array(output);
}
export function deletePickedFiles(uris: string[]) {
  for (const uri of uris) {
    if (!uri.startsWith(Paths.cache.uri)) continue;
    try {
      const file = new File(uri);
      if (file.exists) file.delete();
    } catch {}
  }
}
registerSessionCleanup(() => {
  for (const f of temporary) {
    try {
      if (f.exists) f.delete();
    } catch {}
  }
  temporary.clear();
});
