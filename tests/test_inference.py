# tests/test_inference.py
import pytest
import numpy as np
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from predictor.inference import run_inference
from shared.orm import Market, RawFeature, Prediction


def _make_market(session, market_id="KXBTCUSD-INF"):
    m = Market(
        market_id=market_id, ticker="KXBTCUSD",
        status="active",
        close_time=datetime.now(timezone.utc) + timedelta(minutes=7),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(m)
    session.flush()
    return m


def _make_raw_rows(session, market_id, n=40):
    rows = []
    for i in range(n):
        r = RawFeature()
        r.market_id = market_id
        r.ts = datetime.now(timezone.utc) - timedelta(minutes=(n - i) * 0.5)
        r.price_close = 60000 + i * 10
        r.price_open = 60000 + i * 10 - 5
        r.price_high = 60000 + i * 10 + 20
        r.price_low = 60000 + i * 10 - 20
        r.volume = 10.0
        r.bid_depth_1pct = 5.0
        r.ask_depth_1pct = 4.0
        r.book_imbalance = 0.1
        r.kalshi_yes_price = 0.57
        r.kalshi_no_price = 0.43
        r.kalshi_volume = 500.0
        session.add(r)
        rows.append(r)
    session.flush()
    return rows


def test_run_inference_emits_low_confidence_when_no_model(db_session):
    market = _make_market(db_session, "KXBTCUSD-NC")
    _make_raw_rows(db_session, "KXBTCUSD-NC")
    mock_loader = MagicMock()
    mock_loader.get_model.return_value = None
    pred = run_inference(db_session, market, mock_loader)
    assert pred is not None
    assert pred.low_confidence is True
    assert pred.model_version == "none"


def test_run_inference_writes_prediction_with_model(db_session):
    market = _make_market(db_session, "KXBTCUSD-WM")
    _make_raw_rows(db_session, "KXBTCUSD-WM")
    mock_model = MagicMock()
    mock_model.predict_proba.return_value = np.array([[0.3, 0.7]])
    mock_loader = MagicMock()
    mock_loader.get_model.return_value = mock_model
    mock_loader._version_cache = {"KXBTCUSD-WM": "v1"}
    pred = run_inference(db_session, market, mock_loader)
    assert pred is not None
    assert pred.direction == "UP"
    assert float(pred.confidence) == pytest.approx(0.7, abs=0.01)
    assert pred.low_confidence is False
    assert pred.market_id == "KXBTCUSD-WM"
