# analysis/main.py
from datetime import datetime, timezone
import logging
from contextlib import contextmanager
from shared.orm import SweepRun
from shared.db import session_scope as _session_scope_impl, make_session_factory
from shared.settings import Settings
from analysis.sweep import execute_sweep

logger = logging.getLogger(__name__)


@contextmanager
def session_scope():
    """Session scope context manager. Can be patched in tests."""
    settings = Settings()
    factory = make_session_factory(settings)
    with _session_scope_impl(factory) as session:
        yield session


def run_once(fee_bps: int = 50) -> SweepRun:
    """
    Create a sweep run record, run the sweep, return the completed run.
    The run status is set to 'running' at start, 'complete' or 'failed' at end.
    All DB writes are committed in a single transaction.
    """
    exception_occurred = None
    run = None
    with session_scope() as session:
        run = SweepRun(
            run_at=datetime.now(timezone.utc),
            status="running",
            fee_bps=fee_bps,
        )
        session.add(run)
        session.flush()  # get run.id
        try:
            execute_sweep(session, run)
            run.status = "complete"
        except Exception as e:
            run.status = "failed"
            logger.exception("Sweep run %s failed", run.id)
            exception_occurred = e

    # If an exception occurred, re-raise it after the transaction is committed
    if exception_occurred is not None:
        raise exception_occurred

    return run


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run = run_once()
    print(f"Run {run.id}: status={run.status}, n_predictions={run.n_settled_predictions}, "
          f"best_pnl={run.best_net_pnl_dollars}, elapsed={run.elapsed_seconds:.1f}s")
