# tests/test_trainer.py
import pytest
import numpy as np
from datetime import datetime, timezone, timedelta
from trainer.dataset import build_training_dataset
from trainer.backfill import backfill_outcomes
from shared.orm import Market, RawFeature, Prediction


def _seed_series_data(session, series_ticker="KXBTC15M", n_contracts=3, market_id_prefix="KXBTC15M-TEST"):
    """Create n_contracts contracts for a series, each with raw_features and settled predictions."""
    now = datetime.now(timezone.utc)
    for c in range(n_contracts):
        market_id = f"{market_id_prefix}-{c:02d}"
        base_ts = now - timedelta(hours=n_contracts - c)
        market = Market(
            market_id=market_id,
            ticker=series_ticker,
            status="settled",
            close_time=base_ts + timedelta(minutes=15),
            discovered_at=base_ts,
            updated_at=base_ts,
        )
        session.add(market)
        session.flush()

        for i in range(20):
            rf = RawFeature()
            rf.market_id = market_id
            rf.ts = base_ts + timedelta(minutes=i * 0.5)
            rf.price_close = 60000 + c * 1000 + i * 20
            rf.price_open  = rf.price_close - 10
            rf.price_high  = rf.price_close + 30
            rf.price_low   = rf.price_close - 30
            rf.volume = 10.0
            rf.bid_depth_1pct = 5.0
            rf.ask_depth_1pct = 4.0
            rf.book_imbalance = 0.1
            rf.kalshi_yes_price = 0.55
            rf.kalshi_no_price = 0.45
            rf.kalshi_volume = 500.0
            session.add(rf)
        session.flush()

        for i in range(7):
            p = Prediction()
            p.market_id = market_id
            p.ts = base_ts + timedelta(minutes=i * 1.5)
            p.direction = "UP" if i % 2 == 0 else "DOWN"
            p.confidence = 0.6
            p.low_confidence = False
            p.model_version = "v0"
            p.settled_at = base_ts + timedelta(minutes=15)
            p.actual_outcome = 1 if i % 2 == 0 else 0
            session.add(p)
    session.flush()


def test_build_training_dataset_aggregates_across_contracts(db_session):
    """Training data must come from ALL contracts of the series, not just one."""
    _seed_series_data(db_session, series_ticker="KXBTC15M", n_contracts=3, market_id_prefix="KXBTC15M-AGG")
    result = build_training_dataset(db_session, "KXBTC15M", lookback_hours=2160)
    assert result is not None
    X, y = result
    # 3 contracts × 7 predictions each = 21 total, minus any filtered by _MIN_ROWS
    assert len(y) >= 5  # at least _MIN_SAMPLES
    assert X.shape[1] == 25
    assert set(y).issubset({0, 1})


def test_build_training_dataset_returns_none_for_empty_series(db_session):
    result = build_training_dataset(db_session, "KXBTC15M-NOSUCHSERIES", lookback_hours=2160)
    assert result is None


def test_build_training_dataset_returns_none_when_all_low_confidence(db_session):
    series_ticker = "KXBTC15M-LC"
    market = Market(
        market_id="KXBTC15M-LC-00", ticker=series_ticker, status="settled",
        close_time=datetime.now(timezone.utc),
        discovered_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    db_session.add(market)
    db_session.flush()
    for i in range(10):
        p = Prediction()
        p.market_id = "KXBTC15M-LC-00"
        p.ts = datetime.now(timezone.utc) - timedelta(minutes=10 - i)
        p.direction = "UP"
        p.confidence = 0.5
        p.low_confidence = True   # all filtered out
        p.model_version = "none"
        p.settled_at = datetime.now(timezone.utc)
        p.actual_outcome = 1
        db_session.add(p)
    db_session.flush()
    result = build_training_dataset(db_session, series_ticker, lookback_hours=2160)
    assert result is None


# ── train_and_promote tests ──────────────────────────────────────────────────

from trainer.train import train_and_promote


def test_train_and_promote_creates_model_file(db_session, tmp_path):
    _seed_series_data(db_session, series_ticker="KXBTC15M-TN", n_contracts=3, market_id_prefix="KXBTC15M-TN")
    result = train_and_promote(
        session=db_session,
        series_ticker="KXBTC15M-TN",
        lookback_hours=2160,
        models_dir=str(tmp_path),
    )
    assert result is not None
    assert result["promoted"] in (True, False)
    assert "brier_score" in result
    # Model stored under series ticker, not contract ticker
    assert result["series_ticker"] == "KXBTC15M-TN"


def test_train_and_promote_creates_sentinel_market_row(db_session, tmp_path):
    _seed_series_data(db_session, series_ticker="KXBTC15M-SM", n_contracts=3, market_id_prefix="KXBTC15M-SM")
    train_and_promote(
        session=db_session, series_ticker="KXBTC15M-SM",
        lookback_hours=2160, models_dir=str(tmp_path),
    )
    sentinel = db_session.get(Market, "KXBTC15M-SM")
    assert sentinel is not None
    assert sentinel.status == "series"
    assert sentinel.ticker == "KXBTC15M-SM"


def test_train_does_not_promote_when_worse(db_session, tmp_path):
    from shared.orm import ModelRegistry
    _seed_series_data(db_session, series_ticker="KXBTC15M-NP", n_contracts=3, market_id_prefix="KXBTC15M-NP")
    # Create sentinel + near-perfect existing model
    sentinel = Market(
        market_id="KXBTC15M-NP", ticker="KXBTC15M-NP", status="series",
        discovered_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    db_session.add(sentinel)
    db_session.flush()
    reg = ModelRegistry(
        market_id="KXBTC15M-NP",
        version="v0",
        trained_at=datetime.now(timezone.utc),
        training_rows=100,
        brier_score=0.001,
        artifact_path=str(tmp_path / "dummy.joblib"),
        is_active=True,
    )
    db_session.add(reg)
    db_session.flush()
    result = train_and_promote(
        session=db_session, series_ticker="KXBTC15M-NP",
        lookback_hours=2160, models_dir=str(tmp_path),
    )
    assert result["promoted"] is False


def test_train_does_not_promote_underfilled_candidate(db_session, tmp_path):
    from shared.orm import ModelRegistry
    series_ticker = "KXBTC15M-UC"
    _seed_series_data(
        db_session,
        series_ticker=series_ticker,
        n_contracts=3,
        market_id_prefix=series_ticker,
    )
    sentinel = Market(
        market_id=series_ticker, ticker=series_ticker, status="series",
        discovered_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    db_session.add(sentinel)
    db_session.flush()
    old_reg = ModelRegistry(
        market_id=series_ticker,
        version="v0",
        trained_at=datetime.now(timezone.utc),
        training_rows=500,
        brier_score=1.0,
        artifact_path=str(tmp_path / "active.joblib"),
        is_active=True,
    )
    db_session.add(old_reg)
    db_session.flush()

    result = train_and_promote(
        session=db_session, series_ticker=series_ticker,
        lookback_hours=2160, models_dir=str(tmp_path),
    )

    assert result["promoted"] is False
    db_session.refresh(old_reg)
    assert old_reg.is_active is True


def test_train_promotes_over_underfilled_active_model(db_session, tmp_path):
    from shared.orm import ModelRegistry
    series_ticker = "KXBTC15M-UF"
    _seed_series_data(
        db_session,
        series_ticker=series_ticker,
        n_contracts=30,
        market_id_prefix=series_ticker,
    )
    sentinel = Market(
        market_id=series_ticker, ticker=series_ticker, status="series",
        discovered_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    db_session.add(sentinel)
    db_session.flush()
    old_reg = ModelRegistry(
        market_id=series_ticker,
        version="v0",
        trained_at=datetime.now(timezone.utc),
        training_rows=80,
        brier_score=0.001,
        artifact_path=str(tmp_path / "underfilled.joblib"),
        is_active=True,
    )
    db_session.add(old_reg)
    db_session.flush()

    result = train_and_promote(
        session=db_session, series_ticker=series_ticker,
        lookback_hours=2160, models_dir=str(tmp_path),
    )

    assert result["promoted"] is True
    db_session.refresh(old_reg)
    assert old_reg.is_active is False


# ── backfill_outcomes tests ─────────────────────────────────────────────────

def test_backfill_sets_actual_outcome(db_session):
    market_id = "KXBTC15M-BF-00"
    market = Market(
        market_id=market_id, ticker="KXBTC15M", status="settled",
        close_time=datetime.now(timezone.utc) + timedelta(hours=1),
        discovered_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    db_session.add(market)
    db_session.flush()

    ts_pred   = datetime.now(timezone.utc) - timedelta(minutes=20)
    ts_settle = ts_pred + timedelta(minutes=15)

    rf_pred = RawFeature()
    rf_pred.market_id = market_id
    rf_pred.ts = ts_pred
    rf_pred.price_close = 60000
    db_session.add(rf_pred)

    rf_settle = RawFeature()
    rf_settle.market_id = market_id
    rf_settle.ts = ts_settle
    rf_settle.price_close = 61000
    db_session.add(rf_settle)
    db_session.flush()

    pred = Prediction(
        market_id=market_id, ts=ts_pred, direction="UP",
        confidence=0.65, low_confidence=False, model_version="v1",
        feature_snapshot_id=rf_pred.id, settled_at=ts_settle, actual_outcome=None,
    )
    db_session.add(pred)
    db_session.flush()

    count = backfill_outcomes(db_session)
    assert count == 1
    db_session.expire(pred)
    assert pred.actual_outcome == 1


def test_backfill_skips_already_settled(db_session):
    market_id = "KXBTC15M-SK-00"
    market = Market(
        market_id=market_id, ticker="KXBTC15M", status="settled",
        close_time=datetime.now(timezone.utc) + timedelta(hours=1),
        discovered_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    db_session.add(market)
    ts_pred = datetime.now(timezone.utc) - timedelta(minutes=20)
    pred = Prediction(
        market_id=market_id, ts=ts_pred, direction="DOWN",
        confidence=0.6, low_confidence=False, model_version="v1",
        settled_at=ts_pred + timedelta(minutes=15), actual_outcome=0,
    )
    db_session.add(pred)
    db_session.flush()
    assert backfill_outcomes(db_session) == 0
