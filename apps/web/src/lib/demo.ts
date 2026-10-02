/** Compile-time switch; the normal backend deployment keeps its existing behavior. */
export const DEMO_MODE = process.env.NEXT_PUBLIC_DEMO_MODE === "1";
export const SITE_BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";
export const DEMO_TOKEN_KEY = "edu-agent-pages-demo-token";
export const DEMO_TOKEN = "pages-example-preview";

export function siteUrl(path: string): string {
  if (!path.startsWith("/") || path.startsWith("//")) return path;
  return SITE_BASE_PATH && !path.startsWith(SITE_BASE_PATH + "/")
    ? SITE_BASE_PATH + path : path;
}

export function demoReadOnly(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event("edu-demo-readonly"));
}

/** Explain an unavailable action before its handler can change local state. */
export function guardDemoAction<T extends { preventDefault(): void; stopPropagation(): void }>(
  action: (event: T) => void, blocked = true,
): (event: T) => void {
  return (event) => {
    if (DEMO_MODE && blocked) {
      event.preventDefault();
      event.stopPropagation();
      demoReadOnly();
      return;
    }
    action(event);
  };
}
