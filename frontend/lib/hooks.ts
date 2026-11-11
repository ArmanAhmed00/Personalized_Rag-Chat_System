"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "./api";

export interface Loaded<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
  setData: (value: T | null) => void;
}

/** GET a path (null = don't), optionally polling every ``poll`` ms. */
export function useApi<T>(path: string | null, poll?: number | false): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState<boolean>(path !== null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    if (path === null) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const value = await api<T>(path, { signal: controller.signal });
        setData(value);
        setError(null);
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        setError(err instanceof ApiError ? err : new ApiError(0, "error", String(err)));
      } finally {
        setLoading(false);
      }
      if (poll && !controller.signal.aborted) timer = setTimeout(load, poll);
    };
    void load();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [path, poll, tick]);

  return { data, error, loading, reload, setData };
}

/** A query-string value, kept in sync with client-side navigation. */
export function useQueryParam(name: string): [string | null, (value: string | null, replace?: boolean) => void] {
  const [value, setValue] = useState<string | null>(null);

  useEffect(() => {
    const read = () => setValue(new URLSearchParams(window.location.search).get(name));
    read();
    window.addEventListener("popstate", read);
    window.addEventListener("outlawz:navigate", read);
    return () => {
      window.removeEventListener("popstate", read);
      window.removeEventListener("outlawz:navigate", read);
    };
  }, [name]);

  const update = useCallback(
    (next: string | null, replace = false) => {
      const url = new URL(window.location.href);
      if (next === null) url.searchParams.delete(name);
      else url.searchParams.set(name, next);
      const target = url.pathname + url.search;
      if (replace) window.history.replaceState(window.history.state, "", target);
      else window.history.pushState(window.history.state, "", target);
      window.dispatchEvent(new Event("outlawz:navigate"));
    },
    [name],
  );
  return [value, update];
}

/** True when the user asked the system to reduce motion. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(query.matches);
    const listen = () => setReduced(query.matches);
    query.addEventListener("change", listen);
    return () => query.removeEventListener("change", listen);
  }, []);
  return reduced;
}

/** Width of an element, updated as it resizes. */
export function useWidth<T extends HTMLElement>(fallback = 640): [React.RefObject<T | null>, number] {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (box && box.width > 0) setWidth(Math.round(box.width));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

/** Close on Escape. */
export function useEscape(onEscape: () => void, active = true): void {
  useEffect(() => {
    if (!active) return;
    const listen = (event: KeyboardEvent) => {
      if (event.key === "Escape") onEscape();
    };
    window.addEventListener("keydown", listen);
    return () => window.removeEventListener("keydown", listen);
  }, [onEscape, active]);
}

/** The browser tab title for a page. */
export function usePageTitle(title: string): void {
  useEffect(() => {
    document.title = title ? `${title} · Outlawz` : "Outlawz";
  }, [title]);
}
