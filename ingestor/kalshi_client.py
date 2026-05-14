import base64
import time
import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding


class KalshiClient:
    def __init__(self, api_key: str, private_key_path: str, base_url: str):
        self._api_key = api_key
        with open(private_key_path, "rb") as f:
            self._private_key = serialization.load_pem_private_key(f.read(), password=None)
        self._base_url = base_url.rstrip("/")
        self._http = httpx.Client(timeout=10)

    def _make_headers(self, method: str, path: str) -> dict:
        ts_ms = str(int(time.time() * 1000))
        msg = (ts_ms + method.upper() + path).encode()
        sig = self._private_key.sign(
            msg,
            asym_padding.PSS(
                mgf=asym_padding.MGF1(hashes.SHA256()),
                salt_length=asym_padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "Kalshi-Access-Key": self._api_key,
            "Kalshi-Access-Timestamp": ts_ms,
            "Kalshi-Access-Signature": base64.b64encode(sig).decode(),
            "Content-Type": "application/json",
        }

    def get_crypto_markets(self) -> list[dict]:
        path = "/trade-api/v2/markets"
        url = self._base_url + "/markets"
        headers = self._make_headers("GET", path)
        resp = self._http.get(url, headers=headers, params={"status": "open", "limit": 200})
        resp.raise_for_status()
        all_markets = resp.json().get("markets", [])
        return [m for m in all_markets if m.get("category") == "crypto"]

    def get_market_price(self, ticker: str) -> dict:
        path = f"/trade-api/v2/markets/{ticker}"
        url = f"{self._base_url}/markets/{ticker}"
        headers = self._make_headers("GET", path)
        resp = self._http.get(url, headers=headers)
        resp.raise_for_status()
        m = resp.json()["market"]
        yes_price = (m["yes_bid"] + m["yes_ask"]) / 2
        no_price = (m["no_bid"] + m["no_ask"]) / 2
        return {"yes_price": yes_price, "no_price": no_price, "volume": m.get("volume", 0)}

    def close(self):
        self._http.close()
