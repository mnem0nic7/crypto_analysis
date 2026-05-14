# predictor/inference.py
import logging
import numpy as np
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from shared.orm import Market, RawFeature, Prediction
from predictor.feature_builder import build_feature_vector

logger = logging.getLogger(__name__)
_FEATURE_LOOKBACK_ROWS = 40


def run_inference(session: Session, market: Market, model_loader) -> Prediction | None:
    rows = (
        session.query(RawFeature)
        .filter(RawFeature.market_id == market.market_id)
        .order_by(RawFeature.ts.desc())
        .limit(_FEATURE_LOOKBACK_ROWS)
        .all()
    )
    if not rows:
        logger.warning("No raw_features for %s — skipping inference", market.market_id)
        return None

    ts = datetime.now(timezone.utc)
    minutes_to_close = (
        (market.close_time - ts).total_seconds() / 60
        if market.close_time
        else 7.5
    )

    result = build_feature_vector(rows, minutes_to_close=minutes_to_close, ts=ts)
    feature_snapshot_id = rows[0].id  # most recent row id

    model = model_loader.get_model(market.market_id)
    if model is None:
        pred = Prediction(
            market_id=market.market_id,
            ts=ts,
            direction="UP",
            confidence=0.5,
            low_confidence=True,
            model_version="none",
            feature_snapshot_id=feature_snapshot_id,
            settled_at=market.close_time,
        )
        session.add(pred)
        session.flush()
        return pred

    if result is None:
        logger.info("Insufficient rows for %s — low_confidence", market.market_id)
        pred = Prediction(
            market_id=market.market_id,
            ts=ts,
            direction="UP",
            confidence=0.5,
            low_confidence=True,
            model_version=model_loader._version_cache.get(market.market_id, "unknown"),
            feature_snapshot_id=feature_snapshot_id,
            settled_at=market.close_time,
        )
        session.add(pred)
        session.flush()
        return pred

    vec, _ = result
    proba = model.predict_proba(vec.reshape(1, -1))[0]  # [p_down, p_up]
    p_up = float(proba[1])
    direction = "UP" if p_up >= 0.5 else "DOWN"
    confidence = p_up if direction == "UP" else 1 - p_up

    pred = Prediction(
        market_id=market.market_id,
        ts=ts,
        direction=direction,
        confidence=confidence,
        low_confidence=False,
        model_version=model_loader._version_cache.get(market.market_id, "unknown"),
        feature_snapshot_id=feature_snapshot_id,
        settled_at=market.close_time,
    )
    session.add(pred)
    session.flush()
    logger.info("Prediction for %s: %s (%.2f)", market.market_id, direction, confidence)
    return pred
