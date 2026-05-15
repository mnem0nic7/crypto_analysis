# analysis/loader.py
from datetime import timezone
import numpy as np
from sqlalchemy.orm import Session
from shared.orm import Market, Prediction, RawFeature


def _to_utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_settled_predictions(session: Session) -> dict:
    """
    Query all settled predictions joined with their raw_features snapshot
    and market metadata. Returns a dict of numpy arrays with derived columns
    pre-computed. Returns {"n": 0} when no data is available.
    """
    rows = (
        session.query(
            Prediction.direction,
            Prediction.confidence,
            Prediction.actual_outcome,
            Prediction.ts,
            RawFeature.kalshi_yes_price,
            RawFeature.kalshi_no_price,
            Market.discovered_at,
            Market.close_time,
        )
        .join(RawFeature, RawFeature.id == Prediction.feature_snapshot_id)
        .join(Market, Market.market_id == Prediction.market_id)
        .filter(
            Prediction.actual_outcome != None,          # noqa: E711
            Prediction.feature_snapshot_id != None,     # noqa: E711
            RawFeature.kalshi_yes_price != None,        # noqa: E711
            RawFeature.kalshi_no_price != None,         # noqa: E711
            Market.close_time != None,                  # noqa: E711
            Market.discovered_at != None,               # noqa: E711
            Prediction.ts < Market.close_time,
        )
        .all()
    )

    if not rows:
        return {"n": 0}

    direction = np.array([r.direction for r in rows])
    confidence = np.array([float(r.confidence) for r in rows])
    actual_outcome = np.array([int(r.actual_outcome) for r in rows])
    yes_price = np.array([float(r.kalshi_yes_price) for r in rows])
    no_price = np.array([float(r.kalshi_no_price) for r in rows])

    is_up = direction == "UP"
    entry_price = np.where(is_up, yes_price, no_price)
    spread_bps = (1.0 - yes_price - no_price) * 10000.0
    raw_edge_bps = (confidence - entry_price) * 10000.0

    outcome_correct = np.where(
        is_up, actual_outcome == 1, actual_outcome == 0
    ).astype(np.int8)

    # Raw P&L before fee: win gets (1 - entry_price), loss loses entry_price
    raw_pnl = (
        outcome_correct * (1.0 - entry_price)
        - (1 - outcome_correct) * entry_price
    )

    market_age_seconds = np.array([
        (_to_utc(r.ts) - _to_utc(r.discovered_at)).total_seconds()
        for r in rows
    ])
    seconds_to_close = np.array([
        (_to_utc(r.close_time) - _to_utc(r.ts)).total_seconds()
        for r in rows
    ])

    return {
        "n": len(rows),
        "confidence": confidence,
        "entry_price": entry_price,
        "spread_bps": spread_bps,
        "raw_edge_bps": raw_edge_bps,
        "market_age_seconds": market_age_seconds,
        "seconds_to_close": seconds_to_close,
        "outcome_correct": outcome_correct,
        "raw_pnl": raw_pnl,
    }
