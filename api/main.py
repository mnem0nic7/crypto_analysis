# api/main.py
import os
from datetime import datetime, timezone
from typing import Callable
from fastapi import FastAPI, HTTPException, Depends
from sqlalchemy.orm import Session
from shared.orm import Market, Prediction, RawFeature


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

        hc_expr = sa_case(
            (
                (Prediction.confidence >= 0.65)
                & (Prediction.direction == "UP")
                & (Prediction.actual_outcome == 1),
                1,
            ),
            (
                (Prediction.confidence >= 0.65)
                & (Prediction.direction == "DOWN")
                & (Prediction.actual_outcome == 0),
                1,
            ),
            else_=0,
        )
        hc_total_expr = sa_case(
            (Prediction.confidence >= 0.65, 1),
            else_=0,
        )
        hc_row = (
            session.query(
                func.sum(hc_expr).label("hc_correct"),
                func.sum(hc_total_expr).label("hc_total"),
            )
            .filter(Prediction.actual_outcome != None)  # noqa: E711
            .one()
        )
        if hc_row.hc_total:
            high_conf_accuracy = float(hc_row.hc_correct or 0) / float(hc_row.hc_total)
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

    _RAW_SORTABLE = {
        "ts", "price_open", "price_high", "price_low", "price_close",
        "volume", "bid_depth_1pct", "ask_depth_1pct", "book_imbalance",
        "kalshi_yes_price", "kalshi_no_price", "kalshi_volume",
        "price_momentum_1m", "price_momentum_5m", "price_momentum_15m",
        "volatility_5m",
    }

    @app.get("/data/raw-features")
    def get_raw_features(
        series_ticker: str,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        page: int = 1,
        page_size: int = 100,
        sort_by: str = "ts",
        sort_dir: str = "desc",
        session: Session = Depends(_get_db),
    ):
        if sort_by not in _RAW_SORTABLE:
            raise HTTPException(status_code=400, detail=f"sort_by must be one of {sorted(_RAW_SORTABLE)}")
        if sort_dir not in ("asc", "desc"):
            raise HTTPException(status_code=400, detail="sort_dir must be 'asc' or 'desc'")

        q = (
            session.query(RawFeature)
            .join(Market, RawFeature.market_id == Market.market_id)
            .filter(Market.ticker == series_ticker)
        )
        if from_ts:
            q = q.filter(RawFeature.ts >= from_ts)
        if to_ts:
            q = q.filter(RawFeature.ts <= to_ts)

        total = q.count()
        col = getattr(RawFeature, sort_by)
        rows = (
            q.order_by(col.desc() if sort_dir == "desc" else col.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        def _f(v):
            return float(v) if v is not None else None

        return {
            "rows": [
                {
                    "id": r.id, "ts": r.ts.isoformat(), "market_id": r.market_id,
                    "price_open": _f(r.price_open), "price_high": _f(r.price_high),
                    "price_low": _f(r.price_low), "price_close": _f(r.price_close),
                    "volume": _f(r.volume),
                    "bid_depth_1pct": _f(r.bid_depth_1pct),
                    "ask_depth_1pct": _f(r.ask_depth_1pct),
                    "book_imbalance": _f(r.book_imbalance),
                    "kalshi_yes_price": _f(r.kalshi_yes_price),
                    "kalshi_no_price": _f(r.kalshi_no_price),
                    "kalshi_volume": _f(r.kalshi_volume),
                    "price_momentum_1m": _f(r.price_momentum_1m),
                    "price_momentum_5m": _f(r.price_momentum_5m),
                    "price_momentum_15m": _f(r.price_momentum_15m),
                    "volatility_5m": _f(r.volatility_5m),
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def _get_feature_matrix(session, series_ticker, from_ts, to_ts):
        import numpy as np
        from shared.feature_builder import build_feature_vector
        from shared.orm import RawFeature as RF

        q = (
            session.query(Prediction)
            .join(Market, Prediction.market_id == Market.market_id)
            .filter(Market.ticker == series_ticker, Prediction.actual_outcome != None)  # noqa: E711
        )
        if from_ts:
            q = q.filter(Prediction.ts >= from_ts)
        if to_ts:
            q = q.filter(Prediction.ts <= to_ts)
        preds = q.order_by(Prediction.ts.asc()).all()

        if len(preds) < 5:
            return None, None

        raw_q = (
            session.query(RF)
            .join(Market, RF.market_id == Market.market_id)
            .filter(Market.ticker == series_ticker)
        )
        if from_ts:
            raw_q = raw_q.filter(RF.ts >= from_ts)
        all_raw = raw_q.order_by(RF.ts.asc()).all()
        raw_by_market: dict = {}
        for r in all_raw:
            raw_by_market.setdefault(r.market_id, []).append(r)

        X, y = [], []
        for pred in preds:
            pred_ts = pred.ts if pred.ts.tzinfo else pred.ts.replace(tzinfo=timezone.utc)
            context = [
                r for r in raw_by_market.get(pred.market_id, [])
                if (r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc)) <= pred_ts
            ][-40:]
            market = session.get(Market, pred.market_id)
            minutes_to_close = 7.5
            if market and market.close_time:
                ct = market.close_time if market.close_time.tzinfo else market.close_time.replace(tzinfo=timezone.utc)
                minutes_to_close = max(0.0, (ct - pred_ts).total_seconds() / 60)
            result = build_feature_vector(context, minutes_to_close=minutes_to_close, ts=pred_ts)
            if result is None:
                continue
            vec, _ = result
            X.append(vec)
            y.append(int(pred.actual_outcome))

        if len(X) < 5:
            return None, None
        return np.array(X), np.array(y)

    @app.get("/data/feature-vectors")
    def get_feature_vectors(
        series_ticker: str,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
        sort_by: str = "ts",
        sort_dir: str = "desc",
        session: Session = Depends(_get_db),
    ):
        from shared.feature_builder import build_feature_vector, FEATURE_NAMES
        from shared.orm import RawFeature as RF

        page_size = min(page_size, 50)
        _FV_SORTABLE = {"ts", "confidence", "actual_outcome"}
        if sort_by not in _FV_SORTABLE:
            raise HTTPException(status_code=400, detail=f"sort_by must be one of {sorted(_FV_SORTABLE)}")
        if sort_dir not in ("asc", "desc"):
            raise HTTPException(status_code=400, detail="sort_dir must be 'asc' or 'desc'")

        q = (
            session.query(Prediction)
            .join(Market, Prediction.market_id == Market.market_id)
            .filter(Market.ticker == series_ticker, Prediction.actual_outcome != None)  # noqa: E711
        )
        if from_ts:
            q = q.filter(Prediction.ts >= from_ts)
        if to_ts:
            q = q.filter(Prediction.ts <= to_ts)

        total = q.count()
        col = getattr(Prediction, sort_by)
        preds = (
            q.order_by(col.desc() if sort_dir == "desc" else col.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        rows = []
        skipped = 0
        for pred in preds:
            pred_ts = pred.ts if pred.ts.tzinfo else pred.ts.replace(tzinfo=timezone.utc)
            context = (
                session.query(RF)
                .filter(RF.market_id == pred.market_id, RF.ts <= pred_ts)
                .order_by(RF.ts.desc())
                .limit(40)
                .all()
            )
            market = session.get(Market, pred.market_id)
            minutes_to_close = 7.5
            if market and market.close_time:
                ct = market.close_time if market.close_time.tzinfo else market.close_time.replace(tzinfo=timezone.utc)
                minutes_to_close = max(0.0, (ct - pred_ts).total_seconds() / 60)
            result = build_feature_vector(context, minutes_to_close=minutes_to_close, ts=pred_ts)
            if result is None:
                skipped += 1
                continue
            vec, _ = result
            rows.append({
                "ts": pred.ts.isoformat(),
                "direction": pred.direction,
                "confidence": float(pred.confidence),
                "actual_outcome": int(pred.actual_outcome),
                "features": {name: float(val) for name, val in zip(FEATURE_NAMES, vec)},
            })

        return {"rows": rows, "total": total, "page": page, "page_size": page_size, "skipped": skipped}

    @app.get("/data/stats")
    def get_data_stats(
        series_ticker: str,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        session: Session = Depends(_get_db),
    ):
        import numpy as np
        from shared.feature_builder import FEATURE_NAMES

        X, _ = _get_feature_matrix(session, series_ticker, from_ts, to_ts)
        if X is None:
            return []
        return [
            {
                "feature": name,
                "mean": round(float(np.mean(X[:, i])), 6),
                "std": round(float(np.std(X[:, i])), 6),
                "min": round(float(np.min(X[:, i])), 6),
                "max": round(float(np.max(X[:, i])), 6),
                "null_count": 0,
            }
            for i, name in enumerate(FEATURE_NAMES)
        ]

    @app.get("/data/correlations")
    def get_data_correlations(
        series_ticker: str,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        session: Session = Depends(_get_db),
    ):
        import numpy as np
        from shared.feature_builder import FEATURE_NAMES

        X, y = _get_feature_matrix(session, series_ticker, from_ts, to_ts)
        if X is None:
            return []
        results = []
        for i, name in enumerate(FEATURE_NAMES):
            col = X[:, i]
            r = float(np.corrcoef(col, y)[0, 1]) if np.std(col) > 0 else 0.0
            results.append({"feature": name, "r": round(r, 4)})
        return sorted(results, key=lambda x: abs(x["r"]), reverse=True)

    @app.get("/stats/training")
    def get_stats_training(session: Session = Depends(_get_db)):
        from shared.orm import ModelRegistry
        from sqlalchemy import func
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        cutoff_24h = now - timedelta(hours=24)

        last_trained = session.query(func.max(ModelRegistry.trained_at)).scalar()

        active_models = (
            session.query(func.count(ModelRegistry.id))
            .filter(ModelRegistry.is_active == True)  # noqa: E712
            .scalar()
        ) or 0

        settled_24h = (
            session.query(func.count(Prediction.id))
            .filter(
                Prediction.actual_outcome != None,  # noqa: E711
                Prediction.settled_at >= cutoff_24h,
            )
            .scalar()
        ) or 0

        active_market_ids = [
            r[0]
            for r in session.query(Market.market_id)
            .filter(Market.status == "active")
            .all()
        ]
        modeled_ids = {
            r[0]
            for r in session.query(ModelRegistry.market_id)
            .filter(ModelRegistry.is_active == True)  # noqa: E712
            .all()
        }
        unmodeled = sum(1 for mid in active_market_ids if mid not in modeled_ids)

        return {
            "last_trained_at": last_trained.isoformat() if last_trained else None,
            "active_models": active_models,
            "settled_last_24h": settled_24h,
            "unmodeled_markets": unmodeled,
        }

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
