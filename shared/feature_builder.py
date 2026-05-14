# shared/feature_builder.py
import math
import numpy as np
from datetime import datetime, timezone, timedelta
from shared.orm import RawFeature

FEATURE_NAMES = [
    "price_momentum_1m",
    "price_momentum_5m",
    "price_momentum_15m",
    "volatility_5m",
    "volatility_roc",
    "vwap_deviation_15m",
    "candle_body_ratio",
    "volume_momentum_5m",
    "book_imbalance_latest",
    "book_imbalance_trend",
    "bid_depth_1pct",
    "ask_depth_1pct",
    "kalshi_yes_price",
    "kalshi_no_price",
    "kalshi_price_momentum_5m",
    "kalshi_deviation",
    "kalshi_volume_zscore",
    "kalshi_volume_momentum",
    "minutes_to_close",
    "sin_hour",
    "cos_hour",
    "sin_dow",
    "cos_dow",
    "is_weekend",
    "consecutive_direction",
]

_MIN_ROWS = 10


def build_feature_vector(
    rows: list,
    minutes_to_close: float,
    ts: datetime,
) -> tuple[np.ndarray, list[str]] | None:
    if len(rows) < _MIN_ROWS:
        return None

    rows = sorted(rows, key=lambda r: r.ts)
    latest = rows[-1]

    def _f(val, default=0.0) -> float:
        try:
            return float(val) if val is not None else default
        except (TypeError, ValueError):
            return default

    closes = np.array([_f(r.price_close) for r in rows])
    volumes = np.array([_f(r.volume) for r in rows])
    now = ts

    def _ts_utc(ts) -> datetime:
        if ts is None:
            return now
        if getattr(ts, "tzinfo", None) is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts

    def _within(minutes: float) -> list:
        cutoff = now - timedelta(minutes=minutes)
        return [r for r in rows if _ts_utc(r.ts) >= cutoff]

    def _momentum(minutes: float) -> float:
        window = _within(minutes)
        if not window:
            return 0.0
        first_price = _f(window[0].price_close)
        last_price = _f(latest.price_close)
        return (last_price - first_price) / first_price if first_price != 0 else 0.0

    def _volatility(window_rows: list) -> float:
        prices = np.array([_f(r.price_close) for r in window_rows])
        if len(prices) < 2:
            return 0.0
        rets = np.diff(prices) / np.where(prices[:-1] != 0, prices[:-1], 1.0)
        return float(np.std(rets))

    mom_1m = _momentum(1)
    mom_5m = _momentum(5)
    mom_15m = _momentum(15)

    w5 = _within(5)
    w10 = _within(10)
    vol_5m = _volatility(w5)
    vol_10m = _volatility(w10)
    vol_roc = vol_5m - vol_10m

    w15 = _within(15)
    if w15 and volumes[-len(w15):].sum() > 0:
        p15 = np.array([_f(r.price_close) for r in w15])
        v15 = np.array([_f(r.volume) for r in w15])
        vwap = np.dot(p15, v15) / v15.sum() if v15.sum() > 0 else _f(latest.price_close)
        vwap_dev = (_f(latest.price_close) - vwap) / vwap if vwap > 0 else 0.0
    else:
        vwap_dev = 0.0

    hi = _f(latest.price_high)
    lo = _f(latest.price_low)
    op = _f(latest.price_open)
    cl = _f(latest.price_close)
    candle_range = hi - lo
    body_ratio = (cl - op) / candle_range if candle_range > 0 else 0.0

    avg_vol_15m = float(np.mean([_f(r.volume) for r in w15])) if w15 else 0.0
    avg_vol_5m = float(np.mean([_f(r.volume) for r in w5])) if w5 else 0.0
    vol_mom = (avg_vol_5m - avg_vol_15m) / avg_vol_15m if avg_vol_15m > 0 else 0.0

    book_latest = _f(latest.book_imbalance)
    last5_imb = [_f(r.book_imbalance) for r in rows[-5:]]
    book_trend = last5_imb[-1] - last5_imb[0] if len(last5_imb) >= 2 else 0.0
    bid_depth = _f(latest.bid_depth_1pct)
    ask_depth = _f(latest.ask_depth_1pct)

    kalshi_yes = _f(latest.kalshi_yes_price, 0.5)
    kalshi_no = _f(latest.kalshi_no_price, 0.5)

    kalshi_yes_prices = [_f(r.kalshi_yes_price, 0.5) for r in _within(5)]
    if len(kalshi_yes_prices) >= 2:
        kalshi_mom = kalshi_yes_prices[-1] - kalshi_yes_prices[0]
    else:
        kalshi_mom = 0.0

    fair_yes = max(0.01, min(0.99, 0.5 + mom_15m * 5))
    kalshi_dev = kalshi_yes - fair_yes

    kalshi_vols = np.array([_f(r.kalshi_volume) for r in rows])
    kv_mean = float(np.mean(kalshi_vols))
    kv_std = float(np.std(kalshi_vols))
    kalshi_vol_z = (float(_f(latest.kalshi_volume)) - kv_mean) / kv_std if kv_std > 0 else 0.0

    kv_5m = np.mean([_f(r.kalshi_volume) for r in w5]) if w5 else kv_mean
    kalshi_vol_mom = (kv_5m - kv_mean) / kv_mean if kv_mean > 0 else 0.0

    hour = now.hour
    dow = now.weekday()
    sin_hour = math.sin(2 * math.pi * hour / 24)
    cos_hour = math.cos(2 * math.pi * hour / 24)
    sin_dow = math.sin(2 * math.pi * dow / 7)
    cos_dow = math.cos(2 * math.pi * dow / 7)
    is_weekend = 1.0 if dow >= 5 else 0.0

    consecutive = 0
    for r in reversed(rows[-10:]):
        if _f(r.price_close) > _f(r.price_open):
            if consecutive >= 0:
                consecutive += 1
            else:
                break
        else:
            if consecutive <= 0:
                consecutive -= 1
            else:
                break

    vec = np.array([
        mom_1m, mom_5m, mom_15m,
        vol_5m, vol_roc, vwap_dev,
        body_ratio, vol_mom,
        book_latest, book_trend, bid_depth, ask_depth,
        kalshi_yes, kalshi_no, kalshi_mom, kalshi_dev,
        kalshi_vol_z, kalshi_vol_mom,
        minutes_to_close,
        sin_hour, cos_hour, sin_dow, cos_dow, is_weekend,
        float(consecutive),
    ], dtype=float)

    vec = np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0)
    return vec, FEATURE_NAMES
