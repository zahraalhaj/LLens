"""
Daily retention-purge tick. Same register_<name>_job(scheduler, ...)
pattern as backend/remote/scheduler.py and
backend/analysis/vplus_alerting.py -- adds onto the one shared
BackgroundScheduler rather than starting a second one.

Disabled by default (settings.retention_days is None/0): an existing
install upgrading into this feature must never start silently deleting
data just because the code now exists.
"""
import logging

from backend.api.config import Settings
from backend.core.store import DatabaseManager

logger = logging.getLogger("logtool.core.retention")

TICK_INTERVAL_HOURS = 24


def _tick(db_manager: DatabaseManager, settings: Settings) -> None:
    days = settings.retention_days
    if not days:
        return
    try:
        db_manager.purge_batches_older_than(days)
    except Exception:
        logger.exception("Retention purge failed")


def register_retention_purge_job(scheduler, db_manager: DatabaseManager, settings: Settings) -> None:
    scheduler.add_job(
        _tick,
        "interval",
        hours=TICK_INTERVAL_HOURS,
        args=[db_manager, settings],
        id="retention_purge_tick",
        max_instances=1,
        coalesce=True,
    )
    logger.info(f"Retention purge job registered (check every {TICK_INTERVAL_HOURS}h)")
