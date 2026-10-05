import { act, renderHook } from "@testing-library/react-native";
import type { RunView } from "@next-tutor/api-client";
import type { SlideSpec } from "@next-tutor/contracts/classroom";
import { useLessonPlayback } from "@/features/classroom/useLessonPlayback";
const mockPlayer = {
  pause: jest.fn(),
  setActiveForLockScreen: jest.fn(),
  replace: jest.fn(),
  seekTo: jest.fn(),
  play: jest.fn(),
  playing: false,
  currentTime: 0,
};
const mockApi = {
  acquireLease: jest.fn(),
  renewLease: jest.fn(),
  releaseLease: jest.fn(),
  updateProgress: jest.fn(),
};
jest.mock("@/lib/api", () => ({ apiClient: () => ({ classroom: mockApi }) }));
jest.mock("@/lib/copy", () => ({ useCopy: () => (zh: string) => zh }));
jest.mock("@/ui/Toast", () => ({ useToast: () => jest.fn() }));
jest.mock("expo-audio", () => ({
  useAudioPlayer: () => mockPlayer,
  useAudioPlayerStatus: () => ({ playing: false, didJustFinish: false }),
  setAudioModeAsync: jest.fn(),
}));
jest.mock("expo-file-system", () => ({
  File: class {},
  Paths: { cache: "file:///cache" },
}));
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
}
const slides = [
  { slide_id: "s0", segments: [{ segment_id: "a" }] },
  { slide_id: "s1", segments: [{ segment_id: "b" }] },
] as SlideSpec[];
const refetch = jest.fn(async () => {});
function run(id: string) {
  return {
    data: {
      run_id: id,
      status: "active",
      state_revision: 1,
      lesson_revision: 1,
    } as RunView,
    refetch,
  };
}
beforeEach(() => {
  jest.clearAllMocks();
  mockApi.acquireLease.mockResolvedValue({ lease_epoch: 1 });
  mockApi.releaseLease.mockResolvedValue({});
  mockApi.updateProgress.mockResolvedValue({ state_revision: 2 });
});
test("old progress failure cannot pause a newly selected run", async () => {
  const waiting = deferred<{ state_revision: number }>();
  mockApi.updateProgress.mockReturnValueOnce(waiting.promise);
  const hook = await renderHook(
    ({ id }: { id: string }) =>
      useLessonPlayback("ws", "lesson", id, slides, run(id), "Lesson"),
    { initialProps: { id: "old" } },
  );
  let progress!: Promise<void>;
  await act(async () => {
    progress = hook.result.current.progress(1);
    await Promise.resolve();
    await Promise.resolve();
  });
  await hook.rerender({ id: "new" });
  await act(() => hook.result.current.acquire());
  mockPlayer.pause.mockClear();
  refetch.mockClear();
  await act(async () => {
    waiting.reject(new Error("stale CAS"));
    await progress;
  });
  expect(mockPlayer.pause).not.toHaveBeenCalled();
  expect(refetch).not.toHaveBeenCalled();
  await act(() => hook.result.current.progress(0));
  expect(mockApi.updateProgress.mock.calls.at(-1)?.[2]).toBe("new");
  await hook.unmount();
});
test("delayed checkpoint cannot submit progress with the old route closure", async () => {
  const check = deferred<boolean>();
  const hook = await renderHook(
    ({ id }: { id: string }) =>
      useLessonPlayback(
        "ws",
        "lesson",
        id,
        slides,
        run(id),
        "Lesson",
        () => check.promise,
      ),
    { initialProps: { id: "old" } },
  );
  let jump!: Promise<void>;
  await act(async () => {
    jump = hook.result.current.jump(1);
    await Promise.resolve();
  });
  await hook.rerender({ id: "new" });
  mockApi.updateProgress.mockClear();
  await act(async () => {
    check.resolve(false);
    await jump;
  });
  expect(mockApi.updateProgress).not.toHaveBeenCalled();
  await hook.unmount();
});
test("lease heartbeat failure from an old run cannot stop a new lease", async () => {
  jest.useFakeTimers();
  const waiting = deferred<unknown>();
  mockApi.renewLease.mockReturnValueOnce(waiting.promise);
  try {
    const hook = await renderHook(
      ({ id }: { id: string }) =>
        useLessonPlayback("ws", "lesson", id, slides, run(id), "Lesson"),
      { initialProps: { id: "old" } },
    );
    await act(() => hook.result.current.acquire());
    await act(() => jest.advanceTimersByTime(12000));
    await hook.rerender({ id: "new" });
    await act(() => hook.result.current.acquire());
    mockPlayer.pause.mockClear();
    await act(async () => {
      waiting.reject(new Error("old lease"));
      await Promise.resolve();
    });
    expect(mockPlayer.pause).not.toHaveBeenCalled();
    await hook.unmount();
  } finally {
    jest.useRealTimers();
  }
});
