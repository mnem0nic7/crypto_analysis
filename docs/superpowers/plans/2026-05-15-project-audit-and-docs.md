# Project Audit & Documentation Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Synchronise all written documentation with the running system and remove one dead setting from `shared/settings.py`.

**Architecture:** Three independent tasks — README overhaul, `.env.example` gap-fill, and dead-code removal — each committed separately. No runtime behaviour changes. Task 3 includes a regression test to prevent the setting from returning.

**Tech Stack:** Markdown, bash, Python/pydantic-settings, pytest.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `README.md` | Add Analysis, Data Explorer, Auth sections; all 22 API routes; all Settings vars |
| Modify | `.env.example` | Add `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `AUTH_SECRET_KEY`, `RISK_STALE_MARKET_SECONDS` |
| Modify | `shared/settings.py` | Remove `training_campaign_enabled` dead field |
| Modify | `tests/test_settings.py` | Add regression test confirming the field is gone |

---

## Task 1: README Overhaul

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace README.md with the complete updated version**

Overwrite `README.md` entirely with the following content. Every section is shown in full — do not abbreviate.

```markdown
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
| GET | `/data/raw-features` | Paginated raw feature rows (query by series, date range) |
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
cp .env.example .env          # fill in all credentials (see Settings section)
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
```

- [ ] **Step 2: Verify the markdown renders correctly**

Open `README.md` and visually scan:
- All table columns align
- All code blocks are closed (no unclosed triple-backtick)
- Section headers are H2 (`##`) with H3 (`###`) subsections
- No placeholder text remains

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: overhaul README — add Analysis, Data Explorer, Auth, all 22 API routes"
```

Expected: commit succeeds, no unstaged changes.

---

## Task 2: `.env.example` Gap-Fill

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add missing variables to `.env.example`**

Open `.env.example`. It currently ends after `TRAINING_CAMPAIGN_LOOKBACK_HOURS=2160`. Apply these two additions:

**Add `RISK_STALE_MARKET_SECONDS` under the risk comment** (it exists in `Settings` but was never in the example):

The current file has this section:
```
# Training campaign
TRAINING_CAMPAIGN_COOLDOWN_SECONDS=600
TRAINING_CAMPAIGN_LOOKBACK_HOURS=2160
```

Add `RISK_STALE_MARKET_SECONDS=60` before it:
```
# Risk / ingestor
RISK_STALE_MARKET_SECONDS=60

# Training campaign
TRAINING_CAMPAIGN_COOLDOWN_SECONDS=600
TRAINING_CAMPAIGN_LOOKBACK_HOURS=2160
```

**Append an Auth section at the end of the file:**
```
# Auth (Google OAuth — dashboard login)
# Create an OAuth 2.0 Web Application in Google Cloud Console.
# Set authorized redirect URI to: https://<DOMAIN>/api/auth/callback
GOOGLE_CLIENT_ID=your-google-oauth-client-id
GOOGLE_CLIENT_SECRET=your-google-oauth-client-secret
# Generate with: openssl rand -hex 32
AUTH_SECRET_KEY=generate-with-openssl-rand-hex-32
```

The final `.env.example` should look like:

```
# Kalshi credentials
DEMO_KALSHI_API_KEY=your-demo-api-key-here
DEMO_KALSHI_READ_PRIVATE_KEY_PATH=Kalshi-2-Demo.txt
DEMO_KALSHI_WRITE_PRIVATE_KEY_PATH=Kalshi-2-Demo.txt
LIVE_KALSHI_API_KEY=your-live-api-key-here
LIVE_KALSHI_READ_PRIVATE_KEY_PATH=Kalshi-1.txt
KALSHI_ENV=live

# Postgres
POSTGRES_PASSWORD=changeme
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_USER=postgres
POSTGRES_DB=crypto_analysis

# Coinbase CDP
COINBASE_CDP_KEY_NAME=your-cdp-key-name
COINBASE_CDP_PRIVATE_KEY=your-cdp-private-key

# Risk / ingestor
RISK_STALE_MARKET_SECONDS=60

# Training campaign
TRAINING_CAMPAIGN_COOLDOWN_SECONDS=600
TRAINING_CAMPAIGN_LOOKBACK_HOURS=2160

# Auth (Google OAuth — dashboard login)
# Create an OAuth 2.0 Web Application in Google Cloud Console.
# Set authorized redirect URI to: https://<DOMAIN>/api/auth/callback
GOOGLE_CLIENT_ID=your-google-oauth-client-id
GOOGLE_CLIENT_SECRET=your-google-oauth-client-secret
# Generate with: openssl rand -hex 32
AUTH_SECRET_KEY=generate-with-openssl-rand-hex-32
```

- [ ] **Step 2: Verify no real credentials appear in the file**

```bash
grep -E "(GOCSPX|7c7b|changeme)" .env.example
```

Expected: empty output (no real secrets).

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs: add missing auth and risk vars to .env.example"
```

Expected: commit succeeds.

---

## Task 3: Remove Dead `training_campaign_enabled` Setting

**Files:**
- Modify: `shared/settings.py` — remove one field
- Modify: `tests/test_settings.py` — add regression test

- [ ] **Step 1: Write the regression test first**

Open `tests/test_settings.py`. Append this test at the bottom:

```python
def test_settings_has_no_training_campaign_enabled():
    """training_campaign_enabled was removed as dead code — ensure it stays gone."""
    assert "training_campaign_enabled" not in Settings.model_fields, (
        "training_campaign_enabled is dead code (never read in trainer/); do not re-add it"
    )
```

- [ ] **Step 2: Run the test to confirm it currently fails**

```bash
cd /workspace/crypto_analysis && python3 -m pytest tests/test_settings.py::test_settings_has_no_training_campaign_enabled -v
```

Expected output:
```
FAILED tests/test_settings.py::test_settings_has_no_training_campaign_enabled
AssertionError: training_campaign_enabled is dead code...
```

- [ ] **Step 3: Remove the dead field from `shared/settings.py`**

Open `shared/settings.py`. Delete this line (line 32):

```python
    training_campaign_enabled: bool = False
```

The `# Training campaign` section should now read:

```python
    # Training campaign
    training_campaign_lookback_hours: int = 2160
    training_campaign_cooldown_seconds: int = 600
    training_campaign_max_recent_per_market: int = 5
```

- [ ] **Step 4: Run the new test to confirm it now passes**

```bash
python3 -m pytest tests/test_settings.py::test_settings_has_no_training_campaign_enabled -v
```

Expected:
```
PASSED tests/test_settings.py::test_settings_has_no_training_campaign_enabled
```

- [ ] **Step 5: Run the full test suite to confirm no regressions**

```bash
python3 -m pytest --tb=short -q
```

Expected: all tests pass (same count as before — the existing settings tests do not reference `training_campaign_enabled`).

- [ ] **Step 6: Commit**

```bash
git add shared/settings.py tests/test_settings.py
git commit -m "refactor: remove dead training_campaign_enabled setting"
```

Expected: commit succeeds.

---

## Task 4: Fix Broken `test_post_analysis_runs` Test

**Context:** When `POST /analysis/runs` was made asynchronous (BackgroundTask), the test was not updated. The test still patches `analysis.main.run_once` (which is no longer called). The endpoint now uses `analysis.main.session_scope` directly (which tries to connect to Postgres in tests) and registers `_do_sweep` as a background task.

**Files:**
- Modify: `tests/test_api_analysis.py`

- [ ] **Step 1: Run the failing test to see the current error**

```bash
cd /workspace/crypto_analysis && python3 -m pytest tests/test_api_analysis.py::test_post_analysis_runs -v
```

Expected output:
```
FAILED tests/test_api_analysis.py::test_post_analysis_runs
sqlalchemy.exc.OperationalError: could not translate host name "postgres" to address
```

- [ ] **Step 2: Add `contextmanager` to the imports at the top of `tests/test_api_analysis.py`**

The file currently starts with:
```python
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from shared.orm import SweepRun, SweepResult
```

Change it to:
```python
import pytest
from contextlib import contextmanager
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from shared.orm import SweepRun, SweepResult
```

- [ ] **Step 3: Replace the failing test with the updated version**

Find this test (near the bottom of the file):

```python
def test_post_analysis_runs(db_session):
    client = _make_test_app(db_session)
    mock_run = MagicMock()
    mock_run.id = 42
    mock_run.status = "complete"
    mock_run.n_settled_predictions = 100
    mock_run.elapsed_seconds = 1.5
    mock_run.best_net_pnl_dollars = 5.0
    mock_run.run_at = datetime.now(timezone.utc)
    with patch("analysis.main.run_once", return_value=mock_run):
        resp = client.post("/analysis/runs")
    assert resp.status_code == 200
    assert resp.json()["status"] == "complete"
    assert resp.json()["id"] == 42
```

Replace it with:

```python
def test_post_analysis_runs(db_session):
    client = _make_test_app(db_session)

    @contextmanager
    def _sqlite_session_scope():
        yield db_session

    # Patch analysis.main.session_scope so run creation uses SQLite (not Postgres).
    # Patch api.main._do_sweep so the background sweep doesn't execute in tests.
    with patch("analysis.main.session_scope", _sqlite_session_scope), \
         patch("api.main._do_sweep"):
        resp = client.post("/analysis/runs")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    assert isinstance(data["id"], int)
    assert data["n_predictions"] is None
    assert data["elapsed_seconds"] is None
    assert data["best_net_pnl_dollars"] is None
```

- [ ] **Step 4: Run the fixed test**

```bash
python3 -m pytest tests/test_api_analysis.py::test_post_analysis_runs -v
```

Expected:
```
PASSED tests/test_api_analysis.py::test_post_analysis_runs
```

- [ ] **Step 5: Run the full test suite to confirm all pass**

```bash
python3 -m pytest --tb=short -q
```

Expected: all tests pass, 0 failures.

- [ ] **Step 6: Commit**

```bash
git add tests/test_api_analysis.py
git commit -m "fix: update test_post_analysis_runs for async sweep endpoint"
```

Expected: commit succeeds.

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| README: add Analysis + Data Explorer dashboard views | Task 1 |
| README: add all 22 API routes | Task 1 |
| README: add Auth section | Task 1 |
| README: add Analysis section | Task 1 |
| README: add all Settings vars | Task 1 |
| README: fix TRAINING_CAMPAIGN_LOOKBACK_HOURS default (was 24, correct is 2160) | Task 1 |
| `.env.example`: add GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, AUTH_SECRET_KEY | Task 2 |
| `.env.example`: add RISK_STALE_MARKET_SECONDS | Task 2 |
| Remove `training_campaign_enabled` dead code | Task 3 |
| Regression test preventing the setting from returning | Task 3 |
| Fix `test_post_analysis_runs` broken by async sweep refactor | Task 4 |

All spec requirements covered. No placeholders.
