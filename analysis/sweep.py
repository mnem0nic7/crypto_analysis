# analysis/sweep.py
import heapq
import itertools
import logging
import time

import numpy as np
from sqlalchemy.orm import Session

from analysis.loader import load_settled_predictions
from shared.orm import SweepResult, SweepRun

logger = logging.getLogger(__name__)

KNOB_NAMES = [
    "min_fee_adjusted_edge_bps",
    "max_spread_bps",
    "crypto_live_min_market_age_seconds",
    "crypto_autonomy_min_seconds_to_close",
    "crypto_taker_fallback_close_seconds",
    "min_confidence",
    "min_contract_price_dollars",
    "crypto_market_price_anchor_weight",
    "crypto_late_sure_thing_min_probability",
    "crypto_late_sure_thing_min_market_probability",
]

PARAM_GRID = {
    "min_fee_adjusted_edge_bps": [250, 500, 750, 1000, 1500, 2000],
    "max_spread_bps": [100, 250, 500, 750, 1000],
    "crypto_live_min_market_age_seconds": [0, 60, 180, 300, 600],
    "crypto_autonomy_min_seconds_to_close": [0, 60, 120, 180, 300],
    "crypto_taker_fallback_close_seconds": [0, 30, 60, 90, 180],
    "min_confidence": [0.60, 0.70, 0.80, 0.90],
    "min_contract_price_dollars": [0.05, 0.10, 0.25, 0.50, 0.75],
    "crypto_market_price_anchor_weight": [0.00, 0.25, 0.50, 0.75, 1.00],
    "crypto_late_sure_thing_min_probability": [0.80, 0.85, 0.90, 0.95],
    "crypto_late_sure_thing_min_market_probability": [0.60, 0.70, 0.75, 0.80, 0.90],
}

_TOP_K = 500
_MIN_TRADES = 10
_LATE_SURE_THING_MAX_SECONDS = 300.0


def run_sweep(arrays: dict, fee_bps: int = 50) -> tuple[list[dict], list[dict]]:
    """
    Full grid sweep over PARAM_GRID. Returns (top_k_results, marginal_results).

    top_k_results: up to _TOP_K dicts, sorted by net_pnl_dollars descending.
    marginal_results: one dict per (knob_name, knob_value) with mean metrics
                      across all valid grid points that include that value.
    """
    if arrays["n"] < _MIN_TRADES:
        return [], []

    confidence = arrays["confidence"]
    entry_price = arrays["entry_price"]
    spread_bps = arrays["spread_bps"]
    raw_edge_bps = arrays["raw_edge_bps"]
    market_age_seconds = arrays["market_age_seconds"]
    seconds_to_close = arrays["seconds_to_close"]
    outcome_correct = arrays["outcome_correct"]
    raw_pnl = arrays["raw_pnl"]
    n = arrays["n"]
    fee_dollars = fee_bps / 10_000.0

    # Pre-compute anchored edge for each anchor weight.
    # anchored_edge = (1 - w) * raw_edge_bps - fee_bps
    anchored_edge: dict[float, np.ndarray] = {
        w: (1.0 - w) * raw_edge_bps - fee_bps
        for w in PARAM_GRID["crypto_market_price_anchor_weight"]
    }

    # Pre-compute all threshold masks. Each value is an N-length bool array.
    edge_masks: dict[tuple, np.ndarray] = {
        (w, t): anchored_edge[w] >= t
        for w in PARAM_GRID["crypto_market_price_anchor_weight"]
        for t in PARAM_GRID["min_fee_adjusted_edge_bps"]
    }
    spread_masks = {v: spread_bps <= v for v in PARAM_GRID["max_spread_bps"]}
    age_masks = {v: market_age_seconds >= v for v in PARAM_GRID["crypto_live_min_market_age_seconds"]}
    conf_masks = {v: confidence >= v for v in PARAM_GRID["min_confidence"]}
    price_masks = {v: entry_price >= v for v in PARAM_GRID["min_contract_price_dollars"]}

    # Late sure-thing masks: when near close, require lst thresholds.
    near_close = seconds_to_close <= _LATE_SURE_THING_MAX_SECONDS
    lst_masks: dict[tuple, np.ndarray] = {
        (lp, lm): ~near_close | ((confidence >= lp) & (entry_price >= lm))
        for lp in PARAM_GRID["crypto_late_sure_thing_min_probability"]
        for lm in PARAM_GRID["crypto_late_sure_thing_min_market_probability"]
    }

    # Unique seconds-to-close cutoffs (max of two timing knobs).
    unique_stc = {
        max(a, t)
        for a in PARAM_GRID["crypto_autonomy_min_seconds_to_close"]
        for t in PARAM_GRID["crypto_taker_fallback_close_seconds"]
    }
    stc_masks = {v: seconds_to_close >= v for v in unique_stc}

    # Marginal accumulators: sum net_pnl and ev across valid combos per (knob, value).
    marginal_pnl: dict[tuple, float] = {(k, v): 0.0 for k, vs in PARAM_GRID.items() for v in vs}
    marginal_ev: dict[tuple, float] = {(k, v): 0.0 for k, vs in PARAM_GRID.items() for v in vs}
    marginal_count: dict[tuple, int] = {(k, v): 0 for k, vs in PARAM_GRID.items() for v in vs}

    # Min-heap of (net_pnl, tie_counter, result_dict) — keeps top _TOP_K by net_pnl.
    top_heap: list = []
    _ctr = 0
    n_evaluated = 0

    for combo in itertools.product(*[PARAM_GRID[k] for k in KNOB_NAMES]):
        params = dict(zip(KNOB_NAMES, combo))
        w = params["crypto_market_price_anchor_weight"]
        edge_t = params["min_fee_adjusted_edge_bps"]
        stc_cutoff = max(
            params["crypto_autonomy_min_seconds_to_close"],
            params["crypto_taker_fallback_close_seconds"],
        )
        lp = params["crypto_late_sure_thing_min_probability"]
        lm = params["crypto_late_sure_thing_min_market_probability"]

        mask = (
            edge_masks[(w, edge_t)]
            & spread_masks[params["max_spread_bps"]]
            & age_masks[params["crypto_live_min_market_age_seconds"]]
            & stc_masks[stc_cutoff]
            & conf_masks[params["min_confidence"]]
            & price_masks[params["min_contract_price_dollars"]]
            & lst_masks[(lp, lm)]
        )

        n_trades = int(mask.sum())
        if n_trades < _MIN_TRADES:
            continue

        n_evaluated += 1
        pnl_slice = raw_pnl[mask]
        net_pnl = float(pnl_slice.sum()) - n_trades * fee_dollars
        ev = net_pnl / n_trades
        win_rate = float(outcome_correct[mask].mean())
        starvation = 1.0 - n_trades / n

        entry = (net_pnl, _ctr, {**params,
                                  "n_trades": n_trades,
                                  "win_rate": win_rate,
                                  "net_pnl_dollars": net_pnl,
                                  "ev_per_contract": ev,
                                  "starvation_rate": starvation})
        _ctr += 1
        if len(top_heap) < _TOP_K:
            heapq.heappush(top_heap, entry)
        elif net_pnl > top_heap[0][0]:
            heapq.heapreplace(top_heap, entry)

        for k, v in params.items():
            key = (k, v)
            marginal_pnl[key] += net_pnl
            marginal_ev[key] += ev
            marginal_count[key] += 1

    top_k = sorted(
        [e[2] for e in top_heap],
        key=lambda x: x["net_pnl_dollars"],
        reverse=True,
    )

    marginals = []
    for k, vs in PARAM_GRID.items():
        for v in vs:
            key = (k, v)
            cnt = marginal_count[key]
            if cnt == 0:
                continue
            marginals.append({
                "knob_name": k,
                "knob_value": str(v),
                "net_pnl_dollars": marginal_pnl[key] / cnt,
                "ev_per_contract": marginal_ev[key] / cnt,
                "n_trades": None,
                "win_rate": None,
                "starvation_rate": None,
                "_n_evaluated": n_evaluated,
            })

    return top_k, marginals


def execute_sweep(session: Session, run: SweepRun) -> None:
    """
    Orchestrates: load data → run sweep → write results → update run record.
    Mutates `run` in place. Caller is responsible for committing the session.
    """
    t0 = time.monotonic()
    arrays = load_settled_predictions(session)
    n = arrays["n"]
    run.n_settled_predictions = n

    if n < _MIN_TRADES:
        run.status = "complete"
        run.elapsed_seconds = float(time.monotonic() - t0)
        run.n_combinations_evaluated = 0
        logger.warning("Too few settled predictions (%d) to sweep", n)
        return

    logger.info("Starting sweep with %d settled predictions", n)
    top_k, marginals = run_sweep(arrays, fee_bps=run.fee_bps)
    elapsed = time.monotonic() - t0

    n_evaluated = marginals[0]["_n_evaluated"] if marginals else 0

    run.n_combinations_evaluated = n_evaluated
    run.elapsed_seconds = float(elapsed)
    run.status = "complete"

    if top_k:
        run.best_net_pnl_dollars = top_k[0]["net_pnl_dollars"]
        best = {k: top_k[0][k] for k in KNOB_NAMES}
        run.best_settings_json = best

    # Write top-k results
    for rank, r in enumerate(top_k, start=1):
        session.add(SweepResult(
            run_id=run.id,
            result_type="top_k",
            rank=rank,
            min_fee_adjusted_edge_bps=r["min_fee_adjusted_edge_bps"],
            max_spread_bps=r["max_spread_bps"],
            min_confidence=r["min_confidence"],
            min_contract_price_dollars=r["min_contract_price_dollars"],
            crypto_live_min_market_age_seconds=r["crypto_live_min_market_age_seconds"],
            crypto_autonomy_min_seconds_to_close=r["crypto_autonomy_min_seconds_to_close"],
            crypto_taker_fallback_close_seconds=r["crypto_taker_fallback_close_seconds"],
            crypto_market_price_anchor_weight=r["crypto_market_price_anchor_weight"],
            crypto_late_sure_thing_min_probability=r["crypto_late_sure_thing_min_probability"],
            crypto_late_sure_thing_min_market_probability=r["crypto_late_sure_thing_min_market_probability"],
            n_trades=r["n_trades"],
            win_rate=r["win_rate"],
            net_pnl_dollars=r["net_pnl_dollars"],
            ev_per_contract=r["ev_per_contract"],
            starvation_rate=r["starvation_rate"],
        ))

    # Write marginal results
    for m in marginals:
        session.add(SweepResult(
            run_id=run.id,
            result_type="marginal",
            knob_name=m["knob_name"],
            knob_value=m["knob_value"],
            net_pnl_dollars=m["net_pnl_dollars"],
            ev_per_contract=m["ev_per_contract"],
        ))

    session.flush()
    logger.info(
        "Sweep complete: %d combos evaluated, top P&L=%.4f, elapsed=%.1fs",
        n_evaluated, top_k[0]["net_pnl_dollars"] if top_k else 0.0, elapsed,
    )
