"""
Alert deduplication, persisted to the database instead of an in-memory
dict. The original in-memory version lost all suppression state on every
restart -- meaning a restart right after a real incident could cause an
immediate duplicate-alert storm, exactly when that's least wanted. Keyed
by (rule_id, source, component, normalized message) same as before, just
durable now.

The same table also carries an alert lifecycle (firing/acknowledged/
resolved) -- see AlertDedupStateModel's docstring for why it lives here
rather than a separate table.
"""
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.alerts.models import AlertDedupStateModel
from backend.core.store import Base

logger = logging.getLogger("logtool.alerts.state")

_LIFECYCLE_COLUMNS = (
    ("status", "VARCHAR DEFAULT 'firing'"),
    ("rule_id", "VARCHAR"),
    ("rule_name", "VARCHAR"),
    ("source_system", "VARCHAR"),
    ("component", "VARCHAR"),
    ("message_snippet", "VARCHAR"),
    ("correlation_field", "VARCHAR"),
    ("correlation_value", "VARCHAR"),
    ("first_fired_at", "VARCHAR"),
    ("acknowledged_at", "VARCHAR"),
    ("acknowledged_by_user_id", "VARCHAR"),
    ("resolved_at", "VARCHAR"),
    ("resolved_by_user_id", "VARCHAR"),
)


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
        self._ensure_lifecycle_columns()
        self.Session = sessionmaker(bind=self.engine)

    def _ensure_lifecycle_columns(self) -> None:
        """Base.metadata.create_all() only creates missing TABLES, not
        missing COLUMNS on tables that already exist -- a database created
        before the firing/acknowledged/resolved lifecycle existed needs a
        one-time ALTER TABLE. Same pattern as rule_manager.py's
        _ensure_notification_columns() / rules.py's
        _ensure_correlation_columns()."""
        with self.engine.connect() as conn:
            cols = [row[1] for row in conn.execute(text("PRAGMA table_info(alert_dedup_state)")).fetchall()]
            if cols and "status" not in cols:
                for name, ddl_type in _LIFECYCLE_COLUMNS:
                    conn.execute(text(f"ALTER TABLE alert_dedup_state ADD COLUMN {name} {ddl_type}"))
                conn.commit()
                logger.info("Migrated alert_dedup_state table: added alert lifecycle columns.")

    def should_fire_alert(
        self,
        rule_id: str,
        source: Optional[str],
        component: Optional[str],
        normalized_message: str,
        window_minutes: int,
        rule_name: Optional[str] = None,
        correlation_field: Optional[str] = None,
        correlation_value: Optional[str] = None,
    ) -> bool:
        """Returns True if the alert should fire, False if suppressed as a
        duplicate within the given rule's suppression window. When it does
        fire, also upserts this signature's active-alert record (context +
        lifecycle) -- a signature previously 'resolved' reopens to
        'firing' on recurrence, matching how PagerDuty/Opsgenie reopen a
        resolved incident rather than silently staying resolved while
        still actively occurring."""
        key = _make_key(rule_id, source, component, normalized_message)
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        session = self.Session()
        try:
            record = session.query(AlertDedupStateModel).filter_by(dedup_key=key).first()
            if record:
                last_fired = datetime.fromisoformat(record.last_fired_at)
                if (now - last_fired) < timedelta(minutes=window_minutes):
                    logger.info(f"Alert suppressed as duplicate (rule={rule_id}, key={key[:12]}...)")
                    return False
                record.last_fired_at = now_iso
                if record.status == "resolved":
                    record.status = "firing"
                    record.resolved_at = None
                    record.resolved_by_user_id = None
            else:
                record = AlertDedupStateModel(
                    dedup_key=key, last_fired_at=now_iso, status="firing", first_fired_at=now_iso,
                )
                session.add(record)

            record.rule_id = rule_id
            record.rule_name = rule_name
            record.source_system = source
            record.component = component
            record.message_snippet = (normalized_message or "")[:200]
            record.correlation_field = correlation_field
            record.correlation_value = correlation_value

            session.commit()
            return True
        finally:
            session.close()

    def list_active_alerts(self, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        session = self.Session()
        try:
            query = session.query(AlertDedupStateModel)
            if status_filter:
                query = query.filter_by(status=status_filter)
            else:
                query = query.filter(AlertDedupStateModel.status != "resolved")
            rows = query.order_by(AlertDedupStateModel.last_fired_at.desc()).all()
            return [self._to_dict(r) for r in rows]
        finally:
            session.close()

    def acknowledge(self, dedup_key: str, user_id: Optional[str]) -> Dict[str, Any]:
        return self._transition(dedup_key, status="acknowledged", user_id=user_id, ts_field="acknowledged_at", user_field="acknowledged_by_user_id")

    def resolve(self, dedup_key: str, user_id: Optional[str]) -> Dict[str, Any]:
        return self._transition(dedup_key, status="resolved", user_id=user_id, ts_field="resolved_at", user_field="resolved_by_user_id")

    def _transition(self, dedup_key: str, status: str, user_id: Optional[str], ts_field: str, user_field: str) -> Dict[str, Any]:
        session = self.Session()
        try:
            record = session.query(AlertDedupStateModel).filter_by(dedup_key=dedup_key).first()
            if not record:
                raise ActiveAlertNotFoundError(dedup_key)
            record.status = status
            setattr(record, ts_field, datetime.now(timezone.utc).isoformat())
            setattr(record, user_field, user_id)
            session.commit()
            return self._to_dict(record)
        finally:
            session.close()

    def _to_dict(self, r: AlertDedupStateModel) -> Dict[str, Any]:
        return {
            "dedup_key": r.dedup_key,
            "status": r.status or "firing",
            "rule_id": r.rule_id,
            "rule_name": r.rule_name,
            "source_system": r.source_system,
            "component": r.component,
            "message_snippet": r.message_snippet,
            "correlation_field": r.correlation_field,
            "correlation_value": r.correlation_value,
            "last_fired_at": r.last_fired_at,
            "first_fired_at": r.first_fired_at or r.last_fired_at,
            "acknowledged_at": r.acknowledged_at,
            "acknowledged_by_user_id": r.acknowledged_by_user_id,
            "resolved_at": r.resolved_at,
            "resolved_by_user_id": r.resolved_by_user_id,
        }

    def clear_state(self) -> None:
        session = self.Session()
        try:
            session.query(AlertDedupStateModel).delete()
            session.commit()
        finally:
            session.close()


class ActiveAlertNotFoundError(Exception):
    pass
