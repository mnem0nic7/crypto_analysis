# Accuracy Page — Series-Level Data Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the Accuracy page so all four charts show real data by switching from per-contract to per-series data sources, adding a series selector, and surfacing per-series Brier scores and direction breakdown.

**Architecture:** Two backend changes (fix `/stats/summary` grouping + add `/history/series/{ticker}` endpoint), one TypeScript client update, and a frontend redesign that replaces active-market history fetches with series-level history. No new files — all changes are in-place edits.

**Tech Stack:** FastAPI + SQLAlchemy (backend), React + recharts + TypeScript (frontend), pytest (tests).

---

## File Map

| File | Change |
|------|--------|
| `api/main.py` | Fix `get_stats_summary()` (group by ticker; add direction + brier). Add `get_series_history()` endpoint. |
| `tests/test_api.py` | Add 5 new tests (Task 1 × 3, Task 2 × 2). |
| `dashboard/src/api.ts` | Extend `MarketSummary` interface. Add `fetchSeriesHistory()`. |
| `dashboard/src/views/Accuracy.tsx` | Full redesign: series cards, series selector, use series history. |
| `dashboard/src/views/Accuracy.module.css` | Add CSS for series cards and selector row. |

---

## Task 1: Fix `/stats/summary` — group by series ticker

**Background:** The endpoint currently groups by `(Market.ticker, Prediction.market_id)` and returns one row *per contract* — 41,259 rows for this deployment. The bar chart renders all 41k as near-invisible slivers. The fix changes the `GROUP BY` to `Market.ticker` only (7 rows), adds per-direction accuracy, and joins `ModelRegistry` for Brier scores.

**Files:**
- Modify: `api/main.py` — `get_stats_summary()` function (around line 120–198)
- Modify: `tests/test_api.py` — add 3 new tests after the existing summary tests

- [ ] **Step 1: Write the three failing tests**

Add these three test functions at the end of `tests/test_api.py`:

```python
def test_stats_summary_groups_by_series_not_contract(db_session):
    """Two contracts under the same series ticker must collapse to one markets row."""
    now = datetime.now(timezone.utc)
    for cid in ("KXBTCUSD-C1", "KXBTCUSD-C2"):
        m = Market(
            market_id=cid, ticker="KXBTCUSD", status="active",
            close_time=now + timedelta(minutes=7),
            discovered_at=now, updated_at=now,
        )
        db_session.add(m)
    db_session.flush()
    # C1: 2 correct UP predictions; C2: 2 wrong DOWN predictions (actual=1, direction=DOWN)
    for cid, direction, actual in [
        ("KXBTCUSD-C1", "UP", 1), ("KXBTCUSD-C1", "UP", 1),
        ("KXBTCUSD-C2", "DOWN", 1), ("KXBTCUSD-C2", "DOWN", 1),
    ]:
        db_session.add(Prediction(
            market_id=cid, ts=now, direction=direction, confidence=0.70,
            low_confidence=False, model_version="v1",
            settled_at=now - timedelta(minutes=1), actual_outcome=actual,
        ))
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/stats/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_settled"] == 4
    assert len(body["markets"]) == 1, "must collapse to one row per series"
    assert body["markets"][0]["ticker"] == "KXBTCUSD"
    assert body["markets"][0]["settled_count"] == 4
    assert abs(body["markets"][0]["accuracy"] - 0.5) < 0.01


def test_stats_summary_includes_direction_breakdown(db_session):
    """markets entries must include up_accuracy, up_count, down_accuracy, down_count."""
    now = datetime.now(timezone.utc)
    m = Market(
        market_id="KXBTCUSD-D1", ticker="KXBTCUSD", status="active",
        close_time=now + timedelta(minutes=7), discovered_at=now, updated_at=now,
    )
    db_session.add(m)
    db_session.flush()
    # 3 correct UP, 1 wrong UP, 2 correct DOWN
    for direction, actual in [
        ("UP", 1), ("UP", 1), ("UP", 1), ("UP", 0),  # UP: 3/4
        ("DOWN", 0), ("DOWN", 0),                      # DOWN: 2/2
    ]:
        db_session.add(Prediction(
            market_id="KXBTCUSD-D1", ts=now, direction=direction, confidence=0.70,
            low_confidence=False, model_version="v1",
            settled_at=now - timedelta(minutes=1), actual_outcome=actual,
        ))
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/stats/summary")
    assert resp.status_code == 200
    m_row = resp.json()["markets"][0]
    assert m_row["up_count"] == 4
    assert abs(m_row["up_accuracy"] - 0.75) < 0.01
    assert m_row["down_count"] == 2
    assert abs(m_row["down_accuracy"] - 1.0) < 0.01


def test_stats_summary_includes_brier_score(db_session):
    """markets entries include brier_score from the active ModelRegistry row."""
    from shared.orm import ModelRegistry
    now = datetime.now(timezone.utc)
    m = Market(
        market_id="KXBTCUSD-B1", ticker="KXBTCUSD", status="active",
        close_time=now + timedelta(minutes=7), discovered_at=now, updated_at=now,
    )
    db_session.add(m)
    db_session.flush()
    db_session.add(Prediction(
        market_id="KXBTCUSD-B1", ts=now, direction="UP", confidence=0.70,
        low_confidence=False, model_version="v1",
        settled_at=now - timedelta(minutes=1), actual_outcome=1,
    ))
    # sentinel market row required because ModelRegistry.market_id FK points to markets
    sentinel = Market(
        market_id="KXBTCUSD", ticker="KXBTCUSD", status="series",
        discovered_at=now, updated_at=now,
    )
    db_session.add(sentinel)
    db_session.flush()
    db_session.add(ModelRegistry(
        market_id="KXBTCUSD", version="v3",
        trained_at=now, training_rows=500, brier_score=0.182,
        artifact_path="/app/models/KXBTCUSD_v3.joblib", is_active=True,
    ))
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/stats/summary")
    assert resp.status_code == 200
    m_row = resp.json()["markets"][0]
    assert m_row["brier_score"] is not None
    assert abs(m_row["brier_score"] - 0.182) < 0.001
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /workspace/crypto_analysis
source venv/bin/activate
pytest tests/test_api.py::test_stats_summary_groups_by_series_not_contract tests/test_api.py::test_stats_summary_includes_direction_breakdown tests/test_api.py::test_stats_summary_includes_brier_score -v
```

Expected: 3 FAILED (AssertionError on `len(markets)` and missing keys).

- [ ] **Step 3: Replace `get_stats_summary()` in `api/main.py`**

Replace the entire `@app.get("/stats/summary")` function body. The function signature stays the same. Find it at approximately line 120 and replace with:

```python
    @app.get("/stats/summary")
    def get_stats_summary(session: Session = Depends(_get_db)):
        from sqlalchemy import func, case as sa_case
        from shared.orm import ModelRegistry

        correct_expr = sa_case(
            (
                (Prediction.direction == "UP") & (Prediction.actual_outcome == 1),
                1,
            ),
            (
                (Prediction.direction == "DOWN") & (Prediction.actual_outcome == 0),
                1,
            ),
            else_=0,
        )

        # Query 1: per-series totals (group by ticker only, not market_id)
        rows = (
            session.query(
                Market.ticker,
                func.count(Prediction.id).label("settled_count"),
                func.avg(correct_expr).label("accuracy"),
            )
            .join(Market, Market.market_id == Prediction.market_id)
            .filter(Prediction.actual_outcome != None)  # noqa: E711
            .group_by(Market.ticker)
            .all()
        )

        if not rows:
            return {
                "total_settled": 0,
                "overall_accuracy": 0.0,
                "high_conf_accuracy": 0.0,
                "high_conf_count": 0,
                "markets": [],
            }

        total_settled = sum(r.settled_count for r in rows)
        overall_accuracy = (
            sum(float(r.accuracy or 0) * r.settled_count for r in rows) / total_settled
        )

        hc_expr = sa_case(
            (
                (Prediction.confidence >= 0.65)
                & (Prediction.direction == "UP")
                & (Prediction.actual_outcome == 1),
                1,
            ),
            (
                (Prediction.confidence >= 0.65)
                & (Prediction.direction == "DOWN")
                & (Prediction.actual_outcome == 0),
                1,
            ),
            else_=0,
        )
        hc_total_expr = sa_case(
            (Prediction.confidence >= 0.65, 1),
            else_=0,
        )
        hc_row = (
            session.query(
                func.sum(hc_expr).label("hc_correct"),
                func.sum(hc_total_expr).label("hc_total"),
            )
            .filter(Prediction.actual_outcome != None)  # noqa: E711
            .one()
        )
        if hc_row.hc_total:
            high_conf_accuracy = float(hc_row.hc_correct or 0) / float(hc_row.hc_total)
        else:
            high_conf_accuracy = 0.0

        # Query 2: per-series per-direction breakdown
        dir_rows = (
            session.query(
                Market.ticker,
                Prediction.direction,
                func.count(Prediction.id).label("count"),
                func.avg(correct_expr).label("accuracy"),
            )
            .join(Market, Market.market_id == Prediction.market_id)
            .filter(Prediction.actual_outcome != None)  # noqa: E711
            .group_by(Market.ticker, Prediction.direction)
            .all()
        )
        dir_by_ticker: dict[str, dict] = {}
        for d in dir_rows:
            if d.ticker not in dir_by_ticker:
                dir_by_ticker[d.ticker] = {}
            dir_by_ticker[d.ticker][d.direction] = {
                "count": d.count,
                "accuracy": round(float(d.accuracy or 0), 3),
            }

        # Query 3: active model brier scores
        # ModelRegistry.market_id == series ticker for series models (e.g. "KXBTC15M")
        model_rows = (
            session.query(ModelRegistry.market_id, ModelRegistry.brier_score)
            .filter(ModelRegistry.is_active == True)  # noqa: E712
            .all()
        )
        brier_by_ticker = {r.market_id: round(float(r.brier_score), 4) for r in model_rows}

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
                    "brier_score": brier_by_ticker.get(r.ticker),
                    "up_accuracy": dir_by_ticker.get(r.ticker, {}).get("UP", {}).get("accuracy"),
                    "up_count": dir_by_ticker.get(r.ticker, {}).get("UP", {}).get("count", 0),
                    "down_accuracy": dir_by_ticker.get(r.ticker, {}).get("DOWN", {}).get("accuracy"),
                    "down_count": dir_by_ticker.get(r.ticker, {}).get("DOWN", {}).get("count", 0),
                }
                for r in rows
            ],
        }
```

- [ ] **Step 4: Run all summary tests to verify they pass**

```bash
pytest tests/test_api.py -k "summary" -v
```

Expected: All 6 summary tests PASS (the 3 original + 3 new).

- [ ] **Step 5: Commit**

```bash
git add api/main.py tests/test_api.py
git commit -m "fix: stats/summary groups by series ticker, adds direction breakdown and brier score"
```

---

## Task 2: Add `/history/series/{series_ticker}` endpoint

**Background:** Charts need settled prediction history across all contracts of a series, not just the current active market. This endpoint joins through `Market.ticker` to return all settled predictions for a series.

**Files:**
- Modify: `api/main.py` — add new route after `get_history()`
- Modify: `tests/test_api.py` — add 2 new tests

- [ ] **Step 1: Write the two failing tests**

Add these after the existing history test in `tests/test_api.py`:

```python
def test_series_history_returns_across_contracts(db_session):
    """Returns settled predictions from ALL contracts of a series, not just one."""
    now = datetime.now(timezone.utc)
    for cid in ("KXBTCUSD-SH1", "KXBTCUSD-SH2"):
        db_session.add(Market(
            market_id=cid, ticker="KXBTCUSD", status="active",
            close_time=now + timedelta(minutes=7), discovered_at=now, updated_at=now,
        ))
    db_session.flush()
    for cid, direction, actual in [
        ("KXBTCUSD-SH1", "UP", 1),
        ("KXBTCUSD-SH1", "DOWN", 0),
        ("KXBTCUSD-SH2", "UP", 1),
    ]:
        db_session.add(Prediction(
            market_id=cid, ts=now, direction=direction, confidence=0.70,
            low_confidence=False, model_version="v1",
            settled_at=now - timedelta(minutes=1), actual_outcome=actual,
        ))
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/history/series/KXBTCUSD")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    assert all("ts" in row and "direction" in row and "correct" in row for row in data)


def test_series_history_respects_limit(db_session):
    """?limit=N returns at most N rows."""
    now = datetime.now(timezone.utc)
    db_session.add(Market(
        market_id="KXBTCUSD-LIM", ticker="KXBTCUSD", status="active",
        close_time=now + timedelta(minutes=7), discovered_at=now, updated_at=now,
    ))
    db_session.flush()
    for i in range(10):
        db_session.add(Prediction(
            market_id="KXBTCUSD-LIM",
            ts=now - timedelta(minutes=i),
            direction="UP", confidence=0.70, low_confidence=False, model_version="v1",
            settled_at=now - timedelta(minutes=i + 1), actual_outcome=1,
        ))
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/history/series/KXBTCUSD?limit=4")
    assert resp.status_code == 200
    assert len(resp.json()) == 4
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_api.py::test_series_history_returns_across_contracts tests/test_api.py::test_series_history_respects_limit -v
```

Expected: 2 FAILED with 404 (route does not exist).

- [ ] **Step 3: Add the endpoint in `api/main.py`**

Insert this block immediately after the existing `get_history()` function (after the closing `return [...]`):

```python
    @app.get("/history/series/{series_ticker}")
    def get_series_history(
        series_ticker: str,
        limit: int = 5000,
        session: Session = Depends(_get_db),
    ):
        preds = (
            session.query(Prediction)
            .join(Market, Prediction.market_id == Market.market_id)
            .filter(
                Market.ticker == series_ticker,
                Prediction.actual_outcome != None,  # noqa: E711
            )
            .order_by(Prediction.ts.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "ts": p.ts.isoformat(),
                "direction": p.direction,
                "confidence": float(p.confidence),
                "actual_outcome": p.actual_outcome,
                "correct": (
                    (p.direction == "UP" and p.actual_outcome == 1)
                    or (p.direction == "DOWN" and p.actual_outcome == 0)
                ),
            }
            for p in preds
        ]
```

- [ ] **Step 4: Run all history tests to verify they pass**

```bash
pytest tests/test_api.py -k "history" -v
```

Expected: All 3 history tests PASS (1 original + 2 new).

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/test_api.py -v
```

Expected: All tests PASS. No regressions.

- [ ] **Step 6: Commit**

```bash
git add api/main.py tests/test_api.py
git commit -m "feat: add /history/series/{series_ticker} endpoint for series-level history"
```

---

## Task 3: Update TypeScript client (`api.ts`)

**Background:** The `MarketSummary` interface must reflect the new fields from the fixed `/stats/summary`. A new `fetchSeriesHistory()` function must call the new `/history/series/` endpoint.

**Files:**
- Modify: `dashboard/src/api.ts`

- [ ] **Step 1: Replace `MarketSummary` interface**

Find the existing interface:
```typescript
export interface MarketSummary {
  ticker: string
  accuracy: number
  settled_count: number
}
```

Replace with:
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

- [ ] **Step 2: Add `fetchSeriesHistory` function**

Find:
```typescript
export const fetchHistory = (market_id: string, limit = 200): Promise<HistoryEntry[]> =>
  apiFetch<HistoryEntry[]>(`/history/${encodeURIComponent(market_id)}?limit=${limit}`)
```

Add immediately after:
```typescript
export const fetchSeriesHistory = (series_ticker: string, limit = 5000): Promise<HistoryEntry[]> =>
  apiFetch<HistoryEntry[]>(`/history/series/${encodeURIComponent(series_ticker)}?limit=${limit}`)
```

- [ ] **Step 3: Type-check**

```bash
cd /workspace/crypto_analysis/dashboard
npx tsc --noEmit
```

Expected: No errors.

- [ ] **Step 4: Commit**

```bash
cd /workspace/crypto_analysis
git add dashboard/src/api.ts
git commit -m "feat: extend MarketSummary type and add fetchSeriesHistory to api client"
```

---

## Task 4: Redesign `Accuracy.tsx` and update `Accuracy.module.css`

**Background:** The full frontend redesign. Replace the 6-card stat grid with 4 global cards + 7 per-series mini-cards. Replace active-market history fetches with series-level history (`fetchSeriesHistory`). Add a series selector so the rolling and calibration charts show data for one series at a time. Add 30d window option. The directional chart now uses direction fields from the fixed summary (no extra API calls).

**Files:**
- Modify: `dashboard/src/views/Accuracy.module.css`
- Modify: `dashboard/src/views/Accuracy.tsx`

- [ ] **Step 1: Update `Accuracy.module.css`**

Replace the entire file with:

```css
.statGrid4 {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}

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

.seriesSelector {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.selectorLabel {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-label);
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

.charts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 16px;
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

- [ ] **Step 2: Replace `Accuracy.tsx` entirely**

Replace the entire file contents with:

```tsx
import { useCallback, useEffect, useState } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  AreaChart, Area, CartesianGrid,
  ComposedChart, Line,
} from 'recharts'
import { useRefreshContext } from '../App'
import { fetchModels, fetchSummary, fetchSeriesHistory } from '../api'
import type { HistoryEntry, StatsSummary } from '../api'
import { useAutoRefresh } from '../hooks/useAutoRefresh'
import styles from './Accuracy.module.css'

interface MarketBar { ticker: string; winRate: number; count: number }
interface RollingPoint { label: string; accuracy: number; count: number }
interface CalibPoint { bucket: string; predicted: number; actual: number | null; count: number }
interface DirPoint { ticker: string; up: number | null; upCount: number; down: number | null; downCount: number }

function buildRolling(history: HistoryEntry[], window: '24h' | '7d' | '30d'): RollingPoint[] {
  const now = Date.now()
  const [buckets, bucketMs] =
    window === '24h' ? [24, 3_600_000] :
    window === '7d'  ? [7,  86_400_000] :
                       [30, 86_400_000]
  const points: RollingPoint[] = []
  for (let i = buckets - 1; i >= 0; i--) {
    const end = now - i * bucketMs
    const start = end - bucketMs
    const slice = history.filter(e => { const t = new Date(e.ts).getTime(); return t >= start && t < end })
    if (slice.length === 0) continue
    const correct = slice.filter(e => e.correct).length
    const label =
      window === '24h' ? new Date(end).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) :
      window === '7d'  ? new Date(end).toLocaleDateString([], { weekday: 'short' }) :
                         new Date(end).toLocaleDateString([], { month: 'short', day: 'numeric' })
    points.push({ label, accuracy: Math.round((correct / slice.length) * 100), count: slice.length })
  }
  return points
}

function buildCalibration(history: HistoryEntry[]): CalibPoint[] {
  const edges = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 1.01]
  return edges.slice(0, -1).map((lo, idx) => {
    const hi = edges[idx + 1]
    const mid = (lo + hi) / 2
    const bucket = history.filter(e => e.confidence >= lo && e.confidence < hi)
    const correct = bucket.filter(e => e.correct).length
    return {
      bucket: `${(lo * 100).toFixed(0)}–${(hi === 1.01 ? 100 : hi * 100).toFixed(0)}`,
      predicted: Math.round(mid * 100),
      actual: bucket.length >= 3 ? Math.round((correct / bucket.length) * 100) : null,
      count: bucket.length,
    }
  }).filter(p => p.count > 0)
}

const SERIES_LIST = ['KXBTC15M', 'KXETH15M', 'KXSOL15M', 'KXXRP15M', 'KXDOGE15M', 'KXBNB15M', 'KXHYPE15M']

export default function Accuracy({ intervalMs }: { intervalMs: number }) {
  const [summary, setSummary] = useState<StatsSummary | null>(null)
  const [avgBrier, setAvgBrier] = useState<number | null>(null)
  const [barData, setBarData] = useState<MarketBar[]>([])
  const [dirData, setDirData] = useState<DirPoint[]>([])
  const [selectedSeries, setSelectedSeries] = useState<string>('KXBTC15M')
  const [seriesHistory, setSeriesHistory] = useState<HistoryEntry[]>([])
  const [rollingWindow, setRollingWindow] = useState<'24h' | '7d' | '30d'>('7d')
  const [rollingData, setRollingData] = useState<RollingPoint[]>([])
  const [calibData, setCalibData] = useState<CalibPoint[]>([])
  const [fetchError, setFetchError] = useState<string | null>(null)
  const ctx = useRefreshContext()

  const load = useCallback(async () => {
    try {
      const sum = await fetchSummary()
      setSummary(sum)
      setBarData(sum.markets.map(m => ({
        ticker: m.ticker.replace('KX', '').replace('15M', ''),
        winRate: Math.round(m.accuracy * 100),
        count: m.settled_count,
      })))
      setDirData(sum.markets.map(m => ({
        ticker: m.ticker.replace('KX', '').replace('15M', ''),
        up: (m.up_count ?? 0) >= 3 ? Math.round((m.up_accuracy ?? 0) * 100) : null,
        upCount: m.up_count ?? 0,
        down: (m.down_count ?? 0) >= 3 ? Math.round((m.down_accuracy ?? 0) * 100) : null,
        downCount: m.down_count ?? 0,
      })))
      fetchModels().then(models => {
        const active = models.filter(m => m.is_active)
        if (active.length > 0) setAvgBrier(active.reduce((s, m) => s + m.brier_score, 0) / active.length)
      }).catch(() => {})
      setFetchError(null)
    } catch (e) {
      setFetchError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  // Re-fetch series history whenever the selected series changes
  useEffect(() => {
    fetchSeriesHistory(selectedSeries)
      .then(setSeriesHistory)
      .catch(() => {})
  }, [selectedSeries])

  // Rebuild rolling and calibration from series history or window change
  useEffect(() => {
    setRollingData(buildRolling(seriesHistory, rollingWindow))
    setCalibData(buildCalibration(seriesHistory))
  }, [seriesHistory, rollingWindow])

  const { lastRefreshed, isLoading, triggerRefresh } = useAutoRefresh(load, intervalMs)

  useEffect(() => {
    ctx.setRefreshFn(triggerRefresh)
    ctx.setIsLoading(isLoading)
    ctx.setLastRefreshed(lastRefreshed)
  }, [ctx, triggerRefresh, isLoading, lastRefreshed])

  const tooltipStyle = { background: '#1a1d27', border: '1px solid #2a2d3a' }

  return (
    <div>
      {fetchError && <div className="error-banner">{fetchError}</div>}

      {/* Global stat cards */}
      <div className={styles.statGrid4}>
        <div className="stat-card">
          <div className="stat-label">Overall Win Rate</div>
          <div className="stat-value">{summary ? `${(summary.overall_accuracy * 100).toFixed(1)}%` : '—'}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">High-Conf Accuracy</div>
          <div className="stat-value">{summary ? `${(summary.high_conf_accuracy * 100).toFixed(1)}%` : '—'}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Total Settled</div>
          <div className="stat-value">{summary?.total_settled?.toLocaleString() ?? '—'}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Avg Brier Score</div>
          <div className="stat-value">{avgBrier !== null ? avgBrier.toFixed(3) : '—'}</div>
        </div>
      </div>

      {/* Per-series mini-cards */}
      {summary && summary.markets.length > 0 && (
        <div className={styles.seriesGrid}>
          {summary.markets.map(m => {
            const pct = m.accuracy * 100
            const color = pct >= 90 ? 'var(--green)' : pct >= 80 ? 'var(--amber)' : 'var(--red)'
            return (
              <div key={m.ticker} className={styles.seriesCard}>
                <div className={styles.seriesTicker}>{m.ticker.replace('KX', '').replace('15M', '')}</div>
                <div className={styles.seriesRate} style={{ color }}>{pct.toFixed(1)}%</div>
                <div className={styles.seriesMeta}>n={m.settled_count.toLocaleString()}</div>
                {m.brier_score !== undefined && (
                  <div className={styles.seriesMeta}>Brier {m.brier_score.toFixed(3)}</div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {(!summary || summary.total_settled === 0) && !fetchError && (
        <div className="placeholder">No settled predictions yet</div>
      )}

      {summary && summary.total_settled > 0 && (
        <>
          {/* Row 1: Win Rate per Series + Directional Accuracy */}
          <div className={styles.charts}>
            <div className={styles.chartCard}>
              <div className={styles.chartTitle}>Win Rate per Series</div>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={barData} margin={{ top: 4, right: 8, bottom: 4, left: -16 }}>
                  <XAxis dataKey="ticker" tick={{ fill: '#94a3b8', fontSize: 11 }} />
                  <YAxis domain={[0, 100]} tick={{ fill: '#94a3b8', fontSize: 11 }} />
                  <Tooltip
                    contentStyle={tooltipStyle}
                    formatter={(v: unknown, _n: unknown, props: { payload?: MarketBar }) => [
                      `${String(v)}% (n=${props.payload?.count?.toLocaleString() ?? 0})`, 'Win Rate',
                    ]}
                  />
                  <Bar dataKey="winRate" fill="#7c3aed" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>

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

          {/* Series selector for rolling + calibration */}
          <div className={styles.seriesSelector}>
            <span className={styles.selectorLabel}>Series:</span>
            <select
              className={styles.seriesSelect}
              value={selectedSeries}
              onChange={e => setSelectedSeries(e.target.value)}
            >
              {SERIES_LIST.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>

          {/* Row 2: Rolling Accuracy + Calibration Curve */}
          <div className={styles.charts}>
            <div className={styles.chartCard}>
              <div className={styles.chartHeader}>
                <span className={styles.chartTitle} style={{ marginBottom: 0 }}>Rolling Accuracy</span>
                <div className={styles.windowToggle}>
                  {(['24h', '7d', '30d'] as const).map(w => (
                    <button
                      key={w}
                      className={`${styles.windowBtn} ${rollingWindow === w ? styles.windowBtnActive : ''}`}
                      onClick={() => setRollingWindow(w)}
                    >{w}</button>
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
                      formatter={(v: unknown, _n: unknown, props: { payload?: RollingPoint }) => [
                        `${String(v)}% (n=${props.payload?.count ?? 0})`, 'Accuracy',
                      ]}
                    />
                    <Area type="monotone" dataKey="accuracy" stroke="#7c3aed" fill="rgba(124,58,237,0.15)" />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>

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
          </div>
        </>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Type-check and build**

```bash
cd /workspace/crypto_analysis/dashboard
npx tsc --noEmit
npm run build
```

Expected: No TypeScript errors. Build succeeds with output in `dist/`.

- [ ] **Step 4: Rebuild and restart the blue dashboard container**

```bash
cd /workspace/crypto_analysis
docker compose -f docker-compose.blue.yml build dashboard-blue
docker compose -f docker-compose.blue.yml up -d dashboard-blue
```

Expected: Container recreated. `docker logs blue-dashboard --tail 5` shows nginx worker processes started (no errors).

- [ ] **Step 5: Smoke-test the live dashboard**

```bash
# Summary endpoint now returns 7 rows
curl -s http://localhost:8011/stats/summary | python3 -c "
import json,sys; d=json.load(sys.stdin)
print('markets count:', len(d['markets']))
print('first market:', d['markets'][0] if d['markets'] else 'none')
"
```

Expected: `markets count: 7`. First market has keys `ticker, accuracy, settled_count, brier_score, up_accuracy, up_count, down_accuracy, down_count`.

```bash
# Series history endpoint returns rows
curl -s "http://localhost:8011/history/series/KXBTC15M?limit=10" | python3 -c "
import json,sys; d=json.load(sys.stdin); print(f'{len(d)} rows'); print(d[0] if d else 'empty')
"
```

Expected: 10 rows with `ts, direction, confidence, actual_outcome, correct`.

```bash
# Dashboard proxy works
curl -s -o /dev/null -w "%{http_code}" http://localhost:4001/api/stats/summary
```

Expected: `200`.

- [ ] **Step 6: Commit**

```bash
cd /workspace/crypto_analysis
git add dashboard/src/views/Accuracy.tsx dashboard/src/views/Accuracy.module.css
git commit -m "feat: redesign accuracy page with series-level data, per-series cards, and series selector"
```

---

## Self-Review

**Spec coverage check:**
- ✅ Fix `/stats/summary` group by ticker → Task 1
- ✅ Add direction breakdown + brier_score → Task 1
- ✅ Add `/history/series/{ticker}` endpoint → Task 2
- ✅ Update `MarketSummary` TypeScript type → Task 3
- ✅ Add `fetchSeriesHistory` → Task 3
- ✅ Per-series mini-cards (7 cards) → Task 4
- ✅ Series selector for rolling + calibration → Task 4
- ✅ Win Rate bar chart fixed (7 bars, BTC/ETH labels) → Task 4
- ✅ Rolling Accuracy uses series history + 30d window → Task 4
- ✅ Calibration Curve uses series history → Task 4
- ✅ Directional Accuracy uses summary UP/DOWN fields → Task 4
- ✅ 5 new tests (3 summary + 2 history) → Tasks 1–2

**Placeholder scan:** No TBDs. All code blocks are complete.

**Type consistency:**
- `MarketSummary.up_count` (Task 3) used as `m.up_count` in Task 4 — ✅
- `MarketSummary.up_accuracy` (Task 3) used as `m.up_accuracy` in Task 4 — ✅
- `fetchSeriesHistory` (Task 3) called in Task 4 — ✅
- `DirPoint.up/down/upCount/downCount` defined in Task 4 header and used in chart — ✅
- `buildRolling` window type `'24h' | '7d' | '30d'` matches `rollingWindow` state type — ✅
