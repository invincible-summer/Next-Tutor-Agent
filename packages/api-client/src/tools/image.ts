import type { AbortSignalLike, Transport } from "../types.ts";

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

export interface ImageClient {
  capability(signal?: AbortSignalLike | null): Promise<ImageCapability>;
  generate(body: ImageGenerationRequest, signal?: AbortSignalLike | null): Promise<ImageGenerationResult>;
}

export function createImageClient(transport: Transport): ImageClient {
  const call = async <T>(path: string, init?: Parameters<Transport["request"]>[1]): Promise<T> =>
    transport.request<T>(`/tools/image${path}`, init).then((result) => result.body);
  return {
    capability: (signal) => call<ImageCapability>("/capability", { signal }),
    generate: (body, signal) => call<ImageGenerationResult>("/generate", { method: "POST", json: body, signal }),
  };
}
