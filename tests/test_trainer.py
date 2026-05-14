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
