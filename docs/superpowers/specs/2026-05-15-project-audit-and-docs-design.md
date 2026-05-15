# Project Audit & Documentation Sync — Design Spec

**Date:** 2026-05-15
**Goal:** Close the gap between the running system and all written documentation; remove one confirmed piece of dead code.

---

## 1. Context

The project has gone through five implementation cycles since its initial README was written:

| Cycle | Added |
|---|---|
| Initial platform | Ingestor, predictor, trainer, API, dashboard core |
| Continuous learning loop | Backfill, trainer daemon, model promotion |
| Accuracy / Data Explorer | Series-level accuracy view, raw-features data explorer |
| Parameter sweep analysis | `analysis/` module, two new DB tables, Analysis dashboard tab |
| Auth | Google OAuth login, JWT cookie session, Login page, Sign-out |

None of the last three cycles appear in the README. The `.env.example` is missing five variables. One setting (`TRAINING_CAMPAIGN_ENABLED`) is defined in `Settings` but never read anywhere in the codebase.

---

## 2. Gaps Found

### 2.1 README.md

| Section | Gap |
|---|---|
| Architecture diagram | Missing `analysis/` module; diagram shows only 4 services |
| Dashboard Views | Missing Data Explorer and Analysis tabs |
| API Endpoints | Missing: `/stats/training`, `/auth/*` (4 routes), `/analysis/*` (4 routes), `/data/*` (5 routes) |
| Settings table | Missing: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `AUTH_SECRET_KEY`, `RISK_STALE_MARKET_SECONDS`, `TRAINING_CAMPAIGN_ENABLED` |
| No Authentication section | OAuth flow, session cookie, how to provision credentials |
| No Analysis section | What the parameter sweep does, how to trigger it, what results mean |

### 2.2 `.env.example`

| Variable | Status |
|---|---|
| `GOOGLE_CLIENT_ID` | Missing |
| `GOOGLE_CLIENT_SECRET` | Missing |
| `AUTH_SECRET_KEY` | Missing |
| `RISK_STALE_MARKET_SECONDS` | Missing |
| `TRAINING_CAMPAIGN_ENABLED` | Missing (will be removed from Settings, see §2.3) |

### 2.3 Dead Code — `TRAINING_CAMPAIGN_ENABLED`

`shared/settings.py` defines `training_campaign_enabled: bool = False`. It is not referenced anywhere in `trainer/`, `api/`, or any other module. The trainer daemon always runs training campaigns unconditionally. Keeping the setting creates a false impression that it can be toggled. **Decision: remove it from Settings.**

---

## 3. Changes

### 3.1 `README.md` — Full overhaul

**Architecture section:** Add `analysis/` to the ASCII diagram. Update the services table to clarify that `analysis/` is an on-demand module invoked by the API, not a daemon.

**Dashboard Views:** Add:
- **Data Explorer** — raw feature rows, feature vector table, column stats, correlations, feature importance
- **Analysis** — parameter sweep runs, knob importance chart, per-knob response curves, top-500 results table

**API Endpoints:** Expand the table to include all 22 routes, grouped by concern:

```
Core
  GET /health
  GET /slot
  GET /markets
  GET /predict/{market_id}
  GET /history/{market_id}
  GET /history/series/{series_ticker}

Stats
  GET /stats/summary
  GET /stats/models
  GET /stats/training

Auth
  GET /auth/login        → redirect to Google
  GET /auth/callback     → exchange code, set cookie, redirect to /
  GET /auth/me           → 200 if authenticated
  POST /auth/logout      → clear cookie

Data Explorer
  GET /data/raw-features
  GET /data/feature-vectors
  GET /data/stats
  GET /data/correlations
  GET /data/feature-importance

Analysis
  GET  /analysis/runs
  GET  /analysis/runs/latest
  GET  /analysis/runs/{id}/results
  POST /analysis/runs        → start sweep (returns immediately; runs in background)
```

**Settings table:** Add all missing variables:

| Variable | Default | Description |
|---|---|---|
| `GOOGLE_CLIENT_ID` | — | OAuth 2.0 client ID from Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | — | OAuth 2.0 client secret |
| `AUTH_SECRET_KEY` | — | 256-bit hex key for JWT signing (`openssl rand -hex 32`) |
| `RISK_STALE_MARKET_SECONDS` | `60` | Seconds after close_time before a market is marked stale |

**New Authentication section:**
- Google OAuth 2.0 flow (login → callback → cookie)
- HTTP-only signed JWT cookie (`session`), 30-day expiry
- Allowlist: single email (`m7.ga.77@gmail.com` hardcoded in api/main.py)
- How to provision: create OAuth 2.0 Web Application in Google Cloud Console, set authorized redirect URI to `https://<DOMAIN>/api/auth/callback`

**New Analysis section:**
- What it does: full-grid sweep over 10 knobs × ~5 values = 7.5M combinations evaluated against settled predictions
- Fixed fee assumption: 50 bps
- Outputs: top-500 results by net P&L, per-knob marginals (mean P&L across all combos at each value)
- How to trigger: click "Run sweep now" in the Analysis tab; runs async in background (~215s on 5K predictions)
- Results persist across deployments (stored in `sweep_runs` + `sweep_results` tables)

### 3.2 `.env.example` — Add missing variables

Add a new `# Auth` section:

```bash
# Auth (Google OAuth)
GOOGLE_CLIENT_ID=your-google-oauth-client-id
GOOGLE_CLIENT_SECRET=your-google-oauth-client-secret
AUTH_SECRET_KEY=generate-with--openssl-rand--hex-32
```

Add `RISK_STALE_MARKET_SECONDS=60` under the `# Risk / ingestor` comment.

### 3.3 `shared/settings.py` — Remove dead field

Delete the line:
```python
training_campaign_enabled: bool = False
```

The trainer daemon always runs; this setting was never wired up and misleads future readers.

---

## 4. Implementation Fidelity Verification

Completed inline during audit. Key findings:

| Claim | Verified |
|---|---|
| Anchored-edge formula in sweep.py ≡ spec formula | ✅ algebraically equivalent |
| P&L formula (raw_pnl + fee deducted per-trade) | ✅ matches spec |
| All 10 knobs in PARAM_GRID | ✅ |
| Top-500 stored as `top_k`, marginals as `marginal` | ✅ |
| POST /analysis/runs is async (BackgroundTask) | ✅ intentional improvement over synchronous spec |
| `backfill_outcomes()` in ingestor has isolated session + outer try/except | ✅ |
| `settled_last_24h` filters on `settled_at`, not `ts` | ✅ |

No code corrections required beyond the `TRAINING_CAMPAIGN_ENABLED` removal.

---

## 5. Out of Scope

- Adding new API documentation (OpenAPI/Swagger) — README table is sufficient for this internal tool
- Adding runtime toggling of the sweep grid — one fixed grid per run is correct for now
- Per-user auth or multi-user support — single-user system, allowlist stays hardcoded
