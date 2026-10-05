import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/providers/AuthProvider";
import { useCopy } from "./copy";
import { errorMessage } from "./feedback";
import { useToast } from "@/ui/Toast";
export function useServerQuery<T>(
  key: readonly unknown[],
  load: (signal: AbortSignal) => Promise<T>,
  options: {
    enabled?: boolean;
    public?: boolean;
    poll?: number | ((data: T | undefined) => number | false);
  } = {},
) {
  const { owner, state } = useAuth();
  return useQuery({
    queryKey: [owner, ...key],
    queryFn: ({ signal }) => load(signal),
    enabled:
      options.enabled !== false &&
      (options.public === true || state.status === "signed-in"),
    refetchInterval:
      typeof options.poll === "function"
        ? (query) =>
            (options.poll as (data: T | undefined) => number | false)(
              query.state.data,
            )
        : (options.poll ?? false),
    refetchIntervalInBackground: false,
  });
}
export function useAction() {
  const query = useQueryClient();
  const toast = useToast();
  const c = useCopy();
  const { owner } = useAuth();
  const [pending, setPending] = useState(false);
  const lock = useRef(false);
  const mounted = useRef(true);
  const ownerRef = useRef(owner);
  ownerRef.current = owner;
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const run = useCallback(
    async <T>(
      work: () => Promise<T>,
      success?: (value: T) => void,
      message?: string,
    ): Promise<T | undefined> => {
      if (lock.current) return undefined;
      lock.current = true;
      setPending(true);
      const startedOwner = ownerRef.current;
      try {
        const value = await work();
        if (mounted.current && ownerRef.current === startedOwner) {
          await query.invalidateQueries({ queryKey: [startedOwner] });
          success?.(value);
          if (message) toast(message);
        }
        return value;
      } catch (error) {
        if (mounted.current && ownerRef.current === startedOwner)
          toast(errorMessage(error, c), "error");
        return undefined;
      } finally {
        lock.current = false;
        if (mounted.current) setPending(false);
      }
    },
    [query, toast, c],
  );
  return { run, pending };
}
export function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
export function rows(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(record) : [];
}
export function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}
export function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}
