import pytest
import numpy as np
from analysis.sweep import PARAM_GRID, KNOB_NAMES, run_sweep


def _arrays(n=30, confidence=None, entry_price=None, outcome_correct=None,
            spread_bps=None, market_age_seconds=None, seconds_to_close=None):
    """Build minimal arrays for sweep tests."""
    confidence = confidence if confidence is not None else np.full(n, 0.85)
    entry_price = entry_price if entry_price is not None else np.full(n, 0.60)
    outcome_correct = outcome_correct if outcome_correct is not None else np.ones(n, dtype=np.int8)
    spread_bps = spread_bps if spread_bps is not None else np.full(n, 100.0)
    market_age_seconds = market_age_seconds if market_age_seconds is not None else np.full(n, 600.0)
    seconds_to_close = seconds_to_close if seconds_to_close is not None else np.full(n, 400.0)
    raw_edge_bps = (confidence - entry_price) * 10000.0
    raw_pnl = (
        outcome_correct * (1.0 - entry_price)
        - (1 - outcome_correct) * entry_price
    )
    return {
        "n": n,
        "confidence": confidence,
        "entry_price": entry_price,
        "spread_bps": spread_bps,
        "raw_edge_bps": raw_edge_bps,
        "market_age_seconds": market_age_seconds,
        "seconds_to_close": seconds_to_close,
        "outcome_correct": outcome_correct,
        "raw_pnl": raw_pnl,
    }


def test_run_sweep_returns_top_k_and_marginals():
    arrays = _arrays(n=30)
    top_k, marginals = run_sweep(arrays, fee_bps=50)
    assert len(top_k) > 0
    assert len(marginals) > 0


def test_run_sweep_top_k_sorted_by_net_pnl():
    arrays = _arrays(n=30)
    top_k, _ = run_sweep(arrays, fee_bps=50)
    pnls = [r["net_pnl_dollars"] for r in top_k]
    assert pnls == sorted(pnls, reverse=True)


def test_run_sweep_high_edge_filter_selects_correct_trades():
    """High-edge trades (win) should dominate when min_fee_adjusted_edge_bps is high."""
    n = 40
    conf = np.array([0.90] * 20 + [0.51] * 20)
    ep = np.array([0.60] * 20 + [0.50] * 20)
    oc = np.array([1] * 20 + [0] * 20, dtype=np.int8)
    arrays = _arrays(n=n, confidence=conf, entry_price=ep, outcome_correct=oc)
    top_k, _ = run_sweep(arrays, fee_bps=50)
    best = top_k[0]
    # Best combo must have positive P&L (selects the winning high-edge group)
    assert best["net_pnl_dollars"] > 0


def test_run_sweep_min_trades_threshold_skips_sparse_combos():
    """Combos with fewer than 10 trades should not appear in top_k."""
    # Very aggressive filters → most combos will have 0 trades
    arrays = _arrays(n=5)  # Only 5 predictions → most combos filtered to < 10
    top_k, _ = run_sweep(arrays, fee_bps=50)
    for r in top_k:
        assert r["n_trades"] >= 10


def test_run_sweep_marginals_cover_all_knobs():
    arrays = _arrays(n=30)
    _, marginals = run_sweep(arrays, fee_bps=50)
    knobs_in_marginals = {m["knob_name"] for m in marginals}
    assert knobs_in_marginals == set(KNOB_NAMES)


def test_run_sweep_top_k_has_all_required_fields():
    arrays = _arrays(n=30)
    top_k, _ = run_sweep(arrays, fee_bps=50)
    if not top_k:
        pytest.skip("No valid combos with this data size")
    row = top_k[0]
    for field in ["net_pnl_dollars", "win_rate", "n_trades", "ev_per_contract", "starvation_rate"]:
        assert field in row
    for knob in KNOB_NAMES:
        assert knob in row


def test_param_grid_total_combinations():
    from itertools import product
    total = sum(1 for _ in product(*[PARAM_GRID[k] for k in KNOB_NAMES]))
    assert total == 7_500_000
