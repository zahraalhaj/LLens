"""
Config loading. Paths in config.yaml are resolved relative to the backend/
directory (not the process cwd) so the app runs the same regardless of
where uvicorn is launched from. A few security-relevant settings are read
from environment variables instead of the YAML file, since they shouldn't
live in a checked-in config file (session cookie behavior, SMTP creds --
SMTP creds are already handled this way in alerts/email.py).
"""
import os
from pathlib import Path

import yaml

BACKEND_DIR = Path(__file__).parent.parent


class Settings:
    def __init__(self):
        raw = yaml.safe_load((BACKEND_DIR / "config.yaml").read_text())

        self.max_upload_size_mb: int = raw.get("max_upload_size_mb", 500)
        self.ollama_url: str = os.environ.get("OLLAMA_URL", raw.get("ollama_url", "http://localhost:11434"))
        self.ollama_model: str = os.environ.get("OLLAMA_MODEL", raw.get("ollama_model", "qwen3:8b"))
        self.db_path: str = str(BACKEND_DIR / raw.get("db_path", "data/logs.db"))
        self.profiles_dir: str = str(BACKEND_DIR / raw.get("profiles_dir", "profiles"))
        self.default_match_threshold: float = raw.get("default_match_threshold", 0.8)
        self.batch_size: int = raw.get("batch_size", 1000)

        # Session cookie: Secure requires HTTPS. Default true; set
        # COOKIE_SECURE=false only for local http-only development.
        self.cookie_secure: bool = os.environ.get("COOKIE_SECURE", "true").lower() != "false"
        self.session_lifetime_hours: int = int(os.environ.get("SESSION_LIFETIME_HOURS", "12"))

        # CORS: the frontend's origin in dev (Vite) / prod. Comma-separated.
        self.cors_origins = [
            o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()
        ]

        Path(self.profiles_dir).mkdir(parents=True, exist_ok=True)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
