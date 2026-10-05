import { useEffect, useRef, useState } from "react";
import { AppState } from "react-native";
import { File, Paths } from "expo-file-system";
import { randomUUID } from "expo-crypto";
import {
  setAudioModeAsync,
  useAudioPlayer,
  useAudioPlayerStatus,
} from "expo-audio";
import type { RunView } from "@next-tutor/api-client";
import type { SlideSpec } from "@next-tutor/contracts/classroom";
import { apiClient } from "@/lib/api";
import { registerSessionCleanup } from "@/lib/session-lifecycle";
import { useCopy } from "@/lib/copy";
import { useToast } from "@/ui/Toast";
type RunQuery = { data: RunView | undefined; refetch: () => Promise<unknown> };
export function useLessonPlayback(
  workspaceId: string | null,
  lessonId: string,
  runId: string,
  slides: SlideSpec[],
  run: RunQuery,
  title: string,
  beforeAdvance?: (slideId: string) => Promise<boolean>,
) {
  const c = useCopy();
  const toast = useToast();
  const player = useAudioPlayer();
  const audio = useAudioPlayerStatus(player);
  const [index, setIndex] = useState(0);
  const [segmentIndex, setSegmentIndex] = useState(0);
  const [buffering, setBuffering] = useState(false);
  const client = useRef("cl-" + randomUUID());
  const lease = useRef<number | null>(null);
  const seq = useRef(0);
  const snapshot = useRef<RunView | undefined>(run.data);
  const aligned = useRef("");
  const offset = useRef(0);
  const playingSegment = useRef("");
  const file = useRef<File | null>(null);
  const ctl = useRef<AbortController | null>(null);
  const epoch = useRef(0);
  const played = useRef(new Set<string>());
  const skipped = useRef(new Set<string>());
  const lastCompleted = useRef<string | null>(null);
  const writeQueue = useRef<Promise<unknown>>(Promise.resolve());
  const playLock = useRef(false);
  const continuous = useRef(false);
  const sessionEpoch = useRef(0);
  const mounted = useRef(true);
  const latest = useRef({ index, segmentIndex, slides });
  latest.current = { index, segmentIndex, slides };
  useEffect(() => {
    snapshot.current = run.data?.run_id === runId ? run.data : undefined;
    aligned.current = "";
    offset.current = 0;
    playingSegment.current = "";
    playLock.current = false;
    lastCompleted.current = null;
    writeQueue.current = Promise.resolve();
    setIndex(0);
    setSegmentIndex(0);
  }, [workspaceId, lessonId, runId]);
  useEffect(() => {
    if (
      run.data &&
      (!snapshot.current ||
        snapshot.current.run_id !== run.data.run_id ||
        run.data.state_revision >= snapshot.current.state_revision)
    )
      snapshot.current = run.data;
  }, [run.data]);
  useEffect(() => {
    if (
      !run.data ||
      !slides.length ||
      (run.data.lesson_revision !== undefined && aligned.current === runId)
    )
      return;
    aligned.current = runId;
    const cursor = run.data.resume_anchor ?? run.data.cursor;
    const found = slides.findIndex((s) => s.slide_id === cursor?.slide_id);
    if (found >= 0) {
      setIndex(found);
      setSegmentIndex(
        Math.max(
          0,
          slides[found]!.segments.findIndex(
            (s) => s.segment_id === cursor?.segment_id,
          ),
        ),
      );
      offset.current = cursor?.offset_ms ?? 0;
      lastCompleted.current = cursor?.last_completed_segment_id ?? null;
    }
  }, [run.data, slides, runId]);
  function disposeFile() {
    if (file.current?.exists) file.current.delete();
    file.current = null;
  }
  function stopLocal() {
    continuous.current = false;
    epoch.current++;
    playLock.current = false;
    playingSegment.current = "";
    ctl.current?.abort();
    player.pause();
    player.setActiveForLockScreen(false);
    setBuffering(false);
  }
  useEffect(() => {
    mounted.current = true;
    sessionEpoch.current++;
    lease.current = null;
    const session = sessionEpoch.current;
    seq.current = 0;
    played.current.clear();
    skipped.current.clear();
    const timer = setInterval(() => {
      if (!workspaceId || !runId || lease.current === null) return;
      void apiClient()
        .classroom.renewLease(workspaceId, lessonId, runId, {
          client_id: client.current,
          lease_epoch: lease.current,
        })
        .catch(() => {
          if (session !== sessionEpoch.current || !mounted.current) return;
          lease.current = null;
          stopLocal();
          void run.refetch();
        });
    }, 12000);
    const app = AppState.addEventListener("change", (state) => {
      if (state === "active") {
        void run.refetch();
      } else if (!player.playing) ctl.current?.abort();
    });
    const unregister = registerSessionCleanup(() => {
      sessionEpoch.current++;
      stopLocal();
      disposeFile();
      lease.current = null;
    });
    return () => {
      mounted.current = false;
      sessionEpoch.current++;
      clearInterval(timer);
      app.remove();
      unregister();
      stopLocal();
      disposeFile();
      if (workspaceId && runId && lease.current !== null)
        void apiClient()
          .classroom.releaseLease(workspaceId, lessonId, runId, {
            client_id: client.current,
            lease_epoch: lease.current,
          })
          .catch(() => {});
      lease.current = null;
    };
  }, [workspaceId, lessonId, runId, player]);
  async function acquire(takeover = false) {
    const session = sessionEpoch.current;
    if (!workspaceId || !runId) throw new Error("run_required");
    if (lease.current === null || takeover) {
      const result = await apiClient().classroom.acquireLease(
        workspaceId,
        lessonId,
        runId,
        { client_id: client.current, takeover },
      );
      if (session !== sessionEpoch.current || !mounted.current)
        throw new Error("cancelled");
      lease.current = result.lease_epoch;
    }
    return lease.current;
  }
  function progress(
    nextIndex = index,
    nextSegment = segmentIndex,
    kind: "progress" | "pause" | "complete" | "end" = "progress",
    preserveOffset = false,
  ): Promise<void> {
    const session = sessionEpoch.current;
    const operation = writeQueue.current
      .catch(() => {})
      .then(async () => {
        if (session !== sessionEpoch.current || !mounted.current) return;
        const current = slides[nextIndex];
        if (!current) return;
        if (!runId || !workspaceId || !snapshot.current) {
          setIndex(nextIndex);
          setSegmentIndex(nextSegment);
          offset.current = 0;
          return;
        }
        if (
          snapshot.current.status === "completed" ||
          snapshot.current.status === "ended"
        ) {
          setIndex(nextIndex);
          setSegmentIndex(nextSegment);
          return;
        }
        const leaseEpoch = await acquire();
        if (session !== sessionEpoch.current || !mounted.current) return;
        const offsetMs = preserveOffset
          ? Math.round(player.currentTime * 1000)
          : 0;
        try {
          const result = await apiClient().classroom.updateProgress(
            workspaceId,
            lessonId,
            runId,
            {
              expected_state_revision: snapshot.current.state_revision,
              client_event_id: randomUUID(),
              client_seq: ++seq.current,
              lease_epoch: leaseEpoch,
              action: kind,
              cursor: {
                slide_id: current.slide_id,
                segment_id: current.segments[nextSegment]?.segment_id ?? "",
                chunk_index: 0,
                offset_ms: offsetMs,
                last_completed_segment_id: lastCompleted.current,
              },
              played_segment_ids: [...played.current].slice(-48),
              skipped_slide_ids: [...skipped.current].slice(-24),
            },
          );
          if (session !== sessionEpoch.current || !mounted.current) return;
          snapshot.current = {
            ...snapshot.current,
            state_revision: result.state_revision,
          };
          setIndex(nextIndex);
          setSegmentIndex(nextSegment);
          offset.current = offsetMs;
        } catch (error) {
          if (session !== sessionEpoch.current || !mounted.current) return;
          lease.current = null;
          stopLocal();
          await run.refetch();
          throw error;
        }
      });
    writeQueue.current = operation;
    return operation;
  }
  async function jump(nextIndex: number, nextSegment = 0) {
    const session = sessionEpoch.current;
    stopLocal();
    const previous = slides[index];
    if (
      previous &&
      nextIndex > index &&
      runId &&
      snapshot.current?.status !== "completed" &&
      snapshot.current?.status !== "ended" &&
      (await beforeAdvance?.(previous.slide_id))
    )
      return;
    if (session !== sessionEpoch.current || !mounted.current) return;
    if (
      previous &&
      nextIndex > index &&
      lastCompleted.current !== previous.segments.at(-1)?.segment_id
    )
      skipped.current.add(previous.slide_id);
    await progress(nextIndex, nextSegment);
    playingSegment.current = "";
  }
  async function playAt(atIndex = index, atSegment = segmentIndex) {
    if (playLock.current || !workspaceId || !runId) return;
    const segment = slides[atIndex]?.segments[atSegment];
    if (!segment) return;
    if (
      snapshot.current?.status === "ended" ||
      snapshot.current?.status === "completed"
    )
      return;
    playLock.current = true;
    setBuffering(true);
    const generation = ++epoch.current;
    const controller = new AbortController();
    ctl.current = controller;
    try {
      const leaseEpoch = await acquire();
      if (generation !== epoch.current || controller.signal.aborted) return;
      const response = await apiClient().classroom.requestRunAudio(
        workspaceId,
        lessonId,
        runId,
        { segment_ids: [segment.segment_id], lease_epoch: leaseEpoch },
        randomUUID(),
      );
      let clip = response.clips?.[0];
      if (!clip) throw new Error("audio_unavailable");
      for (
        let attempt = 0;
        clip.state === "pending" && attempt < 25;
        attempt++
      ) {
        await new Promise<void>((resolve, reject) => {
          const timer = setTimeout(
            resolve,
            Math.min(2000, 500 * (attempt + 1)),
          );
          controller.signal.addEventListener(
            "abort",
            () => {
              clearTimeout(timer);
              reject(new Error("cancelled"));
            },
            { once: true },
          );
        });
        if (controller.signal.aborted || generation !== epoch.current) return;
        if (AppState.currentState !== "active") return;
        clip = await apiClient().classroom.getClipStatus(
          workspaceId,
          lessonId,
          runId,
          clip.clip_id,
          controller.signal,
        );
      }
      if (clip.state !== "ready") throw new Error("audio_unavailable");
      const bytes = await apiClient().classroom.clipContent(
        workspaceId,
        lessonId,
        runId,
        clip.clip_id,
        controller.signal,
      );
      if (controller.signal.aborted || generation !== epoch.current) return;
      disposeFile();
      const nextFile = new File(
        Paths.cache,
        "nt-classroom-" + randomUUID() + ".wav",
      );
      nextFile.write(new Uint8Array(bytes));
      file.current = nextFile;
      await setAudioModeAsync({
        allowsRecording: false,
        playsInSilentMode: true,
        shouldPlayInBackground: true,
      });
      if (generation !== epoch.current || controller.signal.aborted) return;
      player.replace(nextFile.uri);
      playingSegment.current = segment.segment_id;
      if (offset.current > 0) await player.seekTo(offset.current / 1000);
      if (generation !== epoch.current || controller.signal.aborted) return;
      player.setActiveForLockScreen(true, {
        title,
        artist: slides[atIndex]?.title ?? "Next Tutor",
      });
      player.play();
    } catch (error) {
      if (generation !== epoch.current || controller.signal.aborted) return;
      throw error;
    } finally {
      if (ctl.current === controller) playLock.current = false;
      if (generation === epoch.current) setBuffering(false);
    }
  }
  async function toggle() {
    if (audio.playing || buffering) {
      stopLocal();
      await progress(index, segmentIndex, "pause", true);
    } else {
      continuous.current = true;
      await playAt();
    }
  }
  useEffect(() => {
    if (!audio.didJustFinish || !playingSegment.current) return;
    const session = sessionEpoch.current;
    const finished = playingSegment.current;
    playingSegment.current = "";
    played.current.add(finished);
    lastCompleted.current = finished;
    offset.current = 0;
    const current = latest.current;
    const nextSegment = current.segmentIndex + 1;
    const nextIndex =
      nextSegment < (current.slides[current.index]?.segments.length ?? 0)
        ? current.index
        : current.index + 1;
    const segmentAt = nextIndex === current.index ? nextSegment : 0;
    void progress(current.index, current.segmentIndex, "progress")
      .then(async () => {
        if (session !== sessionEpoch.current || !mounted.current) return;
        if (
          nextIndex !== current.index &&
          (await beforeAdvance?.(current.slides[current.index]!.slide_id))
        ) {
          continuous.current = false;
          player.setActiveForLockScreen(false);
          return;
        }
        if (session !== sessionEpoch.current || !mounted.current) return;
        if (current.slides[nextIndex]) {
          await progress(nextIndex, segmentAt);
          if (session !== sessionEpoch.current || !mounted.current) return;
          if (continuous.current) await playAt(nextIndex, segmentAt);
        } else {
          continuous.current = false;
          player.setActiveForLockScreen(false);
        }
      })
      .catch(() => {
        if (session !== sessionEpoch.current || !mounted.current) return;
        continuous.current = false;
        toast(
          c(
            "播放已暂停，可继续阅读讲稿或重新连接。",
            "Playback paused. You can read the narration or reconnect.",
          ),
          "info",
        );
      });
  }, [audio.didJustFinish]);
  useEffect(() => {
    if (!audio.playing || !runId) return;
    const timer = setInterval(() => {
      void progress(
        latest.current.index,
        latest.current.segmentIndex,
        "progress",
        true,
      ).catch(() => {});
    }, 5000);
    return () => clearInterval(timer);
  }, [audio.playing, runId]);
  return {
    index,
    segmentIndex,
    audio,
    buffering,
    progress,
    jump,
    toggle,
    acquire,
    stop: stopLocal,
    player,
  };
}
