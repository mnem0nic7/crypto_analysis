# Feature Warm-Up on Cold Start — Design Spec

**Date:** 2026-05-14
**Status:** Approved

---

## Problem

The ingestor writes one `RawFeature` row every 30 seconds. `feature_builder.py` requires `_MIN_ROWS = 10` rows before it can produce a feature vector, so new markets (or a fresh container restart) wait ~5 minutes before the predictor can emit a real signal.

Coinbase's `/candles` endpoint already supports fetching historical 1-minute OHLCV data. We have this data available and should use it to pre-populate rows on startup instead of waiting.

---

## Solution

Add `warm_up_if_needed(session, market_id, product_id, coinbase_client)` to `ingestor/feature_writer.py`. Call it at the top of `fetch_and_write()`. It:

1. Counts existing `RawFeature` rows for the market
2. Returns immediately if `count >= _WARMUP_CANDLES` (already warm)
3. Fetches the last `_WARMUP_CANDLES = 20` one-minute candles from Coinbase
4. Deduplicates against existing rows by `ts` (safe on container restart)
5. Writes one `RawFeature` row per candle, with OHLCV populated and live-only fields (`bid_depth_1pct`, `ask_depth_1pct`, `book_imbalance`, `kalshi_*`, `price_momentum_*`, `volatility_5m`) as `None`
6. Flushes to DB

---

## Why `None` for live-only fields is safe

- `RawFeature` columns for order book and Kalshi data are all `nullable=True` (no column constraints)
- `feature_builder.py` uses `_f(val, default=0.0)` for every field — `None` → `0.0` gracefully
- `feature_builder.py` recomputes momentum/volatility directly from `price_close` values in the row list — it does NOT read the pre-computed `price_momentum_*` / `volatility_5m` columns from `RawFeature`
- Zero order-book and Kalshi features degrade prediction quality slightly but are far better than no prediction for 5 minutes

---

## Why warm-up rows work with `feature_builder`

`feature_builder.py` sets `now = ts` (the inference timestamp, ~current time) and `_within(minutes)` returns rows where `r.ts >= now - timedelta(minutes=minutes)`. Warm-up rows have historical candle timestamps (e.g. 19 min ago through 1 min ago), so they fall correctly within the 1m/5m/15m windows at inference time.

---

## Architecture

### Data flow (after change)

```
fetch_and_write() called
  └─ warm_up_if_needed()
       ├─ count existing rows → if >= 20, return (no-op)
       ├─ coinbase.get_candles(limit=20) → historical OHLCV
       ├─ deduplicate by ts against existing rows
       └─ INSERT RawFeature rows (OHLCV only, live fields = None)
  └─ existing: fetch latest candle + order book + Kalshi
  └─ INSERT RawFeature row (all fields populated)
```

### Modified files

| File | Change |
|------|--------|
| `ingestor/feature_writer.py` | Add `warm_up_if_needed()`, call from `fetch_and_write()` |
| `tests/test_feature_writer.py` | 3 new tests for warm-up behaviour |

No schema migrations. No new env vars. No changes to predictor, trainer, or feature_builder.

---

## Constant

```python
_WARMUP_CANDLES = 20  # fetch 20 min of history; covers the 15m feature windows
```

Using 20 ensures:
- At least `_MIN_ROWS = 10` rows after dedup
- Full coverage of `_within(15)` windows used by `price_momentum_15m` and `vwap_deviation_15m`

---

## Deduplication

On container restart some rows may already exist. Before inserting, collect the set of existing `ts` values for this market in the warm-up window and skip any candle whose `ts` is already present:

```python
cutoff = datetime.now(timezone.utc) - timedelta(minutes=_WARMUP_CANDLES + 1)
existing_ts = {
    r.ts for r in session.query(RawFeature.ts)
    .filter(RawFeature.market_id == market_id, RawFeature.ts >= cutoff)
    .all()
}
```

---

## Error Handling

If `coinbase_client.get_candles()` raises, log a warning and return. The ingestor loop continues normally — warm-up is best-effort.

---

## Out of Scope

- Historical Kalshi price warm-up (API doesn't support per-minute history easily)
- Historical order-book warm-up (not available)
- Changing `_MIN_ROWS` in `feature_builder.py`
- Any changes to the predictor or trainer
