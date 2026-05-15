# tests/test_analysis_main.py
import pytest
from unittest.mock import patch
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shared.orm import Base, SweepRun
from analysis.main import run_once


@pytest.fixture
def test_engine():
    """Create in-memory SQLite test database."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def test_session_factory(test_engine):
    """Create a session factory bound to the test database."""
    Session = sessionmaker(bind=test_engine, expire_on_commit=False)
    return Session


def test_run_once_creates_run_record(test_session_factory):
    """Test that run_once() creates a SweepRun record with correct initial status."""

    @contextmanager
    def mock_session_scope():
        """Mock session_scope that yields a test session."""
        session = test_session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    with patch("analysis.main.session_scope", mock_session_scope):
        # Mock execute_sweep to do nothing
        with patch("analysis.main.execute_sweep"):
            run = run_once()
            # Capture the ID before the session closes
            run_id = run.id
            run_status = run.status
            run_fee = run.fee_bps

    # Verify the run was created and returned
    assert run is not None
    assert isinstance(run, SweepRun)
    assert run_id is not None
    assert run_status == "complete"
    assert run_fee == 50

    # Query the database to verify the record was persisted
    session = test_session_factory()
    persisted_run = session.query(SweepRun).filter_by(id=run_id).first()
    session.close()

    assert persisted_run is not None
    assert persisted_run.status == "complete"
    assert persisted_run.fee_bps == 50


def test_run_once_sets_failed_on_exception(test_session_factory):
    """Test that run_once() sets status to 'failed' when execute_sweep raises an exception."""

    @contextmanager
    def mock_session_scope():
        """Mock session_scope that yields a test session."""
        session = test_session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    with patch("analysis.main.session_scope", mock_session_scope):
        # Mock execute_sweep to raise an exception
        with patch("analysis.main.execute_sweep") as mock_sweep:
            mock_sweep.side_effect = RuntimeError("boom")

            # Verify that the exception is raised
            with pytest.raises(RuntimeError, match="boom"):
                run_once()

    # Query the database to verify the run record has status='failed'
    session = test_session_factory()
    failed_run = session.query(SweepRun).filter_by(status="failed").first()
    session.close()

    assert failed_run is not None
    assert failed_run.status == "failed"
    assert failed_run.fee_bps == 50
