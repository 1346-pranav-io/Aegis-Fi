"use client";

import { useEffect, useState } from "react";
import { api, type CaseDetail } from "./api";

/**
 * Details for a handful of cases (the API has no aggregate endpoint, so the dashboard samples the top of the queue).
 * Fetches a few at a time, ignores a superseded fetch, and keeps the last good result visible while refreshing.
 * `failed` counts cases that could not be read, so a partial sample is never presented as complete.
 */
export function useCaseSample(ids: string[], refreshKey: number): { cases: CaseDetail[]; loading: boolean; failed: number } {
  const key = `${ids.join(",")}|${refreshKey}`;
  const [res, setRes] = useState<{ key: string; cases: CaseDetail[]; failed: number } | null>(null);

  useEffect(() => {
    if (!ids.length) return;
    let live = true;
    (async () => {
      const cases: CaseDetail[] = [];
      let failed = 0;
      for (let i = 0; i < ids.length; i += 4) {
        const got = await Promise.allSettled(ids.slice(i, i + 4).map((id) => api<CaseDetail>(`/cases/${id}`)));
        for (const g of got) {
          if (g.status === "fulfilled") cases.push(g.value);
          else failed += 1;
        }
        if (!live) return;
      }
      setRes({ key, cases, failed });
    })();
    return () => {
      live = false;
    };
    // `ids` is represented by `key`; the array identity changes on every render of the caller.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return { cases: res?.cases ?? [], failed: res?.failed ?? 0, loading: ids.length > 0 && res?.key !== key };
}
