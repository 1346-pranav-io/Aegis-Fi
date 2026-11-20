import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aegis.analytics.behaviour import BehaviourConfig, PeerTable, behaviour_profile
from aegis.analytics.graph import EdgeIndex

DAY = 86_400


def idx(edges: list[tuple[int, int, int, float]]) -> EdgeIndex:
    s, d, t, a = zip(*edges, strict=True)
    return EdgeIndex.build(
        np.array(s, dtype=np.int64),
        np.array(d, dtype=np.int64),
        np.array(t, dtype=np.int64),
        np.array(a, dtype=float),
    )


CFG = BehaviourConfig(min_baseline_txns=4, min_baseline_seconds=DAY, peer_buckets=2)


def steady_account() -> list[tuple[int, int, int, float]]:
    # account 0 sends 1 txn/day to account 1 for 10 days (amount 100), then bursts on day 10
    e = [(0, 1, d * DAY + 100, 100.0) for d in range(10)]
    e += [(0, 2 + i, 10 * DAY + 100 + i, 100.0) for i in range(8)]  # 8 txns, 8 new counterparties
    return e


def test_hand_computed_self_deviation() -> None:
    e = idx(steady_account())
    p = behaviour_profile(e, 0, (0, 10 * DAY), (10 * DAY, 11 * DAY), cfg=CFG)
    assert p["status"] == "OK" and p["baseline_txns"] == 10 and p["test_txns"] == 8
    base_days = (10 * DAY - 100) / DAY  # first seen at t=100
    expected = 10 / base_days
    assert p["expected_out_in_test"] == pytest.approx(expected)
    assert p["z_out_count"] == pytest.approx((8 - expected) / np.sqrt(expected + 1))
    assert p["new_counterparty_share"] == 1.0  # all 8 receivers are new vs baseline {1}
    assert p["z_log_amount_mean"] == pytest.approx(0.0)  # same amounts as baseline


def test_insufficient_history_is_reported_not_guessed() -> None:
    e = idx([(0, 1, 100, 5.0), (0, 1, 200, 5.0), (0, 2, DAY + 5, 5.0)])
    p = behaviour_profile(e, 0, (0, DAY), (DAY, 2 * DAY), cfg=CFG)
    assert p["status"] == "INSUFFICIENT_HISTORY"
    assert "z_out_count" not in p and p["baseline_txns"] == 2


def test_account_first_seen_too_recently_is_insufficient() -> None:
    e = idx([(0, 1, 9 * DAY + 10 + i, 1.0) for i in range(6)] + [(0, 1, 10 * DAY + 5, 1.0)])
    p = behaviour_profile(e, 0, (0, 10 * DAY), (10 * DAY, 11 * DAY), cfg=CFG)
    # first seen 10 s after day 9, so only DAY - 10 seconds of history before the test window
    assert p["status"] == "INSUFFICIENT_HISTORY"
    assert p["baseline_txns"] == 6


def test_peer_percentile_is_a_rank_within_activity_bucket() -> None:
    # accounts 0..3 have 5 baseline txns and test counts 0,1,2,3; sink account 9 (20 baseline,
    # 6 test) lands in the same bucket, so the peer counts are [0, 1, 2, 3, 6]
    edges = []
    for a in range(4):
        edges += [(a, 9, 10 + i, 1.0) for i in range(5)]
        edges += [(a, 9, 2 * DAY + i, 1.0) for i in range(a)]
    e = idx(edges)
    table = PeerTable.build(e, 10, (0, DAY), (2 * DAY, 3 * DAY), CFG)
    assert table.percentile(3) == pytest.approx(4 / 5) and table.percentile(0) == pytest.approx(
        1 / 5
    )
    assert table.percentile(4) is None or table.bucket[4] == 0  # inactive accounts form bucket 0


def test_window_order_validated() -> None:
    e = idx([(0, 1, 5, 1.0)])
    with pytest.raises(ValueError):
        behaviour_profile(e, 0, (0, 100), (50, 200), cfg=CFG)
    with pytest.raises(ValueError):
        behaviour_profile(e, 0, (0, 100), (100, 100), cfg=CFG)


@settings(max_examples=40, deadline=None)
@given(
    st.lists(
        st.tuples(st.integers(0, 4), st.integers(0, 4), st.integers(0, 5 * DAY), st.floats(1, 500)),
        min_size=1,
        max_size=40,
    )
)
def test_edges_after_test_window_never_change_profile(
    edges: list[tuple[int, int, int, float]],
) -> None:
    cfg = BehaviourConfig(min_baseline_txns=1, min_baseline_seconds=1, peer_buckets=2)
    base, test = (0, 2 * DAY), (2 * DAY, 3 * DAY)
    full = idx(edges)
    kept = [e for e in edges if e[2] < test[1]]
    if not kept:
        return
    part = idx(kept)
    for acct in range(5):
        assert behaviour_profile(full, acct, base, test, cfg=cfg) == behaviour_profile(
            part, acct, base, test, cfg=cfg
        )
