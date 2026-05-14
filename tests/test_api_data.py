# tests/test_api_data.py
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from shared.orm import Market, RawFeature, Prediction, ModelRegistry


def _make_app(db_session):
    import api.main as m
    return TestClient(m.create_app(lambda: db_session))


def _seed_market(session, market_id="KXBTCUSD-D1", ticker="KXBTCUSD"):
    now = datetime.now(timezone.utc)
    m = Market(
        market_id=market_id, ticker=ticker, status="active",
        close_time=now + timedelta(minutes=10),
        discovered_at=now, updated_at=now,
    )
    session.add(m)
    session.flush()
    return m


def _seed_raw_features(session, market_id, n=15, base_price=100_000.0):
    """Seed n RawFeature rows 30 seconds apart ending at now."""
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(n):
        ts = now - timedelta(seconds=(n - i) * 30)
        r = RawFeature(
            market_id=market_id, ts=ts,
            price_open=base_price + i, price_high=base_price + i + 10,
            price_low=base_price + i - 10, price_close=base_price + i + 5,
            volume=1.0 + i * 0.1,
            bid_depth_1pct=500.0, ask_depth_1pct=480.0,
            book_imbalance=0.05 * (1 if i % 2 == 0 else -1),
            kalshi_yes_price=0.55, kalshi_no_price=0.45, kalshi_volume=1000.0,
            price_momentum_1m=0.001, price_momentum_5m=0.003,
            price_momentum_15m=0.005, volatility_5m=0.002,
        )
        session.add(r)
        rows.append(r)
    session.flush()
    return rows


def test_raw_features_returns_rows(db_session):
    _seed_market(db_session, "KXBTCUSD-RF1", "KXBTCUSD")
    _seed_raw_features(db_session, "KXBTCUSD-RF1", n=5)
    client = _make_app(db_session)
    resp = client.get("/data/raw-features?series_ticker=KXBTCUSD")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert len(body["rows"]) == 5
    assert body["page"] == 1
    assert "ts" in body["rows"][0]
    assert "price_close" in body["rows"][0]


def test_raw_features_pagination(db_session):
    _seed_market(db_session, "KXBTCUSD-RF2", "KXBTCUSD")
    _seed_raw_features(db_session, "KXBTCUSD-RF2", n=15)
    client = _make_app(db_session)
    resp = client.get("/data/raw-features?series_ticker=KXBTCUSD&page=1&page_size=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 15
    assert len(body["rows"]) == 10
    resp2 = client.get("/data/raw-features?series_ticker=KXBTCUSD&page=2&page_size=10")
    assert resp2.status_code == 200
    assert len(resp2.json()["rows"]) == 5


def test_raw_features_filters_by_ticker(db_session):
    _seed_market(db_session, "KXBTCUSD-RF3", "KXBTCUSD")
    _seed_market(db_session, "KXETHUSD-RF3", "KXETHUSD")
    _seed_raw_features(db_session, "KXBTCUSD-RF3", n=3)
    _seed_raw_features(db_session, "KXETHUSD-RF3", n=7)
    client = _make_app(db_session)
    resp = client.get("/data/raw-features?series_ticker=KXETHUSD")
    assert resp.status_code == 200
    assert resp.json()["total"] == 7


def test_raw_features_rejects_invalid_sort_by(db_session):
    client = _make_app(db_session)
    resp = client.get("/data/raw-features?series_ticker=KXBTCUSD&sort_by=DROP+TABLE")
    assert resp.status_code == 400


def _seed_settled_prediction(session, market_id, ts, outcome=1):
    p = Prediction(
        market_id=market_id, ts=ts,
        direction="UP" if outcome == 1 else "DOWN",
        confidence=0.72, low_confidence=False, model_version="v1",
        settled_at=ts + timedelta(minutes=10),
        actual_outcome=outcome,
    )
    session.add(p)
    session.flush()
    return p


def test_feature_vectors_returns_rows(db_session):
    now = datetime.now(timezone.utc)
    _seed_market(db_session, "KXBTCUSD-FV1", "KXBTCUSD")
    _seed_raw_features(db_session, "KXBTCUSD-FV1", n=15)
    pred_ts = now - timedelta(seconds=30)
    _seed_settled_prediction(db_session, "KXBTCUSD-FV1", pred_ts)
    client = _make_app(db_session)
    resp = client.get("/data/feature-vectors?series_ticker=KXBTCUSD")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["actual_outcome"] == 1
    assert "features" in row
    assert "price_momentum_1m" in row["features"]
    assert len(row["features"]) == 25


def test_feature_vectors_skips_rows_with_insufficient_context(db_session):
    now = datetime.now(timezone.utc)
    _seed_market(db_session, "KXBTCUSD-FV2", "KXBTCUSD")
    # Only 3 raw rows — below _MIN_ROWS=10, so feature vector can't be built
    _seed_raw_features(db_session, "KXBTCUSD-FV2", n=3)
    pred_ts = now - timedelta(seconds=30)
    _seed_settled_prediction(db_session, "KXBTCUSD-FV2", pred_ts)
    client = _make_app(db_session)
    resp = client.get("/data/feature-vectors?series_ticker=KXBTCUSD")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["rows"]) == 0
    assert body["skipped"] == 1


def test_feature_vectors_page_size_capped_at_50(db_session):
    client = _make_app(db_session)
    resp = client.get("/data/feature-vectors?series_ticker=KXBTCUSD&page_size=200")
    assert resp.status_code == 200
    assert resp.json()["page_size"] == 50
