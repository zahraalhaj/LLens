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

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.alerts.email import EmailDispatcher
from backend.alerts.models import AlertDispatchLogModel
from backend.alerts.rule_manager import AlertRuleManager, level_meets_threshold
from backend.alerts.state import AlertDeduplicationEngine
from backend.core.store import Base

logger = logging.getLogger("logtool.alerts.rules")


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
        db_path: str,
    ):
        self.email_dispatcher = email_dispatcher
        self.dedup_engine = dedup_engine
        self.rule_manager = rule_manager
        self.engine = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30.0}, echo=False
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def evaluate_batch_alerts(self, batch_id: str, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        triggered: List[Dict[str, Any]] = []
        rules = self.rule_manager.list_enabled_rules()

        for rule in rules:
            matching = [e for e in events if _matches_rule(e, rule)]
            if not matching:
                continue

            if rule["mode"] == "immediate":
                for e in matching:
                    source = e.get("source_system") or "unknown"
                    comp = e.get("component") or "none"
                    msg = e.get("message") or ""

                    if not self.dedup_engine.should_fire_alert(rule["rule_id"], source, comp, msg, rule["dedup_window_minutes"]):
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
                    self._dispatch_and_log(rule, batch_id, subject, body, event_count=1)
                    triggered.append({"rule": rule["name"], "mode": "immediate", "source": source, "component": comp})

            else:  # digest
                source = matching[0].get("source_system") or "batch"
                comp = "batch_digest"
                msg = f"{len(matching)} matching events"

                if not self.dedup_engine.should_fire_alert(rule["rule_id"], source, comp, msg, rule["dedup_window_minutes"]):
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
                self._dispatch_and_log(rule, batch_id, subject, body, event_count=len(matching))
                triggered.append({"rule": rule["name"], "mode": "digest", "source": source, "count": len(matching)})

        return triggered

    def _dispatch_and_log(self, rule: Dict[str, Any], batch_id: str, subject: str, body: str, event_count: int) -> None:
        success, status_msg = self.email_dispatcher.send_alert_email(
            subject, body, recipient_override=rule["recipients"]
        )
        recipient_used = rule["recipients"] or self.email_dispatcher.alert_email_to

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
                    }
                    for r in rows
                ],
            }
        finally:
            session.close()
