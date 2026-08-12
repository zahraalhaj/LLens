"""
Email Dispatcher module using SMTP.
Credentials MUST come only from environment variables.
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional, Tuple
import logging

logger = logging.getLogger("logtool.alerts.email")


class EmailDispatcher:
    def __init__(self):
        # Credentials sourced strictly from environment variables
        self.smtp_host = os.getenv("SMTP_HOST", "localhost")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.alert_email_to = os.getenv("ALERT_EMAIL_TO", "admin@example.com")

    def send_alert_email(
        self,
        subject: str,
        body_text: str,
        recipient_override: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Sends an alert email via SMTP.
        Returns (success_boolean, status_message).
        """
        recipient = recipient_override or self.alert_email_to

        msg = MIMEMultipart()
        msg["From"] = self.smtp_user if self.smtp_user else "alerts@logtool.local"
        msg["To"] = recipient
        msg["Subject"] = f"[LOGTOOL ALERT] {subject}"

        msg.attach(MIMEText(body_text, "plain"))

        try:
            # If no SMTP host or credentials set, log warning instead of failing hard
            if self.smtp_host == "localhost" and not self.smtp_user:
                logger.info(f"SMTP not configured. Simulated sending email to {recipient}: {subject}")
                return True, "Simulated email dispatch (SMTP host is localhost without credentials)"

            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10.0) as server:
                server.starttls()
                if self.smtp_user and self.smtp_password:
                    server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)

            logger.info(f"Sent alert email to {recipient}: {subject}")
            return True, f"Alert email dispatched to {recipient}"
        except Exception as e:
            logger.error(f"Failed sending alert email to {recipient}: {e}")
            return False, f"SMTP dispatch error: {str(e)}"
