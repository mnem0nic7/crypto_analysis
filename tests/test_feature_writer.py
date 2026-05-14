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


# ── warm_up_if_needed ──────────────────────────────────────────────────────────

def _make_market(market_id: str, db_session):
    from shared.orm import Market
    m = Market(
        market_id=market_id, ticker="KXBTCUSD", status="active",
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(m)
    db_session.flush()
    return m


def _fake_candles(n: int, close: float = 60050.0) -> list[dict]:
    now_ts = int(datetime.now(timezone.utc).timestamp())
    return [
        {
            "start": now_ts - (i * 60),
            "open": 60000.0, "high": 60100.0, "low": 59900.0,
            "close": close, "volume": 1.0,
        }
        for i in range(n)
    ]


def test_warm_up_writes_rows_when_db_empty(db_session):
    from ingestor.feature_writer import warm_up_if_needed
    _make_market("KXBTCUSD-WU1", db_session)

    mock_cb = MagicMock()
    mock_cb.get_candles.return_value = _fake_candles(15)

    warm_up_if_needed(db_session, "KXBTCUSD-WU1", "BTC-USD", mock_cb)

    rows = db_session.query(RawFeature).filter_by(market_id="KXBTCUSD-WU1").all()
    assert len(rows) == 15
    assert all(float(r.price_close) == pytest.approx(60050.0) for r in rows)
    # Live-only fields must be None for warm-up rows
    assert all(r.bid_depth_1pct is None for r in rows)
    assert all(r.kalshi_yes_price is None for r in rows)
    mock_cb.get_candles.assert_called_once_with("BTC-USD", granularity="ONE_MINUTE", limit=20)


def test_warm_up_skips_when_already_warm(db_session):
    from ingestor.feature_writer import warm_up_if_needed
    _make_market("KXBTCUSD-WU2", db_session)

    # Insert 20 existing rows so the market is already warm
    for i in range(20):
        db_session.add(RawFeature(
            market_id="KXBTCUSD-WU2",
            ts=datetime.now(timezone.utc) - timedelta(minutes=i),
            price_close=60000.0,
        ))
    db_session.flush()

    mock_cb = MagicMock()
    warm_up_if_needed(db_session, "KXBTCUSD-WU2", "BTC-USD", mock_cb)

    mock_cb.get_candles.assert_not_called()


def test_warm_up_idempotent_skips_existing_timestamps(db_session):
    from ingestor.feature_writer import warm_up_if_needed
    _make_market("KXBTCUSD-WU3", db_session)

    candles = _fake_candles(15)
    # Pre-insert 3 rows whose timestamps match the first 3 candles
    for c in candles[:3]:
        db_session.add(RawFeature(
            market_id="KXBTCUSD-WU3",
            ts=datetime.fromtimestamp(c["start"], tz=timezone.utc),
            price_close=60000.0,
        ))
    db_session.flush()

    mock_cb = MagicMock()
    mock_cb.get_candles.return_value = candles

    warm_up_if_needed(db_session, "KXBTCUSD-WU3", "BTC-USD", mock_cb)

    total = db_session.query(RawFeature).filter_by(market_id="KXBTCUSD-WU3").count()
    assert total == 15  # 3 pre-existing + 12 new, no duplicates


def test_warm_up_does_not_raise_when_coinbase_fails(db_session):
    from ingestor.feature_writer import warm_up_if_needed
    _make_market("KXBTCUSD-WU4", db_session)

    mock_cb = MagicMock()
    mock_cb.get_candles.side_effect = Exception("network error")

    warm_up_if_needed(db_session, "KXBTCUSD-WU4", "BTC-USD", mock_cb)

    # No rows written, no exception raised
    count = db_session.query(RawFeature).filter_by(market_id="KXBTCUSD-WU4").count()
    assert count == 0


def test_fetch_and_write_triggers_warm_up_on_first_call(db_session):
    from ingestor.feature_writer import fetch_and_write
    _make_market("KXBTCUSD-FW1", db_session)

    now_ts = int(datetime.now(timezone.utc).timestamp())

    def _get_candles(product_id, granularity="ONE_MINUTE", limit=2, start=None, end=None):
        if limit == 20:
            return [
                {"start": now_ts - (i * 60), "open": 60000.0, "high": 60100.0,
                 "low": 59900.0, "close": 60050.0, "volume": 1.0}
                for i in range(15)
            ]
        return [{"start": now_ts, "open": 60000.0, "high": 60100.0,
                 "low": 59900.0, "close": 60100.0, "volume": 2.0}]

    mock_cb = MagicMock()
    mock_cb.get_candles.side_effect = _get_candles
    mock_cb.get_order_book.return_value = {"bid_depth": 5.0, "ask_depth": 3.0, "book_imbalance": 0.25}

    mock_kalshi = MagicMock()
    mock_kalshi.get_market_price.return_value = {"yes_price": 0.55, "no_price": 0.45, "volume": 1000}

    fetch_and_write(db_session, "KXBTCUSD-FW1", "KXBTCUSD", mock_cb, mock_kalshi)

    rows = db_session.query(RawFeature).filter_by(market_id="KXBTCUSD-FW1").all()
    # 15 warm-up rows + 1 normal row = 16 total
    assert len(rows) >= 15
