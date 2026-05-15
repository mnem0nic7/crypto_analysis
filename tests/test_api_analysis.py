# tests/test_api_analysis.py
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from shared.orm import SweepRun, SweepResult


def _make_test_app(db_session):
    import api.main as api_module
    app = api_module.create_app(lambda: db_session)
    return TestClient(app)


def _seed_sweep_run(session, status="complete", run_at=None):
    run = SweepRun(
        run_at=run_at or datetime.now(timezone.utc),
        status=status,
        n_settled_predictions=500,
        fee_bps=50,
        n_combinations_evaluated=1000,
        elapsed_seconds=12.5,
        best_net_pnl_dollars=42.0,
        best_settings_json={},
    )
    session.add(run)
    session.flush()
    return run


def _seed_sweep_result(session, run_id, result_type="top_k", rank=1):
    result = SweepResult(
        run_id=run_id,
        result_type=result_type,
        rank=rank,
        knob_name="min_confidence" if result_type == "marginal" else None,
        knob_value="0.65" if result_type == "marginal" else None,
        min_fee_adjusted_edge_bps=10,
        max_spread_bps=20,
        min_confidence=0.65,
        min_contract_price_dollars=0.10,
        crypto_live_min_market_age_seconds=300,
        crypto_autonomy_min_seconds_to_close=60,
        crypto_taker_fallback_close_seconds=30,
        crypto_market_price_anchor_weight=0.5,
        crypto_late_sure_thing_min_probability=0.9,
        crypto_late_sure_thing_min_market_probability=0.85,
        n_trades=100,
        win_rate=0.55,
        net_pnl_dollars=15.0,
        ev_per_contract=0.12,
        starvation_rate=0.02,
    )
    session.add(result)
    session.flush()
    return result


def test_get_analysis_runs_empty(db_session):
    client = _make_test_app(db_session)
    resp = client.get("/analysis/runs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_analysis_runs_list(db_session):
    _seed_sweep_run(db_session)
    client = _make_test_app(db_session)
    resp = client.get("/analysis/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["status"] == "complete"
    assert data[0]["n_predictions"] == 500
    assert data[0]["elapsed_seconds"] == pytest.approx(12.5)
    assert data[0]["best_net_pnl_dollars"] == pytest.approx(42.0)


def test_get_analysis_runs_latest(db_session):
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    complete_run = _seed_sweep_run(db_session, status="complete", run_at=now - timedelta(hours=1))
    _seed_sweep_run(db_session, status="failed", run_at=now)
    client = _make_test_app(db_session)
    resp = client.get("/analysis/runs/latest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "complete"
    assert data["id"] == complete_run.id


def test_get_analysis_runs_latest_no_complete_run(db_session):
    _seed_sweep_run(db_session, status="failed")
    client = _make_test_app(db_session)
    resp = client.get("/analysis/runs/latest")
    assert resp.status_code == 404


def test_get_analysis_run_results_top_k(db_session):
    run = _seed_sweep_run(db_session)
    _seed_sweep_result(db_session, run_id=run.id, result_type="top_k", rank=1)
    _seed_sweep_result(db_session, run_id=run.id, result_type="top_k", rank=2)
    client = _make_test_app(db_session)
    resp = client.get(f"/analysis/runs/{run.id}/results?type=top_k")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    ranks = {r["rank"] for r in data}
    assert ranks == {1, 2}
    # Check knob columns and metrics are present
    first = data[0]
    assert "min_fee_adjusted_edge_bps" in first
    assert "win_rate" in first
    assert "net_pnl_dollars" in first
    assert "ev_per_contract" in first
    assert "starvation_rate" in first


def test_get_analysis_run_results_marginal(db_session):
    run = _seed_sweep_run(db_session)
    _seed_sweep_result(db_session, run_id=run.id, result_type="marginal", rank=1)
    client = _make_test_app(db_session)
    resp = client.get(f"/analysis/runs/{run.id}/results?type=marginal")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert "knob_name" in data[0]
    assert "knob_value" in data[0]
    assert "net_pnl_dollars" in data[0]
    assert "ev_per_contract" in data[0]
    # marginal results should NOT expose all knob columns
    assert "min_fee_adjusted_edge_bps" not in data[0]


def test_get_analysis_run_results_not_found(db_session):
    client = _make_test_app(db_session)
    resp = client.get("/analysis/runs/999/results?type=top_k")
    assert resp.status_code == 404


def test_post_analysis_runs(db_session):
    client = _make_test_app(db_session)
    mock_run = MagicMock()
    mock_run.id = 42
    mock_run.status = "complete"
    mock_run.n_settled_predictions = 100
    mock_run.elapsed_seconds = 1.5
    mock_run.best_net_pnl_dollars = 5.0
    mock_run.run_at = datetime.now(timezone.utc)
    with patch("analysis.main.run_once", return_value=mock_run):
        resp = client.post("/analysis/runs")
    assert resp.status_code == 200
    assert resp.json()["status"] == "complete"
    assert resp.json()["id"] == 42
