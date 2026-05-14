# Data Explorer — Design Spec

**Date:** 2026-05-14
**Status:** Approved

---

## Overview

A new dashboard page (`/data`) that gives a tabular view of training data and supports data analysis. It adds a fourth nav item ("🔍 Data") to the existing sidebar. The page has three tabs sharing a single filter bar (market + date range):

- **Raw Data** — paginated, sortable table of `raw_features` rows
- **Feature Vectors** — paginated, sortable table of the 25 computed features, one row per settled prediction
- **Analysis** — column stats + feature importance + outcome correlations for the filtered slice

---

## Architecture

### New route
`/data` added to `App.tsx` alongside existing routes. A single `DataExplorer` component handles all three tabs via local tab state.

### New API endpoints (all under `/data/`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/data/raw-features` | Paginated raw_features rows |
| GET | `/data/feature-vectors` | Paginated computed feature vectors (settled predictions only) |
| GET | `/data/stats` | Per-column stats (mean/std/min/max/null count) for current filter |
| GET | `/data/correlations` | Pearson correlation of each feature with actual_outcome |
| GET | `/data/feature-importance` | Active model's feature weights for a given series ticker |

All tabular endpoints share these query params: `market_id`, `from` (ISO datetime), `to` (ISO datetime), `page` (1-based, default 1), `page_size` (default 100), `sort_by` (column name), `sort_dir` (`asc`|`desc`).

### Filter state
Filter values (market, from, to) live in React component state. Switching tabs preserves the filter. No URL-param persistence (not needed for single-page tab design).

---

## Raw Data Tab

**Data source:** `raw_features` table filtered by `market_id` and `ts` range.

**Columns displayed:** `ts`, `price_open`, `price_high`, `price_low`, `price_close`, `volume`, `bid_depth_1pct`, `ask_depth_1pct`, `book_imbalance`, `kalshi_yes_price`, `kalshi_no_price`, `kalshi_volume`, `price_momentum_1m`, `price_momentum_5m`, `price_momentum_15m`, `volatility_5m` (all 17 raw columns).

**Sorting:** any column, default `ts DESC`. Active sort column highlighted in accent colour.

**Pagination:** 100 rows/page. API returns `{ rows, total, page, page_size }`.

**Value formatting:**
- `ts` — locale datetime string
- Price columns — locale number with 0 decimal places
- Ratio/momentum columns — sign-coloured (green ≥ 0, red < 0), 4 decimal places
- `book_imbalance` — sign-coloured, 3 decimal places

---

## Feature Vectors Tab

**Data source:** Settled `predictions` joined with `raw_features` via `feature_snapshot_id` (or by market + ts proximity). The API computes the 25-feature vector server-side using `build_feature_vector()` for each row in the page.

**Columns displayed:** `ts`, `direction` (UP/DOWN badge), `confidence`, all 25 `FEATURE_NAMES` columns, `actual_outcome` (UP/DOWN badge, coloured by match with direction).

**Sorting and pagination:** same as Raw Data tab.

**Performance constraint:** page size capped at 50 rows to bound per-request feature computation cost (up to 50 × 40 raw rows fetched and computed).

---

## Analysis Tab

Three cards laid out in a 2-column grid (stats + importance top row; correlations spanning full width below).

### Column Stats card
Displays per-column descriptive stats for the 25 feature vector columns computed over all settled predictions in the current filter window. Computed via `numpy` on the server.

Columns: Feature name, Mean, Std, Min, Max, Null count.

### Feature Importance card
Loads the active `ModelRegistry` entry for the selected series ticker (e.g. `KXBTCUSD`), reads the serialised XGBoost model artifact from `artifact_path` via `joblib.load()`, extracts `model.feature_importances_`, pairs with `FEATURE_NAMES`, and returns sorted descending by value.

Displayed as a horizontal bar chart (CSS-only bars, no chart library dependency).

If no active model exists for the selected market, shows a placeholder: "No active model — run the trainer first."

### Correlations card
Computes Pearson correlation of each of the 25 features with `actual_outcome` (0/1) across all settled predictions in the filter window.

Displayed as a grid of colour-coded tiles: purple intensity for positive correlation, red intensity for negative, grey for near-zero (|r| < 0.05). Tile shows the feature name and r value.

---

## Filter Bar

Persists at the top of the page above the tab bar. Contains:
- **Market** — `<select>` populated from `GET /markets` (active markets only)
- **From / To** — date inputs (ISO date, time defaults to 00:00:00 UTC / 23:59:59 UTC)
- **Apply** button — triggers data fetch for the active tab
- Row count indicator (e.g. "14,209 rows") returned by the active tab's count query

Default window: last 7 days, first active market.

---

## Sidebar Nav Addition

New entry added to `App.tsx` `navItems`:
```ts
{ to: '/data', label: '🔍 Data' }
```
And `VIEW_TITLES`:
```ts
'/data': 'Data Explorer'
```

---

## New Files

| File | Purpose |
|------|---------|
| `dashboard/src/views/DataExplorer.tsx` | Main view component (tabs, filter bar, tab panels) |
| `dashboard/src/views/DataExplorer.module.css` | Scoped styles for the view |

### Modified files

| File | Change |
|------|--------|
| `dashboard/src/App.tsx` | Add `/data` route + nav item |
| `dashboard/src/api.ts` | Add fetch functions for 5 new endpoints |
| `api/main.py` | Add 5 new route handlers under `/data/` prefix |

---

## API Response Shapes

```ts
// GET /data/raw-features
interface RawFeaturePage {
  rows: RawFeatureRow[]
  total: number
  page: number
  page_size: number
}
interface RawFeatureRow {
  id: number; ts: string; market_id: string
  price_open: number|null; price_high: number|null
  price_low: number|null; price_close: number|null
  volume: number|null; bid_depth_1pct: number|null
  ask_depth_1pct: number|null; book_imbalance: number|null
  kalshi_yes_price: number|null; kalshi_no_price: number|null
  kalshi_volume: number|null; price_momentum_1m: number|null
  price_momentum_5m: number|null; price_momentum_15m: number|null
  volatility_5m: number|null
}

// GET /data/feature-vectors
interface FeatureVectorPage {
  rows: FeatureVectorRow[]
  total: number
  page: number
  page_size: number
}
interface FeatureVectorRow {
  ts: string; direction: 'UP'|'DOWN'; confidence: number
  actual_outcome: 0|1
  features: Record<string, number>  // keyed by FEATURE_NAMES
}

// GET /data/stats
interface ColumnStats {
  feature: string
  mean: number; std: number; min: number; max: number; null_count: number
}[]

// GET /data/correlations
interface CorrelationEntry { feature: string; r: number }[]

// GET /data/feature-importance?series_ticker=...
interface ImportanceEntry { feature: string; importance: number }[]
```

---

## Error Handling

- All fetch errors surface via an `error-banner` div (matching existing pattern in Models.tsx).
- Feature Vectors tab: if `build_feature_vector()` returns `None` for a row (insufficient context), that row is skipped server-side and a `skipped` count is returned alongside `total`.
- Feature Importance: 404 if no active model; UI shows placeholder, not an error banner.
- Analysis tab: if fewer than 5 settled predictions exist in the window, all three cards show a "Not enough data" placeholder.

---

## Out of Scope

- CSV/export download
- Column visibility toggles
- Cross-market comparison
- Real-time auto-refresh (data explorer is intentionally manual — no `useAutoRefresh`)
