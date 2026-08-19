"""
Runs one recurring tick (default every 60s) that asks RemoteMachineService
which enabled machines are due for a poll (based on their own
poll_interval_minutes) and polls each one. Deliberately not one APScheduler
job per machine -- a single tick that re-reads the DB each time is simpler
and naturally picks up machines added/edited/deleted at runtime without any
job-management bookkeeping.
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from backend.remote.service import RemoteMachineService

logger = logging.getLogger("logtool.remote.scheduler")

TICK_INTERVAL_SECONDS = 60


def _tick(service: RemoteMachineService) -> None:
    try:
        due = service.enabled_machines_due_for_poll()
    except Exception:
        logger.exception("Failed to determine which remote machines are due for polling")
        return

    for machine_id in due:
        try:
            service.poll_machine(machine_id)
        except Exception:
            logger.exception(f"Unhandled error polling machine {machine_id}")


def start_scheduler(service: RemoteMachineService) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _tick,
        "interval",
        seconds=TICK_INTERVAL_SECONDS,
        args=[service],
        id="remote_machine_poll_tick",
        max_instances=1,  # don't overlap ticks if a poll cycle runs long
        coalesce=True,
    )
    scheduler.start()
    logger.info(f"Remote machine poll scheduler started (tick every {TICK_INTERVAL_SECONDS}s)")
    return scheduler
