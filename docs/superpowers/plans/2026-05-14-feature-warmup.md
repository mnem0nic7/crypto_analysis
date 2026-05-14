# Feature Warm-Up on Cold Start — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pre-populate `RawFeature` rows from historical Coinbase candles so the predictor can emit signals immediately instead of waiting ~5 minutes for 10 rows to accumulate.

**Architecture:** Add `warm_up_if_needed()` to `ingestor/feature_writer.py`. It checks existing row count for a market, and if fewer than 20, fetches the last 20 one-minute candles from Coinbase and inserts them as `RawFeature` rows (OHLCV populated; live-only fields like order book and Kalshi stored as `None`). Call it at the top of `fetch_and_write()`. No schema changes — all nullable columns in `RawFeature` already accept `None`, and `feature_builder.py` handles `None` via `_f(val, default=0.0)`.

**Tech Stack:** Python 3.13, SQLAlchemy ORM, SQLite (tests), PostgreSQL (production), `unittest.mock.MagicMock` for Coinbase client stub.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `ingestor/feature_writer.py` | Add `warm_up_if_needed()`, call from `fetch_and_write()` |
| Modify | `tests/test_feature_writer.py` | 3 new tests for warm-up behaviour |

---

## Task 1: Implement `warm_up_if_needed()`

**Files:**
- Modify: `ingestor/feature_writer.py`
- Modify: `tests/test_feature_writer.py`

### Background

`ingestor/feature_writer.py` currently has:
- `_POLL_INTERVAL_SECONDS = 30` (module constant, top of file)
- `compute_derived_fields(current_price, prior_rows) -> dict`
- `build_raw_feature_row(market_id, ts, candle, order_book, kalshi_price, derived) -> RawFeature`
- `fetch_and_write(session, market_id, series_ticker, coinbase_client, kalshi_client) -> None`

`RawFeature` (in `shared/orm.py`) has columns:
- `id`, `market_id` (FK), `ts` — NOT NULL
- `price_open`, `price_high`, `price_low`, `price_close`, `volume` — nullable
- `bid_depth_1pct`, `ask_depth_1pct`, `book_imbalance` — nullable (order book; live only)
- `kalshi_yes_price`, `kalshi_no_price`, `kalshi_volume` — nullable (live only)
- `price_momentum_1m`, `price_momentum_5m`, `price_momentum_15m`, `volatility_5m` — nullable

`CoinbaseClient.get_candles(product_id, granularity, limit)` returns a list of dicts:
`[{"start": int_unix_epoch, "open": float, "high": float, "low": float, "close": float, "volume": float}, ...]`
Most-recent candle first.

- [ ] **Step 1: Write three failing tests in `tests/test_feature_writer.py`**

Append to the end of the existing file (do not remove existing tests):

```python
# ── warm_up_if_needed ──────────────────────────────────────────────────────────

def _make_market(market_id: str, db_session):
    from shared.orm import Market
    m = Market(
        market_id=market_id, ticker="KXBTCUSD", status="active",
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(m)
    db_session.flush()
    return m


def _fake_candles(n: int, close: float = 60050.0) -> list[dict]:
    now_ts = int(datetime.now(timezone.utc).timestamp())
    return [
        {
            "start": now_ts - (i * 60),
            "open": 60000.0, "high": 60100.0, "low": 59900.0,
            "close": close, "volume": 1.0,
        }
        for i in range(n)
    ]


def test_warm_up_writes_rows_when_db_empty(db_session):
    from ingestor.feature_writer import warm_up_if_needed
    _make_market("KXBTCUSD-WU1", db_session)

    mock_cb = MagicMock()
    mock_cb.get_candles.return_value = _fake_candles(15)

    warm_up_if_needed(db_session, "KXBTCUSD-WU1", "BTC-USD", mock_cb)

    rows = db_session.query(RawFeature).filter_by(market_id="KXBTCUSD-WU1").all()
    assert len(rows) == 15
    assert all(float(r.price_close) == pytest.approx(60050.0) for r in rows)
    # Live-only fields must be None for warm-up rows
    assert all(r.bid_depth_1pct is None for r in rows)
    assert all(r.kalshi_yes_price is None for r in rows)
    mock_cb.get_candles.assert_called_once_with("BTC-USD", granularity="ONE_MINUTE", limit=20)


def test_warm_up_skips_when_already_warm(db_session):
    from ingestor.feature_writer import warm_up_if_needed
    _make_market("KXBTCUSD-WU2", db_session)

    # Insert 20 existing rows so the market is already warm
    for i in range(20):
        db_session.add(RawFeature(
            market_id="KXBTCUSD-WU2",
            ts=datetime.now(timezone.utc) - timedelta(minutes=i),
            price_close=60000.0,
        ))
    db_session.flush()

    mock_cb = MagicMock()
    warm_up_if_needed(db_session, "KXBTCUSD-WU2", "BTC-USD", mock_cb)

    mock_cb.get_candles.assert_not_called()


def test_warm_up_idempotent_skips_existing_timestamps(db_session):
    from ingestor.feature_writer import warm_up_if_needed
    _make_market("KXBTCUSD-WU3", db_session)

    candles = _fake_candles(15)
    # Pre-insert 3 rows whose timestamps match the first 3 candles
    for c in candles[:3]:
        db_session.add(RawFeature(
            market_id="KXBTCUSD-WU3",
            ts=datetime.fromtimestamp(c["start"], tz=timezone.utc),
            price_close=60000.0,
        ))
    db_session.flush()

    mock_cb = MagicMock()
    mock_cb.get_candles.return_value = candles

    warm_up_if_needed(db_session, "KXBTCUSD-WU3", "BTC-USD", mock_cb)

    total = db_session.query(RawFeature).filter_by(market_id="KXBTCUSD-WU3").count()
    assert total == 15  # 3 pre-existing + 12 new, no duplicates
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_feature_writer.py::test_warm_up_writes_rows_when_db_empty tests/test_feature_writer.py::test_warm_up_skips_when_already_warm tests/test_feature_writer.py::test_warm_up_idempotent_skips_existing_timestamps -v
```

Expected: FAIL with `ImportError: cannot import name 'warm_up_if_needed'`.

- [ ] **Step 3: Add `warm_up_if_needed()` to `ingestor/feature_writer.py`**

Add `_WARMUP_CANDLES = 20` constant directly after `_POLL_INTERVAL_SECONDS = 30` (line 9). Then add the function after the existing module-level constants and before `compute_derived_fields`:

After the imports/constants block (after line 9 `_POLL_INTERVAL_SECONDS = 30`), insert:

```python
_WARMUP_CANDLES = 20  # minutes of history to pre-load on cold start


def warm_up_if_needed(
    session: Session,
    market_id: str,
    product_id: str,
    coinbase_client,
) -> None:
    existing_count = (
        session.query(RawFeature)
        .filter(RawFeature.market_id == market_id)
        .count()
    )
    if existing_count >= _WARMUP_CANDLES:
        return

    try:
        candles = coinbase_client.get_candles(
            product_id, granularity="ONE_MINUTE", limit=_WARMUP_CANDLES
        )
    except Exception as exc:
        logger.warning("Warm-up fetch failed for %s: %s", product_id, exc)
        return

    if not candles:
        return

    # Collect existing timestamps as unix epoch ints to avoid tz comparison issues.
    # SQLite strips tzinfo from TIMESTAMP columns, so normalise to UTC before
    # converting — otherwise .timestamp() would use local time on non-UTC hosts.
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=_WARMUP_CANDLES + 1)
    existing_ts_unix = {
        int((r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc)).timestamp())
        for r in session.query(RawFeature.ts)
        .filter(RawFeature.market_id == market_id, RawFeature.ts >= cutoff)
        .all()
    }

    new_rows = 0
    for candle in sorted(candles, key=lambda c: c["start"]):
        if candle["start"] in existing_ts_unix:
            continue
        ts = datetime.fromtimestamp(candle["start"], tz=timezone.utc)
        session.add(RawFeature(
            market_id=market_id,
            ts=ts,
            price_open=candle["open"],
            price_high=candle["high"],
            price_low=candle["low"],
            price_close=candle["close"],
            volume=candle["volume"],
        ))
        new_rows += 1

    if new_rows:
        session.flush()
        logger.info("Warmed up %d historical rows for %s", new_rows, market_id)
```

Full updated file contents (replace `ingestor/feature_writer.py` entirely):

```python
# ingestor/feature_writer.py
import logging
from datetime import datetime, timezone, timedelta
import numpy as np
from sqlalchemy.orm import Session
from shared.orm import RawFeature

logger = logging.getLogger(__name__)
_POLL_INTERVAL_SECONDS = 30
_WARMUP_CANDLES = 20  # minutes of history to pre-load on cold start


def warm_up_if_needed(
    session: Session,
    market_id: str,
    product_id: str,
    coinbase_client,
) -> None:
    existing_count = (
        session.query(RawFeature)
        .filter(RawFeature.market_id == market_id)
        .count()
    )
    if existing_count >= _WARMUP_CANDLES:
        return

    try:
        candles = coinbase_client.get_candles(
            product_id, granularity="ONE_MINUTE", limit=_WARMUP_CANDLES
        )
    except Exception as exc:
        logger.warning("Warm-up fetch failed for %s: %s", product_id, exc)
        return

    if not candles:
        return

    # SQLite strips tzinfo; normalise before .timestamp() to avoid local-TZ skew.
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=_WARMUP_CANDLES + 1)
    existing_ts_unix = {
        int((r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc)).timestamp())
        for r in session.query(RawFeature.ts)
        .filter(RawFeature.market_id == market_id, RawFeature.ts >= cutoff)
        .all()
    }

    new_rows = 0
    for candle in sorted(candles, key=lambda c: c["start"]):
        if candle["start"] in existing_ts_unix:
            continue
        ts = datetime.fromtimestamp(candle["start"], tz=timezone.utc)
        session.add(RawFeature(
            market_id=market_id,
            ts=ts,
            price_open=candle["open"],
            price_high=candle["high"],
            price_low=candle["low"],
            price_close=candle["close"],
            volume=candle["volume"],
        ))
        new_rows += 1

    if new_rows:
        session.flush()
        logger.info("Warmed up %d historical rows for %s", new_rows, market_id)


def _rows_within(prior_rows: list, minutes: float) -> list:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return [r for r in prior_rows if r.ts >= cutoff]


def compute_derived_fields(current_price: float, prior_rows: list) -> dict:
    def momentum(minutes: float) -> float:
        window = _rows_within(prior_rows, minutes)
        if not window:
            return 0.0
        oldest_price = float(window[0].price_close)
        if oldest_price == 0:
            return 0.0
        return (current_price - oldest_price) / oldest_price

    def volatility_5m() -> float:
        window = _rows_within(prior_rows, 5)
        prices = [float(r.price_close) for r in window] + [current_price]
        if len(prices) < 2:
            return 0.0
        returns = np.diff(prices) / np.array(prices[:-1])
        return float(np.std(returns))

    return {
        "price_momentum_1m": momentum(1),
        "price_momentum_5m": momentum(5),
        "price_momentum_15m": momentum(15),
        "volatility_5m": volatility_5m(),
    }


def build_raw_feature_row(
    market_id: str,
    ts: datetime,
    candle: dict,
    order_book: dict,
    kalshi_price: dict,
    derived: dict,
) -> RawFeature:
    row = RawFeature()
    row.market_id = market_id
    row.ts = ts
    row.price_open = candle["open"]
    row.price_high = candle["high"]
    row.price_low = candle["low"]
    row.price_close = candle["close"]
    row.volume = candle["volume"]
    row.bid_depth_1pct = order_book["bid_depth"]
    row.ask_depth_1pct = order_book["ask_depth"]
    row.book_imbalance = order_book["book_imbalance"]
    row.kalshi_yes_price = kalshi_price["yes_price"]
    row.kalshi_no_price = kalshi_price["no_price"]
    row.kalshi_volume = kalshi_price["volume"]
    row.price_momentum_1m = derived["price_momentum_1m"]
    row.price_momentum_5m = derived["price_momentum_5m"]
    row.price_momentum_15m = derived["price_momentum_15m"]
    row.volatility_5m = derived["volatility_5m"]
    return row


def fetch_and_write(
    session: Session,
    market_id: str,
    series_ticker: str,
    coinbase_client,
    kalshi_client,
) -> None:
    from ingestor.coinbase_client import series_ticker_to_product_id
    product_id = series_ticker_to_product_id(series_ticker)
    try:
        candles = coinbase_client.get_candles(product_id, granularity="ONE_MINUTE", limit=2)
        if not candles:
            logger.warning("No candles for %s", product_id)
            return
        latest_candle = candles[0]
        order_book = coinbase_client.get_order_book(product_id)
        kalshi_price = kalshi_client.get_market_price(market_id)
    except Exception as exc:
        logger.warning("Data fetch failed for %s: %s", market_id, exc)
        return

    prior_rows = (
        session.query(RawFeature)
        .filter(RawFeature.market_id == market_id)
        .order_by(RawFeature.ts.desc())
        .limit(40)
        .all()
    )
    prior_rows = sorted(prior_rows, key=lambda r: r.ts)

    derived = compute_derived_fields(latest_candle["close"], prior_rows)
    ts = datetime.now(timezone.utc)
    row = build_raw_feature_row(market_id, ts, latest_candle, order_book, kalshi_price, derived)
    session.add(row)
    session.flush()
    logger.debug("Wrote raw_feature row for %s at %s", market_id, ts)
```

Note: `warm_up_if_needed` is NOT yet called from `fetch_and_write` in this task — that comes in Task 2. This task only defines and tests the function.

- [ ] **Step 4: Run the three new tests and confirm they pass**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_feature_writer.py::test_warm_up_writes_rows_when_db_empty tests/test_feature_writer.py::test_warm_up_skips_when_already_warm tests/test_feature_writer.py::test_warm_up_idempotent_skips_existing_timestamps -v
```

Expected: 3 PASS.

- [ ] **Step 5: Run full test suite — confirm no regressions**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add ingestor/feature_writer.py tests/test_feature_writer.py
git commit -m "feat: add warm_up_if_needed to pre-populate RawFeature rows from historical candles"
```

---

## Task 2: Wire warm-up into `fetch_and_write()` and deploy

**Files:**
- Modify: `ingestor/feature_writer.py` (one line change)
- Modify: `tests/test_feature_writer.py` (one integration test)

### Background

`fetch_and_write(session, market_id, series_ticker, coinbase_client, kalshi_client)` is called from `ingestor/main.py` inside `_ingest_loop()` every 30 seconds for each active market. The `series_ticker_to_product_id(series_ticker)` helper (imported from `ingestor/coinbase_client`) converts e.g. `"KXBTCUSD"` → `"BTC-USD"`.

After adding `warm_up_if_needed()` call in Task 1's `fetch_and_write`, on the very first call to a new market, the function will:
1. Call `warm_up_if_needed` → fetch 20 candles → write historical rows
2. Proceed to fetch the current candle + order book + Kalshi price → write one more row

The predictor, which polls every 60 seconds, will now find ≥ 20 rows and be able to run inference immediately.

- [ ] **Step 1: Write a failing integration test**

Append to `tests/test_feature_writer.py`:

```python
def test_fetch_and_write_triggers_warm_up_on_first_call(db_session):
    from ingestor.feature_writer import fetch_and_write
    _make_market("KXBTCUSD-FW1", db_session)

    now_ts = int(datetime.now(timezone.utc).timestamp())
    mock_cb = MagicMock()
    # get_candles called with limit=20 (warm-up) returns 15 historical candles
    # get_candles called with limit=2 (normal fetch) returns the latest candle
    def _get_candles(product_id, granularity="ONE_MINUTE", limit=2, start=None, end=None):
        if limit == 20:
            return [
                {"start": now_ts - (i * 60), "open": 60000.0, "high": 60100.0,
                 "low": 59900.0, "close": 60050.0, "volume": 1.0}
                for i in range(15)
            ]
        return [{"start": now_ts, "open": 60000.0, "high": 60100.0,
                 "low": 59900.0, "close": 60100.0, "volume": 2.0}]

    mock_cb.get_candles.side_effect = _get_candles
    mock_cb.get_order_book.return_value = {"bid_depth": 5.0, "ask_depth": 3.0, "book_imbalance": 0.25}

    mock_kalshi = MagicMock()
    mock_kalshi.get_market_price.return_value = {"yes_price": 0.55, "no_price": 0.45, "volume": 1000}

    fetch_and_write(db_session, "KXBTCUSD-FW1", "KXBTCUSD", mock_cb, mock_kalshi)

    rows = db_session.query(RawFeature).filter_by(market_id="KXBTCUSD-FW1").all()
    # 15 warm-up rows + 1 normal row = 16 total
    assert len(rows) >= 15
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_feature_writer.py::test_fetch_and_write_triggers_warm_up_on_first_call -v
```

Expected: FAIL — `assert len(rows) >= 15` fails because warm-up is not called yet (only 1 row written by the normal fetch).

- [ ] **Step 3: Wrap `warm_up_if_needed` call in `fetch_and_write()` with try/except**

In `ingestor/feature_writer.py`, find the `fetch_and_write` function. Add the warm-up call (with its own try/except) immediately after `product_id = series_ticker_to_product_id(series_ticker)`. This keeps warm-up failures non-fatal — a DB hiccup during warm-up logs a warning and continues, rather than propagating to `_ingest_loop` and killing the daemon thread:

The full `fetch_and_write` function should now be:

```python
def fetch_and_write(
    session: Session,
    market_id: str,
    series_ticker: str,
    coinbase_client,
    kalshi_client,
) -> None:
    from ingestor.coinbase_client import series_ticker_to_product_id
    product_id = series_ticker_to_product_id(series_ticker)
    try:
        warm_up_if_needed(session, market_id, product_id, coinbase_client)
    except Exception as exc:
        logger.warning("Warm-up failed for %s: %s", market_id, exc)
    try:
        candles = coinbase_client.get_candles(product_id, granularity="ONE_MINUTE", limit=2)
        if not candles:
            logger.warning("No candles for %s", product_id)
            return
        latest_candle = candles[0]
        order_book = coinbase_client.get_order_book(product_id)
        kalshi_price = kalshi_client.get_market_price(market_id)
    except Exception as exc:
        logger.warning("Data fetch failed for %s: %s", market_id, exc)
        return

    prior_rows = (
        session.query(RawFeature)
        .filter(RawFeature.market_id == market_id)
        .order_by(RawFeature.ts.desc())
        .limit(40)
        .all()
    )
    prior_rows = sorted(prior_rows, key=lambda r: r.ts)

    derived = compute_derived_fields(latest_candle["close"], prior_rows)
    ts = datetime.now(timezone.utc)
    row = build_raw_feature_row(market_id, ts, latest_candle, order_book, kalshi_price, derived)
    session.add(row)
    session.flush()
    logger.debug("Wrote raw_feature row for %s at %s", market_id, ts)
```

- [ ] **Step 4: Run the integration test and confirm it passes**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_feature_writer.py::test_fetch_and_write_triggers_warm_up_on_first_call -v
```

Expected: PASS.

- [ ] **Step 5: Run full test suite — confirm no regressions**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add ingestor/feature_writer.py tests/test_feature_writer.py
git commit -m "feat: wire warm_up_if_needed into fetch_and_write to eliminate cold-start delay"
```

- [ ] **Step 7: Deploy green slot**

```bash
bash /workspace/crypto_analysis/scripts/deploy.sh green
```

Expected: build succeeds, health check passes, Caddy reloads.

- [ ] **Step 8: Push to main**

```bash
cd /workspace/crypto_analysis && git push origin main
```
