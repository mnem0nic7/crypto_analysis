# Accuracy Page — Series-Level Data Fix Design Spec

**Date:** 2026-05-15
**Status:** Approved

---

## Problem

The Accuracy page has correct chart logic but three broken data sources:

1. **`/stats/summary` cardinality bug** — groups by `(ticker, market_id)` and returns one row per *contract* (41,259 rows). The bar chart renders 41,259 near-invisible bars instead of 7 series bars.

2. **Charts fetch from active markets only** — rolling accuracy, calibration, and directional charts all call `fetchHistory(active_market_id)`. Active markets are just-opened 15-minute windows with 0–5 settled predictions each. The 45k-row backfill is spread across historical contracts, not the current active ones. Result: all three charts show "No data" or "Not enough data".

3. **No per-series breakdown** — the operator can see a global 95.6% win rate but cannot see which of the 7 crypto series is performing well or poorly.

---

## Solution

Two backend changes and a frontend redesign.

---

## Backend Changes

### 1. Fix `/stats/summary` — group by ticker, add direction + brier

**Change:** Group by `Market.ticker` only (not `market_id`). Add a second query for per-direction accuracy. Join active `ModelRegistry` rows for brier scores.

**New `markets` entry shape:**
```json
{
  "ticker": "KXBTC15M",
  "accuracy": 0.961,
  "settled_count": 7145,
  "brier_score": 0.162,
  "up_accuracy": 0.958,
  "up_count": 3612,
  "down_accuracy": 0.964,
  "down_count": 3533
}
```

**Implementation approach (three queries, merged in Python):**

```python
# Query 1: per-series totals
rows = (
    session.query(
        Market.ticker,
        func.count(Prediction.id).label("settled_count"),
        func.avg(correct_expr).label("accuracy"),
    )
    .join(Market, Market.market_id == Prediction.market_id)
    .filter(Prediction.actual_outcome != None)
    .group_by(Market.ticker)
    .all()
)

# Query 2: per-series per-direction breakdown
dir_rows = (
    session.query(
        Market.ticker,
        Prediction.direction,
        func.count(Prediction.id).label("count"),
        func.avg(correct_expr).label("accuracy"),
    )
    .join(Market, Market.market_id == Prediction.market_id)
    .filter(Prediction.actual_outcome != None)
    .group_by(Market.ticker, Prediction.direction)
    .all()
)

# Query 3: active model brier scores (ModelRegistry.market_id == series ticker)
model_rows = (
    session.query(ModelRegistry.market_id, ModelRegistry.brier_score)
    .filter(ModelRegistry.is_active == True)
    .all()
)
```

**Merge in Python:** Build `dir_by_ticker` dict from query 2, `brier_by_ticker` dict from query 3, then assemble per-ticker response.

### 2. New endpoint: `GET /history/series/{series_ticker}?limit=N`

Returns the most-recent `limit` settled predictions across all contracts of a series, ordered newest-first.

**Response shape:** Same as existing `/history/{market_id}` — list of `HistoryEntry`-compatible dicts.

```python
@app.get("/history/series/{series_ticker}")
def get_series_history(series_ticker: str, limit: int = 5000, session=Depends(_get_db)):
    preds = (
        session.query(Prediction)
        .join(Market, Prediction.market_id == Market.market_id)
        .filter(Market.ticker == series_ticker, Prediction.actual_outcome != None)
        .order_by(Prediction.ts.desc())
        .limit(limit)
        .all()
    )
    return [{"ts": p.ts.isoformat(), "direction": p.direction,
             "confidence": float(p.confidence), "actual_outcome": p.actual_outcome,
             "correct": (p.direction == "UP" and p.actual_outcome == 1)
                        or (p.direction == "DOWN" and p.actual_outcome == 0)} for p in preds]
```

**Limit default 5000** covers ~70 days of data per series (45k rows / 7 series / 90 days ≈ 70/day; 5000 / 70 ≈ 71 days). Enough for the 7d rolling chart with room to spare.

---

## Frontend Changes

### `api.ts`

Update `MarketSummary` interface:
```typescript
export interface MarketSummary {
  ticker: string
  accuracy: number
  settled_count: number
  brier_score?: number
  up_accuracy?: number
  up_count?: number
  down_accuracy?: number
  down_count?: number
}
```

Add fetch function:
```typescript
export const fetchSeriesHistory = (series_ticker: string, limit = 5000): Promise<HistoryEntry[]> =>
  apiFetch<HistoryEntry[]>(`/history/series/${encodeURIComponent(series_ticker)}?limit=${limit}`)
```

### `Accuracy.tsx` — redesigned layout

**State additions:**
```typescript
const [selectedSeries, setSelectedSeries] = useState<string>('KXBTC15M')
const [seriesHistory, setSeriesHistory] = useState<HistoryEntry[]>([])
```

**Data flow:**
- `fetchSummary()` → fixed 7-row `markets` array + global stats (unchanged call, new shape)
- `fetchModels()` → Avg Brier (unchanged)
- `fetchSeriesHistory(selectedSeries)` → feeds rolling, calibration charts
- `summary.markets` directional fields → feeds directional chart (no extra fetch)

**`load()` callback:** Calls `fetchSummary()` and `fetchSeriesHistory(selectedSeries)` in parallel. On series change, only `fetchSeriesHistory` is re-called.

**Rolling and calibration effects:**
```typescript
useEffect(() => {
  setRollingData(buildRolling(seriesHistory, rollingWindow))
  setCalibData(buildCalibration(seriesHistory))
}, [seriesHistory, rollingWindow])
```

**Directional data (derived from summary, no new fetch):**
```typescript
const dirData: DirPoint[] = (summary?.markets ?? []).map(m => ({
  ticker: m.ticker.replace('KX', '').replace('15M', ''),
  up: (m.up_count ?? 0) >= 3 ? Math.round((m.up_accuracy ?? 0) * 100) : null,
  upCount: m.up_count ?? 0,
  down: (m.down_count ?? 0) >= 3 ? Math.round((m.down_accuracy ?? 0) * 100) : null,
  downCount: m.down_count ?? 0,
}))
```

**Bar chart (fixed, 7 bars):**
```typescript
setBarData(sum.markets.map(m => ({
  ticker: m.ticker.replace('KX', '').replace('15M', ''), // "BTC", "ETH", etc.
  winRate: Math.round(m.accuracy * 100),
  count: m.settled_count,
})))
```

### New `Accuracy.tsx` layout

```
┌──────────────────────────────────────────────────────────────────┐
│ [Overall Win Rate] [High-Conf Acc] [Total Settled] [Avg Brier]   │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ Per-Series Breakdown (7 mini-cards in a row)                     │
│  BTC          ETH          SOL          XRP                      │
│  96.1% ✓     95.8% ✓     96.4% ✓     93.4%                     │
│  n=7,145      n=7,150      n=7,107      n=6,628                  │
│  Brier 0.162  Brier 0.162  Brier 0.160  Brier 0.182             │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────┐ ┌──────────────────────────┐
│ Win Rate per Series      │ │ Directional Accuracy      │
│ [7-bar chart]            │ │ (all series, UP/DOWN)     │
└──────────────────────────┘ └──────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ Series: [KXBTC15M ▼]   [24h] [7d] [30d]                        │
├──────────────────────────┬───────────────────────────────────────┤
│ Rolling Accuracy         │ Calibration Curve                     │
│                          │                                        │
└──────────────────────────┴───────────────────────────────────────┘
```

**Series selector** is a `<select>` element above the rolling+calibration row. Changing it triggers a `fetchSeriesHistory()` call and updates both charts. The selector lists the tickers from `summary.markets` (dynamically populated).

**30d window** is added to the existing 24h/7d toggle. In `buildRolling`, add `'30d'` to the union type and map it to `{ buckets: 30, bucketMs: 86_400_000, labelFn: toLocaleDateString({ month: 'short', day: 'numeric' }) }`.

### `Accuracy.module.css` additions

```css
/* 4-card global stats row */
.statGrid4 {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}

/* 7-card per-series row */
.seriesGrid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 10px;
  margin-bottom: 24px;
}

.seriesCard {
  background: var(--bg-card);
  border-radius: 6px;
  padding: 12px;
  text-align: center;
}

.seriesTicker {
  font-size: 12px;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 4px;
}

.seriesRate {
  font-size: 20px;
  font-weight: 700;
  margin-bottom: 4px;
}

.seriesMeta {
  font-size: 10px;
  color: var(--text-muted);
  line-height: 1.4;
}

/* Series selector row above charts */
.seriesSelector {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.seriesSelect {
  background: var(--bg-card);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
}
```

---

## Tests

### Backend (`tests/test_api.py`)

**Test 1: `test_stats_summary_groups_by_series`**
Seed two contracts for the same series ticker (`KXBTCUSD`), each with multiple predictions. Assert `len(body["markets"]) == 1` and `accuracy` is the pooled rate.

**Test 2: `test_stats_summary_includes_direction_breakdown`**
Seed UP and DOWN predictions with known outcomes. Assert `up_accuracy`, `up_count`, `down_accuracy`, `down_count` in the response.

**Test 3: `test_stats_summary_includes_brier_score`**
Seed an active `ModelRegistry` row for the series. Assert `brier_score` present in the series entry.

**Test 4: `test_series_history_returns_across_contracts`**
Seed two contracts under the same ticker. Add settled predictions to each. Assert `/history/series/{ticker}` returns rows from both contracts.

**Test 5: `test_series_history_respects_limit`**
Seed 20 predictions for a series. Assert `?limit=5` returns exactly 5 rows.

---

## Out of Scope

- Time-of-day accuracy analysis
- Per-series drill-down to individual contracts
- Exporting chart data
- Alert thresholds on accuracy degradation
- Profit/loss tracking alongside accuracy
