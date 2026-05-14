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
