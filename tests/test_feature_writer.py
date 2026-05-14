# tests/test_feature_writer.py
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from ingestor.feature_writer import compute_derived_fields, build_raw_feature_row
from shared.orm import RawFeature, Market


def _make_prior_row(price_close: float, minutes_ago: float):
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    rf = RawFeature()
    rf.price_close = price_close
    rf.ts = ts
    return rf


def test_compute_momentum_positive_when_price_rising():
    prior_rows = [
        _make_prior_row(60000.0, 15),
        _make_prior_row(60500.0, 10),
        _make_prior_row(61000.0, 5),
        _make_prior_row(61200.0, 2),
        _make_prior_row(61300.0, 0.5),
    ]
    derived = compute_derived_fields(current_price=61500.0, prior_rows=prior_rows)
    assert derived["price_momentum_15m"] > 0
    assert derived["price_momentum_5m"] > 0
    assert derived["price_momentum_1m"] > 0


def test_compute_momentum_zero_when_no_prior_rows():
    derived = compute_derived_fields(current_price=61000.0, prior_rows=[])
    assert derived["price_momentum_1m"] == 0.0
    assert derived["price_momentum_5m"] == 0.0
    assert derived["price_momentum_15m"] == 0.0


def test_build_raw_feature_row_has_all_fields():
    row = build_raw_feature_row(
        market_id="KXBTCUSD-001",
        ts=datetime.now(timezone.utc),
        candle={"open": 61000.0, "high": 61500.0, "low": 60900.0, "close": 61200.0, "volume": 10.0},
        order_book={"bid_depth": 5.0, "ask_depth": 3.0, "book_imbalance": 0.25},
        kalshi_price={"yes_price": 0.57, "no_price": 0.43, "volume": 1500},
        derived={"price_momentum_1m": 0.002, "price_momentum_5m": 0.005,
                 "price_momentum_15m": 0.012, "volatility_5m": 0.003},
    )
    assert row.market_id == "KXBTCUSD-001"
    assert float(row.price_close) == pytest.approx(61200.0)
    assert float(row.book_imbalance) == pytest.approx(0.25)
    assert float(row.kalshi_yes_price) == pytest.approx(0.57)
    assert float(row.price_momentum_15m) == pytest.approx(0.012)
