# Series-Level Training + Historical Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the ML pipeline so models train and predict at the series level (`KXBTC15M`) instead of the per-contract level, then seed the DB with ~6,300 settled contracts per series using Kalshi historical outcomes and Coinbase OHLCV candles.

**Architecture:** A critical `now = ts` bug in `feature_builder.py` is fixed first (windowed features were computed relative to wall-clock time, making all historical training data produce garbage features). The training pipeline (`dataset.py`, `train.py`, `main.py`) is updated to aggregate across all contracts of a series. The predictor looks up the series model rather than a per-contract model. Finally a one-shot backfill script fetches ~9 weeks of history from Kalshi + Coinbase and populates `raw_features` + `predictions` with known outcomes.

**Tech Stack:** Python 3.13, SQLAlchemy 2, XGBoost, pytest/SQLite in-memory, Kalshi REST API (cursor pagination), Coinbase Advanced Trade REST API (300-candle time-range chunks).

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `predictor/feature_builder.py` | Modify line 38 | Fix `now = ts` so windowed features use prediction timestamp |
| `trainer/dataset.py` | Rewrite | `build_training_dataset(series_ticker)` — JOIN through `Market.ticker` |
| `trainer/train.py` | Rewrite | `train_and_promote(series_ticker)` — create sentinel Market row, store model under series ticker |
| `trainer/main.py` | Modify | Iterate distinct `Market.ticker` values, not active `market_id`s |
| `predictor/inference.py` | Modify 2 lines | `get_model(market.ticker)` instead of `market.market_id` |
| `ingestor/coinbase_client.py` | Add params | `get_candles(start, end)` for historical range fetches |
| `ingestor/kalshi_client.py` | Add method | `get_settled_markets_page(series_ticker, cursor)` |
| `ingestor/historical_backfill.py` | Create | One-shot backfill script |
| `shared/settings.py` | Modify 1 line | `training_campaign_lookback_hours` default → 2160 (90 days) |
| `.env.example` | Modify 1 line | Update default to 2160 |
| `tests/test_feature_builder.py` | Add test | Historical-timestamp regression |
| `tests/test_trainer.py` | Rewrite fixtures | Two contracts, same series ticker |
| `tests/test_inference.py` | Add assertion | `get_model` called with `market.ticker` |
| `tests/test_historical_backfill.py` | Create | Tests for backfill logic |

---

## Task 1: Fix `feature_builder.py` — `now = ts`

**The bug:** `build_feature_vector` sets `now = datetime.now(timezone.utc)` at line 38. All rolling-window helpers (`_within(minutes)`) compute `cutoff = now - timedelta(minutes=...)`. When rows come from March 2026 and `now` is May 2026, every cutoff is in the future relative to the rows — so every `_within()` call returns an empty list. Result: all momentum, volatility, and VWAP features are 0.0 for any historical data. The `ts` parameter is already passed in specifically to use as the reference time.

**Files:**
- Modify: `predictor/feature_builder.py:38`
- Modify: `tests/test_feature_builder.py`

- [ ] **Step 1: Write the failing test**

Add this test to `tests/test_feature_builder.py`:

```python
def test_feature_builder_uses_ts_not_wall_clock():
    """Momentum must be non-zero for rows timestamped 2 hours in the past."""
    past_ts = datetime.now(timezone.utc) - timedelta(hours=2)
    rows = [
        _make_row(60000 + i * 10, minutes_ago=0)  # we'll override ts manually
        for i in range(40)
    ]
    for i, r in enumerate(rows):
        r.ts = past_ts - timedelta(minutes=40 - i)  # rows span [past_ts-40m .. past_ts]
    result = build_feature_vector(rows, minutes_to_close=7.5, ts=past_ts)
    assert result is not None, "Should produce a vector for historical rows"
    vec, names = result
    mom_5m_idx = names.index("price_momentum_5m")
    assert vec[mom_5m_idx] != 0.0, (
        "price_momentum_5m must be non-zero — if 0, _within() is using datetime.now() "
        "instead of ts, returning empty windows for historical data"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_feature_builder.py::test_feature_builder_uses_ts_not_wall_clock -v
```

Expected: `FAILED — AssertionError: price_momentum_5m must be non-zero`

- [ ] **Step 3: Apply the one-line fix**

In `predictor/feature_builder.py`, find line 38:
```python
    now = datetime.now(timezone.utc)
```
Replace with:
```python
    now = ts
```

The full function signature for context (do not change it):
```python
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
    now = ts                        # ← THE FIX (was: datetime.now(timezone.utc))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_feature_builder.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add predictor/feature_builder.py tests/test_feature_builder.py
git commit -m "fix: feature_builder uses prediction timestamp ts instead of datetime.now()

Windowed helpers (_within, momentum, volatility, VWAP) were computing cutoffs
relative to wall-clock time. For historical training data from months ago, every
window was empty — all features produced 0.0. The ts parameter was already
threaded through for exactly this purpose."
```

---

## Task 2: Fix `trainer/dataset.py` — Series-Level Training Dataset

**What changes:** `build_training_dataset` now takes `series_ticker` (e.g., `"KXBTC15M"`) instead of `market_id`. It JOINs through `Market.ticker` to aggregate predictions across ALL contracts of a series. Context rows are fetched per-prediction from that prediction's specific `market_id`.

Also update `settings.py` and `.env.example` so `lookback_hours` defaults to 2160 (90 days) — otherwise the 24-hour default would filter out all backfilled historical data.

**Files:**
- Modify: `trainer/dataset.py`
- Modify: `shared/settings.py:35`
- Modify: `.env.example:21`
- Modify: `tests/test_trainer.py`

- [ ] **Step 1: Write the failing tests**

Replace the entire content of `tests/test_trainer.py` with:

```python
# tests/test_trainer.py
import pytest
import numpy as np
from datetime import datetime, timezone, timedelta
from trainer.dataset import build_training_dataset
from trainer.backfill import backfill_outcomes
from shared.orm import Market, RawFeature, Prediction


def _seed_series_data(session, series_ticker="KXBTC15M", n_contracts=3, market_id_prefix="KXBTC15M-TEST"):
    """Create n_contracts contracts for a series, each with raw_features and settled predictions."""
    now = datetime.now(timezone.utc)
    for c in range(n_contracts):
        market_id = f"{market_id_prefix}-{c:02d}"
        base_ts = now - timedelta(hours=n_contracts - c)
        market = Market(
            market_id=market_id,
            ticker=series_ticker,
            status="settled",
            close_time=base_ts + timedelta(minutes=15),
            discovered_at=base_ts,
            updated_at=base_ts,
        )
        session.add(market)
        session.flush()

        for i in range(20):
            rf = RawFeature()
            rf.market_id = market_id
            rf.ts = base_ts + timedelta(minutes=i * 0.5)
            rf.price_close = 60000 + c * 1000 + i * 20
            rf.price_open  = rf.price_close - 10
            rf.price_high  = rf.price_close + 30
            rf.price_low   = rf.price_close - 30
            rf.volume = 10.0
            rf.bid_depth_1pct = 5.0
            rf.ask_depth_1pct = 4.0
            rf.book_imbalance = 0.1
            rf.kalshi_yes_price = 0.55
            rf.kalshi_no_price = 0.45
            rf.kalshi_volume = 500.0
            session.add(rf)
        session.flush()

        for i in range(7):
            p = Prediction()
            p.market_id = market_id
            p.ts = base_ts + timedelta(minutes=i * 1.5)
            p.direction = "UP" if i % 2 == 0 else "DOWN"
            p.confidence = 0.6
            p.low_confidence = False
            p.model_version = "v0"
            p.settled_at = base_ts + timedelta(minutes=15)
            p.actual_outcome = 1 if i % 2 == 0 else 0
            session.add(p)
    session.flush()


def test_build_training_dataset_aggregates_across_contracts(db_session):
    """Training data must come from ALL contracts of the series, not just one."""
    _seed_series_data(db_session, series_ticker="KXBTC15M", n_contracts=3, market_id_prefix="KXBTC15M-AGG")
    result = build_training_dataset(db_session, "KXBTC15M", lookback_hours=2160)
    assert result is not None
    X, y = result
    # 3 contracts × 7 predictions each = 21 total, minus any filtered by _MIN_ROWS
    assert len(y) >= 5  # at least _MIN_SAMPLES
    assert X.shape[1] == 25
    assert set(y).issubset({0, 1})


def test_build_training_dataset_returns_none_for_empty_series(db_session):
    result = build_training_dataset(db_session, "KXBTC15M-NOSUCHSERIES", lookback_hours=2160)
    assert result is None


def test_build_training_dataset_returns_none_when_all_low_confidence(db_session):
    series_ticker = "KXBTC15M-LC"
    market = Market(
        market_id="KXBTC15M-LC-00", ticker=series_ticker, status="settled",
        close_time=datetime.now(timezone.utc),
        discovered_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    db_session.add(market)
    db_session.flush()
    for i in range(10):
        p = Prediction()
        p.market_id = "KXBTC15M-LC-00"
        p.ts = datetime.now(timezone.utc) - timedelta(minutes=10 - i)
        p.direction = "UP"
        p.confidence = 0.5
        p.low_confidence = True   # all filtered out
        p.model_version = "none"
        p.settled_at = datetime.now(timezone.utc)
        p.actual_outcome = 1
        db_session.add(p)
    db_session.flush()
    result = build_training_dataset(db_session, series_ticker, lookback_hours=2160)
    assert result is None


# ── train_and_promote tests ──────────────────────────────────────────────────

from trainer.train import train_and_promote


def test_train_and_promote_creates_model_file(db_session, tmp_path):
    _seed_series_data(db_session, series_ticker="KXBTC15M-TN", n_contracts=3, market_id_prefix="KXBTC15M-TN")
    result = train_and_promote(
        session=db_session,
        series_ticker="KXBTC15M-TN",
        lookback_hours=2160,
        models_dir=str(tmp_path),
    )
    assert result is not None
    assert result["promoted"] in (True, False)
    assert "brier_score" in result
    # Model stored under series ticker, not contract ticker
    assert result["series_ticker"] == "KXBTC15M-TN"


def test_train_and_promote_creates_sentinel_market_row(db_session, tmp_path):
    _seed_series_data(db_session, series_ticker="KXBTC15M-SM", n_contracts=3, market_id_prefix="KXBTC15M-SM")
    train_and_promote(
        session=db_session, series_ticker="KXBTC15M-SM",
        lookback_hours=2160, models_dir=str(tmp_path),
    )
    sentinel = db_session.get(Market, "KXBTC15M-SM")
    assert sentinel is not None
    assert sentinel.status == "series"
    assert sentinel.ticker == "KXBTC15M-SM"


def test_train_does_not_promote_when_worse(db_session, tmp_path):
    from shared.orm import ModelRegistry
    _seed_series_data(db_session, series_ticker="KXBTC15M-NP", n_contracts=3, market_id_prefix="KXBTC15M-NP")
    # Create sentinel + near-perfect existing model
    sentinel = Market(
        market_id="KXBTC15M-NP", ticker="KXBTC15M-NP", status="series",
        discovered_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    db_session.add(sentinel)
    db_session.flush()
    reg = ModelRegistry(
        market_id="KXBTC15M-NP",
        version="v0",
        trained_at=datetime.now(timezone.utc),
        training_rows=100,
        brier_score=0.001,
        artifact_path=str(tmp_path / "dummy.joblib"),
        is_active=True,
    )
    db_session.add(reg)
    db_session.flush()
    result = train_and_promote(
        session=db_session, series_ticker="KXBTC15M-NP",
        lookback_hours=2160, models_dir=str(tmp_path),
    )
    assert result["promoted"] is False


# ── backfill_outcomes tests (unchanged) ─────────────────────────────────────

def test_backfill_sets_actual_outcome(db_session):
    market_id = "KXBTC15M-BF-00"
    market = Market(
        market_id=market_id, ticker="KXBTC15M", status="settled",
        close_time=datetime.now(timezone.utc) + timedelta(hours=1),
        discovered_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    db_session.add(market)
    db_session.flush()

    ts_pred   = datetime.now(timezone.utc) - timedelta(minutes=20)
    ts_settle = ts_pred + timedelta(minutes=15)

    rf_pred = RawFeature()
    rf_pred.market_id = market_id
    rf_pred.ts = ts_pred
    rf_pred.price_close = 60000
    db_session.add(rf_pred)

    rf_settle = RawFeature()
    rf_settle.market_id = market_id
    rf_settle.ts = ts_settle
    rf_settle.price_close = 61000
    db_session.add(rf_settle)
    db_session.flush()

    pred = Prediction(
        market_id=market_id, ts=ts_pred, direction="UP",
        confidence=0.65, low_confidence=False, model_version="v1",
        feature_snapshot_id=rf_pred.id, settled_at=ts_settle, actual_outcome=None,
    )
    db_session.add(pred)
    db_session.flush()

    count = backfill_outcomes(db_session)
    assert count == 1
    db_session.expire(pred)
    assert pred.actual_outcome == 1


def test_backfill_skips_already_settled(db_session):
    market_id = "KXBTC15M-SK-00"
    market = Market(
        market_id=market_id, ticker="KXBTC15M", status="settled",
        close_time=datetime.now(timezone.utc) + timedelta(hours=1),
        discovered_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    db_session.add(market)
    ts_pred = datetime.now(timezone.utc) - timedelta(minutes=20)
    pred = Prediction(
        market_id=market_id, ts=ts_pred, direction="DOWN",
        confidence=0.6, low_confidence=False, model_version="v1",
        settled_at=ts_pred + timedelta(minutes=15), actual_outcome=0,
    )
    db_session.add(pred)
    db_session.flush()
    assert backfill_outcomes(db_session) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_trainer.py -v 2>&1 | head -40
```

Expected: multiple FAILs — `build_training_dataset` still takes `market_id`, `train_and_promote` still takes `market_id`.

- [ ] **Step 3: Rewrite `trainer/dataset.py`**

Replace the entire file with:

```python
# trainer/dataset.py
import logging
import numpy as np
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from shared.orm import Market, RawFeature, Prediction
from predictor.feature_builder import build_feature_vector

logger = logging.getLogger(__name__)
_MIN_SAMPLES = 5


def build_training_dataset(
    session: Session, series_ticker: str, lookback_hours: int
) -> tuple[np.ndarray, np.ndarray] | None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    settled_preds = (
        session.query(Prediction)
        .join(Market, Prediction.market_id == Market.market_id)
        .filter(
            Market.ticker == series_ticker,
            Prediction.actual_outcome != None,  # noqa: E711
            Prediction.ts >= cutoff,
            Prediction.low_confidence == False,
        )
        .order_by(Prediction.ts.asc())
        .all()
    )

    if len(settled_preds) < _MIN_SAMPLES:
        logger.info("Insufficient settled predictions for series %s (%d)", series_ticker, len(settled_preds))
        return None

    # Fetch all raw features for all contracts of this series within the lookback window.
    # Group by market_id so each prediction can find its context rows in O(1).
    all_raw = (
        session.query(RawFeature)
        .join(Market, RawFeature.market_id == Market.market_id)
        .filter(Market.ticker == series_ticker, RawFeature.ts >= cutoff)
        .order_by(RawFeature.ts.asc())
        .all()
    )
    raw_by_market: dict[str, list] = {}
    for r in all_raw:
        raw_by_market.setdefault(r.market_id, []).append(r)

    rows_X = []
    rows_y = []

    def _ts_utc(ts) -> datetime:
        if ts is None:
            return datetime.now(timezone.utc)
        if getattr(ts, "tzinfo", None) is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts

    for pred in settled_preds:
        pred_ts = _ts_utc(pred.ts)
        contract_rows = raw_by_market.get(pred.market_id, [])
        context_rows = [r for r in contract_rows if _ts_utc(r.ts) <= pred_ts][-40:]
        if not context_rows:
            continue

        market = session.get(Market, pred.market_id)
        minutes_to_close = 7.5
        if market and market.close_time:
            close_ts = _ts_utc(market.close_time)
            minutes_to_close = max(0.0, (close_ts - pred_ts).total_seconds() / 60)

        result = build_feature_vector(context_rows, minutes_to_close=minutes_to_close, ts=pred_ts)
        if result is None:
            continue
        vec, _ = result
        rows_X.append(vec)
        rows_y.append(int(pred.actual_outcome))

    if len(rows_X) < _MIN_SAMPLES:
        logger.info("Too few valid feature vectors for series %s", series_ticker)
        return None

    return np.array(rows_X), np.array(rows_y)
```

- [ ] **Step 4: Update lookback default in `shared/settings.py`**

In `shared/settings.py`, change line 34:
```python
    training_campaign_lookback_hours: int = 24
```
to:
```python
    training_campaign_lookback_hours: int = 2160
```

- [ ] **Step 5: Update `.env.example`**

In `.env.example`, change:
```
TRAINING_CAMPAIGN_LOOKBACK_HOURS=24
```
to:
```
TRAINING_CAMPAIGN_LOOKBACK_HOURS=2160
```

- [ ] **Step 6: Run tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_trainer.py -v 2>&1 | head -60
```

Expected: `test_build_training_dataset_*` tests now PASS. `test_train_*` tests still fail (train_and_promote not yet updated).

- [ ] **Step 7: Commit**

```bash
git add trainer/dataset.py shared/settings.py .env.example tests/test_trainer.py
git commit -m "feat: trainer/dataset aggregates across all contracts of a series

build_training_dataset(series_ticker) JOINs through markets.ticker so
predictions from all historical contracts feed one model. lookback_hours
default raised to 2160 (90 days) so backfilled data is included."
```

---

## Task 3: Fix `trainer/train.py` and `trainer/main.py` — Series-Level Model Store

**What changes:** `train_and_promote` takes `series_ticker` and stores the model artifact + `ModelRegistry` row under the series ticker. A "sentinel" `Market` row (`market_id = series_ticker`, `status = 'series'`) satisfies the FK without polluting the ingestor's active-market view. `main.py` iterates distinct `Market.ticker` values.

**Files:**
- Modify: `trainer/train.py`
- Modify: `trainer/main.py`

- [ ] **Step 1: Rewrite `trainer/train.py`**

Replace the entire file:

```python
# trainer/train.py
import logging
import os
from datetime import datetime, timezone
import joblib
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.metrics import brier_score_loss
import xgboost as xgb
from sqlalchemy.orm import Session
from shared.orm import Market, ModelRegistry
from trainer.dataset import build_training_dataset

logger = logging.getLogger(__name__)


def _ensure_sentinel_market(session: Session, series_ticker: str) -> None:
    """Create a status='series' Market row for the series ticker if absent.

    model_registry.market_id has a FK to markets.market_id.  Storing the model
    under the series ticker (e.g. 'KXBTC15M') requires a Markets row with that
    market_id.  These rows are never treated as active trading markets.
    """
    if session.get(Market, series_ticker) is None:
        now = datetime.now(timezone.utc)
        session.add(Market(
            market_id=series_ticker,
            ticker=series_ticker,
            title=f"{series_ticker} series",
            status="series",
            discovered_at=now,
            updated_at=now,
        ))
        session.flush()


def train_and_promote(
    session: Session,
    series_ticker: str,
    lookback_hours: int,
    models_dir: str,
) -> dict | None:
    result = build_training_dataset(session, series_ticker, lookback_hours)
    if result is None:
        logger.info("No training data for series %s", series_ticker)
        return None

    X, y = result
    if len(np.unique(y)) < 2:
        logger.warning("Only one class in training data for %s — skipping", series_ticker)
        return None

    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, shuffle=False)

    model = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=4,
        learning_rate=0.1,
        eval_metric="logloss",
        verbosity=0,
    )
    model.fit(X_train, y_train)

    y_prob = model.predict_proba(X_val)[:, 1]
    new_brier = float(brier_score_loss(y_val, y_prob))
    logger.info("Trained %s: brier=%.4f on %d rows", series_ticker, new_brier, len(X))

    _ensure_sentinel_market(session, series_ticker)

    current_active = (
        session.query(ModelRegistry)
        .filter(ModelRegistry.market_id == series_ticker, ModelRegistry.is_active == True)
        .first()
    )
    current_brier = float(current_active.brier_score) if current_active else float("inf")

    version_num = (int(current_active.version.lstrip("v")) + 1) if current_active else 1
    version = f"v{version_num}"
    artifact_path = os.path.join(models_dir, f"{series_ticker}_{version}.joblib")
    joblib.dump(model, artifact_path)

    promoted = new_brier < current_brier
    if promoted and current_active:
        current_active.is_active = False

    session.add(ModelRegistry(
        market_id=series_ticker,
        version=version,
        trained_at=datetime.now(timezone.utc),
        training_rows=len(X),
        brier_score=new_brier,
        artifact_path=artifact_path,
        is_active=promoted,
    ))
    session.flush()

    action = "Promoted" if promoted else "Rejected"
    logger.info("%s %s %s (brier %.4f vs current %.4f)", action, series_ticker, version, new_brier, current_brier)
    return {
        "series_ticker": series_ticker,
        "version": version,
        "brier_score": new_brier,
        "promoted": promoted,
    }
```

- [ ] **Step 2: Rewrite `trainer/main.py`**

Replace the entire file:

```python
# trainer/main.py
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
        series_tickers = [
            row[0]
            for row in session.query(Market.ticker).filter(Market.status != "series").distinct().all()
        ]

    logger.info("Starting training campaign for %d series", len(series_tickers))
    for series_ticker in series_tickers:
        with session_scope(session_factory) as session:
            result = train_and_promote(
                session=session,
                series_ticker=series_ticker,
                lookback_hours=settings.training_campaign_lookback_hours,
                models_dir="models",
            )
            if result:
                logger.info("Series %s: %s", series_ticker, result)
        time.sleep(0.1)


if __name__ == "__main__":
    settings = Settings()
    session_factory = make_session_factory(settings)
    logger.info("Trainer daemon starting — cooldown=%ds", settings.training_campaign_cooldown_seconds)
    while True:
        try:
            run_training_campaign(settings, session_factory)
            logger.info("Campaign done — sleeping %ds", settings.training_campaign_cooldown_seconds)
        except Exception as exc:
            logger.error("Training campaign failed: %s — retrying after cooldown", exc)
        time.sleep(settings.training_campaign_cooldown_seconds)
```

- [ ] **Step 3: Run all trainer tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_trainer.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 4: Run full test suite to check for regressions**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/ -v 2>&1 | tail -20
```

Expected: all tests PASS (or note any pre-existing failures unrelated to this task).

- [ ] **Step 5: Commit**

```bash
git add trainer/train.py trainer/main.py
git commit -m "feat: train_and_promote and training loop operate at series level

Models stored under series ticker (e.g. KXBTC15M) with a sentinel Market
row to satisfy the FK. Trainer iterates distinct series tickers instead
of active per-contract market_ids."
```

---

## Task 4: Fix `predictor/inference.py` — Look Up Model by Series Ticker

**What changes:** Two lines in `run_inference` — `get_model` must be called with `market.ticker` (the series, e.g. `"KXBTC15M"`) not `market.market_id` (the contract, e.g. `"KXBTC15M-26MAY141715-15"`). The `ModelRegistry` now stores entries under series tickers, so looking up by contract ticker returns `None` forever.

**Files:**
- Modify: `predictor/inference.py:35,44,57,64`
- Modify: `tests/test_inference.py`

- [ ] **Step 1: Add the assertion to `tests/test_inference.py`**

Add a new test at the end of `tests/test_inference.py`:

```python
def test_run_inference_looks_up_model_by_series_ticker(db_session):
    """get_model must be called with market.ticker, not market.market_id."""
    market = _make_market(db_session, "KXBTC15M-STCK")
    market.ticker = "KXBTC15M"  # series ticker differs from market_id
    db_session.flush()
    _make_raw_rows(db_session, "KXBTC15M-STCK")

    mock_model = MagicMock()
    mock_model.predict_proba.return_value = np.array([[0.4, 0.6]])
    mock_loader = MagicMock()
    mock_loader.get_model.return_value = mock_model
    mock_loader._version_cache = {"KXBTC15M": "v1"}

    run_inference(db_session, market, mock_loader)

    # get_model must be called with the SERIES ticker, not the contract market_id
    mock_loader.get_model.assert_called_with("KXBTC15M")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_inference.py::test_run_inference_looks_up_model_by_series_ticker -v
```

Expected: `FAILED — AssertionError: expected call with 'KXBTC15M' but got 'KXBTC15M-STCK'`

- [ ] **Step 3: Update `predictor/inference.py`**

Replace the entire file with:

```python
# predictor/inference.py
import logging
import numpy as np
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from shared.orm import Market, RawFeature, Prediction
from predictor.feature_builder import build_feature_vector

logger = logging.getLogger(__name__)
_FEATURE_LOOKBACK_ROWS = 40


def run_inference(session: Session, market: Market, model_loader) -> Prediction | None:
    rows = (
        session.query(RawFeature)
        .filter(RawFeature.market_id == market.market_id)
        .order_by(RawFeature.ts.desc())
        .limit(_FEATURE_LOOKBACK_ROWS)
        .all()
    )
    if not rows:
        logger.warning("No raw_features for %s — skipping inference", market.market_id)
        return None

    ts = datetime.now(timezone.utc)
    minutes_to_close = (
        (market.close_time - ts).total_seconds() / 60
        if market.close_time
        else 7.5
    )

    result = build_feature_vector(rows, minutes_to_close=minutes_to_close, ts=ts)
    feature_snapshot_id = rows[0].id  # most recent row id

    # Look up model by SERIES ticker so the same model is reused across all
    # contracts of a series (e.g. KXBTC15M), not per individual contract.
    model = model_loader.get_model(market.ticker)
    series_version = model_loader._version_cache.get(market.ticker, "unknown")

    if model is None:
        pred = Prediction(
            market_id=market.market_id, ts=ts,
            direction="UP", confidence=0.5, low_confidence=True,
            model_version="none", feature_snapshot_id=feature_snapshot_id,
            settled_at=market.close_time,
        )
        session.add(pred)
        session.flush()
        return pred

    if result is None:
        logger.info("Insufficient rows for %s — low_confidence", market.market_id)
        pred = Prediction(
            market_id=market.market_id, ts=ts,
            direction="UP", confidence=0.5, low_confidence=True,
            model_version=series_version, feature_snapshot_id=feature_snapshot_id,
            settled_at=market.close_time,
        )
        session.add(pred)
        session.flush()
        return pred

    vec, _ = result
    proba = model.predict_proba(vec.reshape(1, -1))[0]
    p_up = float(proba[1])
    direction = "UP" if p_up >= 0.5 else "DOWN"
    confidence = p_up if direction == "UP" else 1 - p_up

    pred = Prediction(
        market_id=market.market_id, ts=ts,
        direction=direction, confidence=confidence, low_confidence=False,
        model_version=series_version, feature_snapshot_id=feature_snapshot_id,
        settled_at=market.close_time,
    )
    session.add(pred)
    session.flush()
    logger.info("Prediction for %s: %s (%.2f)", market.market_id, direction, confidence)
    return pred
```

- [ ] **Step 4: Run all inference and full suite tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_inference.py tests/test_trainer.py tests/test_feature_builder.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add predictor/inference.py tests/test_inference.py
git commit -m "fix: predictor looks up model by series ticker not contract market_id

run_inference now calls get_model(market.ticker) so the KXBTC15M model
trained by the series-level trainer is found for every new contract."
```

---

## Task 5: Extend `coinbase_client.py` and `kalshi_client.py` for Historical Fetching

**What changes:**
- `CoinbaseClient.get_candles` gains optional `start`/`end` Unix timestamp params (integer seconds). When both are provided, the Coinbase API returns all candles in that range (up to 300); `limit` is omitted.
- `KalshiClient` gains `get_settled_markets_page(series_ticker, cursor)` for paginating settled contracts.

**Files:**
- Modify: `ingestor/coinbase_client.py:47-66`
- Modify: `ingestor/kalshi_client.py`
- Modify: `tests/test_coinbase_client.py`

- [ ] **Step 1: Read the existing coinbase test to understand fixture style**

```bash
cd /workspace/crypto_analysis && cat tests/test_coinbase_client.py
```

- [ ] **Step 2: Add the new `get_candles` test**

Add to `tests/test_coinbase_client.py`:

```python
def test_get_candles_with_start_end_omits_limit(monkeypatch):
    """When start+end are given, limit must not be sent to the API."""
    captured = {}
    def fake_get(url, headers, params):
        captured["params"] = params
        mock = MagicMock()
        mock.raise_for_status = lambda: None
        mock.json.return_value = {"candles": [
            {"start": "1700000000", "open": "50000", "high": "50100",
             "low": "49900", "close": "50050", "volume": "5.0"}
        ]}
        return mock
    from ingestor.coinbase_client import CoinbaseClient
    client = CoinbaseClient.__new__(CoinbaseClient)
    client._key_name = "k"
    client._private_key = None
    client._http = MagicMock()
    client._http.get.side_effect = fake_get
    candles = client.get_candles("BTC-USD", start=1699999700, end=1700000000)
    assert "limit" not in captured["params"]
    assert captured["params"]["start"] == "1699999700"
    assert captured["params"]["end"] == "1700000000"
    assert len(candles) == 1
    assert candles[0]["close"] == 50050.0
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_coinbase_client.py::test_get_candles_with_start_end_omits_limit -v
```

Expected: `FAILED — TypeError: get_candles() got unexpected keyword argument 'start'`

- [ ] **Step 4: Update `ingestor/coinbase_client.py`**

Replace the `get_candles` method (lines 47–66):

```python
    def get_candles(
        self, product_id: str, granularity: str = "ONE_MINUTE",
        limit: int = 40, start: int | None = None, end: int | None = None,
    ) -> list[dict]:
        path = f"/api/v3/brokerage/products/{product_id}/candles"
        token = self._make_jwt("GET", path)
        params: dict = {"granularity": granularity}
        if start is not None and end is not None:
            params["start"] = str(start)
            params["end"] = str(end)
        else:
            params["limit"] = limit
        resp = self._http.get(
            self.BASE_URL + path,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        resp.raise_for_status()
        return [
            {
                "start": int(c["start"]),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c["volume"]),
            }
            for c in resp.json().get("candles", [])
        ]
```

- [ ] **Step 5: Add `get_settled_markets_page` to `ingestor/kalshi_client.py`**

Add this method to the `KalshiClient` class (after `get_market_price`):

```python
    def get_settled_markets_page(
        self, series_ticker: str, cursor: str | None = None
    ) -> dict:
        """Return one page (up to 200) of settled markets and the next cursor."""
        path = "/trade-api/v2/markets"
        url = self._base_url + "/markets"
        params: dict = {"series_ticker": series_ticker, "status": "settled", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        headers = self._make_headers("GET", path)
        resp = self._http.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        return {"markets": data.get("markets", []), "cursor": data.get("cursor")}
```

- [ ] **Step 6: Run all client tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_coinbase_client.py tests/test_kalshi_client.py -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add ingestor/coinbase_client.py ingestor/kalshi_client.py tests/test_coinbase_client.py
git commit -m "feat: add start/end params to get_candles and get_settled_markets_page to KalshiClient

Enables historical backfill: Coinbase candles can be fetched for arbitrary
time ranges; Kalshi settled market pages can be paginated with a cursor."
```

---

## Task 6: Write `ingestor/historical_backfill.py`

**What it does:** For each of the 7 series, pages through all settled Kalshi contracts, fetches Coinbase 1-min candles in 5-hour batches (cached in memory), then writes per-contract `Market` + `RawFeature` + `Prediction` rows. Skips any contract that already has raw_features (idempotent). Progress logged every 100 markets.

**Files:**
- Create: `ingestor/historical_backfill.py`
- Create: `tests/test_historical_backfill.py`

- [ ] **Step 1: Write the tests first**

Create `tests/test_historical_backfill.py`:

```python
# tests/test_historical_backfill.py
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from shared.orm import Market, RawFeature, Prediction


def _make_kalshi_market(ticker, series_ticker, close_time, result="yes"):
    open_time = close_time - timedelta(minutes=15)
    return {
        "ticker": ticker,
        "series_ticker": series_ticker,
        "title": f"{ticker} test",
        "open_time": open_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "close_time": close_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "result": result,
        "floor_strike": 80000.0,
        "expiration_value": 81000.0 if result == "yes" else 79000.0,
    }


def _make_candle(unix_ts, price=80000.0):
    return {
        "start": unix_ts,
        "open": price - 10, "high": price + 20, "low": price - 20, "close": price,
        "volume": 5.0,
    }


def _build_candle_cache(base_dt, n=50):
    base_unix = int(base_dt.timestamp())
    return {base_unix - (n - i) * 60: _make_candle(base_unix - (n - i) * 60, 80000 + i * 10)
            for i in range(n)}


def test_process_one_market_inserts_rows(db_session):
    from ingestor.historical_backfill import _process_one_market
    close_time = datetime.now(timezone.utc) - timedelta(hours=2)
    market_dict = _make_kalshi_market("KXBTC15M-TEST-00", "KXBTC15M", close_time, result="yes")
    candle_cache = _build_candle_cache(close_time, n=50)

    result = _process_one_market(db_session, market_dict, "KXBTC15M", candle_cache)
    assert result == 1

    market = db_session.get(Market, "KXBTC15M-TEST-00")
    assert market is not None
    assert market.ticker == "KXBTC15M"
    assert market.status == "settled"

    rf_count = db_session.query(RawFeature).filter_by(market_id="KXBTC15M-TEST-00").count()
    assert rf_count >= 10

    pred = db_session.query(Prediction).filter_by(market_id="KXBTC15M-TEST-00").first()
    assert pred is not None
    assert pred.actual_outcome == 1  # result == "yes"
    assert pred.low_confidence is False
    assert pred.model_version == "backfill"


def test_process_one_market_result_no_gives_outcome_zero(db_session):
    from ingestor.historical_backfill import _process_one_market
    close_time = datetime.now(timezone.utc) - timedelta(hours=3)
    market_dict = _make_kalshi_market("KXBTC15M-TEST-01", "KXBTC15M", close_time, result="no")
    candle_cache = _build_candle_cache(close_time, n=50)

    _process_one_market(db_session, market_dict, "KXBTC15M", candle_cache)
    pred = db_session.query(Prediction).filter_by(market_id="KXBTC15M-TEST-01").first()
    assert pred.actual_outcome == 0


def test_process_one_market_is_idempotent(db_session):
    from ingestor.historical_backfill import _process_one_market
    close_time = datetime.now(timezone.utc) - timedelta(hours=4)
    market_dict = _make_kalshi_market("KXBTC15M-TEST-02", "KXBTC15M", close_time)
    candle_cache = _build_candle_cache(close_time, n=50)

    assert _process_one_market(db_session, market_dict, "KXBTC15M", candle_cache) == 1
    assert _process_one_market(db_session, market_dict, "KXBTC15M", candle_cache) == 0  # skipped


def test_process_one_market_skips_empty_result(db_session):
    from ingestor.historical_backfill import _process_one_market
    close_time = datetime.now(timezone.utc) - timedelta(hours=5)
    market_dict = _make_kalshi_market("KXBTC15M-TEST-03", "KXBTC15M", close_time, result="")
    candle_cache = _build_candle_cache(close_time, n=50)

    result = _process_one_market(db_session, market_dict, "KXBTC15M", candle_cache)
    assert result == 0  # empty result skipped


def test_process_one_market_skips_when_too_few_candles(db_session):
    from ingestor.historical_backfill import _process_one_market
    close_time = datetime.now(timezone.utc) - timedelta(hours=6)
    market_dict = _make_kalshi_market("KXBTC15M-TEST-04", "KXBTC15M", close_time)
    sparse_cache = _build_candle_cache(close_time, n=3)  # fewer than _MIN_ROWS=10

    result = _process_one_market(db_session, market_dict, "KXBTC15M", sparse_cache)
    assert result == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_historical_backfill.py -v 2>&1 | head -20
```

Expected: `ERROR — cannot import name '_process_one_market' from 'ingestor.historical_backfill'`

- [ ] **Step 3: Create `ingestor/historical_backfill.py`**

Create the file:

```python
# ingestor/historical_backfill.py
"""One-shot historical backfill: seeds raw_features + predictions from Kalshi
settled markets and Coinbase historical candles.

Run with:
    python -m ingestor.historical_backfill
"""
import logging
import time
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from shared.db import make_session_factory, session_scope
from shared.orm import Market, RawFeature, Prediction
from shared.settings import Settings
from ingestor.kalshi_client import KalshiClient, CRYPTO_15M_SERIES
from ingestor.coinbase_client import CoinbaseClient, series_ticker_to_product_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

_CANDLE_CHUNK_SECONDS = 300 * 60   # 300 candles × 60 s = 5 hours per Coinbase request
_MIN_CANDLES = 10                  # feature_builder._MIN_ROWS


def run_backfill(settings: Settings) -> None:
    session_factory = make_session_factory(settings)
    kalshi = KalshiClient(
        settings.kalshi_api_key,
        settings.kalshi_private_key_path,
        settings.kalshi_base_url,
    )
    coinbase = CoinbaseClient(settings.coinbase_cdp_key_name, settings.coinbase_cdp_private_key)

    for series_ticker in CRYPTO_15M_SERIES:
        logger.info("=== Starting backfill for %s ===", series_ticker)
        _backfill_series(session_factory, kalshi, coinbase, series_ticker)

    kalshi.close()
    coinbase.close()
    logger.info("=== Backfill complete ===")


def _backfill_series(session_factory, kalshi: KalshiClient, coinbase: CoinbaseClient,
                     series_ticker: str) -> None:
    product_id = series_ticker_to_product_id(series_ticker)

    markets = _fetch_all_settled_markets(kalshi, series_ticker)
    if not markets:
        logger.info("%s: no settled markets found", series_ticker)
        return
    logger.info("%s: fetched %d settled markets", series_ticker, len(markets))

    markets.sort(key=lambda m: m["close_time"])

    oldest_close = _parse_dt(markets[0]["close_time"])
    newest_close = _parse_dt(markets[-1]["close_time"])
    fetch_start = oldest_close - timedelta(hours=1)   # extra lead-in for momentum context
    fetch_end = newest_close + timedelta(minutes=5)

    logger.info("%s: fetching Coinbase candles %s → %s", series_ticker, fetch_start, fetch_end)
    candle_cache = _build_candle_cache(coinbase, product_id, fetch_start, fetch_end)
    logger.info("%s: %d candles cached", series_ticker, len(candle_cache))

    inserted = skipped = 0
    for i, market_dict in enumerate(markets):
        with session_scope(session_factory) as session:
            n = _process_one_market(session, market_dict, series_ticker, candle_cache)
            inserted += n
            skipped += 1 - n
        if (i + 1) % 100 == 0:
            logger.info("%s: %d/%d processed — %d inserted, %d skipped",
                        series_ticker, i + 1, len(markets), inserted, skipped)

    logger.info("%s: done — %d inserted, %d skipped", series_ticker, inserted, skipped)


def _fetch_all_settled_markets(kalshi: KalshiClient, series_ticker: str) -> list[dict]:
    markets: list[dict] = []
    cursor = None
    while True:
        page = kalshi.get_settled_markets_page(series_ticker, cursor)
        batch = page.get("markets", [])
        markets.extend(batch)
        cursor = page.get("cursor")
        if not cursor or not batch:
            break
        time.sleep(0.05)
    return markets


def _build_candle_cache(coinbase: CoinbaseClient, product_id: str,
                        start: datetime, end: datetime) -> dict[int, dict]:
    """Fetch all 1-min candles in [start, end] in 5-hour chunks. Returns {unix_ts: candle}."""
    cache: dict[int, dict] = {}
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(seconds=_CANDLE_CHUNK_SECONDS), end)
        try:
            candles = coinbase.get_candles(
                product_id,
                granularity="ONE_MINUTE",
                start=int(chunk_start.timestamp()),
                end=int(chunk_end.timestamp()),
            )
            for c in candles:
                cache[c["start"]] = c
        except Exception as exc:
            logger.warning("Candle fetch failed %s [%s..%s]: %s", product_id, chunk_start, chunk_end, exc)
        chunk_start = chunk_end
        time.sleep(0.1)
    return cache


def _process_one_market(session: Session, market_dict: dict, series_ticker: str,
                        candle_cache: dict[int, dict]) -> int:
    """Insert Market + RawFeature + Prediction rows for one settled contract.

    Returns 1 if inserted, 0 if skipped (already exists, insufficient candles,
    or missing/empty result field).
    """
    market_id = market_dict["ticker"]
    result_str = market_dict.get("result", "")
    if result_str not in ("yes", "no"):
        return 0

    # Idempotency: skip if any raw_features already exist for this contract
    if session.query(RawFeature).filter_by(market_id=market_id).limit(1).first():
        return 0

    close_time = _parse_dt(market_dict["close_time"])
    open_time_raw = market_dict.get("open_time")
    open_time = _parse_dt(open_time_raw) if open_time_raw else close_time - timedelta(minutes=15)
    pred_ts = open_time + timedelta(minutes=7, seconds=30)   # midpoint of the 15-min window

    # Extract candles: 40 minutes ending at pred_ts
    window_start = pred_ts - timedelta(minutes=40)
    candle_rows = [
        (datetime.fromtimestamp(unix_ts, tz=timezone.utc), candle)
        for unix_ts, candle in sorted(candle_cache.items())
        if window_start <= datetime.fromtimestamp(unix_ts, tz=timezone.utc) <= pred_ts
    ]

    if len(candle_rows) < _MIN_CANDLES:
        logger.debug("Skipping %s — only %d candles in window", market_id, len(candle_rows))
        return 0

    # Upsert Market row
    now = datetime.now(timezone.utc)
    if session.get(Market, market_id) is None:
        session.add(Market(
            market_id=market_id,
            ticker=series_ticker,
            title=market_dict.get("title"),
            close_time=close_time,
            status="settled",
            discovered_at=now,
            updated_at=now,
        ))
        session.flush()

    # Insert RawFeature rows (OHLCV only; Kalshi/order-book columns stay NULL)
    for ts, candle in candle_rows:
        rf = RawFeature()
        rf.market_id = market_id
        rf.ts = ts
        rf.price_open  = candle["open"]
        rf.price_high  = candle["high"]
        rf.price_low   = candle["low"]
        rf.price_close = candle["close"]
        rf.volume      = candle["volume"]
        session.add(rf)
    session.flush()

    # Feature snapshot: last RawFeature row at or before pred_ts
    snapshot = (
        session.query(RawFeature)
        .filter(RawFeature.market_id == market_id, RawFeature.ts <= pred_ts)
        .order_by(RawFeature.ts.desc())
        .first()
    )
    if snapshot is None:
        return 0

    # Insert Prediction with known outcome
    pred = Prediction()
    pred.market_id = market_id
    pred.ts = pred_ts
    pred.actual_outcome = 1 if result_str == "yes" else 0
    pred.direction = "UP" if pred.actual_outcome == 1 else "DOWN"
    pred.confidence = 1.0
    pred.low_confidence = False
    pred.model_version = "backfill"
    pred.feature_snapshot_id = snapshot.id
    pred.settled_at = close_time
    session.add(pred)
    session.flush()
    return 1


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


if __name__ == "__main__":
    run_backfill(Settings())
```

- [ ] **Step 4: Run the backfill tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_historical_backfill.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/ -v 2>&1 | tail -25
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add ingestor/historical_backfill.py tests/test_historical_backfill.py
git commit -m "feat: historical backfill script seeds 9 weeks of training data

For each of 7 series: pages Kalshi settled markets, fetches Coinbase 1-min
candles in 5-hour batches, writes Market + RawFeature + Prediction rows.
Idempotent (skips contracts that already have raw_features). Kalshi result
field provides ground-truth actual_outcome without price inference."
```

---

## Task 7: Deploy, Run Backfill, Verify Training

**What this task does:** Rebuild and deploy the updated services, run the backfill script, confirm training produces models, and verify the predictor starts issuing real (non-low-confidence) predictions.

- [ ] **Step 1: Rebuild and deploy**

```bash
cd /workspace/crypto_analysis && ./scripts/deploy.sh green 2>&1 | tail -15
```

Expected: `✓ Deployed to green. Old blue slot stopped.`

- [ ] **Step 2: Verify services healthy**

```bash
docker compose logs green-trainer --tail=20
docker compose logs green-predictor --tail=10
```

Expected: trainer logs `Starting training campaign for N series`, predictor logs `active_markets`.

- [ ] **Step 3: Run the backfill script**

Run inside the ingestor container (has credentials + DB access):

```bash
docker exec green-ingestor python -m ingestor.historical_backfill 2>&1 | tee /tmp/backfill.log
```

This will take 20–40 minutes. Progress is logged every 100 markets per series. Watch for:
```
INFO ... KXBTC15M: fetched 6301 settled markets
INFO ... KXBTC15M: 6301 candles cached
INFO ... KXBTC15M: 100/6301 processed — 98 inserted, 2 skipped
...
INFO ... KXBTC15M: done — 6189 inserted, 112 skipped
```

- [ ] **Step 4: Confirm DB row counts**

```bash
docker exec crypto_analysis-postgres-1 psql -U postgres -d crypto_analysis -c "
SELECT
    m.ticker,
    COUNT(DISTINCT rf.market_id) AS contracts,
    COUNT(rf.id)                 AS raw_feature_rows,
    COUNT(p.id)                  AS predictions,
    SUM(CASE WHEN p.actual_outcome IS NOT NULL THEN 1 ELSE 0 END) AS settled_preds
FROM raw_features rf
JOIN markets m ON m.market_id = rf.market_id
LEFT JOIN predictions p ON p.market_id = rf.market_id
WHERE m.status = 'settled'
GROUP BY m.ticker
ORDER BY m.ticker;"
```

Expected: 7 rows, each with 5,000–7,000 contracts, 100,000–280,000 raw_feature_rows, matching prediction counts.

- [ ] **Step 5: Trigger a training run and confirm models are created**

```bash
docker exec green-trainer python -c "
from shared.settings import Settings
from shared.db import make_session_factory, session_scope
from trainer.train import train_and_promote

settings = Settings()
sf = make_session_factory(settings)
with session_scope(sf) as session:
    result = train_and_promote(session, 'KXBTC15M', lookback_hours=2160, models_dir='models')
    print(result)
"
```

Expected output:
```
{'series_ticker': 'KXBTC15M', 'version': 'v1', 'brier_score': 0.XXXX, 'promoted': True}
```

- [ ] **Step 6: Verify predictor now uses real models**

```bash
docker logs green-predictor --tail=30 | grep -E "Prediction for|low_confidence"
```

Expected: lines like `Prediction for KXBTC15M-26MAY141730-00: UP (0.63)` — no `low_confidence=True`.

- [ ] **Step 7: Final commit (update README if needed)**

```bash
git add -A
git status  # verify only expected files changed
git commit -m "deploy: series-level training + historical backfill live

After backfill: ~44k predictions with known outcomes across 7 series.
Trainer now trains KXBTC15M/ETH/SOL/XRP/DOGE/BNB/HYPE series models.
Predictor issues real predictions instead of low_confidence=0.5."
```

---

## Self-Review

**Spec coverage check:**
- ✅ `now = ts` bug fixed — Task 1
- ✅ `build_training_dataset(series_ticker)` with cross-contract JOIN — Task 2
- ✅ `train_and_promote(series_ticker)` + sentinel Market row — Task 3
- ✅ `trainer/main.py` iterates distinct series tickers — Task 3
- ✅ `inference.py` calls `get_model(market.ticker)` — Task 4
- ✅ `get_candles(start, end)` — Task 5
- ✅ `get_settled_markets_page` — Task 5
- ✅ `historical_backfill.py` idempotent, per-contract rows, Kalshi result as label — Task 6
- ✅ `lookback_hours` default raised to 2160 — Task 2
- ✅ All changed files have tests — Tasks 1–6

**Placeholder scan:** No TBDs, no "handle edge cases" — all error paths have concrete code.

**Type consistency:**
- `build_training_dataset(series_ticker: str, lookback_hours: int)` — used identically in dataset.py, train.py, test_trainer.py ✅
- `train_and_promote(series_ticker: str, ...)` returns `{"series_ticker": ..., "version": ..., "brier_score": ..., "promoted": ...}` — checked in tests ✅
- `get_model(market.ticker)` — `ticker` is `str` on the `Market` ORM ✅
- `get_candles(..., start: int | None, end: int | None)` — called with `int(chunk_start.timestamp())` ✅
