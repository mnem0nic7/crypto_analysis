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
