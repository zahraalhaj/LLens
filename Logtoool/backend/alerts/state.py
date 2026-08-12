"""
Alert State and Deduplication Management.
Suppresses duplicate alert notifications within a 1-hour window using signature tuples.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple
import logging

logger = logging.getLogger("logtool.alerts.state")


class AlertDeduplicationEngine:
    """
    Deduplicates alerts based on (rule, source, component, normalized_message).
    Suppresses matching alerts for 1 hour (3600 seconds).
    """
    def __init__(self, suppression_window_seconds: int = 3600):
        self.suppression_window_seconds = suppression_window_seconds
        self.seen_signatures: Dict[Tuple[str, str, str, str], datetime] = {}

    def should_fire_alert(
        self,
        rule_name: str,
        source: str,
        component: Optional[str],
        normalized_message: str
    ) -> bool:
        """
        Returns True if the alert should fire, or False if suppressed as a duplicate.
        """
        key = (
            rule_name.lower(),
            (source or "unknown").lower(),
            (component or "none").lower(),
            normalized_message.strip()[:100].lower()
        )

        now = datetime.now(timezone.utc)

        if key in self.seen_signatures:
            last_fired = self.seen_signatures[key]
            if (now - last_fired).total_seconds() < self.suppression_window_seconds:
                logger.info(f"Alert '{rule_name}' suppressed as duplicate for key {key}")
                return False

        # Update last fired timestamp
        self.seen_signatures[key] = now
        return True

    def clear_state(self) -> None:
        self.seen_signatures.clear()
