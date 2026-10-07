import { apiFetch } from "./api-fetch";
import { API_BASE } from "./api";

export type ImageReferenceMode = "direct" | "auto" | "selected";
export type ImageAspectRatio = "1:1" | "4:3" | "16:9" | "3:4";

export interface ImageCapability {
  configured: boolean;
  provider: string;
  protocol: string;
  model: string;
  supports_reference: boolean;
  max_references: number;
}

export interface ImageGenerationRequest {
  prompt: string;
  reference_mode: ImageReferenceMode;
  reference_material_ids: string[];
  reference_material_versions?: Record<string, number>;
  reference_images?: string[];
  aspect_ratio: ImageAspectRatio;
}

export interface ImageGenerationResult {
  provider: string;
  protocol?: string;
  model: string;
  image_url: string;
  mime_type: string;
  revised_prompt?: string;
  source: "provider";
}

async function parseError(response: Response, fallback: string) {
  try {
    const payload = await response.json() as { detail?: string | { code?: string } };
    return typeof payload.detail === "string" ? payload.detail : payload.detail?.code || fallback;
  } catch {
    return fallback;
  }
}

export async function getImageCapability(signal?: AbortSignal): Promise<ImageCapability> {
  const response = await apiFetch(`${API_BASE}/tools/image/capability`, { signal });
  if (!response.ok) throw new Error(`image_capability_failed:${response.status}`);
  return await response.json() as ImageCapability;
}

export async function generateImage(body: ImageGenerationRequest, signal?: AbortSignal): Promise<ImageGenerationResult> {
  const response = await apiFetch(`${API_BASE}/tools/image/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) throw new Error(await parseError(response, "image_generation_failed"));
  return await response.json() as ImageGenerationResult;
}

/** Compatibility helper for older bundles; it returns only the active server capability. */
export async function getImageProviders(signal?: AbortSignal): Promise<Array<ImageCapability & { id: string; configured: boolean; models: string[] }>> {
  const capability = await getImageCapability(signal);
  return [{ ...capability, id: capability.provider, models: capability.model ? [capability.model] : [] }];
}
