# Series-Level Training + Historical Backfill Design

**Date:** 2026-05-14

## Problem

The ML pipeline trains and looks up models keyed to specific 15-minute contract tickers
(e.g., `KXBTC15M-26MAY141715-15`). Each contract is unique and closes in 15 minutes, so:

1. The trainer runs on at most ~15 rows of data per contract — far too few for XGBoost.
2. The model artifact stored for a contract is never reused (the contract never opens again).
3. The predictor calls `get_model(market.market_id)` — always returns `None` for any new contract — so every prediction is `low_confidence=True` with `confidence=0.5` forever.

A secondary bug: `feature_builder.py` computes rolling windows using `datetime.now(timezone.utc)` instead of the prediction timestamp `ts`. This makes all windowed features (momentum, volatility, VWAP) produce zero or garbage values when called on historical data during training.

## Goal

1. **Fix series-level training**: one model per series ticker (`KXBTC15M`), trained on all contracts of that series. The predictor reuses this model for every new contract.
2. **Historical backfill**: seed the DB with ~6,300 settled contracts per series (March–May 2026) using Coinbase historical candles and Kalshi settlement results as labels.

## Architecture

### Series-Level Training

The `market_id` key is replaced with `series_ticker` everywhere it crosses the model boundary. Raw data (raw_features, predictions) stays per-contract; models are shared across contracts of the same series.

```
markets (per-contract rows, ticker = series)
    ↓  JOIN on markets.ticker
Prediction (per-contract, actual_outcome set after settlement)
    ↓  build_training_dataset(series_ticker)  aggregates across all contracts
XGBoost model stored in ModelRegistry(market_id = series_ticker)
    ↑  get_model(market.ticker) in predictor
```

**Sentinel Market rows**: The `model_registry.market_id` FK points to `markets.market_id`. To store a model under `KXBTC15M`, a Market row with `market_id = 'KXBTC15M'` must exist. The trainer creates one on first train (`status = 'series'`) if absent. These rows are never written to by the ingestor.

### Historical Backfill

A one-shot script (`python -m ingestor.historical_backfill`) that:
1. Pages through all settled Kalshi contracts for each of the 7 series tickers.
2. Fetches Coinbase 1-min candles for the full date range in 300-candle time-range chunks, caches in-process as `dict[unix_timestamp → candle]`.
3. For each settled contract (oldest-first):
   - Skips if `raw_features` rows already exist for that `market_id` (idempotent).
   - Upserts a `Market` row (`status='settled'`).
   - Writes up to 40 `RawFeature` rows (OHLCV from candle cache; Kalshi/order-book columns = NULL).
   - Writes 1 `Prediction` row at `open_time + 7.5 min` with `actual_outcome = 1 if result == 'yes' else 0`, `model_version = 'backfill'`.

**What can be reconstructed historically:**
- Coinbase OHLCV, volume — ✅ full history available
- Price momentum, volatility, VWAP — ✅ recomputed by feature_builder from price_close
- Kalshi contract mid-window prices — ❌ no history API; set to NULL (feature_builder defaults to 0.5)
- Order book depth — ❌ ephemeral; set to NULL (feature_builder defaults to 0.0)

The 17 price/volume/time features are fully accurate. The 8 Kalshi/order-book features default to neutral (0.5 / 0.0). The model will learn to weight price features heavily since Kalshi features are uniform across all historical rows.

### Feature Builder Bug Fix (prerequisite)

`predictor/feature_builder.py` line 38:
```python
now = datetime.now(timezone.utc)  # BUG: uses wall clock, not prediction time
```
Fix:
```python
now = ts  # ts is already the prediction timestamp passed by the caller
```
Without this fix, `_within(minutes)` returns empty windows for historical data because the actual candle timestamps (March–May 2026) are far in the past relative to wall clock time, so `_within(1)` = cutoff of one minute ago = no rows match.

## Data Volume

Per series:
- ~6,300 settled contracts × 40 raw_features rows = ~252,000 raw_feature rows
- ~6,300 Prediction rows

Across 7 series:
- ~1.76M raw_features rows (~530 MB in Postgres)
- ~44,100 Prediction rows

Coinbase API calls: ~317 requests/series × 7 = ~2,219 total (300 candles = 5 hours each).
Kalshi API calls: ~32 pages/series × 7 = ~224 total (200 markets per page).

## File Map

| File | Change |
|------|--------|
| `predictor/feature_builder.py` | Fix `now = ts` (line 38) |
| `trainer/dataset.py` | `build_training_dataset(series_ticker)` — JOIN through `markets.ticker` |
| `trainer/train.py` | `train_and_promote(series_ticker)` — create sentinel Market row, store model under series ticker |
| `trainer/main.py` | Iterate distinct `Market.ticker` values instead of active `market_id`s |
| `predictor/inference.py` | `get_model(market.ticker)` instead of `get_model(market.market_id)` |
| `ingestor/coinbase_client.py` | Add `start: int | None, end: int | None` params to `get_candles()` |
| `ingestor/historical_backfill.py` | New: backfill script |
| `tests/test_trainer.py` | Update to series_ticker API |
| `tests/test_inference.py` | Update mock to call `get_model` with `market.ticker` |
| `tests/test_historical_backfill.py` | New: tests for backfill logic |

No Alembic migration required. No schema changes.

## Key Invariants

- `raw_features.market_id` always points to a real `markets.market_id` (FK enforced).
- `predictions.market_id` always points to a real `markets.market_id` (FK enforced).
- `model_registry.market_id` is a series ticker (`KXBTC15M`), backed by a sentinel Market row.
- Backfilled predictions have `low_confidence = False` (they have known outcomes, not model uncertainty).
- The backfill is idempotent: re-running it on a partially-filled DB skips completed markets.
- `feature_builder.build_feature_vector` is a pure function of its inputs (no I/O, no `datetime.now()`).

## Error Handling

- Coinbase candle fetch failure for a specific window: log warning, skip that contract (don't fail the whole run).
- Kalshi pagination failure: retry with exponential backoff (3 attempts), then abort with clear error.
- Missing candles in cache for a contract window: write as many rows as available; if fewer than 10, skip the prediction row (feature builder needs `_MIN_ROWS = 10`).

## Testing Strategy

- `test_feature_builder.py`: add a test that calls `build_feature_vector` with rows from the past (e.g., 2 hours ago) and asserts non-zero momentum (proves `now = ts` fix works).
- `test_trainer.py`: update `_seed_settled_data` to create data for two different market_ids with the same `ticker`; assert that `build_training_dataset(ticker)` returns rows from both contracts.
- `test_inference.py`: assert `mock_loader.get_model` is called with `market.ticker`, not `market.market_id`.
- `test_historical_backfill.py`: mock Kalshi client + Coinbase client; assert correct Market/RawFeature/Prediction rows are created; run twice to verify idempotency.
