# trainer/dataset.py
import logging
import numpy as np
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from shared.orm import Market, RawFeature, Prediction
from shared.feature_builder import build_feature_vector

logger = logging.getLogger(__name__)
_MIN_SAMPLES = 5


def build_training_dataset(
    session: Session, series_ticker: str, lookback_hours: int
) -> tuple[np.ndarray, np.ndarray] | None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    settled_preds = (
        session.query(Prediction)
        .join(Market, Prediction.market_id == Market.market_id)
        .filter(
            Market.ticker == series_ticker,
            Prediction.actual_outcome != None,  # noqa: E711
            Prediction.ts >= cutoff,
            Prediction.low_confidence == False,
        )
        .order_by(Prediction.ts.asc())
        .all()
    )

    if len(settled_preds) < _MIN_SAMPLES:
        logger.info("Insufficient settled predictions for series %s (%d)", series_ticker, len(settled_preds))
        return None

    # Fetch all raw features for all contracts of this series within the lookback window.
    # Group by market_id so each prediction can find its context rows in O(1).
    all_raw = (
        session.query(RawFeature)
        .join(Market, RawFeature.market_id == Market.market_id)
        .filter(Market.ticker == series_ticker, RawFeature.ts >= cutoff)
        .order_by(RawFeature.ts.asc())
        .all()
    )
    raw_by_market: dict[str, list] = {}
    for r in all_raw:
        raw_by_market.setdefault(r.market_id, []).append(r)

    rows_X = []
    rows_y = []

    def _ts_utc(ts) -> datetime:
        if ts is None:
            return datetime.now(timezone.utc)
        if getattr(ts, "tzinfo", None) is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts

    for pred in settled_preds:
        pred_ts = _ts_utc(pred.ts)
        contract_rows = raw_by_market.get(pred.market_id, [])
        context_rows = [r for r in contract_rows if _ts_utc(r.ts) <= pred_ts][-40:]
        if not context_rows:
            continue

        market = session.get(Market, pred.market_id)
        minutes_to_close = 7.5
        if market and market.close_time:
            close_ts = _ts_utc(market.close_time)
            minutes_to_close = max(0.0, (close_ts - pred_ts).total_seconds() / 60)

        result = build_feature_vector(context_rows, minutes_to_close=minutes_to_close, ts=pred_ts)
        if result is None:
            continue
        vec, _ = result
        rows_X.append(vec)
        rows_y.append(int(pred.actual_outcome))

    if len(rows_X) < _MIN_SAMPLES:
        logger.info("Too few valid feature vectors for series %s", series_ticker)
        return None

    return np.array(rows_X), np.array(rows_y)
