# tests/test_api.py
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from shared.orm import Market, Prediction


def _make_test_app(db_session):
    import api.main as api_module
    app = api_module.create_app(lambda: db_session)
    return TestClient(app)


def _seed_market(session, market_id="KXBTCUSD-001"):
    m = Market(
        market_id=market_id, ticker="KXBTCUSD", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(minutes=7),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(m)
    session.flush()
    return m


def _seed_prediction(session, market_id="KXBTCUSD-001"):
    p = Prediction(
        market_id=market_id,
        ts=datetime.now(timezone.utc),
        direction="UP",
        confidence=0.73,
        low_confidence=False,
        model_version="v2",
        settled_at=datetime.now(timezone.utc) + timedelta(minutes=7),
    )
    session.add(p)
    session.flush()
    return p


def test_get_markets_returns_active(db_session):
    _seed_market(db_session, "KXBTCUSD-M1")
    client = _make_test_app(db_session)
    resp = client.get("/markets")
    assert resp.status_code == 200
    data = resp.json()
    ids = [m["market_id"] for m in data]
    assert "KXBTCUSD-M1" in ids


def test_get_predict_returns_latest(db_session):
    _seed_market(db_session, "KXBTCUSD-P1")
    _seed_prediction(db_session, "KXBTCUSD-P1")
    client = _make_test_app(db_session)
    resp = client.get("/predict/KXBTCUSD-P1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["direction"] == "UP"
    assert body["confidence"] == pytest.approx(0.73, abs=0.01)
    assert "feature_age_seconds" in body


def test_get_predict_404_for_unknown_market(db_session):
    client = _make_test_app(db_session)
    resp = client.get("/predict/DOES-NOT-EXIST")
    assert resp.status_code == 404


def test_get_history_returns_settled(db_session):
    _seed_market(db_session, "KXBTCUSD-H1")
    p = _seed_prediction(db_session, "KXBTCUSD-H1")
    p.actual_outcome = 1
    p.settled_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get(f"/history/KXBTCUSD-H1")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["actual_outcome"] == 1


def test_health_endpoint(db_session):
    client = _make_test_app(db_session)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
