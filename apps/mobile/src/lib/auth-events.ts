/** 极简同步事件发射器：api 装配层 → Providers 的解耦通道（如 401）。 */
export type AuthEvent = "unauthorized" | "guest-expired";

type Listener = (event: AuthEvent) => void;

const listeners = new Set<Listener>();

export function onAuthEvent(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function emitAuthEvent(event: AuthEvent): void {
  for (const listener of [...listeners]) listener(event);
}
