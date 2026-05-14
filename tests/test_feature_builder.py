# tests/test_feature_builder.py
import pytest
import numpy as np
from datetime import datetime, timezone, timedelta
from predictor.feature_builder import build_feature_vector, FEATURE_NAMES
from shared.orm import RawFeature


def _make_row(price: float, minutes_ago: float, kalshi_yes: float = 0.55,
              book_imbalance: float = 0.1, volume: float = 10.0, kalshi_vol: float = 500.0) -> RawFeature:
    r = RawFeature()
    r.ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    r.price_close = price
    r.price_open = price * 0.999
    r.price_high = price * 1.002
    r.price_low = price * 0.997
    r.volume = volume
    r.bid_depth_1pct = 5.0
    r.ask_depth_1pct = 4.0
    r.book_imbalance = book_imbalance
    r.kalshi_yes_price = kalshi_yes
    r.kalshi_no_price = 1 - kalshi_yes
    r.kalshi_volume = kalshi_vol
    return r


def _make_rows(n: int = 40) -> list:
    # Simulate gently rising prices over n*30s intervals
    return [_make_row(60000 + i * 10, minutes_ago=(n - i) * 0.5) for i in range(n)]


def test_feature_vector_has_25_elements():
    rows = _make_rows(40)
    vec, names = build_feature_vector(rows, minutes_to_close=7.0, ts=datetime.now(timezone.utc))
    assert vec is not None
    assert len(vec) == 25
    assert len(names) == 25


def test_feature_names_match_constant():
    rows = _make_rows(40)
    _, names = build_feature_vector(rows, minutes_to_close=7.0, ts=datetime.now(timezone.utc))
    assert names == FEATURE_NAMES


def test_returns_none_for_insufficient_rows():
    rows = _make_rows(2)
    result = build_feature_vector(rows, minutes_to_close=7.0, ts=datetime.now(timezone.utc))
    assert result is None


def test_minutes_to_close_is_in_vector():
    rows = _make_rows(40)
    vec, names = build_feature_vector(rows, minutes_to_close=3.5, ts=datetime.now(timezone.utc))
    idx = names.index("minutes_to_close")
    assert vec[idx] == pytest.approx(3.5)


def test_no_nan_in_vector():
    rows = _make_rows(40)
    vec, names = build_feature_vector(rows, minutes_to_close=7.0, ts=datetime.now(timezone.utc))
    assert not np.any(np.isnan(vec))


def test_feature_builder_uses_ts_not_wall_clock():
    """Momentum must be non-zero for rows timestamped 2 hours in the past."""
    past_ts = datetime.now(timezone.utc) - timedelta(hours=2)
    rows = [
        _make_row(60000 + i * 10, minutes_ago=0)  # we'll override ts manually
        for i in range(40)
    ]
    for i, r in enumerate(rows):
        r.ts = past_ts - timedelta(minutes=40 - i)  # rows span [past_ts-40m .. past_ts]
    result = build_feature_vector(rows, minutes_to_close=7.5, ts=past_ts)
    assert result is not None, "Should produce a vector for historical rows"
    vec, names = result
    mom_5m_idx = names.index("price_momentum_5m")
    assert vec[mom_5m_idx] != 0.0, (
        "price_momentum_5m must be non-zero — if 0, _within() is using datetime.now() "
        "instead of ts, returning empty windows for historical data"
    )
