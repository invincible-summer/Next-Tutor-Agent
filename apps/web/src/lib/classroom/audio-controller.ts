/* 课堂音频控制器（plan.md §11.5/§12.2，阶段 G01）。
 *
 * 两个复用的 <audio> 元素交替播放（当前段 + 预取下段可无缝衔接）；
 * 课堂只接收服务端 WAV bytes，经 apiFetch 拉 Blob URL，卸载/切账号撤销
 * 所有 Object URL。媒体时间一律来自 audio.currentTime；setTimeout 只承担
 * pause_after_ms 的短停顿，不推算长音频完成（§12.2）。每个回调带
 * generation token，token 不匹配直接丢弃。playbackRate + preservesPitch
 * 承担个人倍速；倍速改变不重合成、不改 token。
 *
 * 测试注入：`new ClassroomAudioController(clock)` 传 fake clock（提供
 * currentTime/timeupdate/ended 的确定性驱动，见 G05 fake audio 测试）。
 */
import { tokenEquals, type GenerationToken } from "./player-reducer";

export interface AudioClock {
  /** 创建底层媒体元素；真实实现为 new Audio()，测试注入假元素。 */
  createElement(): HTMLAudioElementLike;
  now(): number;
  shortPause(ms: number, fn: () => void): () => void;
}

export interface HTMLAudioElementLike {
  src: string;
  volume: number;
  playbackRate: number;
  preservesPitch?: boolean;
  currentTime: number;
  duration: number;
  paused: boolean;
  play(): Promise<void>;
  pause(): void;
  load(): void;
  addEventListener(type: string, listener: () => void): void;
  removeEventListener(type: string, listener: () => void): void;
}

export class RealAudioClock implements AudioClock {
  createElement(): HTMLAudioElementLike {
    return new Audio() as unknown as HTMLAudioElementLike;
  }
  now(): number {
    return performance.now();
  }
  shortPause(ms: number, fn: () => void): () => void {
    const t = window.setTimeout(fn, ms);
    return () => window.clearTimeout(t);
  }
}

export interface SegmentAudio {
  segmentId: string;
  url: string;          // Blob URL（controller 不负责创建/撤销）
  durationMs: number;   // 服务端真实时长
}

export interface ControllerEvents {
  onEnded: (segmentId: string, token: GenerationToken) => void;
  onBuffering: (segmentId: string | null, token: GenerationToken) => void;
  onTime: (segmentId: string, positionMs: number,
           token: GenerationToken) => void;
  onError: (segmentId: string, message: string,
            token: GenerationToken) => void;
}

interface Slot {
  el: HTMLAudioElementLike;
  segmentId: string | null;
}

export class ClassroomAudioController {
  private clock: AudioClock;
  private slots: [Slot, Slot];
  private active = 0;
  private token: GenerationToken | null = null;
  private events: ControllerEvents;
  private cancelPause: (() => void) | null = null;
  private listeners = new WeakMap<HTMLAudioElementLike, {
    ended: () => void; time: () => void; err: () => void; wait: () => void;
  }>();

  constructor(events: ControllerEvents, clock: AudioClock =
             new RealAudioClock()) {
    this.clock = clock;
    this.events = events;
    this.slots = [
      { el: clock.createElement(), segmentId: null },
      { el: clock.createElement(), segmentId: null },
    ];
  }

  /** 绑定当前有效 token；返回旧的（供调用方作废旧回调）。 */
  setToken(token: GenerationToken): void {
    this.token = token;
  }

  setVolume(volume: number): void {
    for (const s of this.slots) s.el.volume = volume;
  }

  setPlaybackRate(rate: number): void {
    for (const s of this.slots) {
      s.el.playbackRate = rate;
      if ("preservesPitch" in s.el) s.el.preservesPitch = true;
    }
  }

  /** 播放一段（当前槽）；下一段预载到另一槽实现无缝衔接。 */
  async play(audio: SegmentAudio, next: SegmentAudio | null,
             token: GenerationToken, offsetMs = 0): Promise<void> {
    if (!tokenEquals(token, this.token ?? token)) return;
    this.stopShortPause();
    this.attach(this.slots[this.active], audio, token, true);
    const el = this.slots[this.active].el;
    if (offsetMs > 0 && Number.isFinite(el.duration)) {
      el.currentTime = Math.min(offsetMs / 1000, Math.max(0,
        el.duration - 0.05));
    }
    if (next) {
      this.attach(this.slots[1 - this.active], next, token, false);
    }
    try {
      await el.play();
    } catch (err) {
      // 浏览器自动播放策略：不能当网络故障；交给上层显示“点击继续播放”
      this.events.onError(audio.segmentId, "play_rejected", token);
      throw err;
    }
  }

  private attach(slot: Slot, audio: SegmentAudio,
                 token: GenerationToken, isActive: boolean): void {
    const prev = this.listeners.get(slot.el);
    if (prev) {
      slot.el.removeEventListener("ended", prev.ended);
      slot.el.removeEventListener("timeupdate", prev.time);
      slot.el.removeEventListener("error", prev.err);
      slot.el.removeEventListener("waiting", prev.wait);
    }
    slot.segmentId = audio.segmentId;
    slot.el.src = audio.url;
    slot.el.currentTime = 0;
    const l = {
      ended: () => {
        if (!this.match(token) || slot.segmentId === null) return;
        if (isActive) this.handleEnded(slot, token);
      },
      time: () => {
        if (!this.match(token) || slot.segmentId === null) return;
        this.events.onTime(slot.segmentId, slot.el.currentTime * 1000, token);
      },
      err: () => {
        if (!this.match(token) || slot.segmentId === null) return;
        this.events.onError(slot.segmentId, "audio_error", token);
      },
      wait: () => {
        if (!this.match(token) || slot.segmentId === null) return;
        this.events.onBuffering(slot.segmentId, token);
      },
    };
    slot.el.addEventListener("ended", l.ended);
    slot.el.addEventListener("timeupdate", l.time);
    slot.el.addEventListener("error", l.err);
    slot.el.addEventListener("waiting", l.wait);
    this.listeners.set(slot.el, l);
  }

  private handleEnded(slot: Slot, token: GenerationToken): void {
    // 段结束：交换到预载槽；上层决定是否短停顿后继续
    const finished = slot.segmentId;
    this.active = 1 - this.active;
    slot.segmentId = null;
    if (finished) this.events.onEnded(finished, token);
  }

  /** pause_after_ms 短停顿（§12.2：setTimeout 只做短停顿）。 */
  scheduleShortPause(ms: number, token: GenerationToken,
                     fn: () => void): void {
    this.stopShortPause();
    this.cancelPause = this.clock.shortPause(Math.min(ms, 3000), () => {
      if (this.match(token)) fn();
    });
  }

  stopShortPause(): void {
    if (this.cancelPause) {
      this.cancelPause();
      this.cancelPause = null;
    }
  }

  pause(): void {
    this.stopShortPause();
    for (const s of this.slots) s.el.pause();
  }

  async resume(token: GenerationToken): Promise<void> {
    const el = this.slots[this.active].el;
    if (el.src && el.paused) {
      try {
        await el.play();
      } catch {
        this.events.onError(this.slots[this.active].segmentId ?? "",
                            "play_rejected", token);
      }
    }
  }

  /** 跳页/打断：epoch 已由上层递增；此处只停声并清空槽位。 */
  abort(): void {
    this.pause();
    for (const s of this.slots) {
      s.segmentId = null;
      s.el.src = "";
      s.el.load();
    }
  }

  /** 统计当前段播放位置（进度上报用）。 */
  positionMs(): number {
    const el = this.slots[this.active].el;
    return el.paused ? 0 : el.currentTime * 1000;
  }

  currentSegmentId(): string | null {
    return this.slots[this.active].segmentId;
  }

  private match(token: GenerationToken): boolean {
    return this.token !== null && tokenEquals(token, this.token);
  }
}
