# trainer/train.py
import logging
import os
from datetime import datetime, timezone
import joblib
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.metrics import brier_score_loss
import xgboost as xgb
from sqlalchemy.orm import Session
from shared.orm import Market, ModelRegistry
from trainer.dataset import build_training_dataset

logger = logging.getLogger(__name__)


def _ensure_sentinel_market(session: Session, series_ticker: str) -> None:
    """Create a status='series' Market row for the series ticker if absent.

    model_registry.market_id has a FK to markets.market_id.  Storing the model
    under the series ticker (e.g. 'KXBTC15M') requires a Markets row with that
    market_id.  These rows are never treated as active trading markets.
    """
    if session.get(Market, series_ticker) is None:
        now = datetime.now(timezone.utc)
        session.add(Market(
            market_id=series_ticker,
            ticker=series_ticker,
            title=f"{series_ticker} series",
            status="series",
            discovered_at=now,
            updated_at=now,
        ))
        session.flush()


def train_and_promote(
    session: Session,
    series_ticker: str,
    lookback_hours: int,
    models_dir: str,
) -> dict | None:
    result = build_training_dataset(session, series_ticker, lookback_hours)
    if result is None:
        logger.info("No training data for series %s", series_ticker)
        return None

    X, y = result
    if len(np.unique(y)) < 2:
        logger.warning("Only one class in training data for %s — skipping", series_ticker)
        return None

    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, shuffle=False)

    model = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=4,
        learning_rate=0.1,
        eval_metric="logloss",
        verbosity=0,
    )
    model.fit(X_train, y_train)

    y_prob = model.predict_proba(X_val)[:, 1]
    new_brier = float(brier_score_loss(y_val, y_prob))
    logger.info("Trained %s: brier=%.4f on %d rows", series_ticker, new_brier, len(X))

    _ensure_sentinel_market(session, series_ticker)

    current_active = (
        session.query(ModelRegistry)
        .filter(ModelRegistry.market_id == series_ticker, ModelRegistry.is_active == True)
        .first()
    )
    current_brier = float(current_active.brier_score) if current_active else float("inf")

    version_num = (int(current_active.version.lstrip("v")) + 1) if current_active else 1
    version = f"v{version_num}"
    artifact_path = os.path.join(models_dir, f"{series_ticker}_{version}.joblib")
    joblib.dump(model, artifact_path)

    promoted = new_brier < current_brier
    if promoted and current_active:
        current_active.is_active = False

    session.add(ModelRegistry(
        market_id=series_ticker,
        version=version,
        trained_at=datetime.now(timezone.utc),
        training_rows=len(X),
        brier_score=new_brier,
        artifact_path=artifact_path,
        is_active=promoted,
    ))
    session.flush()

    action = "Promoted" if promoted else "Rejected"
    logger.info("%s %s %s (brier %.4f vs current %.4f)", action, series_ticker, version, new_brier, current_brier)
    return {
        "series_ticker": series_ticker,
        "version": version,
        "brier_score": new_brier,
        "promoted": promoted,
    }
