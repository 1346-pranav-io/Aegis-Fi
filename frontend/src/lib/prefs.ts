"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

// Small persisted preferences (sidebar, refresh interval). localStorage is the source of truth and every tab/page
// sees changes through one subscription, so no component needs its own copy in state.
const listeners = new Set<() => void>();

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  window.addEventListener("storage", cb);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", cb);
  };
}

export function usePref(key: string, fallback: string): [string, (v: string) => void] {
  const value = useSyncExternalStore(
    subscribe,
    () => {
      try {
        return window.localStorage.getItem(key) ?? fallback;
      } catch {
        return fallback; // storage can be blocked (private mode); preferences then just do not persist
      }
    },
    () => fallback,
  );
  const set = (v: string) => {
    try {
      window.localStorage.setItem(key, v);
    } catch {
      /* ignore: see above */
    }
    listeners.forEach((f) => f());
  };
  return [value, set];
}

export const REFRESH_OPTIONS = [
  { value: "0", label: "Off" },
  { value: "5000", label: "5 seconds" },
  { value: "10000", label: "10 seconds" },
  { value: "30000", label: "30 seconds" },
];

/** Live-refresh interval in ms (0 = off). Shared by every page that shows live data. */
export function useRefreshMs(): [number, (ms: number) => void] {
  const [v, set] = usePref("aegis.refresh", "10000");
  return [Number(v) || 0, (ms) => set(String(ms))];
}

/** A counter that increments every `ms` while the tab is visible. Pass it to useLoad as the refresh key. */
export function useTick(ms: number): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (ms <= 0) return;
    const id = window.setInterval(() => {
      if (!document.hidden) setN((x) => x + 1);
    }, ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return n;
}

/** Width of an element, kept current with a ResizeObserver. Pass the returned callback as the element's `ref`. */
export function useElementWidth<T extends HTMLElement>(): [(el: T | null) => void, number] {
  const [el, setEl] = useState<T | null>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return [setEl, w];
}
