# ingestor/main.py
import logging
import threading
import time
from datetime import datetime, timezone

from fastapi import FastAPI
import uvicorn

from shared.db import make_session_factory, session_scope
from shared.orm import Market
from shared.settings import Settings
from ingestor.kalshi_client import KalshiClient
from ingestor.coinbase_client import CoinbaseClient
from ingestor.market_discovery import filter_active_crypto_markets, upsert_markets
from ingestor.feature_writer import fetch_and_write
from trainer.backfill import backfill_outcomes

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
            try:
                backfilled = backfill_outcomes(session)
                if backfilled:
                    logger.info("Settled %d outcomes", backfilled)
            except Exception as exc:
                logger.error("Outcome backfill failed: %s", exc)
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
