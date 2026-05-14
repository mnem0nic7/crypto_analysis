# Continuous Learning Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the feedback loop so prediction outcomes are settled automatically, models retrain on a schedule, and the dashboard shows training health.

**Architecture:** `backfill_outcomes()` moves into the ingestor loop (every 30 s) so outcomes settle within one poll after market close. The trainer gains a `while True` daemon loop using the existing `training_campaign_cooldown_seconds` setting. A new `/stats/training` API endpoint and a Training card on the System view make the loop observable.

**Tech Stack:** Python 3.11, SQLAlchemy 2.0, FastAPI, React 18 + TypeScript, Docker Compose

---

## File Map

| File | Change |
|------|--------|
| `trainer/backfill.py` | Remove `_strip_tz()`; use tz-aware datetimes in both functions |
| `ingestor/main.py` | Import + call `backfill_outcomes()` at the top of `_ingest_loop()` |
| `trainer/main.py` | Remove `TRAINING_CAMPAIGN_ENABLED` guard; add daemon `while True` loop |
| `api/main.py` | Add `GET /stats/training` endpoint |
| `tests/test_api.py` | Add 4 tests for the new endpoint |
| `dashboard/src/api.ts` | Add `TrainingStatus` interface + `fetchTrainingStatus` |
| `dashboard/src/views/System.tsx` | Fetch training status; render Training card |
| `dashboard/src/views/System.module.css` | Widen grid to 4 columns; deployment card spans 4 |
| `trainer/Dockerfile` | Add `COPY predictor/ predictor/` (missing dep for `feature_builder`) |
| `docker-compose.blue.yml` | Add `trainer-blue` service |
| `docker-compose.green.yml` | Add `trainer-green` service |

---

### Task 1: Fix Postgres timezone bug in backfill

**Files:**
- Modify: `trainer/backfill.py`
- Test: `tests/test_trainer.py` (existing tests must keep passing)

**Background:** `_strip_tz()` was added for SQLite compat, but in production Postgres
(`TIMESTAMP(timezone=True)`) a naive datetime in the filter silently returns 0 rows.
SQLAlchemy 2.0 handles tz-aware filter values correctly on both SQLite and Postgres.

- [ ] **Step 1: Run existing backfill tests to establish a baseline**

```bash
cd /workspace/crypto_analysis
source venv/bin/activate
pytest tests/test_trainer.py::test_backfill_sets_actual_outcome \
       tests/test_trainer.py::test_backfill_skips_already_settled -v
```

Expected: both tests PASS. If either fails, stop and investigate before proceeding.

- [ ] **Step 2: Replace `trainer/backfill.py` with the tz-aware version**

Replace the entire file content:

```python
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from shared.orm import Prediction, RawFeature

logger = logging.getLogger(__name__)


def backfill_outcomes(session: Session) -> int:
    now = datetime.now(timezone.utc)
    unsettled = (
        session.query(Prediction)
        .filter(
            Prediction.settled_at <= now,
            Prediction.actual_outcome == None,  # noqa: E711
        )
        .all()
    )
    updated = 0
    for pred in unsettled:
        price_at_pred = _price_at(session, pred.market_id, pred.ts, direction="before")
        price_at_settle = _price_at(session, pred.market_id, pred.settled_at, direction="after")
        if price_at_pred is None or price_at_settle is None:
            logger.warning(
                "Cannot backfill %s at %s — missing raw_features",
                pred.market_id, pred.ts,
            )
            continue
        # Flat price (==) treated as DOWN (0); rare in practice
        pred.actual_outcome = 1 if price_at_settle > price_at_pred else 0
        updated += 1
    session.flush()
    logger.info("Backfilled %d predictions", updated)
    return updated


def _price_at(session: Session, market_id: str, ts: datetime, direction: str) -> float | None:
    if direction == "before":
        row = (
            session.query(RawFeature)
            .filter(RawFeature.market_id == market_id, RawFeature.ts <= ts)
            .order_by(RawFeature.ts.desc())
            .first()
        )
    else:
        row = (
            session.query(RawFeature)
            .filter(RawFeature.market_id == market_id, RawFeature.ts >= ts)
            .order_by(RawFeature.ts.asc())
            .first()
        )
    return float(row.price_close) if row is not None and row.price_close is not None else None
```

- [ ] **Step 3: Run backfill tests to verify they still pass**

```bash
pytest tests/test_trainer.py::test_backfill_sets_actual_outcome \
       tests/test_trainer.py::test_backfill_skips_already_settled -v
```

Expected: both PASS. The tz-aware filter values are now used throughout; SQLAlchemy 2.0 renders them correctly as ISO strings with `+00:00` offset for SQLite and as proper timestamptz parameters for Postgres.

- [ ] **Step 4: Run the full test suite to check for regressions**

```bash
pytest --tb=short -q
```

Expected: all tests pass. Note: tests that use live network calls (Kalshi, Coinbase) are skipped or mocked.

- [ ] **Step 5: Commit**

```bash
git add trainer/backfill.py
git commit -m "fix: remove _strip_tz — use tz-aware datetimes for Postgres compat"
```

---

### Task 2: Add outcome settlement to the ingestor loop

**Files:**
- Modify: `ingestor/main.py`

**Background:** The ingestor already runs every 30 s and has a DB session open.
Calling `backfill_outcomes()` at the top of each iteration means any prediction whose
`settled_at` has passed will get its `actual_outcome` filled within 30 s of market close.

- [ ] **Step 1: Update `ingestor/main.py`**

Open `ingestor/main.py`. Make two changes:

**Add import** after the existing imports block (after line `from ingestor.feature_writer import fetch_and_write`):

```python
from trainer.backfill import backfill_outcomes
```

**Replace `_ingest_loop()`** (the entire function, currently lines 70–81):

```python
def _ingest_loop():
    while True:
        with session_scope(session_factory) as session:
            backfilled = backfill_outcomes(session)
            if backfilled:
                logger.info("Settled %d outcomes", backfilled)
            if time.time() - _last_discovery > _DISCOVERY_INTERVAL:
                _run_discovery(session)
            _mark_stale(session)
            active_markets = (
                session.query(Market).filter(Market.status == "active").all()
            )
            for market in active_markets:
                fetch_and_write(session, market.market_id, market.ticker, coinbase, kalshi)
        time.sleep(_POLL_INTERVAL)
```

- [ ] **Step 2: Run the full test suite to confirm no regressions**

```bash
pytest --tb=short -q
```

Expected: all tests pass. The ingestor module-level code (KalshiClient, CoinbaseClient init) is not executed during pytest because tests never import `ingestor.main` directly.

- [ ] **Step 3: Commit**

```bash
git add ingestor/main.py
git commit -m "feat: settle prediction outcomes in ingestor loop every 30s"
```

---

### Task 3: Convert trainer to a persistent daemon

**Files:**
- Modify: `trainer/main.py`

**Background:** Currently `trainer/main.py` exits immediately when
`TRAINING_CAMPAIGN_ENABLED=false` (the default). The container needs to stay running
and retrain periodically. The `training_campaign_cooldown_seconds` setting (default 600)
already exists for this purpose.

- [ ] **Step 1: Replace `trainer/main.py` with the daemon version**

Replace the entire file content:

```python
import logging
import time
from shared.db import make_session_factory, session_scope
from shared.orm import Market
from shared.settings import Settings
from trainer.backfill import backfill_outcomes
from trainer.train import train_and_promote

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def run_training_campaign(settings: Settings, session_factory) -> None:
    with session_scope(session_factory) as session:
        backfilled = backfill_outcomes(session)
        logger.info("Backfilled %d outcomes before training", backfilled)

    with session_scope(session_factory) as session:
        markets = session.query(Market).filter(Market.status == "active").all()
        market_ids = [m.market_id for m in markets]

    logger.info("Starting training campaign for %d markets", len(market_ids))
    for market_id in market_ids:
        with session_scope(session_factory) as session:
            result = train_and_promote(
                session=session,
                market_id=market_id,
                lookback_hours=settings.training_campaign_lookback_hours,
                models_dir="models",
            )
            if result:
                logger.info("Market %s: %s", market_id, result)
        time.sleep(0.1)


if __name__ == "__main__":
    settings = Settings()
    session_factory = make_session_factory(settings)
    logger.info(
        "Trainer daemon starting — cooldown=%ds", settings.training_campaign_cooldown_seconds
    )
    while True:
        run_training_campaign(settings, session_factory)
        logger.info(
            "Campaign done — sleeping %ds", settings.training_campaign_cooldown_seconds
        )
        time.sleep(settings.training_campaign_cooldown_seconds)
```

Key changes from the old version:
- `TRAINING_CAMPAIGN_ENABLED` check removed from `run_training_campaign()`
- `__main__` block now loops forever, sleeping `training_campaign_cooldown_seconds` between runs

- [ ] **Step 2: Run the full test suite**

```bash
pytest --tb=short -q
```

Expected: all tests pass. No existing tests call `run_training_campaign` directly, so removing the guard has no test impact.

- [ ] **Step 3: Commit**

```bash
git add trainer/main.py
git commit -m "feat: convert trainer to persistent daemon with cooldown loop"
```

---

### Task 4: Add `/stats/training` API endpoint (TDD)

**Files:**
- Modify: `tests/test_api.py` (write tests first)
- Modify: `api/main.py` (then implement)

- [ ] **Step 1: Add 4 failing tests to `tests/test_api.py`**

Append to the end of `tests/test_api.py`:

```python
def test_stats_training_empty_db(db_session):
    client = _make_test_app(db_session)
    resp = client.get("/stats/training")
    assert resp.status_code == 200
    body = resp.json()
    assert body["last_trained_at"] is None
    assert body["active_models"] == 0
    assert body["settled_last_24h"] == 0
    assert body["unmodeled_markets"] == 0


def test_stats_training_counts_settled_24h(db_session):
    from shared.orm import ModelRegistry
    m = Market(
        market_id="KXBTCUSD-TR1", ticker="BTC", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(minutes=7),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(m)
    db_session.flush()
    # One prediction settled within last 24h
    p_recent = Prediction(
        market_id="KXBTCUSD-TR1",
        ts=datetime.now(timezone.utc) - timedelta(hours=2),
        direction="UP", confidence=0.7, low_confidence=False,
        model_version="v1",
        settled_at=datetime.now(timezone.utc) - timedelta(hours=2),
        actual_outcome=1,
    )
    # One prediction settled more than 24h ago — should NOT be counted
    p_old = Prediction(
        market_id="KXBTCUSD-TR1",
        ts=datetime.now(timezone.utc) - timedelta(hours=25),
        direction="DOWN", confidence=0.6, low_confidence=False,
        model_version="v1",
        settled_at=datetime.now(timezone.utc) - timedelta(hours=25),
        actual_outcome=0,
    )
    db_session.add(p_recent)
    db_session.add(p_old)
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/stats/training")
    assert resp.status_code == 200
    assert resp.json()["settled_last_24h"] == 1


def test_stats_training_counts_active_models(db_session):
    from shared.orm import ModelRegistry
    m = Market(
        market_id="KXBTCUSD-TR2", ticker="BTC", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(minutes=7),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(m)
    db_session.flush()
    active_reg = ModelRegistry(
        market_id="KXBTCUSD-TR2", version="v1",
        trained_at=datetime.now(timezone.utc) - timedelta(hours=1),
        training_rows=200, brier_score=0.22,
        artifact_path="/app/models/KXBTCUSD-TR2_v1.joblib",
        is_active=True,
    )
    inactive_reg = ModelRegistry(
        market_id="KXBTCUSD-TR2", version="v0",
        trained_at=datetime.now(timezone.utc) - timedelta(hours=2),
        training_rows=100, brier_score=0.28,
        artifact_path="/app/models/KXBTCUSD-TR2_v0.joblib",
        is_active=False,
    )
    db_session.add(active_reg)
    db_session.add(inactive_reg)
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/stats/training")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_models"] == 1
    assert body["last_trained_at"] is not None


def test_stats_training_detects_unmodeled_markets(db_session):
    from shared.orm import ModelRegistry
    m1 = Market(
        market_id="KXBTCUSD-TR3", ticker="BTC", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(minutes=7),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    m2 = Market(
        market_id="KXBTCUSD-TR4", ticker="ETH", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(minutes=7),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(m1)
    db_session.add(m2)
    db_session.flush()
    # Only m1 has an active model
    reg = ModelRegistry(
        market_id="KXBTCUSD-TR3", version="v1",
        trained_at=datetime.now(timezone.utc),
        training_rows=150, brier_score=0.20,
        artifact_path="/app/models/KXBTCUSD-TR3_v1.joblib",
        is_active=True,
    )
    db_session.add(reg)
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/stats/training")
    assert resp.status_code == 200
    assert resp.json()["unmodeled_markets"] == 1
```

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
pytest tests/test_api.py::test_stats_training_empty_db \
       tests/test_api.py::test_stats_training_counts_settled_24h \
       tests/test_api.py::test_stats_training_counts_active_models \
       tests/test_api.py::test_stats_training_detects_unmodeled_markets -v
```

Expected: all 4 FAIL with `404 Not Found` (endpoint doesn't exist yet).

- [ ] **Step 3: Add the `/stats/training` endpoint to `api/main.py`**

In `api/main.py`, add the following method inside `create_app()`, after the `get_slot` function (before the final `return app` line):

```python
    @app.get("/stats/training")
    def get_stats_training(session: Session = Depends(_get_db)):
        from shared.orm import ModelRegistry
        from sqlalchemy import func
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        cutoff_24h = now - timedelta(hours=24)

        last_trained = session.query(func.max(ModelRegistry.trained_at)).scalar()

        active_models = (
            session.query(func.count(ModelRegistry.id))
            .filter(ModelRegistry.is_active == True)  # noqa: E712
            .scalar()
        ) or 0

        settled_24h = (
            session.query(func.count(Prediction.id))
            .filter(
                Prediction.actual_outcome != None,  # noqa: E711
                Prediction.ts >= cutoff_24h,
            )
            .scalar()
        ) or 0

        active_market_ids = [
            r[0]
            for r in session.query(Market.market_id)
            .filter(Market.status == "active")
            .all()
        ]
        modeled_ids = {
            r[0]
            for r in session.query(ModelRegistry.market_id)
            .filter(ModelRegistry.is_active == True)  # noqa: E712
            .all()
        }
        unmodeled = sum(1 for mid in active_market_ids if mid not in modeled_ids)

        return {
            "last_trained_at": last_trained.isoformat() if last_trained else None,
            "active_models": active_models,
            "settled_last_24h": settled_24h,
            "unmodeled_markets": unmodeled,
        }
```

The full `api/main.py` around the insertion point (find the `get_slot` function at the end of `create_app`):

```python
    @app.get("/slot")
    def get_slot():
        return {"slot": os.environ.get("DEPLOY_SLOT", "blue")}

    @app.get("/stats/training")
    def get_stats_training(session: Session = Depends(_get_db)):
        from shared.orm import ModelRegistry
        from sqlalchemy import func
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        cutoff_24h = now - timedelta(hours=24)

        last_trained = session.query(func.max(ModelRegistry.trained_at)).scalar()

        active_models = (
            session.query(func.count(ModelRegistry.id))
            .filter(ModelRegistry.is_active == True)  # noqa: E712
            .scalar()
        ) or 0

        settled_24h = (
            session.query(func.count(Prediction.id))
            .filter(
                Prediction.actual_outcome != None,  # noqa: E711
                Prediction.ts >= cutoff_24h,
            )
            .scalar()
        ) or 0

        active_market_ids = [
            r[0]
            for r in session.query(Market.market_id)
            .filter(Market.status == "active")
            .all()
        ]
        modeled_ids = {
            r[0]
            for r in session.query(ModelRegistry.market_id)
            .filter(ModelRegistry.is_active == True)  # noqa: E712
            .all()
        }
        unmodeled = sum(1 for mid in active_market_ids if mid not in modeled_ids)

        return {
            "last_trained_at": last_trained.isoformat() if last_trained else None,
            "active_models": active_models,
            "settled_last_24h": settled_24h,
            "unmodeled_markets": unmodeled,
        }

    return app
```

- [ ] **Step 4: Run the 4 new tests to verify they pass**

```bash
pytest tests/test_api.py::test_stats_training_empty_db \
       tests/test_api.py::test_stats_training_counts_settled_24h \
       tests/test_api.py::test_stats_training_counts_active_models \
       tests/test_api.py::test_stats_training_detects_unmodeled_markets -v
```

Expected: all 4 PASS.

- [ ] **Step 5: Run the full test suite**

```bash
pytest --tb=short -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add tests/test_api.py api/main.py
git commit -m "feat: add /stats/training endpoint with settlement and model counts"
```

---

### Task 5: Update dashboard API client

**Files:**
- Modify: `dashboard/src/api.ts`

- [ ] **Step 1: Add `TrainingStatus` interface and `fetchTrainingStatus` to `dashboard/src/api.ts`**

Append to the end of `dashboard/src/api.ts`:

```typescript
export interface TrainingStatus {
  last_trained_at: string | null
  active_models: number
  settled_last_24h: number
  unmodeled_markets: number
}

export const fetchTrainingStatus = (): Promise<TrainingStatus> =>
  apiFetch<TrainingStatus>('/stats/training')
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd /workspace/crypto_analysis/dashboard
npm run build 2>&1 | tail -20
```

Expected: build succeeds with no TypeScript errors. (`npm run build` uses `tsc --noEmit` + Vite.)

- [ ] **Step 3: Commit**

```bash
cd /workspace/crypto_analysis
git add dashboard/src/api.ts
git commit -m "feat: add TrainingStatus type and fetchTrainingStatus to API client"
```

---

### Task 6: Add Training card to System view

**Files:**
- Modify: `dashboard/src/views/System.module.css`
- Modify: `dashboard/src/views/System.tsx`

- [ ] **Step 1: Update `System.module.css` to a 4-column grid**

Replace the entire content of `dashboard/src/views/System.module.css`:

```css
.cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}

.serviceCard {
  background: var(--bg-card);
  border-radius: 6px;
  padding: 20px;
}

.cardHeader {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.dot-green { background: var(--green); }
.dot-red   { background: var(--red); }

.cardTitle {
  font-size: 13px;
  font-weight: 600;
}

.cardRow {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--text-muted);
  padding: 3px 0;
}

.deployCard {
  background: var(--bg-card);
  border-radius: 6px;
  padding: 20px;
  grid-column: span 4;
}
```

Changes from old version: `repeat(3, 1fr)` → `repeat(4, 1fr)`, `span 3` → `span 4`.

- [ ] **Step 2: Replace `dashboard/src/views/System.tsx` with the version that includes Training**

```tsx
import { useCallback, useEffect, useState } from 'react'
import { useRefreshContext } from '../App'
import { fetchHealth, fetchSlot, fetchTrainingStatus, HealthResponse, TrainingStatus } from '../api'
import { useAutoRefresh } from '../hooks/useAutoRefresh'
import styles from './System.module.css'

interface ServiceCard {
  name: string
  port: number
  isUp: boolean
  rows: { label: string; value: string }[]
}

function buildCards(health: HealthResponse | null, apiError: boolean): ServiceCard[] {
  const active = health?.active_markets ?? 0
  const stale = health?.stale_markets ?? 0
  return [
    {
      name: 'Ingestor',
      port: 8001,
      isUp: !apiError && active > 0,
      rows: [
        { label: 'Active Markets', value: String(active) },
        { label: 'Stale Markets', value: String(stale) },
      ],
    },
    {
      name: 'Predictor',
      port: 8002,
      isUp: !apiError && active > 0,
      rows: [
        { label: 'Active Markets', value: String(active) },
        { label: 'Stale Markets', value: String(stale) },
      ],
    },
    {
      name: 'API',
      port: 8000,
      isUp: !apiError && health?.status === 'ok',
      rows: [
        { label: 'Status', value: apiError ? 'ERROR' : (health?.status ?? '—') },
        { label: 'Active Markets', value: String(active) },
        { label: 'Stale Markets', value: String(stale) },
      ],
    },
  ]
}

function formatTrainedAt(iso: string | null): string {
  if (!iso) return 'Never'
  const d = new Date(iso)
  return d.toLocaleString()
}

export default function System({ intervalMs }: { intervalMs: number }) {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [apiError, setApiError] = useState(false)
  const [slot, setSlot] = useState<string>('—')
  const [training, setTraining] = useState<TrainingStatus | null>(null)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const ctx = useRefreshContext()

  const load = useCallback(async () => {
    try {
      const [healthResult, slotResult, trainingResult] = await Promise.allSettled([
        fetchHealth(),
        fetchSlot(),
        fetchTrainingStatus(),
      ])
      if (healthResult.status === 'fulfilled') {
        setHealth(healthResult.value)
        setApiError(false)
      } else {
        setHealth(null)
        setApiError(true)
      }
      if (slotResult.status === 'fulfilled') setSlot(slotResult.value.slot)
      if (trainingResult.status === 'fulfilled') setTraining(trainingResult.value)
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

  const cards = buildCards(health, apiError)

  return (
    <div>
      {fetchError && <div className="error-banner">{fetchError}</div>}

      <div className={styles.cards}>
        {cards.map(svc => (
          <div key={svc.name} className={styles.serviceCard}>
            <div className={styles.cardHeader}>
              <div className={`${styles.dot} ${svc.isUp ? styles['dot-green'] : styles['dot-red']}`} />
              <span className={styles.cardTitle}>{svc.name}</span>
            </div>
            <div className={styles.cardRow}>
              <span>Port</span>
              <span>{svc.port}</span>
            </div>
            {svc.rows.map(r => (
              <div key={r.label} className={styles.cardRow}>
                <span>{r.label}</span>
                <span>{r.value}</span>
              </div>
            ))}
          </div>
        ))}

        <div className={styles.serviceCard}>
          <div className={styles.cardHeader}>
            <span className={styles.cardTitle}>Training</span>
          </div>
          <div className={styles.cardRow}>
            <span>Last Trained</span>
            <span>{formatTrainedAt(training?.last_trained_at ?? null)}</span>
          </div>
          <div className={styles.cardRow}>
            <span>Active Models</span>
            <span>{training?.active_models ?? '—'}</span>
          </div>
          <div className={styles.cardRow}>
            <span>Settled 24h</span>
            <span>{training?.settled_last_24h ?? '—'}</span>
          </div>
          <div className={styles.cardRow}>
            <span>Unmodeled</span>
            <span style={(training?.unmodeled_markets ?? 0) > 0 ? { color: 'var(--red)' } : {}}>
              {training?.unmodeled_markets ?? '—'}
            </span>
          </div>
        </div>

        <div className={`${styles.serviceCard} ${styles.deployCard}`}>
          <div className={styles.cardHeader}>
            <span className={styles.cardTitle}>Deployment</span>
          </div>
          <div className={styles.cardRow}>
            <span>Active Slot</span>
            <span style={{ fontWeight: 700, textTransform: 'uppercase', color: 'var(--accent)' }}>
              {slot}
            </span>
          </div>
          <div className={styles.cardRow}>
            <span>Last Checked</span>
            <span>{lastRefreshed?.toLocaleString() ?? '—'}</span>
          </div>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd /workspace/crypto_analysis/dashboard
npm run build 2>&1 | tail -20
```

Expected: build succeeds with no TypeScript errors.

- [ ] **Step 4: Commit**

```bash
cd /workspace/crypto_analysis
git add dashboard/src/views/System.tsx dashboard/src/views/System.module.css
git commit -m "feat: add Training card to System view with settlement and model stats"
```

---

### Task 7: Add trainer service to docker-compose

**Files:**
- Modify: `trainer/Dockerfile` (add missing `COPY predictor/`)
- Modify: `docker-compose.blue.yml`
- Modify: `docker-compose.green.yml`

**Background:** `trainer/Dockerfile` already exists but is missing `COPY predictor/ predictor/`,
which the trainer needs because `trainer/dataset.py` imports
`from predictor.feature_builder import build_feature_vector`. Fix the Dockerfile first,
then add the service to both compose stacks.

- [ ] **Step 1: Fix `trainer/Dockerfile` to include the predictor module**

Replace the entire content of `trainer/Dockerfile`:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY trainer/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ shared/
COPY alembic/ alembic/
COPY predictor/ predictor/
COPY trainer/ trainer/
COPY .env .env
CMD ["python", "-m", "trainer.main"]
```

The only change from the existing file is adding `COPY predictor/ predictor/` before `COPY trainer/ trainer/`.

- [ ] **Step 2: Add `trainer-blue` service to `docker-compose.blue.yml`**

In `docker-compose.blue.yml`, add the following service block after the `ingestor-blue` service (before `predictor-blue`):

```yaml
  trainer-blue:
    build:
      context: .
      dockerfile: trainer/Dockerfile
    container_name: blue-trainer
    environment:
      <<: *slot
      POSTGRES_HOST: postgres
    env_file: .env
    depends_on:
      migrate-blue:
        condition: service_completed_successfully
    volumes:
      - models:/app/models
    networks:
      - infra
```

Note: `volumes: models:/app/models` is needed so `train_and_promote` can write `.joblib` files to the shared volume that the predictor reads from.

- [ ] **Step 3: Add `trainer-green` service to `docker-compose.green.yml`**

In `docker-compose.green.yml`, add the following service block after the `ingestor-green` service (before `predictor-green`):

```yaml
  trainer-green:
    build:
      context: .
      dockerfile: trainer/Dockerfile
    container_name: green-trainer
    environment:
      <<: *slot
      POSTGRES_HOST: postgres
    env_file: .env
    depends_on:
      migrate-green:
        condition: service_completed_successfully
    volumes:
      - models:/app/models
    networks:
      - infra
```

- [ ] **Step 3: Validate compose file syntax**

```bash
docker compose -f /workspace/crypto_analysis/docker-compose.blue.yml config --quiet
docker compose -f /workspace/crypto_analysis/docker-compose.green.yml config --quiet
```

Expected: no output (silent = valid). If there are YAML syntax errors, they will be printed.

- [ ] **Step 4: Run the full test suite one final time**

```bash
cd /workspace/crypto_analysis
pytest --tb=short -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.blue.yml docker-compose.green.yml
git commit -m "feat: add trainer daemon service to blue and green compose stacks"
```

---

## Deployment

After merging, redeploy with:

```bash
./scripts/deploy.sh blue   # or green, whichever is the inactive slot
```

The trainer container will start after migrations complete and begin its first campaign immediately. Check its log output:

```bash
docker logs blue-trainer --follow
```

Expected log lines (after 5+ minutes of running):
```
Trainer daemon starting — cooldown=600s
Backfilled N outcomes before training
Starting training campaign for M markets
...
Campaign done — sleeping 600s
```

If you see `Insufficient settled predictions for ... (0)` on first run, that's normal — it means no predictions have been settled yet. Give the ingestor 30+ minutes to collect data and the system a few market closes to accumulate settled outcomes.
