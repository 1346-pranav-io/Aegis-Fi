"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

function parse<T extends Record<string, string>>(defaults: T, qs: string): T {
  const out: Record<string, string> = { ...defaults };
  const p = new URLSearchParams(qs);
  for (const k of Object.keys(defaults)) {
    const v = p.get(k);
    if (v !== null) out[k] = v;
  }
  return out as T;
}

/**
 * View state (filters, sort, search) mirrored into the query string, so a view survives a reload, can be
 * shared, and works with the back button. Values equal to their default are left out of the URL.
 *
 * The component's own copy updates immediately, so typing is never dropped while the URL catches up; the URL is
 * written with history.replaceState (no navigation, no refetch). A change to the URL that did not come from here
 * (back button, a link) replaces the local copy. `defaults` must be a module-level constant.
 */
export function useUrlState<T extends Record<string, string>>(defaults: T): [T, (patch: Partial<T>) => void] {
  const spKey = useSearchParams().toString();
  const path = usePathname();
  const [s, setS] = useState(() => ({ v: parse(defaults, spKey), written: spKey, seen: spKey }));

  let cur = s;
  if (spKey !== s.seen) {
    // The URL changed. If we wrote it, nothing to do; otherwise it came from outside, so adopt it.
    cur = spKey === s.written ? { ...s, seen: spKey } : { v: parse(defaults, spKey), written: spKey, seen: spKey };
    setS(cur);
  }

  // Keep the address bar in step with what this component last wrote.
  useEffect(() => {
    if (window.location.search.replace(/^\?/, "") !== cur.written) {
      window.history.replaceState(null, "", cur.written ? `${path}?${cur.written}` : path);
    }
  }, [cur.written, path]);

  const set = useCallback(
    (patch: Partial<T>) => {
      setS((prev) => {
        const next = { ...prev.v, ...patch } as T;
        const p = new URLSearchParams();
        for (const k of Object.keys(defaults)) if (next[k] !== defaults[k] && next[k] !== "") p.set(k, next[k]);
        return { v: next, written: p.toString(), seen: prev.seen };
      });
    },
    [defaults],
  );
  return [cur.v, set];
}
