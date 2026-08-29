"""
Config loading. Paths in config.yaml are resolved relative to the backend/
directory (not the process cwd) so the app runs the same regardless of
where uvicorn is launched from. A few security-relevant settings are read
from environment variables instead of the YAML file, since they shouldn't
live in a checked-in config file (session cookie behavior, SMTP creds).
"""
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

BACKEND_DIR = Path(__file__).parent.parent
CONFIG_PATH = BACKEND_DIR / "config.yaml"


class Settings:
    def __init__(self):
        self._raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}

        self.max_upload_size_mb: int = self._raw.get("max_upload_size_mb", 500)
        self.ollama_url: str = os.environ.get("OLLAMA_URL", self._raw.get("ollama_url", "http://localhost:11434"))
        self.ollama_model: str = os.environ.get("OLLAMA_MODEL", self._raw.get("ollama_model", "qwen3:8b"))
        self.db_path: str = str(BACKEND_DIR / self._raw.get("db_path", "data/logs.db"))
        self.profiles_dir: str = str(BACKEND_DIR / self._raw.get("profiles_dir", "profiles"))
        self.default_match_threshold: float = self._raw.get("default_match_threshold", 0.8)
        self.batch_size: int = self._raw.get("batch_size", 1000)

        # Session cookie: Secure requires HTTPS. Default true; set
        # COOKIE_SECURE=false only for local http-only development.
        self.cookie_secure: bool = os.environ.get("COOKIE_SECURE", "true").lower() != "false"
        self.session_lifetime_hours: int = int(os.environ.get("SESSION_LIFETIME_HOURS", "12"))

        # CORS: the frontend's origin in dev (Vite) / prod. Comma-separated.
        self.cors_origins = [
            o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()
        ]

        # SMTP config: env vars take precedence over config.yaml.
        self.smtp_host: str = os.environ.get("SMTP_HOST", self._raw.get("smtp_host", "localhost"))
        self.smtp_port: int = int(os.environ.get("SMTP_PORT", str(self._raw.get("smtp_port", 587))))
        self.smtp_user: str = os.environ.get("SMTP_USER", self._raw.get("smtp_user", ""))
        self.smtp_password: str = os.environ.get("SMTP_PASSWORD", self._raw.get("smtp_password", ""))
        self.alert_email_to: str = os.environ.get("ALERT_EMAIL_TO", self._raw.get("alert_email_to", "admin@example.com"))

        # Retention: None/0 = keep forever. MUST default to disabled --
        # an existing install upgrading into this feature should never
        # start silently deleting data just because the field exists now.
        self.retention_days: Optional[int] = self._raw.get("retention_days") or None

        Path(self.profiles_dir).mkdir(parents=True, exist_ok=True)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    def get_smtp_config(self) -> Dict[str, Any]:
        return {
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "smtp_user": self.smtp_user,
            "smtp_password": self.smtp_password,
            "alert_email_to": self.alert_email_to,
        }

    def update_smtp_config(self, cfg: Dict[str, Any]) -> None:
        """Update SMTP settings and persist to config.yaml. Env vars still
        take precedence on next reload, but the YAML file reflects the
        latest GUI edits."""
        self.smtp_host = cfg.get("smtp_host", self.smtp_host)
        self.smtp_port = int(cfg.get("smtp_port", self.smtp_port))
        self.smtp_user = cfg.get("smtp_user", self.smtp_user)
        self.smtp_password = cfg.get("smtp_password", self.smtp_password)
        self.alert_email_to = cfg.get("alert_email_to", self.alert_email_to)

        # Persist to config.yaml
        self._raw["smtp_host"] = self.smtp_host
        self._raw["smtp_port"] = self.smtp_port
        self._raw["smtp_user"] = self.smtp_user
        self._raw["smtp_password"] = self.smtp_password
        self._raw["alert_email_to"] = self.alert_email_to
        CONFIG_PATH.write_text(yaml.dump(self._raw, default_flow_style=False, sort_keys=False))

    def get_retention_config(self) -> Dict[str, Any]:
        return {"retention_days": self.retention_days}

    def update_retention_config(self, retention_days: Optional[int]) -> None:
        """0 or None both mean "disabled" -- normalized to None so
        core/retention.py's job only ever has to check falsiness once."""
        self.retention_days = retention_days or None
        self._raw["retention_days"] = self.retention_days
        CONFIG_PATH.write_text(yaml.dump(self._raw, default_flow_style=False, sort_keys=False))


settings = Settings()
