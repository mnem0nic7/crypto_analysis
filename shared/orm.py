# shared/orm.py
from sqlalchemy import (
    BigInteger, Boolean, Column, ForeignKey, Integer, JSON, Numeric,
    SmallInteger, Text, TIMESTAMP, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

# SQLite does not support BigInteger RETURNING; use Integer as a variant for tests
_BigInt = BigInteger().with_variant(Integer, "sqlite")

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

    id = Column(_BigInt, primary_key=True, autoincrement=True)
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

    id = Column(_BigInt, primary_key=True, autoincrement=True)
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

    id = Column(_BigInt, primary_key=True, autoincrement=True)
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
