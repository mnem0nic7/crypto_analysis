# Accuracy Page Upgrades — Design Spec

**Date:** 2026-05-14
**Status:** Approved

---

## Overview

Upgrade the `/accuracy` dashboard page with four improvements:

1. **Two additional stat cards** — Avg Brier Score and High-Conf Count
2. **Time-window toggle** on the rolling accuracy chart (24h / 7d)
3. **Calibration Curve** — a new chart showing whether model confidence is well-calibrated
4. **Directional Accuracy** — a new grouped-bar chart splitting accuracy by UP vs DOWN direction

All new visualisations are computed client-side from data already available via existing endpoints. The only backend change is exposing `high_conf_count` that is already computed in `GET /stats/summary`.

---

## Architecture

### Data flow

```
fetchSummary()          → 4 existing stat cards + market bar data
                        → high_conf_count (new field, already computed server-side)
fetchModels()           → avg_brier stat card (active models only)
fetchHistory(id, 500)   → allHistory (bumped from 200 → 500)
  └─ buildRolling(allHistory, window)   → rolling chart (24h or 7d)
  └─ buildCalibration(allHistory)       → calibration chart
  └─ buildDirectional(allHistory)       → directional accuracy chart
```

### Modified files

| File | Change |
|------|--------|
| `api/main.py` | Add `high_conf_count` field to `/stats/summary` response |
| `dashboard/src/api.ts` | Add `high_conf_count: number` to `StatsSummary` interface |
| `dashboard/src/views/Accuracy.tsx` | All new charts + stat cards + toggle |
| `dashboard/src/views/Accuracy.module.css` | Styles for 4-chart grid + toggle buttons |

No new files, no new API routes.

---

## Backend change — `/stats/summary`

`hc_total` is already computed in `get_stats_summary()` (line ~194 of `api/main.py`) as `hc_row.hc_total`. Expose it:

```python
# existing line:
"high_conf_accuracy": round(high_conf_accuracy, 3),
# add after:
"high_conf_count": int(hc_row.hc_total or 0),
```

Also add to the empty-data return:
```python
"high_conf_count": 0,
```

---

## Stat Cards (6 total)

| Position | Label | Source |
|----------|-------|--------|
| 1 | Overall Win Rate | `summary.overall_accuracy` |
| 2 | High-Conf Accuracy | `summary.high_conf_accuracy` |
| 3 | High-Conf Count | `summary.high_conf_count` (new) |
| 4 | Total Settled | `summary.total_settled` |
| 5 | Avg Brier Score | mean of `model.brier_score` for active models from `fetchModels()` |
| 6 | Best Market | `summary.markets` max accuracy ticker |

The `stat-grid` CSS class already supports a 4-col grid; extend to 6 cols via a modifier class or set `grid-template-columns: repeat(6, 1fr)` on the Accuracy page.

---

## Chart 1 — Win Rate per Market (unchanged)

Existing `BarChart` — no changes. Only tooltip improvement: show `settled_count` alongside win rate.

---

## Chart 2 — Rolling Accuracy (upgraded)

**Toggle:** Two buttons above the chart — `24h` and `7d`. Default `24h`.

**`RollingPoint` type change:** Rename `hour` field to `label` to serve both modes (hours for 24h, day names for 7d). Update all references in the component.

**24h mode (existing logic):** Hourly buckets over the last 24 hours. 24 data points max. Label: `toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })`.

**7d mode (new):** Daily buckets over the last 7 days. 7 data points max. X-axis shows date label (`Mon`, `Tue`, …).

```ts
interface RollingPoint { label: string; accuracy: number; count: number }

function buildRolling(allHistory: HistoryEntry[], window: '24h' | '7d'): RollingPoint[] {
  const now = Date.now()
  const buckets = window === '24h' ? 24 : 7
  const bucketMs = window === '24h' ? 3_600_000 : 86_400_000
  const points: RollingPoint[] = []
  for (let i = buckets - 1; i >= 0; i--) {
    const windowEnd = now - i * bucketMs
    const windowStart = windowEnd - bucketMs
    const bucket = allHistory.filter(e => {
      const t = new Date(e.ts).getTime()
      return t >= windowStart && t < windowEnd
    })
    if (bucket.length === 0) continue
    const correct = bucket.filter(e => e.correct).length
    const label = window === '24h'
      ? new Date(windowEnd).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : new Date(windowEnd).toLocaleDateString([], { weekday: 'short' })
    points.push({ label, accuracy: Math.round((correct / bucket.length) * 100), count: bucket.length })
  }
  return points
}
```
```

History fetch bumped to `fetchHistory(m.market_id, 500)` to ensure 7 days of settled predictions are available.

---

## Chart 3 — Calibration Curve (new)

**Purpose:** The most important ML diagnostic — shows whether the model's confidence scores are meaningful. A confidence of 0.70 should correspond to ~70% actual accuracy.

**Implementation:** `ComposedChart` from recharts with two series:
- `Line` for model calibration (actual accuracy per confidence bucket)
- `Line` for the "perfect calibration" diagonal reference (dashed, muted colour)

**Bucketing logic:**
```ts
interface CalibPoint {
  bucket: string   // e.g. "0.55–0.60"
  midpoint: number // e.g. 0.575
  predicted: number // = midpoint × 100 (for reference line)
  actual: number    // actual accuracy % in this bucket
  count: number
}

function buildCalibration(allHistory: HistoryEntry[]): CalibPoint[] {
  const edges = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 1.01]
  return edges.slice(0, -1).map((lo, i) => {
    const hi = edges[i + 1]
    const mid = (lo + hi) / 2
    const bucket = allHistory.filter(e => e.confidence >= lo && e.confidence < hi)
    const correct = bucket.filter(e => e.correct).length
    return {
      bucket: `${(lo * 100).toFixed(0)}–${(hi === 1.01 ? 100 : hi * 100).toFixed(0)}`,
      midpoint: mid,
      predicted: Math.round(mid * 100),
      actual: bucket.length >= 3 ? Math.round((correct / bucket.length) * 100) : null,
      count: bucket.length,
    }
  }).filter(p => p.count > 0)
}
```

**Tooltip:** Shows `Predicted: X%`, `Actual: Y%`, `n = Z predictions` for the hovered bucket.

**Rendering:** Buckets with `count < 3` render as `null` in the `actual` series (recharts skips null data points with `connectNulls={false}`).

---

## Chart 4 — Directional Accuracy (new)

**Purpose:** Shows UP accuracy and DOWN accuracy separately per market ticker. Reveals directional bias — e.g., model may be better at calling UP than DOWN.

**Implementation:** `BarChart` with two `Bar` children (recharts grouped bars).

```ts
interface DirPoint {
  ticker: string
  up: number | null    // % correct on UP predictions
  upCount: number
  down: number | null  // % correct on DOWN predictions
  downCount: number
}

function buildDirectional(
  historyByTicker: Map<string, HistoryEntry[]>,
): DirPoint[] {
  return Array.from(historyByTicker.entries()).map(([ticker, entries]) => {
    const ups = entries.filter(e => e.direction === 'UP')
    const downs = entries.filter(e => e.direction === 'DOWN')
    return {
      ticker,
      up: ups.length >= 3 ? Math.round(ups.filter(e => e.correct).length / ups.length * 100) : null,
      upCount: ups.length,
      down: downs.length >= 3 ? Math.round(downs.filter(e => e.correct).length / downs.length * 100) : null,
      downCount: downs.length,
    }
  })
}
```

**Building `historyByTicker`:** In the `load()` callback, when `Promise.allSettled` resolves, zip results with the `markets` array (same order as the fetch loop):

```ts
const historyByTicker = new Map<string, HistoryEntry[]>()
markets.forEach((m, i) => {
  const r = histories[i]
  if (r.status === 'fulfilled') historyByTicker.set(m.ticker, r.value)
})
```

This avoids needing a ticker field on `HistoryEntry`.

**Chart:** Two bars per market — green (`var(--green)`) for UP, red (`var(--red)`) for DOWN. Y-axis 0–100%.

---

## Chart Grid Layout

Upgrade from 2-column to 2×2:

```css
.charts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-top: 8px;
}
```

This is already the existing layout — just add 2 more `chartCard` divs. No CSS change needed for the grid itself.

The toggle buttons for the rolling chart sit inside the `chartCard` header area:

```css
.chartHeader {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.windowToggle {
  display: flex;
  gap: 4px;
}

.windowBtn {
  padding: 2px 8px;
  font-size: 10px;
  background: none;
  border: 1px solid var(--border);
  color: var(--text-muted);
  border-radius: 3px;
}

.windowBtnActive {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
```

---

## Error Handling

- If `fetchModels()` fails, Avg Brier stat card shows `—` (non-fatal, separate try/catch)
- Calibration chart with `< 3` predictions in a bucket: that bucket's `actual` value is `null` (recharts skips it with no gap — uses `connectNulls={false}`)
- If all calibration buckets have `count = 0`, show the existing `<div className="placeholder">` pattern

---

## Out of Scope

- Per-market drill-down filter (would need a market selector; too much scope)
- Confidence vs. accuracy scatter plot (redundant with calibration curve)
- Export / download of chart data
- Real-time streaming of new predictions
