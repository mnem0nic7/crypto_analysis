import pytest
from unittest.mock import patch, MagicMock
from ingestor.coinbase_client import CoinbaseClient


def test_get_candles_returns_ohlcv():
    client = CoinbaseClient(key_name="orgs/x/apiKeys/y", private_key_pem="dummy")
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "candles": [
            {"start": "1715700000", "open": "61000", "high": "61500",
             "low": "60800", "close": "61200", "volume": "12.5"},
            {"start": "1715700060", "open": "61200", "high": "61400",
             "low": "61100", "close": "61350", "volume": "8.3"},
        ]
    }
    mock_response.raise_for_status = MagicMock()
    with patch.object(client._http, "get", return_value=mock_response):
        candles = client.get_candles("BTC-USD", granularity="ONE_MINUTE", limit=2)
    assert len(candles) == 2
    assert candles[0]["close"] == pytest.approx(61200.0)
    assert candles[0]["volume"] == pytest.approx(12.5)


def test_get_order_book_returns_bid_ask_depth():
    client = CoinbaseClient(key_name="orgs/x/apiKeys/y", private_key_pem="dummy")
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "pricebook": {
            "product_id": "BTC-USD",
            "bids": [
                {"price": "61000", "size": "0.5"},
                {"price": "60950", "size": "1.2"},
                {"price": "60900", "size": "2.1"},
            ],
            "asks": [
                {"price": "61050", "size": "0.3"},
                {"price": "61100", "size": "0.9"},
                {"price": "61150", "size": "1.5"},
            ],
        }
    }
    mock_response.raise_for_status = MagicMock()
    with patch.object(client._http, "get", return_value=mock_response):
        book = client.get_order_book("BTC-USD", depth=3)
    assert book["bid_depth"] == pytest.approx(3.8)   # 0.5 + 1.2 + 2.1
    assert book["ask_depth"] == pytest.approx(2.7)   # 0.3 + 0.9 + 1.5
    assert "book_imbalance" in book


def test_series_ticker_to_coinbase_product():
    from ingestor.coinbase_client import series_ticker_to_product_id
    assert series_ticker_to_product_id("KXBTCUSD") == "BTC-USD"
    assert series_ticker_to_product_id("KXETHUSD") == "ETH-USD"
    assert series_ticker_to_product_id("KXSOLUSD") == "SOL-USD"
