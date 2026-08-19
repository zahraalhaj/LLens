"""
Alert deduplication, persisted to the database instead of an in-memory
dict. The original in-memory version lost all suppression state on every
restart -- meaning a restart right after a real incident could cause an
immediate duplicate-alert storm, exactly when that's least wanted. Keyed
by (rule_id, source, component, normalized message) same as before, just
durable now.
"""
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.alerts.models import AlertDedupStateModel
from backend.core.store import Base

logger = logging.getLogger("logtool.alerts.state")


def _make_key(rule_id: str, source: Optional[str], component: Optional[str], normalized_message: str) -> str:
    raw = "|".join(
        [
            rule_id,
            (source or "unknown").lower(),
            (component or "none").lower(),
            normalized_message.strip()[:100].lower(),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AlertDeduplicationEngine:
    def __init__(self, db_path: str):
        self.engine = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30.0}, echo=False
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def should_fire_alert(
        self,
        rule_id: str,
        source: Optional[str],
        component: Optional[str],
        normalized_message: str,
        window_minutes: int,
    ) -> bool:
        """Returns True if the alert should fire, False if suppressed as a
        duplicate within the given rule's suppression window."""
        key = _make_key(rule_id, source, component, normalized_message)
        now = datetime.now(timezone.utc)

        session = self.Session()
        try:
            record = session.query(AlertDedupStateModel).filter_by(dedup_key=key).first()
            if record:
                last_fired = datetime.fromisoformat(record.last_fired_at)
                if (now - last_fired) < timedelta(minutes=window_minutes):
                    logger.info(f"Alert suppressed as duplicate (rule={rule_id}, key={key[:12]}...)")
                    return False
                record.last_fired_at = now.isoformat()
            else:
                session.add(AlertDedupStateModel(dedup_key=key, last_fired_at=now.isoformat()))
            session.commit()
            return True
        finally:
            session.close()

    def clear_state(self) -> None:
        session = self.Session()
        try:
            session.query(AlertDedupStateModel).delete()
            session.commit()
        finally:
            session.close()
