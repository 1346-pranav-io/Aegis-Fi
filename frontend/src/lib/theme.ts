"use client";

// Theme: "system" follows the OS, "light" and "dark" are explicit. The stored value lives in localStorage under
// aegis.theme (absent = system). The first paint is handled by a tiny inline script in layout.tsx.

export type ThemeMode = "system" | "light" | "dark";
export type Resolved = "light" | "dark";

const EVENT = "aegis:theme";
const KEY = "aegis.theme";

export function getThemeMode(): ThemeMode {
  try {
    const v = window.localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function getResolved(): Resolved {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function systemTheme(): Resolved {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(mode: ThemeMode): void {
  try {
    if (mode === "system") window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, mode);
  } catch {
    /* storage blocked: the choice applies for this page view only */
  }
  document.documentElement.dataset.theme = mode === "system" ? systemTheme() : mode;
  window.dispatchEvent(new Event(EVENT));
}

/** Subscribe to theme changes, including the OS switching while the mode is "system". */
export function subscribeTheme(cb: () => void): () => void {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const onOs = () => {
    if (getThemeMode() === "system") {
      document.documentElement.dataset.theme = systemTheme();
      cb();
    }
  };
  window.addEventListener(EVENT, cb);
  mq.addEventListener("change", onOs);
  return () => {
    window.removeEventListener(EVENT, cb);
    mq.removeEventListener("change", onOs);
  };
}
