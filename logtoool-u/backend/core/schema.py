"""
Core schema definitions for Canonical Event Model and Parser Profiles.
Uses Pydantic v2 for data validation and typing.
"""

import hashlib
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, computed_field
from datetime import datetime, timezone
import uuid


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class TimestampConfidence(str, Enum):
    PARSED = "parsed"
    ASSUMED_UTC = "assumed_utc"
    ASSUMED_LOCAL = "assumed_local"
    UNPARSEABLE = "unparseable"


class ProfileType(str, Enum):
    REGEX = "regex"
    JSON = "json"
    DELIMITED = "delimited"
    CUSTOM = "custom"  # code-defined parser plugin, see backend/custom_parsers/


class MultilineConfig(BaseModel):
    mode: str = "starts_with_timestamp"  # starts_with_timestamp, regex_pattern, indent
    pattern: Optional[str] = None


class ParserProfile(BaseModel):
    name: str
    type: ProfileType
    pattern: str  # Regex string, JSON field selector, or delimiter character (e.g. ',', '\t', '|')
    timestamp_field: str = "timestamp"
    timestamp_format: Optional[str] = None  # strftime directive or None for ISO/auto
    level_field: str = "level"
    component_field: Optional[str] = "component"
    message_field: str = "message"
    source_system_field: Optional[str] = None
    default_source_system: str = "generic_vendor"
    timezone: Optional[str] = "UTC"
    multiline_config: Optional[MultilineConfig] = Field(default_factory=MultilineConfig)
    min_match_ratio: float = 0.85
    level_map: Optional[Dict[str, str]] = None  # e.g., {"WARNING": "WARN", "ERR": "ERROR", "FATAL": "CRITICAL"}
    delimiter_fields: Optional[List[str]] = None  # For delimited format: header list if file lacks headers
    custom_parser_module: Optional[str] = None  # For type=CUSTOM: key into backend.core.custom_parser_registry

    # Names, in priority order, of this profile's own extracted `attributes`
    # keys (regex named groups / JSON field paths / delimiter field names)
    # that identify a real-world transaction/session -- lets a declarative
    # profile's events participate in cross-log correlation (see
    # backend/analysis/normalize.py's generic-event fallback and
    # backend/analysis/correlate.py's extra_identifiers pass) without
    # needing a hand-written per-family normalizer. None (the default)
    # means this profile's events still get minimally normalized, just
    # with no correlation identifiers beyond attributes.correlation_id if
    # the profile happens to populate that exact field name.
    correlation_keys: Optional[List[str]] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def profile_version(self) -> str:
        """Content-based hash of the profile configuration.  Changes
        automatically whenever any field is modified, giving every
        profile a deterministic version identifier without manual
        version bumping."""
        # Exclude profile_version itself to avoid infinite recursion
        data = self.model_dump(exclude={"profile_version"})
        raw = hashlib.sha256(
            __import__("json").dumps(data, sort_keys=True, default=str).encode()
        ).hexdigest()[:12]
        return f"p-{raw}"


class CanonicalLogEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ts_utc: Optional[str] = None  # ISO-8601 string in UTC, e.g. 2026-08-05T20:17:33Z
    ts_raw: str
    ts_confidence: TimestampConfidence
    level: LogLevel
    source_system: str
    component: Optional[str] = None
    message: str
    raw: str
    attributes: Dict[str, Any] = Field(default_factory=dict)
    line_no: int
    batch_id: str
    file_name: str


class BatchRecord(BaseModel):
    batch_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    file_name: str
    file_size_bytes: int
    total_events: int = 0
    matched_profile: Optional[str] = None
    matched_profile_version: Optional[str] = None
    match_ratio: float = 0.0
    uploaded_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class IngestionSummary(BaseModel):
    batch_id: str
    file_name: str
    total_lines: int
    grouped_events_count: int
    parsed_events_count: int
    matched_profile: str
    match_ratio: float
    warnings: List[str] = Field(default_factory=list)


def normalize_level(raw_level: Optional[str], custom_map: Optional[Dict[str, str]] = None) -> LogLevel:
    """
    Normalizes arbitrary level strings into canonical LogLevel enum values.
    Never fails or discards events; defaults to UNKNOWN.
    """
    if not raw_level:
        return LogLevel.UNKNOWN

    raw_clean = str(raw_level).strip().upper()

    if custom_map:
        for k, v in custom_map.items():
            if k.upper() == raw_clean:
                try:
                    return LogLevel(v.upper())
                except ValueError:
                    pass

    # Standard level mapping heuristic
    if raw_clean in ("DEBUG", "DBG", "TRACE", "FINE", "FINER", "FINEST"):
        return LogLevel.DEBUG
    elif raw_clean in ("INFO", "INFORMATIONAL", "NOTICE", "LOG"):
        return LogLevel.INFO
    elif raw_clean in ("WARN", "WARNING", "ALERT"):
        return LogLevel.WARN
    elif raw_clean in ("ERROR", "ERR", "FAIL", "FAILED", "FAILURE", "SEVERE"):
        return LogLevel.ERROR
    elif raw_clean in ("CRITICAL", "CRIT", "FATAL", "PANIC", "EMERGENCY", "EMERG"):
        return LogLevel.CRITICAL

    # Partial substring check
    if "ERR" in raw_clean or "FAIL" in raw_clean:
        return LogLevel.ERROR
    if "WARN" in raw_clean:
        return LogLevel.WARN
    if "INFO" in raw_clean:
        return LogLevel.INFO
    if "CRIT" in raw_clean or "FATAL" in raw_clean:
        return LogLevel.CRITICAL
    if "DBG" in raw_clean or "TRACE" in raw_clean:
        return LogLevel.DEBUG

    return LogLevel.UNKNOWN
