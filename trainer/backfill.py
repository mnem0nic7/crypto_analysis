import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from shared.orm import Prediction, RawFeature

logger = logging.getLogger(__name__)


def backfill_outcomes(session: Session) -> int:
    now = datetime.now(timezone.utc)
    unsettled = (
        session.query(Prediction)
        .filter(
            Prediction.settled_at <= now,
            Prediction.actual_outcome == None,  # noqa: E711
        )
        .all()
    )
    updated = 0
    for pred in unsettled:
        price_at_pred = _price_at(session, pred.market_id, pred.ts, direction="before")
        price_at_settle = _price_at(session, pred.market_id, pred.settled_at, direction="after")
        if price_at_pred is None or price_at_settle is None:
            logger.warning(
                "Cannot backfill %s at %s — missing raw_features",
                pred.market_id, pred.ts,
            )
            continue
        # Flat price (==) treated as DOWN (0); rare in practice
        pred.actual_outcome = 1 if price_at_settle > price_at_pred else 0
        updated += 1
    session.flush()
    logger.info("Backfilled %d predictions", updated)
    return updated


def _price_at(session: Session, market_id: str, ts: datetime, direction: str) -> float | None:
    if direction == "before":
        row = (
            session.query(RawFeature)
            .filter(RawFeature.market_id == market_id, RawFeature.ts <= ts)
            .order_by(RawFeature.ts.desc())
            .first()
        )
    else:
        row = (
            session.query(RawFeature)
            .filter(RawFeature.market_id == market_id, RawFeature.ts >= ts)
            .order_by(RawFeature.ts.asc())
            .first()
        )
    return float(row.price_close) if row is not None and row.price_close is not None else None
