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
