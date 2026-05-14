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


def test_stats_summary_no_data(db_session):
    client = _make_test_app(db_session)
    resp = client.get("/stats/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_settled"] == 0
    assert body["overall_accuracy"] == 0.0
    assert body["high_conf_accuracy"] == 0.0
    assert body["markets"] == []


def test_stats_summary_aggregates_correctly(db_session):
    from datetime import datetime, timezone, timedelta
    from shared.orm import Market, Prediction
    m = Market(
        market_id="KXBTCUSD-S1", ticker="BTC", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(minutes=7),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(m)
    db_session.flush()
    # 2 correct UP predictions, 1 wrong DOWN (actual_outcome=1 but direction=DOWN)
    for direction, actual, conf in [("UP", 1, 0.73), ("UP", 1, 0.70), ("DOWN", 1, 0.68)]:
        p = Prediction(
            market_id="KXBTCUSD-S1", ts=datetime.now(timezone.utc),
            direction=direction, confidence=conf,
            low_confidence=False, model_version="v1",
            settled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            actual_outcome=actual,
        )
        db_session.add(p)
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/stats/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_settled"] == 3
    assert abs(body["overall_accuracy"] - 2/3) < 0.01
    # high_conf_accuracy: all 3 have conf >= 0.65, 2 correct
    assert abs(body["high_conf_accuracy"] - 2/3) < 0.01
    assert len(body["markets"]) == 1
    assert body["markets"][0]["ticker"] == "BTC"


def test_stats_models_returns_active(db_session):
    from datetime import datetime, timezone, timedelta
    from shared.orm import Market, ModelRegistry
    m = Market(
        market_id="KXBTCUSD-MR1", ticker="BTC", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(minutes=7),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(m)
    db_session.flush()
    reg = ModelRegistry(
        market_id="KXBTCUSD-MR1", version="v4",
        trained_at=datetime.now(timezone.utc),
        training_rows=842, brier_score=0.19,
        artifact_path="/app/models/KXBTCUSD-MR1_v4.ubj",
        is_active=True,
    )
    db_session.add(reg)
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/stats/models")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["ticker"] == "BTC"
    assert data[0]["version"] == "v4"
    assert abs(data[0]["brier_score"] - 0.19) < 0.001
    assert data[0]["is_active"] is True


def test_stats_models_excludes_inactive(db_session):
    from datetime import datetime, timezone, timedelta
    from shared.orm import Market, ModelRegistry
    m = Market(
        market_id="KXBTCUSD-MR2", ticker="BTC", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(minutes=7),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(m)
    db_session.flush()
    reg = ModelRegistry(
        market_id="KXBTCUSD-MR2", version="v3",
        trained_at=datetime.now(timezone.utc),
        training_rows=500, brier_score=0.25,
        artifact_path="/app/models/KXBTCUSD-MR2_v3.ubj",
        is_active=False,
    )
    db_session.add(reg)
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/stats/models")
    assert resp.status_code == 200
    assert resp.json() == []


def test_slot_defaults_to_blue(monkeypatch):
    monkeypatch.delenv("DEPLOY_SLOT", raising=False)
    from fastapi.testclient import TestClient
    import api.main as api_module
    app = api_module.create_app(lambda: None)
    client = TestClient(app)
    resp = client.get("/slot")
    assert resp.status_code == 200
    assert resp.json()["slot"] == "blue"


def test_slot_returns_green(monkeypatch):
    monkeypatch.setenv("DEPLOY_SLOT", "green")
    from fastapi.testclient import TestClient
    import api.main as api_module
    app = api_module.create_app(lambda: None)
    client = TestClient(app)
    resp = client.get("/slot")
    assert resp.status_code == 200
    assert resp.json()["slot"] == "green"
