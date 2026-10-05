const cleanups = new Set<() => void | Promise<void>>();
export function registerSessionCleanup(cleanup: () => void | Promise<void>) {
  cleanups.add(cleanup);
  return () => {
    cleanups.delete(cleanup);
  };
}
export async function clearPrivateState() {
  await Promise.allSettled(
    [...cleanups].map((cleanup) => Promise.resolve().then(cleanup)),
  );
}
