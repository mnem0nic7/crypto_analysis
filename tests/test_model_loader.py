# tests/test_model_loader.py
import os
import joblib
import pytest
import numpy as np
from datetime import datetime, timezone
from unittest.mock import MagicMock
from predictor.model_loader import ModelLoader
from shared.orm import Market, ModelRegistry


def _insert_market(session, market_id="KXBTCUSD-001"):
    m = Market(
        market_id=market_id, ticker="KXBTCUSD", status="active",
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(m)
    session.flush()
    return m


def test_get_model_returns_none_for_unknown_market(db_session, tmp_path):
    loader = ModelLoader(db_session, models_dir=str(tmp_path))
    assert loader.get_model("NO_SUCH_MARKET") is None


def test_get_model_loads_active_model(db_session, tmp_path):
    _insert_market(db_session, "KXBTCUSD-002")
    from sklearn.dummy import DummyClassifier
    clf = DummyClassifier()
    clf.fit([[0] * 25], [1])
    path = str(tmp_path / "KXBTCUSD-002_v1.joblib")
    joblib.dump(clf, path)
    reg = ModelRegistry(
        market_id="KXBTCUSD-002",
        version="v1",
        trained_at=datetime.now(timezone.utc),
        training_rows=100,
        brier_score=0.22,
        artifact_path=path,
        is_active=True,
    )
    db_session.add(reg)
    db_session.flush()
    loader = ModelLoader(db_session, models_dir=str(tmp_path))
    model = loader.get_model("KXBTCUSD-002")
    assert model is not None


def test_reload_picks_up_newer_model(db_session, tmp_path):
    _insert_market(db_session, "KXBTCUSD-003")
    from sklearn.dummy import DummyClassifier
    clf = DummyClassifier()
    clf.fit([[0] * 25], [1])
    path = str(tmp_path / "KXBTCUSD-003_v2.joblib")
    joblib.dump(clf, path)
    reg = ModelRegistry(
        market_id="KXBTCUSD-003",
        version="v2",
        trained_at=datetime.now(timezone.utc),
        training_rows=200,
        brier_score=0.18,
        artifact_path=path,
        is_active=True,
    )
    db_session.add(reg)
    db_session.flush()
    loader = ModelLoader(db_session, models_dir=str(tmp_path))
    loader.get_model("KXBTCUSD-003")  # initial load
    loader.reload_all()               # simulate hot-reload
    model = loader.get_model("KXBTCUSD-003")
    assert model is not None
