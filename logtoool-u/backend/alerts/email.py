"""
Email Dispatcher module using SMTP.
Credentials MUST come only from environment variables.
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, parseaddr
from typing import List, Optional, Tuple
import logging

logger = logging.getLogger("logtool.alerts.email")


class EmailDispatcher:
    def __init__(
        self,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_user: str | None = None,
        smtp_password: str | None = None,
        alert_email_to: str | None = None,
    ):
        # Credentials sourced from explicit params (Settings) with env var fallback
        self.smtp_host = smtp_host or os.getenv("SMTP_HOST", "localhost")
        self.smtp_port = smtp_port or int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = smtp_user or os.getenv("SMTP_USER", "")
        self.smtp_password = smtp_password or os.getenv("SMTP_PASSWORD", "")
        self.alert_email_to = alert_email_to or os.getenv("ALERT_EMAIL_TO", "admin@example.com")
        # Was documented in .env.example but never actually read -- the From
        # address silently fell back to smtp_user (or a hardcoded default)
        # regardless of what was set here. Fixed to actually use it.
        self.from_email = os.getenv("ALERT_FROM_EMAIL") or self.smtp_user or "alerts@logtool.local"

    @staticmethod
    def _parse_recipients(raw: str) -> List[str]:
        """Split a comma/semicolon-separated recipient string into clean
        individual addresses.  Strips whitespace and drops blanks."""
        import re
        parts = re.split(r"[,;]", raw)
        return [p.strip() for p in parts if p.strip()]

    def send_alert_email(
        self,
        subject: str,
        body_text: str,
        recipient_override: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Send alert email(s).  *recipient_override* may contain multiple
        comma-separated addresses; each gets an individual email so that
        delivery issues for one recipient don't block others."""
        raw = recipient_override or self.alert_email_to
        recipients = self._parse_recipients(raw)

        if not recipients:
            logger.warning("send_alert_email called with no recipients; falling back to default")
            recipients = [self.alert_email_to]

        simulated = self.smtp_host == "localhost" and not self.smtp_user
        sent, failed = [], []

        for addr in recipients:
            msg = MIMEMultipart()
            msg["From"] = self.from_email
            msg["To"] = formataddr(parseaddr(addr))
            msg["Subject"] = f"[LOGTOOL ALERT] {subject}"
            msg.attach(MIMEText(body_text, "plain"))

            if simulated:
                logger.info(f"SMTP not configured. Simulated email → {addr}: {subject}")
                sent.append(addr)
                continue

            try:
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10.0) as server:
                    server.starttls()
                    if self.smtp_user and self.smtp_password:
                        server.login(self.smtp_user, self.smtp_password)
                    server.send_message(msg)
                sent.append(addr)
                logger.info(f"Sent alert email → {addr}: {subject}")
            except Exception as e:
                failed.append(addr)
                logger.error(f"Failed sending to {addr}: {e}")

        if simulated:
            return True, f"Simulated dispatch to {', '.join(sent)} (no SMTP configured)"
        if failed:
            return False, f"Sent to {', '.join(sent)}; failed for {', '.join(failed)}"
        return True, f"Alert email dispatched to {', '.join(sent)}"
