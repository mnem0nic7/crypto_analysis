from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy.orm import sessionmaker

from shared.orm import Market
from trainer import main as trainer_main


def _settings():
    return SimpleNamespace(training_campaign_lookback_hours=2160)


def test_campaign_lock_is_noop_for_sqlite(db_session):
    assert trainer_main._try_acquire_campaign_lock(db_session) is True
    trainer_main._release_campaign_lock(db_session)


def test_training_campaign_skips_when_lock_is_held(test_engine, monkeypatch):
    Session = sessionmaker(bind=test_engine)
    monkeypatch.setattr(trainer_main, "_try_acquire_campaign_lock", lambda session: False)

    def fail_if_called(session):
        raise AssertionError("backfill should not run without the campaign lock")

    monkeypatch.setattr(trainer_main, "backfill_outcomes", fail_if_called)

    trainer_main.run_training_campaign(_settings(), Session)


def test_training_campaign_releases_lock_after_run(test_engine, monkeypatch):
    Session = sessionmaker(bind=test_engine)
    session = Session()
    now = datetime.now(timezone.utc)
    session.add(Market(
        market_id="KXLOCK-00",
        ticker="KXLOCK",
        status="settled",
        discovered_at=now,
        updated_at=now,
    ))
    session.commit()
    session.close()

    released = {"value": False}
    monkeypatch.setattr(trainer_main, "_try_acquire_campaign_lock", lambda session: True)
    monkeypatch.setattr(trainer_main, "_release_campaign_lock", lambda session: released.__setitem__("value", True))
    monkeypatch.setattr(trainer_main, "backfill_outcomes", lambda session: 0)
    monkeypatch.setattr(trainer_main, "train_and_promote", lambda **kwargs: None)
    monkeypatch.setattr(trainer_main.time, "sleep", lambda seconds: None)

    trainer_main.run_training_campaign(_settings(), Session)

    assert released["value"] is True
