# trainer/main.py
import logging
import time
from sqlalchemy import text
from shared.db import make_session_factory, session_scope
from shared.orm import Market
from shared.settings import Settings
from trainer.backfill import backfill_outcomes
from trainer.train import train_and_promote

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)
_TRAINING_CAMPAIGN_LOCK_KEYS = (1129470288, 1414676809)  # "CRYP", "TRAI"


def _try_acquire_campaign_lock(session) -> bool:
    if session.get_bind().dialect.name != "postgresql":
        return True

    key1, key2 = _TRAINING_CAMPAIGN_LOCK_KEYS
    return bool(session.execute(
        text("SELECT pg_try_advisory_lock(:key1, :key2)"),
        {"key1": key1, "key2": key2},
    ).scalar())


def _release_campaign_lock(session) -> None:
    if session.get_bind().dialect.name != "postgresql":
        return

    key1, key2 = _TRAINING_CAMPAIGN_LOCK_KEYS
    session.execute(
        text("SELECT pg_advisory_unlock(:key1, :key2)"),
        {"key1": key1, "key2": key2},
    )


def run_training_campaign(settings: Settings, session_factory) -> None:
    with session_scope(session_factory) as lock_session:
        if not _try_acquire_campaign_lock(lock_session):
            logger.info("Training campaign already running — skipping this cycle")
            return
        try:
            _run_training_campaign_unlocked(settings, session_factory)
        finally:
            _release_campaign_lock(lock_session)


def _run_training_campaign_unlocked(settings: Settings, session_factory) -> None:
    with session_scope(session_factory) as session:
        backfilled = backfill_outcomes(session)
        logger.info("Backfilled %d outcomes before training", backfilled)

    with session_scope(session_factory) as session:
        series_tickers = [
            row[0]
            for row in session.query(Market.ticker).filter(Market.status != "series").distinct().all()
        ]

    logger.info("Starting training campaign for %d series", len(series_tickers))
    for series_ticker in series_tickers:
        with session_scope(session_factory) as session:
            result = train_and_promote(
                session=session,
                series_ticker=series_ticker,
                lookback_hours=settings.training_campaign_lookback_hours,
                models_dir="models",
            )
            if result:
                logger.info("Series %s: %s", series_ticker, result)
        time.sleep(0.1)


if __name__ == "__main__":
    settings = Settings()
    session_factory = make_session_factory(settings)
    logger.info("Trainer daemon starting — cooldown=%ds", settings.training_campaign_cooldown_seconds)
    while True:
        try:
            run_training_campaign(settings, session_factory)
            logger.info("Campaign done — sleeping %ds", settings.training_campaign_cooldown_seconds)
        except Exception as exc:
            logger.error("Training campaign failed: %s — retrying after cooldown", exc)
        time.sleep(settings.training_campaign_cooldown_seconds)
