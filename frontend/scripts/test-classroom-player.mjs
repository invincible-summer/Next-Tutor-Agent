/* 课堂播放器确定性测试（plan.md §18 G05、§19.3）。
 *
 * 用 tsc 把 audio-controller/player-reducer 编译到临时目录，在 Node 里以
 * fake HTMLMediaElement + 受控 clock 驱动，验证：
 * 1. 段推进只由 ended 事件触发（时间流逝/暂停恢复不推进）；
 * 2. 迟到回调（旧 playback epoch）被丢弃，不出现旧声音/旧推进；
 * 3. pause/abort 立即停声并清槽；prefetch 槽在 ended 后无缝交接；
 * 4. play() 拒绝 → play_rejected，不当网络故障；
 * 5. shortPause 只做短停顿且随 token 作废；
 * 6. 整课 6 段连续播放：仅凭 ended 事件跑完，末段 ended 后不再推进。
 *
 * 运行：pnpm run test:player（CI classroom-checks 复用同一命令）。
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";

const root = process.cwd();
const outDir = mkdtempSync(join(tmpdir(), "classroom-player-test-"));
try {
  execFileSync(
    process.execPath,
    [join(root, "node_modules/typescript/lib/tsc.js"),
     "--module", "commonjs", "--target", "ES2020",
     "--lib", "ES2020,DOM", "--strict", "--skipLibCheck",
     "--outDir", outDir,
     join(root, "src/lib/classroom/audio-controller.ts"),
     join(root, "src/lib/classroom/player-reducer.ts")],
    { stdio: "inherit" },
  );
  const require_ = createRequire(import.meta.url);
  const { ClassroomAudioController } = require_(join(outDir, "audio-controller.js"));
  const { makeToken, bumpToken } = require_(join(outDir, "player-reducer.js"));
  await run(makeToken, bumpToken, ClassroomAudioController);
} finally {
  rmSync(outDir, { recursive: true, force: true });
}

async function run(makeToken, bumpToken, ClassroomAudioController) {
  let failures = 0;
  const check = (name, cond) => {
    if (cond) {
      console.log(`  ok - ${name}`);
    } else {
      failures += 1;
      console.error(`  FAIL - ${name}`);
    }
  };

  /* ---------- fake 媒体元素 + 受控 clock ---------- */
  class FakeAudio {
    constructor() {
      this.src = "";
      this.volume = 1;
      this.playbackRate = 1;
      this.preservesPitch = undefined;
      this.currentTime = 0;
      this.duration = 10;
      this.paused = true;
      this.playCalls = 0;
      this.pauseCalls = 0;
      this.loadCalls = 0;
      this.listeners = new Map();
      this.autoEnd = false;   // play() 后是否在 tick 推进中
    }
    async play() {
      this.playCalls += 1;
      if (FakeAudio.rejectNextPlay) {
        FakeAudio.rejectNextPlay = false;
        this.paused = true;
        return Promise.reject(new Error("NotAllowedError"));
      }
      this.paused = false;
      return Promise.resolve();
    }
    pause() {
      this.pauseCalls += 1;
      this.paused = true;
    }
    load() { this.loadCalls += 1; }
    addEventListener(type, fn) {
      if (!this.listeners.has(type)) this.listeners.set(type, new Set());
      this.listeners.get(type).add(fn);
    }
    removeEventListener(type, fn) { this.listeners.get(type)?.delete(fn); }
    emit(type) {
      if (type === "ended") this.paused = true;
      for (const fn of this.listeners.get(type) ?? []) fn();
    }
    /** 测试驱动：模拟时间流逝到结尾（timeupdate 若干次 + ended）。 */
    runToEnd(timeupdates = 2) {
      for (let i = 0; i < timeupdates; i += 1) this.emit("timeupdate");
      this.currentTime = this.duration;
      this.emit("ended");
    }
  }
  FakeAudio.rejectNextPlay = false;

  const elements = [];
  let clockNow = 0;
  const pendingPauses = [];
  const clock = {
    createElement() {
      const el = new FakeAudio();
      elements.push(el);
      return el;
    },
    now: () => clockNow,
    shortPause(ms, fn) {
      const entry = { at: clockNow + ms, fn, cancelled: false };
      pendingPauses.push(entry);
      return () => { entry.cancelled = true; };
    },
  };
  const advanceClock = (ms) => {
    clockNow += ms;
    for (const p of pendingPauses) {
      if (!p.cancelled && p.at <= clockNow) {
        p.cancelled = true;
        p.fn();
      }
    }
  };

  const seg = (n) => ({ segmentId: `seg-${n}`, url: `blob:${n}`,
                        durationMs: 10_000 });
  const events = { ended: [], time: [], error: [], buffering: [] };
  const ctl = new ClassroomAudioController({
    onEnded: (sid, tok) => events.ended.push([sid, tok]),
    onBuffering: (sid, tok) => events.buffering.push([sid, tok]),
    onTime: (sid, ms, tok) => events.time.push([sid, ms, tok]),
    onError: (sid, msg, tok) => events.error.push([sid, msg, tok]),
  }, clock);

  const token = makeToken("run-1", 1, 7);
  ctl.setToken(token);

  /* ---------- 1. ended 才推进：play 后时间流逝不推进 ---------- */
  console.log("1) advance only on ended");
  void ctl.play(seg(1), seg(2), token);
  check("no ended event before media ends", events.ended.length === 0);
  elements[0].emit("timeupdate");
  elements[0].emit("timeupdate");
  check("timeupdate alone does not advance", events.ended.length === 0);
  advanceClock(60_000);
  check("wall clock alone does not advance", events.ended.length === 0);
  elements[0].runToEnd();
  check("ended fires exactly once for seg-1",
        events.ended.length === 1 && events.ended[0][0] === "seg-1");

  /* ---------- 2. prefetch 槽交接：ended 后 active 槽切到预载段 ----------
   * handleEnded 交换 active 并清空已结束槽；预载槽成为当前段。 */
  console.log("2) prefetched slot handover");
  check("prefetched segment becomes active after ended",
        ctl.currentSegmentId() === "seg-2");
  void ctl.play(seg(2), seg(3), token);
  check("second play went to the other (prefetched) element",
        elements[1].playCalls === 1 && elements[0].playCalls === 1);
  elements[1].runToEnd();
  check("seg-2 ended delivered", events.ended.at(-1)[0] === "seg-2");

  /* ---------- 3. 迟到回调丢弃：token 已前进后，旧元素事件全部无效 ---------- */
  console.log("3) stale-token callbacks dropped");
  const t1 = bumpToken(token);
  ctl.setToken(t1);
  void ctl.play(seg(4), null, t1);       // elements[0] 承载 seg-4/t1
  const t2 = bumpToken(t1);              // 上层跳页/打断：token 前进
  ctl.setToken(t2);
  const timesBefore = events.time.length;
  const endedBefore = events.ended.length;
  elements[0].emit("timeupdate");        // 旧 token 的 time
  elements[0].runToEnd();                // 旧 token 的 ended
  check("stale timeupdate dropped", events.time.length === timesBefore);
  check("stale ended dropped (no advance)",
        events.ended.length === endedBefore
        && !events.ended.some(([sid]) => sid === "seg-4"));

  /* ---------- 4. pause/abort 立即停声 ---------- */
  console.log("4) pause and abort");
  const beforePause = elements[0].pauseCalls + elements[1].pauseCalls;
  ctl.pause();
  check("pause() pauses both slots synchronously",
        elements[0].pauseCalls + elements[1].pauseCalls === beforePause + 2);
  ctl.abort();
  check("abort clears sources", elements.every((e) => e.src === ""));
  check("abort nulls segment ids", ctl.currentSegmentId() === null);

  /* ---------- 5. play_rejected：浏览器手势策略不当网络故障 ---------- */
  console.log("5) autoplay rejection");
  FakeAudio.rejectNextPlay = true;
  let rejected = false;
  await ctl.play(seg(5), null, t2).then(
    () => undefined, () => { rejected = true; });
  check("play() promise rejects", rejected);
  check("onError carries play_rejected",
        events.error.some(([sid, msg]) => sid === "seg-5"
                       && msg === "play_rejected"));

  /* ---------- 6. shortPause：短停顿 + token 作废 ---------- */
  console.log("6) short pause between segments");
  let resumed = false;
  ctl.scheduleShortPause(800, t2, () => { resumed = true; });
  advanceClock(400);
  check("short pause not fired early", resumed === false);
  advanceClock(500);
  check("short pause fires once due", resumed === true);
  let staleResume = false;
  ctl.scheduleShortPause(100, t1, () => { staleResume = true; });
  advanceClock(200);
  check("stale-token short pause cancelled", staleResume === false);

  /* ---------- 7. 整课连播：仅凭 ended 跑完 6 段 ---------- */
  console.log("7) full lesson playback via ended only");
  events.ended.length = 0;
  const total = 6;
  for (let i = 1; i <= total; i += 1) {
    const tok = { ...t2 };
    void ctl.play(seg(i), i < total ? seg(i + 1) : null, tok);
    const el = elements.find((e) => e.src === `blob:${i}`);
    el.runToEnd(1);
  }
  check(`all ${total} segments ended in order`,
        events.ended.length === total
        && events.ended.every(([sid], i) => sid === `seg-${i + 1}`));

  /* ---------- 8. 音量/倍速：作用于两个槽，不动 token ---------- */
  console.log("8) volume and rate");
  ctl.setVolume(0.4);
  ctl.setPlaybackRate(1.25);
  check("volume applied to both slots",
        elements.every((e) => e.volume === 0.4));
  check("playbackRate applied to both slots",
        elements.every((e) => e.playbackRate === 1.25));
  check("preservesPitch enabled",
        elements.every((e) => e.preservesPitch === true));

  console.log(failures === 0
    ? "\nclassroom player deterministic suite: ALL PASS"
    : `\nclassroom player deterministic suite: ${failures} FAILURES`);
  process.exitCode = failures === 0 ? 0 : 1;
}
