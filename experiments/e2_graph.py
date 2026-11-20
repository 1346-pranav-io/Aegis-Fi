"""E2: Graph analytics - latency/size limits (R-05, NFR-PERF-02/05) and validation against the 370
IBM pattern blocks (D-23 gold). Read-only; reads the pattern file from data/raw/ibm-aml.

Validation is ORACLE-WINDOWED: each block's centre and time window come from the block's own
transactions (first sender, [first_t - 1 day, last_t + 1 day]). It asks "given the right window, do
the analytics expose the structure?", not "can we find blocks without knowing them". Controls are
random accounts with a window of the same length. Nothing here is a detector evaluation.

Run from backend/:
  AEGIS_APP_URL=postgresql://aegis_app:<pw>@127.0.0.1:5432/aegis uv run python ../experiments/e2_graph.py
"""

import csv
import json
import os
import re
import time
import tracemalloc
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import psycopg

from aegis.analytics.graph import DEFAULT_LIMITS, EdgeIndex, ego_metrics, ego_subgraph
from aegis.store.frames import load_transactions

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "ibm-aml"
DAY = 86400
RNG = np.random.default_rng(7)


def pct(a: list[float], q: float) -> float:
    return float(np.percentile(np.array(a), q)) if a else float("nan")


def parse_blocks() -> list[tuple[str, list[tuple[str, ...]]]]:
    blocks: list[tuple[str, list[tuple[str, ...]]]] = []
    cur: list[tuple[str, ...]] | None = None
    kind = ""
    for line in (RAW / "HI-Small_Patterns.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("BEGIN"):
            kind = re.sub(r":.*", "", line.replace("BEGIN LAUNDERING ATTEMPT - ", ""))
            cur = []
        elif line.startswith("END"):
            if cur is None:
                raise ValueError("END without BEGIN")
            blocks.append((kind, cur))
            cur = None
        elif line.strip() and cur is not None:
            cur.append(tuple(next(csv.reader([line]))))
    return blocks


def main() -> None:
    t0 = time.monotonic()
    with psycopg.connect(os.environ["AEGIS_APP_URL"]) as conn:
        cur = conn.execute("SELECT clock_timestamp()").fetchone()
        if cur is None:
            raise RuntimeError("no clock")
        raw = load_transactions(conn, cur[0])
    refs = raw.attrs["account_refs"]
    code = {r: i for i, r in enumerate(refs)}
    src, dst = raw["origin"].to_numpy(np.int64), raw["dest"].to_numpy(np.int64)
    t, amt = raw["t"].to_numpy(np.int64), raw["amount"].to_numpy(np.float64)
    print(f"loaded {len(raw):,} edges {time.monotonic() - t0:.0f}s", flush=True)

    tb = time.monotonic()
    index = EdgeIndex.build(src, dst, t, amt)
    out: dict[str, Any] = {
        "edges": len(raw),
        "accounts": len(refs),
        "index_build_s": time.monotonic() - tb,
    }

    # ---------- latency: random centres, 2 hops, 24h trailing window
    degree = np.bincount(np.concatenate([src, dst]), minlength=len(refs))
    sample_rows = RNG.integers(0, len(raw), 1500)
    lat, nedges, trunc, hub_flags = [], [], 0, []
    for r in sample_rows:
        c, te = int(src[r]), int(t[r])
        s0 = time.perf_counter()
        ego = ego_subgraph(index, c, 2, te - DAY, te + 1)
        ego_metrics(index, ego)
        lat.append((time.perf_counter() - s0) * 1000)
        nedges.append(len(ego.edge_ids))
        trunc += ego.truncated
        hub_flags.append(int(degree[c]) > 10_000)
    out["latency_random_centres_ms"] = {
        "n": len(lat), "p50": pct(lat, 50), "p95": pct(lat, 95), "p99": pct(lat, 99), "max": max(lat),
        "ego_edges_p50": pct([float(x) for x in nedges], 50), "ego_edges_p95": pct([float(x) for x in nedges], 95),
        "truncated_fraction": trunc / len(lat), "hub_centres_in_sample": int(sum(hub_flags)),
    }  # fmt: skip
    hubs = np.argsort(-degree)[:30]
    hl, ht = [], 0
    for c in hubs:
        te = int(t[RNG.integers(0, len(raw))])
        s0 = time.perf_counter()
        ego = ego_subgraph(index, int(c), 2, te - DAY, te + 1)
        ego_metrics(index, ego)
        hl.append((time.perf_counter() - s0) * 1000)
        ht += ego.truncated
    out["latency_top30_hubs_ms"] = {"p50": pct(hl, 50), "max": max(hl), "truncated_fraction": ht / len(hl),
                                    "max_degree": int(degree[hubs[0]])}  # fmt: skip
    print("latency done", out["latency_random_centres_ms"], flush=True)

    # ---------- whole-graph NetworkX scaling (why we use windowed ego graphs)
    scale = {}
    for n in (100_000, 500_000, 1_500_000):
        tracemalloc.start()
        s0 = time.perf_counter()
        g: nx.DiGraph[int] = nx.DiGraph()
        g.add_edges_from(zip(src[:n].tolist(), dst[:n].tolist(), strict=True))
        sec = time.perf_counter() - s0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        scale[str(n)] = {"build_s": sec, "peak_mb": peak / 1e6, "nodes": g.number_of_nodes()}
        del g
    out["networkx_whole_graph_build"] = scale
    print("scale", scale, flush=True)

    # ---------- validation on pattern blocks (oracle-windowed)
    blocks = parse_blocks()
    per_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for kind, rows in blocks:
        ts = []
        outs: dict[int, set[int]] = defaultdict(set)
        ins: dict[int, set[int]] = defaultdict(set)
        first_sender = None
        for i, rw in enumerate(rows):
            tt = int(datetime.strptime(rw[0], "%Y/%m/%d %H:%M").replace(tzinfo=UTC).timestamp())
            a, b = code[f"{rw[1]}|{rw[2]}"], code[f"{rw[3]}|{rw[4]}"]
            ts.append(tt)
            outs[a].add(b)
            ins[b].add(a)
            if tt == min(ts) and first_sender is None:
                first_sender = a
        hub_out = max(outs, key=lambda k: len(outs[k]))
        hub_in = max(ins, key=lambda k: len(ins[k]))
        win = (min(ts) - DAY, max(ts) + DAY)
        centre = {"FAN-OUT": hub_out, "FAN-IN": hub_in}.get(kind, first_sender)
        if centre is None:
            continue
        ego = ego_subgraph(index, int(centre), 2, win[0], win[1])
        m = ego_metrics(index, ego)
        per_type[kind].append(m)  # type: ignore[arg-type]
    # controls: random accounts with windows matching block-window length
    ctrl = []
    lengths = [max(x) - min(x) + 2 * DAY for x in ([int(datetime.strptime(r[0], "%Y/%m/%d %H:%M").replace(tzinfo=UTC).timestamp()) for r in rows] for _, rows in blocks)]  # fmt: skip
    for _ in range(600):
        r = int(RNG.integers(0, len(raw)))
        w = int(RNG.choice(lengths))
        ego = ego_subgraph(index, int(src[r]), 2, int(t[r]) - w // 2, int(t[r]) + w // 2)
        ctrl.append(ego_metrics(index, ego))
    per_type["CONTROL(random accounts)"] = ctrl

    def summ(ms: list[dict[str, Any]]) -> dict[str, Any]:
        ft = [m["flow_through_fraction"] for m in ms if m["flow_through_fraction"] is not None]
        return {
            "n": len(ms),
            "cycle_found_rate": float(np.mean([m["n_cycles_through_center"] > 0 for m in ms])),
            "median_out_distinct": float(np.median([m["center_out_distinct"] for m in ms])),
            "median_in_distinct": float(np.median([m["center_in_distinct"] for m in ms])),
            "median_flow_through": float(np.median(ft)) if ft else None,
            "median_scc_size": float(np.median([m["scc_size_of_center"] for m in ms])),
            "truncated_fraction": float(np.mean([m["truncated"] for m in ms])),
        }

    out["block_validation_oracle_windowed"] = {k: summ(v) for k, v in per_type.items()}
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    res = ROOT / "experiments" / "results" / f"e2_graph_{stamp}.json"
    res.write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    print(json.dumps(out["block_validation_oracle_windowed"], indent=1), flush=True)
    print(f"done {time.monotonic() - t0:.0f}s -> {res}", flush=True)


if __name__ == "__main__":
    main()
