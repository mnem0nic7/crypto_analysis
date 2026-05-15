# Crypto Parameter Sweep Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a full-grid parameter sweep analysis system that evaluates 7.5M knob combinations against historical settled predictions and surfaces the best CRYPTO_15M settings in a new dashboard tab.

**Architecture:** A new `analysis/` Python module (loader + sweep engine + orchestrator) stores top-500 results and per-knob marginals into two new DB tables (`sweep_runs`, `sweep_results`). Four new FastAPI endpoints expose the data; a new React "Analysis" tab visualises it with a knob importance bar chart, per-knob detail line chart, and sortable top-500 table.

**Tech Stack:** Python/SQLAlchemy/numpy/itertools for the sweep; FastAPI for the API; React 18 + Recharts for the dashboard; Alembic for the migration.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `shared/orm.py` | Add `SweepRun` + `SweepResult` ORM models |
| Create | `alembic/versions/002_sweep_tables.py` | Migration for `sweep_runs` + `sweep_results` |
| Create | `analysis/__init__.py` | Empty package marker |
| Create | `analysis/loader.py` | Query DB → numpy arrays of derived columns |
| Create | `analysis/sweep.py` | PARAM_GRID, `run_sweep()`, `execute_sweep()` |
| Create | `analysis/main.py` | CLI entry point |
| Modify | `api/main.py` | Add 4 analysis endpoints |
| Create | `tests/test_analysis_loader.py` | Loader unit tests |
| Create | `tests/test_analysis_sweep.py` | Sweep engine unit tests |
| Create | `tests/test_api_analysis.py` | API endpoint tests |
| Modify | `dashboard/src/api.ts` | Add Analysis types + fetch functions |
| Create | `dashboard/src/views/Analysis.tsx` | Analysis dashboard tab |
| Create | `dashboard/src/views/Analysis.module.css` | Analysis tab styles |
| Modify | `dashboard/src/App.tsx` | Wire Analysis route + nav item |

---

## Task 1: ORM Models

**Files:**
- Modify: `shared/orm.py`
- Test: `tests/test_analysis_loader.py` (imports only — full test in Task 3)

- [ ] **Step 1: Add imports and two new ORM classes to `shared/orm.py`**

Open `shared/orm.py`. After the existing imports add `JSON` to the sqlalchemy import list, then append the two classes at the bottom of the file:

```python
# Add JSON to the sqlalchemy import at the top:
from sqlalchemy import (
    BigInteger, Boolean, Column, ForeignKey, Integer, JSON, Numeric,
    SmallInteger, Text, TIMESTAMP, UniqueConstraint,
)
```

```python
class SweepRun(Base):
    __tablename__ = "sweep_runs"

    id = Column(_BigInt, primary_key=True, autoincrement=True)
    run_at = Column(TIMESTAMP(timezone=True), nullable=False)
    status = Column(Text, nullable=False)  # running | complete | failed
    n_settled_predictions = Column(Integer)
    fee_bps = Column(Integer, nullable=False, default=50)
    n_combinations_evaluated = Column(Integer)
    elapsed_seconds = Column(Numeric)
    best_net_pnl_dollars = Column(Numeric)
    best_settings_json = Column(JSON)


class SweepResult(Base):
    __tablename__ = "sweep_results"

    id = Column(_BigInt, primary_key=True, autoincrement=True)
    run_id = Column(_BigInt, ForeignKey("sweep_runs.id"), nullable=False)
    result_type = Column(Text, nullable=False)  # top_k | marginal
    rank = Column(Integer)
    knob_name = Column(Text)
    knob_value = Column(Text)
    # Knob values (NULL for marginal rows)
    min_fee_adjusted_edge_bps = Column(Integer)
    max_spread_bps = Column(Integer)
    min_confidence = Column(Numeric)
    min_contract_price_dollars = Column(Numeric)
    crypto_live_min_market_age_seconds = Column(Integer)
    crypto_autonomy_min_seconds_to_close = Column(Integer)
    crypto_taker_fallback_close_seconds = Column(Integer)
    crypto_market_price_anchor_weight = Column(Numeric)
    crypto_late_sure_thing_min_probability = Column(Numeric)
    crypto_late_sure_thing_min_market_probability = Column(Numeric)
    # Metrics
    n_trades = Column(Integer)
    win_rate = Column(Numeric)
    net_pnl_dollars = Column(Numeric)
    ev_per_contract = Column(Numeric)
    starvation_rate = Column(Numeric)
```

- [ ] **Step 2: Verify the models are importable**

```bash
cd /workspace/crypto_analysis && python -c "from shared.orm import SweepRun, SweepResult; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add shared/orm.py
git commit -m "feat: add SweepRun and SweepResult ORM models"
```

---

## Task 2: Alembic Migration

**Files:**
- Create: `alembic/versions/002_sweep_tables.py`

- [ ] **Step 1: Create the migration file**

Create `alembic/versions/002_sweep_tables.py`:

```python
"""add sweep_runs and sweep_results tables"""
revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "sweep_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("run_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("n_settled_predictions", sa.Integer),
        sa.Column("fee_bps", sa.Integer, nullable=False, server_default="50"),
        sa.Column("n_combinations_evaluated", sa.Integer),
        sa.Column("elapsed_seconds", sa.Numeric),
        sa.Column("best_net_pnl_dollars", sa.Numeric),
        sa.Column("best_settings_json", sa.JSON),
    )
    op.create_table(
        "sweep_results",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.BigInteger, sa.ForeignKey("sweep_runs.id"), nullable=False),
        sa.Column("result_type", sa.Text, nullable=False),
        sa.Column("rank", sa.Integer),
        sa.Column("knob_name", sa.Text),
        sa.Column("knob_value", sa.Text),
        sa.Column("min_fee_adjusted_edge_bps", sa.Integer),
        sa.Column("max_spread_bps", sa.Integer),
        sa.Column("min_confidence", sa.Numeric),
        sa.Column("min_contract_price_dollars", sa.Numeric),
        sa.Column("crypto_live_min_market_age_seconds", sa.Integer),
        sa.Column("crypto_autonomy_min_seconds_to_close", sa.Integer),
        sa.Column("crypto_taker_fallback_close_seconds", sa.Integer),
        sa.Column("crypto_market_price_anchor_weight", sa.Numeric),
        sa.Column("crypto_late_sure_thing_min_probability", sa.Numeric),
        sa.Column("crypto_late_sure_thing_min_market_probability", sa.Numeric),
        sa.Column("n_trades", sa.Integer),
        sa.Column("win_rate", sa.Numeric),
        sa.Column("net_pnl_dollars", sa.Numeric),
        sa.Column("ev_per_contract", sa.Numeric),
        sa.Column("starvation_rate", sa.Numeric),
    )
    op.create_index("ix_sweep_results_run_type", "sweep_results", ["run_id", "result_type"])


def downgrade():
    op.drop_index("ix_sweep_results_run_type")
    op.drop_table("sweep_results")
    op.drop_table("sweep_runs")
```

- [ ] **Step 2: Verify the migration file is syntactically valid**

```bash
cd /workspace/crypto_analysis && python -c "import alembic.versions.002_sweep_tables; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add alembic/versions/002_sweep_tables.py
git commit -m "feat: migration 002 — sweep_runs and sweep_results tables"
```

---

## Task 3: Analysis Loader

**Files:**
- Create: `analysis/__init__.py`
- Create: `analysis/loader.py`
- Create: `tests/test_analysis_loader.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_analysis_loader.py`:

```python
import pytest
from datetime import datetime, timezone, timedelta
import numpy as np
from shared.orm import Market, Prediction, RawFeature
from analysis.loader import load_settled_predictions


def _seed(session):
    now = datetime.now(timezone.utc)
    market = Market(
        market_id="TEST-001",
        ticker="KXBTC15M",
        status="active",
        discovered_at=now - timedelta(minutes=10),
        close_time=now + timedelta(minutes=5),
        updated_at=now,
    )
    session.add(market)
    session.flush()

    rf = RawFeature(
        market_id="TEST-001",
        ts=now - timedelta(minutes=2),
        kalshi_yes_price=0.6,
        kalshi_no_price=0.35,
    )
    session.add(rf)
    session.flush()

    pred = Prediction(
        market_id="TEST-001",
        ts=now - timedelta(minutes=2),
        direction="UP",
        confidence=0.85,
        low_confidence=False,
        model_version="v1",
        feature_snapshot_id=rf.id,
        settled_at=now,
        actual_outcome=1,
    )
    session.add(pred)
    session.flush()
    return market, rf, pred


def test_load_returns_arrays_for_settled_predictions(db_session):
    _seed(db_session)
    arrays = load_settled_predictions(db_session)

    assert arrays["n"] == 1
    assert arrays["confidence"][0] == pytest.approx(0.85)
    assert arrays["entry_price"][0] == pytest.approx(0.6)   # UP → yes_price
    assert arrays["outcome_correct"][0] == 1


def test_load_computes_spread_bps(db_session):
    _seed(db_session)
    arrays = load_settled_predictions(db_session)
    # spread = (1 - 0.6 - 0.35) * 10000 = 500
    assert arrays["spread_bps"][0] == pytest.approx(500.0)


def test_load_computes_timing(db_session):
    _seed(db_session)
    arrays = load_settled_predictions(db_session)
    assert arrays["market_age_seconds"][0] == pytest.approx(8 * 60, abs=5)
    assert arrays["seconds_to_close"][0] == pytest.approx(7 * 60, abs=5)


def test_load_excludes_unsettled(db_session):
    now = datetime.now(timezone.utc)
    market = Market(
        market_id="TEST-002", ticker="KXBTC15M", status="active",
        discovered_at=now - timedelta(minutes=5),
        close_time=now + timedelta(minutes=5),
        updated_at=now,
    )
    session = db_session
    session.add(market)
    session.flush()
    rf = RawFeature(
        market_id="TEST-002", ts=now,
        kalshi_yes_price=0.5, kalshi_no_price=0.45,
    )
    session.add(rf)
    session.flush()
    pred = Prediction(
        market_id="TEST-002", ts=now,
        direction="UP", confidence=0.7, low_confidence=False,
        model_version="v1", feature_snapshot_id=rf.id,
        actual_outcome=None,  # unsettled
    )
    session.add(pred)
    session.flush()
    arrays = load_settled_predictions(session)
    assert arrays["n"] == 0


def test_load_returns_empty_when_no_data(db_session):
    arrays = load_settled_predictions(db_session)
    assert arrays["n"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_analysis_loader.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'analysis'`

- [ ] **Step 3: Create `analysis/__init__.py`**

```bash
touch /workspace/crypto_analysis/analysis/__init__.py
```

- [ ] **Step 4: Create `analysis/loader.py`**

```python
# analysis/loader.py
from datetime import timezone
import numpy as np
from sqlalchemy.orm import Session
from shared.orm import Market, Prediction, RawFeature


def _to_utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_settled_predictions(session: Session) -> dict:
    """
    Query all settled predictions joined with their raw_features snapshot
    and market metadata. Returns a dict of numpy arrays with derived columns
    pre-computed. Returns {"n": 0} when no data is available.
    """
    rows = (
        session.query(
            Prediction.direction,
            Prediction.confidence,
            Prediction.actual_outcome,
            Prediction.ts,
            RawFeature.kalshi_yes_price,
            RawFeature.kalshi_no_price,
            Market.discovered_at,
            Market.close_time,
        )
        .join(RawFeature, RawFeature.id == Prediction.feature_snapshot_id)
        .join(Market, Market.market_id == Prediction.market_id)
        .filter(
            Prediction.actual_outcome != None,          # noqa: E711
            Prediction.feature_snapshot_id != None,     # noqa: E711
            RawFeature.kalshi_yes_price != None,        # noqa: E711
            RawFeature.kalshi_no_price != None,         # noqa: E711
            Market.close_time != None,                  # noqa: E711
            Market.discovered_at != None,               # noqa: E711
            Prediction.ts < Market.close_time,
        )
        .all()
    )

    if not rows:
        return {"n": 0}

    direction = np.array([r.direction for r in rows])
    confidence = np.array([float(r.confidence) for r in rows])
    actual_outcome = np.array([int(r.actual_outcome) for r in rows])
    yes_price = np.array([float(r.kalshi_yes_price) for r in rows])
    no_price = np.array([float(r.kalshi_no_price) for r in rows])

    is_up = direction == "UP"
    entry_price = np.where(is_up, yes_price, no_price)
    spread_bps = (1.0 - yes_price - no_price) * 10000.0
    raw_edge_bps = (confidence - entry_price) * 10000.0

    outcome_correct = np.where(
        is_up, actual_outcome == 1, actual_outcome == 0
    ).astype(np.int8)

    # Raw P&L before fee: win gets (1 - entry_price), loss loses entry_price
    raw_pnl = (
        outcome_correct * (1.0 - entry_price)
        - (1 - outcome_correct) * entry_price
    )

    market_age_seconds = np.array([
        (_to_utc(r.ts) - _to_utc(r.discovered_at)).total_seconds()
        for r in rows
    ])
    seconds_to_close = np.array([
        (_to_utc(r.close_time) - _to_utc(r.ts)).total_seconds()
        for r in rows
    ])

    return {
        "n": len(rows),
        "confidence": confidence,
        "entry_price": entry_price,
        "spread_bps": spread_bps,
        "raw_edge_bps": raw_edge_bps,
        "market_age_seconds": market_age_seconds,
        "seconds_to_close": seconds_to_close,
        "outcome_correct": outcome_correct,
        "raw_pnl": raw_pnl,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_analysis_loader.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 6: Commit**

```bash
git add analysis/__init__.py analysis/loader.py tests/test_analysis_loader.py
git commit -m "feat: analysis loader — load settled predictions into numpy arrays"
```

---

## Task 4: Sweep Engine

**Files:**
- Create: `analysis/sweep.py`
- Create: `tests/test_analysis_sweep.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_analysis_sweep.py`:

```python
import pytest
import numpy as np
from analysis.sweep import PARAM_GRID, KNOB_NAMES, run_sweep


def _arrays(n=30, confidence=None, entry_price=None, outcome_correct=None,
            spread_bps=None, market_age_seconds=None, seconds_to_close=None):
    """Build minimal arrays for sweep tests."""
    confidence = confidence if confidence is not None else np.full(n, 0.85)
    entry_price = entry_price if entry_price is not None else np.full(n, 0.60)
    outcome_correct = outcome_correct if outcome_correct is not None else np.ones(n, dtype=np.int8)
    spread_bps = spread_bps if spread_bps is not None else np.full(n, 100.0)
    market_age_seconds = market_age_seconds if market_age_seconds is not None else np.full(n, 600.0)
    seconds_to_close = seconds_to_close if seconds_to_close is not None else np.full(n, 400.0)
    raw_edge_bps = (confidence - entry_price) * 10000.0
    raw_pnl = (
        outcome_correct * (1.0 - entry_price)
        - (1 - outcome_correct) * entry_price
    )
    return {
        "n": n,
        "confidence": confidence,
        "entry_price": entry_price,
        "spread_bps": spread_bps,
        "raw_edge_bps": raw_edge_bps,
        "market_age_seconds": market_age_seconds,
        "seconds_to_close": seconds_to_close,
        "outcome_correct": outcome_correct,
        "raw_pnl": raw_pnl,
    }


def test_run_sweep_returns_top_k_and_marginals():
    arrays = _arrays(n=30)
    top_k, marginals = run_sweep(arrays, fee_bps=50)
    assert len(top_k) > 0
    assert len(marginals) > 0


def test_run_sweep_top_k_sorted_by_net_pnl():
    arrays = _arrays(n=30)
    top_k, _ = run_sweep(arrays, fee_bps=50)
    pnls = [r["net_pnl_dollars"] for r in top_k]
    assert pnls == sorted(pnls, reverse=True)


def test_run_sweep_high_edge_filter_selects_correct_trades():
    """High-edge trades (win) should dominate when min_fee_adjusted_edge_bps is high."""
    n = 40
    conf = np.array([0.90] * 20 + [0.51] * 20)
    ep = np.array([0.60] * 20 + [0.50] * 20)
    oc = np.array([1] * 20 + [0] * 20, dtype=np.int8)
    arrays = _arrays(n=n, confidence=conf, entry_price=ep, outcome_correct=oc)
    top_k, _ = run_sweep(arrays, fee_bps=50)
    best = top_k[0]
    # Best combo must have positive P&L (selects the winning high-edge group)
    assert best["net_pnl_dollars"] > 0


def test_run_sweep_min_trades_threshold_skips_sparse_combos():
    """Combos with fewer than 10 trades should not appear in top_k."""
    # Very aggressive filters → most combos will have 0 trades
    arrays = _arrays(n=5)  # Only 5 predictions → most combos filtered to < 10
    top_k, _ = run_sweep(arrays, fee_bps=50)
    for r in top_k:
        assert r["n_trades"] >= 10


def test_run_sweep_marginals_cover_all_knobs():
    arrays = _arrays(n=30)
    _, marginals = run_sweep(arrays, fee_bps=50)
    knobs_in_marginals = {m["knob_name"] for m in marginals}
    assert knobs_in_marginals == set(KNOB_NAMES)


def test_run_sweep_top_k_has_all_required_fields():
    arrays = _arrays(n=30)
    top_k, _ = run_sweep(arrays, fee_bps=50)
    if not top_k:
        pytest.skip("No valid combos with this data size")
    row = top_k[0]
    for field in ["net_pnl_dollars", "win_rate", "n_trades", "ev_per_contract", "starvation_rate"]:
        assert field in row
    for knob in KNOB_NAMES:
        assert knob in row


def test_param_grid_total_combinations():
    from itertools import product
    total = sum(1 for _ in product(*[PARAM_GRID[k] for k in KNOB_NAMES]))
    assert total == 7_500_000
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_analysis_sweep.py -v 2>&1 | head -15
```

Expected: `ModuleNotFoundError: No module named 'analysis.sweep'`

- [ ] **Step 3: Create `analysis/sweep.py`**

```python
# analysis/sweep.py
import itertools
import logging
import time
from datetime import datetime, timezone

import numpy as np
from sqlalchemy.orm import Session

from analysis.loader import load_settled_predictions
from shared.orm import SweepResult, SweepRun

logger = logging.getLogger(__name__)

KNOB_NAMES = [
    "min_fee_adjusted_edge_bps",
    "max_spread_bps",
    "crypto_live_min_market_age_seconds",
    "crypto_autonomy_min_seconds_to_close",
    "crypto_taker_fallback_close_seconds",
    "min_confidence",
    "min_contract_price_dollars",
    "crypto_market_price_anchor_weight",
    "crypto_late_sure_thing_min_probability",
    "crypto_late_sure_thing_min_market_probability",
]

PARAM_GRID = {
    "min_fee_adjusted_edge_bps": [250, 500, 750, 1000, 1500, 2000],
    "max_spread_bps": [100, 250, 500, 750, 1000],
    "crypto_live_min_market_age_seconds": [0, 60, 180, 300, 600],
    "crypto_autonomy_min_seconds_to_close": [0, 60, 120, 180, 300],
    "crypto_taker_fallback_close_seconds": [0, 30, 60, 90, 180],
    "min_confidence": [0.60, 0.70, 0.80, 0.90],
    "min_contract_price_dollars": [0.05, 0.10, 0.25, 0.50, 0.75],
    "crypto_market_price_anchor_weight": [0.00, 0.25, 0.50, 0.75, 1.00],
    "crypto_late_sure_thing_min_probability": [0.80, 0.85, 0.90, 0.95],
    "crypto_late_sure_thing_min_market_probability": [0.60, 0.70, 0.75, 0.80, 0.90],
}

_TOP_K = 500
_MIN_TRADES = 10
_LATE_SURE_THING_MAX_SECONDS = 300.0


def run_sweep(arrays: dict, fee_bps: int = 50) -> tuple[list[dict], list[dict]]:
    """
    Full grid sweep over PARAM_GRID. Returns (top_k_results, marginal_results).

    top_k_results: up to _TOP_K dicts, sorted by net_pnl_dollars descending.
    marginal_results: one dict per (knob_name, knob_value) with mean metrics
                      across all valid grid points that include that value.
    """
    if arrays["n"] < _MIN_TRADES:
        return [], []

    confidence = arrays["confidence"]
    entry_price = arrays["entry_price"]
    spread_bps = arrays["spread_bps"]
    raw_edge_bps = arrays["raw_edge_bps"]
    market_age_seconds = arrays["market_age_seconds"]
    seconds_to_close = arrays["seconds_to_close"]
    outcome_correct = arrays["outcome_correct"]
    raw_pnl = arrays["raw_pnl"]
    n = arrays["n"]
    fee_dollars = fee_bps / 10_000.0

    # Pre-compute anchored edge for each anchor weight.
    # anchored_edge = (1 - w) * raw_edge_bps - fee_bps
    anchored_edge: dict[float, np.ndarray] = {
        w: (1.0 - w) * raw_edge_bps - fee_bps
        for w in PARAM_GRID["crypto_market_price_anchor_weight"]
    }

    # Pre-compute all threshold masks. Each value is an N-length bool array.
    edge_masks: dict[tuple, np.ndarray] = {
        (w, t): anchored_edge[w] >= t
        for w in PARAM_GRID["crypto_market_price_anchor_weight"]
        for t in PARAM_GRID["min_fee_adjusted_edge_bps"]
    }
    spread_masks = {v: spread_bps <= v for v in PARAM_GRID["max_spread_bps"]}
    age_masks = {v: market_age_seconds >= v for v in PARAM_GRID["crypto_live_min_market_age_seconds"]}
    conf_masks = {v: confidence >= v for v in PARAM_GRID["min_confidence"]}
    price_masks = {v: entry_price >= v for v in PARAM_GRID["min_contract_price_dollars"]}

    # Late sure-thing masks: when near close, require lst thresholds.
    near_close = seconds_to_close <= _LATE_SURE_THING_MAX_SECONDS
    lst_masks: dict[tuple, np.ndarray] = {
        (lp, lm): ~near_close | ((confidence >= lp) & (entry_price >= lm))
        for lp in PARAM_GRID["crypto_late_sure_thing_min_probability"]
        for lm in PARAM_GRID["crypto_late_sure_thing_min_market_probability"]
    }

    # Unique seconds-to-close cutoffs (max of two timing knobs).
    unique_stc = {
        max(a, t)
        for a in PARAM_GRID["crypto_autonomy_min_seconds_to_close"]
        for t in PARAM_GRID["crypto_taker_fallback_close_seconds"]
    }
    stc_masks = {v: seconds_to_close >= v for v in unique_stc}

    # Marginal accumulators: sum net_pnl and ev across valid combos per (knob, value).
    marginal_pnl: dict[tuple, float] = {(k, v): 0.0 for k, vs in PARAM_GRID.items() for v in vs}
    marginal_ev: dict[tuple, float] = {(k, v): 0.0 for k, vs in PARAM_GRID.items() for v in vs}
    marginal_count: dict[tuple, int] = {(k, v): 0 for k, vs in PARAM_GRID.items() for v in vs}

    # Min-heap of (net_pnl, tie_counter, result_dict) — keeps top _TOP_K by net_pnl.
    import heapq
    top_heap: list = []
    _ctr = 0
    n_evaluated = 0

    for combo in itertools.product(*[PARAM_GRID[k] for k in KNOB_NAMES]):
        params = dict(zip(KNOB_NAMES, combo))
        w = params["crypto_market_price_anchor_weight"]
        edge_t = params["min_fee_adjusted_edge_bps"]
        stc_cutoff = max(
            params["crypto_autonomy_min_seconds_to_close"],
            params["crypto_taker_fallback_close_seconds"],
        )
        lp = params["crypto_late_sure_thing_min_probability"]
        lm = params["crypto_late_sure_thing_min_market_probability"]

        mask = (
            edge_masks[(w, edge_t)]
            & spread_masks[params["max_spread_bps"]]
            & age_masks[params["crypto_live_min_market_age_seconds"]]
            & stc_masks[stc_cutoff]
            & conf_masks[params["min_confidence"]]
            & price_masks[params["min_contract_price_dollars"]]
            & lst_masks[(lp, lm)]
        )

        n_trades = int(mask.sum())
        if n_trades < _MIN_TRADES:
            continue

        n_evaluated += 1
        pnl_slice = raw_pnl[mask]
        net_pnl = float(pnl_slice.sum()) - n_trades * fee_dollars
        ev = net_pnl / n_trades
        win_rate = float(outcome_correct[mask].mean())
        starvation = 1.0 - n_trades / n

        entry = (net_pnl, _ctr, {**params,
                                  "n_trades": n_trades,
                                  "win_rate": win_rate,
                                  "net_pnl_dollars": net_pnl,
                                  "ev_per_contract": ev,
                                  "starvation_rate": starvation})
        _ctr += 1
        if len(top_heap) < _TOP_K:
            heapq.heappush(top_heap, entry)
        elif net_pnl > top_heap[0][0]:
            heapq.heapreplace(top_heap, entry)

        for k, v in params.items():
            key = (k, v)
            marginal_pnl[key] += net_pnl
            marginal_ev[key] += ev
            marginal_count[key] += 1

    top_k = sorted(
        [e[2] for e in top_heap],
        key=lambda x: x["net_pnl_dollars"],
        reverse=True,
    )

    marginals = []
    for k, vs in PARAM_GRID.items():
        for v in vs:
            key = (k, v)
            cnt = marginal_count[key]
            if cnt == 0:
                continue
            marginals.append({
                "knob_name": k,
                "knob_value": str(v),
                "net_pnl_dollars": marginal_pnl[key] / cnt,
                "ev_per_contract": marginal_ev[key] / cnt,
                "n_trades": None,
                "win_rate": None,
                "starvation_rate": None,
                "_n_evaluated": n_evaluated,
            })

    return top_k, marginals


def execute_sweep(session: Session, run: SweepRun) -> None:
    """
    Orchestrates: load data → run sweep → write results → update run record.
    Mutates `run` in place. Caller is responsible for committing the session.
    """
    t0 = time.monotonic()
    arrays = load_settled_predictions(session)
    n = arrays["n"]
    run.n_settled_predictions = n

    if n < _MIN_TRADES:
        run.status = "complete"
        run.elapsed_seconds = float(time.monotonic() - t0)
        run.n_combinations_evaluated = 0
        logger.warning("Too few settled predictions (%d) to sweep", n)
        return

    logger.info("Starting sweep with %d settled predictions", n)
    top_k, marginals = run_sweep(arrays, fee_bps=run.fee_bps)
    elapsed = time.monotonic() - t0

    n_evaluated = marginals[0]["_n_evaluated"] if marginals else 0

    run.n_combinations_evaluated = n_evaluated
    run.elapsed_seconds = float(elapsed)
    run.status = "complete"

    if top_k:
        run.best_net_pnl_dollars = top_k[0]["net_pnl_dollars"]
        best = {k: top_k[0][k] for k in KNOB_NAMES}
        run.best_settings_json = best

    # Write top-k results
    for rank, r in enumerate(top_k, start=1):
        session.add(SweepResult(
            run_id=run.id,
            result_type="top_k",
            rank=rank,
            min_fee_adjusted_edge_bps=r["min_fee_adjusted_edge_bps"],
            max_spread_bps=r["max_spread_bps"],
            min_confidence=r["min_confidence"],
            min_contract_price_dollars=r["min_contract_price_dollars"],
            crypto_live_min_market_age_seconds=r["crypto_live_min_market_age_seconds"],
            crypto_autonomy_min_seconds_to_close=r["crypto_autonomy_min_seconds_to_close"],
            crypto_taker_fallback_close_seconds=r["crypto_taker_fallback_close_seconds"],
            crypto_market_price_anchor_weight=r["crypto_market_price_anchor_weight"],
            crypto_late_sure_thing_min_probability=r["crypto_late_sure_thing_min_probability"],
            crypto_late_sure_thing_min_market_probability=r["crypto_late_sure_thing_min_market_probability"],
            n_trades=r["n_trades"],
            win_rate=r["win_rate"],
            net_pnl_dollars=r["net_pnl_dollars"],
            ev_per_contract=r["ev_per_contract"],
            starvation_rate=r["starvation_rate"],
        ))

    # Write marginal results
    for m in marginals:
        session.add(SweepResult(
            run_id=run.id,
            result_type="marginal",
            knob_name=m["knob_name"],
            knob_value=m["knob_value"],
            net_pnl_dollars=m["net_pnl_dollars"],
            ev_per_contract=m["ev_per_contract"],
        ))

    session.flush()
    logger.info(
        "Sweep complete: %d combos evaluated, top P&L=%.4f, elapsed=%.1fs",
        n_evaluated, top_k[0]["net_pnl_dollars"] if top_k else 0.0, elapsed,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_analysis_sweep.py -v
```

Expected: all 7 tests pass. (Note: `test_param_grid_total_combinations` will take a moment — it generates 7.5M combinations to count them.)

- [ ] **Step 5: Commit**

```bash
git add analysis/sweep.py tests/test_analysis_sweep.py
git commit -m "feat: sweep engine — full grid search over 7.5M knob combinations"
```

---

## Task 5: Analysis Entry Point

**Files:**
- Create: `analysis/main.py`

- [ ] **Step 1: Create `analysis/main.py`**

```python
# analysis/main.py
import logging
from datetime import datetime, timezone

from shared.db import make_session_factory, session_scope
from shared.orm import SweepRun
from shared.settings import Settings
from analysis.sweep import execute_sweep

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def run_once(settings: Settings, session_factory) -> None:
    with session_scope(session_factory) as session:
        run = SweepRun(
            run_at=datetime.now(timezone.utc),
            status="running",
            fee_bps=50,
        )
        session.add(run)
        session.flush()
        try:
            execute_sweep(session, run)
        except Exception as exc:
            run.status = "failed"
            logger.error("Sweep failed: %s", exc)
            raise


if __name__ == "__main__":
    settings = Settings()
    session_factory = make_session_factory(settings)
    run_once(settings, session_factory)
```

- [ ] **Step 2: Verify the module is importable**

```bash
cd /workspace/crypto_analysis && python -c "from analysis.main import run_once; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add analysis/main.py
git commit -m "feat: analysis/main.py — CLI entry point for sweep"
```

---

## Task 6: API Endpoints

**Files:**
- Modify: `api/main.py`
- Create: `tests/test_api_analysis.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_analysis.py`:

```python
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from shared.orm import Market, Prediction, RawFeature, SweepRun, SweepResult


def _make_client(db_session):
    import api.main as api_module
    return TestClient(api_module.create_app(lambda: db_session))


def _seed_run(session, status="complete", best_pnl=1.23):
    run = SweepRun(
        run_at=datetime.now(timezone.utc),
        status=status,
        fee_bps=50,
        n_settled_predictions=100,
        n_combinations_evaluated=50000,
        elapsed_seconds=45.2,
        best_net_pnl_dollars=best_pnl,
        best_settings_json={"min_confidence": 0.80},
    )
    session.add(run)
    session.flush()
    return run


def _seed_result(session, run_id, result_type="top_k", rank=1, pnl=1.0):
    r = SweepResult(
        run_id=run_id,
        result_type=result_type,
        rank=rank if result_type == "top_k" else None,
        knob_name=None if result_type == "top_k" else "min_confidence",
        knob_value=None if result_type == "top_k" else "0.8",
        min_fee_adjusted_edge_bps=500,
        max_spread_bps=250,
        min_confidence=0.80,
        min_contract_price_dollars=0.10,
        crypto_live_min_market_age_seconds=180,
        crypto_autonomy_min_seconds_to_close=60,
        crypto_taker_fallback_close_seconds=90,
        crypto_market_price_anchor_weight=0.75,
        crypto_late_sure_thing_min_probability=0.90,
        crypto_late_sure_thing_min_market_probability=0.75,
        n_trades=120,
        win_rate=0.62,
        net_pnl_dollars=pnl,
        ev_per_contract=0.008,
        starvation_rate=0.15,
    )
    session.add(r)
    session.flush()
    return r


def test_get_analysis_runs_empty(db_session):
    client = _make_client(db_session)
    resp = client.get("/analysis/runs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_analysis_runs_returns_list(db_session):
    _seed_run(db_session)
    client = _make_client(db_session)
    resp = client.get("/analysis/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["status"] == "complete"
    assert data[0]["best_net_pnl_dollars"] == pytest.approx(1.23, abs=0.01)


def test_get_latest_run_404_when_none(db_session):
    client = _make_client(db_session)
    resp = client.get("/analysis/runs/latest")
    assert resp.status_code == 404


def test_get_latest_run_returns_completed(db_session):
    _seed_run(db_session, status="complete", best_pnl=2.50)
    client = _make_client(db_session)
    resp = client.get("/analysis/runs/latest")
    assert resp.status_code == 200
    assert resp.json()["best_net_pnl_dollars"] == pytest.approx(2.50, abs=0.01)


def test_get_run_results_top_k(db_session):
    run = _seed_run(db_session)
    _seed_result(db_session, run.id, result_type="top_k", rank=1, pnl=1.5)
    client = _make_client(db_session)
    resp = client.get(f"/analysis/runs/{run.id}/results?result_type=top_k")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["rank"] == 1
    assert data[0]["net_pnl_dollars"] == pytest.approx(1.5, abs=0.01)


def test_get_run_results_404_unknown_run(db_session):
    client = _make_client(db_session)
    resp = client.get("/analysis/runs/9999/results?result_type=top_k")
    assert resp.status_code == 404


def test_post_analysis_runs_with_no_data(db_session):
    """POST with empty DB returns a completed run with 0 predictions."""
    client = _make_client(db_session)
    resp = client.post("/analysis/runs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("complete", "failed")
    assert "run_id" in body
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_api_analysis.py -v 2>&1 | head -15
```

Expected: `404` or `422` errors — the endpoints don't exist yet.

- [ ] **Step 3: Add the four analysis endpoints to `api/main.py`**

Inside `create_app()`, after the existing `@app.get("/stats/training")` handler and before `return app`, add:

```python
    # ── Analysis sweep endpoints ───────────────────────────────────────────────
    from shared.orm import SweepRun as _SweepRun, SweepResult as _SweepResult

    def _fmt_run(r: _SweepRun) -> dict:
        return {
            "run_id": r.id,
            "run_at": r.run_at.isoformat() if r.run_at else None,
            "status": r.status,
            "n_settled_predictions": r.n_settled_predictions,
            "fee_bps": r.fee_bps,
            "n_combinations_evaluated": r.n_combinations_evaluated,
            "elapsed_seconds": float(r.elapsed_seconds) if r.elapsed_seconds is not None else None,
            "best_net_pnl_dollars": float(r.best_net_pnl_dollars) if r.best_net_pnl_dollars is not None else None,
            "best_settings_json": r.best_settings_json,
        }

    def _fmt_result(r: _SweepResult) -> dict:
        def _f(v):
            return float(v) if v is not None else None
        return {
            "id": r.id,
            "result_type": r.result_type,
            "rank": r.rank,
            "knob_name": r.knob_name,
            "knob_value": r.knob_value,
            "min_fee_adjusted_edge_bps": r.min_fee_adjusted_edge_bps,
            "max_spread_bps": r.max_spread_bps,
            "min_confidence": _f(r.min_confidence),
            "min_contract_price_dollars": _f(r.min_contract_price_dollars),
            "crypto_live_min_market_age_seconds": r.crypto_live_min_market_age_seconds,
            "crypto_autonomy_min_seconds_to_close": r.crypto_autonomy_min_seconds_to_close,
            "crypto_taker_fallback_close_seconds": r.crypto_taker_fallback_close_seconds,
            "crypto_market_price_anchor_weight": _f(r.crypto_market_price_anchor_weight),
            "crypto_late_sure_thing_min_probability": _f(r.crypto_late_sure_thing_min_probability),
            "crypto_late_sure_thing_min_market_probability": _f(r.crypto_late_sure_thing_min_market_probability),
            "n_trades": r.n_trades,
            "win_rate": _f(r.win_rate),
            "net_pnl_dollars": _f(r.net_pnl_dollars),
            "ev_per_contract": _f(r.ev_per_contract),
            "starvation_rate": _f(r.starvation_rate),
        }

    @app.get("/analysis/runs/latest")
    def get_latest_analysis_run(session: Session = Depends(_get_db)):
        run = (
            session.query(_SweepRun)
            .filter(_SweepRun.status == "complete")
            .order_by(_SweepRun.run_at.desc())
            .first()
        )
        if run is None:
            raise HTTPException(status_code=404, detail="No completed sweep runs")
        return _fmt_run(run)

    @app.get("/analysis/runs")
    def get_analysis_runs(session: Session = Depends(_get_db)):
        runs = (
            session.query(_SweepRun)
            .order_by(_SweepRun.run_at.desc())
            .limit(20)
            .all()
        )
        return [_fmt_run(r) for r in runs]

    @app.get("/analysis/runs/{run_id}/results")
    def get_analysis_run_results(
        run_id: int,
        result_type: str = "top_k",
        session: Session = Depends(_get_db),
    ):
        if session.get(_SweepRun, run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        results = (
            session.query(_SweepResult)
            .filter(
                _SweepResult.run_id == run_id,
                _SweepResult.result_type == result_type,
            )
            .order_by(_SweepResult.rank)
            .all()
        )
        return [_fmt_result(r) for r in results]

    @app.post("/analysis/runs")
    def trigger_analysis_run(session: Session = Depends(_get_db)):
        from analysis.sweep import execute_sweep as _execute_sweep
        run = _SweepRun(
            run_at=datetime.now(timezone.utc),
            status="running",
            fee_bps=50,
        )
        session.add(run)
        session.flush()
        try:
            _execute_sweep(session, run)
        except Exception as exc:
            run.status = "failed"
            raise HTTPException(status_code=500, detail=str(exc))
        return _fmt_run(run)
```

**Important:** Register `/analysis/runs/latest` **before** `/analysis/runs/{run_id}/results` so FastAPI matches the literal path first. The order above is correct.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_api_analysis.py -v
```

Expected: all 8 tests pass.

- [ ] **Step 5: Run the full test suite to check for regressions**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add api/main.py tests/test_api_analysis.py
git commit -m "feat: analysis API endpoints — GET runs, GET latest, GET results, POST trigger"
```

---

## Task 7: TypeScript API Types and Fetch Functions

**Files:**
- Modify: `dashboard/src/api.ts`

- [ ] **Step 1: Append Analysis types and fetch functions to `dashboard/src/api.ts`**

Add the following at the end of `dashboard/src/api.ts`:

```typescript
// ── Analysis Sweep ─────────────────────────────────────────────────────────────

export interface SweepRun {
  run_id: number
  run_at: string
  status: 'running' | 'complete' | 'failed'
  n_settled_predictions: number | null
  fee_bps: number
  n_combinations_evaluated: number | null
  elapsed_seconds: number | null
  best_net_pnl_dollars: number | null
  best_settings_json: Record<string, number> | null
}

export interface SweepResultRow {
  id: number
  result_type: 'top_k' | 'marginal'
  rank: number | null
  knob_name: string | null
  knob_value: string | null
  min_fee_adjusted_edge_bps: number | null
  max_spread_bps: number | null
  min_confidence: number | null
  min_contract_price_dollars: number | null
  crypto_live_min_market_age_seconds: number | null
  crypto_autonomy_min_seconds_to_close: number | null
  crypto_taker_fallback_close_seconds: number | null
  crypto_market_price_anchor_weight: number | null
  crypto_late_sure_thing_min_probability: number | null
  crypto_late_sure_thing_min_market_probability: number | null
  n_trades: number | null
  win_rate: number | null
  net_pnl_dollars: number | null
  ev_per_contract: number | null
  starvation_rate: number | null
}

export const fetchAnalysisRuns = (): Promise<SweepRun[]> =>
  apiFetch<SweepRun[]>('/analysis/runs')

export const fetchLatestAnalysisRun = (): Promise<SweepRun> =>
  apiFetch<SweepRun>('/analysis/runs/latest')

export const fetchAnalysisResults = (
  runId: number,
  resultType: 'top_k' | 'marginal',
): Promise<SweepResultRow[]> =>
  apiFetch<SweepResultRow[]>(`/analysis/runs/${runId}/results?result_type=${resultType}`)

export const triggerAnalysisRun = (): Promise<SweepRun> => {
  return fetch('/api/analysis/runs', { method: 'POST' }).then(res => {
    if (!res.ok) throw new Error(`${res.status} — POST /analysis/runs`)
    return res.json() as Promise<SweepRun>
  })
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd /workspace/crypto_analysis/dashboard && npx tsc --noEmit 2>&1 | head -20
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/api.ts
git commit -m "feat: TypeScript Analysis API types and fetch functions"
```

---

## Task 8: Analysis Dashboard View

**Files:**
- Create: `dashboard/src/views/Analysis.tsx`
- Create: `dashboard/src/views/Analysis.module.css`

- [ ] **Step 1: Create `dashboard/src/views/Analysis.module.css`**

```css
.page { display: flex; flex-direction: column; gap: 1.5rem; }

.section { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem; }

.sectionTitle {
  font-size: 0.85rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  margin: 0 0 1rem;
}

.runTable { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
.runTable th { text-align: left; padding: 0.4rem 0.6rem; color: var(--text-muted); font-weight: 500; border-bottom: 1px solid var(--border); }
.runTable td { padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border); cursor: pointer; }
.runTable tr:hover td { background: var(--surface-hover, #f5f5f5); }
.runTable tr.active td { background: var(--accent-faint, #eef4ff); }

.status-complete { color: var(--green); }
.status-running { color: var(--amber); }
.status-failed { color: var(--red); }

.triggerBtn {
  padding: 0.4rem 1rem;
  background: var(--accent, #2563eb);
  color: #fff;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.85rem;
}
.triggerBtn:disabled { opacity: 0.5; cursor: not-allowed; }

.topKTable { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
.topKTable th { text-align: right; padding: 0.3rem 0.5rem; color: var(--text-muted); font-weight: 500; border-bottom: 1px solid var(--border); }
.topKTable th:first-child { text-align: left; }
.topKTable td { text-align: right; padding: 0.3rem 0.5rem; border-bottom: 1px solid var(--border); }
.topKTable td:first-child { text-align: left; }
.topKTable tr:hover td { background: var(--surface-hover, #f5f5f5); }

.chartWrap { width: 100%; height: 260px; }

.noData { color: var(--text-muted); font-size: 0.85rem; padding: 1rem 0; }

.headerRow { display: flex; align-items: center; justify-content: space-between; margin-bottom: 1rem; }

.bestCard { background: var(--accent-faint, #eef4ff); border: 1px solid var(--accent, #2563eb); border-radius: 6px; padding: 0.75rem 1rem; font-size: 0.82rem; }
.bestCard pre { margin: 0.5rem 0 0; font-size: 0.78rem; white-space: pre-wrap; }
```

- [ ] **Step 2: Create `dashboard/src/views/Analysis.tsx`**

```tsx
import { useCallback, useEffect, useState } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  LineChart, Line, CartesianGrid,
} from 'recharts'
import { useRefreshContext } from '../App'
import {
  fetchAnalysisRuns, fetchAnalysisResults, triggerAnalysisRun,
} from '../api'
import type { SweepRun, SweepResultRow } from '../api'
import { useAutoRefresh } from '../hooks/useAutoRefresh'
import styles from './Analysis.module.css'

const KNOB_LABELS: Record<string, string> = {
  min_fee_adjusted_edge_bps: 'Min Edge (bps)',
  max_spread_bps: 'Max Spread (bps)',
  crypto_live_min_market_age_seconds: 'Min Market Age (s)',
  crypto_autonomy_min_seconds_to_close: 'Min Secs to Close',
  crypto_taker_fallback_close_seconds: 'Taker Fallback (s)',
  min_confidence: 'Min Confidence',
  min_contract_price_dollars: 'Min Contract Price ($)',
  crypto_market_price_anchor_weight: 'Anchor Weight',
  crypto_late_sure_thing_min_probability: 'LST Min Prob',
  crypto_late_sure_thing_min_market_probability: 'LST Min Market Prob',
}

function fmt(v: number | null, digits = 4): string {
  return v == null ? '—' : v.toFixed(digits)
}

function KnobImportanceChart({
  marginals,
  selectedKnob,
  onKnobClick,
}: {
  marginals: SweepResultRow[]
  selectedKnob: string | null
  onKnobClick: (knob: string) => void
}) {
  // For each knob, compute (max_avg_pnl - min_avg_pnl) as leverage
  const byKnob: Record<string, number[]> = {}
  for (const m of marginals) {
    if (!m.knob_name || m.net_pnl_dollars == null) continue
    ;(byKnob[m.knob_name] ??= []).push(m.net_pnl_dollars)
  }
  const data = Object.entries(byKnob).map(([knob, pnls]) => ({
    knob,
    label: KNOB_LABELS[knob] ?? knob,
    leverage: Math.max(...pnls) - Math.min(...pnls),
    selected: knob === selectedKnob,
  })).sort((a, b) => b.leverage - a.leverage)

  return (
    <div className={styles.chartWrap}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ left: 160, right: 20 }}
          onClick={e => e?.activePayload?.[0] && onKnobClick(e.activePayload[0].payload.knob)}>
          <XAxis type="number" tickFormatter={v => `$${v.toFixed(2)}`} />
          <YAxis type="category" dataKey="label" width={155} tick={{ fontSize: 11 }} />
          <Tooltip formatter={(v: number) => [`$${v.toFixed(4)}`, 'P&L Leverage']} />
          <Bar dataKey="leverage" fill="var(--accent, #2563eb)" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function KnobDetailChart({ marginals, knob }: { marginals: SweepResultRow[]; knob: string }) {
  const data = marginals
    .filter(m => m.knob_name === knob && m.knob_value != null && m.net_pnl_dollars != null)
    .map(m => ({ value: Number(m.knob_value), pnl: m.net_pnl_dollars as number }))
    .sort((a, b) => a.value - b.value)

  return (
    <div className={styles.chartWrap}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ left: 20, right: 20 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="value" label={{ value: KNOB_LABELS[knob] ?? knob, position: 'insideBottom', offset: -4 }} />
          <YAxis tickFormatter={v => `$${v.toFixed(2)}`} />
          <Tooltip formatter={(v: number) => [`$${v.toFixed(4)}`, 'Avg Net P&L']} />
          <Line type="monotone" dataKey="pnl" stroke="var(--accent, #2563eb)" dot strokeWidth={2} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function TopKTable({ results }: { results: SweepResultRow[] }) {
  const [sortBy, setSortBy] = useState<'net_pnl_dollars' | 'win_rate' | 'ev_per_contract' | 'starvation_rate'>('net_pnl_dollars')
  const sorted = [...results].sort((a, b) => {
    if (sortBy === 'starvation_rate') return (a[sortBy] ?? 0) - (b[sortBy] ?? 0)
    return (b[sortBy] ?? 0) - (a[sortBy] ?? 0)
  })

  const cols: { key: typeof sortBy; label: string }[] = [
    { key: 'net_pnl_dollars', label: 'Net P&L ($)' },
    { key: 'win_rate', label: 'Win Rate' },
    { key: 'ev_per_contract', label: 'EV/Contract' },
    { key: 'starvation_rate', label: 'Starvation' },
  ]

  return (
    <div style={{ overflowX: 'auto' }}>
      <table className={styles.topKTable}>
        <thead>
          <tr>
            <th>Rank</th>
            {cols.map(c => (
              <th key={c.key} onClick={() => setSortBy(c.key)}
                style={{ cursor: 'pointer', color: sortBy === c.key ? 'var(--accent, #2563eb)' : undefined }}>
                {c.label} {sortBy === c.key ? '↓' : ''}
              </th>
            ))}
            <th>Trades</th>
            <th>Edge (bps)</th>
            <th>Spread (bps)</th>
            <th>Confidence</th>
            <th>Anchor W</th>
            <th>Age (s)</th>
            <th>STC (s)</th>
          </tr>
        </thead>
        <tbody>
          {sorted.slice(0, 100).map((r, i) => (
            <tr key={r.id}>
              <td>{r.rank ?? i + 1}</td>
              <td>{fmt(r.net_pnl_dollars)}</td>
              <td>{r.win_rate != null ? `${(r.win_rate * 100).toFixed(1)}%` : '—'}</td>
              <td>{fmt(r.ev_per_contract)}</td>
              <td>{r.starvation_rate != null ? `${(r.starvation_rate * 100).toFixed(1)}%` : '—'}</td>
              <td>{r.n_trades ?? '—'}</td>
              <td>{r.min_fee_adjusted_edge_bps ?? '—'}</td>
              <td>{r.max_spread_bps ?? '—'}</td>
              <td>{r.min_confidence ?? '—'}</td>
              <td>{r.crypto_market_price_anchor_weight ?? '—'}</td>
              <td>{r.crypto_live_min_market_age_seconds ?? '—'}</td>
              <td>{r.crypto_autonomy_min_seconds_to_close ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function Analysis({ intervalMs }: { intervalMs: number }) {
  const [runs, setRuns] = useState<SweepRun[]>([])
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)
  const [topK, setTopK] = useState<SweepResultRow[]>([])
  const [marginals, setMarginals] = useState<SweepResultRow[]>([])
  const [selectedKnob, setSelectedKnob] = useState<string | null>(null)
  const [isTriggering, setIsTriggering] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const ctx = useRefreshContext()

  const loadRuns = useCallback(async () => {
    ctx.setIsLoading(true)
    try {
      const data = await fetchAnalysisRuns()
      setRuns(data)
      ctx.setLastRefreshed(new Date())
    } catch {
      setError('Failed to load sweep runs')
    } finally {
      ctx.setIsLoading(false)
    }
  }, [ctx])

  useEffect(() => { ctx.setRefreshFn(loadRuns) }, [ctx, loadRuns])
  useAutoRefresh(loadRuns, intervalMs)

  useEffect(() => {
    if (selectedRunId == null) return
    Promise.all([
      fetchAnalysisResults(selectedRunId, 'top_k'),
      fetchAnalysisResults(selectedRunId, 'marginal'),
    ]).then(([tk, mg]) => {
      setTopK(tk)
      setMarginals(mg)
    }).catch(() => setError('Failed to load results'))
  }, [selectedRunId])

  const handleTrigger = async () => {
    setIsTriggering(true)
    setError(null)
    try {
      await triggerAnalysisRun()
      await loadRuns()
    } catch {
      setError('Sweep failed — check API logs')
    } finally {
      setIsTriggering(false)
    }
  }

  const selectedRun = runs.find(r => r.run_id === selectedRunId) ?? null

  return (
    <div className={styles.page}>
      {error && <div style={{ color: 'var(--red)', fontSize: '0.85rem' }}>{error}</div>}

      {/* Run history */}
      <div className={styles.section}>
        <div className={styles.headerRow}>
          <h3 className={styles.sectionTitle}>Sweep Runs</h3>
          <button className={styles.triggerBtn} onClick={handleTrigger} disabled={isTriggering}>
            {isTriggering ? 'Running… (~2 min)' : '▶ Run Sweep Now'}
          </button>
        </div>
        {runs.length === 0 ? (
          <p className={styles.noData}>No sweep runs yet. Click "Run Sweep Now" to start.</p>
        ) : (
          <table className={styles.runTable}>
            <thead>
              <tr>
                <th>Date</th><th>Status</th><th>Predictions</th>
                <th>Combos</th><th>Elapsed</th><th>Best P&L ($)</th>
              </tr>
            </thead>
            <tbody>
              {runs.map(r => (
                <tr key={r.run_id} className={selectedRunId === r.run_id ? styles.active : ''}
                  onClick={() => { setSelectedRunId(r.run_id); setSelectedKnob(null) }}>
                  <td>{new Date(r.run_at).toLocaleString()}</td>
                  <td className={styles[`status-${r.status}`]}>{r.status}</td>
                  <td>{r.n_settled_predictions ?? '—'}</td>
                  <td>{r.n_combinations_evaluated?.toLocaleString() ?? '—'}</td>
                  <td>{r.elapsed_seconds != null ? `${r.elapsed_seconds.toFixed(0)}s` : '—'}</td>
                  <td>{r.best_net_pnl_dollars != null ? `$${r.best_net_pnl_dollars.toFixed(4)}` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Knob importance */}
      {selectedRun && marginals.length > 0 && (
        <div className={styles.section}>
          <h3 className={styles.sectionTitle}>
            Knob Importance — click a bar to drill in
          </h3>
          <KnobImportanceChart
            marginals={marginals}
            selectedKnob={selectedKnob}
            onKnobClick={setSelectedKnob}
          />
        </div>
      )}

      {/* Per-knob detail */}
      {selectedKnob && marginals.length > 0 && (
        <div className={styles.section}>
          <h3 className={styles.sectionTitle}>
            {KNOB_LABELS[selectedKnob] ?? selectedKnob} — Avg P&L by Value
          </h3>
          <KnobDetailChart marginals={marginals} knob={selectedKnob} />
        </div>
      )}

      {/* Best settings card */}
      {selectedRun?.best_settings_json && (
        <div className={styles.bestCard}>
          <strong>Best settings (#{selectedRun.run_id})</strong>
          <pre>{JSON.stringify(selectedRun.best_settings_json, null, 2)}</pre>
        </div>
      )}

      {/* Top-K table */}
      {selectedRun && topK.length > 0 && (
        <div className={styles.section}>
          <h3 className={styles.sectionTitle}>Top Results — showing top 100 of {topK.length}</h3>
          <TopKTable results={topK} />
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd /workspace/crypto_analysis/dashboard && npx tsc --noEmit 2>&1 | head -20
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/views/Analysis.tsx dashboard/src/views/Analysis.module.css
git commit -m "feat: Analysis dashboard tab — run history, knob importance, top-K table"
```

---

## Task 9: Wire Analysis into App.tsx

**Files:**
- Modify: `dashboard/src/App.tsx`

- [ ] **Step 1: Add the Analysis import, nav item, and route**

In `dashboard/src/App.tsx`:

**Add import** after the existing view imports:
```tsx
import Analysis from './views/Analysis'
```

**Add nav item** in the `navItems` array inside `Sidebar`:
```tsx
{ to: '/analysis', label: '🔬 Analysis' },
```

**Add `VIEW_TITLES` entry**:
```tsx
'/analysis': 'Sweep Analysis',
```

**Add route** inside `<Routes>`:
```tsx
<Route path="/analysis" element={<Analysis intervalMs={REFRESH_INTERVAL_MS} />} />
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd /workspace/crypto_analysis/dashboard && npx tsc --noEmit 2>&1 | head -20
```

Expected: no errors.

- [ ] **Step 3: Run the full Python test suite one final time**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/ -v --tb=short 2>&1 | tail -25
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/App.tsx
git commit -m "feat: wire Analysis tab into dashboard nav and routing"
```

---

## Self-Review

**Spec coverage check:**

| Spec section | Covered by |
|---|---|
| `analysis/loader.py` — join + derived columns | Task 3 |
| Full grid: `itertools.product`, 7.5M combos | Task 4 |
| Pre-computed boolean masks (vectorised) | Task 4, sweep.py |
| `max(autonomy, taker)` stc cutoff | Task 4, sweep.py |
| Anchor weight re-derives edge | Task 4, sweep.py |
| `n_trades < 10` skip | Task 4, sweep.py |
| Top-500 storage | Task 4 + 6 |
| Per-knob marginals | Task 4 + 6 |
| `sweep_runs` table | Task 1 + 2 |
| `sweep_results` table | Task 1 + 2 |
| `GET /analysis/runs` | Task 6 |
| `GET /analysis/runs/latest` | Task 6 |
| `GET /analysis/runs/{id}/results` | Task 6 |
| `POST /analysis/runs` | Task 6 |
| Knob importance bar chart | Task 8 |
| Per-knob detail line chart | Task 8 |
| Top-K sortable table | Task 8 |
| Best settings card | Task 8 |
| Run history strip | Task 8 |
| "Run sweep now" button | Task 8 |
| Dashboard route + nav | Task 9 |

**No placeholders, no TODOs, no vague steps found.**

**Type consistency check:** `SweepRun`, `SweepResult` ORM class names match exactly across `shared/orm.py` → `api/main.py` → `tests/test_api_analysis.py`. `execute_sweep(session, run)` signature consistent across `analysis/sweep.py`, `api/main.py`, and `analysis/main.py`. TypeScript types in `api.ts` match API response shapes from `_fmt_run` / `_fmt_result`.
