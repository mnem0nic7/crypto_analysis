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
