"""
Proactive "V+ is down" / "V+ recovered" alerting.

The existing alert engine (backend/alerts/) only evaluates rules against
events that WERE ingested -- it has no way to express "alert when nothing
arrives," which is exactly what availability monitoring needs. This module
is a separate, small mechanism for that one job: a periodic check (wired
into the same scheduler already running for Control Center) that persists
its own up/down state and only sends an email on a state TRANSITION, not
on every tick -- otherwise an hours-long outage would generate one email
per tick for its entire duration.

Deliberately reuses EmailDispatcher (no new sending mechanism) and follows
the same "short-lived sessions, don't hold one open across a call into a
different engine" discipline established after the concurrency bug found
in remote/service.py earlier in this project.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.alerts.email import EmailDispatcher
from backend.analysis.models import ServiceAvailabilityStateModel
from backend.analysis.vplus_monitoring import DEFAULT_SOURCE_SYSTEM, compute_vplus_availability
from backend.core.store import Base, DatabaseManager

logger = logging.getLogger("logtool.analysis.vplus_alerting")

DEFAULT_SERVICE_NAME = "vplus_stepup"
DEFAULT_LOOKBACK_HOURS = 24
CHECK_INTERVAL_SECONDS = 300  # every 5 minutes -- frequent enough to catch an outage promptly
                                # without hammering the DB on every remote-machine-poll tick (60s)


class VPlusAvailabilityMonitor:
    def __init__(
        self,
        db_path: str,
        db_manager: DatabaseManager,
        email_dispatcher: EmailDispatcher,
        source_system: str = DEFAULT_SOURCE_SYSTEM,
        service_name: str = DEFAULT_SERVICE_NAME,
        gap_threshold_minutes: int = 10,
        lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    ):
        self.db_manager = db_manager
        self.email_dispatcher = email_dispatcher
        self.source_system = source_system
        self.service_name = service_name
        self.gap_threshold_minutes = gap_threshold_minutes
        self.lookback_hours = lookback_hours

        self.engine = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30.0}, echo=False
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def check_and_alert(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        date_from = (now - timedelta(hours=self.lookback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

        events = self.db_manager.get_events_for_analysis(self.source_system, date_from=date_from)
        report = compute_vplus_availability(events, self.gap_threshold_minutes, reference_now=now)

        # "no_data" (nothing at all in the lookback window) is treated the
        # same as "down" for alerting purposes -- the requirement is
        # explicit: "if no data is received, this must generate an alert."
        now_down = report["status"] in ("down", "no_data")

        state = self._get_state()
        was_down = bool(state.is_down) if state else False
        transitioned = False

        if now_down and not was_down:
            down_since = report.get("window_end") or now.strftime("%Y-%m-%dT%H:%M:%SZ")
            self._send_alert(
                subject="[V+ ALERT] V+ / StepUp service appears DOWN",
                body=(
                    f"No V+/StepUp activity detected since {down_since}.\n\n"
                    f"{report.get('message', '')}\n"
                    f"Gap threshold: {self.gap_threshold_minutes} minutes.\n"
                ),
            )
            self._set_state(is_down=True, down_since=down_since, checked_at=now)
            transitioned = True

        elif not now_down and was_down:
            down_since = state.down_since if state else None
            duration_minutes = None
            if down_since:
                try:
                    down_dt = datetime.fromisoformat(down_since.replace("Z", "+00:00"))
                    duration_minutes = round((now - down_dt).total_seconds() / 60, 1)
                except ValueError:
                    pass
            self._send_alert(
                subject="[V+ RECOVERED] V+ / StepUp service back to normal",
                body=(
                    f"V+/StepUp activity has resumed.\n\n"
                    f"Was down since: {down_since or 'unknown'}\n"
                    f"Recovered at: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
                    + (f"Total downtime: {duration_minutes} minutes\n" if duration_minutes is not None else "")
                ),
            )
            self._set_state(is_down=False, down_since=None, checked_at=now)
            transitioned = True

        else:
            self._touch_checked_at(now)

        return {"report": report, "now_down": now_down, "transitioned": transitioned}

    def _send_alert(self, subject: str, body: str) -> None:
        try:
            success, status_msg = self.email_dispatcher.send_alert_email(subject=subject, body_text=body)
            logger.info(f"V+ availability alert dispatched: {subject} (success={success}, status={status_msg})")
        except Exception:
            logger.exception("Failed to send V+ availability alert email")

    def _get_state(self) -> Optional[ServiceAvailabilityStateModel]:
        session = self.Session()
        try:
            return session.query(ServiceAvailabilityStateModel).filter_by(service_name=self.service_name).first()
        finally:
            session.close()

    def _set_state(self, is_down: bool, down_since: Optional[str], checked_at: datetime) -> None:
        session = self.Session()
        try:
            state = session.query(ServiceAvailabilityStateModel).filter_by(service_name=self.service_name).first()
            checked_str = checked_at.strftime("%Y-%m-%dT%H:%M:%SZ")
            if state:
                state.is_down = 1 if is_down else 0
                state.down_since = down_since
                state.last_checked_at = checked_str
                state.last_alert_sent_at = checked_str
            else:
                session.add(
                    ServiceAvailabilityStateModel(
                        service_name=self.service_name,
                        is_down=1 if is_down else 0,
                        down_since=down_since,
                        last_checked_at=checked_str,
                        last_alert_sent_at=checked_str,
                    )
                )
            session.commit()
        finally:
            session.close()

    def _touch_checked_at(self, checked_at: datetime) -> None:
        session = self.Session()
        try:
            state = session.query(ServiceAvailabilityStateModel).filter_by(service_name=self.service_name).first()
            checked_str = checked_at.strftime("%Y-%m-%dT%H:%M:%SZ")
            if state:
                state.last_checked_at = checked_str
            else:
                session.add(
                    ServiceAvailabilityStateModel(
                        service_name=self.service_name, is_down=0, down_since=None,
                        last_checked_at=checked_str, last_alert_sent_at=None,
                    )
                )
            session.commit()
        finally:
            session.close()


def _tick(monitor: VPlusAvailabilityMonitor) -> None:
    try:
        monitor.check_and_alert()
    except Exception:
        logger.exception("V+ availability check failed")


def register_vplus_availability_job(scheduler, monitor: VPlusAvailabilityMonitor) -> None:
    """Adds the periodic availability check onto an already-running
    scheduler (the same BackgroundScheduler instance used for Control
    Center polling) rather than spinning up a second scheduler."""
    scheduler.add_job(
        _tick,
        "interval",
        seconds=CHECK_INTERVAL_SECONDS,
        args=[monitor],
        id="vplus_availability_check",
        max_instances=1,
        coalesce=True,
    )
    logger.info(f"V+ availability monitor registered (check every {CHECK_INTERVAL_SECONDS}s)")
