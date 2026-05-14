# tests/test_historical_backfill.py
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from shared.orm import Market, RawFeature, Prediction


def _make_kalshi_market(ticker, series_ticker, close_time, result="yes"):
    open_time = close_time - timedelta(minutes=15)
    return {
        "ticker": ticker,
        "series_ticker": series_ticker,
        "title": f"{ticker} test",
        "open_time": open_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "close_time": close_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "result": result,
        "floor_strike": 80000.0,
        "expiration_value": 81000.0 if result == "yes" else 79000.0,
    }


def _make_candle(unix_ts, price=80000.0):
    return {
        "start": unix_ts,
        "open": price - 10, "high": price + 20, "low": price - 20, "close": price,
        "volume": 5.0,
    }


def _build_candle_cache(base_dt, n=50):
    base_unix = int(base_dt.timestamp())
    return {base_unix - (n - i) * 60: _make_candle(base_unix - (n - i) * 60, 80000 + i * 10)
            for i in range(n)}


def test_process_one_market_inserts_rows(db_session):
    from ingestor.historical_backfill import _process_one_market
    close_time = datetime.now(timezone.utc) - timedelta(hours=2)
    market_dict = _make_kalshi_market("KXBTC15M-TEST-00", "KXBTC15M", close_time, result="yes")
    candle_cache = _build_candle_cache(close_time, n=50)

    result = _process_one_market(db_session, market_dict, "KXBTC15M", candle_cache)
    assert result == 1

    market = db_session.get(Market, "KXBTC15M-TEST-00")
    assert market is not None
    assert market.ticker == "KXBTC15M"
    assert market.status == "settled"

    rf_count = db_session.query(RawFeature).filter_by(market_id="KXBTC15M-TEST-00").count()
    assert rf_count >= 10

    pred = db_session.query(Prediction).filter_by(market_id="KXBTC15M-TEST-00").first()
    assert pred is not None
    assert pred.actual_outcome == 1  # result == "yes"
    assert pred.low_confidence is False
    assert pred.model_version == "backfill"


def test_process_one_market_result_no_gives_outcome_zero(db_session):
    from ingestor.historical_backfill import _process_one_market
    close_time = datetime.now(timezone.utc) - timedelta(hours=3)
    market_dict = _make_kalshi_market("KXBTC15M-TEST-01", "KXBTC15M", close_time, result="no")
    candle_cache = _build_candle_cache(close_time, n=50)

    _process_one_market(db_session, market_dict, "KXBTC15M", candle_cache)
    pred = db_session.query(Prediction).filter_by(market_id="KXBTC15M-TEST-01").first()
    assert pred.actual_outcome == 0


def test_process_one_market_is_idempotent(db_session):
    from ingestor.historical_backfill import _process_one_market
    close_time = datetime.now(timezone.utc) - timedelta(hours=4)
    market_dict = _make_kalshi_market("KXBTC15M-TEST-02", "KXBTC15M", close_time)
    candle_cache = _build_candle_cache(close_time, n=50)

    assert _process_one_market(db_session, market_dict, "KXBTC15M", candle_cache) == 1
    assert _process_one_market(db_session, market_dict, "KXBTC15M", candle_cache) == 0  # skipped


def test_process_one_market_skips_empty_result(db_session):
    from ingestor.historical_backfill import _process_one_market
    close_time = datetime.now(timezone.utc) - timedelta(hours=5)
    market_dict = _make_kalshi_market("KXBTC15M-TEST-03", "KXBTC15M", close_time, result="")
    candle_cache = _build_candle_cache(close_time, n=50)

    result = _process_one_market(db_session, market_dict, "KXBTC15M", candle_cache)
    assert result == 0  # empty result skipped


def test_process_one_market_skips_when_too_few_candles(db_session):
    from ingestor.historical_backfill import _process_one_market
    close_time = datetime.now(timezone.utc) - timedelta(hours=6)
    market_dict = _make_kalshi_market("KXBTC15M-TEST-04", "KXBTC15M", close_time)

    # pred_ts = open_time + 7m30s = (close_time - 15m) + 7m30s = close_time - 7m30s
    # window is [pred_ts - 40min, pred_ts); anchor 3 candles inside that window
    open_time = close_time - timedelta(minutes=15)
    pred_ts = open_time + timedelta(minutes=7, seconds=30)
    base_unix = int((pred_ts - timedelta(minutes=5)).timestamp())
    sparse_cache = {base_unix - i * 60: _make_candle(base_unix - i * 60) for i in range(3)}

    result = _process_one_market(db_session, market_dict, "KXBTC15M", sparse_cache)
    assert result == 0
