# Accuracy Page Upgrades Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the `/accuracy` dashboard page with 6 stat cards, a 24h/7d toggle on the rolling chart, a calibration curve, and a directional accuracy chart — all computed client-side from existing data.

**Architecture:** A single backend change exposes `high_conf_count` (already computed) from `/stats/summary`. The frontend rewrites `Accuracy.tsx` to add new state, builder functions, and four chart cards. All history data is fetched with limit 500 and processed client-side into rolling, calibration, and directional datasets.

**Tech Stack:** FastAPI + SQLAlchemy (Python backend), React 18 + TypeScript + recharts 2.x + CSS Modules (frontend).

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `api/main.py` | Add `high_conf_count` to `/stats/summary` response |
| Modify | `dashboard/src/api.ts` | Add `high_conf_count: number` to `StatsSummary` interface |
| Modify | `tests/test_api.py` | Assert `high_conf_count` in summary tests |
| Modify | `dashboard/src/views/Accuracy.module.css` | Add `.statGrid6`, `.chartHeader`, `.windowToggle`, `.windowBtn`, `.windowBtnActive` |
| Modify | `dashboard/src/views/Accuracy.tsx` | Full component rewrite: 6 stat cards, rolling toggle, calibration + directional charts |

---

## Task 1: Backend — expose `high_conf_count` in `/stats/summary`

**Files:**
- Modify: `api/main.py`
- Modify: `dashboard/src/api.ts`
- Modify: `tests/test_api.py`

Context: The `/stats/summary` handler already computes `hc_total` (count of predictions with confidence ≥ 0.65) in the `hc_row` query result, but never returns it. This task exposes it.

- [ ] **Step 1: Add a failing test assertion to `tests/test_api.py`**

In `test_stats_summary_no_data` (around line 91), add one assertion:
```python
def test_stats_summary_no_data(db_session):
    client = _make_test_app(db_session)
    resp = client.get("/stats/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_settled"] == 0
    assert body["overall_accuracy"] == 0.0
    assert body["high_conf_accuracy"] == 0.0
    assert body["high_conf_count"] == 0   # NEW
    assert body["markets"] == []
```

In `test_stats_summary_aggregates_correctly` (around line 131), add:
```python
    assert abs(body["high_conf_accuracy"] - 2/3) < 0.01
    assert body["high_conf_count"] == 3  # NEW — all 3 predictions have conf >= 0.65
    assert len(body["markets"]) == 1
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_api.py::test_stats_summary_no_data tests/test_api.py::test_stats_summary_aggregates_correctly -v
```

Expected: FAILED with `KeyError: 'high_conf_count'` or assertion error.

- [ ] **Step 3: Add `high_conf_count` to `api/main.py`**

In `get_stats_summary()`, find the early-return block (when `not rows`) and add the new field:
```python
        if not rows:
            return {
                "total_settled": 0,
                "overall_accuracy": 0.0,
                "high_conf_accuracy": 0.0,
                "high_conf_count": 0,
                "markets": [],
            }
```

Then find the main return block (after `high_conf_accuracy = ...`) and add the field:
```python
        return {
            "total_settled": total_settled,
            "overall_accuracy": round(overall_accuracy, 3),
            "high_conf_accuracy": round(high_conf_accuracy, 3),
            "high_conf_count": int(hc_row.hc_total or 0),
            "markets": [
                {
                    "ticker": r.ticker,
                    "accuracy": round(float(r.accuracy or 0), 3),
                    "settled_count": r.settled_count,
                }
                for r in rows
            ],
        }
```

- [ ] **Step 4: Add `high_conf_count` to `StatsSummary` in `dashboard/src/api.ts`**

Find the `StatsSummary` interface (around line 35) and add one field:
```typescript
export interface StatsSummary {
  total_settled: number
  overall_accuracy: number
  high_conf_accuracy: number
  high_conf_count: number
  markets: MarketSummary[]
}
```

- [ ] **Step 5: Run the tests and confirm they pass**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_api.py::test_stats_summary_no_data tests/test_api.py::test_stats_summary_aggregates_correctly -v
```

Expected: 2 tests PASS.

- [ ] **Step 6: Run full test suite — confirm no regressions**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add api/main.py dashboard/src/api.ts tests/test_api.py
git commit -m "feat: expose high_conf_count in /stats/summary"
```

---

## Task 2: CSS — add styles for new Accuracy UI elements

**Files:**
- Modify: `dashboard/src/views/Accuracy.module.css`

- [ ] **Step 1: Replace the contents of `dashboard/src/views/Accuracy.module.css` with**

```css
.statGrid6 {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 12px;
  margin-bottom: 24px;
}

.charts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-top: 8px;
}

.chartCard {
  background: var(--bg-card);
  border-radius: 6px;
  padding: 16px;
}

.chartTitle {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-label);
  margin-bottom: 12px;
}

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
  cursor: pointer;
  font-family: inherit;
}

.windowBtn:hover {
  background: none;
  color: var(--text);
}

.windowBtnActive {
  background: var(--accent) !important;
  border-color: var(--accent) !important;
  color: #fff !important;
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/src/views/Accuracy.module.css
git commit -m "style: add accuracy page CSS for 6-stat grid and chart toggle"
```

---

## Task 3: Frontend — rewrite `Accuracy.tsx` with all new charts and stat cards

**Files:**
- Modify: `dashboard/src/views/Accuracy.tsx`

Before writing this file, read `dashboard/src/App.tsx` to confirm the exact shape of `useRefreshContext()` — it should have `setRefreshFn`, `setIsLoading`, `setLastRefreshed`. Also confirm that `fetchModels` is exported from `dashboard/src/api.ts` (it is, as `fetchModels(): Promise<ModelInfo[]>`).

- [ ] **Step 1: Replace the contents of `dashboard/src/views/Accuracy.tsx` with**

```tsx
import { useCallback, useEffect, useState } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  AreaChart, Area, CartesianGrid,
  ComposedChart, Line,
} from 'recharts'
import { useRefreshContext } from '../App'
import { fetchHistory, fetchMarkets, fetchModels, fetchSummary } from '../api'
import type { HistoryEntry, StatsSummary } from '../api'
import { useAutoRefresh } from '../hooks/useAutoRefresh'
import styles from './Accuracy.module.css'

interface MarketBar {
  ticker: string
  winRate: number
  count: number
}

interface RollingPoint {
  label: string
  accuracy: number
  count: number
}

interface CalibPoint {
  bucket: string
  predicted: number
  actual: number | null
  count: number
}

interface DirPoint {
  ticker: string
  up: number | null
  upCount: number
  down: number | null
  downCount: number
}

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

function buildCalibration(allHistory: HistoryEntry[]): CalibPoint[] {
  const edges = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 1.01]
  return edges.slice(0, -1).map((lo, idx) => {
    const hi = edges[idx + 1]
    const mid = (lo + hi) / 2
    const bucket = allHistory.filter(e => e.confidence >= lo && e.confidence < hi)
    const correct = bucket.filter(e => e.correct).length
    return {
      bucket: `${(lo * 100).toFixed(0)}–${(hi === 1.01 ? 100 : hi * 100).toFixed(0)}`,
      predicted: Math.round(mid * 100),
      actual: bucket.length >= 3 ? Math.round((correct / bucket.length) * 100) : null,
      count: bucket.length,
    }
  }).filter(p => p.count > 0)
}

function buildDirectional(historyByTicker: Map<string, HistoryEntry[]>): DirPoint[] {
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

export default function Accuracy({ intervalMs }: { intervalMs: number }) {
  const [summary, setSummary] = useState<StatsSummary | null>(null)
  const [avgBrier, setAvgBrier] = useState<number | null>(null)
  const [barData, setBarData] = useState<MarketBar[]>([])
  const [allHistory, setAllHistory] = useState<HistoryEntry[]>([])
  const [rollingWindow, setRollingWindow] = useState<'24h' | '7d'>('24h')
  const [rollingData, setRollingData] = useState<RollingPoint[]>([])
  const [calibData, setCalibData] = useState<CalibPoint[]>([])
  const [dirData, setDirData] = useState<DirPoint[]>([])
  const [fetchError, setFetchError] = useState<string | null>(null)
  const ctx = useRefreshContext()

  const load = useCallback(async () => {
    try {
      const [sum, markets] = await Promise.all([fetchSummary(), fetchMarkets()])
      setSummary(sum)
      setBarData(sum.markets.map(m => ({
        ticker: m.ticker,
        winRate: Math.round(m.accuracy * 100),
        count: m.settled_count,
      })))

      fetchModels().then(models => {
        const active = models.filter(m => m.is_active)
        if (active.length > 0) {
          setAvgBrier(active.reduce((s, m) => s + m.brier_score, 0) / active.length)
        }
      }).catch(() => {})

      const histories = await Promise.allSettled(
        markets.map(m => fetchHistory(m.market_id, 500))
      )
      const all: HistoryEntry[] = histories
        .filter((r): r is PromiseFulfilledResult<HistoryEntry[]> => r.status === 'fulfilled')
        .flatMap(r => r.value)

      const byTicker = new Map<string, HistoryEntry[]>()
      markets.forEach((m, i) => {
        const r = histories[i]
        if (r.status === 'fulfilled') byTicker.set(m.ticker, r.value)
      })

      setAllHistory(all)
      setCalibData(buildCalibration(all))
      setDirData(buildDirectional(byTicker))
      setFetchError(null)
    } catch (e) {
      setFetchError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  const { lastRefreshed, isLoading, triggerRefresh } = useAutoRefresh(load, intervalMs)

  useEffect(() => {
    ctx.setRefreshFn(triggerRefresh)
    ctx.setIsLoading(isLoading)
    ctx.setLastRefreshed(lastRefreshed)
  }, [ctx, triggerRefresh, isLoading, lastRefreshed])

  // Rebuild rolling data when history loads or window toggles — no re-fetch needed
  useEffect(() => {
    setRollingData(buildRolling(allHistory, rollingWindow))
  }, [allHistory, rollingWindow])

  const bestMarket = summary?.markets.reduce(
    (best, m) => (!best || m.accuracy > best.accuracy ? m : best),
    null as (typeof summary.markets)[0] | null
  )

  const tooltipStyle = { background: '#1a1d27', border: '1px solid #2a2d3a' }

  return (
    <div>
      {fetchError && <div className="error-banner">{fetchError}</div>}

      <div className={styles.statGrid6}>
        <div className="stat-card">
          <div className="stat-label">Overall Win Rate</div>
          <div className="stat-value">
            {summary ? `${(summary.overall_accuracy * 100).toFixed(1)}%` : '—'}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">High-Conf Accuracy</div>
          <div className="stat-value">
            {summary ? `${(summary.high_conf_accuracy * 100).toFixed(1)}%` : '—'}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">High-Conf Count</div>
          <div className="stat-value">{summary ? summary.high_conf_count : '—'}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Total Settled</div>
          <div className="stat-value">{summary?.total_settled ?? '—'}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Avg Brier Score</div>
          <div className="stat-value">{avgBrier !== null ? avgBrier.toFixed(3) : '—'}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Best Market</div>
          <div className="stat-value">{bestMarket?.ticker ?? '—'}</div>
        </div>
      </div>

      {(!summary || summary.total_settled === 0) && !fetchError && (
        <div className="placeholder">No settled predictions yet</div>
      )}

      {summary && summary.total_settled > 0 && (
        <div className={styles.charts}>

          {/* Chart 1 — Win Rate per Market */}
          <div className={styles.chartCard}>
            <div className={styles.chartTitle}>Win Rate per Market</div>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={barData} margin={{ top: 4, right: 8, bottom: 4, left: -16 }}>
                <XAxis dataKey="ticker" tick={{ fill: '#94a3b8', fontSize: 11 }} />
                <YAxis domain={[0, 100]} tick={{ fill: '#94a3b8', fontSize: 11 }} />
                <Tooltip
                  contentStyle={tooltipStyle}
                  formatter={(v: unknown, _name: unknown, props: { payload?: MarketBar }) => [
                    `${String(v)}% (n=${props.payload?.count ?? 0})`,
                    'Win Rate',
                  ]}
                />
                <Bar dataKey="winRate" fill="#7c3aed" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Chart 2 — Rolling Accuracy with 24h/7d toggle */}
          <div className={styles.chartCard}>
            <div className={styles.chartHeader}>
              <span className={styles.chartTitle} style={{ marginBottom: 0 }}>Rolling Accuracy</span>
              <div className={styles.windowToggle}>
                {(['24h', '7d'] as const).map(w => (
                  <button
                    key={w}
                    className={`${styles.windowBtn} ${rollingWindow === w ? styles.windowBtnActive : ''}`}
                    onClick={() => setRollingWindow(w)}
                  >
                    {w}
                  </button>
                ))}
              </div>
            </div>
            {rollingData.length === 0 ? (
              <div className="placeholder" style={{ padding: '60px 0' }}>No data</div>
            ) : (
              <ResponsiveContainer width="100%" height={200}>
                <AreaChart data={rollingData} margin={{ top: 4, right: 8, bottom: 4, left: -16 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3a" />
                  <XAxis dataKey="label" tick={{ fill: '#94a3b8', fontSize: 10 }} />
                  <YAxis domain={[0, 100]} tick={{ fill: '#94a3b8', fontSize: 11 }} />
                  <Tooltip
                    contentStyle={tooltipStyle}
                    formatter={(v: unknown, _name: unknown, props: { payload?: RollingPoint }) => [
                      `${String(v)}% (n=${props.payload?.count ?? 0})`,
                      'Accuracy',
                    ]}
                  />
                  <Area type="monotone" dataKey="accuracy" stroke="#7c3aed" fill="rgba(124,58,237,0.15)" />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>

          {/* Chart 3 — Calibration Curve */}
          <div className={styles.chartCard}>
            <div className={styles.chartTitle}>Calibration Curve</div>
            {calibData.length === 0 ? (
              <div className="placeholder" style={{ padding: '60px 0' }}>Not enough data</div>
            ) : (
              <ResponsiveContainer width="100%" height={200}>
                <ComposedChart data={calibData} margin={{ top: 4, right: 8, bottom: 4, left: -16 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3a" />
                  <XAxis dataKey="bucket" tick={{ fill: '#94a3b8', fontSize: 10 }} />
                  <YAxis domain={[0, 100]} tick={{ fill: '#94a3b8', fontSize: 11 }} />
                  <Tooltip
                    contentStyle={tooltipStyle}
                    formatter={(v: unknown, name: string, props: { payload?: CalibPoint }) => {
                      if (name === 'actual') return [`${String(v)}% (n=${props.payload?.count ?? 0})`, 'Actual']
                      return [`${String(v)}%`, 'Perfect']
                    }}
                  />
                  <Line type="monotone" dataKey="predicted" stroke="#2a2d3a" strokeDasharray="4 4" dot={false} name="predicted" />
                  <Line type="monotone" dataKey="actual" stroke="#7c3aed" dot={{ r: 3, fill: '#7c3aed' }} connectNulls={false} name="actual" />
                </ComposedChart>
              </ResponsiveContainer>
            )}
          </div>

          {/* Chart 4 — Directional Accuracy */}
          <div className={styles.chartCard}>
            <div className={styles.chartTitle}>Directional Accuracy (UP vs DOWN)</div>
            {dirData.length === 0 ? (
              <div className="placeholder" style={{ padding: '60px 0' }}>No data</div>
            ) : (
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={dirData} margin={{ top: 4, right: 8, bottom: 4, left: -16 }}>
                  <XAxis dataKey="ticker" tick={{ fill: '#94a3b8', fontSize: 11 }} />
                  <YAxis domain={[0, 100]} tick={{ fill: '#94a3b8', fontSize: 11 }} />
                  <Tooltip
                    contentStyle={tooltipStyle}
                    formatter={(v: unknown, name: string, props: { payload?: DirPoint }) => {
                      const count = name === 'up' ? props.payload?.upCount : props.payload?.downCount
                      return [`${String(v)}% (n=${count ?? 0})`, name === 'up' ? 'UP' : 'DOWN']
                    }}
                  />
                  <Bar dataKey="up" fill="#22c55e" radius={[3, 3, 0, 0]} name="up" />
                  <Bar dataKey="down" fill="#ef4444" radius={[3, 3, 0, 0]} name="down" />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>

        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Run TypeScript type check**

```bash
cd /workspace/crypto_analysis/dashboard && npx tsc --noEmit
```

Expected: no errors. If TypeScript complains about the recharts `formatter` callback types, change the `props` parameter type to `any` for those callbacks only — recharts' internal types are complex.

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/views/Accuracy.tsx
git commit -m "feat: upgrade accuracy page with calibration curve, directional chart, and 24h/7d toggle"
```

---

## Task 4: Rebuild and smoke-test

**Files:** none (integration check only)

- [ ] **Step 1: Run full Python test suite**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 2: Deploy green slot**

```bash
bash /workspace/crypto_analysis/scripts/deploy.sh green
```

Expected: build succeeds, health check passes, Caddy reloads.

- [ ] **Step 3: Push to main**

```bash
cd /workspace/crypto_analysis && git push origin main
```
