import logging
import time
from shared.db import make_session_factory, session_scope
from shared.orm import Market
from shared.settings import Settings
from trainer.backfill import backfill_outcomes
from trainer.train import train_and_promote

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def run_training_campaign(settings: Settings, session_factory) -> None:
    with session_scope(session_factory) as session:
        backfilled = backfill_outcomes(session)
        logger.info("Backfilled %d outcomes before training", backfilled)

    with session_scope(session_factory) as session:
        markets = session.query(Market).filter(Market.status == "active").all()
        market_ids = [m.market_id for m in markets]

    logger.info("Starting training campaign for %d markets", len(market_ids))
    for market_id in market_ids:
        with session_scope(session_factory) as session:
            result = train_and_promote(
                session=session,
                market_id=market_id,
                lookback_hours=settings.training_campaign_lookback_hours,
                models_dir="models",
            )
            if result:
                logger.info("Market %s: %s", market_id, result)
        time.sleep(0.1)


if __name__ == "__main__":
    settings = Settings()
    session_factory = make_session_factory(settings)
    logger.info(
        "Trainer daemon starting — cooldown=%ds", settings.training_campaign_cooldown_seconds
    )
    while True:
        try:
            run_training_campaign(settings, session_factory)
            logger.info(
                "Campaign done — sleeping %ds", settings.training_campaign_cooldown_seconds
            )
        except Exception as exc:
            logger.error("Training campaign failed: %s — retrying after cooldown", exc)
        time.sleep(settings.training_campaign_cooldown_seconds)
