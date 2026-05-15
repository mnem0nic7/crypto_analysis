import pytest
from datetime import datetime, timezone, timedelta
import numpy as np
from shared.orm import Market, Prediction, RawFeature
from analysis.loader import load_settled_predictions


def _seed(session):
    now = datetime.now(timezone.utc)
    market = Market(
        market_id="TEST-001",
        ticker="KXBTC15M",
        status="active",
        discovered_at=now - timedelta(minutes=10),
        close_time=now + timedelta(minutes=5),
        updated_at=now,
    )
    session.add(market)
    session.flush()

    rf = RawFeature(
        market_id="TEST-001",
        ts=now - timedelta(minutes=2),
        kalshi_yes_price=0.6,
        kalshi_no_price=0.35,
    )
    session.add(rf)
    session.flush()

    pred = Prediction(
        market_id="TEST-001",
        ts=now - timedelta(minutes=2),
        direction="UP",
        confidence=0.85,
        low_confidence=False,
        model_version="v1",
        feature_snapshot_id=rf.id,
        settled_at=now,
        actual_outcome=1,
    )
    session.add(pred)
    session.flush()
    return market, rf, pred


def test_load_returns_arrays_for_settled_predictions(db_session):
    _seed(db_session)
    arrays = load_settled_predictions(db_session)

    assert arrays["n"] == 1
    assert arrays["confidence"][0] == pytest.approx(0.85)
    assert arrays["entry_price"][0] == pytest.approx(0.6)   # UP → yes_price
    assert arrays["outcome_correct"][0] == 1


def test_load_computes_spread_bps(db_session):
    _seed(db_session)
    arrays = load_settled_predictions(db_session)
    # spread = (1 - 0.6 - 0.35) * 10000 = 500
    assert arrays["spread_bps"][0] == pytest.approx(500.0)


def test_load_computes_timing(db_session):
    _seed(db_session)
    arrays = load_settled_predictions(db_session)
    assert arrays["market_age_seconds"][0] == pytest.approx(8 * 60, abs=5)
    assert arrays["seconds_to_close"][0] == pytest.approx(7 * 60, abs=5)


def test_load_excludes_unsettled(db_session):
    now = datetime.now(timezone.utc)
    market = Market(
        market_id="TEST-002", ticker="KXBTC15M", status="active",
        discovered_at=now - timedelta(minutes=5),
        close_time=now + timedelta(minutes=5),
        updated_at=now,
    )
    session = db_session
    session.add(market)
    session.flush()
    rf = RawFeature(
        market_id="TEST-002", ts=now,
        kalshi_yes_price=0.5, kalshi_no_price=0.45,
    )
    session.add(rf)
    session.flush()
    pred = Prediction(
        market_id="TEST-002", ts=now,
        direction="UP", confidence=0.7, low_confidence=False,
        model_version="v1", feature_snapshot_id=rf.id,
        actual_outcome=None,  # unsettled
    )
    session.add(pred)
    session.flush()
    arrays = load_settled_predictions(session)
    assert arrays["n"] == 0


def test_load_returns_empty_when_no_data(db_session):
    arrays = load_settled_predictions(db_session)
    assert arrays["n"] == 0
