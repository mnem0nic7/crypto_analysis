# Kalshi Crypto 15-Minute Prediction Module — Design Spec

**Date:** 2026-05-14
**Status:** Approved

---

## Overview

A self-contained prediction module that outputs binary UP/DOWN signals with confidence scores for active Kalshi crypto markets. Designed as a component within a larger trading system (credentials and env vars already established in `.env`). The module is not a standalone trading system — it produces predictions that the broader engine will consume.

**Core question answered:** In the next 15 minutes, will this crypto asset be higher or lower at Kalshi contract settlement?

---

## Architecture

Three microservices communicating via shared PostgreSQL:

```
Ingestor ──▶ Postgres ◀── Predictor ──▶ Postgres ◀── API Service
```

### Ingestor Service
- Discovers all active Kalshi crypto markets at startup; refreshes periodically
- Polls Coinbase Advanced Trade every 30s for OHLCV candles and order book depth
- Polls Kalshi API every 30s for contract yes/no prices and volume
- Computes derived fields (momentum, volatility) on insert
- Writes to `raw_features` table
- Marks markets as `stale` in `markets` table if no data for `RISK_STALE_MARKET_SECONDS` (60s)

### Predictor Service
- On startup: loads `is_active` model per market from `model_registry`
- Every 60s: builds feature vector from latest `raw_features`, runs XGBoost inference, writes to `predictions`
- Every 5 minutes: checks `model_registry` for newer active models and hot-reloads without restart
- Skips inference for stale markets (logs reason)
- Emits `low_confidence: true` for markets with insufficient training history

### API Service (FastAPI)
- Read-only; no auth required (internal network assumed for v1)
- Three endpoints (see API section)

### Training (Offline)
- Separate script triggered by `TRAINING_CAMPAIGN_*` env vars
- Reads `raw_features` joined with settled `predictions`
- Trains one XGBoost binary classifier per market
- New model replaces `is_active` only if Brier score improves
- Artifacts stored in `/models` directory, registered in `model_registry`

---

## Data Model

### `markets`
```sql
market_id     TEXT PRIMARY KEY,
ticker        TEXT NOT NULL,
title         TEXT,
close_time    TIMESTAMPTZ,
status        TEXT,          -- active | stale | closed
discovered_at TIMESTAMPTZ NOT NULL,
updated_at    TIMESTAMPTZ NOT NULL
```

### `raw_features`
```sql
id               BIGSERIAL PRIMARY KEY,
market_id        TEXT REFERENCES markets(market_id),
ts               TIMESTAMPTZ NOT NULL,
-- Coinbase price
price_open       NUMERIC,
price_high       NUMERIC,
price_low        NUMERIC,
price_close      NUMERIC,
volume           NUMERIC,
-- Order book
bid_depth_1pct   NUMERIC,
ask_depth_1pct   NUMERIC,
book_imbalance   NUMERIC,    -- (bid - ask) / (bid + ask), range [-1, 1]
-- Kalshi contract
kalshi_yes_price NUMERIC,    -- 0.0–1.0 implied probability
kalshi_no_price  NUMERIC,
kalshi_volume    NUMERIC,
-- Derived (computed on insert)
price_momentum_1m  NUMERIC,
price_momentum_5m  NUMERIC,
price_momentum_15m NUMERIC,
volatility_5m      NUMERIC
```
Index on `(market_id, ts DESC)`.

### `predictions`
```sql
id                  BIGSERIAL PRIMARY KEY,
market_id           TEXT REFERENCES markets(market_id),
ts                  TIMESTAMPTZ NOT NULL,
direction           TEXT NOT NULL,     -- UP | DOWN
confidence          NUMERIC NOT NULL,  -- 0.0–1.0
low_confidence      BOOLEAN NOT NULL DEFAULT false,
model_version       TEXT NOT NULL,
feature_snapshot_id BIGINT REFERENCES raw_features(id),
settled_at          TIMESTAMPTZ,
actual_outcome      SMALLINT           -- 1=UP, 0=DOWN, NULL=unsettled
```

### `model_registry`
```sql
id            BIGSERIAL PRIMARY KEY,
market_id     TEXT REFERENCES markets(market_id),
version       TEXT NOT NULL,
trained_at    TIMESTAMPTZ NOT NULL,
training_rows INTEGER NOT NULL,
brier_score   NUMERIC NOT NULL,
artifact_path TEXT NOT NULL,
is_active     BOOLEAN NOT NULL DEFAULT false
```
Constraint: at most one `is_active = true` per `market_id`.

---

## Feature Engineering

~25-dimensional tabular feature vector built from the latest N rows of `raw_features`:

**Price momentum**
- Returns over 1m, 5m, 15m lookback windows
- Rate-of-change of volatility
- Distance from 15m VWAP

**Order book pressure**
- `book_imbalance` (−1 to +1)
- Imbalance trend over last 5 snapshots

**Kalshi market signal**
- `kalshi_yes_price` as implied probability
- Deviation of Kalshi implied prob from momentum-based fair value
- Kalshi volume spike indicator (z-score vs. recent baseline)

**Time features**
- Minutes-to-settlement (most predictive near expiry)
- Hour-of-day (0–23)
- Day-of-week (0–6)

Training labels: `actual_outcome` from settled `predictions` (1 = UP, 0 = DOWN).

---

## Model Training & Serving

**Training controls (from `.env`):**
```
TRAINING_CAMPAIGN_ENABLED           gate — must be true to run
TRAINING_CAMPAIGN_LOOKBACK_HOURS    rows to pull per market
TRAINING_CAMPAIGN_COOLDOWN_SECONDS  min gap between retraining runs
TRAINING_CAMPAIGN_MAX_RECENT_PER_MARKET  min rows required to train
```

**Promotion rule:** New model artifact becomes `is_active = true` only if `new_brier_score < current_brier_score` (lower Brier score = better calibration). Otherwise rejected silently; comparison logged for audit.

**Hot-reload:** Predictor checks `model_registry` every 5 minutes. Loads newer `is_active` models without process restart.

**New market bootstrap:** Markets with fewer than `TRAINING_CAMPAIGN_MAX_RECENT_PER_MARKET` settled predictions emit `low_confidence: true` until a model is trained.

---

## REST API

### `GET /markets`
Lists all active markets.
```json
[{
  "market_id": "BTCUSD-15M-20260514T1815",
  "ticker": "BTC",
  "close_time": "2026-05-14T18:15:00Z",
  "minutes_to_close": 7
}]
```

### `GET /predict/{market_id}`
Latest prediction for a market.
```json
{
  "market_id": "BTCUSD-15M-20260514T1815",
  "direction": "UP",
  "confidence": 0.73,
  "low_confidence": false,
  "model_version": "v4",
  "ts": "2026-05-14T18:08:12Z",
  "feature_age_seconds": 31
}
```
Returns `feature_age_seconds` for consumer-side staleness enforcement.

### `GET /history/{market_id}`
Settled predictions with outcomes for backtesting.
```json
[{
  "ts": "2026-05-14T16:00:12Z",
  "direction": "UP",
  "confidence": 0.71,
  "actual_outcome": 1,
  "correct": true
}]
```
Query params: `?limit=100&from=<iso_timestamp>`

---

## Error Handling

| Failure | Behavior |
|---------|----------|
| Coinbase/Kalshi API error | Log warning, skip cycle, continue |
| No ingest for 60s | Mark market `stale`, Predictor skips inference |
| Missing model for market | `low_confidence: true`, no crash |
| Inference exception | Log error + market_id, skip that market, continue |
| New model Brier worse | Reject, keep old model, log comparison |
| Insufficient training rows | Skip market training, log reason |

## Observability

- Structured JSON logs from all services
- `GET /health` on each service:
```json
{ "status": "ok", "active_markets": 12, "stale_markets": 0, "last_ingest_age_seconds": 28 }
```
- `predictions.actual_outcome` backfill provides built-in accuracy audit trail

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.11+ |
| ML | XGBoost + scikit-learn (feature pipeline) |
| API | FastAPI + uvicorn |
| Database | PostgreSQL (existing, `POSTGRES_PASSWORD` in `.env`) |
| Containerization | Docker + Docker Compose (one container per service) |
| Kalshi auth | RSA private key (paths in `.env`) |
| Coinbase auth | CDP API key (`cdp_api_key.json`, already present) |

---

## Out of Scope (v1)

- LLM-assisted predictions (env vars present for future use)
- Order placement (consumed by the trading engine, not this module)
- Authentication on the REST API (internal network assumed)
- Sentiment / on-chain data sources
