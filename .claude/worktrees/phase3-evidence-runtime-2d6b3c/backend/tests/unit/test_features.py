import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aegis.analytics.features import FEATURE_NAMES, build_features


def frame(rows: list[tuple[int, int, int, float]]) -> pd.DataFrame:
    """rows: (t, origin, dest, amount); one currency, one bank, one format."""
    t, o, d, a = zip(*rows, strict=True)
    n = len(rows)
    return pd.DataFrame(
        {
            "t": np.array(t, dtype=np.int64),
            "origin": o,
            "dest": d,
            "origin_bank": [1] * n,
            "dest_bank": [1] * n,
            "amount": np.array(a, dtype=float),
            "currency": [0] * n,
            "recv_amount": np.array(a, dtype=float),
            "recv_currency": [0] * n,
            "fmt": [0] * n,
        }
    )


def test_hand_computed_history_features() -> None:
    # account 1 sends at t=100,200,200,90000; account 2 sends to 1 at t=150
    df = frame(
        [
            (100, 1, 2, 10.0),
            (150, 2, 1, 5.0),
            (200, 1, 2, 30.0),
            (200, 1, 3, 1.0),
            (90000, 1, 2, 20.0),
        ]
    )
    f = build_features(df)
    assert list(f.columns) == FEATURE_NAMES
    # row 2 (t=200, 1->2): strictly earlier outs of acct 1 = only t=100 (same-time row excluded)
    assert f.loc[2, "o_out_all"] == 1 and f.loc[3, "o_out_all"] == 1
    assert f.loc[2, "o_in_all"] == 1  # received from acct 2 at t=150
    assert f.loc[2, "o_secs_since_out"] == 100 and f.loc[2, "o_secs_since_in"] == 50
    assert f.loc[2, "pair_prior"] == 1 and f.loc[2, "pair_reverse_prior"] == 1
    assert f.loc[2, "amt_vs_own_mean"] == pytest.approx(30.0 / 10.0)
    # row 4 (t=90000): 24h window [3600, 90000) excludes t=100/150/200 -> 0; all-history = 3
    assert f.loc[4, "o_out_24h"] == 0 and f.loc[4, "o_out_all"] == 3
    assert f.loc[4, "amt_vs_own_mean"] == pytest.approx(20.0 / ((10 + 30 + 1) / 3))
    # first-ever event has no history
    assert f.loc[0, "o_out_all"] == 0 and np.isnan(f.loc[0, "o_secs_since_out"])
    assert np.isnan(f.loc[0, "amt_vs_own_mean"])
    # destination perspective: acct 2 had received at t=100 before row 2 at t=200
    assert f.loc[2, "d_in_all"] == 1 and f.loc[2, "d_secs_since_in"] == 100
    assert f.loc[2, "d_out_all"] == 1  # acct 2 sent at t=150


def test_window_boundary_is_inclusive_of_start_exclusive_of_now() -> None:
    df = frame([(0, 1, 2, 1.0), (3600, 1, 2, 1.0), (7200, 1, 2, 1.0)])
    f = build_features(df)
    # at t=7200, the 1h window is [3600, 7200): includes the t=3600 event only
    assert f.loc[2, "o_out_1h"] == 1


@settings(max_examples=60, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.integers(0, 10_000), st.integers(0, 4), st.integers(0, 4), st.floats(0.01, 1000)
        ),
        min_size=1,
        max_size=40,
    ),
    st.integers(0, 10_000),
)
def test_future_events_never_change_past_features(
    rows: list[tuple[int, int, int, float]], cut: int
) -> None:
    """T-INV-03 for features: events at or after the cut cannot change features of earlier rows."""
    df = frame(rows)
    full = build_features(df)
    past = df[df["t"] < cut]
    if past.empty:
        return
    partial = build_features(past)
    pd.testing.assert_frame_equal(full.loc[past.index], partial, check_exact=False, rtol=1e-12)


def test_missing_columns_rejected() -> None:
    with pytest.raises(ValueError):
        build_features(pd.DataFrame({"t": [1]}))
