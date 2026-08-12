"""
Alert Rules Evaluation Engine.
Evaluates ingested log events against configured rules and dispatches immediate or digest alerts.
"""

from typing import Any, Dict, List, Tuple
import logging

from backend.alerts.email import EmailDispatcher
from backend.alerts.state import AlertDeduplicationEngine
from backend.core.schema import LogLevel

logger = logging.getLogger("logtool.alerts.rules")


class AlertRulesProcessor:
    def __init__(self, email_dispatcher: EmailDispatcher, dedup_engine: AlertDeduplicationEngine):
        self.email_dispatcher = email_dispatcher
        self.dedup_engine = dedup_engine

    def evaluate_batch_alerts(
        self,
        batch_id: str,
        events: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Evaluates a batch of events against alert rules:
        - CRITICAL events trigger immediate alert if not deduplicated.
        - ERROR events are tracked for hourly digest.
        Returns list of triggered alert summaries.
        """
        triggered_alerts: List[Dict[str, Any]] = []

        critical_events = [e for e in events if e.get("level") == LogLevel.CRITICAL.value]
        error_events = [e for e in events if e.get("level") == LogLevel.ERROR.value]

        # 1. Immediate CRITICAL alerts
        for e in critical_events:
            rule_name = "CRITICAL Event Immediate Trigger"
            source = e.get("source_system", "unknown")
            comp = e.get("component", "none")
            msg = e.get("message", "")

            if self.dedup_engine.should_fire_alert(rule_name, source, comp, msg):
                subject = f"CRITICAL alert from {source} [{comp}]"
                body = f"""Immediate CRITICAL Alert Triggered!

Batch ID: {batch_id}
Timestamp: {e.get('ts_utc')}
Source System: {source}
Component: {comp}
Line Number: {e.get('line_no')}

Message:
{msg}

Raw Log:
{e.get('raw')}
"""
                success, status_msg = self.email_dispatcher.send_alert_email(subject, body)
                triggered_alerts.append({
                    "type": "CRITICAL_IMMEDIATE",
                    "source": source,
                    "component": comp,
                    "message": msg,
                    "status": status_msg
                })

        # 2. ERROR hourly summary alert
        if error_events:
            rule_name = "ERROR Batch Summary Trigger"
            source = error_events[0].get("source_system", "batch")
            comp = "batch_summary"
            msg = f"Batch contains {len(error_events)} ERROR events"

            if self.dedup_engine.should_fire_alert(rule_name, source, comp, msg):
                subject = f"ERROR Summary Digest ({len(error_events)} errors) in {source}"
                sample_lines = "\n".join([f"- Line {e.get('line_no')}: {e.get('message')[:120]}" for e in error_events[:5]])
                body = f"""ERROR Summary Digest Report:

Total ERROR events in batch {batch_id}: {len(error_events)}

Sample Errors:
{sample_lines}
"""
                success, status_msg = self.email_dispatcher.send_alert_email(subject, body)
                triggered_alerts.append({
                    "type": "ERROR_DIGEST",
                    "source": source,
                    "count": len(error_events),
                    "status": status_msg
                })

        return triggered_alerts
