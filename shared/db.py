# shared/db.py
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from shared.settings import Settings


def make_engine(settings: Settings):
    return create_engine(settings.db_url, pool_pre_ping=True)


def make_session_factory(settings: Settings):
    return sessionmaker(bind=make_engine(settings), autocommit=False, autoflush=False)


@contextmanager
def session_scope(session_factory) -> Session:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
