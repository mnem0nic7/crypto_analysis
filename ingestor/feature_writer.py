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
