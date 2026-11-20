"""Point-in-time transaction features (D-25). Pure functions: arrays in, arrays out.

For a transaction at time t every history feature uses only events with time STRICTLY earlier
than t (same-timestamp events are excluded, which is conservative because the source has minute
resolution). No label column is accepted or produced here (T-INV-14).
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

FEATURE_SET_VERSION = "txnfeat-v1"
_M = np.int64(1) << 32  # composite key = entity * 2^32 + seconds (entity < 2^31, t < 2^32)

REQUIRED = [
    "t",  # int64 epoch seconds
    "origin",  # int64 account code
    "dest",  # int64 account code
    "origin_bank",
    "dest_bank",
    "amount",  # float64, as paid, in `currency`
    "currency",  # int category code
    "recv_amount",
    "recv_currency",
    "fmt",  # int category code (payment format)
]

FEATURE_NAMES: list[str] = [
    "o_out_1h",
    "o_out_24h",
    "o_out_all",
    "o_in_24h",
    "o_in_all",
    "o_secs_since_out",
    "o_secs_since_in",
    "d_in_24h",
    "d_in_all",
    "d_out_24h",
    "d_out_all",
    "d_secs_since_in",
    "pair_prior",
    "pair_reverse_prior",
    "amt_vs_own_mean",
    "log_amount",
    "same_bank",
    "self_transfer",
    "cross_currency",
    "recv_over_paid",
    "fmt",
    "currency",
    "hour_of_day",
]


@dataclass(frozen=True)
class _Events:
    """Sorted composite keys of (entity, t) with prefix sums of a value column."""

    comp: np.ndarray
    csum: np.ndarray  # csum[i] = sum of values of the first i events

    @classmethod
    def build(cls, entity: np.ndarray, t: np.ndarray, value: np.ndarray) -> "_Events":
        comp = entity.astype(np.int64) * _M + t.astype(np.int64)
        order = np.argsort(comp, kind="stable")
        v = value[order].astype(np.float64)
        return cls(comp[order], np.concatenate([[0.0], np.cumsum(v)]))

    def _bounds(
        self, q: np.ndarray, t: np.ndarray, window: int | None
    ) -> tuple[np.ndarray, np.ndarray]:
        q = q.astype(np.int64)
        t = t.astype(np.int64)
        hi = np.searchsorted(self.comp, q * _M + t, side="left")  # events strictly before t
        lo_t = np.zeros_like(t) if window is None else np.maximum(t - window, 0)
        lo = np.searchsorted(self.comp, q * _M + lo_t, side="left")
        return lo, hi

    def count(self, q: np.ndarray, t: np.ndarray, window: int | None) -> np.ndarray:
        lo, hi = self._bounds(q, t, window)
        return np.asarray(hi - lo, dtype=np.float64)

    def total(self, q: np.ndarray, t: np.ndarray, window: int | None) -> np.ndarray:
        lo, hi = self._bounds(q, t, window)
        return np.asarray(self.csum[hi] - self.csum[lo], dtype=np.float64)

    def secs_since_last(self, q: np.ndarray, t: np.ndarray) -> np.ndarray:
        """Seconds since the previous event of the entity strictly before t; NaN if none."""
        q = q.astype(np.int64)
        t = t.astype(np.int64)
        idx = np.searchsorted(self.comp, q * _M + t, side="left") - 1
        ok = idx >= 0
        prev = self.comp[np.where(ok, idx, 0)]
        same = ok & (prev // _M == q)
        return np.where(same, (t - prev % _M).astype(np.float64), np.nan)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    t = df["t"].to_numpy(np.int64)
    o = df["origin"].to_numpy(np.int64)
    d = df["dest"].to_numpy(np.int64)
    amt = df["amount"].to_numpy(np.float64)
    cur = df["currency"].to_numpy(np.int64)
    ones = np.ones(len(df))

    out_ev = _Events.build(o, t, ones)  # events where an account SENT
    in_ev = _Events.build(d, t, ones)  # events where an account RECEIVED
    hour, day = 3600, 86400

    f: dict[str, np.ndarray] = {}
    # origin account behaviour
    f["o_out_1h"] = out_ev.count(o, t, hour)
    f["o_out_24h"] = out_ev.count(o, t, day)
    f["o_out_all"] = out_ev.count(o, t, None)
    f["o_in_24h"] = in_ev.count(o, t, day)
    f["o_in_all"] = in_ev.count(o, t, None)
    f["o_secs_since_out"] = out_ev.secs_since_last(o, t)
    f["o_secs_since_in"] = in_ev.secs_since_last(o, t)
    # destination account behaviour
    f["d_in_24h"] = in_ev.count(d, t, day)
    f["d_in_all"] = in_ev.count(d, t, None)
    f["d_out_24h"] = out_ev.count(d, t, day)
    f["d_out_all"] = out_ev.count(d, t, None)
    f["d_secs_since_in"] = in_ev.secs_since_last(d, t)
    # pair history (origin -> dest, and the reverse direction)
    k = np.int64(1) << 21
    pair = _Events.build(o * k + d, t, ones)
    f["pair_prior"] = pair.count(o * k + d, t, None)
    f["pair_reverse_prior"] = pair.count(d * k + o, t, None)
    # amount relative to the sender's own history in the same currency
    key = o * 64 + cur
    hist = _Events.build(key, t, amt)
    n_prior = hist.count(key, t, None)
    mean_prior = np.where(n_prior > 0, hist.total(key, t, None) / np.maximum(n_prior, 1), np.nan)
    safe_mean = np.where(mean_prior > 0, mean_prior, np.nan)
    f["amt_vs_own_mean"] = amt / safe_mean
    # transaction-intrinsic features
    f["log_amount"] = np.log1p(amt)
    f["same_bank"] = (df["origin_bank"].to_numpy() == df["dest_bank"].to_numpy()).astype(np.float64)
    f["self_transfer"] = (o == d).astype(np.float64)
    f["cross_currency"] = (cur != df["recv_currency"].to_numpy(np.int64)).astype(np.float64)
    recv = df["recv_amount"].to_numpy(np.float64)
    f["recv_over_paid"] = recv / np.where(amt > 0, amt, np.nan)
    f["fmt"] = df["fmt"].to_numpy(np.float64)
    f["currency"] = cur.astype(np.float64)
    f["hour_of_day"] = ((t % day) // hour).astype(np.float64)
    return pd.DataFrame(f, index=df.index)[FEATURE_NAMES]
