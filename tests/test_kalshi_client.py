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
            {"ticker": "KXBTC15M-26MAY141715-15", "series_ticker": "KXBTC15M",
             "status": "open", "close_time": "2026-05-14T18:15:00Z",
             "yes_bid_dollars": "0.55", "yes_ask_dollars": "0.57",
             "volume_fp": "1200"},
        ]
    }
    mock_response.raise_for_status = MagicMock()
    with patch.object(client._http, "get", return_value=mock_response):
        markets = client.get_crypto_markets()
    # 7 series × 1 market each = 7 total; each market gets series_ticker injected
    assert len(markets) == 7
    assert markets[0]["series_ticker"] in [
        "KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M",
        "KXDOGE15M", "KXBNB15M", "KXHYPE15M",
    ]


def test_get_market_price_returns_midpoint():
    client = KalshiClient(
        api_key="test-key",
        private_key_path="Kalshi-2-Demo.txt",
        base_url="https://demo-api.kalshi.co/trade-api/v2",
    )
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "market": {
            "ticker": "KXBTC15M-26MAY141715-15",
            "yes_bid_dollars": "0.54",
            "yes_ask_dollars": "0.58",
            "no_bid_dollars": "0.42",
            "no_ask_dollars": "0.46",
            "volume_fp": "1500.0",
        }
    }
    mock_response.raise_for_status = MagicMock()
    with patch.object(client._http, "get", return_value=mock_response):
        price = client.get_market_price("KXBTC15M-26MAY141715-15")
    assert price["yes_price"] == pytest.approx(0.56, abs=0.01)
    assert price["no_price"] == pytest.approx(0.44, abs=0.01)
    assert price["volume"] == pytest.approx(1500.0)
