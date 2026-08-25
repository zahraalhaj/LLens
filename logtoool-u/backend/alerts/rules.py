"""
Alert Rules Evaluation Engine.

Evaluates ingested log events against every enabled rule from
AlertRuleManager (configurable, DB-stored) instead of two hardcoded rules.
Every dispatch attempt (success or failure) is persisted to
alert_dispatch_log -- addresses the "no dispatch history" gap from the
original audit.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.alerts.email import EmailDispatcher
from backend.alerts.models import AlertDispatchLogModel
from backend.alerts.notification_groups import NotificationGroupManager
from backend.alerts.rule_manager import SEVERITY_ORDER, AlertRuleManager, level_meets_threshold
from backend.alerts.state import AlertDeduplicationEngine
from backend.core.schema import LogLevel
from backend.core.store import Base

logger = logging.getLogger("logtool.alerts.rules")


def _effective_recipients(rule: Dict[str, Any], group_by_id: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """A rule's notification_group_id, when set, takes precedence over its
    raw `recipients` string -- lets one named group be reused across rules
    instead of retyping the same email list on each one. Falls back
    gracefully to `recipients` (then ultimately the server default) if the
    referenced group was since deleted."""
    if rule.get("notification_group_id"):
        group = group_by_id.get(rule["notification_group_id"])
        if group:
            return group["emails"]
    return rule["recipients"]


def _highest_severity(events: List[Dict[str, Any]]) -> Optional[str]:
    best: Optional[str] = None
    best_idx = -1
    for e in events:
        try:
            idx = SEVERITY_ORDER.index(LogLevel(e.get("level")))
        except (ValueError, KeyError):
            continue
        if idx > best_idx:
            best_idx = idx
            best = e.get("level")
    return best


def _effective_dedup_window(rule: Dict[str, Any], severity: Optional[str]) -> int:
    """A rule's dedup_windows (severity -> minutes) lets it notify more or
    less often depending on the SEVERITY OF THE TRIGGERING EVENT, not just
    the rule's own min_level threshold -- e.g. CRITICAL notifies every
    time (window 0) while WARN suppresses repeats for an hour, all within
    one rule. Falls back to the rule's flat dedup_window_minutes for any
    severity not explicitly mapped, or when dedup_windows isn't configured
    at all -- every rule created before this feature existed keeps
    behaving exactly as it did."""
    windows = rule.get("dedup_windows") or {}
    if severity and severity in windows:
        return windows[severity]
    return rule["dedup_window_minutes"]


def _extract_correlation_identifier(event: Dict[str, Any]) -> Optional[str]:
    """Best-effort identifier for deep-linking an alert into the
    Investigation dashboard. correlation_id is the one field every
    family's normalize_<family>_event() reads from the SAME top-level
    attributes.correlation_id path (see backend/analysis/*.py) -- the only
    identifier this family-agnostic alert engine (fires on ANY ingested
    log, not just the 5 payment families) can extract without hardcoding
    per-family nested payload shapes like transaction_id/tracker_no."""
    attrs = event.get("attributes") or {}
    value = attrs.get("correlation_id")
    return str(value) if value else None


def _matches_rule(event: Dict[str, Any], rule: Dict[str, Any]) -> bool:
    if not level_meets_threshold(event.get("level", "UNKNOWN"), rule["min_level"]):
        return False
    if rule["source_system_filter"]:
        if rule["source_system_filter"].lower() not in (event.get("source_system") or "").lower():
            return False
    if rule["component_filter"]:
        if rule["component_filter"].lower() not in (event.get("component") or "").lower():
            return False
    if rule["message_contains"]:
        if rule["message_contains"].lower() not in (event.get("message") or "").lower():
            return False
    return True


class AlertRulesProcessor:
    def __init__(
        self,
        email_dispatcher: EmailDispatcher,
        dedup_engine: AlertDeduplicationEngine,
        rule_manager: AlertRuleManager,
        group_manager: NotificationGroupManager,
        db_path: str,
    ):
        self.email_dispatcher = email_dispatcher
        self.dedup_engine = dedup_engine
        self.rule_manager = rule_manager
        self.group_manager = group_manager
        self.engine = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30.0}, echo=False
        )
        Base.metadata.create_all(self.engine)
        self._ensure_correlation_columns()
        self.Session = sessionmaker(bind=self.engine)

    def _ensure_correlation_columns(self) -> None:
        """Base.metadata.create_all() only creates missing TABLES, not
        missing COLUMNS on tables that already exist -- a database created
        before correlation_field/correlation_value existed needs a one-time
        ALTER TABLE. Same pattern as backend/auth/service.py's
        _ensure_must_change_password_column() -- no migration framework in
        this project yet, deliberately minimal and idempotent."""
        with self.engine.connect() as conn:
            cols = [row[1] for row in conn.execute(text("PRAGMA table_info(alert_dispatch_log)")).fetchall()]
            if cols and "correlation_field" not in cols:
                conn.execute(text("ALTER TABLE alert_dispatch_log ADD COLUMN correlation_field VARCHAR"))
                conn.execute(text("ALTER TABLE alert_dispatch_log ADD COLUMN correlation_value VARCHAR"))
                conn.commit()
                logger.info("Migrated alert_dispatch_log table: added correlation_field/correlation_value columns.")

    def evaluate_batch_alerts(self, batch_id: str, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        triggered: List[Dict[str, Any]] = []
        rules = self.rule_manager.list_enabled_rules()
        group_by_id = {g["group_id"]: g for g in self.group_manager.list_groups()}

        for rule in rules:
            matching = [e for e in events if _matches_rule(e, rule)]
            if not matching:
                continue

            recipients = _effective_recipients(rule, group_by_id)

            if rule["mode"] == "immediate":
                for e in matching:
                    source = e.get("source_system") or "unknown"
                    comp = e.get("component") or "none"
                    msg = e.get("message") or ""
                    window = _effective_dedup_window(rule, e.get("level"))

                    if not self.dedup_engine.should_fire_alert(rule["rule_id"], source, comp, msg, window):
                        continue

                    subject = f"{rule['name']}: {source} [{comp}]"
                    body = (
                        f"Alert rule: {rule['name']}\n\n"
                        f"Batch ID: {batch_id}\n"
                        f"Timestamp: {e.get('ts_utc')}\n"
                        f"Level: {e.get('level')}\n"
                        f"Source System: {source}\n"
                        f"Component: {comp}\n"
                        f"Line Number: {e.get('line_no')}\n\n"
                        f"Message:\n{msg}\n\n"
                        f"Raw Log:\n{e.get('raw')}\n"
                    )
                    self._dispatch_and_log(
                        rule, batch_id, subject, body, event_count=1,
                        recipients=recipients, correlation_value=_extract_correlation_identifier(e),
                    )
                    triggered.append({"rule": rule["name"], "mode": "immediate", "source": source, "component": comp})

            else:  # digest
                source = matching[0].get("source_system") or "batch"
                comp = "batch_digest"
                msg = f"{len(matching)} matching events"
                # Most conservative choice across the batch: one CRITICAL
                # event in an otherwise-quiet digest means don't suppress
                # this dispatch, even if lower-severity events in the same
                # batch would normally be suppressed.
                window = _effective_dedup_window(rule, _highest_severity(matching))

                if not self.dedup_engine.should_fire_alert(rule["rule_id"], source, comp, msg, window):
                    continue

                sample_lines = "\n".join(
                    f"- Line {e.get('line_no')} [{e.get('level')}] {(e.get('message') or '')[:120]}"
                    for e in matching[:5]
                )
                subject = f"{rule['name']}: {len(matching)} matching event(s) in {source}"
                body = (
                    f"Alert rule: {rule['name']}\n\n"
                    f"Batch ID: {batch_id}\n"
                    f"Total matching events: {len(matching)}\n\n"
                    f"Sample events:\n{sample_lines}\n"
                )
                # Best-effort only: one dispatch row covers ALL `matching`
                # events, but only the FIRST one's identifier is stored --
                # a full per-event mapping would need a many-rows-per-
                # dispatch model this table doesn't have.
                self._dispatch_and_log(
                    rule, batch_id, subject, body, event_count=len(matching),
                    recipients=recipients, correlation_value=_extract_correlation_identifier(matching[0]),
                )
                triggered.append({"rule": rule["name"], "mode": "digest", "source": source, "count": len(matching)})

        return triggered

    def _dispatch_and_log(
        self,
        rule: Dict[str, Any],
        batch_id: str,
        subject: str,
        body: str,
        event_count: int,
        recipients: Optional[str] = None,
        correlation_value: Optional[str] = None,
    ) -> None:
        success, status_msg = self.email_dispatcher.send_alert_email(
            subject, body, recipient_override=recipients
        )
        recipient_used = recipients or self.email_dispatcher.alert_email_to

        session = self.Session()
        try:
            session.add(
                AlertDispatchLogModel(
                    dispatch_id=str(uuid.uuid4()),
                    rule_id=rule["rule_id"],
                    rule_name=rule["name"],
                    batch_id=batch_id,
                    triggered_at=datetime.now(timezone.utc).isoformat(),
                    recipient=recipient_used,
                    subject=subject,
                    success=1 if success else 0,
                    status_message=status_msg,
                    event_count=event_count,
                    correlation_field="correlation_id" if correlation_value else None,
                    correlation_value=correlation_value,
                )
            )
            session.commit()
        finally:
            session.close()

    def log_manual_dispatch(self, subject: str, success: bool, status_msg: str, recipient: str) -> None:
        """Used by the manual /api/alerts/test endpoint so ad-hoc test
        sends show up in history too, not just rule-triggered ones."""
        session = self.Session()
        try:
            session.add(
                AlertDispatchLogModel(
                    dispatch_id=str(uuid.uuid4()),
                    rule_id=None,
                    rule_name="(manual test)",
                    batch_id=None,
                    triggered_at=datetime.now(timezone.utc).isoformat(),
                    recipient=recipient,
                    subject=subject,
                    success=1 if success else 0,
                    status_message=status_msg,
                    event_count=0,
                )
            )
            session.commit()
        finally:
            session.close()

    def get_dispatch_history(self, page: int = 1, page_size: int = 50) -> Dict[str, Any]:
        session = self.Session()
        try:
            query = session.query(AlertDispatchLogModel).order_by(AlertDispatchLogModel.triggered_at.desc())
            total = query.count()
            rows = query.offset((page - 1) * page_size).limit(page_size).all()
            return {
                "total": total,
                "page": page,
                "page_size": page_size,
                "entries": [
                    {
                        "dispatch_id": r.dispatch_id,
                        "rule_id": r.rule_id,
                        "rule_name": r.rule_name,
                        "batch_id": r.batch_id,
                        "triggered_at": r.triggered_at,
                        "recipient": r.recipient,
                        "subject": r.subject,
                        "success": bool(r.success),
                        "status_message": r.status_message,
                        "event_count": r.event_count,
                        "correlation_field": r.correlation_field,
                        "correlation_value": r.correlation_value,
                    }
                    for r in rows
                ],
            }
        finally:
            session.close()
