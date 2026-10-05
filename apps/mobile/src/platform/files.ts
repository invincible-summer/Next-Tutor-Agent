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
    file.write(typeof bytes === "string" ? bytes : new Uint8Array(bytes));
    if (!(await Sharing.isAvailableAsync()))
      throw new Error("sharing_unavailable");
    await Sharing.shareAsync(file.uri, { mimeType, dialogTitle: name });
  } finally {
    if (file.exists) file.delete();
    temporary.delete(file);
  }
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
