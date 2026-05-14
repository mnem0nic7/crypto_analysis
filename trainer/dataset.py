# trainer/dataset.py
import logging
import numpy as np
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from shared.orm import Market, RawFeature, Prediction
from predictor.feature_builder import build_feature_vector

logger = logging.getLogger(__name__)
_MIN_SAMPLES = 5


def build_training_dataset(
    session: Session, market_id: str, lookback_hours: int
) -> tuple[np.ndarray, np.ndarray] | None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    settled_preds = (
        session.query(Prediction)
        .filter(
            Prediction.market_id == market_id,
            Prediction.actual_outcome != None,
            Prediction.ts >= cutoff,
            Prediction.low_confidence == False,
        )
        .order_by(Prediction.ts.asc())
        .all()
    )
    if len(settled_preds) < _MIN_SAMPLES:
        logger.info("Insufficient settled predictions for %s (%d)", market_id, len(settled_preds))
        return None

    all_raw = (
        session.query(RawFeature)
        .filter(RawFeature.market_id == market_id, RawFeature.ts >= cutoff)
        .order_by(RawFeature.ts.asc())
        .all()
    )

    market = session.get(Market, market_id)
    rows_X = []
    rows_y = []

    def _ts_utc(ts) -> datetime:
        """Return a timezone-aware UTC datetime, adding UTC if naive (e.g. from SQLite)."""
        if ts is None:
            return datetime.now(timezone.utc)
        if getattr(ts, "tzinfo", None) is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts

    for pred in settled_preds:
        pred_ts_utc = _ts_utc(pred.ts)
        context_rows = [r for r in all_raw if _ts_utc(r.ts) <= pred_ts_utc][-40:]
        if not context_rows:
            continue
        minutes_to_close = 7.5
        if market and market.close_time:
            close_time_utc = _ts_utc(market.close_time)
            minutes_to_close = max(0.0, (close_time_utc - pred_ts_utc).total_seconds() / 60)
        result = build_feature_vector(context_rows, minutes_to_close=minutes_to_close, ts=pred_ts_utc)
        if result is None:
            continue
        vec, _ = result
        rows_X.append(vec)
        rows_y.append(int(pred.actual_outcome))

    if len(rows_X) < _MIN_SAMPLES:
        logger.info("Too few valid feature vectors for %s", market_id)
        return None

    return np.array(rows_X), np.array(rows_y)
