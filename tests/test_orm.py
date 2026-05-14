# tests/test_orm.py
from shared.orm import Market, RawFeature, Prediction, ModelRegistry


def test_table_names():
    assert Market.__tablename__ == "markets"
    assert RawFeature.__tablename__ == "raw_features"
    assert Prediction.__tablename__ == "predictions"
    assert ModelRegistry.__tablename__ == "model_registry"


def test_raw_feature_has_derived_columns():
    cols = {c.name for c in RawFeature.__table__.columns}
    assert "price_momentum_1m" in cols
    assert "volatility_5m" in cols
    assert "book_imbalance" in cols


def test_prediction_has_audit_columns():
    cols = {c.name for c in Prediction.__table__.columns}
    assert "actual_outcome" in cols
    assert "low_confidence" in cols
    assert "feature_snapshot_id" in cols


def test_model_registry_has_promotion_columns():
    cols = {c.name for c in ModelRegistry.__table__.columns}
    assert "is_active" in cols
    assert "brier_score" in cols


def test_db_session_fixture_works(db_session):
    from shared.orm import Market
    from datetime import datetime, timezone
    m = Market(
        market_id="TEST-001",
        ticker="KXBTCUSD",
        status="active",
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(m)
    db_session.flush()
    result = db_session.get(Market, "TEST-001")
    assert result is not None
    assert result.ticker == "KXBTCUSD"
