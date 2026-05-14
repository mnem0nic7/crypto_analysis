# Continuous Learning Loop Design

**Date:** 2026-05-14

## Problem

The prediction platform collects data and produces predictions but never closes the feedback loop. In production:

- `backfill_outcomes()` only runs when `TRAINING_CAMPAIGN_ENABLED=true` and the trainer container is invoked — neither is done automatically
- `trainer/main.py` runs once and exits; there is no daemon loop
- `actual_outcome` is always NULL on every prediction row
- `/stats/summary` always returns `total_settled: 0`
- The Accuracy dashboard view is permanently empty
- Models are never retrained after initial deployment

There is also a Postgres timezone bug in `trainer/backfill.py`: `_strip_tz()` strips timezone info for SQLite compat, but the production Postgres schema declares `settled_at` as `TIMESTAMP(timezone=True)`. Comparing a naive Python datetime against a tz-aware Postgres column will silently return 0 rows (or raise a type error in strict mode).

## Goal

Make the platform self-sustaining: outcomes are settled automatically, models retrain on a schedule, and the dashboard surfaces training status.

## Architecture

Three small, targeted changes to three existing services, plus one new API endpoint and a dashboard update:

1. **Ingestor settlement** — `backfill_outcomes()` added to the ingestor loop. Runs every 30 s (the existing poll interval). The ingestor already manages market lifecycle and has a DB session, making it the natural home for settlement.

2. **Trainer daemon** — `trainer/main.py` gains a `while True` loop using the existing `training_campaign_cooldown_seconds` setting (default 600 s = 10 min). The loop calls `run_training_campaign()` unconditionally (the `TRAINING_CAMPAIGN_ENABLED` guard is removed — that setting is now irrelevant once the loop is always-on; the trainer container is simply not started if not wanted).

3. **Postgres tz fix** — `backfill.py` drops `_strip_tz()` and uses tz-aware datetimes throughout. The SQLite compat workaround is no longer needed (SQLite is only used in tests via SQLAlchemy's `DateTime` type which handles tz correctly via text representation).

4. **API `/stats/training` endpoint** — Returns training metadata derived from `model_registry` and `predictions` tables: last trained-at, total active models, outcomes settled in the last 24 h, and whether any markets lack a model.

5. **System view update** — Adds a "Training" card to the System view showing the four stats above, making the learning loop visible to operators.

## Components

### `trainer/backfill.py` — fix Postgres tz

Remove `_strip_tz()`. Use `datetime.now(timezone.utc)` (tz-aware) directly in queries. SQLAlchemy handles tz-aware datetimes correctly against `TIMESTAMP(timezone=True)` columns in Postgres; SQLite test sessions work because SQLAlchemy coerces to ISO string on insert/select.

Before:
```python
now = _strip_tz(datetime.now(timezone.utc))
unsettled = session.query(Prediction).filter(
    Prediction.settled_at <= now, ...
)
```

After:
```python
now = datetime.now(timezone.utc)
unsettled = session.query(Prediction).filter(
    Prediction.settled_at <= now, ...
)
```

The `_price_at` helper also strips tz from the `ts` parameter — fix the same way.

### `ingestor/main.py` — settlement in loop

Import `backfill_outcomes` from `trainer.backfill`. Call it at the top of each `_ingest_loop()` iteration, before market discovery. This ensures every 30 s any newly-closed markets get their prediction outcomes resolved before the next inference cycle.

```python
from trainer.backfill import backfill_outcomes

def _ingest_loop():
    while True:
        with session_scope(session_factory) as session:
            backfilled = backfill_outcomes(session)
            if backfilled:
                logger.info("Settled %d outcomes", backfilled)
            if time.time() - _last_discovery > _DISCOVERY_INTERVAL:
                _run_discovery(session)
            _mark_stale(session)
            ...
        time.sleep(_POLL_INTERVAL)
```

### `trainer/main.py` — daemon loop

Replace the one-shot guard with a `while True` loop. Use the existing `training_campaign_cooldown_seconds` setting. The trainer container must now be kept running (not `restart: "no"`).

```python
def main():
    settings = Settings()
    session_factory = make_session_factory(settings)
    logger.info("Trainer daemon starting — cooldown=%ds", settings.training_campaign_cooldown_seconds)
    while True:
        run_training_campaign(settings, session_factory)
        logger.info("Campaign done — sleeping %ds", settings.training_campaign_cooldown_seconds)
        time.sleep(settings.training_campaign_cooldown_seconds)
```

`run_training_campaign()` drops the `TRAINING_CAMPAIGN_ENABLED` check — the caller (the loop above) is responsible for running or not running the container.

### `api/main.py` — `/stats/training` endpoint

```python
@app.get("/stats/training")
def get_stats_training(session: Session = Depends(_get_db)):
    from shared.orm import ModelRegistry
    from sqlalchemy import func
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)

    # Last trained_at across all active models
    last_trained = session.query(func.max(ModelRegistry.trained_at)).scalar()

    # Total active models
    active_models = session.query(func.count(ModelRegistry.id)).filter(
        ModelRegistry.is_active == True
    ).scalar() or 0

    # Outcomes settled in last 24h
    settled_24h = session.query(func.count(Prediction.id)).filter(
        Prediction.actual_outcome != None,
        Prediction.ts >= cutoff_24h,
    ).scalar() or 0

    # Markets without an active model
    active_market_ids = [
        r[0] for r in session.query(Market.market_id).filter(Market.status == "active").all()
    ]
    modeled_ids = {
        r[0] for r in session.query(ModelRegistry.market_id).filter(
            ModelRegistry.is_active == True
        ).all()
    }
    unmodeled = [mid for mid in active_market_ids if mid not in modeled_ids]

    return {
        "last_trained_at": last_trained.isoformat() if last_trained else None,
        "active_models": active_models,
        "settled_last_24h": settled_24h,
        "unmodeled_markets": len(unmodeled),
    }
```

### `dashboard/src/api.ts` — add `TrainingStatus` type + fetch

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

### `dashboard/src/views/System.tsx` — Training card

Add a fourth card alongside Ingestor / Predictor / API / Deployment showing:
- Last Trained (formatted datetime or "Never")
- Active Models (count)
- Settled 24h (count)
- Unmodeled Markets (count, red if > 0)

### `docker-compose.blue.yml` / `docker-compose.green.yml` — add trainer service

Add a `trainer-{slot}` service using the ingestor Dockerfile (which already has the trainer module), with `TRAINING_CAMPAIGN_COOLDOWN_SECONDS` env var. Depends on `migrate-{slot}` completing.

```yaml
trainer-blue:
  build:
    context: .
    dockerfile: ingestor/Dockerfile
  container_name: blue-trainer
  command: python -m trainer.main
  environment:
    <<: *slot
    POSTGRES_HOST: postgres
  env_file: .env
  depends_on:
    migrate-blue:
      condition: service_completed_successfully
  networks:
    - infra
```

## Data Flow

```
[ingestor loop every 30s]
  └─ backfill_outcomes()  ← settles closed market predictions
  └─ fetch_and_write()    ← collects new raw_features

[trainer daemon every 10min]
  └─ backfill_outcomes()  ← redundant safety call, idempotent
  └─ train_and_promote()  ← for each market with enough data

[predictor loop every 60s]
  └─ reload models (every 5min)  ← picks up newly promoted models
  └─ run_inference()
```

## Error Handling

- `backfill_outcomes()` is already wrapped in `session_scope` (rolls back on exception). Adding it to ingestor doesn't change error semantics.
- Trainer loop catches exceptions per-market already (`train_and_promote` is wrapped in `try/except` in `run_training_campaign`).
- New API endpoint: if DB is unavailable, FastAPI's existing error middleware returns 500. No special handling needed.

## Testing

- `tests/test_api.py`: add 4 tests for `/stats/training` covering empty DB, settled predictions, active models, unmodeled markets.
- `tests/test_trainer.py`: update existing `test_backfill_outcomes_*` tests to use tz-aware datetimes (remove any `_strip_tz` usage in test fixtures).
- No new test files needed.

## What Changes, What Doesn't

**Changes:**
- `trainer/backfill.py` — tz fix (2 functions)
- `ingestor/main.py` — add `backfill_outcomes` call
- `trainer/main.py` — replace one-shot with daemon loop; drop `TRAINING_CAMPAIGN_ENABLED` guard
- `api/main.py` — add `/stats/training` endpoint
- `dashboard/src/api.ts` — add type + fetch
- `dashboard/src/views/System.tsx` — add Training card
- `docker-compose.blue.yml` + `docker-compose.green.yml` — add trainer service

**Does NOT change:**
- Database schema (no migration needed)
- `shared/orm.py` — no changes
- `predictor/` — no changes
- `trainer/train.py`, `trainer/dataset.py` — no changes
- `trainer/backfill.py`'s public interface — `backfill_outcomes(session)` signature unchanged

## Settings

No new settings. Existing settings are used:
- `training_campaign_cooldown_seconds` (default 600) — trainer sleep interval
- `training_campaign_lookback_hours` (default 24) — dataset window
- `training_campaign_max_recent_per_market` (default 5) — note: currently unused in code; leave as-is (YAGNI)

`TRAINING_CAMPAIGN_ENABLED` is deprecated. The trainer container simply isn't deployed if training isn't wanted.
