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
from shared.orm import ModelRegistry
from trainer.dataset import build_training_dataset

logger = logging.getLogger(__name__)


def train_and_promote(
    session: Session,
    market_id: str,
    lookback_hours: int,
    models_dir: str,
) -> dict | None:
    result = build_training_dataset(session, market_id, lookback_hours)
    if result is None:
        logger.info("No training data for %s", market_id)
        return None

    X, y = result
    if len(np.unique(y)) < 2:
        logger.warning("Only one class in training data for %s — skipping", market_id)
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
    logger.info("Trained %s: brier=%.4f on %d rows", market_id, new_brier, len(X))

    current_active = (
        session.query(ModelRegistry)
        .filter(ModelRegistry.market_id == market_id, ModelRegistry.is_active == True)
        .first()
    )
    current_brier = float(current_active.brier_score) if current_active else float("inf")

    version_num = (
        (int(current_active.version.lstrip("v")) + 1) if current_active else 1
    )
    version = f"v{version_num}"
    artifact_path = os.path.join(models_dir, f"{market_id.replace('/', '_')}_{version}.joblib")
    joblib.dump(model, artifact_path)

    promoted = new_brier < current_brier
    if promoted:
        if current_active:
            current_active.is_active = False
        new_reg = ModelRegistry(
            market_id=market_id,
            version=version,
            trained_at=datetime.now(timezone.utc),
            training_rows=len(X),
            brier_score=new_brier,
            artifact_path=artifact_path,
            is_active=True,
        )
        session.add(new_reg)
        logger.info("Promoted %s %s (brier %.4f < %.4f)", market_id, version, new_brier, current_brier)
    else:
        new_reg = ModelRegistry(
            market_id=market_id,
            version=version,
            trained_at=datetime.now(timezone.utc),
            training_rows=len(X),
            brier_score=new_brier,
            artifact_path=artifact_path,
            is_active=False,
        )
        session.add(new_reg)
        logger.info("Rejected %s %s (brier %.4f >= %.4f)", market_id, version, new_brier, current_brier)

    session.flush()
    return {"market_id": market_id, "version": version, "brier_score": new_brier, "promoted": promoted}
