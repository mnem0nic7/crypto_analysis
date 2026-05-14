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
