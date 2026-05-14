# Analytics Dashboard + Blue-Green Deployment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a React analytics dashboard served as a Docker container, add three new FastAPI endpoints it depends on, and wire all services into blue/green Docker Compose stacks behind an existing Caddy reverse proxy.

**Architecture:** Three new API endpoints (`/stats/summary`, `/stats/models`, `/slot`) extend `api/main.py`. The React SPA lives in `dashboard/`, built with Vite and served by nginx, which also proxies `/api/*` to the FastAPI service so the browser only ever talks to one origin. All app services are duplicated into `docker-compose.blue.yml` and `docker-compose.green.yml` with different host ports; Postgres lives exclusively in `docker-compose.infra.yml` and is never restarted during deploys. A shell deploy script builds the inactive slot, health-checks it, rewrites `caddy/active-slot.caddy`, runs `caddy reload`, then stops the old slot.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2, pytest; React 18, TypeScript, Vite 5, React Router v6, Recharts; nginx:alpine; Docker Compose v2; Caddy (host-level, already installed).

---

## File Map

**New files:**
- `api/main.py` — add `/stats/summary`, `/stats/models`, `/slot` (modify existing)
- `tests/test_api.py` — add tests for new endpoints (modify existing)
- `docker-compose.infra.yml` — Postgres + shared volumes + infra network
- `docker-compose.blue.yml` — full app stack on blue ports (3001/8011/8021/8031)
- `docker-compose.green.yml` — full app stack on green ports (3002/8012/8022/8032)
- `caddy/Caddyfile` — imports `{$CADDY_SNIPPET_PATH}`
- `caddy/active-slot.caddy` — initial blue slot snippet (managed by deploy script)
- `scripts/deploy.sh` — blue-green deploy orchestration
- `dashboard/package.json`
- `dashboard/vite.config.ts`
- `dashboard/tsconfig.json`
- `dashboard/index.html`
- `dashboard/nginx.conf`
- `dashboard/Dockerfile`
- `dashboard/src/main.tsx`
- `dashboard/src/App.tsx`
- `dashboard/src/styles/global.css`
- `dashboard/src/api.ts`
- `dashboard/src/hooks/useAutoRefresh.ts`
- `dashboard/src/views/Signals.tsx`
- `dashboard/src/views/Signals.module.css`
- `dashboard/src/views/Accuracy.tsx`
- `dashboard/src/views/Accuracy.module.css`
- `dashboard/src/views/Models.tsx`
- `dashboard/src/views/Models.module.css`
- `dashboard/src/views/System.tsx`
- `dashboard/src/views/System.module.css`

---

## Task 1: New API endpoints — /stats/summary, /stats/models, /slot

**Files:**
- Modify: `api/main.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing tests for `/stats/summary`**

Add to `tests/test_api.py`:

```python
def test_stats_summary_no_data(db_session):
    client = _make_test_app(db_session)
    resp = client.get("/stats/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_settled"] == 0
    assert body["overall_accuracy"] == 0.0
    assert body["high_conf_accuracy"] == 0.0
    assert body["markets"] == []


def test_stats_summary_aggregates_correctly(db_session):
    from datetime import datetime, timezone, timedelta
    from shared.orm import Market, Prediction
    m = Market(
        market_id="KXBTCUSD-S1", ticker="BTC", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(minutes=7),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(m)
    db_session.flush()
    # 2 correct UP predictions, 1 wrong DOWN (actual_outcome=1 but direction=DOWN)
    for direction, actual, conf in [("UP", 1, 0.73), ("UP", 1, 0.70), ("DOWN", 1, 0.68)]:
        p = Prediction(
            market_id="KXBTCUSD-S1", ts=datetime.now(timezone.utc),
            direction=direction, confidence=conf,
            low_confidence=False, model_version="v1",
            settled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            actual_outcome=actual,
        )
        db_session.add(p)
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/stats/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_settled"] == 3
    assert abs(body["overall_accuracy"] - 2/3) < 0.01
    # high_conf_accuracy: all 3 have conf >= 0.65, 2 correct
    assert abs(body["high_conf_accuracy"] - 2/3) < 0.01
    assert len(body["markets"]) == 1
    assert body["markets"][0]["ticker"] == "BTC"


def test_stats_models_returns_active(db_session):
    from datetime import datetime, timezone, timedelta
    from shared.orm import Market, ModelRegistry
    m = Market(
        market_id="KXBTCUSD-MR1", ticker="BTC", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(minutes=7),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(m)
    db_session.flush()
    reg = ModelRegistry(
        market_id="KXBTCUSD-MR1", version="v4",
        trained_at=datetime.now(timezone.utc),
        training_rows=842, brier_score=0.19,
        artifact_path="/app/models/KXBTCUSD-MR1_v4.ubj",
        is_active=True,
    )
    db_session.add(reg)
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/stats/models")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["ticker"] == "BTC"
    assert data[0]["version"] == "v4"
    assert abs(data[0]["brier_score"] - 0.19) < 0.001
    assert data[0]["is_active"] is True


def test_stats_models_excludes_inactive(db_session):
    from datetime import datetime, timezone, timedelta
    from shared.orm import Market, ModelRegistry
    m = Market(
        market_id="KXBTCUSD-MR2", ticker="BTC", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(minutes=7),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(m)
    db_session.flush()
    reg = ModelRegistry(
        market_id="KXBTCUSD-MR2", version="v3",
        trained_at=datetime.now(timezone.utc),
        training_rows=500, brier_score=0.25,
        artifact_path="/app/models/KXBTCUSD-MR2_v3.ubj",
        is_active=False,
    )
    db_session.add(reg)
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get("/stats/models")
    assert resp.status_code == 200
    assert resp.json() == []


def test_slot_returns_env_var(monkeypatch):
    import os
    monkeypatch.setenv("DEPLOY_SLOT", "green")
    import importlib
    import api.main as api_module
    importlib.reload(api_module)
    # create a fresh app after setting env var
    from fastapi.testclient import TestClient
    app = api_module.create_app(lambda: None)
    client = TestClient(app)
    resp = client.get("/slot")
    assert resp.status_code == 200
    assert resp.json()["slot"] == "green"
    monkeypatch.delenv("DEPLOY_SLOT", raising=False)


def test_slot_defaults_to_blue():
    import os
    os.environ.pop("DEPLOY_SLOT", None)
    from fastapi.testclient import TestClient
    import api.main as api_module
    app = api_module.create_app(lambda: None)
    client = TestClient(app)
    resp = client.get("/slot")
    assert resp.status_code == 200
    assert resp.json()["slot"] == "blue"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /workspace/crypto_analysis && source venv/bin/activate && pytest tests/test_api.py::test_stats_summary_no_data tests/test_api.py::test_stats_models_returns_active tests/test_api.py::test_slot_defaults_to_blue -v 2>&1 | tail -20
```

Expected: FAIL — `404 Not Found` or `AttributeError: 'NoneType'` because endpoints don't exist yet.

- [ ] **Step 3: Implement the three new endpoints in `api/main.py`**

Add `import os` at the top. Then add the following three route functions inside `create_app()`, after the existing `/history/{market_id}` route and before `return app`:

```python
    @app.get("/stats/summary")
    def get_stats_summary(session: Session = Depends(_get_db)):
        from sqlalchemy import func, case as sa_case
        from shared.orm import ModelRegistry  # noqa: F401 (already imported above if needed)

        # Correct = direction matches actual_outcome
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

        rows = (
            session.query(
                Market.ticker,
                Prediction.market_id,
                func.count(Prediction.id).label("settled_count"),
                func.avg(correct_expr).label("accuracy"),
            )
            .join(Market, Market.market_id == Prediction.market_id)
            .filter(Prediction.actual_outcome != None)  # noqa: E711
            .group_by(Market.ticker, Prediction.market_id)
            .all()
        )

        if not rows:
            return {
                "total_settled": 0,
                "overall_accuracy": 0.0,
                "high_conf_accuracy": 0.0,
                "markets": [],
            }

        total_settled = sum(r.settled_count for r in rows)
        overall_accuracy = (
            sum(float(r.accuracy or 0) * r.settled_count for r in rows) / total_settled
        )

        hc_preds = (
            session.query(Prediction)
            .filter(
                Prediction.actual_outcome != None,  # noqa: E711
                Prediction.confidence >= 0.65,
            )
            .all()
        )
        if hc_preds:
            hc_correct = sum(
                1 for p in hc_preds
                if (p.direction == "UP" and p.actual_outcome == 1)
                or (p.direction == "DOWN" and p.actual_outcome == 0)
            )
            high_conf_accuracy = hc_correct / len(hc_preds)
        else:
            high_conf_accuracy = 0.0

        return {
            "total_settled": total_settled,
            "overall_accuracy": round(overall_accuracy, 3),
            "high_conf_accuracy": round(high_conf_accuracy, 3),
            "markets": [
                {
                    "ticker": r.ticker,
                    "accuracy": round(float(r.accuracy or 0), 3),
                    "settled_count": r.settled_count,
                }
                for r in rows
            ],
        }

    @app.get("/stats/models")
    def get_stats_models(session: Session = Depends(_get_db)):
        from shared.orm import ModelRegistry
        rows = (
            session.query(ModelRegistry, Market.ticker)
            .join(Market, Market.market_id == ModelRegistry.market_id)
            .filter(ModelRegistry.is_active == True)  # noqa: E712
            .all()
        )
        return [
            {
                "market_id": m.market_id,
                "ticker": ticker,
                "version": m.version,
                "brier_score": float(m.brier_score),
                "training_rows": m.training_rows,
                "trained_at": m.trained_at.isoformat(),
                "is_active": m.is_active,
            }
            for m, ticker in rows
        ]

    @app.get("/slot")
    def get_slot():
        return {"slot": os.environ.get("DEPLOY_SLOT", "blue")}
```

Also add `import os` at the top of `api/main.py` (after the existing imports).

- [ ] **Step 4: Run all API tests**

```bash
cd /workspace/crypto_analysis && source venv/bin/activate && pytest tests/test_api.py -v 2>&1 | tail -30
```

Expected: All tests PASS (including the new ones). If `test_slot_returns_env_var` is flaky due to module reload, remove the `importlib.reload` approach and replace with a direct `os.environ` patch inside `create_app` — but try the direct approach first.

- [ ] **Step 5: Commit**

```bash
cd /workspace/crypto_analysis && git add api/main.py tests/test_api.py && git commit -m "feat: add /stats/summary, /stats/models, /slot API endpoints"
```

---

## Task 2: docker-compose.infra.yml

**Files:**
- Create: `docker-compose.infra.yml`

- [ ] **Step 1: Create `docker-compose.infra.yml`**

```yaml
# docker-compose.infra.yml — Postgres only. Start once; never touch during deploys.
# docker compose -f docker-compose.infra.yml up -d
version: "3.9"

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
    networks:
      - infra
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 10

volumes:
  pgdata:
    name: crypto_analysis_pgdata
  models:
    name: crypto_analysis_models

networks:
  infra:
    name: crypto_analysis_infra
```

- [ ] **Step 2: Verify it parses**

```bash
cd /workspace/crypto_analysis && docker compose -f docker-compose.infra.yml config --quiet && echo "OK"
```

Expected: `OK` with no errors.

- [ ] **Step 3: Commit**

```bash
cd /workspace/crypto_analysis && git add docker-compose.infra.yml && git commit -m "feat: add docker-compose.infra.yml for shared postgres + volumes"
```

---

## Task 3: docker-compose.blue.yml and docker-compose.green.yml

**Files:**
- Create: `docker-compose.blue.yml`
- Create: `docker-compose.green.yml`

- [ ] **Step 1: Create `docker-compose.blue.yml`**

```yaml
# docker-compose.blue.yml — Blue slot. Ports: dashboard 3001, api 8011, ingestor 8021, predictor 8031.
# Requires infra stack running first: docker compose -f docker-compose.infra.yml up -d
version: "3.9"

x-slot: &slot
  DEPLOY_SLOT: blue

services:
  migrate-blue:
    build:
      context: .
      dockerfile: ingestor/Dockerfile
    # pg_isready loop because postgres is in a separate compose project;
    # depends_on condition: service_healthy only works within the same project.
    command: sh -c "until pg_isready -h postgres -U postgres; do sleep 2; done && alembic upgrade head"
    container_name: blue-migrate
    environment:
      <<: *slot
      POSTGRES_HOST: postgres
    env_file: .env
    networks:
      - infra

  ingestor-blue:
    build:
      context: .
      dockerfile: ingestor/Dockerfile
    container_name: blue-ingestor
    environment:
      <<: *slot
      POSTGRES_HOST: postgres
    env_file: .env
    depends_on:
      migrate-blue:
        condition: service_completed_successfully
    volumes:
      - ./Kalshi-1.txt:/app/Kalshi-1.txt:ro
      - ./Kalshi-2-Demo.txt:/app/Kalshi-2-Demo.txt:ro
    ports:
      - "8021:8001"
    networks:
      - infra

  predictor-blue:
    build:
      context: .
      dockerfile: predictor/Dockerfile
    container_name: blue-predictor
    environment:
      <<: *slot
      POSTGRES_HOST: postgres
    env_file: .env
    depends_on:
      migrate-blue:
        condition: service_completed_successfully
    volumes:
      - models:/app/models
    ports:
      - "8031:8002"
    networks:
      - infra

  api-blue:
    build:
      context: .
      dockerfile: api/Dockerfile
    container_name: blue-api
    environment:
      <<: *slot
      POSTGRES_HOST: postgres
    env_file: .env
    depends_on:
      migrate-blue:
        condition: service_completed_successfully
    ports:
      - "8011:8000"
    networks:
      - infra

  dashboard-blue:
    build:
      context: .
      dockerfile: dashboard/Dockerfile
    container_name: blue-dashboard
    depends_on:
      - api-blue
    ports:
      - "3001:80"
    networks:
      - infra

volumes:
  models:
    external: true
    name: crypto_analysis_models

networks:
  infra:
    external: true
    name: crypto_analysis_infra

# External service reference so depends_on can reference postgres health
# (postgres is in the infra stack but healthcheck is declared there)
```

- [ ] **Step 2: Create `docker-compose.green.yml`**

Same as blue, with these substitutions:
- `blue` → `green` everywhere in container names and DEPLOY_SLOT
- Ports: `8021` → `8022`, `8031` → `8032`, `8011` → `8012`, `3001` → `3002`

```yaml
# docker-compose.green.yml — Green slot. Ports: dashboard 3002, api 8012, ingestor 8022, predictor 8032.
version: "3.9"

x-slot: &slot
  DEPLOY_SLOT: green

services:
  migrate-green:
    build:
      context: .
      dockerfile: ingestor/Dockerfile
    command: sh -c "until pg_isready -h postgres -U postgres; do sleep 2; done && alembic upgrade head"
    container_name: green-migrate
    environment:
      <<: *slot
      POSTGRES_HOST: postgres
    env_file: .env
    networks:
      - infra

  ingestor-green:
    build:
      context: .
      dockerfile: ingestor/Dockerfile
    container_name: green-ingestor
    environment:
      <<: *slot
      POSTGRES_HOST: postgres
    env_file: .env
    depends_on:
      migrate-green:
        condition: service_completed_successfully
    volumes:
      - ./Kalshi-1.txt:/app/Kalshi-1.txt:ro
      - ./Kalshi-2-Demo.txt:/app/Kalshi-2-Demo.txt:ro
    ports:
      - "8022:8001"
    networks:
      - infra

  predictor-green:
    build:
      context: .
      dockerfile: predictor/Dockerfile
    container_name: green-predictor
    environment:
      <<: *slot
      POSTGRES_HOST: postgres
    env_file: .env
    depends_on:
      migrate-green:
        condition: service_completed_successfully
    volumes:
      - models:/app/models
    ports:
      - "8032:8002"
    networks:
      - infra

  api-green:
    build:
      context: .
      dockerfile: api/Dockerfile
    container_name: green-api
    environment:
      <<: *slot
      POSTGRES_HOST: postgres
    env_file: .env
    depends_on:
      migrate-green:
        condition: service_completed_successfully
    ports:
      - "8012:8000"
    networks:
      - infra

  dashboard-green:
    build:
      context: .
      dockerfile: dashboard/Dockerfile
    container_name: green-dashboard
    depends_on:
      - api-green
    ports:
      - "3002:80"
    networks:
      - infra

volumes:
  models:
    external: true
    name: crypto_analysis_models

networks:
  infra:
    external: true
    name: crypto_analysis_infra
```

- [ ] **Step 3: Verify both files parse**

```bash
cd /workspace/crypto_analysis && docker compose -f docker-compose.blue.yml config --quiet 2>&1 | head -5 && docker compose -f docker-compose.green.yml config --quiet 2>&1 | head -5 && echo "Both OK"
```

Expected: `Both OK` (there may be warnings about the `dashboard` image not existing yet — that's fine at this stage).

- [ ] **Step 4: Commit**

```bash
cd /workspace/crypto_analysis && git add docker-compose.blue.yml docker-compose.green.yml && git commit -m "feat: add blue/green docker compose stacks"
```

---

## Task 4: Caddy config files

**Files:**
- Create: `caddy/Caddyfile`
- Create: `caddy/active-slot.caddy`

- [ ] **Step 1: Create `caddy/` directory and Caddyfile**

```bash
mkdir -p /workspace/crypto_analysis/caddy
```

Create `caddy/Caddyfile`:

```
# caddy/Caddyfile
# CADDY_SNIPPET_PATH env var must be set to the absolute path of active-slot.caddy
# Example (systemd): Environment=CADDY_SNIPPET_PATH=/opt/crypto_analysis/caddy/active-slot.caddy
da.ai-al.site {
  import {$CADDY_SNIPPET_PATH}
}
```

- [ ] **Step 2: Create initial `caddy/active-slot.caddy` (blue slot)**

```
# Active slot: blue
# Managed by scripts/deploy.sh — do not edit manually
reverse_proxy /api/* localhost:8011
reverse_proxy localhost:3001
```

- [ ] **Step 3: Commit**

```bash
cd /workspace/crypto_analysis && git add caddy/ && git commit -m "feat: add Caddy config for blue-green proxy"
```

---

## Task 5: scripts/deploy.sh

**Files:**
- Create: `scripts/deploy.sh`

- [ ] **Step 1: Create `scripts/` and write deploy script**

```bash
mkdir -p /workspace/crypto_analysis/scripts
```

Create `scripts/deploy.sh`:

```bash
#!/usr/bin/env bash
# Blue-green deploy script.
# Usage: ./scripts/deploy.sh [blue|green]
# Required env vars: CADDY_SNIPPET_PATH, CADDYFILE_PATH
set -euo pipefail

TARGET="${1:-}"
if [[ "$TARGET" != "blue" && "$TARGET" != "green" ]]; then
  echo "Usage: $0 [blue|green]" >&2
  exit 1
fi

if [[ -z "${CADDY_SNIPPET_PATH:-}" ]]; then
  echo "ERROR: CADDY_SNIPPET_PATH is not set" >&2
  exit 1
fi

if [[ -z "${CADDYFILE_PATH:-}" ]]; then
  echo "ERROR: CADDYFILE_PATH is not set" >&2
  exit 1
fi

# Determine old slot
if [[ "$TARGET" == "blue" ]]; then
  OLD="green"
  API_PORT=8011
  DASHBOARD_PORT=3001
else
  OLD="blue"
  API_PORT=8012
  DASHBOARD_PORT=3002
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "==> Building and starting ${TARGET} slot..."
docker compose -f "${PROJECT_DIR}/docker-compose.${TARGET}.yml" up -d --build

echo "==> Waiting for ${TARGET} slot to be healthy (up to 120s)..."
DEADLINE=$(( $(date +%s) + 120 ))
while true; do
  if [[ $(date +%s) -gt $DEADLINE ]]; then
    echo "ERROR: Health check timed out after 120s. Caddy unchanged; ${OLD} slot stays live." >&2
    exit 1
  fi
  API_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${API_PORT}/health" || echo "000")
  DASH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${DASHBOARD_PORT}/" || echo "000")
  if [[ "$API_STATUS" == "200" && "$DASH_STATUS" == "200" ]]; then
    echo "   API: ${API_STATUS}  Dashboard: ${DASH_STATUS} — healthy"
    break
  fi
  echo "   API: ${API_STATUS}  Dashboard: ${DASH_STATUS} — waiting..."
  sleep 5
done

echo "==> Switching Caddy to ${TARGET} slot..."
cat > "${CADDY_SNIPPET_PATH}" <<EOF
# Active slot: ${TARGET}
# Managed by scripts/deploy.sh — do not edit manually
reverse_proxy /api/* localhost:${API_PORT}
reverse_proxy localhost:${DASHBOARD_PORT}
EOF

caddy reload --config "${CADDYFILE_PATH}"

echo "==> Waiting 5s for Caddy to drain in-flight requests..."
sleep 5

echo "==> Stopping ${OLD} slot..."
docker compose -f "${PROJECT_DIR}/docker-compose.${OLD}.yml" down

echo "✓ Deployed to ${TARGET}. Old ${OLD} slot stopped."
```

- [ ] **Step 2: Make executable**

```bash
chmod +x /workspace/crypto_analysis/scripts/deploy.sh
```

- [ ] **Step 3: Validate script syntax**

```bash
bash -n /workspace/crypto_analysis/scripts/deploy.sh && echo "Syntax OK"
```

Expected: `Syntax OK`

- [ ] **Step 4: Commit**

```bash
cd /workspace/crypto_analysis && git add scripts/deploy.sh && git commit -m "feat: add blue-green deploy script"
```

---

## Task 6: Dashboard scaffold — package.json, Vite config, TypeScript config, index.html, main.tsx, global CSS

**Files:**
- Create: `dashboard/package.json`
- Create: `dashboard/vite.config.ts`
- Create: `dashboard/tsconfig.json`
- Create: `dashboard/index.html`
- Create: `dashboard/src/main.tsx`
- Create: `dashboard/src/styles/global.css`

- [ ] **Step 1: Create `dashboard/package.json`**

```json
{
  "name": "kalshi-dashboard",
  "private": true,
  "version": "1.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.23.1",
    "recharts": "^2.12.7"
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "typescript": "^5.4.5",
    "vite": "^5.3.1"
  }
}
```

- [ ] **Step 2: Create `dashboard/vite.config.ts`**

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
  },
})
```

- [ ] **Step 3: Create `dashboard/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"]
}
```

- [ ] **Step 4: Create `dashboard/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Kalshi Analytics</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Create `dashboard/src/styles/global.css`**

```css
:root {
  --bg: #0a0c14;
  --bg-card: #1a1d27;
  --bg-row: #1e2233;
  --border: #2a2d3a;
  --accent: #7c3aed;
  --accent-dim: #5b21b6;
  --text: #e2e8f0;
  --text-muted: #64748b;
  --text-label: #94a3b8;
  --green: #22c55e;
  --red: #ef4444;
  --amber: #f59e0b;
  --sidebar-width: 200px;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: var(--bg);
  color: var(--text);
  font-family: ui-monospace, 'Cascadia Code', 'Source Code Pro', monospace;
  font-size: 14px;
  line-height: 1.5;
}

a { color: inherit; text-decoration: none; }

button {
  cursor: pointer;
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: 4px;
  padding: 4px 12px;
  font-size: 12px;
  font-family: inherit;
}

button:hover { background: var(--accent-dim); }

.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 3px;
  font-size: 11px;
  font-weight: 600;
}

.badge-ok { background: rgba(34,197,94,0.15); color: var(--green); }
.badge-warn { background: rgba(245,158,11,0.15); color: var(--amber); }
.badge-err { background: rgba(239,68,68,0.15); color: var(--red); }

.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 24px;
}

.stat-card {
  background: var(--bg-card);
  border-radius: 6px;
  padding: 16px;
}

.stat-label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-label);
  margin-bottom: 6px;
}

.stat-value {
  font-size: 22px;
  font-weight: 700;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
}

.data-table th {
  text-align: left;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-label);
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
}

.data-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
}

.data-table tbody tr:hover { background: var(--bg-row); }

.error-banner {
  background: rgba(239,68,68,0.1);
  border: 1px solid var(--red);
  border-radius: 6px;
  padding: 10px 16px;
  color: var(--red);
  margin-bottom: 16px;
  font-size: 13px;
}

.placeholder {
  text-align: center;
  color: var(--text-muted);
  padding: 48px 0;
}
```

- [ ] **Step 6: Create `dashboard/src/main.tsx`**

```typescript
import React from 'react'
import ReactDOM from 'react-dom/client'
import './styles/global.css'
import App from './App'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
```

- [ ] **Step 7: Install dependencies**

```bash
cd /workspace/crypto_analysis/dashboard && npm install 2>&1 | tail -5
```

Expected: `added NNN packages` with no errors.

- [ ] **Step 8: Commit scaffold**

```bash
cd /workspace/crypto_analysis && git add dashboard/ && git commit -m "feat: scaffold dashboard with Vite + React 18 + TypeScript"
```

---

## Task 7: dashboard/src/api.ts and useAutoRefresh hook

**Files:**
- Create: `dashboard/src/api.ts`
- Create: `dashboard/src/hooks/useAutoRefresh.ts`

- [ ] **Step 1: Create `dashboard/src/api.ts`**

```typescript
// All paths go through nginx /api/ prefix, which nginx strips before forwarding to api:8000

export interface Market {
  market_id: string
  ticker: string
  title: string | null
  close_time: string | null
  minutes_to_close: number | null
}

export interface Prediction {
  market_id: string
  direction: 'UP' | 'DOWN'
  confidence: number
  low_confidence: boolean
  model_version: string
  ts: string
  feature_age_seconds: number | null
}

export interface HistoryEntry {
  ts: string
  direction: 'UP' | 'DOWN'
  confidence: number
  actual_outcome: number
  correct: boolean
}

export interface MarketSummary {
  ticker: string
  accuracy: number
  settled_count: number
}

export interface StatsSummary {
  total_settled: number
  overall_accuracy: number
  high_conf_accuracy: number
  markets: MarketSummary[]
}

export interface ModelInfo {
  market_id: string
  ticker: string
  version: string
  brier_score: number
  training_rows: number
  trained_at: string
  is_active: boolean
}

export interface HealthResponse {
  status: string
  active_markets: number
  stale_markets: number
}

export interface SlotResponse {
  slot: string
}

async function apiFetch<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`)
  return res.json() as Promise<T>
}

export const fetchMarkets = (): Promise<Market[]> =>
  apiFetch<Market[]>('/markets')

export const fetchPrediction = (market_id: string): Promise<Prediction> =>
  apiFetch<Prediction>(`/predict/${encodeURIComponent(market_id)}`)

export const fetchHistory = (market_id: string, limit = 200): Promise<HistoryEntry[]> =>
  apiFetch<HistoryEntry[]>(`/history/${encodeURIComponent(market_id)}?limit=${limit}`)

export const fetchSummary = (): Promise<StatsSummary> =>
  apiFetch<StatsSummary>('/stats/summary')

export const fetchModels = (): Promise<ModelInfo[]> =>
  apiFetch<ModelInfo[]>('/stats/models')

export const fetchHealth = (): Promise<HealthResponse> =>
  apiFetch<HealthResponse>('/health')

export const fetchSlot = (): Promise<SlotResponse> =>
  apiFetch<SlotResponse>('/slot')
```

- [ ] **Step 2: Create `dashboard/src/hooks/useAutoRefresh.ts`**

```typescript
import { useCallback, useEffect, useRef, useState } from 'react'

export interface AutoRefreshState {
  lastRefreshed: Date | null
  isLoading: boolean
  triggerRefresh: () => void
}

export function useAutoRefresh(
  fetchFn: () => Promise<void>,
  intervalMs: number = 30_000
): AutoRefreshState {
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const fetchRef = useRef(fetchFn)
  fetchRef.current = fetchFn

  const run = useCallback(async () => {
    setIsLoading(true)
    try {
      await fetchRef.current()
      setLastRefreshed(new Date())
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    run()
    const id = setInterval(run, intervalMs)
    return () => clearInterval(id)
  }, [run, intervalMs])

  return { lastRefreshed, isLoading, triggerRefresh: run }
}
```

- [ ] **Step 3: Type-check**

```bash
cd /workspace/crypto_analysis/dashboard && npx tsc --noEmit 2>&1
```

Expected: No errors (or only "Cannot find module" if App.tsx not yet created — that's fine, it's a forward reference).

- [ ] **Step 4: Commit**

```bash
cd /workspace/crypto_analysis && git add dashboard/src/api.ts dashboard/src/hooks/ && git commit -m "feat: add dashboard API client and useAutoRefresh hook"
```

---

## Task 8: App.tsx — router + sidebar shell

**Files:**
- Create: `dashboard/src/App.tsx`
- Create: `dashboard/src/App.module.css`

- [ ] **Step 1: Create `dashboard/src/App.module.css`**

```css
.layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

.sidebar {
  width: var(--sidebar-width);
  background: var(--bg-card);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}

.logo {
  padding: 20px 16px 16px;
  font-size: 13px;
  font-weight: 700;
  color: var(--accent);
  border-bottom: 1px solid var(--border);
}

.nav {
  flex: 1;
  padding: 12px 0;
}

.navItem {
  display: block;
  padding: 10px 16px;
  font-size: 13px;
  color: var(--text-muted);
  transition: color 0.15s, background 0.15s;
}

.navItem:hover {
  color: var(--text);
  background: rgba(124,58,237,0.08);
}

.navItemActive {
  color: var(--text);
  background: rgba(124,58,237,0.18);
  border-left: 3px solid var(--accent);
}

.sidebarFooter {
  padding: 12px 16px;
  font-size: 11px;
  color: var(--text-muted);
  border-top: 1px solid var(--border);
  line-height: 1.8;
}

.slotBadge {
  font-weight: 700;
  text-transform: uppercase;
  color: var(--accent);
}

.main {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.topbar {
  padding: 12px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--border);
  background: var(--bg-card);
  flex-shrink: 0;
}

.topbarTitle {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-label);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.topbarRight {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  color: var(--text-muted);
}

.content {
  flex: 1;
  padding: 24px;
  overflow-y: auto;
}
```

- [ ] **Step 2: Create `dashboard/src/App.tsx`**

```typescript
import { useEffect, useState } from 'react'
import { BrowserRouter, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import styles from './App.module.css'
import { fetchSlot } from './api'
import Signals from './views/Signals'
import Accuracy from './views/Accuracy'
import Models from './views/Models'
import System from './views/System'

const REFRESH_INTERVAL_MS = 30_000

const VIEW_TITLES: Record<string, string> = {
  '/': 'Live Signals',
  '/accuracy': 'Historical Accuracy',
  '/models': 'Model Health',
  '/system': 'System Health',
}

function Topbar({ onRefresh, lastRefreshed, isLoading }: {
  onRefresh: () => void
  lastRefreshed: Date | null
  isLoading: boolean
}) {
  const location = useLocation()
  const title = VIEW_TITLES[location.pathname] ?? ''
  return (
    <div className={styles.topbar}>
      <span className={styles.topbarTitle}>{title}</span>
      <div className={styles.topbarRight}>
        {lastRefreshed && (
          <span>Updated {lastRefreshed.toLocaleTimeString()}</span>
        )}
        <button onClick={onRefresh} disabled={isLoading}>
          {isLoading ? '...' : '↺ Refresh'}
        </button>
      </div>
    </div>
  )
}

function Sidebar({ slot }: { slot: string }) {
  const navItems = [
    { to: '/', label: '📡 Signals' },
    { to: '/accuracy', label: '📊 Accuracy' },
    { to: '/models', label: '🧠 Models' },
    { to: '/system', label: '💚 System' },
  ]
  return (
    <aside className={styles.sidebar}>
      <div className={styles.logo}>⬡ Kalshi Analytics</div>
      <nav className={styles.nav}>
        {navItems.map(({ to, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `${styles.navItem} ${isActive ? styles.navItemActive : ''}`
            }
          >
            {label}
          </NavLink>
        ))}
      </nav>
      <div className={styles.sidebarFooter}>
        <div>Slot: <span className={styles.slotBadge}>{slot}</span></div>
        <div>Refresh: 30s</div>
      </div>
    </aside>
  )
}

// RefreshContext lets child views expose their refresh/loading state to the topbar
import { createContext, useContext } from 'react'
export const RefreshContext = createContext<{
  setRefreshFn: (fn: () => void) => void
  setIsLoading: (v: boolean) => void
  setLastRefreshed: (d: Date | null) => void
}>({
  setRefreshFn: () => {},
  setIsLoading: () => {},
  setLastRefreshed: () => {},
})
export const useRefreshContext = () => useContext(RefreshContext)

export default function App() {
  const [slot, setSlot] = useState('—')
  const [refreshFn, setRefreshFn] = useState<() => void>(() => () => {})
  const [isLoading, setIsLoading] = useState(false)
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null)

  useEffect(() => {
    fetchSlot().then(r => setSlot(r.slot)).catch(() => {})
  }, [])

  return (
    <BrowserRouter>
      <RefreshContext.Provider value={{
        setRefreshFn: fn => setRefreshFn(() => fn),
        setIsLoading,
        setLastRefreshed,
      }}>
        <div className={styles.layout}>
          <Sidebar slot={slot} />
          <div className={styles.main}>
            <Topbar
              onRefresh={refreshFn}
              lastRefreshed={lastRefreshed}
              isLoading={isLoading}
            />
            <div className={styles.content}>
              <Routes>
                <Route path="/" element={<Signals intervalMs={REFRESH_INTERVAL_MS} />} />
                <Route path="/accuracy" element={<Accuracy intervalMs={REFRESH_INTERVAL_MS} />} />
                <Route path="/models" element={<Models intervalMs={REFRESH_INTERVAL_MS} />} />
                <Route path="/system" element={<System intervalMs={REFRESH_INTERVAL_MS} />} />
              </Routes>
            </div>
          </div>
        </div>
      </RefreshContext.Provider>
    </BrowserRouter>
  )
}
```

- [ ] **Step 3: Build to verify no TypeScript errors so far**

```bash
cd /workspace/crypto_analysis/dashboard && npm run build 2>&1 | tail -20
```

Expected: Build may fail because view files don't exist yet — that's fine. The important thing is no errors in App.tsx, api.ts, or the hook. If errors appear in those files, fix them before proceeding.

- [ ] **Step 4: Commit**

```bash
cd /workspace/crypto_analysis && git add dashboard/src/App.tsx dashboard/src/App.module.css && git commit -m "feat: add dashboard App shell with sidebar and router"
```

---

## Task 9: Signals.tsx — Live Signals view

**Files:**
- Create: `dashboard/src/views/Signals.tsx`
- Create: `dashboard/src/views/Signals.module.css`

- [ ] **Step 1: Create `dashboard/src/views/Signals.module.css`**

```css
.confBar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.confTrack {
  width: 80px;
  height: 6px;
  background: var(--border);
  border-radius: 3px;
  overflow: hidden;
}

.confFill {
  height: 100%;
  border-radius: 3px;
  background: var(--accent);
}

.up { color: var(--green); font-weight: 700; }
.down { color: var(--red); font-weight: 700; }
.muted { color: var(--text-muted); }
```

- [ ] **Step 2: Create `dashboard/src/views/Signals.tsx`**

```typescript
import { useCallback, useEffect, useState } from 'react'
import { useRefreshContext } from '../App'
import { fetchMarkets, fetchPrediction, Market, Prediction } from '../api'
import { useAutoRefresh } from '../hooks/useAutoRefresh'
import styles from './Signals.module.css'

interface SignalRow {
  market: Market
  prediction: Prediction | null
  error: boolean
}

export default function Signals({ intervalMs }: { intervalMs: number }) {
  const [rows, setRows] = useState<SignalRow[]>([])
  const [fetchError, setFetchError] = useState<string | null>(null)
  const ctx = useRefreshContext()

  const load = useCallback(async () => {
    try {
      const markets = await fetchMarkets()
      const settled = await Promise.allSettled(
        markets.map(m => fetchPrediction(m.market_id))
      )
      setRows(markets.map((m, i) => ({
        market: m,
        prediction: settled[i].status === 'fulfilled' ? settled[i].value : null,
        error: settled[i].status === 'rejected',
      })))
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

  const activeCount = rows.filter(r => r.prediction).length
  const avgConf = rows.length
    ? rows.filter(r => r.prediction).reduce((s, r) => s + (r.prediction?.confidence ?? 0), 0) /
      Math.max(1, rows.filter(r => r.prediction).length)
    : 0
  const lowConfCount = rows.filter(r => r.prediction?.low_confidence).length
  const lastIngestAge = rows.length
    ? Math.min(...rows.filter(r => r.prediction?.feature_age_seconds != null)
        .map(r => r.prediction!.feature_age_seconds!))
    : null

  return (
    <div>
      {fetchError && <div className="error-banner">{fetchError}</div>}
      <div className="stat-grid">
        <div className="stat-card">
          <div className="stat-label">Active Markets</div>
          <div className="stat-value">{activeCount}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Last Ingest Age</div>
          <div className="stat-value">{lastIngestAge != null ? `${lastIngestAge}s` : '—'}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Avg Confidence</div>
          <div className="stat-value">{(avgConf * 100).toFixed(1)}%</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Low-Conf Count</div>
          <div className="stat-value">{lowConfCount}</div>
        </div>
      </div>

      {rows.length === 0 && !fetchError && (
        <div className="placeholder">No active markets</div>
      )}

      {rows.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Market</th>
              <th>Signal</th>
              <th>Confidence</th>
              <th>Status</th>
              <th>Closes In</th>
              <th>Feature Age</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ market, prediction, error }) => (
              <tr key={market.market_id}>
                <td>
                  <div>{market.ticker}</div>
                  <div className={styles.muted} style={{ fontSize: 11 }}>{market.market_id}</div>
                </td>
                <td>
                  {error && <span className="badge badge-err">ERROR</span>}
                  {!error && !prediction && <span className="badge badge-warn">NO PRED</span>}
                  {prediction && (
                    <span className={prediction.direction === 'UP' ? styles.up : styles.down}>
                      {prediction.direction === 'UP' ? '↑ UP' : '↓ DOWN'}
                    </span>
                  )}
                </td>
                <td>
                  {prediction && (
                    <div className={styles.confBar}>
                      <div className={styles.confTrack}>
                        <div
                          className={styles.confFill}
                          style={{ width: `${prediction.confidence * 100}%` }}
                        />
                      </div>
                      <span>{(prediction.confidence * 100).toFixed(0)}%</span>
                    </div>
                  )}
                </td>
                <td>
                  {prediction && (
                    <span className={`badge ${prediction.low_confidence ? 'badge-warn' : 'badge-ok'}`}>
                      {prediction.low_confidence ? 'LOW CONF' : 'MODEL OK'}
                    </span>
                  )}
                </td>
                <td className={styles.muted}>
                  {market.minutes_to_close != null ? `${market.minutes_to_close}m` : '—'}
                </td>
                <td className={styles.muted}>
                  {prediction?.feature_age_seconds != null
                    ? `${prediction.feature_age_seconds}s`
                    : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Commit**

```bash
cd /workspace/crypto_analysis && git add dashboard/src/views/Signals.tsx dashboard/src/views/Signals.module.css && git commit -m "feat: add Signals view"
```

---

## Task 10: Accuracy.tsx — Historical Accuracy view

**Files:**
- Create: `dashboard/src/views/Accuracy.tsx`
- Create: `dashboard/src/views/Accuracy.module.css`

- [ ] **Step 1: Create `dashboard/src/views/Accuracy.module.css`**

```css
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
```

- [ ] **Step 2: Create `dashboard/src/views/Accuracy.tsx`**

```typescript
import { useCallback, useEffect, useState } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  AreaChart, Area, CartesianGrid,
} from 'recharts'
import { useRefreshContext } from '../App'
import { fetchHistory, fetchMarkets, fetchSummary, HistoryEntry, StatsSummary } from '../api'
import { useAutoRefresh } from '../hooks/useAutoRefresh'
import styles from './Accuracy.module.css'

interface MarketBar {
  ticker: string
  winRate: number
}

interface RollingPoint {
  hour: string
  accuracy: number
}

function buildRolling24h(allHistory: HistoryEntry[]): RollingPoint[] {
  const now = Date.now()
  const points: RollingPoint[] = []
  for (let h = 23; h >= 0; h--) {
    const windowEnd = now - h * 3_600_000
    const windowStart = windowEnd - 3_600_000
    const bucket = allHistory.filter(e => {
      const t = new Date(e.ts).getTime()
      return t >= windowStart && t < windowEnd
    })
    if (bucket.length === 0) continue
    const correct = bucket.filter(e => e.correct).length
    points.push({
      hour: new Date(windowEnd).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      accuracy: Math.round((correct / bucket.length) * 100),
    })
  }
  return points
}

export default function Accuracy({ intervalMs }: { intervalMs: number }) {
  const [summary, setSummary] = useState<StatsSummary | null>(null)
  const [barData, setBarData] = useState<MarketBar[]>([])
  const [rollingData, setRollingData] = useState<RollingPoint[]>([])
  const [fetchError, setFetchError] = useState<string | null>(null)
  const ctx = useRefreshContext()

  const load = useCallback(async () => {
    try {
      const [sum, markets] = await Promise.all([fetchSummary(), fetchMarkets()])
      setSummary(sum)
      setBarData(
        sum.markets.map(m => ({ ticker: m.ticker, winRate: Math.round(m.accuracy * 100) }))
      )
      const histories = await Promise.allSettled(
        markets.map(m => fetchHistory(m.market_id, 200))
      )
      const allHistory: HistoryEntry[] = histories
        .filter((r): r is PromiseFulfilledResult<HistoryEntry[]> => r.status === 'fulfilled')
        .flatMap(r => r.value)
      setRollingData(buildRolling24h(allHistory))
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

  const bestMarket = summary?.markets.reduce(
    (best, m) => (!best || m.accuracy > best.accuracy ? m : best),
    null as (typeof summary.markets)[0] | null
  )

  return (
    <div>
      {fetchError && <div className="error-banner">{fetchError}</div>}
      <div className="stat-grid">
        <div className="stat-card">
          <div className="stat-label">Overall Win Rate</div>
          <div className="stat-value">
            {summary ? `${(summary.overall_accuracy * 100).toFixed(1)}%` : '—'}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Best Market</div>
          <div className="stat-value">{bestMarket?.ticker ?? '—'}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Total Settled</div>
          <div className="stat-value">{summary?.total_settled ?? '—'}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">High-Conf Accuracy</div>
          <div className="stat-value">
            {summary ? `${(summary.high_conf_accuracy * 100).toFixed(1)}%` : '—'}
          </div>
        </div>
      </div>

      {(!summary || summary.total_settled === 0) && !fetchError && (
        <div className="placeholder">No settled predictions yet</div>
      )}

      {summary && summary.total_settled > 0 && (
        <div className={styles.charts}>
          <div className={styles.chartCard}>
            <div className={styles.chartTitle}>Win Rate per Market (last 7d)</div>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={barData} margin={{ top: 4, right: 8, bottom: 4, left: -16 }}>
                <XAxis dataKey="ticker" tick={{ fill: '#94a3b8', fontSize: 11 }} />
                <YAxis domain={[0, 100]} tick={{ fill: '#94a3b8', fontSize: 11 }} />
                <Tooltip
                  contentStyle={{ background: '#1a1d27', border: '1px solid #2a2d3a' }}
                  formatter={(v: number) => [`${v}%`, 'Win Rate']}
                />
                <Bar dataKey="winRate" fill="#7c3aed" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className={styles.chartCard}>
            <div className={styles.chartTitle}>Rolling 24h Accuracy</div>
            {rollingData.length === 0 ? (
              <div className="placeholder" style={{ padding: '60px 0' }}>No data</div>
            ) : (
              <ResponsiveContainer width="100%" height={200}>
                <AreaChart data={rollingData} margin={{ top: 4, right: 8, bottom: 4, left: -16 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3a" />
                  <XAxis dataKey="hour" tick={{ fill: '#94a3b8', fontSize: 10 }} />
                  <YAxis domain={[0, 100]} tick={{ fill: '#94a3b8', fontSize: 11 }} />
                  <Tooltip
                    contentStyle={{ background: '#1a1d27', border: '1px solid #2a2d3a' }}
                    formatter={(v: number) => [`${v}%`, 'Accuracy']}
                  />
                  <Area
                    type="monotone" dataKey="accuracy"
                    stroke="#7c3aed" fill="rgba(124,58,237,0.15)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Commit**

```bash
cd /workspace/crypto_analysis && git add dashboard/src/views/Accuracy.tsx dashboard/src/views/Accuracy.module.css && git commit -m "feat: add Accuracy view with Recharts"
```

---

## Task 11: Models.tsx — Model Health view

**Files:**
- Create: `dashboard/src/views/Models.tsx`
- Create: `dashboard/src/views/Models.module.css`

- [ ] **Step 1: Create `dashboard/src/views/Models.module.css`**

```css
.brier-green { color: var(--green); }
.brier-amber { color: var(--amber); }
.brier-red   { color: var(--red); }
```

- [ ] **Step 2: Create `dashboard/src/views/Models.tsx`**

```typescript
import { useCallback, useEffect, useState } from 'react'
import { useRefreshContext } from '../App'
import { fetchMarkets, fetchModels, ModelInfo } from '../api'
import { useAutoRefresh } from '../hooks/useAutoRefresh'
import styles from './Models.module.css'

function brierClass(score: number): string {
  if (score < 0.22) return styles['brier-green']
  if (score <= 0.27) return styles['brier-amber']
  return styles['brier-red']
}

export default function Models({ intervalMs }: { intervalMs: number }) {
  const [models, setModels] = useState<ModelInfo[]>([])
  const [totalMarkets, setTotalMarkets] = useState(0)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const ctx = useRefreshContext()

  const load = useCallback(async () => {
    try {
      const [mods, markets] = await Promise.all([fetchModels(), fetchMarkets()])
      setModels(mods)
      setTotalMarkets(markets.length)
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

  const bestBrier = models.length
    ? Math.min(...models.map(m => m.brier_score))
    : null
  const lastTrained = models.length
    ? new Date(Math.max(...models.map(m => new Date(m.trained_at).getTime())))
    : null
  const withoutModel = totalMarkets - models.length

  return (
    <div>
      {fetchError && <div className="error-banner">{fetchError}</div>}
      <div className="stat-grid">
        <div className="stat-card">
          <div className="stat-label">Active Models</div>
          <div className="stat-value">{models.length}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Best Brier Score</div>
          <div className="stat-value">{bestBrier != null ? bestBrier.toFixed(3) : '—'}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Last Trained</div>
          <div className="stat-value" style={{ fontSize: 14 }}>
            {lastTrained ? lastTrained.toLocaleDateString() : '—'}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Markets Without Model</div>
          <div className="stat-value">{withoutModel}</div>
        </div>
      </div>

      {models.length === 0 && !fetchError && (
        <div className="placeholder">No active models — run the trainer first</div>
      )}

      {models.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Market</th>
              <th>Version</th>
              <th>Brier Score</th>
              <th>Training Rows</th>
              <th>Trained At</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {models.map(m => (
              <tr key={m.market_id}>
                <td>
                  <div>{m.ticker}</div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{m.market_id}</div>
                </td>
                <td>{m.version}</td>
                <td className={brierClass(m.brier_score)}>{m.brier_score.toFixed(3)}</td>
                <td>{m.training_rows.toLocaleString()}</td>
                <td>{new Date(m.trained_at).toLocaleString()}</td>
                <td>
                  <span className={`badge ${m.is_active ? 'badge-ok' : 'badge-warn'}`}>
                    {m.is_active ? 'ACTIVE' : 'INACTIVE'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Commit**

```bash
cd /workspace/crypto_analysis && git add dashboard/src/views/Models.tsx dashboard/src/views/Models.module.css && git commit -m "feat: add Models health view"
```

---

## Task 12: System.tsx — System Health view

**Files:**
- Create: `dashboard/src/views/System.tsx`
- Create: `dashboard/src/views/System.module.css`

- [ ] **Step 1: Create `dashboard/src/views/System.module.css`**

```css
.cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
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
  grid-column: span 3;
}
```

- [ ] **Step 2: Create `dashboard/src/views/System.tsx`**

The three service cards (Ingestor, Predictor, API) all derive data from the single `/api/health` response — the API is the only service directly reachable through the nginx proxy. Ingestor and predictor status is inferred: if active_markets > 0, the ingestor is considered running; if active_markets > 0, the predictor is assumed healthy (it only becomes unhealthy if no markets have active predictions, which is detectable via the Signals view). This avoids adding proxy-forwarding endpoints to the API.

```typescript
import { useCallback, useEffect, useState } from 'react'
import { useRefreshContext } from '../App'
import { fetchHealth, fetchSlot, HealthResponse } from '../api'
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

export default function System({ intervalMs }: { intervalMs: number }) {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [apiError, setApiError] = useState(false)
  const [slot, setSlot] = useState<string>('—')
  const [fetchError, setFetchError] = useState<string | null>(null)
  const ctx = useRefreshContext()

  const load = useCallback(async () => {
    try {
      const [healthResult, slotResult] = await Promise.allSettled([
        fetchHealth(),
        fetchSlot(),
      ])
      if (healthResult.status === 'fulfilled') {
        setHealth(healthResult.value)
        setApiError(false)
      } else {
        setHealth(null)
        setApiError(true)
      }
      if (slotResult.status === 'fulfilled') setSlot(slotResult.value.slot)
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

- [ ] **Step 3: Commit**

```bash
cd /workspace/crypto_analysis && git add dashboard/src/views/System.tsx dashboard/src/views/System.module.css && git commit -m "feat: add System health view"
```

---

## Task 13: Dashboard Dockerfile + nginx.conf

**Files:**
- Create: `dashboard/nginx.conf`
- Create: `dashboard/Dockerfile`

- [ ] **Step 1: Create `dashboard/nginx.conf`**

```nginx
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;

    # SPA fallback: any unknown path returns index.html
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Proxy /api/* to FastAPI service; strip /api prefix
    location /api/ {
        proxy_pass http://api:8000/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

- [ ] **Step 2: Create `dashboard/Dockerfile`**

```dockerfile
# Stage 1: build
FROM node:20-alpine AS build
WORKDIR /app
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ .
RUN npm run build

# Stage 2: serve with nginx (~25MB final image)
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY dashboard/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

Note: The build context is the repo root (`.`), which is why `COPY dashboard/package.json ...` uses the `dashboard/` prefix. This matches how it's referenced in the compose files.

- [ ] **Step 3: Verify dashboard builds cleanly**

```bash
cd /workspace/crypto_analysis/dashboard && npm run build 2>&1 | tail -20
```

Expected: `dist/index.html` created. Exit code 0. Fix any TypeScript errors before proceeding.

- [ ] **Step 4: Generate package-lock.json (required by Dockerfile `npm ci`)**

```bash
cd /workspace/crypto_analysis/dashboard && npm install --package-lock-only 2>&1 | tail -5
```

Expected: `package-lock.json` created.

- [ ] **Step 5: Commit**

```bash
cd /workspace/crypto_analysis && git add dashboard/nginx.conf dashboard/Dockerfile dashboard/package-lock.json && git commit -m "feat: add dashboard Dockerfile and nginx config"
```

---

## Task 14: Final wiring — update .gitignore, verify full build

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add dashboard build artifacts to .gitignore**

Append to `.gitignore`:

```
# Dashboard build output
dashboard/dist/
dashboard/node_modules/
```

- [ ] **Step 2: Run full Python test suite**

```bash
cd /workspace/crypto_analysis && source venv/bin/activate && pytest tests/ -v 2>&1 | tail -30
```

Expected: All tests PASS. Fix any failures before proceeding.

- [ ] **Step 3: Verify both compose files still parse after all changes**

```bash
cd /workspace/crypto_analysis && docker compose -f docker-compose.infra.yml config --quiet && docker compose -f docker-compose.blue.yml config --quiet && docker compose -f docker-compose.green.yml config --quiet && echo "All compose files valid"
```

Expected: `All compose files valid`

- [ ] **Step 4: Verify deploy script syntax**

```bash
bash -n /workspace/crypto_analysis/scripts/deploy.sh && echo "deploy.sh syntax OK"
```

Expected: `deploy.sh syntax OK`

- [ ] **Step 5: Commit**

```bash
cd /workspace/crypto_analysis && git add .gitignore && git commit -m "chore: ignore dashboard dist and node_modules"
```

---

## Final notes

**To start the infra layer on the server:**
```bash
docker compose -f docker-compose.infra.yml up -d
```

**First deploy (blue):**
```bash
CADDY_SNIPPET_PATH=/absolute/path/to/caddy/active-slot.caddy \
CADDYFILE_PATH=/absolute/path/to/caddy/Caddyfile \
  ./scripts/deploy.sh blue
```

**Subsequent deploy (cut to green):**
```bash
CADDY_SNIPPET_PATH=... CADDYFILE_PATH=... ./scripts/deploy.sh green
```

**To run the trainer (on either slot's volume):**
```bash
docker compose -f docker-compose.blue.yml --profile training run --rm trainer
```
