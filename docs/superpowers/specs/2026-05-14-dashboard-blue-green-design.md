# Analytics Dashboard + Blue-Green Deployment — Design Spec

**Date:** 2026-05-14
**Status:** Approved

---

## Overview

Two deliverables, tightly coupled:

1. **Analytics Dashboard** — a React + Vite single-page application served as a Docker container. Sidebar navigation with four views: Live Signals, Historical Accuracy, Model Health, System Health. Reads data exclusively from the existing `api/` FastAPI service (no direct DB access). Hosted at `da.ai-al.site`.

2. **Blue-Green Deployment** — all services (ingestor, predictor, api, dashboard) run in two parallel stacks ("blue" and "green") behind a Caddy reverse proxy already in place at `da.ai-al.site`. A deploy script builds the inactive slot, health-checks it, reconfigures Caddy to point at the new slot, reloads Caddy, then tears down the old slot. Postgres sits outside both slots and is never switched.

---

## Architecture

```
Browser
  └─▶ Caddy (da.ai-al.site)  ← Caddyfile points at active slot
          ├─▶ dashboard:5173  (React SPA, active slot)
          └─▶ api:8000        (FastAPI, active slot)
                  │
         ┌────────┴────────┐
      ingestor          predictor
         │                  │
         └────────┬──────────┘
                Postgres  (shared, never switched)
```

**Blue slot internal ports:** dashboard 3001, api 8011, ingestor 8021, predictor 8031
**Green slot internal ports:** dashboard 3002, api 8012, ingestor 8022, predictor 8032

Caddy always proxies `da.ai-al.site` to whichever slot is active. The deploy script updates `caddy/active-slot` (a tiny config snippet Caddy imports) and runs `caddy reload`.

---

## File Structure

```
crypto_analysis/
├── dashboard/
│   ├── Dockerfile
│   ├── nginx.conf               # serves dist/, proxies /api/* to api service
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx              # router + sidebar shell
│       ├── api.ts               # typed fetch helpers for all API calls
│       ├── hooks/
│       │   └── useAutoRefresh.ts  # polls at a configurable interval
│       └── views/
│           ├── Signals.tsx      # Live Signals view
│           ├── Accuracy.tsx     # Historical Accuracy view
│           ├── Models.tsx       # Model Health view
│           └── System.tsx       # System Health view
├── caddy/
│   ├── Caddyfile                # main Caddy config, imports active-slot.caddy
│   └── active-slot.caddy        # single line: reverse_proxy localhost:300X
│                                  updated by deploy script; caddy reload picks it up
├── scripts/
│   └── deploy.sh                # blue-green deploy script
├── docker-compose.infra.yml     # postgres only — never restarted during deploy
├── docker-compose.blue.yml      # blue stack: all app services on blue ports
└── docker-compose.green.yml     # green stack: all app services on green ports
```

---

## Dashboard Service

### Technology
- React 18 + TypeScript, Vite 5, React Router v6
- Recharts for all charts (bar, line, area)
- No external CSS framework — plain CSS modules + CSS variables for theming
- Dark theme: `#0a0c14` background, `#7c3aed` accent

### Sidebar Layout
Persistent left sidebar (200px). Main content area fills the rest. Each nav item routes to `/`, `/accuracy`, `/models`, `/system`.

Sidebar footer shows: active deployment slot (BLUE/GREEN) and auto-refresh interval.

### Auto-refresh
`useAutoRefresh(intervalMs)` hook calls a provided fetch function on mount and on each interval tick. Default: 30 seconds. User can manually trigger refresh via a button in the topbar.

### Views

#### `/` — Live Signals
Stat row (4 cards): Active Markets, Last Ingest Age, Avg Confidence, Low-Confidence Count.

Table columns: Market (ticker + market_id), Signal (↑ UP / ↓ DOWN with color), Confidence (progress bar + %), Status (MODEL OK / LOW CONF badge), Closes In (minutes), Feature Age (seconds).

Data source: `GET /markets` → for each market `GET /predict/{market_id}`. Parallel fetch, settled with `Promise.allSettled`.

#### `/accuracy` — Historical Accuracy
Stat row: Overall Win Rate, Best Market, Avg Brier Score, High-Confidence Accuracy (conf ≥ 0.65).

Bar chart: win rate per market (last 7 days). Line/area chart: rolling 24h accuracy over time.

Data source: `GET /stats/summary` (new), `GET /history/{market_id}?limit=200` per market.

#### `/models` — Model Health
Stat row: Active Models, Best Brier Score, Last Trained, Markets Without Model.

Table: Market, Version, Brier Score (color-coded: green < 0.22, amber 0.22–0.27, red > 0.27), Training Rows, Trained At, Status badge.

Data source: `GET /stats/models` (new).

#### `/system` — System Health
Three service cards (Ingestor, Predictor, API): status dot (green/red), port, last-ingest age, active/stale market counts.

Deployment card: Active Slot, Green Status (running / not running), Last Deploy timestamp.

Data source: `GET /health` polled from each service via the API proxy. Active slot read from `GET /slot` (new, returns `{"slot":"blue"}`).

### nginx.conf (dashboard container)
- Serves `dist/` as static files for all routes (SPA fallback to `index.html`)
- Proxies `/api/*` → `http://api:8000/*` (strips `/api` prefix)
- This means all dashboard fetch calls use `/api/...` — no hardcoded service URLs

### Dockerfile (dashboard)
Multi-stage: `node:20-alpine` build stage (`npm ci && npm run build`), then `nginx:alpine` serve stage. Final image is ~25MB.

---

## New API Endpoints

Two new endpoints added to `api/main.py`:

### `GET /stats/summary`
Aggregates accuracy stats across all markets.
```json
{
  "total_settled": 128,
  "overall_accuracy": 0.643,
  "high_conf_accuracy": 0.718,
  "markets": [
    {"ticker": "BTC", "accuracy": 0.712, "settled_count": 64}
  ]
}
```
Query: settled predictions (`actual_outcome IS NOT NULL`) grouped by market. High-confidence = `confidence >= 0.65`.

### `GET /stats/models`
Returns full model registry with active models per market.
```json
[{
  "market_id": "KXBTCUSD-15M-...",
  "ticker": "BTC",
  "version": "v4",
  "brier_score": 0.19,
  "training_rows": 842,
  "trained_at": "2026-05-14T16:00:00Z",
  "is_active": true
}]
```
Query: all rows from `model_registry` where `is_active = true`, joined with `markets` for ticker.

### `GET /slot`
Returns the active deployment slot, read from the `DEPLOY_SLOT` environment variable (set to `blue` or `green` in each compose file).
```json
{"slot": "blue"}
```

---

## Blue-Green Infrastructure

### docker-compose.infra.yml
Contains only `postgres` service + `pgdata` volume. Started once; never touched during deploys.

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: crypto_analysis
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 10
volumes:
  pgdata:
```

### docker-compose.blue.yml / docker-compose.green.yml
Each defines a complete app stack. The only differences are:
- Container name prefix (`blue-` vs `green-`)
- Host port bindings (blue: 3001/8011/8021/8031, green: 3002/8012/8022/8032)
- `DEPLOY_SLOT=blue` / `DEPLOY_SLOT=green` env var

All services connect to Postgres via `POSTGRES_HOST=postgres` on the `infra` network (declared as `external: true`).

Services in each stack: `migrate` (one-shot, alembic upgrade head), `ingestor`, `predictor`, `api`, `dashboard`.

`models` named volume is **shared across both slots** — a single `models:` volume mounted into both blue and green predictors. This ensures green starts with the same trained artifacts as blue. Model versioning in `model_registry` prevents stale loads.

### caddy/Caddyfile
```
da.ai-al.site {
  import {$CADDY_SNIPPET_PATH}
}
```
`CADDY_SNIPPET_PATH` defaults to `/etc/caddy/active-slot.caddy` when Caddy is managed by systemd, or to the absolute project path otherwise. Set it in the environment where Caddy runs.

### caddy/active-slot.caddy (managed by deploy script)
```
# Active slot: blue
reverse_proxy /api/* localhost:8011
reverse_proxy localhost:3001
```
The deploy script writes this file to the absolute path in `CADDY_SNIPPET_PATH`, then runs `caddy reload --config ${CADDYFILE_PATH}`. Both env vars are required; the script exits with an error if either is unset.

### scripts/deploy.sh
```
Usage: ./scripts/deploy.sh [blue|green]
```
Steps:
1. Determine target slot (argument) and current active slot (read from `caddy/active-slot.caddy`)
2. Run `docker compose -f docker-compose.${TARGET}.yml up -d --build`
3. Wait for all services healthy: poll both `GET http://localhost:${API_PORT}/health` (confirms DB connectivity) and `GET http://localhost:${DASHBOARD_PORT}/` (confirms nginx is up) on the target slot, up to 120s at 5s intervals. Both must return 2xx before proceeding.
4. Rewrite `caddy/active-slot.caddy` with target slot ports
5. Run `caddy reload --config caddy/Caddyfile`
6. Wait 5s (let Caddy drain in-flight requests)
7. Run `docker compose -f docker-compose.${OLD}.yml down`
8. Print: `✓ Deployed to ${TARGET}. Old ${OLD} slot stopped.`

On any failure in steps 2–5, exit non-zero without touching Caddy — old slot stays live.

---

## Error Handling

| Failure | Behavior |
|---------|----------|
| New slot health-check timeout | Deploy aborts; Caddy unchanged; old slot stays live |
| `caddy reload` fails | Deploy aborts; old slot stays live; new slot left running for inspection |
| API endpoint returns 5xx | Dashboard shows last-known data + error banner with timestamp |
| Market fetch fails in Signals view | Show partial results for markets that succeeded; error badge on failed ones |
| `/stats/summary` has no settled predictions | Return zeros; dashboard shows "No data yet" placeholder |

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Dashboard frontend | React 18, TypeScript, Vite 5, Recharts, React Router v6 |
| Dashboard container | nginx:alpine (static), multi-stage build |
| API additions | Python 3.11, FastAPI (extends existing `api/main.py`) |
| Reverse proxy | Caddy (existing, host-level) |
| Blue-green switching | Shell script + Caddy config reload |
| Compose orchestration | Docker Compose v2 (three files: infra, blue, green) |

---

## Out of Scope

- Authentication on the dashboard (internal/ops use assumed)
- Real-time WebSocket push (30s polling is sufficient)
- Dark/light mode toggle
- Mobile layout optimization
- Alerting / notifications (Slack, email) on model degradation
