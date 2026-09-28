/* 浏览器语音识别适配器（从 useVoiceCall 的 STT 代码抽取，plan.md
 * §12.5/§17.3）。课堂按住说话复用；旧电话模式（useVoiceCall）不改动，
 * 两处语义不同：课堂只要「按住收集 → 松开出可修订文本」，不连接
 * voice.start，不启动电话式黑板，不做后台连续识别。
 *
 * supported=false 时调用方保留文字输入（§12.5）。
 */
"use client";

type SpeechRecognitionAlternativeLike = { transcript: string };
type SpeechRecognitionResultLike = {
  isFinal: boolean;
  length: number;
  [index: number]: SpeechRecognitionAlternativeLike;
};
type SpeechRecognitionResultListLike = {
  length: number;
  [index: number]: SpeechRecognitionResultLike;
};
type SpeechRecognitionEventLike = Event & {
  resultIndex: number;
  results: SpeechRecognitionResultListLike;
};
type SpeechRecognitionErrorEventLike = Event & {
  error: string;
  message?: string;
};
type BrowserSpeechRecognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
};
type BrowserSpeechRecognitionConstructor =
  new () => BrowserSpeechRecognition;

declare global {
  interface Window {
    SpeechRecognition?: BrowserSpeechRecognitionConstructor;
    webkitSpeechRecognition?: BrowserSpeechRecognitionConstructor;
  }
}

export type RecognitionLang = "zh" | "en";

export interface RecognitionHandlers {
  /** 每次识别结果：已定稿累计文本 + 本轮临时文本。 */
  onText?: (finalText: string, interim: string) => void;
  /** no-speech/aborted 之外的可恢复错误。 */
  onError?: (code: string) => void;
}

export class BrowserRecognition {
  private recognition: BrowserSpeechRecognition | null = null;
  private finalText = "";
  private interim = "";
  private handlers: RecognitionHandlers = {};
  private finalizedIndexes = new Set<number>();
  private lang: RecognitionLang = "zh";
  private stopping = false;

  static supported(): boolean {
    if (typeof window === "undefined") return false;
    return Boolean(window.SpeechRecognition
                   ?? window.webkitSpeechRecognition);
  }

  /** 按住开始：一次会话，从空文本累计；重复调用先中止上一会话。 */
  start(lang: RecognitionLang, handlers: RecognitionHandlers = {}): boolean {
    this.abort();
    const Recognition = window.SpeechRecognition
      ?? window.webkitSpeechRecognition;
    if (!Recognition) return false;
    let recognition: BrowserSpeechRecognition;
    try {
      recognition = new Recognition();
    } catch {
      return false;
    }
    this.recognition = recognition;
    this.handlers = handlers;
    this.lang = lang;
    this.finalText = "";
    this.interim = "";
    this.finalizedIndexes = new Set();
    this.stopping = false;
    recognition.lang = lang === "zh" ? "zh-CN" : "en-US";
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    recognition.onresult = (event) => {
      if (this.recognition !== recognition) return;
      const startIndex = Math.max(0, event.resultIndex || 0);
      for (let i = startIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        if (!result) continue;
        const transcript = result[0]?.transcript ?? "";
        if (result.isFinal) {
          if (this.finalizedIndexes.has(i)) continue;
          this.finalizedIndexes.add(i);
          this.finalText += transcript;
          this.interim = "";
        } else {
          this.interim = transcript;
        }
      }
      this.handlers.onText?.(this.finalText, this.interim);
    };
    recognition.onerror = (event) => {
      if (this.recognition !== recognition) return;
      const code = event.error || "unknown";
      if (code !== "aborted" && code !== "no-speech") {
        this.handlers.onError?.(code);
      }
    };
    recognition.onend = () => {
      if (this.recognition !== recognition) return;
      // 松开后浏览器自行结束：等 stop() 兜底收尾，这里不重复触发
      if (!this.stopping) {
        this.handlers.onText?.(this.finalText, "");
      }
    };
    try {
      recognition.start();
    } catch {
      this.recognition = null;
      return false;
    }
    return true;
  }

  /** 松开结束：返回定稿文本并释放识别器；不自动发送（§12.5 可修订）。 */
  stop(): string {
    const recognition = this.recognition;
    this.stopping = true;
    this.recognition = null;
    if (recognition) {
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
      try {
        recognition.stop();
      } catch { /* 已结束 */ }
    }
    return this.finalText.trim();
  }

  /** 取消（组件卸载/抽屉关闭）：丢弃文本并中止。 */
  abort(): void {
    const recognition = this.recognition;
    this.recognition = null;
    this.finalText = "";
    this.interim = "";
    if (recognition) {
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
      try {
        recognition.abort();
      } catch { /* 已结束 */ }
    }
  }
}
