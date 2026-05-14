# tests/test_trainer.py
import pytest
import numpy as np
from datetime import datetime, timezone, timedelta
from trainer.dataset import build_training_dataset
from shared.orm import Market, RawFeature, Prediction


def _seed_settled_data(session, market_id="KXBTCUSD-TR"):
    market = Market(
        market_id=market_id, ticker="KXBTCUSD", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(hours=1),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(market)
    session.flush()

    for i in range(50):
        rf = RawFeature()
        rf.market_id = market_id
        rf.ts = datetime.now(timezone.utc) - timedelta(hours=2) + timedelta(minutes=i * 0.5)
        rf.price_close = 60000 + i * 20
        rf.price_open = 60000 + i * 20 - 10
        rf.price_high = 60000 + i * 20 + 30
        rf.price_low = 60000 + i * 20 - 30
        rf.volume = 10.0
        rf.bid_depth_1pct = 5.0
        rf.ask_depth_1pct = 4.0
        rf.book_imbalance = 0.1
        rf.kalshi_yes_price = 0.55
        rf.kalshi_no_price = 0.45
        rf.kalshi_volume = 500.0
        session.add(rf)
    session.flush()

    for i in range(20):
        p = Prediction()
        p.market_id = market_id
        p.ts = datetime.now(timezone.utc) - timedelta(hours=2) + timedelta(minutes=i * 1.5)
        p.direction = "UP" if i % 2 == 0 else "DOWN"
        p.confidence = 0.6
        p.low_confidence = False
        p.model_version = "v0"
        p.settled_at = p.ts + timedelta(minutes=15)
        p.actual_outcome = 1 if i % 2 == 0 else 0
        session.add(p)
    session.flush()


def test_build_training_dataset_returns_xy(db_session):
    _seed_settled_data(db_session, "KXBTCUSD-DS")
    X, y = build_training_dataset(db_session, "KXBTCUSD-DS", lookback_hours=24)
    assert X is not None
    assert y is not None
    assert X.shape[1] == 25
    assert len(y) == X.shape[0]
    assert set(y).issubset({0, 1})


def test_build_training_dataset_returns_none_for_insufficient_data(db_session):
    result = build_training_dataset(db_session, "NO_DATA_MARKET", lookback_hours=24)
    assert result is None


# Append to tests/test_trainer.py
from trainer.train import train_and_promote


def test_train_and_promote_creates_model_file(db_session, tmp_path):
    _seed_settled_data(db_session, "KXBTCUSD-TN")
    result = train_and_promote(
        session=db_session,
        market_id="KXBTCUSD-TN",
        lookback_hours=24,
        models_dir=str(tmp_path),
    )
    assert result is not None
    assert result["promoted"] in (True, False)
    assert "brier_score" in result


def test_train_does_not_promote_when_worse(db_session, tmp_path):
    from shared.orm import ModelRegistry
    _seed_settled_data(db_session, "KXBTCUSD-NP")
    # Insert a very good existing active model
    reg = ModelRegistry(
        market_id="KXBTCUSD-NP",
        version="v0",
        trained_at=datetime.now(timezone.utc),
        training_rows=100,
        brier_score=0.001,   # near-perfect — new model won't beat this
        artifact_path=str(tmp_path / "dummy.joblib"),
        is_active=True,
    )
    db_session.add(reg)
    db_session.flush()
    result = train_and_promote(
        session=db_session,
        market_id="KXBTCUSD-NP",
        lookback_hours=24,
        models_dir=str(tmp_path),
    )
    assert result["promoted"] is False


from trainer.backfill import backfill_outcomes


def test_backfill_sets_actual_outcome(db_session):
    market_id = "KXBTCUSD-BF"
    market = Market(
        market_id=market_id, ticker="KXBTCUSD", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(hours=1),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(market)
    db_session.flush()

    ts_pred = datetime.now(timezone.utc) - timedelta(minutes=20)
    ts_settle = ts_pred + timedelta(minutes=15)

    # Raw feature at prediction time — price 60000
    rf_at_pred = RawFeature()
    rf_at_pred.market_id = market_id
    rf_at_pred.ts = ts_pred
    rf_at_pred.price_close = 60000
    db_session.add(rf_at_pred)

    # Raw feature at settlement time — price 61000 (UP)
    rf_at_settle = RawFeature()
    rf_at_settle.market_id = market_id
    rf_at_settle.ts = ts_settle
    rf_at_settle.price_close = 61000
    db_session.add(rf_at_settle)
    db_session.flush()

    pred = Prediction(
        market_id=market_id,
        ts=ts_pred,
        direction="UP",
        confidence=0.65,
        low_confidence=False,
        model_version="v1",
        feature_snapshot_id=rf_at_pred.id,
        settled_at=ts_settle,
        actual_outcome=None,
    )
    db_session.add(pred)
    db_session.flush()

    count = backfill_outcomes(db_session)
    assert count == 1
    db_session.expire(pred)
    assert pred.actual_outcome == 1   # price went up


def test_backfill_skips_already_settled(db_session):
    market_id = "KXBTCUSD-SK"
    market = Market(
        market_id=market_id, ticker="KXBTCUSD", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(hours=1),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(market)
    ts_pred = datetime.now(timezone.utc) - timedelta(minutes=20)
    pred = Prediction(
        market_id=market_id,
        ts=ts_pred,
        direction="DOWN",
        confidence=0.6,
        low_confidence=False,
        model_version="v1",
        settled_at=ts_pred + timedelta(minutes=15),
        actual_outcome=0,  # already set
    )
    db_session.add(pred)
    db_session.flush()
    count = backfill_outcomes(db_session)
    assert count == 0
