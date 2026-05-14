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
        if window_start <= datetime.fromtimestamp(unix_ts, tz=timezone.utc) < pred_ts
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
