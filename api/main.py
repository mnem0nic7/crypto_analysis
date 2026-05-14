# api/main.py
import os
from datetime import datetime, timezone
from typing import Callable
from fastapi import FastAPI, HTTPException, Depends
from sqlalchemy.orm import Session
from shared.orm import Market, Prediction


def create_app(session_factory_fn: Callable = None) -> FastAPI:
    app = FastAPI(title="Kalshi Crypto Prediction API")

    if session_factory_fn is None:
        # Production mode: create factory from settings
        from shared.db import make_session_factory
        from shared.settings import Settings
        settings = Settings()
        _factory = make_session_factory(settings)

        def _get_db():
            session = _factory()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
    else:
        # Test mode: caller manages session lifecycle
        def _get_db():
            yield session_factory_fn()

    @app.get("/health")
    def health(session: Session = Depends(_get_db)):
        active = session.query(Market).filter(Market.status == "active").count()
        stale = session.query(Market).filter(Market.status == "stale").count()
        return {"status": "ok", "active_markets": active, "stale_markets": stale}

    @app.get("/markets")
    def get_markets(session: Session = Depends(_get_db)):
        markets = session.query(Market).filter(Market.status == "active").all()
        now = datetime.now(timezone.utc)
        result = []
        for m in markets:
            try:
                if m.close_time is not None:
                    close_time = m.close_time
                    # Ensure timezone-aware for subtraction
                    if close_time.tzinfo is None:
                        close_time = close_time.replace(tzinfo=timezone.utc)
                    minutes_to_close = round((close_time - now).total_seconds() / 60, 1)
                else:
                    minutes_to_close = None
            except Exception:
                minutes_to_close = None
            result.append(
                {
                    "market_id": m.market_id,
                    "ticker": m.ticker,
                    "title": m.title,
                    "close_time": m.close_time.isoformat() if m.close_time else None,
                    "minutes_to_close": minutes_to_close,
                }
            )
        return result

    @app.get("/predict/{market_id}")
    def get_prediction(market_id: str, session: Session = Depends(_get_db)):
        market = session.get(Market, market_id)
        if market is None:
            raise HTTPException(status_code=404, detail="Market not found")
        pred = (
            session.query(Prediction)
            .filter(Prediction.market_id == market_id)
            .order_by(Prediction.ts.desc())
            .first()
        )
        if pred is None:
            raise HTTPException(status_code=404, detail="No prediction available")
        now = datetime.now(timezone.utc)
        ts = pred.ts
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        feature_age = int((now - ts).total_seconds()) if ts is not None else None
        return {
            "market_id": market_id,
            "direction": pred.direction,
            "confidence": float(pred.confidence),
            "low_confidence": pred.low_confidence,
            "model_version": pred.model_version,
            "ts": pred.ts.isoformat(),
            "feature_age_seconds": feature_age,
        }

    @app.get("/history/{market_id}")
    def get_history(
        market_id: str,
        limit: int = 100,
        session: Session = Depends(_get_db),
    ):
        market = session.get(Market, market_id)
        if market is None:
            raise HTTPException(status_code=404, detail="Market not found")
        preds = (
            session.query(Prediction)
            .filter(
                Prediction.market_id == market_id,
                Prediction.actual_outcome != None,  # noqa: E711
            )
            .order_by(Prediction.ts.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "ts": p.ts.isoformat(),
                "direction": p.direction,
                "confidence": float(p.confidence),
                "actual_outcome": p.actual_outcome,
                "correct": (
                    (p.direction == "UP" and p.actual_outcome == 1)
                    or (p.direction == "DOWN" and p.actual_outcome == 0)
                ),
            }
            for p in preds
        ]

    @app.get("/stats/summary")
    def get_stats_summary(session: Session = Depends(_get_db)):
        from sqlalchemy import func, case as sa_case

        correct_expr = sa_case(
            (
                (Prediction.direction == "UP") & (Prediction.actual_outcome == 1),
                1,
            ),
            (
                (Prediction.direction == "DOWN") & (Prediction.actual_outcome == 0),
                1,
            ),
            else_=0,
        )

        rows = (
            session.query(
                Market.ticker,
                Prediction.market_id,
                func.count(Prediction.id).label("settled_count"),
                func.avg(correct_expr).label("accuracy"),
            )
            .join(Market, Market.market_id == Prediction.market_id)
            .filter(Prediction.actual_outcome != None)  # noqa: E711
            .group_by(Market.ticker, Prediction.market_id)
            .all()
        )

        if not rows:
            return {
                "total_settled": 0,
                "overall_accuracy": 0.0,
                "high_conf_accuracy": 0.0,
                "markets": [],
            }

        total_settled = sum(r.settled_count for r in rows)
        overall_accuracy = (
            sum(float(r.accuracy or 0) * r.settled_count for r in rows) / total_settled
        )

        hc_preds = (
            session.query(Prediction)
            .filter(
                Prediction.actual_outcome != None,  # noqa: E711
                Prediction.confidence >= 0.65,
            )
            .all()
        )
        if hc_preds:
            hc_correct = sum(
                1 for p in hc_preds
                if (p.direction == "UP" and p.actual_outcome == 1)
                or (p.direction == "DOWN" and p.actual_outcome == 0)
            )
            high_conf_accuracy = hc_correct / len(hc_preds)
        else:
            high_conf_accuracy = 0.0

        return {
            "total_settled": total_settled,
            "overall_accuracy": round(overall_accuracy, 3),
            "high_conf_accuracy": round(high_conf_accuracy, 3),
            "markets": [
                {
                    "ticker": r.ticker,
                    "accuracy": round(float(r.accuracy or 0), 3),
                    "settled_count": r.settled_count,
                }
                for r in rows
            ],
        }

    @app.get("/stats/models")
    def get_stats_models(session: Session = Depends(_get_db)):
        from shared.orm import ModelRegistry
        rows = (
            session.query(ModelRegistry, Market.ticker)
            .join(Market, Market.market_id == ModelRegistry.market_id)
            .filter(ModelRegistry.is_active == True)  # noqa: E712
            .all()
        )
        return [
            {
                "market_id": m.market_id,
                "ticker": ticker,
                "version": m.version,
                "brier_score": float(m.brier_score),
                "training_rows": m.training_rows,
                "trained_at": m.trained_at.isoformat(),
                "is_active": m.is_active,
            }
            for m, ticker in rows
        ]

    @app.get("/slot")
    def get_slot():
        return {"slot": os.environ.get("DEPLOY_SLOT", "blue")}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
