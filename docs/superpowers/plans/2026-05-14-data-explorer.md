# Data Explorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/data` page to the dashboard with three tabs — Raw Data (paginated raw_features table), Feature Vectors (computed 25-feature table per settled prediction), and Analysis (column stats + correlations + feature importance).

**Architecture:** `build_feature_vector` and `FEATURE_NAMES` move to `shared/feature_builder.py` so the API container can use them alongside the predictor. Five new endpoints are added to `api/main.py`. A new `DataExplorer` React component handles all three tabs behind a shared filter bar (series ticker + date range). Filter state lives in component state; no URL persistence needed.

**Tech Stack:** FastAPI, SQLAlchemy, numpy, XGBoost, joblib (Python); React + TypeScript + CSS Modules; recharts already present in dashboard.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `shared/feature_builder.py` | `FEATURE_NAMES` + `build_feature_vector` (moved from predictor) |
| Modify | `predictor/feature_builder.py` | Re-export from shared for backward compat |
| Modify | `trainer/dataset.py` | Update import to use `shared.feature_builder` |
| Modify | `api/requirements.txt` | Add numpy, xgboost, joblib |
| Modify | `api/main.py` | Add 5 new `/data/*` endpoints + `_get_feature_matrix` helper |
| Modify | `docker-compose.green.yml` | Mount `models` volume on `api-green` |
| Modify | `docker-compose.blue.yml` | Mount `models` volume on `api-blue` |
| Create | `tests/test_api_data.py` | Tests for all 5 new endpoints |
| Modify | `dashboard/src/api.ts` | 5 new fetch functions + TypeScript interfaces |
| Modify | `dashboard/src/App.tsx` | Add `/data` route, nav item, view title |
| Create | `dashboard/src/views/DataExplorer.tsx` | Three-tab view (filter bar + Raw/FV/Analysis) |
| Create | `dashboard/src/views/DataExplorer.module.css` | Scoped styles |

---

## Task 1: Move `build_feature_vector` to `shared/`

**Files:**
- Create: `shared/feature_builder.py`
- Modify: `predictor/feature_builder.py`
- Modify: `trainer/dataset.py`
- Modify: `api/requirements.txt`

- [ ] **Step 1: Create `shared/feature_builder.py`** by copying the exact contents of `predictor/feature_builder.py` verbatim (it only depends on `shared.orm`, `numpy`, `math`, `datetime`):

```python
# shared/feature_builder.py
import math
import numpy as np
from datetime import datetime, timezone, timedelta
from shared.orm import RawFeature

FEATURE_NAMES = [
    "price_momentum_1m",
    "price_momentum_5m",
    "price_momentum_15m",
    "volatility_5m",
    "volatility_roc",
    "vwap_deviation_15m",
    "candle_body_ratio",
    "volume_momentum_5m",
    "book_imbalance_latest",
    "book_imbalance_trend",
    "bid_depth_1pct",
    "ask_depth_1pct",
    "kalshi_yes_price",
    "kalshi_no_price",
    "kalshi_price_momentum_5m",
    "kalshi_deviation",
    "kalshi_volume_zscore",
    "kalshi_volume_momentum",
    "minutes_to_close",
    "sin_hour",
    "cos_hour",
    "sin_dow",
    "cos_dow",
    "is_weekend",
    "consecutive_direction",
]

_MIN_ROWS = 10


def build_feature_vector(
    rows: list,
    minutes_to_close: float,
    ts: datetime,
) -> tuple[np.ndarray, list[str]] | None:
    if len(rows) < _MIN_ROWS:
        return None

    rows = sorted(rows, key=lambda r: r.ts)
    latest = rows[-1]

    def _f(val, default=0.0) -> float:
        try:
            return float(val) if val is not None else default
        except (TypeError, ValueError):
            return default

    closes = np.array([_f(r.price_close) for r in rows])
    volumes = np.array([_f(r.volume) for r in rows])
    now = ts

    def _ts_utc(ts) -> datetime:
        if ts is None:
            return now
        if getattr(ts, "tzinfo", None) is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts

    def _within(minutes: float) -> list:
        cutoff = now - timedelta(minutes=minutes)
        return [r for r in rows if _ts_utc(r.ts) >= cutoff]

    def _momentum(minutes: float) -> float:
        window = _within(minutes)
        if not window:
            return 0.0
        first_price = _f(window[0].price_close)
        last_price = _f(latest.price_close)
        return (last_price - first_price) / first_price if first_price != 0 else 0.0

    def _volatility(window_rows: list) -> float:
        prices = np.array([_f(r.price_close) for r in window_rows])
        if len(prices) < 2:
            return 0.0
        rets = np.diff(prices) / np.where(prices[:-1] != 0, prices[:-1], 1.0)
        return float(np.std(rets))

    mom_1m = _momentum(1)
    mom_5m = _momentum(5)
    mom_15m = _momentum(15)

    w5 = _within(5)
    w10 = _within(10)
    vol_5m = _volatility(w5)
    vol_10m = _volatility(w10)
    vol_roc = vol_5m - vol_10m

    w15 = _within(15)
    if w15 and volumes[-len(w15):].sum() > 0:
        p15 = np.array([_f(r.price_close) for r in w15])
        v15 = np.array([_f(r.volume) for r in w15])
        vwap = np.dot(p15, v15) / v15.sum() if v15.sum() > 0 else _f(latest.price_close)
        vwap_dev = (_f(latest.price_close) - vwap) / vwap if vwap > 0 else 0.0
    else:
        vwap_dev = 0.0

    hi = _f(latest.price_high)
    lo = _f(latest.price_low)
    op = _f(latest.price_open)
    cl = _f(latest.price_close)
    candle_range = hi - lo
    body_ratio = (cl - op) / candle_range if candle_range > 0 else 0.0

    avg_vol_15m = float(np.mean([_f(r.volume) for r in w15])) if w15 else 0.0
    avg_vol_5m = float(np.mean([_f(r.volume) for r in w5])) if w5 else 0.0
    vol_mom = (avg_vol_5m - avg_vol_15m) / avg_vol_15m if avg_vol_15m > 0 else 0.0

    book_latest = _f(latest.book_imbalance)
    last5_imb = [_f(r.book_imbalance) for r in rows[-5:]]
    book_trend = last5_imb[-1] - last5_imb[0] if len(last5_imb) >= 2 else 0.0
    bid_depth = _f(latest.bid_depth_1pct)
    ask_depth = _f(latest.ask_depth_1pct)

    kalshi_yes = _f(latest.kalshi_yes_price, 0.5)
    kalshi_no = _f(latest.kalshi_no_price, 0.5)

    kalshi_yes_prices = [_f(r.kalshi_yes_price, 0.5) for r in _within(5)]
    if len(kalshi_yes_prices) >= 2:
        kalshi_mom = kalshi_yes_prices[-1] - kalshi_yes_prices[0]
    else:
        kalshi_mom = 0.0

    fair_yes = max(0.01, min(0.99, 0.5 + mom_15m * 5))
    kalshi_dev = kalshi_yes - fair_yes

    kalshi_vols = np.array([_f(r.kalshi_volume) for r in rows])
    kv_mean = float(np.mean(kalshi_vols))
    kv_std = float(np.std(kalshi_vols))
    kalshi_vol_z = (float(_f(latest.kalshi_volume)) - kv_mean) / kv_std if kv_std > 0 else 0.0

    kv_5m = np.mean([_f(r.kalshi_volume) for r in w5]) if w5 else kv_mean
    kalshi_vol_mom = (kv_5m - kv_mean) / kv_mean if kv_mean > 0 else 0.0

    hour = now.hour
    dow = now.weekday()
    sin_hour = math.sin(2 * math.pi * hour / 24)
    cos_hour = math.cos(2 * math.pi * hour / 24)
    sin_dow = math.sin(2 * math.pi * dow / 7)
    cos_dow = math.cos(2 * math.pi * dow / 7)
    is_weekend = 1.0 if dow >= 5 else 0.0

    consecutive = 0
    for r in reversed(rows[-10:]):
        if _f(r.price_close) > _f(r.price_open):
            if consecutive >= 0:
                consecutive += 1
            else:
                break
        else:
            if consecutive <= 0:
                consecutive -= 1
            else:
                break

    vec = np.array([
        mom_1m, mom_5m, mom_15m,
        vol_5m, vol_roc, vwap_dev,
        body_ratio, vol_mom,
        book_latest, book_trend, bid_depth, ask_depth,
        kalshi_yes, kalshi_no, kalshi_mom, kalshi_dev,
        kalshi_vol_z, kalshi_vol_mom,
        minutes_to_close,
        sin_hour, cos_hour, sin_dow, cos_dow, is_weekend,
        float(consecutive),
    ], dtype=float)

    vec = np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0)
    return vec, FEATURE_NAMES
```

- [ ] **Step 2: Replace `predictor/feature_builder.py` with a re-export shim**

```python
# predictor/feature_builder.py
# Re-exports from shared so existing imports in predictor/inference.py and
# trainer/dataset.py continue to work without changes.
from shared.feature_builder import FEATURE_NAMES, build_feature_vector  # noqa: F401
```

- [ ] **Step 3: Update the import in `trainer/dataset.py`** — change line 6 from:

```python
from predictor.feature_builder import build_feature_vector
```

to:

```python
from shared.feature_builder import build_feature_vector
```

- [ ] **Step 4: Add numpy, xgboost, and joblib to `api/requirements.txt`**

```
fastapi==0.111.0
uvicorn==0.29.0
sqlalchemy==2.0.30
psycopg2-binary==2.9.9
pydantic-settings==2.2.1
numpy==1.26.4
xgboost==2.0.3
joblib==1.4.2
```

- [ ] **Step 5: Run all existing tests to confirm nothing broke**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/ -x -q
```

Expected: all tests pass (no failures or import errors).

- [ ] **Step 6: Commit**

```bash
git add shared/feature_builder.py predictor/feature_builder.py trainer/dataset.py api/requirements.txt
git commit -m "refactor: move build_feature_vector to shared/ for API container access"
```

---

## Task 2: Mount models volume on API service

**Files:**
- Modify: `docker-compose.green.yml`
- Modify: `docker-compose.blue.yml`

- [ ] **Step 1: Add `models` volume mount to `api-green` in `docker-compose.green.yml`**

Find the `api-green` service block and add the `volumes` key:

```yaml
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
    volumes:
      - models:/app/models
    ports:
      - "8012:8000"
    networks:
      infra:
        aliases:
          - api
```

- [ ] **Step 2: Apply the same change to `api-blue` in `docker-compose.blue.yml`**

Open `docker-compose.blue.yml`, find the `api-blue` service, add:

```yaml
    volumes:
      - models:/app/models
```

(The blue compose file follows the same structure; api-blue is on port 8011.)

- [ ] **Step 3: Commit**

```bash
git add docker-compose.green.yml docker-compose.blue.yml
git commit -m "chore: mount models volume on api containers for feature-importance endpoint"
```

---

## Task 3: API — `/data/raw-features` endpoint + tests

**Files:**
- Create: `tests/test_api_data.py`
- Modify: `api/main.py`

- [ ] **Step 1: Create `tests/test_api_data.py` with helper fixtures and the raw-features test**

```python
# tests/test_api_data.py
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from shared.orm import Market, RawFeature, Prediction, ModelRegistry


def _make_app(db_session):
    import api.main as m
    return TestClient(m.create_app(lambda: db_session))


def _seed_market(session, market_id="KXBTCUSD-D1", ticker="KXBTCUSD"):
    now = datetime.now(timezone.utc)
    m = Market(
        market_id=market_id, ticker=ticker, status="active",
        close_time=now + timedelta(minutes=10),
        discovered_at=now, updated_at=now,
    )
    session.add(m)
    session.flush()
    return m


def _seed_raw_features(session, market_id, n=15, base_price=100_000.0):
    """Seed n RawFeature rows 30 seconds apart ending at now."""
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(n):
        ts = now - timedelta(seconds=(n - i) * 30)
        r = RawFeature(
            market_id=market_id, ts=ts,
            price_open=base_price + i, price_high=base_price + i + 10,
            price_low=base_price + i - 10, price_close=base_price + i + 5,
            volume=1.0 + i * 0.1,
            bid_depth_1pct=500.0, ask_depth_1pct=480.0,
            book_imbalance=0.05 * (1 if i % 2 == 0 else -1),
            kalshi_yes_price=0.55, kalshi_no_price=0.45, kalshi_volume=1000.0,
            price_momentum_1m=0.001, price_momentum_5m=0.003,
            price_momentum_15m=0.005, volatility_5m=0.002,
        )
        session.add(r)
        rows.append(r)
    session.flush()
    return rows


def test_raw_features_returns_rows(db_session):
    _seed_market(db_session, "KXBTCUSD-RF1", "KXBTCUSD")
    _seed_raw_features(db_session, "KXBTCUSD-RF1", n=5)
    client = _make_app(db_session)
    resp = client.get("/data/raw-features?series_ticker=KXBTCUSD")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert len(body["rows"]) == 5
    assert body["page"] == 1
    assert "ts" in body["rows"][0]
    assert "price_close" in body["rows"][0]


def test_raw_features_pagination(db_session):
    _seed_market(db_session, "KXBTCUSD-RF2", "KXBTCUSD")
    _seed_raw_features(db_session, "KXBTCUSD-RF2", n=15)
    client = _make_app(db_session)
    resp = client.get("/data/raw-features?series_ticker=KXBTCUSD&page=1&page_size=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 15
    assert len(body["rows"]) == 10
    resp2 = client.get("/data/raw-features?series_ticker=KXBTCUSD&page=2&page_size=10")
    assert resp2.status_code == 200
    assert len(resp2.json()["rows"]) == 5


def test_raw_features_filters_by_ticker(db_session):
    _seed_market(db_session, "KXBTCUSD-RF3", "KXBTCUSD")
    _seed_market(db_session, "KXETHUSD-RF3", "KXETHUSD")
    _seed_raw_features(db_session, "KXBTCUSD-RF3", n=3)
    _seed_raw_features(db_session, "KXETHUSD-RF3", n=7)
    client = _make_app(db_session)
    resp = client.get("/data/raw-features?series_ticker=KXETHUSD")
    assert resp.status_code == 200
    assert resp.json()["total"] == 7


def test_raw_features_rejects_invalid_sort_by(db_session):
    client = _make_app(db_session)
    resp = client.get("/data/raw-features?series_ticker=KXBTCUSD&sort_by=DROP+TABLE")
    assert resp.status_code == 400
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_api_data.py::test_raw_features_returns_rows -v
```

Expected: `FAILED` — `404 Not Found` (endpoint doesn't exist yet).

- [ ] **Step 3: Add the `/data/raw-features` endpoint to `api/main.py`**

Add `import numpy as np` to the top-level imports in `api/main.py` (after the existing imports). Then add this route inside `create_app`, after the existing `/stats/training` route:

```python
    _RAW_SORTABLE = {
        "ts", "price_open", "price_high", "price_low", "price_close",
        "volume", "bid_depth_1pct", "ask_depth_1pct", "book_imbalance",
        "kalshi_yes_price", "kalshi_no_price", "kalshi_volume",
        "price_momentum_1m", "price_momentum_5m", "price_momentum_15m",
        "volatility_5m",
    }

    @app.get("/data/raw-features")
    def get_raw_features(
        series_ticker: str,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        page: int = 1,
        page_size: int = 100,
        sort_by: str = "ts",
        sort_dir: str = "desc",
        session: Session = Depends(_get_db),
    ):
        if sort_by not in _RAW_SORTABLE:
            raise HTTPException(status_code=400, detail=f"sort_by must be one of {sorted(_RAW_SORTABLE)}")
        if sort_dir not in ("asc", "desc"):
            raise HTTPException(status_code=400, detail="sort_dir must be 'asc' or 'desc'")

        from shared.orm import RawFeature as RF
        q = (
            session.query(RF)
            .join(Market, RF.market_id == Market.market_id)
            .filter(Market.ticker == series_ticker)
        )
        if from_ts:
            q = q.filter(RF.ts >= from_ts)
        if to_ts:
            q = q.filter(RF.ts <= to_ts)

        total = q.count()
        col = getattr(RF, sort_by)
        rows = (
            q.order_by(col.desc() if sort_dir == "desc" else col.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        def _f(v):
            return float(v) if v is not None else None

        return {
            "rows": [
                {
                    "id": r.id, "ts": r.ts.isoformat(), "market_id": r.market_id,
                    "price_open": _f(r.price_open), "price_high": _f(r.price_high),
                    "price_low": _f(r.price_low), "price_close": _f(r.price_close),
                    "volume": _f(r.volume),
                    "bid_depth_1pct": _f(r.bid_depth_1pct),
                    "ask_depth_1pct": _f(r.ask_depth_1pct),
                    "book_imbalance": _f(r.book_imbalance),
                    "kalshi_yes_price": _f(r.kalshi_yes_price),
                    "kalshi_no_price": _f(r.kalshi_no_price),
                    "kalshi_volume": _f(r.kalshi_volume),
                    "price_momentum_1m": _f(r.price_momentum_1m),
                    "price_momentum_5m": _f(r.price_momentum_5m),
                    "price_momentum_15m": _f(r.price_momentum_15m),
                    "volatility_5m": _f(r.volatility_5m),
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_api_data.py -k "raw_features" -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add api/main.py tests/test_api_data.py
git commit -m "feat: add /data/raw-features API endpoint"
```

---

## Task 4: API — `/data/feature-vectors` endpoint

**Files:**
- Modify: `api/main.py`
- Modify: `tests/test_api_data.py`

- [ ] **Step 1: Add the feature-vectors test to `tests/test_api_data.py`**

```python
def _seed_settled_prediction(session, market_id, ts, outcome=1):
    p = Prediction(
        market_id=market_id, ts=ts,
        direction="UP" if outcome == 1 else "DOWN",
        confidence=0.72, low_confidence=False, model_version="v1",
        settled_at=ts + timedelta(minutes=10),
        actual_outcome=outcome,
    )
    session.add(p)
    session.flush()
    return p


def test_feature_vectors_returns_rows(db_session):
    now = datetime.now(timezone.utc)
    _seed_market(db_session, "KXBTCUSD-FV1", "KXBTCUSD")
    _seed_raw_features(db_session, "KXBTCUSD-FV1", n=15)
    pred_ts = now - timedelta(seconds=30)
    _seed_settled_prediction(db_session, "KXBTCUSD-FV1", pred_ts)
    client = _make_app(db_session)
    resp = client.get("/data/feature-vectors?series_ticker=KXBTCUSD")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["actual_outcome"] == 1
    assert "features" in row
    assert "price_momentum_1m" in row["features"]
    assert len(row["features"]) == 25


def test_feature_vectors_skips_rows_with_insufficient_context(db_session):
    now = datetime.now(timezone.utc)
    _seed_market(db_session, "KXBTCUSD-FV2", "KXBTCUSD")
    # Only 3 raw rows — below _MIN_ROWS=10, so feature vector can't be built
    _seed_raw_features(db_session, "KXBTCUSD-FV2", n=3)
    pred_ts = now - timedelta(seconds=30)
    _seed_settled_prediction(db_session, "KXBTCUSD-FV2", pred_ts)
    client = _make_app(db_session)
    resp = client.get("/data/feature-vectors?series_ticker=KXBTCUSD")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["rows"]) == 0
    assert body["skipped"] == 1


def test_feature_vectors_page_size_capped_at_50(db_session):
    client = _make_app(db_session)
    resp = client.get("/data/feature-vectors?series_ticker=KXBTCUSD&page_size=200")
    assert resp.status_code == 200
    assert resp.json()["page_size"] == 50
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_api_data.py -k "feature_vectors" -v
```

Expected: `FAILED` — 404.

- [ ] **Step 3: Add the `/data/feature-vectors` endpoint to `api/main.py`**, inside `create_app` after the raw-features route:

```python
    @app.get("/data/feature-vectors")
    def get_feature_vectors(
        series_ticker: str,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
        sort_by: str = "ts",
        sort_dir: str = "desc",
        session: Session = Depends(_get_db),
    ):
        from shared.feature_builder import build_feature_vector, FEATURE_NAMES
        from shared.orm import RawFeature as RF

        page_size = min(page_size, 50)
        _FV_SORTABLE = {"ts", "confidence", "actual_outcome"}
        if sort_by not in _FV_SORTABLE:
            raise HTTPException(status_code=400, detail=f"sort_by must be one of {sorted(_FV_SORTABLE)}")
        if sort_dir not in ("asc", "desc"):
            raise HTTPException(status_code=400, detail="sort_dir must be 'asc' or 'desc'")

        q = (
            session.query(Prediction)
            .join(Market, Prediction.market_id == Market.market_id)
            .filter(Market.ticker == series_ticker, Prediction.actual_outcome != None)  # noqa: E711
        )
        if from_ts:
            q = q.filter(Prediction.ts >= from_ts)
        if to_ts:
            q = q.filter(Prediction.ts <= to_ts)

        total = q.count()
        col = getattr(Prediction, sort_by)
        preds = (
            q.order_by(col.desc() if sort_dir == "desc" else col.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        rows = []
        skipped = 0
        for pred in preds:
            pred_ts = pred.ts if pred.ts.tzinfo else pred.ts.replace(tzinfo=timezone.utc)
            context = (
                session.query(RF)
                .filter(RF.market_id == pred.market_id, RF.ts <= pred_ts)
                .order_by(RF.ts.desc())
                .limit(40)
                .all()
            )
            market = session.get(Market, pred.market_id)
            minutes_to_close = 7.5
            if market and market.close_time:
                ct = market.close_time if market.close_time.tzinfo else market.close_time.replace(tzinfo=timezone.utc)
                minutes_to_close = max(0.0, (ct - pred_ts).total_seconds() / 60)
            result = build_feature_vector(context, minutes_to_close=minutes_to_close, ts=pred_ts)
            if result is None:
                skipped += 1
                continue
            vec, _ = result
            rows.append({
                "ts": pred.ts.isoformat(),
                "direction": pred.direction,
                "confidence": float(pred.confidence),
                "actual_outcome": int(pred.actual_outcome),
                "features": {name: float(val) for name, val in zip(FEATURE_NAMES, vec)},
            })

        return {"rows": rows, "total": total, "page": page, "page_size": page_size, "skipped": skipped}
```

- [ ] **Step 4: Run tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_api_data.py -k "feature_vectors" -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add api/main.py tests/test_api_data.py
git commit -m "feat: add /data/feature-vectors API endpoint"
```

---

## Task 5: API — `/data/stats` and `/data/correlations` endpoints

**Files:**
- Modify: `api/main.py`
- Modify: `tests/test_api_data.py`

- [ ] **Step 1: Add tests for stats and correlations to `tests/test_api_data.py`**

```python
def _seed_full_training_slice(session, ticker="KXBTCUSD", prefix="SL", n_preds=6):
    """Seed n_preds settled predictions each with 15 raw_feature context rows."""
    now = datetime.now(timezone.utc)
    market_id = f"{ticker}-{prefix}1"
    _seed_market(session, market_id, ticker)
    _seed_raw_features(session, market_id, n=15)
    for i in range(n_preds):
        pred_ts = now - timedelta(seconds=(n_preds - i) * 30)
        _seed_settled_prediction(session, market_id, pred_ts, outcome=i % 2)
    return market_id


def test_stats_returns_25_features(db_session):
    _seed_full_training_slice(db_session, "KXBTCUSD", "ST1")
    client = _make_app(db_session)
    resp = client.get("/data/stats?series_ticker=KXBTCUSD")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 25
    names = [row["feature"] for row in body]
    assert "price_momentum_1m" in names
    assert "kalshi_deviation" in names
    first = body[0]
    assert all(k in first for k in ("feature", "mean", "std", "min", "max", "null_count"))


def test_stats_returns_empty_when_insufficient_data(db_session):
    _seed_market(db_session, "KXBTCUSD-ST2", "KXBTCUSD")
    client = _make_app(db_session)
    resp = client.get("/data/stats?series_ticker=KXBTCUSD")
    assert resp.status_code == 200
    assert resp.json() == []


def test_correlations_returns_25_entries(db_session):
    _seed_full_training_slice(db_session, "KXBTCUSD", "CR1")
    client = _make_app(db_session)
    resp = client.get("/data/correlations?series_ticker=KXBTCUSD")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 25
    assert all("feature" in row and "r" in row for row in body)
    # Should be sorted by |r| descending
    abs_rs = [abs(row["r"]) for row in body]
    assert abs_rs == sorted(abs_rs, reverse=True)


def test_correlations_returns_empty_when_insufficient_data(db_session):
    _seed_market(db_session, "KXBTCUSD-CR2", "KXBTCUSD")
    client = _make_app(db_session)
    resp = client.get("/data/correlations?series_ticker=KXBTCUSD")
    assert resp.status_code == 200
    assert resp.json() == []
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_api_data.py -k "stats or correlations" -v
```

Expected: `FAILED` — 404.

- [ ] **Step 3: Add the `_get_feature_matrix` helper and both endpoints to `api/main.py`**

Add the helper function inside `create_app` (before the route definitions), after the `_get_db` closure:

```python
    def _get_feature_matrix(session, series_ticker, from_ts, to_ts):
        import numpy as np
        from shared.feature_builder import build_feature_vector
        from shared.orm import RawFeature as RF

        q = (
            session.query(Prediction)
            .join(Market, Prediction.market_id == Market.market_id)
            .filter(Market.ticker == series_ticker, Prediction.actual_outcome != None)  # noqa: E711
        )
        if from_ts:
            q = q.filter(Prediction.ts >= from_ts)
        if to_ts:
            q = q.filter(Prediction.ts <= to_ts)
        preds = q.order_by(Prediction.ts.asc()).all()

        if len(preds) < 5:
            return None, None

        raw_q = (
            session.query(RF)
            .join(Market, RF.market_id == Market.market_id)
            .filter(Market.ticker == series_ticker)
        )
        if from_ts:
            raw_q = raw_q.filter(RF.ts >= from_ts)
        all_raw = raw_q.order_by(RF.ts.asc()).all()
        raw_by_market: dict = {}
        for r in all_raw:
            raw_by_market.setdefault(r.market_id, []).append(r)

        X, y = [], []
        for pred in preds:
            pred_ts = pred.ts if pred.ts.tzinfo else pred.ts.replace(tzinfo=timezone.utc)
            context = [
                r for r in raw_by_market.get(pred.market_id, [])
                if (r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc)) <= pred_ts
            ][-40:]
            market = session.get(Market, pred.market_id)
            minutes_to_close = 7.5
            if market and market.close_time:
                ct = market.close_time if market.close_time.tzinfo else market.close_time.replace(tzinfo=timezone.utc)
                minutes_to_close = max(0.0, (ct - pred_ts).total_seconds() / 60)
            result = build_feature_vector(context, minutes_to_close=minutes_to_close, ts=pred_ts)
            if result is None:
                continue
            vec, _ = result
            X.append(vec)
            y.append(int(pred.actual_outcome))

        if len(X) < 5:
            return None, None
        return np.array(X), np.array(y)
```

Then add the two route handlers inside `create_app`:

```python
    @app.get("/data/stats")
    def get_data_stats(
        series_ticker: str,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        session: Session = Depends(_get_db),
    ):
        import numpy as np
        from shared.feature_builder import FEATURE_NAMES

        X, _ = _get_feature_matrix(session, series_ticker, from_ts, to_ts)
        if X is None:
            return []
        return [
            {
                "feature": name,
                "mean": round(float(np.mean(X[:, i])), 6),
                "std": round(float(np.std(X[:, i])), 6),
                "min": round(float(np.min(X[:, i])), 6),
                "max": round(float(np.max(X[:, i])), 6),
                "null_count": 0,
            }
            for i, name in enumerate(FEATURE_NAMES)
        ]

    @app.get("/data/correlations")
    def get_data_correlations(
        series_ticker: str,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        session: Session = Depends(_get_db),
    ):
        import numpy as np
        from shared.feature_builder import FEATURE_NAMES

        X, y = _get_feature_matrix(session, series_ticker, from_ts, to_ts)
        if X is None:
            return []
        results = []
        for i, name in enumerate(FEATURE_NAMES):
            col = X[:, i]
            r = float(np.corrcoef(col, y)[0, 1]) if np.std(col) > 0 else 0.0
            results.append({"feature": name, "r": round(r, 4)})
        return sorted(results, key=lambda x: abs(x["r"]), reverse=True)
```

- [ ] **Step 4: Run tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_api_data.py -k "stats or correlations" -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add api/main.py tests/test_api_data.py
git commit -m "feat: add /data/stats and /data/correlations API endpoints"
```

---

## Task 6: API — `/data/feature-importance` endpoint

**Files:**
- Modify: `api/main.py`
- Modify: `tests/test_api_data.py`

- [ ] **Step 1: Add the feature-importance test to `tests/test_api_data.py`**

```python
def test_feature_importance_returns_sorted_list(db_session):
    import numpy as np
    from unittest.mock import patch, MagicMock
    from shared.feature_builder import FEATURE_NAMES

    now = datetime.now(timezone.utc)
    # Sentinel market row needed for ModelRegistry FK
    sentinel = Market(
        market_id="KXBTCUSD", ticker="KXBTCUSD", status="series",
        discovered_at=now, updated_at=now,
    )
    db_session.add(sentinel)
    db_session.flush()
    reg = ModelRegistry(
        market_id="KXBTCUSD", version="v1",
        trained_at=now, training_rows=200, brier_score=0.21,
        artifact_path="/app/models/KXBTCUSD_v1.joblib",
        is_active=True,
    )
    db_session.add(reg)
    db_session.flush()

    mock_model = MagicMock()
    mock_model.feature_importances_ = np.linspace(0.01, 0.10, len(FEATURE_NAMES))

    client = _make_app(db_session)
    with patch("joblib.load", return_value=mock_model):
        resp = client.get("/data/feature-importance?series_ticker=KXBTCUSD")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 25
    assert body[0]["feature"] == FEATURE_NAMES[-1]  # linspace: last = highest
    importances = [row["importance"] for row in body]
    assert importances == sorted(importances, reverse=True)


def test_feature_importance_404_when_no_active_model(db_session):
    client = _make_app(db_session)
    resp = client.get("/data/feature-importance?series_ticker=DOESNOTEXIST")
    assert resp.status_code == 404


def test_feature_importance_404_when_artifact_missing(db_session):
    import numpy as np
    from unittest.mock import patch

    now = datetime.now(timezone.utc)
    sentinel = Market(
        market_id="KXBTCUSD", ticker="KXBTCUSD", status="series",
        discovered_at=now, updated_at=now,
    )
    db_session.add(sentinel)
    db_session.flush()
    reg = ModelRegistry(
        market_id="KXBTCUSD", version="v2",
        trained_at=now, training_rows=100, brier_score=0.25,
        artifact_path="/nonexistent/path.joblib",
        is_active=True,
    )
    db_session.add(reg)
    db_session.flush()

    client = _make_app(db_session)
    with patch("joblib.load", side_effect=FileNotFoundError("not found")):
        resp = client.get("/data/feature-importance?series_ticker=KXBTCUSD")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_api_data.py -k "feature_importance" -v
```

Expected: `FAILED` — 404.

- [ ] **Step 3: Add the `/data/feature-importance` endpoint to `api/main.py`** inside `create_app`:

```python
    @app.get("/data/feature-importance")
    def get_feature_importance(
        series_ticker: str,
        session: Session = Depends(_get_db),
    ):
        import joblib
        from shared.feature_builder import FEATURE_NAMES
        from shared.orm import ModelRegistry

        registry = (
            session.query(ModelRegistry)
            .filter(
                ModelRegistry.market_id == series_ticker,
                ModelRegistry.is_active == True,  # noqa: E712
            )
            .first()
        )
        if registry is None:
            raise HTTPException(status_code=404, detail="No active model for this series ticker")

        try:
            model = joblib.load(registry.artifact_path)
        except (FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=404, detail=f"Model artifact not found: {exc}")

        result = [
            {"feature": name, "importance": round(float(imp), 6)}
            for name, imp in zip(FEATURE_NAMES, model.feature_importances_)
        ]
        return sorted(result, key=lambda x: x["importance"], reverse=True)
```

- [ ] **Step 4: Run all data API tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_api_data.py -v
```

Expected: all 16 tests pass.

- [ ] **Step 5: Run the full test suite to confirm no regressions**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add api/main.py tests/test_api_data.py
git commit -m "feat: add /data/feature-importance API endpoint"
```

---

## Task 7: Frontend — TypeScript interfaces, fetch functions, and App.tsx wiring

**Files:**
- Modify: `dashboard/src/api.ts`
- Modify: `dashboard/src/App.tsx`

- [ ] **Step 1: Add interfaces and fetch functions to `dashboard/src/api.ts`**

Append to the end of the file:

```typescript
// ── Data Explorer ─────────────────────────────────────────────────────────────

export interface RawFeatureRow {
  id: number
  ts: string
  market_id: string
  price_open: number | null
  price_high: number | null
  price_low: number | null
  price_close: number | null
  volume: number | null
  bid_depth_1pct: number | null
  ask_depth_1pct: number | null
  book_imbalance: number | null
  kalshi_yes_price: number | null
  kalshi_no_price: number | null
  kalshi_volume: number | null
  price_momentum_1m: number | null
  price_momentum_5m: number | null
  price_momentum_15m: number | null
  volatility_5m: number | null
}

export interface RawFeaturePage {
  rows: RawFeatureRow[]
  total: number
  page: number
  page_size: number
}

export interface FeatureVectorRow {
  ts: string
  direction: 'UP' | 'DOWN'
  confidence: number
  actual_outcome: 0 | 1
  features: Record<string, number>
}

export interface FeatureVectorPage {
  rows: FeatureVectorRow[]
  total: number
  page: number
  page_size: number
  skipped: number
}

export interface ColumnStat {
  feature: string
  mean: number
  std: number
  min: number
  max: number
  null_count: number
}

export interface CorrelationEntry {
  feature: string
  r: number
}

export interface ImportanceEntry {
  feature: string
  importance: number
}

export interface DataFilter {
  series_ticker: string
  from_ts?: string
  to_ts?: string
}

function _dataParams(filter: DataFilter, extra: Record<string, string | number> = {}): string {
  const p = new URLSearchParams({ series_ticker: filter.series_ticker })
  if (filter.from_ts) p.set('from_ts', filter.from_ts)
  if (filter.to_ts) p.set('to_ts', filter.to_ts)
  Object.entries(extra).forEach(([k, v]) => p.set(k, String(v)))
  return p.toString()
}

export const fetchRawFeatures = (
  filter: DataFilter,
  page: number,
  pageSize: number,
  sortBy: string,
  sortDir: 'asc' | 'desc',
): Promise<RawFeaturePage> =>
  apiFetch<RawFeaturePage>(`/data/raw-features?${_dataParams(filter, { page, page_size: pageSize, sort_by: sortBy, sort_dir: sortDir })}`)

export const fetchFeatureVectors = (
  filter: DataFilter,
  page: number,
  sortBy: string,
  sortDir: 'asc' | 'desc',
): Promise<FeatureVectorPage> =>
  apiFetch<FeatureVectorPage>(`/data/feature-vectors?${_dataParams(filter, { page, page_size: 50, sort_by: sortBy, sort_dir: sortDir })}`)

export const fetchDataStats = (filter: DataFilter): Promise<ColumnStat[]> =>
  apiFetch<ColumnStat[]>(`/data/stats?${_dataParams(filter)}`)

export const fetchDataCorrelations = (filter: DataFilter): Promise<CorrelationEntry[]> =>
  apiFetch<CorrelationEntry[]>(`/data/correlations?${_dataParams(filter)}`)

export const fetchFeatureImportance = (series_ticker: string): Promise<ImportanceEntry[]> =>
  apiFetch<ImportanceEntry[]>(`/data/feature-importance?series_ticker=${encodeURIComponent(series_ticker)}`)
```

- [ ] **Step 2: Add the `/data` route, nav item, and view title to `dashboard/src/App.tsx`**

In the `navItems` array inside `Sidebar`, add after the System entry:

```tsx
{ to: '/data', label: '🔍 Data' },
```

In `VIEW_TITLES`, add:

```tsx
'/data': 'Data Explorer',
```

In the `<Routes>` block, add the import at the top of the file:

```tsx
import DataExplorer from './views/DataExplorer'
```

And the route:

```tsx
<Route path="/data" element={<DataExplorer />} />
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/api.ts dashboard/src/App.tsx
git commit -m "feat: add Data Explorer TypeScript interfaces and route wiring"
```

---

## Task 8: Frontend — DataExplorer scaffold, CSS, and Raw Data tab

**Files:**
- Create: `dashboard/src/views/DataExplorer.tsx`
- Create: `dashboard/src/views/DataExplorer.module.css`

- [ ] **Step 1: Create `dashboard/src/views/DataExplorer.module.css`**

```css
.filterBar {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 10px 0 14px;
  flex-wrap: wrap;
}

.filterLabel {
  color: var(--text-label);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.filterInput {
  background: var(--bg);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 3px 8px;
  font-size: 12px;
  font-family: inherit;
}

.filterInput:focus {
  outline: 1px solid var(--accent);
}

.rowCount {
  margin-left: auto;
  color: var(--text-muted);
  font-size: 11px;
}

.tabBar {
  display: flex;
  gap: 0;
  border-bottom: 1px solid var(--border);
  margin-bottom: 16px;
}

.tab {
  padding: 8px 16px;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 12px;
  border-bottom: 2px solid transparent;
  background: none;
  border-top: none;
  border-left: none;
  border-right: none;
  font-family: inherit;
}

.tab:hover {
  color: var(--text);
  background: none;
}

.tabActive {
  color: var(--accent);
  border-bottom-color: var(--accent);
}

.tableWrap {
  overflow-x: auto;
}

.pagination {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  font-size: 11px;
  color: var(--text-muted);
  border-top: 1px solid var(--border);
  margin-top: 8px;
}

.pagination button {
  background: var(--bg-card);
  color: var(--text);
  border: 1px solid var(--border);
  font-size: 11px;
}

.pagination button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.pageInfo {
  flex: 1;
  text-align: center;
}

.signPos { color: var(--green); }
.signNeg { color: var(--red); }

.analysisGrid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.analysisCard {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 14px;
}

.analysisCard h3 {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-label);
  margin-bottom: 12px;
}

.fullWidth {
  grid-column: 1 / -1;
}

.barRow {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 5px;
  font-size: 11px;
}

.barLabel {
  width: 160px;
  text-align: right;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--text);
}

.barTrack {
  flex: 1;
  background: var(--border);
  border-radius: 2px;
  height: 8px;
}

.barFill {
  background: var(--accent);
  border-radius: 2px;
  height: 8px;
}

.barValue {
  width: 48px;
  color: var(--text-muted);
  font-size: 10px;
}

.corrGrid {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.corrTile {
  width: 64px;
  height: 64px;
  border-radius: 4px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
}

.corrValue {
  font-size: 11px;
  font-weight: 600;
}

.corrName {
  font-size: 9px;
  color: var(--text-muted);
  text-align: center;
  max-width: 60px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.corrLegend {
  margin-top: 8px;
  font-size: 9px;
  color: var(--text-muted);
}

.placeholder {
  color: var(--text-muted);
  text-align: center;
  padding: 40px 0;
  font-size: 13px;
}
```

- [ ] **Step 2: Create `dashboard/src/views/DataExplorer.tsx`** with the scaffold, filter bar, tab bar, and Raw Data tab panel:

```tsx
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useRefreshContext } from '../App'
import {
  fetchMarkets, fetchRawFeatures, fetchFeatureVectors,
  fetchDataStats, fetchDataCorrelations, fetchFeatureImportance,
  RawFeatureRow, FeatureVectorRow, ColumnStat, CorrelationEntry, ImportanceEntry,
  DataFilter,
} from '../api'
import styles from './DataExplorer.module.css'

type Tab = 'raw' | 'vectors' | 'analysis'

const RAW_COLS: Array<{ key: keyof RawFeatureRow; label: string; signed?: boolean }> = [
  { key: 'ts', label: 'Timestamp' },
  { key: 'price_close', label: 'Close' },
  { key: 'volume', label: 'Volume' },
  { key: 'book_imbalance', label: 'Book Imbal.', signed: true },
  { key: 'kalshi_yes_price', label: 'Kalshi Yes' },
  { key: 'kalshi_no_price', label: 'Kalshi No' },
  { key: 'price_momentum_1m', label: 'Mom 1m', signed: true },
  { key: 'price_momentum_5m', label: 'Mom 5m', signed: true },
  { key: 'price_momentum_15m', label: 'Mom 15m', signed: true },
  { key: 'volatility_5m', label: 'Vol 5m' },
  { key: 'bid_depth_1pct', label: 'Bid Depth' },
  { key: 'ask_depth_1pct', label: 'Ask Depth' },
]

function fmt(v: number | null, decimals = 4, signed = false): ReactNode {
  if (v === null || v === undefined) return <span style={{ color: 'var(--text-muted)' }}>—</span>
  const s = typeof v === 'number' ? v.toFixed(decimals) : String(v)
  if (signed) {
    const cls = v >= 0 ? styles.signPos : styles.signNeg
    return <span className={cls}>{v >= 0 ? '+' : ''}{s}</span>
  }
  return <>{s}</>
}

function RawDataTab({ filter }: { filter: DataFilter }) {
  const [page, setPage] = useState(1)
  const [sortBy, setSortBy] = useState('ts')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [data, setData] = useState<{ rows: RawFeatureRow[]; total: number } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await fetchRawFeatures(filter, page, 100, sortBy, sortDir)
      setData({ rows: result.rows, total: result.total })
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [filter, page, sortBy, sortDir])

  useEffect(() => { load() }, [load])

  const totalPages = data ? Math.ceil(data.total / 100) : 1

  function toggleSort(col: string) {
    if (sortBy === col) {
      setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    } else {
      setSortBy(col)
      setSortDir('desc')
    }
    setPage(1)
  }

  function SortIcon({ col }: { col: string }) {
    if (sortBy !== col) return <span style={{ color: 'var(--text-muted)' }}> ↕</span>
    return <span style={{ color: 'var(--accent)' }}> {sortDir === 'desc' ? '↓' : '↑'}</span>
  }

  if (error) return <div className="error-banner">{error}</div>

  return (
    <div>
      <div className={styles.tableWrap}>
        <table className="data-table">
          <thead>
            <tr>
              {RAW_COLS.map(c => (
                <th
                  key={c.key}
                  onClick={() => toggleSort(c.key as string)}
                  style={{ cursor: 'pointer', whiteSpace: 'nowrap' }}
                >
                  {c.label}<SortIcon col={c.key as string} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={RAW_COLS.length} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>Loading…</td></tr>
            )}
            {!loading && data?.rows.map(r => (
              <tr key={r.id}>
                {RAW_COLS.map(c => (
                  <td key={c.key} style={{ whiteSpace: c.key === 'ts' ? 'nowrap' : undefined }}>
                    {c.key === 'ts'
                      ? new Date(r.ts).toLocaleString()
                      : fmt(r[c.key] as number | null, c.key === 'price_close' ? 0 : 4, c.signed)}
                  </td>
                ))}
              </tr>
            ))}
            {!loading && data?.rows.length === 0 && (
              <tr><td colSpan={RAW_COLS.length} className={styles.placeholder}>No data for this filter</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className={styles.pagination}>
        <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}>← Prev</button>
        <span className={styles.pageInfo}>Page {page} of {totalPages}</span>
        <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages}>Next →</button>
        <span style={{ marginLeft: 'auto' }}>100 rows / page · {data?.total.toLocaleString() ?? 0} total</span>
      </div>
    </div>
  )
}

function FeatureVectorsTab({ filter }: { filter: DataFilter }) {
  const [page, setPage] = useState(1)
  const [sortBy, setSortBy] = useState('ts')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [data, setData] = useState<{ rows: FeatureVectorRow[]; total: number; skipped: number } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const FEATURE_NAMES = [
    'price_momentum_1m', 'price_momentum_5m', 'price_momentum_15m',
    'volatility_5m', 'volatility_roc', 'vwap_deviation_15m',
    'candle_body_ratio', 'volume_momentum_5m',
    'book_imbalance_latest', 'book_imbalance_trend', 'bid_depth_1pct', 'ask_depth_1pct',
    'kalshi_yes_price', 'kalshi_no_price', 'kalshi_price_momentum_5m', 'kalshi_deviation',
    'kalshi_volume_zscore', 'kalshi_volume_momentum',
    'minutes_to_close', 'sin_hour', 'cos_hour', 'sin_dow', 'cos_dow', 'is_weekend',
    'consecutive_direction',
  ]

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await fetchFeatureVectors(filter, page, sortBy, sortDir)
      setData({ rows: result.rows, total: result.total, skipped: result.skipped })
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [filter, page, sortBy, sortDir])

  useEffect(() => { load() }, [load])

  const totalPages = data ? Math.ceil(data.total / 50) : 1

  function toggleSort(col: string) {
    if (sortBy === col) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortBy(col); setSortDir('desc') }
    setPage(1)
  }

  function SortIcon({ col }: { col: string }) {
    if (sortBy !== col) return <span style={{ color: 'var(--text-muted)' }}> ↕</span>
    return <span style={{ color: 'var(--accent)' }}> {sortDir === 'desc' ? '↓' : '↑'}</span>
  }

  if (error) return <div className="error-banner">{error}</div>

  const SIGNED_FEATURES = new Set([
    'price_momentum_1m', 'price_momentum_5m', 'price_momentum_15m',
    'volatility_roc', 'vwap_deviation_15m', 'candle_body_ratio',
    'volume_momentum_5m', 'book_imbalance_latest', 'book_imbalance_trend',
    'kalshi_price_momentum_5m', 'kalshi_deviation',
    'kalshi_volume_zscore', 'kalshi_volume_momentum', 'consecutive_direction',
  ])

  return (
    <div>
      {data && data.skipped > 0 && (
        <div style={{ color: 'var(--text-muted)', fontSize: 11, marginBottom: 8 }}>
          {data.skipped} row(s) skipped — insufficient raw feature context
        </div>
      )}
      <div className={styles.tableWrap}>
        <table className="data-table">
          <thead>
            <tr>
              <th onClick={() => toggleSort('ts')} style={{ cursor: 'pointer', whiteSpace: 'nowrap' }}>
                Timestamp<SortIcon col="ts" />
              </th>
              <th>Dir</th>
              <th onClick={() => toggleSort('confidence')} style={{ cursor: 'pointer' }}>
                Conf<SortIcon col="confidence" />
              </th>
              <th onClick={() => toggleSort('actual_outcome')} style={{ cursor: 'pointer' }}>
                Outcome<SortIcon col="actual_outcome" />
              </th>
              {FEATURE_NAMES.map(f => <th key={f} style={{ whiteSpace: 'nowrap' }}>{f}</th>)}
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={4 + FEATURE_NAMES.length} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>Loading…</td></tr>
            )}
            {!loading && data?.rows.map((r, i) => (
              <tr key={i}>
                <td style={{ whiteSpace: 'nowrap' }}>{new Date(r.ts).toLocaleString()}</td>
                <td>
                  <span className={`badge ${r.direction === 'UP' ? 'badge-ok' : 'badge-err'}`}>
                    {r.direction}
                  </span>
                </td>
                <td>{r.confidence.toFixed(3)}</td>
                <td>
                  <span className={`badge ${r.actual_outcome === 1 ? 'badge-ok' : 'badge-err'}`}>
                    {r.actual_outcome === 1 ? 'UP' : 'DOWN'}
                  </span>
                </td>
                {FEATURE_NAMES.map(f => (
                  <td key={f}>{fmt(r.features[f] ?? null, 4, SIGNED_FEATURES.has(f))}</td>
                ))}
              </tr>
            ))}
            {!loading && data?.rows.length === 0 && (
              <tr><td colSpan={4 + FEATURE_NAMES.length} className={styles.placeholder}>No settled predictions in this window</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className={styles.pagination}>
        <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}>← Prev</button>
        <span className={styles.pageInfo}>Page {page} of {totalPages}</span>
        <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages}>Next →</button>
        <span style={{ marginLeft: 'auto' }}>50 rows / page · {data?.total.toLocaleString() ?? 0} total</span>
      </div>
    </div>
  )
}

function AnalysisTab({ filter }: { filter: DataFilter }) {
  const [stats, setStats] = useState<ColumnStat[]>([])
  const [correlations, setCorrelations] = useState<CorrelationEntry[]>([])
  const [importance, setImportance] = useState<ImportanceEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [s, c, imp] = await Promise.all([
        fetchDataStats(filter),
        fetchDataCorrelations(filter),
        fetchFeatureImportance(filter.series_ticker).catch(() => [] as ImportanceEntry[]),
      ])
      setStats(s)
      setCorrelations(c)
      setImportance(imp)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => { load() }, [load])

  if (error) return <div className="error-banner">{error}</div>
  if (loading) return <div className={styles.placeholder}>Computing analysis…</div>

  const noData = stats.length === 0

  const maxImportance = importance.length ? importance[0].importance : 1

  return (
    <div className={styles.analysisGrid}>

      {/* Column Stats */}
      <div className={styles.analysisCard}>
        <h3>Column Stats — Feature Vectors</h3>
        {noData ? (
          <div className={styles.placeholder}>Not enough settled predictions in this window (need ≥ 5)</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Feature</th>
                <th style={{ textAlign: 'right' }}>Mean</th>
                <th style={{ textAlign: 'right' }}>Std</th>
                <th style={{ textAlign: 'right' }}>Min</th>
                <th style={{ textAlign: 'right' }}>Max</th>
              </tr>
            </thead>
            <tbody>
              {stats.map(s => (
                <tr key={s.feature}>
                  <td style={{ whiteSpace: 'nowrap' }}>{s.feature}</td>
                  <td style={{ textAlign: 'right' }}>{s.mean.toFixed(4)}</td>
                  <td style={{ textAlign: 'right' }}>{s.std.toFixed(4)}</td>
                  <td style={{ textAlign: 'right' }}>{s.min.toFixed(4)}</td>
                  <td style={{ textAlign: 'right' }}>{s.max.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Feature Importance */}
      <div className={styles.analysisCard}>
        <h3>Feature Importance — Active Model</h3>
        {importance.length === 0 ? (
          <div className={styles.placeholder}>No active model — run the trainer first</div>
        ) : (
          <>
            {importance.map(({ feature, importance: imp }) => (
              <div key={feature} className={styles.barRow}>
                <span className={styles.barLabel} title={feature}>{feature}</span>
                <div className={styles.barTrack}>
                  <div
                    className={styles.barFill}
                    style={{ width: `${(imp / maxImportance) * 100}%` }}
                  />
                </div>
                <span className={styles.barValue}>{imp.toFixed(3)}</span>
              </div>
            ))}
          </>
        )}
      </div>

      {/* Correlations */}
      <div className={`${styles.analysisCard} ${styles.fullWidth}`}>
        <h3>Feature Correlation with Outcome (settled predictions only)</h3>
        {noData ? (
          <div className={styles.placeholder}>Not enough settled predictions in this window (need ≥ 5)</div>
        ) : (
          <>
            <div className={styles.corrGrid}>
              {correlations.map(({ feature, r }) => {
                const abs = Math.abs(r)
                const bg = r >= 0
                  ? `rgba(124,58,237,${Math.min(0.8, abs * 2.5)})`
                  : `rgba(239,68,68,${Math.min(0.8, abs * 2.5)})`
                const textColor = abs > 0.15 ? '#fff' : 'var(--text-muted)'
                return (
                  <div key={feature} className={styles.corrTile} style={{ background: bg }} title={feature}>
                    <span className={styles.corrValue} style={{ color: textColor }}>
                      {r >= 0 ? '+' : ''}{r.toFixed(2)}
                    </span>
                    <span className={styles.corrName}>{feature.replace(/_/g, '_​')}</span>
                  </div>
                )
              })}
            </div>
            <div className={styles.corrLegend}>
              Purple = positive correlation with UP outcome &nbsp;|&nbsp; Red = negative &nbsp;|&nbsp; Intensity = magnitude
            </div>
          </>
        )}
      </div>

    </div>
  )
}

export default function DataExplorer() {
  const [tab, setTab] = useState<Tab>('raw')
  const [tickers, setTickers] = useState<string[]>([])
  const [seriesTicker, setSeriesTicker] = useState('')
  const [fromTs, setFromTs] = useState(() => {
    const d = new Date()
    d.setDate(d.getDate() - 7)
    return d.toISOString().split('T')[0]
  })
  const [toTs, setToTs] = useState(() => new Date().toISOString().split('T')[0])
  const [appliedFilter, setAppliedFilter] = useState<DataFilter | null>(null)
  const ctx = useRefreshContext()

  useEffect(() => {
    fetchMarkets().then(markets => {
      const unique = [...new Set(markets.map(m => m.ticker))].sort()
      setTickers(unique)
      if (unique.length > 0) {
        setSeriesTicker(unique[0])
        setAppliedFilter({ series_ticker: unique[0], from_ts: fromTs, to_ts: toTs })
      }
    }).catch(() => {})
  }, [])

  // DataExplorer has no auto-refresh — analysis is intentionally on-demand
  useEffect(() => {
    ctx.setRefreshFn(() => {})
    ctx.setIsLoading(false)
    ctx.setLastRefreshed(null)
  }, [ctx])

  function applyFilter() {
    setAppliedFilter({
      series_ticker: seriesTicker,
      from_ts: fromTs ? `${fromTs}T00:00:00Z` : undefined,
      to_ts: toTs ? `${toTs}T23:59:59Z` : undefined,
    })
  }

  return (
    <div>
      {/* Filter bar */}
      <div className={styles.filterBar}>
        <span className={styles.filterLabel}>Market</span>
        <select
          className={styles.filterInput}
          value={seriesTicker}
          onChange={e => setSeriesTicker(e.target.value)}
        >
          {tickers.map(t => <option key={t} value={t}>{t}</option>)}
        </select>

        <span className={styles.filterLabel}>From</span>
        <input
          type="date"
          className={styles.filterInput}
          value={fromTs}
          onChange={e => setFromTs(e.target.value)}
        />

        <span className={styles.filterLabel}>To</span>
        <input
          type="date"
          className={styles.filterInput}
          value={toTs}
          onChange={e => setToTs(e.target.value)}
        />

        <button onClick={applyFilter}>Apply</button>
      </div>

      {/* Tab bar */}
      <div className={styles.tabBar}>
        {(['raw', 'vectors', 'analysis'] as Tab[]).map(t => (
          <button
            key={t}
            className={`${styles.tab} ${tab === t ? styles.tabActive : ''}`}
            onClick={() => setTab(t)}
          >
            {t === 'raw' ? 'Raw Data' : t === 'vectors' ? 'Feature Vectors' : 'Analysis'}
          </button>
        ))}
      </div>

      {/* Tab panels — only render when filter is ready */}
      {!appliedFilter ? (
        <div className={styles.placeholder}>Loading markets…</div>
      ) : tab === 'raw' ? (
        <RawDataTab filter={appliedFilter} />
      ) : tab === 'vectors' ? (
        <FeatureVectorsTab filter={appliedFilter} />
      ) : (
        <AnalysisTab filter={appliedFilter} />
      )}
    </div>
  )
}
```

- [ ] **Step 3: Verify TypeScript compiles without errors**

```bash
cd /workspace/crypto_analysis/dashboard && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/views/DataExplorer.tsx dashboard/src/views/DataExplorer.module.css
git commit -m "feat: add DataExplorer view with Raw Data, Feature Vectors, and Analysis tabs"
```

---

## Task 9: Rebuild and smoke-test

**Files:** none (integration check only)

- [ ] **Step 1: Run the full Python test suite**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/ -q
```

Expected: all tests pass (including the 16 new tests in `test_api_data.py`).

- [ ] **Step 2: Deploy green slot and verify the new page loads**

```bash
bash scripts/deploy.sh green
```

Expected: build succeeds, health check passes, Caddy reloads.

- [ ] **Step 3: Open the dashboard and navigate to the Data Explorer**

Open `https://da.ai-al.site` in a browser, click `🔍 Data` in the sidebar. Confirm:
- Filter bar appears with market selector and date pickers
- "Apply" loads the Raw Data tab with paginated rows
- Switching to Feature Vectors tab shows computed features + outcome badges
- Switching to Analysis tab shows Stats and Importance cards (or "not enough data" placeholders)
- Sorting a column header changes sort direction

- [ ] **Step 4: Commit any final fixes, then push**

```bash
git push origin main
```
