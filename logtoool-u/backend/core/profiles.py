"""
Parser Profile Manager.
Loads, validates, saves, and supplies default parser profiles stored as JSON.
"""

import json
import os
import re
import glob
from typing import Dict, List, Optional
import logging

from backend.core.schema import ParserProfile, ProfileType, MultilineConfig

logger = logging.getLogger("logtool.profiles")


def _slugify(name: str) -> str:
    """Turn a profile's display name into a safe filename stem."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_")

DEFAULT_PROFILES: List[Dict] = [
    {
        "name": "Apache Access Log",
        "type": "regex",
        "pattern": r'^(?P<ip>\S+) \S+ \S+ \[(?P<timestamp>[^\]]+)\] "(?P<message>(?P<method>\S+) (?P<path>\S+) [^"]+)" (?P<status>\d{3}) (?P<bytes>\S+)',
        "timestamp_field": "timestamp",
        "timestamp_format": None,
        "level_field": "status",
        "component_field": "method",
        "message_field": "message",
        "default_source_system": "apache",
        "timezone": "UTC",
        "min_match_ratio": 0.85,
        "level_map": {
            "200": "INFO", "201": "INFO", "204": "INFO",
            "301": "INFO", "302": "INFO", "304": "INFO",
            "400": "WARN", "401": "WARN", "403": "WARN", "404": "WARN",
            "500": "ERROR", "502": "ERROR", "503": "ERROR", "504": "ERROR"
        }
    },
    {
        "name": "Standard Syslog",
        "type": "regex",
        "pattern": r'^(?P<timestamp>[A-Za-z]{3}\s+\d+\s+\d{2}:\d{2}:\d{2})\s+(?P<host>\S+)\s+(?P<component>[^\[:]+)(?:\[(?P<pid>\d+)\])?:\s+(?P<message>.*)$',
        "timestamp_field": "timestamp",
        "timestamp_format": None,
        "level_field": "level",
        "component_field": "component",
        "message_field": "message",
        "source_system_field": "host",
        "default_source_system": "syslog",
        "timezone": "UTC",
        "min_match_ratio": 0.85
    },
    {
        "name": "Application JSON Log",
        "type": "json",
        "pattern": "root",
        "timestamp_field": "timestamp",
        "level_field": "level",
        "component_field": "service",
        "message_field": "message",
        "default_source_system": "app_backend",
        "timezone": "UTC",
        "min_match_ratio": 0.85
    },
    {
        "name": "Delimited CSV/TSV Log",
        "type": "delimited",
        "pattern": ",",
        "timestamp_field": "timestamp",
        "level_field": "level",
        "component_field": "component",
        "message_field": "message",
        "default_source_system": "csv_export",
        "delimiter_fields": ["timestamp", "level", "component", "message"],
        "timezone": "UTC",
        "min_match_ratio": 0.85
    },
    {
        "name": "Apache Error Log",
        "type": "regex",
        "pattern": r'^\[(?P<timestamp>[^\]]+)\]\s+\[(?:[\w.]+:)?(?P<level>\w+)\]\s+(?:\[pid\s+\d+:\w+\s+\d+\]\s+)?(?:\[client\s+(?P<client>[^\]]+)\]\s+)?(?P<message>.*)$',
        "timestamp_field": "timestamp",
        "timestamp_format": None,
        "level_field": "level",
        "component_field": None,
        "message_field": "message",
        "source_system_field": "client",
        "default_source_system": "apache_server",
        "timezone": "UTC",
        "min_match_ratio": 0.85,
        "level_map": {"NOTICE": "INFO", "WARN": "WARN", "ERROR": "ERROR", "CRIT": "CRITICAL", "ALERT": "CRITICAL", "EMERG": "CRITICAL"}
    },
    {
        "name": "Spring Boot / Java Microservice Log",
        "type": "regex",
        "pattern": r'^(?P<timestamp>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+(?P<level>[A-Z]+)\s+(?P<pid>\d+)?\s*---\s*\[(?P<thread>[^\]]+)\]\s+(?P<component>[\w.]+)\s*:\s*(?P<message>.*)$',
        "timestamp_field": "timestamp",
        "timestamp_format": None,
        "level_field": "level",
        "component_field": "component",
        "message_field": "message",
        "default_source_system": "springboot_app",
        "timezone": "UTC",
        "min_match_ratio": 0.85
    },
    {
        "name": "Standard Bracket [TIMESTAMP] [LEVEL]",
        "type": "regex",
        "pattern": r'^\[(?P<timestamp>\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)\]\s*\[(?P<level>\w+)\]\s*(?:\[(?P<component>[^\]]+)\]\s*)?(?::?\s*(?P<message>.+))?$',
        "timestamp_field": "timestamp",
        "timestamp_format": None,
        "level_field": "level",
        "component_field": "component",
        "message_field": "message",
        "default_source_system": "generic_app",
        "timezone": "UTC",
        "min_match_ratio": 0.85
    }
]


class ProfileManager:
    """
    Manages loading, saving, listing, and validation of parser profiles.
    """
    def __init__(self, profiles_dir: str = "profiles"):
        self.profiles_dir = profiles_dir
        os.makedirs(self.profiles_dir, exist_ok=True)
        self.ensure_default_profiles()

    def ensure_default_profiles(self) -> None:
        """Seed default profiles if none exist."""
        for prof in DEFAULT_PROFILES:
            file_name = f"{_slugify(prof['name'])}.json"
            file_path = os.path.join(self.profiles_dir, file_name)
            if not os.path.exists(file_path):
                try:
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(prof, f, indent=2)
                except Exception as e:
                    logger.error(f"Failed writing default profile {file_name}: {e}")

    def list_profiles(self) -> List[ParserProfile]:
        """Loads and returns all profiles from the profiles directory."""
        profiles: List[ParserProfile] = []
        pattern = os.path.join(self.profiles_dir, "*.json")
        for filepath in glob.glob(pattern):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    prof = ParserProfile(**data)
                    profiles.append(prof)
            except Exception as e:
                logger.warning(f"Failed to load profile from {filepath}: {e}")
        return profiles

    def save_profile(self, profile: ParserProfile) -> str:
        """Saves a profile to JSON file and returns file path."""
        safe_name = _slugify(profile.name)
        file_path = os.path.join(self.profiles_dir, f"{safe_name}.json")
        data = profile.model_dump()
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved parser profile '{profile.name}' to {file_path}")
        return file_path

    def get_profile_by_name(self, name: str) -> Optional[ParserProfile]:
        for prof in self.list_profiles():
            if prof.name.lower() == name.lower():
                return prof
        return None
