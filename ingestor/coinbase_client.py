import secrets
import time
import httpx
import jwt
from cryptography.hazmat.primitives import serialization


def series_ticker_to_product_id(series_ticker: str) -> str:
    # "KXBTCUSD" → "BTC-USD", "KXETHUSD" → "ETH-USD"
    # Strip leading "KX" and trailing "USD", insert "-USD"
    symbol = series_ticker[2:-3]  # e.g. "BTC"
    return f"{symbol}-USD"


class CoinbaseClient:
    BASE_URL = "https://api.coinbase.com"

    def __init__(self, key_name: str, private_key_pem: str):
        self._key_name = key_name
        # .env stores \n as literal backslash-n — normalize to real newlines
        normalized = private_key_pem.replace("\\n", "\n")
        try:
            self._private_key = serialization.load_pem_private_key(
                normalized.encode(), password=None
            )
        except Exception:
            self._private_key = None
        self._http = httpx.Client(timeout=10)

    def _make_jwt(self, method: str, path: str) -> str:
        if self._private_key is None:
            return ""
        payload = {
            "sub": self._key_name,
            "iss": "cdp",
            "nbf": int(time.time()),
            "exp": int(time.time()) + 120,
            "uri": f"{method.upper()} api.coinbase.com{path}",
        }
        return jwt.encode(
            payload,
            self._private_key,
            algorithm="ES256",
            headers={"kid": self._key_name, "nonce": secrets.token_hex(16)},
        )

    def get_candles(self, product_id: str, granularity: str = "ONE_MINUTE", limit: int = 40) -> list[dict]:
        path = f"/api/v3/brokerage/products/{product_id}/candles"
        token = self._make_jwt("GET", path)
        resp = self._http.get(
            self.BASE_URL + path,
            headers={"Authorization": f"Bearer {token}"},
            params={"granularity": granularity, "limit": limit},
        )
        resp.raise_for_status()
        return [
            {
                "start": int(c["start"]),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c["volume"]),
            }
            for c in resp.json().get("candles", [])
        ]

    def get_order_book(self, product_id: str, depth: int = 10) -> dict:
        path = "/api/v3/brokerage/product_book"
        token = self._make_jwt("GET", path)
        resp = self._http.get(
            self.BASE_URL + path,
            headers={"Authorization": f"Bearer {token}"},
            params={"product_id": product_id, "limit": depth},
        )
        resp.raise_for_status()
        book = resp.json()["pricebook"]
        bid_depth = sum(float(b["size"]) for b in book["bids"])
        ask_depth = sum(float(a["size"]) for a in book["asks"])
        total = bid_depth + ask_depth
        book_imbalance = (bid_depth - ask_depth) / total if total > 0 else 0.0
        return {
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
            "book_imbalance": book_imbalance,
        }

    def close(self):
        self._http.close()
