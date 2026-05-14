import base64
import time
import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding


CRYPTO_15M_SERIES = [
    "KXBTC15M",
    "KXETH15M",
    "KXSOL15M",
    "KXXRP15M",
    "KXDOGE15M",
    "KXBNB15M",
    "KXHYPE15M",
]


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
        # The new elections API dropped the category field, so we query each known
        # 15-minute crypto series directly instead of fetching all and filtering.
        markets = []
        path = "/trade-api/v2/markets"
        url = self._base_url + "/markets"
        for series_ticker in CRYPTO_15M_SERIES:
            headers = self._make_headers("GET", path)
            resp = self._http.get(url, headers=headers,
                params={"series_ticker": series_ticker, "status": "open", "limit": 200})
            resp.raise_for_status()
            for m in resp.json().get("markets", []):
                m["series_ticker"] = series_ticker
                markets.append(m)
        return markets

    def get_market_price(self, ticker: str) -> dict:
        path = f"/trade-api/v2/markets/{ticker}"
        url = f"{self._base_url}/markets/{ticker}"
        headers = self._make_headers("GET", path)
        resp = self._http.get(url, headers=headers)
        resp.raise_for_status()
        m = resp.json()["market"]
        yes_bid = float(m.get("yes_bid_dollars") or 0)
        yes_ask = float(m.get("yes_ask_dollars") or 0)
        no_bid = float(m.get("no_bid_dollars") or 0)
        no_ask = float(m.get("no_ask_dollars") or 0)
        yes_price = (yes_bid + yes_ask) / 2
        no_price = (no_bid + no_ask) / 2
        return {"yes_price": yes_price, "no_price": no_price, "volume": float(m.get("volume_fp") or 0)}

    def get_settled_markets_page(
        self, series_ticker: str, cursor: str | None = None
    ) -> dict:
        """Return one page (up to 200) of settled markets and the next cursor."""
        path = "/trade-api/v2/markets"
        url = self._base_url + "/markets"
        params: dict = {"series_ticker": series_ticker, "status": "settled", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        headers = self._make_headers("GET", path)
        resp = self._http.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        return {"markets": data.get("markets", []), "cursor": data.get("cursor")}

    def close(self):
        self._http.close()
