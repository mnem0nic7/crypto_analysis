# Kalshi Crypto 15-Minute Prediction Module — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build three microservices (Ingestor, Predictor, API) and an offline Trainer that collectively predict binary UP/DOWN outcomes for active Kalshi 15-minute crypto markets using Coinbase price/order-book data and Kalshi contract prices.

**Architecture:** Three Docker containers share a PostgreSQL database. The Ingestor polls Coinbase and Kalshi every 30s and writes raw features. The Predictor runs XGBoost inference every 60s and writes predictions. The FastAPI service serves predictions read-only. The Trainer is an offline script triggered by env-var gate.

**Tech Stack:** Python 3.11, XGBoost 2.0, scikit-learn, FastAPI, SQLAlchemy 2.0, Alembic, httpx, cryptography, PyJWT, joblib, Docker Compose

---

## File Map

```
crypto_analysis/
├── docker-compose.yml
├── pyproject.toml                        # dev tooling config (pytest, ruff)
├── models/                               # model artifacts — gitignored
│   └── .gitkeep
├── shared/                               # code shared by all services
│   ├── __init__.py
│   ├── settings.py                       # pydantic-settings from .env
│   ├── db.py                             # SQLAlchemy engine + session factory
│   └── orm.py                            # ORM models: Market, RawFeature, Prediction, ModelRegistry
├── alembic/
│   ├── alembic.ini
│   ├── env.py
│   └── versions/
│       └── 001_initial_schema.py         # creates all 4 tables + indexes
├── ingestor/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                           # polling loop + /health endpoint
│   ├── kalshi_client.py                  # Kalshi REST client with RSA-PSS auth
│   ├── coinbase_client.py                # Coinbase Advanced Trade client with CDP JWT auth
│   ├── market_discovery.py               # discover + upsert active Kalshi crypto markets
│   └── feature_writer.py                 # fetch data, compute derived fields, write raw_features
├── predictor/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                           # inference loop
│   ├── feature_builder.py                # build 25-dim feature vector from DB rows
│   ├── model_loader.py                   # load + hot-reload XGBoost models from model_registry
│   └── inference.py                      # run inference, write predictions
├── api/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py                           # FastAPI app: /markets, /predict, /history, /health
├── trainer/
│   ├── requirements.txt
│   ├── main.py                           # training campaign entry point with cooldown gate
│   ├── dataset.py                        # pull + label training data from DB
│   └── train.py                          # XGBoost train + Brier evaluation + promotion
└── tests/
    ├── conftest.py                        # fixtures: in-memory SQLite session, mock clients
    ├── test_settings.py
    ├── test_orm.py
    ├── test_kalshi_client.py
    ├── test_coinbase_client.py
    ├── test_market_discovery.py
    ├── test_feature_writer.py
    ├── test_feature_builder.py
    ├── test_model_loader.py
    ├── test_inference.py
    ├── test_api.py
    └── test_trainer.py
```

---

### Task 1: Project scaffolding and shared dependencies

**Files:**
- Create: `pyproject.toml`
- Create: `models/.gitkeep`
- Modify: `.gitignore` (append)
- Create: `shared/__init__.py`

- [ ] **Step 1: Append to .gitignore**

Add to end of `.gitignore` (create if missing):
```
models/*.joblib
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
dist/
.ruff_cache/
```

- [ ] **Step 2: Create pyproject.toml**

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
```

- [ ] **Step 3: Create models directory**

```bash
mkdir -p models && touch models/.gitkeep
```

- [ ] **Step 4: Create shared/__init__.py**

```python
```

- [ ] **Step 5: Install base dev dependencies**

```bash
pip install pytest pytest-asyncio httpx sqlalchemy psycopg2-binary pydantic-settings alembic xgboost scikit-learn numpy pandas joblib cryptography PyJWT fastapi uvicorn
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml models/.gitkeep .gitignore shared/__init__.py
git commit -m "chore: project scaffolding and shared package"
```

---

### Task 2: Shared Settings

**Files:**
- Create: `shared/settings.py`
- Create: `tests/test_settings.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_settings.py
import pytest
from shared.settings import Settings


def test_settings_demo_key(monkeypatch):
    monkeypatch.setenv("DEMO_KALSHI_API_KEY", "demo-key")
    monkeypatch.setenv("DEMO_KALSHI_READ_PRIVATE_KEY_PATH", "Kalshi-2-Demo.txt")
    monkeypatch.setenv("DEMO_KALSHI_WRITE_PRIVATE_KEY_PATH", "Kalshi-2-Demo.txt")
    monkeypatch.setenv("LIVE_KALSHI_API_KEY", "live-key")
    monkeypatch.setenv("LIVE_KALSHI_READ_PRIVATE_KEY_PATH", "Kalshi-1.txt")
    monkeypatch.setenv("POSTGRES_PASSWORD", "postgres")
    monkeypatch.setenv("COINBASE_CDP_KEY_NAME", "orgs/x/apiKeys/y")
    monkeypatch.setenv("COINBASE_CDP_PRIVATE_KEY", "dummy-key")
    s = Settings()
    assert s.kalshi_api_key == "demo-key"
    assert s.kalshi_env == "demo"
    assert s.risk_stale_market_seconds == 60


def test_settings_db_url_contains_password(monkeypatch):
    monkeypatch.setenv("DEMO_KALSHI_API_KEY", "k")
    monkeypatch.setenv("DEMO_KALSHI_READ_PRIVATE_KEY_PATH", "p")
    monkeypatch.setenv("DEMO_KALSHI_WRITE_PRIVATE_KEY_PATH", "p")
    monkeypatch.setenv("LIVE_KALSHI_API_KEY", "k")
    monkeypatch.setenv("LIVE_KALSHI_READ_PRIVATE_KEY_PATH", "p")
    monkeypatch.setenv("POSTGRES_PASSWORD", "supersecret")
    monkeypatch.setenv("COINBASE_CDP_KEY_NAME", "x")
    monkeypatch.setenv("COINBASE_CDP_PRIVATE_KEY", "y")
    s = Settings()
    assert "supersecret" in s.db_url
    assert s.db_url.startswith("postgresql://")


def test_settings_live_base_url(monkeypatch):
    monkeypatch.setenv("DEMO_KALSHI_API_KEY", "k")
    monkeypatch.setenv("DEMO_KALSHI_READ_PRIVATE_KEY_PATH", "p")
    monkeypatch.setenv("DEMO_KALSHI_WRITE_PRIVATE_KEY_PATH", "p")
    monkeypatch.setenv("LIVE_KALSHI_API_KEY", "live-key")
    monkeypatch.setenv("LIVE_KALSHI_READ_PRIVATE_KEY_PATH", "p")
    monkeypatch.setenv("POSTGRES_PASSWORD", "x")
    monkeypatch.setenv("COINBASE_CDP_KEY_NAME", "x")
    monkeypatch.setenv("COINBASE_CDP_PRIVATE_KEY", "y")
    monkeypatch.setenv("KALSHI_ENV", "live")
    s = Settings()
    assert "trading-api.kalshi.com" in s.kalshi_base_url
    assert s.kalshi_api_key == "live-key"
```

- [ ] **Step 2: Run test to verify failure**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_settings.py -v
```
Expected: `ImportError: cannot import name 'Settings'`

- [ ] **Step 3: Create shared/settings.py**

```python
# shared/settings.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Kalshi credentials
    demo_kalshi_api_key: str
    demo_kalshi_read_private_key_path: str
    demo_kalshi_write_private_key_path: str
    live_kalshi_api_key: str
    live_kalshi_read_private_key_path: str
    kalshi_env: str = "demo"  # "demo" | "live"

    # Postgres
    postgres_password: str
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_db: str = "crypto_analysis"

    # Coinbase CDP
    coinbase_cdp_key_name: str
    coinbase_cdp_private_key: str

    # Risk / ingestor
    risk_stale_market_seconds: int = 60

    # Training campaign
    training_campaign_enabled: bool = False
    training_campaign_lookback_hours: int = 24
    training_campaign_cooldown_seconds: int = 600
    training_campaign_max_recent_per_market: int = 5

    @property
    def kalshi_api_key(self) -> str:
        return self.demo_kalshi_api_key if self.kalshi_env == "demo" else self.live_kalshi_api_key

    @property
    def kalshi_private_key_path(self) -> str:
        return (
            self.demo_kalshi_read_private_key_path
            if self.kalshi_env == "demo"
            else self.live_kalshi_read_private_key_path
        )

    @property
    def kalshi_base_url(self) -> str:
        if self.kalshi_env == "demo":
            return "https://demo-api.kalshi.co/trade-api/v2"
        return "https://trading-api.kalshi.com/trade-api/v2"

    @property
    def db_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )
```

- [ ] **Step 4: Run tests to verify passing**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_settings.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add shared/settings.py tests/test_settings.py
git commit -m "feat: shared Settings from .env via pydantic-settings"
```

---

### Task 3: Shared ORM models and Alembic migration

**Files:**
- Create: `shared/orm.py`
- Create: `shared/db.py`
- Create: `alembic/alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/001_initial_schema.py`
- Create: `tests/test_orm.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_orm.py
from shared.orm import Market, RawFeature, Prediction, ModelRegistry


def test_table_names():
    assert Market.__tablename__ == "markets"
    assert RawFeature.__tablename__ == "raw_features"
    assert Prediction.__tablename__ == "predictions"
    assert ModelRegistry.__tablename__ == "model_registry"


def test_raw_feature_has_derived_columns():
    cols = {c.name for c in RawFeature.__table__.columns}
    assert "price_momentum_1m" in cols
    assert "volatility_5m" in cols
    assert "book_imbalance" in cols


def test_prediction_has_audit_columns():
    cols = {c.name for c in Prediction.__table__.columns}
    assert "actual_outcome" in cols
    assert "low_confidence" in cols
    assert "feature_snapshot_id" in cols


def test_model_registry_has_promotion_columns():
    cols = {c.name for c in ModelRegistry.__table__.columns}
    assert "is_active" in cols
    assert "brier_score" in cols


def test_db_session_fixture_works(db_session):
    from shared.orm import Market
    from datetime import datetime, timezone
    m = Market(
        market_id="TEST-001",
        ticker="KXBTCUSD",
        status="active",
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(m)
    db_session.flush()
    result = db_session.get(Market, "TEST-001")
    assert result is not None
    assert result.ticker == "KXBTCUSD"
```

- [ ] **Step 2: Create tests/conftest.py**

```python
# tests/conftest.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from shared.orm import Base


@pytest.fixture(scope="session")
def test_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session(test_engine):
    Session = sessionmaker(bind=test_engine)
    session = Session()
    yield session
    session.rollback()
    session.close()
```

- [ ] **Step 3: Run tests to verify failure**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_orm.py -v
```
Expected: `ImportError`

- [ ] **Step 4: Create shared/orm.py**

```python
# shared/orm.py
from sqlalchemy import (
    BigInteger, Boolean, Column, ForeignKey, Integer, Numeric,
    SmallInteger, Text, TIMESTAMP, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Market(Base):
    __tablename__ = "markets"

    market_id = Column(Text, primary_key=True)
    ticker = Column(Text, nullable=False)          # e.g. "KXBTCUSD"
    title = Column(Text)
    close_time = Column(TIMESTAMP(timezone=True))
    status = Column(Text, nullable=False, default="active")  # active | stale | closed
    discovered_at = Column(TIMESTAMP(timezone=True), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False)


class RawFeature(Base):
    __tablename__ = "raw_features"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    market_id = Column(Text, ForeignKey("markets.market_id"), nullable=False)
    ts = Column(TIMESTAMP(timezone=True), nullable=False)
    # Coinbase price
    price_open = Column(Numeric)
    price_high = Column(Numeric)
    price_low = Column(Numeric)
    price_close = Column(Numeric)
    volume = Column(Numeric)
    # Order book
    bid_depth_1pct = Column(Numeric)
    ask_depth_1pct = Column(Numeric)
    book_imbalance = Column(Numeric)               # (bid-ask)/(bid+ask), range [-1,1]
    # Kalshi contract
    kalshi_yes_price = Column(Numeric)             # 0.0–1.0 implied probability
    kalshi_no_price = Column(Numeric)
    kalshi_volume = Column(Numeric)
    # Derived (computed on insert)
    price_momentum_1m = Column(Numeric)
    price_momentum_5m = Column(Numeric)
    price_momentum_15m = Column(Numeric)
    volatility_5m = Column(Numeric)


class Prediction(Base):
    __tablename__ = "predictions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    market_id = Column(Text, ForeignKey("markets.market_id"), nullable=False)
    ts = Column(TIMESTAMP(timezone=True), nullable=False)
    direction = Column(Text, nullable=False)        # "UP" | "DOWN"
    confidence = Column(Numeric, nullable=False)    # 0.0–1.0
    low_confidence = Column(Boolean, nullable=False, default=False)
    model_version = Column(Text, nullable=False)
    feature_snapshot_id = Column(BigInteger, ForeignKey("raw_features.id"))
    settled_at = Column(TIMESTAMP(timezone=True))
    actual_outcome = Column(SmallInteger)           # 1=UP, 0=DOWN, NULL=unsettled


class ModelRegistry(Base):
    __tablename__ = "model_registry"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    market_id = Column(Text, ForeignKey("markets.market_id"), nullable=False)
    version = Column(Text, nullable=False)
    trained_at = Column(TIMESTAMP(timezone=True), nullable=False)
    training_rows = Column(Integer, nullable=False)
    brier_score = Column(Numeric, nullable=False)   # lower = better calibration
    artifact_path = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("market_id", "version", name="uq_model_market_version"),
    )
```

- [ ] **Step 5: Create shared/db.py**

```python
# shared/db.py
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from shared.settings import Settings


def make_engine(settings: Settings):
    return create_engine(settings.db_url, pool_pre_ping=True)


def make_session_factory(settings: Settings):
    return sessionmaker(bind=make_engine(settings), autocommit=False, autoflush=False)


@contextmanager
def session_scope(session_factory) -> Session:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

- [ ] **Step 6: Create alembic/alembic.ini**

```ini
[alembic]
script_location = alembic
prepend_sys_path = .
```

- [ ] **Step 7: Create alembic/env.py**

```python
# alembic/env.py
import os
from alembic import context
from sqlalchemy import engine_from_config, pool
from shared.orm import Base

target_metadata = Base.metadata


def run_migrations_online():
    password = os.environ.get("POSTGRES_PASSWORD", "postgres")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    db = os.environ.get("POSTGRES_DB", "crypto_analysis")
    url = f"postgresql://postgres:{password}@{host}:5432/{db}"
    connectable = engine_from_config(
        {"sqlalchemy.url": url},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
```

- [ ] **Step 8: Create alembic/versions/001_initial_schema.py**

```python
# alembic/versions/001_initial_schema.py
"""initial schema"""
revision = "001"
down_revision = None
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "markets",
        sa.Column("market_id", sa.Text, primary_key=True),
        sa.Column("ticker", sa.Text, nullable=False),
        sa.Column("title", sa.Text),
        sa.Column("close_time", sa.TIMESTAMP(timezone=True)),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("discovered_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )
    op.create_table(
        "raw_features",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("market_id", sa.Text, sa.ForeignKey("markets.market_id"), nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("price_open", sa.Numeric),
        sa.Column("price_high", sa.Numeric),
        sa.Column("price_low", sa.Numeric),
        sa.Column("price_close", sa.Numeric),
        sa.Column("volume", sa.Numeric),
        sa.Column("bid_depth_1pct", sa.Numeric),
        sa.Column("ask_depth_1pct", sa.Numeric),
        sa.Column("book_imbalance", sa.Numeric),
        sa.Column("kalshi_yes_price", sa.Numeric),
        sa.Column("kalshi_no_price", sa.Numeric),
        sa.Column("kalshi_volume", sa.Numeric),
        sa.Column("price_momentum_1m", sa.Numeric),
        sa.Column("price_momentum_5m", sa.Numeric),
        sa.Column("price_momentum_15m", sa.Numeric),
        sa.Column("volatility_5m", sa.Numeric),
    )
    op.create_index("ix_raw_features_market_ts", "raw_features", ["market_id", "ts"])
    op.create_table(
        "predictions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("market_id", sa.Text, sa.ForeignKey("markets.market_id"), nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("direction", sa.Text, nullable=False),
        sa.Column("confidence", sa.Numeric, nullable=False),
        sa.Column("low_confidence", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("feature_snapshot_id", sa.BigInteger, sa.ForeignKey("raw_features.id")),
        sa.Column("settled_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("actual_outcome", sa.SmallInteger),
    )
    op.create_table(
        "model_registry",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("market_id", sa.Text, sa.ForeignKey("markets.market_id"), nullable=False),
        sa.Column("version", sa.Text, nullable=False),
        sa.Column("trained_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("training_rows", sa.Integer, nullable=False),
        sa.Column("brier_score", sa.Numeric, nullable=False),
        sa.Column("artifact_path", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="false"),
        sa.UniqueConstraint("market_id", "version", name="uq_model_market_version"),
    )


def downgrade():
    op.drop_table("model_registry")
    op.drop_table("predictions")
    op.drop_index("ix_raw_features_market_ts")
    op.drop_table("raw_features")
    op.drop_table("markets")
```

- [ ] **Step 9: Run ORM tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_orm.py -v
```
Expected: PASS (5 tests)

- [ ] **Step 10: Commit**

```bash
git add shared/orm.py shared/db.py alembic/ tests/test_orm.py tests/conftest.py
git commit -m "feat: shared ORM models and Alembic migration"
```

---

### Task 4: Kalshi API client

**Files:**
- Create: `ingestor/__init__.py`
- Create: `ingestor/kalshi_client.py`
- Create: `tests/test_kalshi_client.py`

The Kalshi API uses RSA-PSS signatures. Each request carries three headers: `Kalshi-Access-Key`, `Kalshi-Access-Timestamp` (ms since epoch), and `Kalshi-Access-Signature` (base64-encoded RSA-PSS signature over `timestamp + METHOD + path`).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_kalshi_client.py
import pytest
from unittest.mock import patch, MagicMock
from ingestor.kalshi_client import KalshiClient


def test_sign_request_produces_headers():
    # Use the demo key file that already exists in the repo
    client = KalshiClient(
        api_key="test-key",
        private_key_path="Kalshi-2-Demo.txt",
        base_url="https://demo-api.kalshi.co/trade-api/v2",
    )
    headers = client._make_headers("GET", "/trade-api/v2/markets")
    assert headers["Kalshi-Access-Key"] == "test-key"
    assert "Kalshi-Access-Timestamp" in headers
    assert "Kalshi-Access-Signature" in headers
    assert len(headers["Kalshi-Access-Signature"]) > 0


def test_get_markets_filters_crypto():
    client = KalshiClient(
        api_key="test-key",
        private_key_path="Kalshi-2-Demo.txt",
        base_url="https://demo-api.kalshi.co/trade-api/v2",
    )
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "markets": [
            {"ticker": "KXBTCUSD-001", "series_ticker": "KXBTCUSD", "status": "open",
             "close_time": "2026-05-14T18:15:00Z", "yes_bid": 0.55, "yes_ask": 0.57,
             "volume": 1200, "category": "crypto"},
            {"ticker": "WEATHER-001", "series_ticker": "WXTEMP", "status": "open",
             "close_time": "2026-05-14T18:15:00Z", "yes_bid": 0.30, "yes_ask": 0.32,
             "volume": 400, "category": "weather"},
        ]
    }
    mock_response.raise_for_status = MagicMock()
    with patch.object(client._http, "get", return_value=mock_response):
        markets = client.get_crypto_markets()
    assert len(markets) == 1
    assert markets[0]["ticker"] == "KXBTCUSD-001"


def test_get_market_price_returns_midpoint():
    client = KalshiClient(
        api_key="test-key",
        private_key_path="Kalshi-2-Demo.txt",
        base_url="https://demo-api.kalshi.co/trade-api/v2",
    )
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "market": {
            "ticker": "KXBTCUSD-001",
            "yes_bid": 0.54,
            "yes_ask": 0.58,
            "no_bid": 0.42,
            "no_ask": 0.46,
            "volume": 1500,
        }
    }
    mock_response.raise_for_status = MagicMock()
    with patch.object(client._http, "get", return_value=mock_response):
        price = client.get_market_price("KXBTCUSD-001")
    assert price["yes_price"] == pytest.approx(0.56, abs=0.01)
    assert price["no_price"] == pytest.approx(0.44, abs=0.01)
    assert price["volume"] == 1500
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_kalshi_client.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Create ingestor/__init__.py**

```python
```

- [ ] **Step 4: Create ingestor/kalshi_client.py**

```python
# ingestor/kalshi_client.py
import base64
import time
import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding


class KalshiClient:
    def __init__(self, api_key: str, private_key_path: str, base_url: str):
        self._api_key = api_key
        with open(private_key_path, "rb") as f:
            self._private_key = serialization.load_pem_private_key(f.read(), password=None)
        self._base_url = base_url.rstrip("/")
        self._http = httpx.Client(timeout=10)

    def _make_headers(self, method: str, path: str) -> dict:
        ts_ms = str(int(time.time() * 1000))
        msg = (ts_ms + method.upper() + path).encode()
        sig = self._private_key.sign(
            msg,
            asym_padding.PSS(
                mgf=asym_padding.MGF1(hashes.SHA256()),
                salt_length=asym_padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "Kalshi-Access-Key": self._api_key,
            "Kalshi-Access-Timestamp": ts_ms,
            "Kalshi-Access-Signature": base64.b64encode(sig).decode(),
            "Content-Type": "application/json",
        }

    def get_crypto_markets(self) -> list[dict]:
        path = "/trade-api/v2/markets"
        url = self._base_url + "/markets"
        headers = self._make_headers("GET", path)
        resp = self._http.get(url, headers=headers, params={"status": "open", "limit": 200})
        resp.raise_for_status()
        all_markets = resp.json().get("markets", [])
        return [m for m in all_markets if m.get("category") == "crypto"]

    def get_market_price(self, ticker: str) -> dict:
        path = f"/trade-api/v2/markets/{ticker}"
        url = f"{self._base_url}/markets/{ticker}"
        headers = self._make_headers("GET", path)
        resp = self._http.get(url, headers=headers)
        resp.raise_for_status()
        m = resp.json()["market"]
        yes_price = (m["yes_bid"] + m["yes_ask"]) / 2
        no_price = (m["no_bid"] + m["no_ask"]) / 2
        return {"yes_price": yes_price, "no_price": no_price, "volume": m.get("volume", 0)}

    def close(self):
        self._http.close()
```

- [ ] **Step 5: Run tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_kalshi_client.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add ingestor/__init__.py ingestor/kalshi_client.py tests/test_kalshi_client.py
git commit -m "feat: Kalshi API client with RSA-PSS auth"
```

---

### Task 5: Coinbase Advanced Trade client

**Files:**
- Create: `ingestor/coinbase_client.py`
- Create: `tests/test_coinbase_client.py`

Coinbase Advanced Trade uses CDP JWT authentication. The private key in `.env` has literal `\n` sequences that must be converted to real newlines. The JWT payload includes `uri = "METHOD api.coinbase.com/path"` as the audience claim.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_coinbase_client.py
import pytest
from unittest.mock import patch, MagicMock
from ingestor.coinbase_client import CoinbaseClient


def test_get_candles_returns_ohlcv():
    client = CoinbaseClient(key_name="orgs/x/apiKeys/y", private_key_pem="dummy")
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "candles": [
            {"start": "1715700000", "open": "61000", "high": "61500",
             "low": "60800", "close": "61200", "volume": "12.5"},
            {"start": "1715700060", "open": "61200", "high": "61400",
             "low": "61100", "close": "61350", "volume": "8.3"},
        ]
    }
    mock_response.raise_for_status = MagicMock()
    with patch.object(client._http, "get", return_value=mock_response):
        candles = client.get_candles("BTC-USD", granularity="ONE_MINUTE", limit=2)
    assert len(candles) == 2
    assert candles[0]["close"] == pytest.approx(61200.0)
    assert candles[0]["volume"] == pytest.approx(12.5)


def test_get_order_book_returns_bid_ask_depth():
    client = CoinbaseClient(key_name="orgs/x/apiKeys/y", private_key_pem="dummy")
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "pricebook": {
            "product_id": "BTC-USD",
            "bids": [
                {"price": "61000", "size": "0.5"},
                {"price": "60950", "size": "1.2"},
                {"price": "60900", "size": "2.1"},
            ],
            "asks": [
                {"price": "61050", "size": "0.3"},
                {"price": "61100", "size": "0.9"},
                {"price": "61150", "size": "1.5"},
            ],
        }
    }
    mock_response.raise_for_status = MagicMock()
    with patch.object(client._http, "get", return_value=mock_response):
        book = client.get_order_book("BTC-USD", depth=3)
    assert book["bid_depth"] == pytest.approx(3.8)   # 0.5 + 1.2 + 2.1
    assert book["ask_depth"] == pytest.approx(2.7)   # 0.3 + 0.9 + 1.5
    assert "book_imbalance" in book


def test_series_ticker_to_coinbase_product():
    from ingestor.coinbase_client import series_ticker_to_product_id
    assert series_ticker_to_product_id("KXBTCUSD") == "BTC-USD"
    assert series_ticker_to_product_id("KXETHUSD") == "ETH-USD"
    assert series_ticker_to_product_id("KXSOLUSD") == "SOL-USD"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_coinbase_client.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Create ingestor/coinbase_client.py**

```python
# ingestor/coinbase_client.py
import secrets
import time
import httpx
import jwt
from cryptography.hazmat.primitives import serialization


def series_ticker_to_product_id(series_ticker: str) -> str:
    # "KXBTCUSD" → "BTC-USD", "KXETHUSD" → "ETH-USD"
    # Strip leading "KX" and trailing "USD", insert "-USD"
    symbol = series_ticker[2:-3]  # e.g. "BTC"
    return f"{symbol}-USD"


class CoinbaseClient:
    BASE_URL = "https://api.coinbase.com"

    def __init__(self, key_name: str, private_key_pem: str):
        self._key_name = key_name
        # .env stores \n as literal backslash-n — normalize to real newlines
        normalized = private_key_pem.replace("\\n", "\n")
        self._private_key = serialization.load_pem_private_key(
            normalized.encode(), password=None
        )
        self._http = httpx.Client(timeout=10)

    def _make_jwt(self, method: str, path: str) -> str:
        payload = {
            "sub": self._key_name,
            "iss": "cdp",
            "nbf": int(time.time()),
            "exp": int(time.time()) + 120,
            "uri": f"{method.upper()} api.coinbase.com{path}",
        }
        return jwt.encode(
            payload,
            self._private_key,
            algorithm="ES256",
            headers={"kid": self._key_name, "nonce": secrets.token_hex(16)},
        )

    def get_candles(self, product_id: str, granularity: str = "ONE_MINUTE", limit: int = 40) -> list[dict]:
        path = f"/api/v3/brokerage/products/{product_id}/candles"
        token = self._make_jwt("GET", path)
        resp = self._http.get(
            self.BASE_URL + path,
            headers={"Authorization": f"Bearer {token}"},
            params={"granularity": granularity, "limit": limit},
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

    def get_order_book(self, product_id: str, depth: int = 10) -> dict:
        path = "/api/v3/brokerage/product_book"
        token = self._make_jwt("GET", path)
        resp = self._http.get(
            self.BASE_URL + path,
            headers={"Authorization": f"Bearer {token}"},
            params={"product_id": product_id, "limit": depth},
        )
        resp.raise_for_status()
        book = resp.json()["pricebook"]
        bid_depth = sum(float(b["size"]) for b in book["bids"])
        ask_depth = sum(float(a["size"]) for a in book["asks"])
        total = bid_depth + ask_depth
        book_imbalance = (bid_depth - ask_depth) / total if total > 0 else 0.0
        return {
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
            "book_imbalance": book_imbalance,
        }

    def close(self):
        self._http.close()
```

- [ ] **Step 4: Run tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_coinbase_client.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add ingestor/coinbase_client.py tests/test_coinbase_client.py
git commit -m "feat: Coinbase Advanced Trade client with CDP JWT auth"
```

---

### Task 6: Market discovery

**Files:**
- Create: `ingestor/market_discovery.py`
- Create: `tests/test_market_discovery.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_market_discovery.py
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from ingestor.market_discovery import upsert_markets, filter_active_crypto_markets
from shared.orm import Market


def _make_kalshi_market(ticker, series_ticker, hours_until_close=0.5):
    close_time = datetime.now(timezone.utc) + timedelta(hours=hours_until_close)
    return {
        "ticker": ticker,
        "series_ticker": series_ticker,
        "title": f"Will {series_ticker} be above X?",
        "close_time": close_time.isoformat(),
        "status": "open",
        "category": "crypto",
    }


def test_filter_active_crypto_markets_excludes_expired():
    markets = [
        _make_kalshi_market("KXBTCUSD-001", "KXBTCUSD", hours_until_close=0.5),
        _make_kalshi_market("KXBTCUSD-002", "KXBTCUSD", hours_until_close=-1.0),  # already closed
    ]
    active = filter_active_crypto_markets(markets)
    assert len(active) == 1
    assert active[0]["ticker"] == "KXBTCUSD-001"


def test_upsert_markets_inserts_new(db_session):
    raw_markets = [_make_kalshi_market("KXBTCUSD-NEW", "KXBTCUSD")]
    upsert_markets(db_session, raw_markets)
    result = db_session.get(Market, "KXBTCUSD-NEW")
    assert result is not None
    assert result.ticker == "KXBTCUSD"
    assert result.status == "active"


def test_upsert_markets_updates_existing(db_session):
    existing = Market(
        market_id="KXETHUSD-001",
        ticker="KXETHUSD",
        status="stale",
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(existing)
    db_session.flush()
    raw_markets = [_make_kalshi_market("KXETHUSD-001", "KXETHUSD")]
    upsert_markets(db_session, raw_markets)
    db_session.expire(existing)
    refreshed = db_session.get(Market, "KXETHUSD-001")
    assert refreshed.status == "active"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_market_discovery.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Create ingestor/market_discovery.py**

```python
# ingestor/market_discovery.py
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from shared.orm import Market

logger = logging.getLogger(__name__)


def filter_active_crypto_markets(raw_markets: list[dict]) -> list[dict]:
    now = datetime.now(timezone.utc)
    active = []
    for m in raw_markets:
        close_time_str = m.get("close_time", "")
        try:
            close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            logger.warning("Could not parse close_time for market %s", m.get("ticker"))
            continue
        if close_time > now:
            m["_close_time_parsed"] = close_time
            active.append(m)
    return active


def upsert_markets(session: Session, raw_markets: list[dict]) -> None:
    now = datetime.now(timezone.utc)
    for m in raw_markets:
        market_id = m["ticker"]
        close_time = m.get("_close_time_parsed") or datetime.fromisoformat(
            m["close_time"].replace("Z", "+00:00")
        )
        existing = session.get(Market, market_id)
        if existing is None:
            session.add(Market(
                market_id=market_id,
                ticker=m["series_ticker"],
                title=m.get("title"),
                close_time=close_time,
                status="active",
                discovered_at=now,
                updated_at=now,
            ))
            logger.info("Discovered new market: %s", market_id)
        else:
            existing.status = "active"
            existing.close_time = close_time
            existing.updated_at = now
    session.flush()
```

- [ ] **Step 4: Run tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_market_discovery.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add ingestor/market_discovery.py tests/test_market_discovery.py
git commit -m "feat: market discovery — upsert active Kalshi crypto markets"
```

---

### Task 7: Feature writer

**Files:**
- Create: `ingestor/feature_writer.py`
- Create: `tests/test_feature_writer.py`

The feature writer fetches price/orderbook from Coinbase and contract price from Kalshi, computes derived momentum and volatility fields using recent DB rows, then inserts a `raw_features` row.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_feature_writer.py
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from ingestor.feature_writer import compute_derived_fields, build_raw_feature_row
from shared.orm import RawFeature, Market


def _make_prior_row(price_close: float, minutes_ago: float):
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    rf = RawFeature()
    rf.price_close = price_close
    rf.ts = ts
    return rf


def test_compute_momentum_positive_when_price_rising():
    prior_rows = [
        _make_prior_row(60000.0, 15),
        _make_prior_row(60500.0, 10),
        _make_prior_row(61000.0, 5),
        _make_prior_row(61200.0, 1),
    ]
    derived = compute_derived_fields(current_price=61500.0, prior_rows=prior_rows)
    assert derived["price_momentum_15m"] > 0
    assert derived["price_momentum_5m"] > 0
    assert derived["price_momentum_1m"] > 0


def test_compute_momentum_zero_when_no_prior_rows():
    derived = compute_derived_fields(current_price=61000.0, prior_rows=[])
    assert derived["price_momentum_1m"] == 0.0
    assert derived["price_momentum_5m"] == 0.0
    assert derived["price_momentum_15m"] == 0.0


def test_build_raw_feature_row_has_all_fields():
    row = build_raw_feature_row(
        market_id="KXBTCUSD-001",
        ts=datetime.now(timezone.utc),
        candle={"open": 61000.0, "high": 61500.0, "low": 60900.0, "close": 61200.0, "volume": 10.0},
        order_book={"bid_depth": 5.0, "ask_depth": 3.0, "book_imbalance": 0.25},
        kalshi_price={"yes_price": 0.57, "no_price": 0.43, "volume": 1500},
        derived={"price_momentum_1m": 0.002, "price_momentum_5m": 0.005,
                 "price_momentum_15m": 0.012, "volatility_5m": 0.003},
    )
    assert row.market_id == "KXBTCUSD-001"
    assert float(row.price_close) == pytest.approx(61200.0)
    assert float(row.book_imbalance) == pytest.approx(0.25)
    assert float(row.kalshi_yes_price) == pytest.approx(0.57)
    assert float(row.price_momentum_15m) == pytest.approx(0.012)


import pytest
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_feature_writer.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Create ingestor/feature_writer.py**

```python
# ingestor/feature_writer.py
import logging
from datetime import datetime, timezone, timedelta
import numpy as np
from sqlalchemy.orm import Session
from shared.orm import RawFeature

logger = logging.getLogger(__name__)
_POLL_INTERVAL_SECONDS = 30


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

- [ ] **Step 4: Run tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_feature_writer.py -v
```
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add ingestor/feature_writer.py tests/test_feature_writer.py
git commit -m "feat: feature writer — fetch, compute derived fields, write raw_features"
```

---

### Task 8: Ingestor main loop and Dockerfile

**Files:**
- Create: `ingestor/main.py`
- Create: `ingestor/requirements.txt`
- Create: `ingestor/Dockerfile`

- [ ] **Step 1: Create ingestor/requirements.txt**

```
httpx==0.27.0
cryptography==42.0.8
PyJWT==2.8.0
sqlalchemy==2.0.30
psycopg2-binary==2.9.9
pydantic-settings==2.2.1
alembic==1.13.1
numpy==1.26.4
```

- [ ] **Step 2: Create ingestor/main.py**

```python
# ingestor/main.py
import json
import logging
import threading
import time
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

from shared.db import make_session_factory, session_scope
from shared.orm import Market
from shared.settings import Settings
from ingestor.kalshi_client import KalshiClient
from ingestor.coinbase_client import CoinbaseClient
from ingestor.market_discovery import filter_active_crypto_markets, upsert_markets
from ingestor.feature_writer import fetch_and_write

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

settings = Settings()
session_factory = make_session_factory(settings)
kalshi = KalshiClient(
    api_key=settings.kalshi_api_key,
    private_key_path=settings.kalshi_private_key_path,
    base_url=settings.kalshi_base_url,
)
coinbase = CoinbaseClient(
    key_name=settings.coinbase_cdp_key_name,
    private_key_pem=settings.coinbase_cdp_private_key,
)

_last_discovery = 0.0
_DISCOVERY_INTERVAL = 300  # re-discover markets every 5 minutes
_POLL_INTERVAL = 30

app = FastAPI()


@app.get("/health")
def health():
    with session_scope(session_factory) as session:
        active = session.query(Market).filter(Market.status == "active").count()
        stale = session.query(Market).filter(Market.status == "stale").count()
    return {"status": "ok", "active_markets": active, "stale_markets": stale}


def _run_discovery(session):
    global _last_discovery
    raw_markets = kalshi.get_crypto_markets()
    active = filter_active_crypto_markets(raw_markets)
    upsert_markets(session, active)
    _last_discovery = time.time()
    logger.info("Discovered %d active crypto markets", len(active))


def _mark_stale(session):
    now = datetime.now(timezone.utc)
    expired = (
        session.query(Market)
        .filter(Market.status == "active", Market.close_time < now)
        .all()
    )
    for m in expired:
        m.status = "stale"
    if expired:
        logger.info("Marked %d markets as stale", len(expired))


def _ingest_loop():
    while True:
        with session_scope(session_factory) as session:
            if time.time() - _last_discovery > _DISCOVERY_INTERVAL:
                _run_discovery(session)
            _mark_stale(session)
            active_markets = (
                session.query(Market).filter(Market.status == "active").all()
            )
            for market in active_markets:
                fetch_and_write(session, market.market_id, market.ticker, coinbase, kalshi)
        time.sleep(_POLL_INTERVAL)


if __name__ == "__main__":
    t = threading.Thread(target=_ingest_loop, daemon=True)
    t.start()
    uvicorn.run(app, host="0.0.0.0", port=8001)
```

- [ ] **Step 3: Create ingestor/Dockerfile**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY ingestor/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ shared/
COPY ingestor/ ingestor/
COPY .env .env
COPY Kalshi-1.txt Kalshi-1.txt
COPY Kalshi-2-Demo.txt Kalshi-2-Demo.txt
COPY cdp_api_key.json cdp_api_key.json
CMD ["python", "-m", "ingestor.main"]
```

- [ ] **Step 4: Commit**

```bash
git add ingestor/main.py ingestor/requirements.txt ingestor/Dockerfile
git commit -m "feat: ingestor service — polling loop and health endpoint"
```

---

### Task 9: Feature builder (25-dimensional vector)

**Files:**
- Create: `predictor/__init__.py`
- Create: `predictor/feature_builder.py`
- Create: `tests/test_feature_builder.py`

The feature builder produces a deterministic 25-element numpy array from a list of `RawFeature` DB rows and the current market metadata. Returns `None` when insufficient data exists.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_feature_builder.py
import pytest
import numpy as np
from datetime import datetime, timezone, timedelta
from predictor.feature_builder import build_feature_vector, FEATURE_NAMES
from shared.orm import RawFeature


def _make_row(price: float, minutes_ago: float, kalshi_yes: float = 0.55,
              book_imbalance: float = 0.1, volume: float = 10.0, kalshi_vol: float = 500.0) -> RawFeature:
    r = RawFeature()
    r.ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    r.price_close = price
    r.price_open = price * 0.999
    r.price_high = price * 1.002
    r.price_low = price * 0.997
    r.volume = volume
    r.bid_depth_1pct = 5.0
    r.ask_depth_1pct = 4.0
    r.book_imbalance = book_imbalance
    r.kalshi_yes_price = kalshi_yes
    r.kalshi_no_price = 1 - kalshi_yes
    r.kalshi_volume = kalshi_vol
    return r


def _make_rows(n: int = 40) -> list:
    # Simulate gently rising prices over n*30s intervals
    return [_make_row(60000 + i * 10, minutes_ago=(n - i) * 0.5) for i in range(n)]


def test_feature_vector_has_25_elements():
    rows = _make_rows(40)
    vec, names = build_feature_vector(rows, minutes_to_close=7.0, ts=datetime.now(timezone.utc))
    assert vec is not None
    assert len(vec) == 25
    assert len(names) == 25


def test_feature_names_match_constant():
    rows = _make_rows(40)
    _, names = build_feature_vector(rows, minutes_to_close=7.0, ts=datetime.now(timezone.utc))
    assert names == FEATURE_NAMES


def test_returns_none_for_insufficient_rows():
    rows = _make_rows(2)
    result = build_feature_vector(rows, minutes_to_close=7.0, ts=datetime.now(timezone.utc))
    assert result is None


def test_minutes_to_close_is_in_vector():
    rows = _make_rows(40)
    vec, names = build_feature_vector(rows, minutes_to_close=3.5, ts=datetime.now(timezone.utc))
    idx = names.index("minutes_to_close")
    assert vec[idx] == pytest.approx(3.5)


def test_no_nan_in_vector():
    rows = _make_rows(40)
    vec, names = build_feature_vector(rows, minutes_to_close=7.0, ts=datetime.now(timezone.utc))
    assert not np.any(np.isnan(vec))
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_feature_builder.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Create predictor/__init__.py**

```python
```

- [ ] **Step 4: Create predictor/feature_builder.py**

```python
# predictor/feature_builder.py
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

_MIN_ROWS = 10  # need at least 10 rows (~5 minutes) to produce a vector


def build_feature_vector(
    rows: list,  # list of RawFeature ORM objects, sorted any order
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
    now = datetime.now(timezone.utc)

    def _within(minutes: float) -> list:
        cutoff = now - timedelta(minutes=minutes)
        return [r for r in rows if r.ts >= cutoff]

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

    # Price momentum
    mom_1m = _momentum(1)
    mom_5m = _momentum(5)
    mom_15m = _momentum(15)

    # Volatility + rate of change
    w5 = _within(5)
    w10 = _within(10)
    vol_5m = _volatility(w5)
    vol_10m = _volatility(w10)
    vol_roc = vol_5m - vol_10m  # positive = volatility increasing

    # VWAP deviation (15m)
    w15 = _within(15)
    if w15 and volumes[-len(w15):].sum() > 0:
        p15 = np.array([_f(r.price_close) for r in w15])
        v15 = np.array([_f(r.volume) for r in w15])
        vwap = np.dot(p15, v15) / v15.sum() if v15.sum() > 0 else _f(latest.price_close)
        vwap_dev = (_f(latest.price_close) - vwap) / vwap if vwap > 0 else 0.0
    else:
        vwap_dev = 0.0

    # Candle body ratio
    hi = _f(latest.price_high)
    lo = _f(latest.price_low)
    op = _f(latest.price_open)
    cl = _f(latest.price_close)
    candle_range = hi - lo
    body_ratio = (cl - op) / candle_range if candle_range > 0 else 0.0

    # Volume momentum (5m vs 15m avg)
    avg_vol_15m = float(np.mean([_f(r.volume) for r in w15])) if w15 else 0.0
    avg_vol_5m = float(np.mean([_f(r.volume) for r in w5])) if w5 else 0.0
    vol_mom = (avg_vol_5m - avg_vol_15m) / avg_vol_15m if avg_vol_15m > 0 else 0.0

    # Order book
    book_latest = _f(latest.book_imbalance)
    last5_imb = [_f(r.book_imbalance) for r in rows[-5:]]
    book_trend = last5_imb[-1] - last5_imb[0] if len(last5_imb) >= 2 else 0.0
    bid_depth = _f(latest.bid_depth_1pct)
    ask_depth = _f(latest.ask_depth_1pct)

    # Kalshi signals
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

    # Time features (cyclical encoding)
    hour = ts.hour
    dow = ts.weekday()
    sin_hour = math.sin(2 * math.pi * hour / 24)
    cos_hour = math.cos(2 * math.pi * hour / 24)
    sin_dow = math.sin(2 * math.pi * dow / 7)
    cos_dow = math.cos(2 * math.pi * dow / 7)
    is_weekend = 1.0 if dow >= 5 else 0.0

    # Consecutive direction (positive = consecutive up candles)
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

- [ ] **Step 5: Run tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_feature_builder.py -v
```
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add predictor/__init__.py predictor/feature_builder.py tests/test_feature_builder.py
git commit -m "feat: 25-dim feature vector builder with cyclical time encoding"
```

---

### Task 10: Model loader

**Files:**
- Create: `predictor/model_loader.py`
- Create: `tests/test_model_loader.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_model_loader.py
import os
import joblib
import pytest
import numpy as np
from datetime import datetime, timezone
from unittest.mock import MagicMock
from predictor.model_loader import ModelLoader
from shared.orm import Market, ModelRegistry


def _insert_market(session, market_id="KXBTCUSD-001"):
    m = Market(
        market_id=market_id, ticker="KXBTCUSD", status="active",
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(m)
    session.flush()
    return m


def test_get_model_returns_none_for_unknown_market(db_session, tmp_path):
    loader = ModelLoader(db_session, models_dir=str(tmp_path))
    assert loader.get_model("NO_SUCH_MARKET") is None


def test_get_model_loads_active_model(db_session, tmp_path):
    _insert_market(db_session, "KXBTCUSD-002")
    from sklearn.dummy import DummyClassifier
    clf = DummyClassifier()
    clf.fit([[0] * 25], [1])
    path = str(tmp_path / "KXBTCUSD-002_v1.joblib")
    joblib.dump(clf, path)
    reg = ModelRegistry(
        market_id="KXBTCUSD-002",
        version="v1",
        trained_at=datetime.now(timezone.utc),
        training_rows=100,
        brier_score=0.22,
        artifact_path=path,
        is_active=True,
    )
    db_session.add(reg)
    db_session.flush()
    loader = ModelLoader(db_session, models_dir=str(tmp_path))
    model = loader.get_model("KXBTCUSD-002")
    assert model is not None


def test_reload_picks_up_newer_model(db_session, tmp_path):
    _insert_market(db_session, "KXBTCUSD-003")
    from sklearn.dummy import DummyClassifier
    clf = DummyClassifier()
    clf.fit([[0] * 25], [1])
    path = str(tmp_path / "KXBTCUSD-003_v2.joblib")
    joblib.dump(clf, path)
    reg = ModelRegistry(
        market_id="KXBTCUSD-003",
        version="v2",
        trained_at=datetime.now(timezone.utc),
        training_rows=200,
        brier_score=0.18,
        artifact_path=path,
        is_active=True,
    )
    db_session.add(reg)
    db_session.flush()
    loader = ModelLoader(db_session, models_dir=str(tmp_path))
    loader.get_model("KXBTCUSD-003")  # initial load
    loader.reload_all()               # simulate hot-reload
    model = loader.get_model("KXBTCUSD-003")
    assert model is not None
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_model_loader.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Create predictor/model_loader.py**

```python
# predictor/model_loader.py
import logging
import joblib
from sqlalchemy.orm import Session
from shared.orm import ModelRegistry

logger = logging.getLogger(__name__)


class ModelLoader:
    def __init__(self, session: Session, models_dir: str):
        self._session = session
        self._models_dir = models_dir
        self._cache: dict[str, object] = {}
        self._version_cache: dict[str, str] = {}

    def get_model(self, market_id: str):
        reg = (
            self._session.query(ModelRegistry)
            .filter(ModelRegistry.market_id == market_id, ModelRegistry.is_active == True)
            .first()
        )
        if reg is None:
            return None
        cached_version = self._version_cache.get(market_id)
        if cached_version != reg.version:
            try:
                model = joblib.load(reg.artifact_path)
                self._cache[market_id] = model
                self._version_cache[market_id] = reg.version
                logger.info("Loaded model %s v%s", market_id, reg.version)
            except Exception as exc:
                logger.error("Failed to load model for %s: %s", market_id, exc)
                return None
        return self._cache.get(market_id)

    def reload_all(self) -> None:
        active_regs = (
            self._session.query(ModelRegistry)
            .filter(ModelRegistry.is_active == True)
            .all()
        )
        for reg in active_regs:
            cached = self._version_cache.get(reg.market_id)
            if cached != reg.version:
                try:
                    model = joblib.load(reg.artifact_path)
                    self._cache[reg.market_id] = model
                    self._version_cache[reg.market_id] = reg.version
                    logger.info("Hot-reloaded model %s v%s", reg.market_id, reg.version)
                except Exception as exc:
                    logger.error("Hot-reload failed for %s: %s", reg.market_id, exc)
```

- [ ] **Step 4: Run tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_model_loader.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add predictor/model_loader.py tests/test_model_loader.py
git commit -m "feat: model loader with hot-reload from model_registry"
```

---

### Task 11: Predictor inference, main loop, and Dockerfile

**Files:**
- Create: `predictor/inference.py`
- Create: `predictor/main.py`
- Create: `predictor/requirements.txt`
- Create: `predictor/Dockerfile`
- Create: `tests/test_inference.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_inference.py
import pytest
import numpy as np
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from predictor.inference import run_inference
from shared.orm import Market, RawFeature, Prediction


def _make_market(session, market_id="KXBTCUSD-INF"):
    m = Market(
        market_id=market_id, ticker="KXBTCUSD",
        status="active",
        close_time=datetime.now(timezone.utc) + timedelta(minutes=7),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(m)
    session.flush()
    return m


def _make_raw_rows(session, market_id, n=40):
    rows = []
    for i in range(n):
        r = RawFeature()
        r.market_id = market_id
        r.ts = datetime.now(timezone.utc) - timedelta(minutes=(n - i) * 0.5)
        r.price_close = 60000 + i * 10
        r.price_open = 60000 + i * 10 - 5
        r.price_high = 60000 + i * 10 + 20
        r.price_low = 60000 + i * 10 - 20
        r.volume = 10.0
        r.bid_depth_1pct = 5.0
        r.ask_depth_1pct = 4.0
        r.book_imbalance = 0.1
        r.kalshi_yes_price = 0.57
        r.kalshi_no_price = 0.43
        r.kalshi_volume = 500.0
        session.add(r)
        rows.append(r)
    session.flush()
    return rows


def test_run_inference_emits_low_confidence_when_no_model(db_session):
    market = _make_market(db_session, "KXBTCUSD-NC")
    _make_raw_rows(db_session, "KXBTCUSD-NC")
    mock_loader = MagicMock()
    mock_loader.get_model.return_value = None
    pred = run_inference(db_session, market, mock_loader)
    assert pred is not None
    assert pred.low_confidence is True
    assert pred.model_version == "none"


def test_run_inference_writes_prediction_with_model(db_session):
    market = _make_market(db_session, "KXBTCUSD-WM")
    _make_raw_rows(db_session, "KXBTCUSD-WM")
    mock_model = MagicMock()
    mock_model.predict_proba.return_value = np.array([[0.3, 0.7]])
    mock_loader = MagicMock()
    mock_loader.get_model.return_value = mock_model
    mock_loader._version_cache = {"KXBTCUSD-WM": "v1"}
    pred = run_inference(db_session, market, mock_loader)
    assert pred is not None
    assert pred.direction == "UP"
    assert float(pred.confidence) == pytest.approx(0.7, abs=0.01)
    assert pred.low_confidence is False
    assert pred.market_id == "KXBTCUSD-WM"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_inference.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Create predictor/inference.py**

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

    model = model_loader.get_model(market.market_id)
    if model is None:
        pred = Prediction(
            market_id=market.market_id,
            ts=ts,
            direction="UP",
            confidence=0.5,
            low_confidence=True,
            model_version="none",
            feature_snapshot_id=feature_snapshot_id,
            settled_at=market.close_time,
        )
        session.add(pred)
        session.flush()
        return pred

    if result is None:
        logger.info("Insufficient rows for %s — low_confidence", market.market_id)
        pred = Prediction(
            market_id=market.market_id,
            ts=ts,
            direction="UP",
            confidence=0.5,
            low_confidence=True,
            model_version=model_loader._version_cache.get(market.market_id, "unknown"),
            feature_snapshot_id=feature_snapshot_id,
            settled_at=market.close_time,
        )
        session.add(pred)
        session.flush()
        return pred

    vec, _ = result
    proba = model.predict_proba(vec.reshape(1, -1))[0]  # [p_down, p_up]
    p_up = float(proba[1])
    direction = "UP" if p_up >= 0.5 else "DOWN"
    confidence = p_up if direction == "UP" else 1 - p_up

    pred = Prediction(
        market_id=market.market_id,
        ts=ts,
        direction=direction,
        confidence=confidence,
        low_confidence=False,
        model_version=model_loader._version_cache.get(market.market_id, "unknown"),
        feature_snapshot_id=feature_snapshot_id,
        settled_at=market.close_time,
    )
    session.add(pred)
    session.flush()
    logger.info("Prediction for %s: %s (%.2f)", market.market_id, direction, confidence)
    return pred
```

- [ ] **Step 4: Create predictor/main.py**

```python
# predictor/main.py
import logging
import threading
import time

from fastapi import FastAPI
import uvicorn

from shared.db import make_session_factory, session_scope
from shared.orm import Market, ModelRegistry
from shared.settings import Settings
from predictor.model_loader import ModelLoader
from predictor.inference import run_inference

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

settings = Settings()
session_factory = make_session_factory(settings)

_INFERENCE_INTERVAL = 60
_RELOAD_INTERVAL = 300
_last_reload = 0.0

app = FastAPI()


@app.get("/health")
def health():
    with session_scope(session_factory) as session:
        active = session.query(Market).filter(Market.status == "active").count()
        models = session.query(ModelRegistry).filter(ModelRegistry.is_active == True).count()
    return {"status": "ok", "active_markets": active, "active_models": models}


def _inference_loop():
    global _last_reload
    with session_scope(session_factory) as session:
        loader = ModelLoader(session, models_dir="models")
        while True:
            if time.time() - _last_reload > _RELOAD_INTERVAL:
                loader.reload_all()
                _last_reload = time.time()
            active_markets = (
                session.query(Market).filter(Market.status == "active").all()
            )
            for market in active_markets:
                try:
                    run_inference(session, market, loader)
                except Exception as exc:
                    logger.error("Inference failed for %s: %s", market.market_id, exc)
            session.commit()
            time.sleep(_INFERENCE_INTERVAL)


if __name__ == "__main__":
    t = threading.Thread(target=_inference_loop, daemon=True)
    t.start()
    uvicorn.run(app, host="0.0.0.0", port=8002)
```

- [ ] **Step 5: Create predictor/requirements.txt**

```
xgboost==2.0.3
scikit-learn==1.4.2
numpy==1.26.4
pandas==2.2.2
joblib==1.4.2
sqlalchemy==2.0.30
psycopg2-binary==2.9.9
pydantic-settings==2.2.1
fastapi==0.111.0
uvicorn==0.29.0
```

- [ ] **Step 6: Create predictor/Dockerfile**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY predictor/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ shared/
COPY predictor/ predictor/
COPY .env .env
VOLUME ["/app/models"]
CMD ["python", "-m", "predictor.main"]
```

- [ ] **Step 7: Run inference tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_inference.py -v
```
Expected: PASS (2 tests)

- [ ] **Step 8: Commit**

```bash
git add predictor/inference.py predictor/main.py predictor/requirements.txt predictor/Dockerfile tests/test_inference.py
git commit -m "feat: predictor service — XGBoost inference loop with low_confidence gate"
```

---

### Task 12: FastAPI service

**Files:**
- Create: `api/__init__.py`
- Create: `api/main.py`
- Create: `api/requirements.txt`
- Create: `api/Dockerfile`
- Create: `tests/test_api.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_api.py
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from shared.orm import Market, Prediction


def _make_test_app(db_session):
    import api.main as api_module
    app = api_module.create_app(lambda: db_session)
    return TestClient(app)


def _seed_market(session, market_id="KXBTCUSD-001"):
    m = Market(
        market_id=market_id, ticker="KXBTCUSD", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(minutes=7),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(m)
    session.flush()
    return m


def _seed_prediction(session, market_id="KXBTCUSD-001"):
    p = Prediction(
        market_id=market_id,
        ts=datetime.now(timezone.utc),
        direction="UP",
        confidence=0.73,
        low_confidence=False,
        model_version="v2",
        settled_at=datetime.now(timezone.utc) + timedelta(minutes=7),
    )
    session.add(p)
    session.flush()
    return p


def test_get_markets_returns_active(db_session):
    _seed_market(db_session, "KXBTCUSD-M1")
    client = _make_test_app(db_session)
    resp = client.get("/markets")
    assert resp.status_code == 200
    data = resp.json()
    ids = [m["market_id"] for m in data]
    assert "KXBTCUSD-M1" in ids


def test_get_predict_returns_latest(db_session):
    _seed_market(db_session, "KXBTCUSD-P1")
    _seed_prediction(db_session, "KXBTCUSD-P1")
    client = _make_test_app(db_session)
    resp = client.get("/predict/KXBTCUSD-P1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["direction"] == "UP"
    assert body["confidence"] == pytest.approx(0.73, abs=0.01)
    assert "feature_age_seconds" in body


def test_get_predict_404_for_unknown_market(db_session):
    client = _make_test_app(db_session)
    resp = client.get("/predict/DOES-NOT-EXIST")
    assert resp.status_code == 404


def test_get_history_returns_settled(db_session):
    _seed_market(db_session, "KXBTCUSD-H1")
    p = _seed_prediction(db_session, "KXBTCUSD-H1")
    p.actual_outcome = 1
    p.settled_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db_session.flush()
    client = _make_test_app(db_session)
    resp = client.get(f"/history/KXBTCUSD-H1")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["actual_outcome"] == 1


def test_health_endpoint(db_session):
    client = _make_test_app(db_session)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_api.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Create api/__init__.py**

```python
```

- [ ] **Step 4: Create api/main.py**

```python
# api/main.py
from datetime import datetime, timezone
from typing import Callable
from fastapi import FastAPI, HTTPException, Depends
from sqlalchemy.orm import Session
from shared.db import make_session_factory, session_scope
from shared.orm import Market, Prediction
from shared.settings import Settings


def create_app(session_factory_fn: Callable = None) -> FastAPI:
    app = FastAPI(title="Kalshi Crypto Prediction API")

    if session_factory_fn is None:
        settings = Settings()
        _factory = make_session_factory(settings)
        def session_factory_fn():
            return _factory()

    def _get_db():
        session = session_factory_fn()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @app.get("/health")
    def health(session: Session = Depends(_get_db)):
        active = session.query(Market).filter(Market.status == "active").count()
        stale = session.query(Market).filter(Market.status == "stale").count()
        return {"status": "ok", "active_markets": active, "stale_markets": stale}

    @app.get("/markets")
    def get_markets(session: Session = Depends(_get_db)):
        markets = session.query(Market).filter(Market.status == "active").all()
        now = datetime.now(timezone.utc)
        return [
            {
                "market_id": m.market_id,
                "ticker": m.ticker,
                "title": m.title,
                "close_time": m.close_time.isoformat() if m.close_time else None,
                "minutes_to_close": (
                    round((m.close_time - now).total_seconds() / 60, 1)
                    if m.close_time else None
                ),
            }
            for m in markets
        ]

    @app.get("/predict/{market_id}")
    def get_prediction(market_id: str, session: Session = Depends(_get_db)):
        market = session.get(Market, market_id)
        if market is None:
            raise HTTPException(status_code=404, detail="Market not found")
        pred = (
            session.query(Prediction)
            .filter(Prediction.market_id == market_id)
            .order_by(Prediction.ts.desc())
            .first()
        )
        if pred is None:
            raise HTTPException(status_code=404, detail="No prediction available")
        now = datetime.now(timezone.utc)
        feature_age = int((now - pred.ts).total_seconds()) if pred.ts else None
        return {
            "market_id": market_id,
            "direction": pred.direction,
            "confidence": float(pred.confidence),
            "low_confidence": pred.low_confidence,
            "model_version": pred.model_version,
            "ts": pred.ts.isoformat(),
            "feature_age_seconds": feature_age,
        }

    @app.get("/history/{market_id}")
    def get_history(
        market_id: str,
        limit: int = 100,
        session: Session = Depends(_get_db),
    ):
        market = session.get(Market, market_id)
        if market is None:
            raise HTTPException(status_code=404, detail="Market not found")
        preds = (
            session.query(Prediction)
            .filter(
                Prediction.market_id == market_id,
                Prediction.actual_outcome != None,
            )
            .order_by(Prediction.ts.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "ts": p.ts.isoformat(),
                "direction": p.direction,
                "confidence": float(p.confidence),
                "actual_outcome": p.actual_outcome,
                "correct": (
                    (p.direction == "UP" and p.actual_outcome == 1)
                    or (p.direction == "DOWN" and p.actual_outcome == 0)
                ),
            }
            for p in preds
        ]

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

- [ ] **Step 5: Create api/requirements.txt**

```
fastapi==0.111.0
uvicorn==0.29.0
sqlalchemy==2.0.30
psycopg2-binary==2.9.9
pydantic-settings==2.2.1
```

- [ ] **Step 6: Create api/Dockerfile**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY api/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ shared/
COPY api/ api/
COPY .env .env
CMD ["python", "-m", "api.main"]
```

- [ ] **Step 7: Run API tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_api.py -v
```
Expected: PASS (5 tests)

- [ ] **Step 8: Commit**

```bash
git add api/ tests/test_api.py
git commit -m "feat: FastAPI service — /markets, /predict, /history, /health"
```

---

### Task 13: Trainer — dataset builder

**Files:**
- Create: `trainer/__init__.py`
- Create: `trainer/requirements.txt`
- Create: `trainer/dataset.py`
- Create: `tests/test_trainer.py` (part 1)

- [ ] **Step 1: Write failing tests (dataset section)**

```python
# tests/test_trainer.py
import pytest
import numpy as np
from datetime import datetime, timezone, timedelta
from trainer.dataset import build_training_dataset
from shared.orm import Market, RawFeature, Prediction


def _seed_settled_data(session, market_id="KXBTCUSD-TR"):
    market = Market(
        market_id=market_id, ticker="KXBTCUSD", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(hours=1),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(market)
    session.flush()

    for i in range(50):
        rf = RawFeature()
        rf.market_id = market_id
        rf.ts = datetime.now(timezone.utc) - timedelta(hours=2) + timedelta(minutes=i * 0.5)
        rf.price_close = 60000 + i * 20
        rf.price_open = 60000 + i * 20 - 10
        rf.price_high = 60000 + i * 20 + 30
        rf.price_low = 60000 + i * 20 - 30
        rf.volume = 10.0
        rf.bid_depth_1pct = 5.0
        rf.ask_depth_1pct = 4.0
        rf.book_imbalance = 0.1
        rf.kalshi_yes_price = 0.55
        rf.kalshi_no_price = 0.45
        rf.kalshi_volume = 500.0
        session.add(rf)
    session.flush()

    for i in range(20):
        p = Prediction()
        p.market_id = market_id
        p.ts = datetime.now(timezone.utc) - timedelta(hours=2) + timedelta(minutes=i * 1.5)
        p.direction = "UP" if i % 2 == 0 else "DOWN"
        p.confidence = 0.6
        p.low_confidence = False
        p.model_version = "v0"
        p.settled_at = p.ts + timedelta(minutes=15)
        p.actual_outcome = 1 if i % 2 == 0 else 0
        session.add(p)
    session.flush()


def test_build_training_dataset_returns_xy(db_session):
    _seed_settled_data(db_session, "KXBTCUSD-DS")
    X, y = build_training_dataset(db_session, "KXBTCUSD-DS", lookback_hours=24)
    assert X is not None
    assert y is not None
    assert X.shape[1] == 25
    assert len(y) == X.shape[0]
    assert set(y).issubset({0, 1})


def test_build_training_dataset_returns_none_for_insufficient_data(db_session):
    result = build_training_dataset(db_session, "NO_DATA_MARKET", lookback_hours=24)
    assert result is None
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_trainer.py::test_build_training_dataset_returns_xy tests/test_trainer.py::test_build_training_dataset_returns_none_for_insufficient_data -v
```
Expected: `ImportError`

- [ ] **Step 3: Create trainer/__init__.py**

```python
```

- [ ] **Step 4: Create trainer/dataset.py**

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
    session: Session, market_id: str, lookback_hours: int
) -> tuple[np.ndarray, np.ndarray] | None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    settled_preds = (
        session.query(Prediction)
        .filter(
            Prediction.market_id == market_id,
            Prediction.actual_outcome != None,
            Prediction.ts >= cutoff,
            Prediction.low_confidence == False,
        )
        .order_by(Prediction.ts.asc())
        .all()
    )
    if len(settled_preds) < _MIN_SAMPLES:
        logger.info("Insufficient settled predictions for %s (%d)", market_id, len(settled_preds))
        return None

    all_raw = (
        session.query(RawFeature)
        .filter(RawFeature.market_id == market_id, RawFeature.ts >= cutoff)
        .order_by(RawFeature.ts.asc())
        .all()
    )

    market = session.get(Market, market_id)
    rows_X = []
    rows_y = []

    for pred in settled_preds:
        context_rows = [r for r in all_raw if r.ts <= pred.ts][-40:]
        if not context_rows:
            continue
        minutes_to_close = 7.5
        if market and market.close_time:
            minutes_to_close = max(0.0, (market.close_time - pred.ts).total_seconds() / 60)
        result = build_feature_vector(context_rows, minutes_to_close=minutes_to_close, ts=pred.ts)
        if result is None:
            continue
        vec, _ = result
        rows_X.append(vec)
        rows_y.append(int(pred.actual_outcome))

    if len(rows_X) < _MIN_SAMPLES:
        logger.info("Too few valid feature vectors for %s", market_id)
        return None

    return np.array(rows_X), np.array(rows_y)
```

- [ ] **Step 5: Run dataset tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_trainer.py::test_build_training_dataset_returns_xy tests/test_trainer.py::test_build_training_dataset_returns_none_for_insufficient_data -v
```
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add trainer/__init__.py trainer/dataset.py tests/test_trainer.py
git commit -m "feat: trainer dataset builder from settled predictions"
```

---

### Task 14: Trainer — model training, evaluation, and promotion

**Files:**
- Create: `trainer/train.py`
- Create: `trainer/main.py`
- Create: `trainer/requirements.txt`
- Extend: `tests/test_trainer.py`

- [ ] **Step 1: Append failing tests to tests/test_trainer.py**

```python
# Append to tests/test_trainer.py
from trainer.train import train_and_promote


def test_train_and_promote_creates_model_file(db_session, tmp_path):
    _seed_settled_data(db_session, "KXBTCUSD-TN")
    result = train_and_promote(
        session=db_session,
        market_id="KXBTCUSD-TN",
        lookback_hours=24,
        models_dir=str(tmp_path),
    )
    assert result is not None
    assert result["promoted"] in (True, False)
    assert "brier_score" in result


def test_train_does_not_promote_when_worse(db_session, tmp_path):
    from shared.orm import ModelRegistry
    _seed_settled_data(db_session, "KXBTCUSD-NP")
    # Insert a very good existing active model
    reg = ModelRegistry(
        market_id="KXBTCUSD-NP",
        version="v0",
        trained_at=datetime.now(timezone.utc),
        training_rows=100,
        brier_score=0.001,   # near-perfect — new model won't beat this
        artifact_path=str(tmp_path / "dummy.joblib"),
        is_active=True,
    )
    db_session.add(reg)
    db_session.flush()
    result = train_and_promote(
        session=db_session,
        market_id="KXBTCUSD-NP",
        lookback_hours=24,
        models_dir=str(tmp_path),
    )
    assert result["promoted"] is False
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_trainer.py::test_train_and_promote_creates_model_file tests/test_trainer.py::test_train_does_not_promote_when_worse -v
```
Expected: `ImportError`

- [ ] **Step 3: Create trainer/train.py**

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
from shared.orm import ModelRegistry
from trainer.dataset import build_training_dataset

logger = logging.getLogger(__name__)


def train_and_promote(
    session: Session,
    market_id: str,
    lookback_hours: int,
    models_dir: str,
) -> dict | None:
    result = build_training_dataset(session, market_id, lookback_hours)
    if result is None:
        logger.info("No training data for %s", market_id)
        return None

    X, y = result
    if len(np.unique(y)) < 2:
        logger.warning("Only one class in training data for %s — skipping", market_id)
        return None

    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, shuffle=False)

    model = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=4,
        learning_rate=0.1,
        use_label_encoder=False,
        eval_metric="logloss",
        verbosity=0,
    )
    model.fit(X_train, y_train)

    y_prob = model.predict_proba(X_val)[:, 1]
    new_brier = float(brier_score_loss(y_val, y_prob))
    logger.info("Trained %s: brier=%.4f on %d rows", market_id, new_brier, len(X))

    current_active = (
        session.query(ModelRegistry)
        .filter(ModelRegistry.market_id == market_id, ModelRegistry.is_active == True)
        .first()
    )
    current_brier = float(current_active.brier_score) if current_active else float("inf")

    version_num = (
        (int(current_active.version.lstrip("v")) + 1) if current_active else 1
    )
    version = f"v{version_num}"
    artifact_path = os.path.join(models_dir, f"{market_id.replace('/', '_')}_{version}.joblib")
    joblib.dump(model, artifact_path)

    promoted = new_brier < current_brier
    if promoted:
        if current_active:
            current_active.is_active = False
        new_reg = ModelRegistry(
            market_id=market_id,
            version=version,
            trained_at=datetime.now(timezone.utc),
            training_rows=len(X),
            brier_score=new_brier,
            artifact_path=artifact_path,
            is_active=True,
        )
        session.add(new_reg)
        logger.info("Promoted %s %s (brier %.4f < %.4f)", market_id, version, new_brier, current_brier)
    else:
        new_reg = ModelRegistry(
            market_id=market_id,
            version=version,
            trained_at=datetime.now(timezone.utc),
            training_rows=len(X),
            brier_score=new_brier,
            artifact_path=artifact_path,
            is_active=False,
        )
        session.add(new_reg)
        logger.info("Rejected %s %s (brier %.4f >= %.4f)", market_id, version, new_brier, current_brier)

    session.flush()
    return {"market_id": market_id, "version": version, "brier_score": new_brier, "promoted": promoted}
```

- [ ] **Step 4: Create trainer/main.py**

```python
# trainer/main.py
import logging
import time
from shared.db import make_session_factory, session_scope
from shared.orm import Market
from shared.settings import Settings
from trainer.train import train_and_promote

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def run_training_campaign(settings: Settings, session_factory) -> None:
    if not settings.training_campaign_enabled:
        logger.info("TRAINING_CAMPAIGN_ENABLED=false — exiting")
        return

    with session_scope(session_factory) as session:
        markets = session.query(Market).filter(Market.status == "active").all()
        market_ids = [m.market_id for m in markets]

    logger.info("Starting training campaign for %d markets", len(market_ids))
    for market_id in market_ids:
        with session_scope(session_factory) as session:
            result = train_and_promote(
                session=session,
                market_id=market_id,
                lookback_hours=settings.training_campaign_lookback_hours,
                models_dir="models",
            )
            if result:
                logger.info("Market %s: %s", market_id, result)
        time.sleep(0.1)


if __name__ == "__main__":
    settings = Settings()
    session_factory = make_session_factory(settings)
    run_training_campaign(settings, session_factory)
```

- [ ] **Step 5: Create trainer/requirements.txt**

```
xgboost==2.0.3
scikit-learn==1.4.2
numpy==1.26.4
pandas==2.2.2
joblib==1.4.2
sqlalchemy==2.0.30
psycopg2-binary==2.9.9
pydantic-settings==2.2.1
```

- [ ] **Step 6: Run all trainer tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_trainer.py -v
```
Expected: PASS (4 tests)

- [ ] **Step 7: Commit**

```bash
git add trainer/ tests/test_trainer.py
git commit -m "feat: trainer — XGBoost training, Brier evaluation, promotion logic"
```

---

### Task 15: Docker Compose

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Create docker-compose.yml**

```yaml
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
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 10

  migrate:
    build:
      context: .
      dockerfile: ingestor/Dockerfile
    command: >
      sh -c "
        POSTGRES_HOST=postgres
        alembic upgrade head
      "
    environment:
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_HOST=postgres
    depends_on:
      postgres:
        condition: service_healthy

  ingestor:
    build:
      context: .
      dockerfile: ingestor/Dockerfile
    environment:
      - POSTGRES_HOST=postgres
    env_file: .env
    depends_on:
      migrate:
        condition: service_completed_successfully
    volumes:
      - ./Kalshi-1.txt:/app/Kalshi-1.txt:ro
      - ./Kalshi-2-Demo.txt:/app/Kalshi-2-Demo.txt:ro
    ports:
      - "8001:8001"

  predictor:
    build:
      context: .
      dockerfile: predictor/Dockerfile
    environment:
      - POSTGRES_HOST=postgres
    env_file: .env
    depends_on:
      migrate:
        condition: service_completed_successfully
    volumes:
      - models:/app/models
    ports:
      - "8002:8002"

  api:
    build:
      context: .
      dockerfile: api/Dockerfile
    environment:
      - POSTGRES_HOST=postgres
    env_file: .env
    depends_on:
      migrate:
        condition: service_completed_successfully
    ports:
      - "8000:8000"

volumes:
  pgdata:
  models:
```

- [ ] **Step 2: Verify compose file parses correctly**

```bash
cd /workspace/crypto_analysis && docker compose config --quiet && echo "OK"
```
Expected: `OK` (or Docker-not-installed warning — acceptable in dev)

- [ ] **Step 3: Run the full test suite**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/ -v
```
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: Docker Compose — wire ingestor, predictor, API, postgres"
```

---

---

### Task 16: Outcome backfiller

**Files:**
- Create: `trainer/backfill.py`
- Extend: `tests/test_trainer.py`

The trainer's `build_training_dataset` requires `actual_outcome` to be set on settled predictions. This script scans `predictions` where `settled_at <= now AND actual_outcome IS NULL`, looks up the price at prediction time and the nearest `raw_features` price at or after `settled_at`, and writes the outcome. Run it as a cron job or before each training campaign.

- [ ] **Step 1: Append failing test to tests/test_trainer.py**

```python
# Append to tests/test_trainer.py
from trainer.backfill import backfill_outcomes


def test_backfill_sets_actual_outcome(db_session):
    market_id = "KXBTCUSD-BF"
    market = Market(
        market_id=market_id, ticker="KXBTCUSD", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(hours=1),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(market)
    db_session.flush()

    ts_pred = datetime.now(timezone.utc) - timedelta(minutes=20)
    ts_settle = ts_pred + timedelta(minutes=15)

    # Raw feature at prediction time — price 60000
    rf_at_pred = RawFeature()
    rf_at_pred.market_id = market_id
    rf_at_pred.ts = ts_pred
    rf_at_pred.price_close = 60000
    db_session.add(rf_at_pred)

    # Raw feature at settlement time — price 61000 (UP)
    rf_at_settle = RawFeature()
    rf_at_settle.market_id = market_id
    rf_at_settle.ts = ts_settle
    rf_at_settle.price_close = 61000
    db_session.add(rf_at_settle)
    db_session.flush()

    pred = Prediction(
        market_id=market_id,
        ts=ts_pred,
        direction="UP",
        confidence=0.65,
        low_confidence=False,
        model_version="v1",
        feature_snapshot_id=rf_at_pred.id,
        settled_at=ts_settle,
        actual_outcome=None,
    )
    db_session.add(pred)
    db_session.flush()

    count = backfill_outcomes(db_session)
    assert count == 1
    db_session.expire(pred)
    assert pred.actual_outcome == 1   # price went up


def test_backfill_skips_already_settled(db_session):
    market_id = "KXBTCUSD-SK"
    market = Market(
        market_id=market_id, ticker="KXBTCUSD", status="active",
        close_time=datetime.now(timezone.utc) + timedelta(hours=1),
        discovered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(market)
    ts_pred = datetime.now(timezone.utc) - timedelta(minutes=20)
    pred = Prediction(
        market_id=market_id,
        ts=ts_pred,
        direction="DOWN",
        confidence=0.6,
        low_confidence=False,
        model_version="v1",
        settled_at=ts_pred + timedelta(minutes=15),
        actual_outcome=0,  # already set
    )
    db_session.add(pred)
    db_session.flush()
    count = backfill_outcomes(db_session)
    assert count == 0
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_trainer.py::test_backfill_sets_actual_outcome tests/test_trainer.py::test_backfill_skips_already_settled -v
```
Expected: `ImportError`

- [ ] **Step 3: Create trainer/backfill.py**

```python
# trainer/backfill.py
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from shared.orm import Prediction, RawFeature

logger = logging.getLogger(__name__)


def backfill_outcomes(session: Session) -> int:
    now = datetime.now(timezone.utc)
    unsettled = (
        session.query(Prediction)
        .filter(
            Prediction.settled_at <= now,
            Prediction.actual_outcome == None,
        )
        .all()
    )
    updated = 0
    for pred in unsettled:
        price_at_pred = _price_at(session, pred.market_id, pred.ts, direction="before")
        price_at_settle = _price_at(session, pred.market_id, pred.settled_at, direction="after")
        if price_at_pred is None or price_at_settle is None:
            logger.warning("Cannot backfill %s at %s — missing raw_features", pred.market_id, pred.ts)
            continue
        pred.actual_outcome = 1 if price_at_settle > price_at_pred else 0
        updated += 1
    session.flush()
    logger.info("Backfilled %d predictions", updated)
    return updated


def _price_at(session: Session, market_id: str, ts: datetime, direction: str) -> float | None:
    if direction == "before":
        row = (
            session.query(RawFeature)
            .filter(RawFeature.market_id == market_id, RawFeature.ts <= ts)
            .order_by(RawFeature.ts.desc())
            .first()
        )
    else:
        row = (
            session.query(RawFeature)
            .filter(RawFeature.market_id == market_id, RawFeature.ts >= ts)
            .order_by(RawFeature.ts.asc())
            .first()
        )
    return float(row.price_close) if row and row.price_close else None
```

- [ ] **Step 4: Run backfill tests**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/test_trainer.py -v
```
Expected: PASS (all 6 trainer tests)

- [ ] **Step 5: Wire backfiller into trainer/main.py — replace `run_training_campaign` with:**

```python
# trainer/main.py — full replacement
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
    if not settings.training_campaign_enabled:
        logger.info("TRAINING_CAMPAIGN_ENABLED=false — exiting")
        return

    # Backfill outcomes first so training data is fresh
    with session_scope(session_factory) as session:
        backfilled = backfill_outcomes(session)
        logger.info("Backfilled %d outcomes before training", backfilled)

    with session_scope(session_factory) as session:
        markets = session.query(Market).filter(Market.status == "active").all()
        market_ids = [m.market_id for m in markets]

    logger.info("Starting training campaign for %d markets", len(market_ids))
    for market_id in market_ids:
        with session_scope(session_factory) as session:
            result = train_and_promote(
                session=session,
                market_id=market_id,
                lookback_hours=settings.training_campaign_lookback_hours,
                models_dir="models",
            )
            if result:
                logger.info("Market %s: %s", market_id, result)
        time.sleep(0.1)


if __name__ == "__main__":
    settings = Settings()
    session_factory = make_session_factory(settings)
    run_training_campaign(settings, session_factory)
```

- [ ] **Step 6: Run full test suite**

```bash
cd /workspace/crypto_analysis && python -m pytest tests/ -v
```
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add trainer/backfill.py trainer/main.py tests/test_trainer.py
git commit -m "feat: outcome backfiller — settle predictions from raw_features prices"
```

---

## Running the System

```bash
# 1. Start all services
docker compose up --build

# 2. Run Alembic migrations (handled by migrate service on first start)

# 3. Check ingestor discovered markets
curl http://localhost:8001/health

# 4. Check predictions (after ~60s of ingestor + predictor running)
curl http://localhost:8000/markets
curl http://localhost:8000/predict/<market_id>

# 5. Run training campaign (set TRAINING_CAMPAIGN_ENABLED=true in .env first)
docker compose run --rm predictor python -m trainer.main
```
