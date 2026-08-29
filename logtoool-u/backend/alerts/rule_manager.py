"""
Manages the alert_rules table: CRUD + seeding sensible defaults on first
run so a fresh install isn't alert-silent (mirrors the old hardcoded
CRITICAL-immediate / ERROR-digest behavior, but as regular editable rows
now instead of code).
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.alerts.models import AlertRuleModel, AlertRuleSeedMarkerModel
from backend.core.schema import LogLevel
from backend.core.store import Base

logger = logging.getLogger("logtool.alerts.rule_manager")

# Severity order for ">= min_level" comparisons. UNKNOWN is deliberately
# excluded from ordering -- it means "couldn't be determined," not "low
# severity," so it never satisfies a min_level threshold implicitly. A rule
# that specifically wants UNKNOWN events must set min_level to it exactly
# (handled as a special case in level_meets_threshold below).
SEVERITY_ORDER = [LogLevel.DEBUG, LogLevel.INFO, LogLevel.WARN, LogLevel.ERROR, LogLevel.CRITICAL]

VALID_MODES = {"immediate", "digest"}
VALID_TRIGGER_TYPES = {"severity", "anomaly"}

# Fields that mean "no filter" when empty -- stored as NULL, not "".
OPTIONAL_FILTER_FIELDS = ("source_system_filter", "component_filter", "message_contains", "recipients", "notification_group_id")

DEFAULT_RULES = [
    {
        "name": "CRITICAL Event Immediate Alert",
        "min_level": "CRITICAL",
        "mode": "immediate",
        "dedup_window_minutes": 60,
    },
    {
        "name": "ERROR Batch Summary Digest",
        "min_level": "ERROR",
        "mode": "digest",
        "dedup_window_minutes": 60,
    },
]


class RuleNotFoundError(Exception):
    pass


class RuleNameTakenError(Exception):
    pass


def level_meets_threshold(level: str, min_level: str) -> bool:
    if level == min_level:
        return True
    try:
        return SEVERITY_ORDER.index(LogLevel(level)) >= SEVERITY_ORDER.index(LogLevel(min_level))
    except (ValueError, KeyError):
        return False


class AlertRuleManager:
    def __init__(self, db_path: str):
        self.engine = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30.0}, echo=False
        )
        Base.metadata.create_all(self.engine)
        self._ensure_notification_columns()
        self._ensure_trigger_type_column()
        self.Session = sessionmaker(bind=self.engine)
        self._seed_defaults_if_empty()

    def _ensure_notification_columns(self) -> None:
        """Base.metadata.create_all() only creates missing TABLES, not
        missing COLUMNS on tables that already exist -- a database created
        before notification_group_id/dedup_windows_json existed needs a
        one-time ALTER TABLE. Same pattern as backend/auth/service.py's
        _ensure_must_change_password_column() and
        backend/alerts/rules.py's _ensure_correlation_columns()."""
        with self.engine.connect() as conn:
            cols = [row[1] for row in conn.execute(text("PRAGMA table_info(alert_rules)")).fetchall()]
            if cols and "notification_group_id" not in cols:
                conn.execute(text("ALTER TABLE alert_rules ADD COLUMN notification_group_id VARCHAR"))
                conn.execute(text("ALTER TABLE alert_rules ADD COLUMN dedup_windows_json TEXT"))
                conn.commit()
                logger.info("Migrated alert_rules table: added notification_group_id/dedup_windows_json columns.")

    def _ensure_trigger_type_column(self) -> None:
        """Same idempotent ALTER TABLE pattern as _ensure_notification_columns
        -- a database created before trigger_type existed needs a one-time
        migration. Existing rows default to 'severity', preserving their
        exact current behavior."""
        with self.engine.connect() as conn:
            cols = [row[1] for row in conn.execute(text("PRAGMA table_info(alert_rules)")).fetchall()]
            if cols and "trigger_type" not in cols:
                conn.execute(text("ALTER TABLE alert_rules ADD COLUMN trigger_type VARCHAR DEFAULT 'severity'"))
                conn.commit()
                logger.info("Migrated alert_rules table: added trigger_type column.")

    def _seed_defaults_if_empty(self) -> None:
        session = self.Session()
        try:
            if session.query(AlertRuleSeedMarkerModel).filter_by(marker_id="seeded").first():
                return  # already seeded once, even if rules were since deleted -- don't resurrect them
            now = datetime.now(timezone.utc).isoformat()
            for defaults in DEFAULT_RULES:
                session.add(
                    AlertRuleModel(
                        rule_id=str(uuid.uuid4()),
                        name=defaults["name"],
                        enabled=1,
                        min_level=defaults["min_level"],
                        mode=defaults["mode"],
                        dedup_window_minutes=defaults["dedup_window_minutes"],
                        created_at=now,
                        updated_at=now,
                    )
                )
            session.add(AlertRuleSeedMarkerModel(marker_id="seeded"))
            session.commit()
            logger.info("Seeded default alert rules (CRITICAL immediate, ERROR digest)")
        finally:
            session.close()

    def list_rules(self) -> List[Dict[str, Any]]:
        session = self.Session()
        try:
            rules = session.query(AlertRuleModel).order_by(AlertRuleModel.created_at).all()
            return [self._to_dict(r) for r in rules]
        finally:
            session.close()

    def list_enabled_rules(self) -> List[Dict[str, Any]]:
        session = self.Session()
        try:
            rules = session.query(AlertRuleModel).filter_by(enabled=1).all()
            return [self._to_dict(r) for r in rules]
        finally:
            session.close()

    def get_rule(self, rule_id: str) -> Dict[str, Any]:
        session = self.Session()
        try:
            rule = session.query(AlertRuleModel).filter_by(rule_id=rule_id).first()
            if not rule:
                raise RuleNotFoundError(rule_id)
            return self._to_dict(rule)
        finally:
            session.close()

    def create_rule(
        self,
        name: str,
        min_level: str,
        mode: str,
        source_system_filter: Optional[str] = None,
        component_filter: Optional[str] = None,
        message_contains: Optional[str] = None,
        dedup_window_minutes: int = 60,
        recipients: Optional[str] = None,
        notification_group_id: Optional[str] = None,
        dedup_windows: Optional[Dict[str, int]] = None,
        created_by_user_id: Optional[str] = None,
        trigger_type: str = "severity",
    ) -> Dict[str, Any]:
        self._validate(min_level, mode, trigger_type)
        session = self.Session()
        try:
            if session.query(AlertRuleModel).filter_by(name=name).first():
                raise RuleNameTakenError(f"A rule named '{name}' already exists")
            now = datetime.now(timezone.utc).isoformat()
            rule = AlertRuleModel(
                rule_id=str(uuid.uuid4()),
                name=name,
                enabled=1,
                trigger_type=trigger_type,
                min_level=min_level,
                source_system_filter=source_system_filter or None,
                component_filter=component_filter or None,
                message_contains=message_contains or None,
                mode=mode,
                dedup_window_minutes=dedup_window_minutes,
                recipients=recipients or None,
                notification_group_id=notification_group_id or None,
                dedup_windows_json=json.dumps(dedup_windows) if dedup_windows else None,
                created_at=now,
                updated_at=now,
                created_by_user_id=created_by_user_id,
            )
            session.add(rule)
            session.commit()
            return self._to_dict(rule)
        finally:
            session.close()

    def update_rule(self, rule_id: str, **fields) -> Dict[str, Any]:
        if fields.get("min_level") is not None:
            LogLevel(fields["min_level"])  # raises ValueError if invalid
        if fields.get("mode") is not None and fields["mode"] not in VALID_MODES:
            raise ValueError(f"mode must be one of {VALID_MODES}")
        if fields.get("trigger_type") is not None and fields["trigger_type"] not in VALID_TRIGGER_TYPES:
            raise ValueError(f"trigger_type must be one of {VALID_TRIGGER_TYPES}")

        session = self.Session()
        try:
            rule = session.query(AlertRuleModel).filter_by(rule_id=rule_id).first()
            if not rule:
                raise RuleNotFoundError(rule_id)

            if fields.get("name") and fields["name"] != rule.name:
                if session.query(AlertRuleModel).filter_by(name=fields["name"]).first():
                    raise RuleNameTakenError(f"A rule named '{fields['name']}' already exists")
                rule.name = fields["name"]

            for key in ("min_level", "mode", "dedup_window_minutes", "trigger_type"):
                if fields.get(key) is not None:
                    setattr(rule, key, fields[key])

            for key in OPTIONAL_FILTER_FIELDS:
                if key in fields:
                    setattr(rule, key, fields[key] or None)  # "" -> NULL (means "no filter")

            if "dedup_windows" in fields:
                rule.dedup_windows_json = json.dumps(fields["dedup_windows"]) if fields["dedup_windows"] else None

            if fields.get("enabled") is not None:
                rule.enabled = 1 if fields["enabled"] else 0

            rule.updated_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            return self._to_dict(rule)
        finally:
            session.close()

    def delete_rule(self, rule_id: str) -> None:
        session = self.Session()
        try:
            session.query(AlertRuleModel).filter_by(rule_id=rule_id).delete()
            session.commit()
        finally:
            session.close()

    def _validate(self, min_level: str, mode: str, trigger_type: str = "severity") -> None:
        LogLevel(min_level)  # raises ValueError if invalid
        if mode not in VALID_MODES:
            raise ValueError(f"mode must be one of {VALID_MODES}")
        if trigger_type not in VALID_TRIGGER_TYPES:
            raise ValueError(f"trigger_type must be one of {VALID_TRIGGER_TYPES}")

    def _to_dict(self, r: AlertRuleModel) -> Dict[str, Any]:
        return {
            "rule_id": r.rule_id,
            "name": r.name,
            "enabled": bool(r.enabled),
            "trigger_type": r.trigger_type or "severity",
            "min_level": r.min_level,
            "source_system_filter": r.source_system_filter,
            "component_filter": r.component_filter,
            "message_contains": r.message_contains,
            "mode": r.mode,
            "dedup_window_minutes": r.dedup_window_minutes,
            "recipients": r.recipients,
            "notification_group_id": r.notification_group_id,
            # Always the parsed dict (or None) -- callers never hand-parse
            # the raw JSON column themselves.
            "dedup_windows": json.loads(r.dedup_windows_json) if r.dedup_windows_json else None,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        }
