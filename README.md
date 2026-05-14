# Kalshi Crypto Prediction Platform

Automated 15-minute UP/DOWN price prediction for Kalshi crypto contracts, with continuous outcome settlement, model retraining, and a live operator dashboard.

Live at: **da.ai-al.site**

---

## Architecture

```
Kalshi API ──┐                        ┌── Predictor (60s inference)
Coinbase API ─┤──► Ingestor (30s) ───►├── Postgres
              │    └─ backfill (30s)   └── API (FastAPI) ──► Dashboard (React)
              │                        
              └────────────────────► Trainer (10min retrain)
                                        └─ backfill (safety)
```

Five Docker services per slot, deployed in blue-green pairs:

| Service | Responsibility |
|---------|---------------|
| **ingestor** | Polls Kalshi + Coinbase every 30s, writes `raw_features`, settles outcomes |
| **predictor** | Runs XGBoost inference every 60s, reloads models every 5min |
| **trainer** | Daemon — backfills outcomes then retrains all active markets every 10min |
| **api** | FastAPI serving predictions, history, stats |
| **dashboard** | React 18 + Vite + Recharts SPA |

---

## Data Model

**`markets`** — Kalshi contract metadata (market_id PK, ticker, close_time, status: active/stale/closed)

**`raw_features`** — 30s snapshots per market: Coinbase OHLCV, order book imbalance, Kalshi yes/no price, derived momentum/volatility columns

**`predictions`** — One row per inference cycle: direction (UP/DOWN), confidence (0–1), model_version, settled_at, actual_outcome (1/0/NULL)

**`model_registry`** — Trained model metadata: version, brier_score, training_rows, artifact_path, is_active

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Active/stale market counts |
| GET | `/markets` | Active markets with minutes-to-close |
| GET | `/predict/{market_id}` | Latest prediction + feature age |
| GET | `/history/{market_id}` | Settled predictions with outcomes |
| GET | `/stats/summary` | Overall + per-market accuracy |
| GET | `/stats/models` | Active model registry entries |
| GET | `/stats/training` | Last trained-at, active models, settled 24h, unmodeled markets |
| GET | `/slot` | Blue or green (deployment identity) |

---

## Continuous Learning Loop

1. **Ingestor** calls `backfill_outcomes()` every 30s — finds predictions whose `settled_at <= now` and `actual_outcome IS NULL`, looks up `price_close` before/after via `raw_features`, writes 1 (UP) or 0 (DOWN)

2. **Trainer** daemon wakes every 10min (configurable via `TRAINING_CAMPAIGN_COOLDOWN_SECONDS`), calls `backfill_outcomes()` for safety, then `train_and_promote()` for each active market

3. **Predictor** reloads models from disk every 5min — picks up newly promoted models automatically

---

## Dashboard Views

- **Signals** — Live predictions per market with confidence and feature age
- **Accuracy** — Historical UP/DOWN calls charted against actual outcomes
- **Models** — Active model registry: version, Brier score, training rows
- **System** — Health, deployment slot, training status (last trained, active models, settled 24h, unmodeled markets)

---

## Deployment

### Prerequisites

```bash
cp .env.example .env          # fill in Kalshi + Coinbase + Postgres credentials
# Place Kalshi-1.txt, Kalshi-2-Demo.txt, cdp_api_key.json in project root
docker compose -f docker-compose.infra.yml up -d   # postgres + caddy network
```

### Blue-green deploy

```bash
./scripts/deploy.sh green     # builds green, health-checks, switches Caddy, stops blue
# or
./scripts/deploy.sh blue
```

The script:
1. Builds all images for the target slot
2. Runs `migrate-{slot}` (Alembic `upgrade head`), waits for completion
3. Starts all 5 services
4. Polls `/health` and dashboard root until both return 200 (120s timeout)
5. Rewrites Caddy config to point `da.ai-al.site` at the new slot
6. Tears down the old slot

### Watching the trainer

```bash
docker logs green-trainer --follow
```

---

## Settings (`.env`)

See `.env.example` for all variables. Key ones:

| Variable | Default | Description |
|----------|---------|-------------|
| `KALSHI_ENV` | `demo` | `demo` or `live` |
| `TRAINING_CAMPAIGN_COOLDOWN_SECONDS` | `600` | Trainer sleep between campaigns |
| `TRAINING_CAMPAIGN_LOOKBACK_HOURS` | `24` | Dataset window for training |
| `POSTGRES_HOST` | `postgres` | Set to `postgres` inside Docker |

---

## Development

```bash
# Run tests (SQLite in-memory, no Docker needed)
pytest

# Type-check dashboard
cd dashboard && npm run build
```

Tests use a SQLite in-memory database via a `db_session` fixture in `conftest.py`. SQLAlchemy handles tz-aware datetimes correctly across both Postgres (TIMESTAMPTZ) and SQLite (ISO string coercion).
