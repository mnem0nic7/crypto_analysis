# predictor/model_loader.py
import logging
import joblib
from sqlalchemy.orm import Session
from shared.orm import ModelRegistry

logger = logging.getLogger(__name__)


class ModelLoader:
    def __init__(self, session: Session, models_dir: str):
        self._session = session
        self._models_dir = models_dir
        self._cache: dict[str, object] = {}
        self._version_cache: dict[str, str] = {}

    def get_model(self, market_id: str):
        reg = (
            self._session.query(ModelRegistry)
            .filter(ModelRegistry.market_id == market_id, ModelRegistry.is_active == True)
            .first()
        )
        if reg is None:
            return None
        cached_version = self._version_cache.get(market_id)
        if cached_version != reg.version:
            try:
                model = joblib.load(reg.artifact_path)
                self._cache[market_id] = model
                self._version_cache[market_id] = reg.version
                logger.info("Loaded model %s v%s", market_id, reg.version)
            except Exception as exc:
                logger.error("Failed to load model for %s: %s", market_id, exc)
                return None
        return self._cache.get(market_id)

    def reload_all(self) -> None:
        active_regs = (
            self._session.query(ModelRegistry)
            .filter(ModelRegistry.is_active == True)
            .all()
        )
        for reg in active_regs:
            cached = self._version_cache.get(reg.market_id)
            if cached != reg.version:
                try:
                    model = joblib.load(reg.artifact_path)
                    self._cache[reg.market_id] = model
                    self._version_cache[reg.market_id] = reg.version
                    logger.info("Hot-reloaded model %s v%s", reg.market_id, reg.version)
                except Exception as exc:
                    logger.error("Hot-reload failed for %s: %s", reg.market_id, exc)
