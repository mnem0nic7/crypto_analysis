# Kalshi Crypto Prediction Platform

Automated 15-minute UP/DOWN price prediction for Kalshi crypto contracts, with continuous outcome settlement, model retraining, parameter sweep analysis, and a live operator dashboard.

Live at: **da.ai-al.site** (requires Google authentication)

---

## Architecture

```
Kalshi API ──┐                        ┌── Predictor (60s inference)
Coinbase API ─┤──► Ingestor (30s) ───►├── Postgres ◄── Analysis (on-demand)
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
| **api** | FastAPI serving predictions, history, stats, and analysis sweeps |
| **dashboard** | React 18 + Vite + Recharts SPA |

`analysis/` is not a daemon — it is an on-demand module imported by the API and executed in a background thread when a sweep is triggered from the dashboard.

---

## Data Model

**`markets`** — Kalshi contract metadata (market_id PK, ticker, close_time, status: active/stale/closed)

**`raw_features`** — 30s snapshots per market: Coinbase OHLCV, order book imbalance, Kalshi yes/no price, derived momentum/volatility columns

**`predictions`** — One row per inference cycle: direction (UP/DOWN), confidence (0–1), model_version, settled_at, actual_outcome (1/0/NULL)

**`model_registry`** — Trained model metadata: version, brier_score, training_rows, artifact_path, is_active

**`sweep_runs`** — One row per parameter sweep execution: status, fee assumption, n_combinations_evaluated, elapsed_seconds, best_net_pnl_dollars

**`sweep_results`** — Top-500 results and per-knob marginals for each sweep run

---

## API Endpoints

All routes are accessed via `/api/` in the browser (nginx strips the prefix before forwarding to FastAPI on port 8000).

### Core

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Active/stale market counts |
| GET | `/slot` | Blue or green (deployment identity) |
| GET | `/markets` | Active markets with minutes-to-close |
| GET | `/predict/{market_id}` | Latest prediction + feature age |
| GET | `/history/{market_id}` | Settled predictions for one market |
| GET | `/history/series/{series_ticker}` | Settled predictions for a whole series |

### Stats

| Method | Path | Description |
|--------|------|-------------|
| GET | `/stats/summary` | Overall + per-market accuracy |
| GET | `/stats/models` | Active model registry entries |
| GET | `/stats/training` | Last trained-at, active models, settled 24h, unmodeled markets |

### Auth

| Method | Path | Description |
|--------|------|-------------|
| GET | `/auth/login` | Redirect to Google OAuth consent screen |
| GET | `/auth/callback` | Exchange auth code, set session cookie, redirect to `/` |
| GET | `/auth/me` | 200 + `{"email": "..."}` if authenticated, 401 otherwise |
| POST | `/auth/logout` | Clear session cookie |

### Data Explorer

| Method | Path | Description |
|--------|------|-------------|
| GET | `/data/raw-features` | Paginated raw feature rows (filter by series, date range) |
| GET | `/data/feature-vectors` | Prediction + feature vectors for ML inspection |
| GET | `/data/stats` | Column statistics: mean, std, min, max, null count |
| GET | `/data/correlations` | Feature-to-outcome Pearson correlations |
| GET | `/data/feature-importance` | XGBoost feature importance for a series |

### Analysis

| Method | Path | Description |
|--------|------|-------------|
| GET | `/analysis/runs` | All sweep runs (id, date, status, n_predictions, elapsed, best P&L) |
| GET | `/analysis/runs/latest` | Most recent completed run |
| GET | `/analysis/runs/{id}/results` | `?type=top_k` or `?type=marginal` |
| POST | `/analysis/runs` | Start a new sweep — returns immediately; sweep runs in background |

---

## Dashboard Views

- **Signals** — Live predictions per market with confidence and feature age
- **Accuracy** — Historical UP/DOWN calls charted against actual outcomes (series-level)
- **Models** — Active model registry: version, Brier score, training rows
- **System** — Health, deployment slot, training status
- **Data Explorer** — Browse raw features, feature vectors, column stats, correlations, feature importance
- **Analysis** — Parameter sweep: knob importance chart, per-knob response curves, top-500 results table

---

## Authentication

The dashboard is protected by Google OAuth 2.0. Only the configured email address (`m7.ga.77@gmail.com`, hardcoded in `api/main.py` as `_ALLOWED_EMAIL`) may log in. API endpoints are not authenticated.

### Flow

1. Unauthenticated users see a login page
2. "Sign in with Google" redirects to Google's consent screen
3. Google redirects to `/api/auth/callback` with an authorization code
4. The API exchanges the code, fetches the user email, checks it against `_ALLOWED_EMAIL`
5. On match: a signed HTTP-only JWT cookie (`session`, 30-day expiry) is set; user is redirected to `/`

### Setup

1. In Google Cloud Console, create an **OAuth 2.0 Web Application** credential
2. Add `https://<DOMAIN>/api/auth/callback` as an authorized redirect URI
3. Copy client ID and secret into `.env` as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`
4. Generate the signing key: `openssl rand -hex 32` → set as `AUTH_SECRET_KEY` in `.env`

---

## Analysis / Parameter Sweep

The Analysis tab runs a full-grid parameter sweep over CRYPTO_15M strategy knobs to find the combination that maximises net P&L on all historical settled predictions.

### What it sweeps

10 knobs × ~5 values each = **7,500,000** combinations per run, evaluated using numpy boolean masking.

| Knob | Values |
|------|--------|
| `min_fee_adjusted_edge_bps` | 250, 500, 750, 1000, 1500, 2000 |
| `max_spread_bps` | 100, 250, 500, 750, 1000 |
| `min_confidence` | 0.60, 0.70, 0.80, 0.90 |
| `min_contract_price_dollars` | 0.05, 0.10, 0.25, 0.50, 0.75 |
| `crypto_live_min_market_age_seconds` | 0, 60, 180, 300, 600 |
| `crypto_autonomy_min_seconds_to_close` | 0, 60, 120, 180, 300 |
| `crypto_taker_fallback_close_seconds` | 0, 30, 60, 90, 180 |
| `crypto_market_price_anchor_weight` | 0.00, 0.25, 0.50, 0.75, 1.00 |
| `crypto_late_sure_thing_min_probability` | 0.80, 0.85, 0.90, 0.95 |
| `crypto_late_sure_thing_min_market_probability` | 0.60, 0.70, 0.75, 0.80, 0.90 |

Fee assumption: **50 bps** per contract (stored on each run record so future runs can vary it).

### Outputs

- **Top-500** combinations by net P&L — stored as `result_type = 'top_k'` in `sweep_results`
- **Per-knob marginals** — mean P&L at each knob value, marginalised over all other knobs — stored as `result_type = 'marginal'`; drives the knob importance bar chart

### Triggering a sweep

Click **Run sweep now** in the Analysis tab. The POST to `/analysis/runs` returns immediately with `status: "running"`. The sweep executes in a background thread (~215s on 5,000 predictions). Refresh the page to see the completed run.

Results persist in `sweep_runs` + `sweep_results` across deployments.

---

## Continuous Learning Loop

1. **Ingestor** calls `backfill_outcomes()` every 30s — finds predictions whose `settled_at <= now` and `actual_outcome IS NULL`, looks up `price_close` before/after via `raw_features`, writes 1 (UP) or 0 (DOWN)

2. **Trainer** daemon wakes every 10min (configurable via `TRAINING_CAMPAIGN_COOLDOWN_SECONDS`), calls `backfill_outcomes()` for safety, then `train_and_promote()` for each active market

3. **Predictor** reloads models from disk every 5min — picks up newly promoted models automatically

---

## Deployment

### Prerequisites

```bash
cp .env.example .env          # fill in all credentials (see Settings section below)
# Place Kalshi-1.txt, Kalshi-2-Demo.txt in project root
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

### Watching logs

```bash
docker logs green-trainer --follow
docker logs green-api --follow
docker logs green-ingestor --follow
```

---

## Settings (`.env`)

See `.env.example` for all variables. Key ones:

| Variable | Default | Description |
|----------|---------|-------------|
| `KALSHI_ENV` | `live` | `demo` or `live` |
| `POSTGRES_HOST` | `postgres` | Set to `postgres` inside Docker |
| `RISK_STALE_MARKET_SECONDS` | `60` | Seconds after close_time before a market is marked stale |
| `TRAINING_CAMPAIGN_COOLDOWN_SECONDS` | `600` | Trainer sleep between campaigns |
| `TRAINING_CAMPAIGN_LOOKBACK_HOURS` | `2160` | Dataset window for training (2160 = 90 days) |
| `GOOGLE_CLIENT_ID` | — | Google OAuth 2.0 client ID |
| `GOOGLE_CLIENT_SECRET` | — | Google OAuth 2.0 client secret |
| `AUTH_SECRET_KEY` | — | 256-bit hex JWT signing key (`openssl rand -hex 32`) |

---

## Development

```bash
# Run tests (SQLite in-memory, no Docker needed)
pytest

# Type-check dashboard
cd dashboard && npm run build
```

Tests use a SQLite in-memory database via a `db_session` fixture in `conftest.py`. SQLAlchemy handles tz-aware datetimes correctly across both Postgres (TIMESTAMPTZ) and SQLite (ISO string coercion).
