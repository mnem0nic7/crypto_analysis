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
    assert body["high_conf_count"] == 0
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
    assert body["high_conf_count"] == 3
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


def test_stats_training_empty_db(db_session):
    client = _make_test_app(db_session)
    resp = client.get("/stats/training")
    assert resp.status_code == 200
    body = resp.json()
    assert body["last_trained_at"] is None
    assert body["active_models"] == 0
    assert body["settled_last_24h"] == 0
    assert body["unmodeled_markets"] == 0


def test_stats_training_counts_settled_24h(db_session):
    m = Market(
        market_id="KXBTCUSD-TR1", ticker="BTC", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(minutes=7),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(m)
    db_session.flush()
    # Both predictions were MADE within 24h, but only p_recent was SETTLED within 24h
    p_recent = Prediction(
        market_id="KXBTCUSD-TR1",
        ts=datetime.now(timezone.utc) - timedelta(hours=2),
        direction="UP", confidence=0.7, low_confidence=False,
        model_version="v1",
        settled_at=datetime.now(timezone.utc) - timedelta(hours=2),
        actual_outcome=1,
    )
    p_old = Prediction(
        market_id="KXBTCUSD-TR1",
        ts=datetime.now(timezone.utc) - timedelta(hours=2),
        direction="DOWN", confidence=0.6, low_confidence=False,
        model_version="v1",
        settled_at=datetime.now(timezone.utc) - timedelta(hours=25),
        actual_outcome=0,
    )
    db_session.add(p_recent)
    db_session.add(p_old)
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/stats/training")
    assert resp.status_code == 200
    assert resp.json()["settled_last_24h"] == 1


def test_stats_training_counts_active_models(db_session):
    from shared.orm import ModelRegistry
    m = Market(
        market_id="KXBTCUSD-TR2", ticker="BTC", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(minutes=7),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(m)
    db_session.flush()
    active_reg = ModelRegistry(
        market_id="KXBTCUSD-TR2", version="v1",
        trained_at=datetime.now(timezone.utc) - timedelta(hours=1),
        training_rows=200, brier_score=0.22,
        artifact_path="/app/models/KXBTCUSD-TR2_v1.joblib",
        is_active=True,
    )
    inactive_reg = ModelRegistry(
        market_id="KXBTCUSD-TR2", version="v0",
        trained_at=datetime.now(timezone.utc) - timedelta(hours=2),
        training_rows=100, brier_score=0.28,
        artifact_path="/app/models/KXBTCUSD-TR2_v0.joblib",
        is_active=False,
    )
    db_session.add(active_reg)
    db_session.add(inactive_reg)
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/stats/training")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_models"] == 1
    assert body["last_trained_at"] is not None


def test_stats_summary_groups_by_series_not_contract(db_session):
    """Two contracts under the same series ticker must collapse to one markets row."""
    now = datetime.now(timezone.utc)
    for cid in ("KXBTCUSD-C1", "KXBTCUSD-C2"):
        db_session.add(Market(
            market_id=cid, ticker="KXBTCUSD", status="active",
            close_time=now + timedelta(minutes=7),
            discovered_at=now, updated_at=now,
        ))
    db_session.flush()
    for cid, direction, actual in [
        ("KXBTCUSD-C1", "UP", 1), ("KXBTCUSD-C1", "UP", 1),
        ("KXBTCUSD-C2", "DOWN", 1), ("KXBTCUSD-C2", "DOWN", 1),
    ]:
        db_session.add(Prediction(
            market_id=cid, ts=now, direction=direction, confidence=0.70,
            low_confidence=False, model_version="v1",
            settled_at=now - timedelta(minutes=1), actual_outcome=actual,
        ))
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/stats/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_settled"] == 4
    assert len(body["markets"]) == 1, "must collapse to one row per series"
    assert body["markets"][0]["ticker"] == "KXBTCUSD"
    assert body["markets"][0]["settled_count"] == 4
    assert abs(body["markets"][0]["accuracy"] - 0.5) < 0.01


def test_stats_summary_includes_direction_breakdown(db_session):
    now = datetime.now(timezone.utc)
    db_session.add(Market(
        market_id="KXBTCUSD-D1", ticker="KXBTCUSD", status="active",
        close_time=now + timedelta(minutes=7), discovered_at=now, updated_at=now,
    ))
    db_session.flush()
    for direction, actual in [
        ("UP", 1), ("UP", 1), ("UP", 1), ("UP", 0),
        ("DOWN", 0), ("DOWN", 0),
    ]:
        db_session.add(Prediction(
            market_id="KXBTCUSD-D1", ts=now, direction=direction, confidence=0.70,
            low_confidence=False, model_version="v1",
            settled_at=now - timedelta(minutes=1), actual_outcome=actual,
        ))
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/stats/summary")
    assert resp.status_code == 200
    m_row = resp.json()["markets"][0]
    assert m_row["up_count"] == 4
    assert abs(m_row["up_accuracy"] - 0.75) < 0.01
    assert m_row["down_count"] == 2
    assert abs(m_row["down_accuracy"] - 1.0) < 0.01


def test_stats_summary_includes_brier_score(db_session):
    from shared.orm import ModelRegistry
    now = datetime.now(timezone.utc)
    db_session.add(Market(
        market_id="KXBTCUSD-B1", ticker="KXBTCUSD", status="active",
        close_time=now + timedelta(minutes=7), discovered_at=now, updated_at=now,
    ))
    db_session.flush()
    db_session.add(Prediction(
        market_id="KXBTCUSD-B1", ts=now, direction="UP", confidence=0.70,
        low_confidence=False, model_version="v1",
        settled_at=now - timedelta(minutes=1), actual_outcome=1,
    ))
    # Sentinel market row required by ModelRegistry FK
    db_session.add(Market(
        market_id="KXBTCUSD", ticker="KXBTCUSD", status="series",
        discovered_at=now, updated_at=now,
    ))
    db_session.flush()
    db_session.add(ModelRegistry(
        market_id="KXBTCUSD", version="v3",
        trained_at=now, training_rows=500, brier_score=0.182,
        artifact_path="/app/models/KXBTCUSD_v3.joblib", is_active=True,
    ))
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/stats/summary")
    assert resp.status_code == 200
    m_row = resp.json()["markets"][0]
    assert m_row["brier_score"] is not None
    assert abs(m_row["brier_score"] - 0.182) < 0.001


def test_series_history_returns_across_contracts(db_session):
    now = datetime.now(timezone.utc)
    for cid in ("KXBTCUSD-SH1", "KXBTCUSD-SH2"):
        db_session.add(Market(
            market_id=cid, ticker="KXBTCUSD", status="active",
            close_time=now + timedelta(minutes=7), discovered_at=now, updated_at=now,
        ))
    db_session.flush()
    for cid, direction, actual in [
        ("KXBTCUSD-SH1", "UP", 1),
        ("KXBTCUSD-SH1", "DOWN", 0),
        ("KXBTCUSD-SH2", "UP", 1),
    ]:
        db_session.add(Prediction(
            market_id=cid, ts=now, direction=direction, confidence=0.70,
            low_confidence=False, model_version="v1",
            settled_at=now - timedelta(minutes=1), actual_outcome=actual,
        ))
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/history/series/KXBTCUSD")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    assert all("ts" in row and "direction" in row and "correct" in row for row in data)


def test_series_history_respects_limit(db_session):
    now = datetime.now(timezone.utc)
    db_session.add(Market(
        market_id="KXBTCUSD-LIM", ticker="KXBTCUSD", status="active",
        close_time=now + timedelta(minutes=7), discovered_at=now, updated_at=now,
    ))
    db_session.flush()
    for i in range(10):
        db_session.add(Prediction(
            market_id="KXBTCUSD-LIM",
            ts=now - timedelta(minutes=i),
            direction="UP", confidence=0.70, low_confidence=False, model_version="v1",
            settled_at=now - timedelta(minutes=i + 1), actual_outcome=1,
        ))
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/history/series/KXBTCUSD?limit=4")
    assert resp.status_code == 200
    assert len(resp.json()) == 4


def test_stats_training_detects_unmodeled_markets(db_session):
    from shared.orm import ModelRegistry
    m1 = Market(
        market_id="KXBTCUSD-TR3", ticker="BTC", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(minutes=7),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    m2 = Market(
        market_id="KXBTCUSD-TR4", ticker="ETH", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(minutes=7),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(m1)
    db_session.add(m2)
    db_session.flush()
    # Only m1 has an active model
    reg = ModelRegistry(
        market_id="KXBTCUSD-TR3", version="v1",
        trained_at=datetime.now(timezone.utc),
        training_rows=150, brier_score=0.20,
        artifact_path="/app/models/KXBTCUSD-TR3_v1.joblib",
        is_active=True,
    )
    db_session.add(reg)
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/stats/training")
    assert resp.status_code == 200
    assert resp.json()["unmodeled_markets"] == 1
