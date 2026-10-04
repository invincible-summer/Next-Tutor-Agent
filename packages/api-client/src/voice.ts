/**
 * Server-mediated speech domain (ADR-0012, mirrors `app/api/v1/speech.py`):
 * one-utterance transcription upload and per-sentence synthesis download.
 * Provider credentials never leave the server; errors surface as
 * `ApiError` codes (`stt_unavailable`, `audio_format_rejected`,
 * `audio_too_long`, `audio_too_large`, `audio_empty`, `stt_rate_limited`,
 * `tts_unavailable`). The legacy browser `/voice/ws` pair stays Web-local.
 */
import type {
  AbortSignalLike,
  FormDataLike,
  RequestOptions,
  Transport,
} from "./types.ts";

/** Read-only provider projection; keys vary by provider configuration. */
export type SpeechProviderCapabilities = Record<string, unknown> & {
  available?: boolean;
  reason?: string;
};

export interface SpeechCapabilities {
  stt: SpeechProviderCapabilities;
  synthesis: SpeechProviderCapabilities;
}

export interface TranscriptionResult {
  text: string;
  language: string;
  duration_ms: number;
  provider_class: string;
}

/**
 * The audio part handed to `FormData.append`: browser `File`/`Blob`, RN
 * `{uri, name, type}` — opaque to this package.
 */
export interface VoiceAudioFile {
  data: unknown;
  filename?: string;
}

export interface SynthesisInput {
  text: string;
  language?: "zh" | "en";
  voice_id?: string;
  speed?: number;
  policy?: "auto" | "cloud" | "local";
  allow_local_fallback?: boolean;
}

export interface SynthesisAudio {
  audio: ArrayBuffer;
  contentType: string;
  sampleRate: number | null;
  voiceId: string | null;
}

export interface VoiceCallOptions {
  signal?: AbortSignalLike | null;
  timeoutMs?: number;
}

export interface VoiceClient {
  /** GET /speech/capabilities — STT + synthesis projections, no secrets. */
  capabilities(signal?: AbortSignalLike | null): Promise<SpeechCapabilities>;
  /**
   * POST /speech/transcriptions — multipart upload of one utterance. The
   * caller supplies the platform FormData instance; this method injects the
   * wire fields (`file`, `duration_ms`, `language`) so field names stay a
   * shared concern.
   */
  transcribe(
    form: FormDataLike,
    input: { file: VoiceAudioFile; durationMs: number; language?: string },
    options?: VoiceCallOptions,
  ): Promise<TranscriptionResult>;
  /**
   * POST /speech/synthesis — controlled text → WAV bytes (headers
   * `X-Sample-Rate` / `X-Voice-Id` describe the returned audio).
   */
  synthesize(payload: SynthesisInput, options?: VoiceCallOptions): Promise<SynthesisAudio>;
}

export function createVoiceClient(transport: Transport): VoiceClient {
  const synthesisRequest = (payload: SynthesisInput): RequestOptions["json"] => ({
    text: payload.text,
    language: payload.language ?? "zh",
    voice_id: payload.voice_id ?? "",
    speed: payload.speed ?? 1.0,
    policy: payload.policy ?? "auto",
    allow_local_fallback: payload.allow_local_fallback ?? true,
  });
  return {
    capabilities: (signal) =>
      transport
        .request<SpeechCapabilities>("/speech/capabilities", { signal })
        .then((result) => result.body),
    transcribe: (form, input, options) => {
      form.append("file", input.file.data, input.file.filename);
      form.append("duration_ms", String(input.durationMs));
      form.append("language", input.language ?? "zh");
      return transport
        .request<TranscriptionResult>("/speech/transcriptions", {
          method: "POST",
          body: form,
          signal: options?.signal ?? null,
          timeoutMs: options?.timeoutMs,
        })
        .then((result) => result.body);
    },
    synthesize: (payload, options) =>
      transport
        .request<ArrayBuffer>("/speech/synthesis", {
          method: "POST",
          json: synthesisRequest(payload),
          responseType: "bytes",
          signal: options?.signal ?? null,
          timeoutMs: options?.timeoutMs,
        })
        .then((result) => ({
          audio: result.body,
          contentType: result.headers.get("content-type") ?? "audio/wav",
          sampleRate: parseHeaderNumber(result.headers.get("x-sample-rate")),
          voiceId: result.headers.get("x-voice-id"),
        })),
  };
}

function parseHeaderNumber(raw: string | null): number | null {
  if (raw === null) return null;
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) ? value : null;
}
