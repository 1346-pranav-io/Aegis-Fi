"""Behaviour analytics for the Behaviour Agent (AG-04): self-history and peer-group deviation.

Pure functions over edge arrays (src, dst, t, amount) that the caller has already restricted to
what is visible at `as_of`. Windows are half-open [start, end) in epoch seconds. Insufficient
history is reported as such; no value is guessed (AG-04). Peer percentiles are ranks, not
probabilities (D-06). Amounts in different currencies are not converted; the amount measure is a
within-account comparison of log amounts and is only meaningful for accounts that mostly use one
currency (not enforced here).
"""

from dataclasses import dataclass

import numpy as np

from aegis.analytics.graph import EdgeIndex

ALGORITHM_VERSION = "behaviour-v1"


@dataclass(frozen=True)
class BehaviourConfig:
    min_baseline_txns: int = 10
    min_baseline_seconds: int = 86_400  # account must have been seen at least this long before
    peer_buckets: int = 10


DEFAULT_CONFIG = BehaviourConfig()


@dataclass(frozen=True)
class PeerTable:
    """Per-account transaction counts in the baseline and test windows plus bucket assignment."""

    n_base: np.ndarray
    n_test: np.ndarray
    bucket: np.ndarray
    bucket_edges: np.ndarray

    @classmethod
    def build(
        cls,
        edges: EdgeIndex,
        n_accounts: int,
        base: tuple[int, int],
        test: tuple[int, int],
        cfg: BehaviourConfig = DEFAULT_CONFIG,
    ) -> "PeerTable":
        def counts(w: tuple[int, int]) -> np.ndarray:
            m = (edges.t >= w[0]) & (edges.t < w[1])
            return np.bincount(edges.src[m], minlength=n_accounts) + np.bincount(
                edges.dst[m], minlength=n_accounts
            )

        n_base, n_test = counts(base), counts(test)
        active = n_base[n_base > 0]
        qs = (
            np.quantile(active, np.linspace(0, 1, cfg.peer_buckets + 1)[1:-1])
            if len(active)
            else []
        )
        edges_q = np.unique(np.asarray(qs, dtype=np.float64))
        bucket = np.where(n_base > 0, np.searchsorted(edges_q, n_base, side="right") + 1, 0)
        return cls(n_base, n_test, bucket, edges_q)

    def percentile(self, account: int) -> float | None:
        """Rank of the account's test-window count among peers in its baseline-activity bucket
        (fraction of peers with a count <= its own). A rank, not a probability."""
        b = self.bucket[account]
        peers = self.n_test[self.bucket == b]
        if len(peers) < 2:
            return None
        return float(np.mean(peers <= self.n_test[account]))


def behaviour_profile(
    edges: EdgeIndex,
    account: int,
    base: tuple[int, int],
    test: tuple[int, int],
    peers: PeerTable | None = None,
    cfg: BehaviourConfig = DEFAULT_CONFIG,
) -> dict[str, object]:
    """Evidence-ready behaviour summary for one account; `base` must end at or before `test`."""
    if base[1] > test[0]:
        raise ValueError("baseline window must end before the test window starts")
    if test[1] <= test[0] or base[1] <= base[0]:
        raise ValueError("empty window")
    b_ids = edges.incident(account, base[0], base[1])
    t_ids = edges.incident(account, test[0], test[1])
    res: dict[str, object] = {
        "algorithm": ALGORITHM_VERSION,
        "params": {
            "base": list(base),
            "test": list(test),
            "min_baseline_txns": cfg.min_baseline_txns,
        },
        "baseline_txns": int(len(b_ids)),
        "test_txns": int(len(t_ids)),
    }
    first_seen = int(edges.t[b_ids].min()) if len(b_ids) else None
    if (
        len(b_ids) < cfg.min_baseline_txns
        or first_seen is None
        or test[0] - first_seen < cfg.min_baseline_seconds
    ):
        res["status"] = "INSUFFICIENT_HISTORY"
        if peers is not None:
            res["peer_percentile_of_test_count"] = peers.percentile(account)
        return res

    res["status"] = "OK"
    base_days = max((base[1] - first_seen) / 86_400.0, 1e-9)
    test_days = (test[1] - test[0]) / 86_400.0

    def counts(ids: np.ndarray) -> tuple[int, int]:
        s, d = edges.src[ids], edges.dst[ids]
        return int(((s == account) & (d != account)).sum()), int(
            ((d == account) & (s != account)).sum()
        )

    b_out, b_in = counts(b_ids)
    t_out, t_in = counts(t_ids)
    for name, b, t in (("out", b_out, t_out), ("in", b_in, t_in)):
        expected = b / base_days * test_days
        res[f"expected_{name}_in_test"] = expected
        res[f"z_{name}_count"] = (t - expected) / float(np.sqrt(expected + 1.0))

    def counterparties(ids: np.ndarray) -> set[int]:
        s, d = edges.src[ids], edges.dst[ids]
        return {int(x) for x in np.where(s == account, d, s) if int(x) != account}

    base_cp, test_cp = counterparties(b_ids), counterparties(t_ids)
    res["new_counterparty_share"] = (
        float(len(test_cp - base_cp) / len(test_cp)) if test_cp else None
    )
    la_b = np.log1p(edges.amount[b_ids])
    la_t = np.log1p(edges.amount[t_ids])
    if len(t_ids):
        sd = max(float(la_b.std()), 0.1)  # floor: avoids dividing by ~0 for constant amounts
        res["z_log_amount_mean"] = float((la_t.mean() - la_b.mean()) / (sd / np.sqrt(len(t_ids))))
    else:
        res["z_log_amount_mean"] = None
    if peers is not None:
        res["peer_percentile_of_test_count"] = peers.percentile(account)
        res["peer_bucket"] = int(peers.bucket[account])
    return res
