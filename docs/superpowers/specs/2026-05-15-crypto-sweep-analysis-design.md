# Crypto Parameter Sweep Analysis — Design Spec

**Date:** 2026-05-15
**Strategy:** CRYPTO_15M
**Goal:** Full grid search over the "Suggested First Sweep" knobs from `crypto-trading-analysis-knob-list.md` to find the parameter combination that maximises net P&L after fees.

---

## 1. Architecture

New `analysis/` module alongside `trainer/`, `predictor/`, `api/`. Three source files:

| File | Responsibility |
|---|---|
| `analysis/loader.py` | Query settled predictions joined with `raw_features` + `markets`; return a pandas DataFrame with all derived columns pre-computed. |
| `analysis/sweep.py` | Generate the full parameter grid via `itertools.product`; evaluate each combination with numpy boolean masking; return top-500 results by net P&L and per-knob marginals. |
| `analysis/main.py` | Entry point. Runs a single sweep and exits (invoked by cron/scheduler or the API trigger). |

New Alembic migration adds two ORM tables (see §3).

New API endpoints added to `api/main.py` (see §4).

New "Analysis" tab added to the React dashboard (see §5).

---

## 2. Sweep Engine + P&L Simulation

### 2.1 Data loading

`loader.py` issues one SQL query joining `predictions`, `raw_features` (via `feature_snapshot_id`), and `markets`. Only settled predictions (`actual_outcome IS NOT NULL`) are included.

Pre-computed derived columns:

| Column | Formula |
|---|---|
| `spread_bps` | `(1 - kalshi_yes_price - kalshi_no_price) * 10000` |
| `market_age_seconds` | `prediction.ts - market.discovered_at` (total seconds) |
| `seconds_to_close` | `market.close_time - prediction.ts` (total seconds) |
| `entry_price` | `kalshi_yes_price` if direction=UP, else `kalshi_no_price` |
| `raw_edge_bps` | `(confidence - entry_price) * 10000` |
| `fee_adj_edge_bps` | `raw_edge_bps - fee_bps` |
| `contract_price_dollars` | `entry_price * 1.0` (Kalshi $1 face value) |
| `outcome_correct` | `1` if `(direction=UP and actual_outcome=1) or (direction=DOWN and actual_outcome=0)`, else `0` |
| `pnl` | `outcome_correct * (1 - entry_price) - (1 - outcome_correct) * entry_price - fee_bps / 10000` |

The `crypto_market_price_anchor_weight` knob requires re-deriving `fee_adj_edge_bps` per anchor value:

```
anchored_conf = w * entry_price + (1 - w) * confidence
anchored_edge_bps = (anchored_conf - entry_price) * 10000 - fee_bps
```

This column is re-computed for each unique `anchor_weight` value in the grid before the main sweep loop.

### 2.2 Parameter grid

Exactly the values from the "Suggested First Sweep" table:

| Knob | Values |
|---|---|
| `min_fee_adjusted_edge_bps` | 250, 500, 750, 1000, 1500, 2000 |
| `max_spread_bps` | 100, 250, 500, 750, 1000 |
| `crypto_live_min_market_age_seconds` | 0, 60, 180, 300, 600 |
| `crypto_autonomy_min_seconds_to_close` | 0, 60, 120, 180, 300 |
| `crypto_taker_fallback_close_seconds` | 0, 30, 60, 90, 180 |
| `min_confidence` | 0.60, 0.70, 0.80, 0.90 |
| `min_contract_price_dollars` | 0.05, 0.10, 0.25, 0.50, 0.75 |
| `crypto_market_price_anchor_weight` | 0.00, 0.25, 0.50, 0.75, 1.00 |
| `crypto_late_sure_thing_min_probability` | 0.80, 0.85, 0.90, 0.95 |
| `crypto_late_sure_thing_min_market_probability` | 0.60, 0.70, 0.75, 0.80, 0.90 |

Total combinations: 6 × 5 × 5 × 5 × 5 × 4 × 5 × 5 × 4 × 5 = **7,500,000**.

### 2.3 Evaluation

Per combination, apply a numpy boolean mask to the pre-loaded arrays:

```
mask = (
    fee_adj_edge >= min_fee_adjusted_edge_bps  [uses anchored column for that weight]
    & spread_bps <= max_spread_bps
    & market_age_seconds >= crypto_live_min_market_age_seconds
    & seconds_to_close >= max(crypto_autonomy_min_seconds_to_close,
                              crypto_taker_fallback_close_seconds)
    & confidence >= min_confidence
    & contract_price_dollars >= min_contract_price_dollars
    & (seconds_to_close <= 300  [late_sure_thing_max_seconds, fixed at default]
       ? confidence >= late_sure_thing_min_prob
         & entry_price >= late_sure_thing_min_market_prob
       : True)
)
```

Note: `crypto_autonomy_min_seconds_to_close` (hard entry cutoff) and `crypto_taker_fallback_close_seconds` (taker-mode window) both act as minimum-seconds-to-close filters in simulation (no order-type data available), so the stricter of the two applies.

Metrics computed from `pnl[mask]` and `outcome_correct[mask]`:

| Metric | Formula |
|---|---|
| `n_trades` | `mask.sum()` |
| `win_rate` | `outcome_correct[mask].mean()` |
| `net_pnl_dollars` | `pnl[mask].sum()` |
| `ev_per_contract` | `pnl[mask].mean()` |
| `starvation_rate` | `1 - n_trades / len(df)` |

Combinations where `n_trades < 10` are skipped (insufficient sample, not stored).

### 2.4 Storage strategy

Storing all 7.5M rows is impractical (~1.5 GB/run). Instead:

- **Top-500** combinations by `net_pnl_dollars` are stored as `result_type = 'top_k'` rows.
- **Per-knob marginals** are stored as `result_type = 'marginal'` rows. For each (knob_name, knob_value) pair, store the mean `net_pnl_dollars` across all grid points that include that value (i.e. marginalised over all other knobs). The schema `net_pnl_dollars` column holds this mean; `ev_per_contract` holds the mean EV/contract. This gives knob importance without reading 7.5M rows.

### 2.5 Fee assumption

Fixed at **50 bps** per contract. Stored on `sweep_runs.fee_bps` so future runs can use different values.

---

## 3. DB Schema

### `sweep_runs`

| Column | Type | Notes |
|---|---|---|
| `id` | bigserial PK | |
| `run_at` | timestamptz | when the run started |
| `status` | text | `running` / `complete` / `failed` |
| `n_settled_predictions` | int | rows available at run time |
| `fee_bps` | int | fee assumption used |
| `n_combinations_evaluated` | int | combinations with n_trades ≥ 10 |
| `elapsed_seconds` | float | wall-clock time |
| `best_net_pnl_dollars` | float | top-1 net P&L |
| `best_settings_json` | jsonb | full knob values for top-1 result |

### `sweep_results`

| Column | Type | Notes |
|---|---|---|
| `id` | bigserial PK | |
| `run_id` | bigint FK → sweep_runs | |
| `result_type` | text | `top_k` or `marginal` |
| `rank` | int | 1-500 for top_k; NULL for marginal |
| `knob_name` | text | NULL for top_k; knob name for marginal |
| `knob_value` | text | NULL for top_k; value as string for marginal |
| `min_fee_adjusted_edge_bps` | int | NULL for marginal rows |
| `max_spread_bps` | int | NULL for marginal rows |
| `min_confidence` | float | NULL for marginal rows |
| `min_contract_price_dollars` | float | NULL for marginal rows |
| `crypto_live_min_market_age_seconds` | int | NULL for marginal rows |
| `crypto_autonomy_min_seconds_to_close` | int | NULL for marginal rows |
| `crypto_taker_fallback_close_seconds` | int | NULL for marginal rows |
| `crypto_market_price_anchor_weight` | float | NULL for marginal rows |
| `crypto_late_sure_thing_min_probability` | float | NULL for marginal rows |
| `crypto_late_sure_thing_min_market_probability` | float | NULL for marginal rows |
| `n_trades` | int | |
| `win_rate` | float | |
| `net_pnl_dollars` | float | |
| `ev_per_contract` | float | |
| `starvation_rate` | float | |

---

## 4. API Endpoints

All added inside the existing `create_app()` factory in `api/main.py`.

| Method | Path | Response |
|---|---|---|
| `GET` | `/analysis/runs` | List of sweep runs (id, run_at, status, n_predictions, elapsed, best P&L) |
| `GET` | `/analysis/runs/latest` | Most recent completed run (same shape) |
| `GET` | `/analysis/runs/{id}/results` | `?type=top_k` or `?type=marginal` — results rows for that run |
| `POST` | `/analysis/runs` | Trigger a new sweep run (runs synchronously; returns when complete) |

The `POST` endpoint runs the sweep in-process. If sweeps grow slow (>60s), this can be moved to a background thread with a polling pattern — but start synchronous.

---

## 5. Dashboard

New "Analysis" tab in the existing React 18 + Recharts dashboard.

### 5.1 Run history strip

Compact table at the top: columns = date, status, n_predictions, elapsed, best P&L. Clicking a row loads that run's results. A "Run sweep now" button POSTs to `/analysis/runs`.

### 5.2 Knob importance chart

Horizontal bar chart. One bar per knob. Bar length = `max_avg_pnl - min_avg_pnl` across that knob's marginal values (wider = more leverage). Data from `marginal` rows. Clicking a bar opens the per-knob detail panel.

### 5.3 Per-knob detail panel

Line chart: x = knob value, y = average net P&L at that value (marginal). Shows the response curve shape — monotone, peaked, flat — so you can see whether the optimum is an interior point or a boundary.

### 5.4 Top-500 results table

Sortable by net P&L, win rate, EV/contract, starvation rate. All 10 knob value columns + metrics. Clicking a row shows a "Best settings" card with all 10 knob values formatted for copy-paste into `.env` or settings config.

---

## 6. Out of Scope (first version)

- Multi-asset slicing (BTC, ETH, SOL, etc.) — analysis dimensions from the knob list; add after first sweep works.
- Replay promotion gates sweep — no replay data in current ORM.
- Shadow exploration and empirical bucket gate sweeps — no corresponding data.
- Risk/sizing knobs — no order-level data; requires a position simulation layer.
- Optuna / Bayesian optimisation — full grid first; switch if runtime is a problem.
