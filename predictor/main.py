# predictor/main.py
import logging
import threading
import time

from fastapi import FastAPI
import uvicorn

from shared.db import make_session_factory, session_scope
from shared.orm import Market, ModelRegistry
from shared.settings import Settings
from predictor.model_loader import ModelLoader
from predictor.inference import run_inference

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

settings = Settings()
session_factory = make_session_factory(settings)

_INFERENCE_INTERVAL = 60
_RELOAD_INTERVAL = 300
_last_reload = 0.0

app = FastAPI()


@app.get("/health")
def health():
    with session_scope(session_factory) as session:
        active = session.query(Market).filter(Market.status == "active").count()
        models = session.query(ModelRegistry).filter(ModelRegistry.is_active == True).count()
    return {"status": "ok", "active_markets": active, "active_models": models}


def _inference_loop():
    global _last_reload
    with session_scope(session_factory) as session:
        loader = ModelLoader(session, models_dir="models")
        while True:
            if time.time() - _last_reload > _RELOAD_INTERVAL:
                loader.reload_all()
                _last_reload = time.time()
            active_markets = (
                session.query(Market).filter(Market.status == "active").all()
            )
            for market in active_markets:
                try:
                    run_inference(session, market, loader)
                except Exception as exc:
                    logger.error("Inference failed for %s: %s", market.market_id, exc)
                    session.rollback()
            session.commit()
            time.sleep(_INFERENCE_INTERVAL)


if __name__ == "__main__":
    t = threading.Thread(target=_inference_loop, daemon=True)
    t.start()
    uvicorn.run(app, host="0.0.0.0", port=8002)
