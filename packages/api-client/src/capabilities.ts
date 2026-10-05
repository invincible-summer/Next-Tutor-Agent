/**
 * Product capability probe (mirrors `app/api/v1/capabilities.py`): one
 * read-only aggregation the shell consults to decide which features to
 * render — the server is the source of truth, clients never guess from
 * environment variables. Dotted keys (`illustration.quiz`) are the wire
 * contract, kept verbatim.
 */
import type { AbortSignalLike, Transport } from "./types.ts";

export interface CapabilityEntry {
  available: boolean;
  reason: string;
}

export interface ProductCapabilities {
  chat: CapabilityEntry;
  upload: CapabilityEntry;
  classroom: CapabilityEntry;
  cloud_stt: CapabilityEntry;
  cloud_tts: CapabilityEntry;
  assistant: CapabilityEntry;
  "illustration.quiz": CapabilityEntry;
  "illustration.scenario": CapabilityEntry;
  "illustration.v3": CapabilityEntry;
  "diagram.materials": CapabilityEntry;
}

export interface CapabilitiesClient {
  /** GET /capabilities — feature visibility for the current principal. */
  get(signal?: AbortSignalLike | null): Promise<ProductCapabilities>;
}

export function createCapabilitiesClient(transport: Transport): CapabilitiesClient {
  return {
    get: (signal) =>
      transport
        .request<ProductCapabilities>("/capabilities", { signal })
        .then((result) => result.body),
  };
}
